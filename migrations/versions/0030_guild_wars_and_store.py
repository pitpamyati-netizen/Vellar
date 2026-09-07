"""Гильдия воюет и хранит: две таблицы к тем же гильдиям (ADR 0077).

- ``guild_items`` - хранилище гильдии: по строке на вид вещи, как в сумке
  игрока. Место считается видами, а не штуками, и сколько их у гильдии, решает
  её ступень (``content/guilds.toml``, ``store_slots``).
- ``guild_wars`` - война двух гильдий: кто с кем, ставка, до какого переворота
  прилавка и счёт с обеих сторон. Срок войны - номер переворота, а не время:
  таймеров в игре нет, и война их не заводит (``Claude.md``, правило 3).

Подряда тут нет нарочно: он живёт переворот, и счёт сделанного держит кэш со
сроком, как разовость надбавки сводки (ADR 0053).

Всё каскадит от ``guilds``: распущенная гильдия уносит и своё добро, и свои
войны. Частичный индекс держит «одна незакрытая война на гильдию».

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-06
"""

from __future__ import annotations

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE guild_items (
            guild_id BIGINT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
            item_id  TEXT   NOT NULL,
            quantity BIGINT NOT NULL DEFAULT 0 CHECK (quantity >= 0),
            PRIMARY KEY (guild_id, item_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE guild_wars (
            id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            challenger_id    BIGINT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
            defender_id      BIGINT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
            stake            BIGINT NOT NULL DEFAULT 0 CHECK (stake >= 0),
            started          BIGINT NOT NULL DEFAULT 0,
            ends             BIGINT NOT NULL DEFAULT 0,
            challenger_score BIGINT NOT NULL DEFAULT 0 CHECK (challenger_score >= 0),
            defender_score   BIGINT NOT NULL DEFAULT 0 CHECK (defender_score >= 0),
            over             BOOLEAN NOT NULL DEFAULT FALSE,
            CHECK (challenger_id <> defender_id)
        )
        """
    )
    # Одна незакрытая война на гильдию - с обеих сторон.
    op.execute(
        "CREATE UNIQUE INDEX guild_wars_one_per_challenger ON guild_wars (challenger_id)"
        " WHERE NOT over"
    )
    op.execute(
        "CREATE UNIQUE INDEX guild_wars_one_per_defender ON guild_wars (defender_id) WHERE NOT over"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS guild_wars")
    op.execute("DROP TABLE IF EXISTS guild_items")
