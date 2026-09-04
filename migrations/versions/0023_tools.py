"""Износ инструмента: ``characters.tools`` (ADR 0056).

Сколько работы каждый инструмент уже отработал: имя вещи - и потраченная
прочность. Хранится, потому что износ ниоткуда не пересчитывается: он и есть то
единственное, что игрок делает с инструментом.

Одна колонка, один документ, как ``crafts`` (миграция 0005). Персонажи начинают
с пустым документом: всё, что у них надето, считается новым - задним числом
никого не стачивают.

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-04
"""

from __future__ import annotations

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE characters ADD COLUMN tools JSONB NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS tools")
