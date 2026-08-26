"""Как далеко персонаж зашёл во вступлении.

Одно целое число, которое ``mmorpg.domain.rules.tutorial`` читает битовой
маской. Персонажи, созданные до этой миграции, начинают вступление сначала - об
этом и говорит значение по умолчанию; ничто из уже сделанного не теряется,
потому что каждое дело отмечается в ту минуту, когда случается снова.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-15
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE characters ADD COLUMN tutorial INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS tutorial")
