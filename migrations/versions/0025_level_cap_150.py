"""Потолок стал сто пятьдесят, и кривая опыта - новой (ADR 0058).

Полоса уровней сжалась вдвое, а очков за уровень стало больше: характеристики
теперь и растут сами, и раздаются вчетверо щедрее. Сохранённому персонажу это
надо отдать, а не отобрать.

Что делает миграция.

- Уровень выше потолка опускается на потолок. Всё нажитое - золото, вещи,
  умения, задания - остаётся при персонаже: уровень был единственным, что
  перестало существовать.
- Опыт пересчитывается по новой кривой на начало того уровня, где персонаж
  стоит. Прогресс внутри уровня теряется - его нечем перевести: старая кривая
  считала другие числа, и попытка «дотянуть долю» вернула бы персонажа на
  уровень назад или подняла бы на два вперёд.
- Разница в очках доначисляется нераспределённой: за уровень теперь дают четыре
  очка характеристик вместо двух и два очка умений вместо одного, а при создании
  десять вместо пяти. Уже вложенное не трогается вовсе.
- Проверка уровня в схеме переписана на новый потолок.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-04
"""

from __future__ import annotations

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

MAX_LEVEL = 150


def upgrade() -> None:
    op.execute("ALTER TABLE characters DROP CONSTRAINT IF EXISTS characters_level_range")
    op.execute(f"UPDATE characters SET level = LEAST(level, {MAX_LEVEL})")
    # Новая кривая: 50 * L^1.45 за уровень (``domain/rules/progression.py``).
    op.execute(
        """
        UPDATE characters AS c
        SET experience = COALESCE(
            (
                SELECT SUM(ROUND(50 * POWER(step, 1.45)))
                FROM generate_series(1, c.level - 1) AS step
            ),
            0
        )::BIGINT
        """
    )
    # Что новая раздача даёт сверх старой: (10 + 4*(L-1)) - (5 + 2*(L-1)) очков
    # характеристик и (2 - 1)*(L-1) очков умений.
    op.execute(
        """
        UPDATE characters
        SET unspent_stat_points = unspent_stat_points + 5 + 2 * (level - 1),
            unspent_skill_points = unspent_skill_points + (level - 1)
        """
    )
    op.execute(
        f"ALTER TABLE characters ADD CONSTRAINT characters_level_range "
        f"CHECK (level BETWEEN 1 AND {MAX_LEVEL})"
    )


def downgrade() -> None:
    # Уровни, срезанные потолком, и опыт по старой кривой обратно не восстановить:
    # вниз возвращается только сама проверка.
    op.execute("ALTER TABLE characters DROP CONSTRAINT IF EXISTS characters_level_range")
    op.execute(
        "ALTER TABLE characters ADD CONSTRAINT characters_level_range "
        "CHECK (level BETWEEN 1 AND 300)"
    )
