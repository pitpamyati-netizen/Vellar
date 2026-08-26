"""Раны, переживающие бой, сундук и журнал заданий.

Три вещи, которые персонаж теперь носит между заходами в игру (Roadmap 1.3, 1.5):

``health``    - что осталось после последнего боя. Ноль значит «как новенький»,
                а именно таким был каждый персонаж, созданный до этой миграции,
                поэтому значению по умолчанию не нужна доливка.
``bank_gold`` - золото в городском сундуке. Проигранный бой берёт долю того, что
                при персонаже, и этого не трогает никогда.
``quests``    - журнал заданий: взятые задания со счётчиками и идентификаторы
                тех, за которые уже заплатили. Это документ, а не отношение: он
                читается и пишется только целиком и только для одного персонажа
                за раз, поэтому таблица не дала бы ничего.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-14
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE characters ADD COLUMN health INTEGER NOT NULL DEFAULT 0")
    # Сундук не уходит в минус никогда: обещание, что проигранный бой до него не
    # дотянется, стоит чего-то, только если сама колонка отказывается уходить за ноль.
    op.execute(
        "ALTER TABLE characters ADD COLUMN bank_gold BIGINT NOT NULL DEFAULT 0"
        " CONSTRAINT characters_bank_gold_non_negative CHECK (bank_gold >= 0)"
    )
    op.execute("ALTER TABLE characters ADD COLUMN quests JSONB NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS quests")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS bank_gold")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS health")
