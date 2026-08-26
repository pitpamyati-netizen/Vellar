"""Круг долгов: что персонаж в нём выиграл и проиграл.

Два счётчика, и больше ничего. Арена платит золотом в минуту боя, поэтому нести
через время нечего; эти колонки существуют ради таблицы сезона и ради той
строки, которую экран арены показывает игроку о нём самом.

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
    # Таблица сезона задаёт один вопрос - у кого больше побед - и задаёт его часто.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_characters_arena_wins"
        " ON characters (arena_wins DESC, level DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_characters_arena_wins")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS arena_losses")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS arena_wins")
