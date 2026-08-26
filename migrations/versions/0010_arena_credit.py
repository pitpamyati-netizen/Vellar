"""Что Круг долгов держит с персонажа.

Победа когда-то платила вдвое из ниоткуда, и это делало Круг единственным местом
в игре, где золото появлялось без того, чтобы кто-то кого-то победил. Теперь
добавка сверх возвращённой ставки идёт из ставок, которые Круг уже взял с этого
персонажа, и эта колонка и есть тот залог.

Существующие персонажи начинают с нуля: то, что они проиграли Кругу до этой
миграции, нигде не держалось, и зачесть это сейчас значило бы выдумать ровно то
золото, ради невыдумывания которого эта правка и существует.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-16
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE characters ADD COLUMN arena_credit INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS arena_credit")
