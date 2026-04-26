"""ORM models: User + Reminder."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base."""


class User(Base):
    """One row per Telegram user. v0 is single-tenant but we still model
    users so multi-user mode is a small step later."""

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
    # v1: optional recurrence rule. Null = one-shot. Shape:
    #   {"kind": "daily"|"weekly", "time": "HH:MM", "weekdays": [0..6], "tz": "Europe/Amsterdam"}
    # On terminal status, scheduler creates the next instance (carrying the rule forward).
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
