"""Движения золота — второй приёмник журнала (ADR 0044).

``mmorpg.economy_log`` пишет строку в журнал на каждое движение золота, кроме
передачи из рук в руки. Теперь то же событие пишет ещё и строку сюда, чтобы
смотритель мог увидеть срез по одному игроку на его карточке.

Таблица — не источник истины: строка может отстать, потеряться или
продублироваться. Ни одно игровое правило её не читает.

Индекс — по ``(character_id, at DESC)``: срез всегда по одному игроку, свежее
сначала.

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-30
"""

from __future__ import annotations

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE gold_flow (
            id           BIGSERIAL PRIMARY KEY,
            at           BIGINT NOT NULL DEFAULT 0,
            flow         TEXT NOT NULL,
            amount       BIGINT NOT NULL DEFAULT 0,
            character_id BIGINT NOT NULL DEFAULT 0,
            detail       TEXT NOT NULL DEFAULT ''
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_gold_flow_character"
        " ON gold_flow (character_id, at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_gold_flow_character")
    op.execute("DROP TABLE IF EXISTS gold_flow")
