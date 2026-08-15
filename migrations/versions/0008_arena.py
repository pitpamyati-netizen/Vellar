"""The Debt Circle: what a character has won and lost in it.

Two counters, nothing else. The arena pays in gold at the moment of the fight, so
there is no balance to carry; these columns exist for the season table and for the
line the arena screen shows a player about themselves.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-15
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE characters ADD COLUMN arena_wins INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE characters ADD COLUMN arena_losses INTEGER NOT NULL DEFAULT 0")
    # The season table asks one question - who has won most - and asks it often.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_characters_arena_wins"
        " ON characters (arena_wins DESC, level DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_characters_arena_wins")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS arena_losses")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS arena_wins")
