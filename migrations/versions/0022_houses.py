"""Дом игрока: ``characters.house_id`` (ADR 0049).

В каком из семи великих домов состоит игрок. Пусто - ни в каком. Даёт доступ к
технике дома (пассивный свёрток прибавок); уход под новое имя (ADR 0048) эту
колонку не трогает.

Персонажи начинают без дома: вступают руками, задним числом никого не записывают.

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-30
"""

from __future__ import annotations

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE characters ADD COLUMN house_id TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS house_id")
