"""ORM models: User + Reminder + InviteCode."""

from __future__ import annotations

from datetime import datetime, time
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base."""


class User(Base):
    """One row per Telegram user. v3: multi-user — each user has an `is_active`
    flag flipped by /start (admins) or /start <invite_code> (invitees)."""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_username: Mapped[str | None] = mapped_column(String(64))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    # v1: per-user UI language. Auto-detected from messages (Cyrillic → 'ru'),
    # persisted so subsequent bot replies + the pinned summary use the same locale.
    preferred_locale: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    # v1: id of the bot's pinned active-tasks summary message in the user's chat.
    # NULL = no summary pinned yet; lazily created on the next state change.
    pinned_summary_message_id: Mapped[int | None] = mapped_column(BigInteger)
    # v3: only active users can interact with the bot. Admins (TELEGRAM_ADMIN_IDS)
    # are auto-promoted to active on /start; everyone else needs an invite code.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    invited_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # v2: quiet hours — local-time window during which pings are deferred.
    #   quiet_from < quiet_to: window is [quiet_from, quiet_to) (e.g. 13:00–14:00 nap)
    #   quiet_from > quiet_to: window crosses midnight (e.g. 23:00–07:00)
    #   either NULL: no quiet hours
    quiet_from: Mapped[time | None] = mapped_column(Time, nullable=True)
    quiet_to: Mapped[time | None] = mapped_column(Time, nullable=True)
    # v2: opt-in daily digest at this local time. NULL = off.
    digest_local_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    # v2: dedup — last UTC moment we pushed the digest. Updated by the digest tick.
    last_digest_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("telegram_user_id", name="uq_users_telegram_user_id"),
    )


class Reminder(Base):
    """A single reminder.

    Lifecycle:
      created → status='active', ping_count=0, next_ping_at=fire_at
      first ping fires → ping_count=1, next_ping_at = now + escalation[0]
      escalation pings continue until ping_count >= MAX or user marks done
      user taps Done → status='done', next_ping_at=NULL, done_at set
      user taps Snooze → ping_count reset to 0, next_ping_at = now + delta
      user taps Stop / max escalations → status='expired'
    """

    __tablename__ = "reminders"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    transcript: Mapped[str | None] = mapped_column(Text)
    input_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="text")
    fire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_ping_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ping_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # v1+v2: optional recurrence rule. Null = one-shot. Shape:
    #   {
    #     "kind": "daily"|"weekly"|"monthly",
    #     "time": "HH:MM",
    #     "weekdays": [0..6],          # weekly only
    #     "day_of_month": 1..31,       # monthly only (clamped if month is short)
    #     "tz": "Europe/Amsterdam",
    #     "until": "<ISO datetime>",   # optional terminal date (UTC)
    #     "count": <int>,              # optional max instance count
    #     "fired_count": <int>         # tracked across chained instances
    #   }
    # On terminal status, scheduler creates the next instance (carrying rule forward).
    recurrence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", index=True
    )
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Reminder {self.status} {self.ping_count}× {self.text[:40]!r}>"


class InviteCode(Base):
    """Single-use invite code generated by an admin via /invite.

    Codes are short (8 chars, base32-ish) and unique. Redeemed via
    `/start <code>` — sets `used_by` on the redeemer and flips their
    `is_active=true`. Expired (expires_at < now) and used codes are rejected.
    """

    __tablename__ = "invite_codes"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    created_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
