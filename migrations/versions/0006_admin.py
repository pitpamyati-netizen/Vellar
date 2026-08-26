"""Кто держит игру.

``is_admin`` отражает настройку ``ADMIN_IDS`` на персонаже, чтобы экраны
спрашивали персонажа, а не объект настроек. Источником истины остаётся
окружение: колонка переписывается из него на каждом ``/start``, и выдать её себе
изнутри игры не может никто. Каждый персонаж, созданный до этой миграции, -
обычный игрок, об этом и говорит значение по умолчанию.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-15
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE characters ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS is_admin")
