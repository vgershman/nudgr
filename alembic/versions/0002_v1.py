"""v1: users.preferred_locale, users.pinned_summary_message_id, reminders.recurrence

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-23

- preferred_locale: per-user UI language (auto-detected from messages, persisted).
- pinned_summary_message_id: id of the bot's pinned active-tasks summary in the
  user's chat. Null = no summary pinned yet (re-created lazily on next state change).
- recurrence: optional JSONB on reminders. {"kind": "daily"|"weekly", "time": "HH:MM",
  "weekdays": [0..6], "tz": "Europe/Amsterdam"}. When a recurring reminder reaches a
  terminal status (done/cancelled/expired), the scheduler creates the next instance.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "preferred_locale",
            sa.String(8),
            nullable=False,
            server_default=sa.text("'en'"),
        ),
    )
    op.add_column(
        "users",
        sa.Column("pinned_summary_message_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "reminders",
        sa.Column("recurrence", postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reminders", "recurrence")
    op.drop_column("users", "pinned_summary_message_id")
    op.drop_column("users", "preferred_locale")
