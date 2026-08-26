"""Работа, сделанная персонажем в ремёслах.

Одна колонка, один документ (Roadmap 1.7). ``crafts`` сопоставляет ремеслу уже
вложенную в него работу и стражу, в которую случился последний сбор::

    {"mining": {"experience": 240, "cycle": 12}}

Ранг не хранится никогда: его отсчитывает обратно от опыта
``mmorpg.domain.rules.crafts``, ровно как уровень персонажа отсчитывается от
своего. Читается и пишется целиком и для одного персонажа за раз, поэтому
таблица не дала бы ничего. Каждый персонаж, созданный до этой миграции, не
изучил ни одного ремесла - об этом и говорит пустое значение по умолчанию.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-14
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE characters ADD COLUMN crafts JSONB NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS crafts")
