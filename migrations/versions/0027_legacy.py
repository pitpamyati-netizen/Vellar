"""Наследие вместо голосов совета: ``characters.legacy_ids`` (ADR 0070).

Большого совета в игре больше нет, и двух колонок под ответ на его вопрос - тоже:
``turning_cycle`` и ``turning_answer`` держали голос, который некому считать.

На их место встаёт ``legacy_ids`` - вехи, названные наследием. Уход теперь
возвращает розданные очки нерозданными, а вместе с ними падают характеристики и
теряются вехи (ADR 0068); наследие - это то, что игрок уносит через сброс.

Массив, как ``trait_ids`` и ``subclass_ids``: сколько слотов будет завтра, решает
``content/turnings.toml``, а не схема базы. Пустой у всех, кто уже играет: назвать
веху за игрока значило бы решить за него, что в его сборке было главным.

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-05
"""

from __future__ import annotations

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE characters ADD COLUMN IF NOT EXISTS legacy_ids TEXT[] NOT NULL DEFAULT '{}'"
    )
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS turning_cycle")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS turning_answer")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE characters ADD COLUMN IF NOT EXISTS turning_cycle TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE characters ADD COLUMN IF NOT EXISTS turning_answer TEXT NOT NULL DEFAULT ''"
    )
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS legacy_ids")
