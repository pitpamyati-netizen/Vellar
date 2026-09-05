"""Ступени специализации: ``characters.subclass_ids`` (ADR 0069).

Взятые подклассы - это выбор игрока, а не производное: их нельзя пересчитать ни
из уровня, ни из очков, ни из чего-либо ещё (``Claude.md``, правило 8). Значит
они хранятся, и хранятся так же, как черты, - массивом идентификаторов.

Массив, а не колонка на ступень: ступеней три сегодня, и решение, какими они
будут завтра, живёт в ``content/subclasses.toml``, а не в схеме базы. Порядок в
массиве ничего не значит - свёрток прибавок складывается, а не накапливается.

Пустой массив у всех, кто уже играет: подкласс берут нарочно, и выдать его за
игрока значило бы решить за него первое настоящее решение о том, кем он будет.

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-05
"""

from __future__ import annotations

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE characters ADD COLUMN IF NOT EXISTS subclass_ids TEXT[] NOT NULL DEFAULT '{}'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS subclass_ids")
