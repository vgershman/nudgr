"""Pending intent storage: multi-turn clarification context.

Lifecycle:
  user sends "remind me to X" with no time
    → parser flags needs_clarification
    → bot calls upsert_pending(user_id, chat_id, original_text, context, msg_id)
    → bot sends "When? [Cancel]" (msg_id stored for edit-on-cancel)
  user sends "in 5 minutes"
    → bot calls get_pending(user_id) → returns the snapshot
    → parser is invoked with pending_context kwarg, merges
    → if complete: bot creates the reminder + clear_pending(user_id)
    → if still partial: bot calls upsert_pending again with new context
  user taps Cancel
    → bot calls clear_pending(user_id) + edits message to "Cancelled"

Stale rows are swept periodically via expire_stale(). One pending per user
(PK on user_id) so a fresh task implicitly evicts the old.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete

from nudgr.db.models import PendingIntent
from nudgr.db.session import session_scope
from nudgr.observability.logging import logger

# How long a pending lives before being treated as stale. Long enough to cover
# realistic "user got distracted, came back" gaps; short enough that a
# multi-day-old pending doesn't accidentally merge with a fresh task.
DEFAULT_TTL_MIN = 15

# Callback prefix for the inline Cancel button.
CB_CANCEL_PENDING = "pcncl"


@dataclass
class PendingSnapshot:
    """Detached read-only snapshot suitable for use after the session closes."""

    user_id: UUID
    chat_id: int
    clarification_message_id: int | None
    original_text: str
    context: dict
    created_at: datetime
    expires_at: datetime

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at


def upsert_pending(
    *,
    user_id: UUID,
    chat_id: int,
    original_text: str,
    context: dict,
    clarification_message_id: int | None = None,
    ttl_min: int = DEFAULT_TTL_MIN,
) -> None:
    """Store (or overwrite) the pending intent for `user_id`."""
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_min)
    with session_scope() as s:
        existing = s.get(PendingIntent, user_id)
        if existing is None:
            s.add(
                PendingIntent(
                    user_id=user_id,
                    chat_id=chat_id,
                    clarification_message_id=clarification_message_id,
                    original_text=original_text,
                    context=context or {},
                    expires_at=expires_at,
                )
            )
        else:
            existing.chat_id = chat_id
            existing.clarification_message_id = clarification_message_id
            existing.original_text = original_text
            existing.context = context or {}
            existing.expires_at = expires_at


def update_clarification_message_id(user_id: UUID, message_id: int) -> None:
    """Late-bind the bot's reply message id once Telegram returns it."""
    with session_scope() as s:
        p = s.get(PendingIntent, user_id)
        if p is not None:
            p.clarification_message_id = message_id


def get_pending(user_id: UUID) -> PendingSnapshot | None:
    """Return the active pending for `user_id`, or None if missing/expired.

    Expired rows are deleted in the same call so the next path is clean.
    """
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        p = s.get(PendingIntent, user_id)
        if p is None:
            return None
        if p.expires_at <= now:
            s.delete(p)
            return None
        return PendingSnapshot(
            user_id=p.user_id,
            chat_id=p.chat_id,
            clarification_message_id=p.clarification_message_id,
            original_text=p.original_text,
            context=dict(p.context or {}),
            created_at=p.created_at,
            expires_at=p.expires_at,
        )


def clear_pending(user_id: UUID) -> bool:
    """Remove the pending row for `user_id`. Returns True if a row was deleted."""
    with session_scope() as s:
        p = s.get(PendingIntent, user_id)
        if p is None:
            return False
        s.delete(p)
        return True


def expire_stale() -> int:
    """Bulk-delete pendings whose `expires_at` has passed. Returns count."""
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        result = s.execute(
            delete(PendingIntent).where(PendingIntent.expires_at <= now)
        )
        n = result.rowcount or 0
        if n:
            logger.info(f"pending: swept {n} stale row(s)")
        return n
