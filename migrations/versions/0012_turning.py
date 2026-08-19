"""Оборот, Печать Палаты и голос в счётном вопросе.

Четыре колонки на персонаже и ни одной новой таблицы: голос — это не событие, а
состояние, и считать его надо одним запросом по тем, кто сейчас в игре
(``docs/endgame.md``). Кто ответил на прошлый вопрос, видно по ``turning_cycle``:
голос за прошлый цикл в этом не считается.

``pledges`` — что уже ушло в Обороты. Без этого списка грань, выбираемую
бесплатно, можно было бы заложить и выбрать заново, и Печати печатались бы из
воздуха.

Существующие персонажи начинают с нуля Печатей: Оборот совершают руками, задним
числом его никому не записывают.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-19
"""

from __future__ import annotations

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE characters ADD COLUMN seals INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE characters ADD COLUMN pledges JSONB NOT NULL DEFAULT '[]'::jsonb")
    op.execute("ALTER TABLE characters ADD COLUMN turning_cycle TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE characters ADD COLUMN turning_answer TEXT NOT NULL DEFAULT ''")
    # Счёт голосов идёт по открытому вопросу и ни по чему больше.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_characters_turning"
        " ON characters (turning_cycle) WHERE turning_answer <> ''"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_characters_turning")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS turning_answer")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS turning_cycle")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS pledges")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS seals")
