"""Новое имя вместо Печати: ``remorts`` вместо ``seals``, ``pledges`` убран.

ADR 0048. На 300 уровне игрок берёт у Престола новое имя — сброс до первого
уровня, всё нажитое остаётся. Печати, заклада и списка заложенного больше нет:
считается только число уходов.

``turning_cycle``/``turning_answer`` и индекс по ним остаются — голос в Большом
совете держится теми же двумя колонками. Игра не запущена, переносить по сути
нечего, но ``remorts = seals`` бесплатен и честнее пустого дефолта.

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-30
"""

from __future__ import annotations

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE characters ADD COLUMN remorts INTEGER NOT NULL DEFAULT 0")
    op.execute("UPDATE characters SET remorts = seals")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS pledges")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS seals")


def downgrade() -> None:
    op.execute("ALTER TABLE characters ADD COLUMN seals INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE characters ADD COLUMN pledges JSONB NOT NULL DEFAULT '[]'::jsonb")
    op.execute("UPDATE characters SET seals = remorts")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS remorts")
