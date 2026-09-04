"""Износ надетого: ``characters.tools`` стала ``characters.wear`` (ADR 0057).

Колонка та же и документ тот же - имя вещи и потраченная прочность, - но точат
её теперь не одни сборы. Инструмент стачивается о работу (ADR 0056), снаряжение
- о бои, и запись у них одна: две одинаковые вещи в Vellar это одна вещь дважды,
а два счётчика на одну карту износа разошлись бы в первый же день.

Переименование, а не новая колонка: сточенное сборами остаётся сточенным.

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-04
"""

from __future__ import annotations

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE characters RENAME COLUMN tools TO wear")


def downgrade() -> None:
    op.execute("ALTER TABLE characters RENAME COLUMN wear TO tools")
