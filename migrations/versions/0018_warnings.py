"""Предупреждения на аккаунте.

Предупреждение — не наказание, а счётчик: смотритель отмечает им «так больше не
надо», и по нему видно, кому это говорили и сколько раз. Лежит оно на аккаунте
по той же причине, что блокировка и чёрный список: счётчик, который обнуляют,
заведя второго персонажа, ничего не считает.

``warnings`` не уходит в минус: «снять предупреждение» останавливается на нуле.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-29
"""

from __future__ import annotations

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN warnings INT NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS warnings")
