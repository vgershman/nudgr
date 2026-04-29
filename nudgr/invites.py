"""Invite-code lifecycle: generate (admin) + redeem (/start <code>).

Codes are 8-char base32-style tokens (no easily-confusable glyphs). They live
in the `invite_codes` table; redemption flips the redeemer's `is_active=true`,
records who invited whom, and fills `joined_at`. Each code is single-use and
optionally expires.

Kept deliberately small — auth is one of those areas that grows weeds fast.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select

from nudgr.config import settings
from nudgr.db.models import InviteCode, User
from nudgr.db.session import session_scope
from nudgr.observability.logging import logger

# 32 unambiguous chars — no 0/O/1/I/L confusion when typing on a phone.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LEN = 8


def _new_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LEN))


def issue_invite(
    *, created_by: UUID, ttl_days: int | None = None
) -> tuple[str, datetime | None]:
    """Generate a fresh invite code on behalf of `created_by`.

    Returns (code, expires_at). `ttl_days` defaults to settings.invite_default_ttl_days;
    pass 0 to mint a never-expiring code.
    """
    ttl = settings.invite_default_ttl_days if ttl_days is None else ttl_days
    expires_at: datetime | None = None
    if ttl and ttl > 0:
        expires_at = datetime.now(timezone.utc) + timedelta(days=ttl)

    # Tiny retry loop to dodge the very unlikely PK collision.
    with session_scope() as s:
        for _ in range(8):
            code = _new_code()
            existing = s.get(InviteCode, code)
            if existing is None:
                s.add(
                    InviteCode(
                        code=code,
                        created_by=created_by,
                        expires_at=expires_at,
                    )
                )
                return code, expires_at
        raise RuntimeError("invite: failed to generate a unique code after 8 tries")


def redeem_invite(*, code: str, redeemer_id: UUID) -> tuple[bool, str]:
    """Attempt to redeem `code` on behalf of `redeemer_id`.

    Returns (success, reason_key). reason_key is an i18n key into
    {invite_redeemed | invite_expired | invite_used | invite_unknown |
     invite_already_active}.
    """
    code = (code or "").strip().upper()
    if not code:
        return False, "invite_unknown"

    now = datetime.now(timezone.utc)
    with session_scope() as s:
        user = s.get(User, redeemer_id)
        if user is None:
            return False, "invite_unknown"
        if user.is_active:
            # Idempotent re-/start with a code is a no-op, not an error.
            return True, "invite_already_active"

        invite = s.get(InviteCode, code)
        if invite is None:
            return False, "invite_unknown"
        if invite.used_by is not None:
            return False, "invite_used"
        if invite.expires_at is not None and invite.expires_at < now:
            return False, "invite_expired"

        invite.used_by = redeemer_id
        invite.used_at = now
        user.is_active = True
        user.invited_by = invite.created_by
        if user.joined_at is None:
            user.joined_at = now
        logger.info(f"invite: {code} redeemed by user={redeemer_id}")
        return True, "invite_redeemed"


def list_active_invites(*, created_by: UUID | None = None) -> list[InviteCode]:
    """Return unused, unexpired invites — optionally filtered by issuer."""
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        stmt = (
            select(InviteCode)
            .where(InviteCode.used_by.is_(None))
            .where((InviteCode.expires_at.is_(None)) | (InviteCode.expires_at > now))
            .order_by(InviteCode.created_at.desc())
        )
        if created_by is not None:
            stmt = stmt.where(InviteCode.created_by == created_by)
        rows = list(s.execute(stmt).scalars())
        # Detach from session — caller doesn't have one.
        for r in rows:
            s.expunge(r)
        return rows
