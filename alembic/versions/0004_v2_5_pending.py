"""v2.5: pending_intents — multi-turn clarification context

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-29

When the parser asks the user "when?" we now persist what it already knows
(target_text, recurrence so far) so the next free-form reply can be merged
into the same intent. The clarification message carries an inline Cancel
button that wipes the row when tapped.

One pending row per user — newer ones overwrite older. Expired pendings
(>15 min by default) are pruned by a small sweeper; they're harmless to
ignore otherwise since each lookup checks expires_at.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "pending_intents",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        # The bot's clarification message id — used to edit it on cancel/complete.
        sa.Column("clarification_message_id", sa.BigInteger(), nullable=True),
        # Original user prompt that triggered the clarification (for context + UI).
        sa.Column("original_text", sa.Text(), nullable=False),
        # Partial parsed intent so far. Shape mirrors ParsedIntent fields:
        #   {"target_text": str, "recurrence": dict|None,
        #    "fire_at_iso": str|None, "clarification_question": str|None}
        sa.Column(
            "context",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_pending_intents_expires_at", "pending_intents", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_pending_intents_expires_at", table_name="pending_intents")
    op.drop_table("pending_intents")
