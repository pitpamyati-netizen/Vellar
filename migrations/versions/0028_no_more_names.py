"""Нового имени больше нет: ``remorts`` и ``legacy_ids`` уходят (ADR 0075).

Уход под новое имя сбрасывал уровень до первого и раздачу очков - нерозданными,
а платил процентом к характеристикам навсегда. Он отбирал единственное, что
игрок успел построить, и был при этом входом в специализацию: гибрид просил один
уход, венец - три. Конец полосы теперь держит дерево подклассов (ADR 0074), и
второй проход по первым ста пятидесяти уровням игре не нужен.

``legacy_ids`` уходит вместе с ним: вехи, названные наследием, отвечали на потерю
характеристик при сбросе, а сбрасывать больше нечего.

Прибавка к характеристикам за взятые имена исчезает вместе с колонкой, и это
честнее, чем оставить её: она платила за сброс, которого больше не бывает.
Уровень при этом не трогается - тот, кто ушёл и стоит на сороковом, остаётся на
сороковом.

Взятые ступени специализации (``subclass_ids``) остаются: они переезжают в новое
дерево по идентификатору там, где имя ветки уцелело, а исчезнувшая ветка просто
не показывается (``domain/rules/subclass.taken``).

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-06
"""

from __future__ import annotations

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS remorts")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS legacy_ids")


def downgrade() -> None:
    op.execute("ALTER TABLE characters ADD COLUMN IF NOT EXISTS remorts INTEGER NOT NULL DEFAULT 0")
    op.execute(
        "ALTER TABLE characters ADD COLUMN IF NOT EXISTS legacy_ids TEXT[] NOT NULL DEFAULT '{}'"
    )
