"""Отряд, переживающий вылазку.

Отряд жил в кэше со сроком: два часа без действий - и он распадался сам. Это
делало «постоянный состав» невозможным по устройству. Теперь состав отряда лежит
в PostgreSQL и держится, пока его не расформируют или пока из него не уйдёт
собравший (ADR 0029).

``parties`` - по строке на собравшего; ``party_members`` - по строке на каждого,
включая собравшего (``Party.__post_init__`` кладёт его в состав). Уникальный
индекс по ``character_id`` - это правило «в двух отрядах сразу не стоит никто».
Всё каскадит от ``characters``: удалённый персонаж уносит свои строки с собой, а
удалённый собравший уносит и весь отряд.

Приглашения при этом остаются в кэше со сроком: висящий зов, который нельзя ни
принять, ни отменить, хуже, чем никакого (``Claude.md``, правило 8).

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-27
"""

from __future__ import annotations

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE parties (
            leader_id  BIGINT PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
            created_at BIGINT NOT NULL DEFAULT 0
        )
        """
    )
    op.execute(
        """
        CREATE TABLE party_members (
            leader_id    BIGINT NOT NULL REFERENCES parties(leader_id) ON DELETE CASCADE,
            character_id BIGINT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            PRIMARY KEY (leader_id, character_id)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX party_members_one_per_character ON party_members (character_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS party_members")
    op.execute("DROP TABLE IF EXISTS parties")
