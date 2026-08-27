"""Гильдии: объединения игроков со званиями и общей казной.

Три таблицы не понадобилось: казна - это число на строке гильдии, а не журнал.
``guilds`` - по строке на гильдию (имя уникально без учёта регистра, как имена
персонажей), ``guild_members`` - по строке на каждого, включая основателя, с
званием (0 участник, 1 офицер, 2 основатель). Уникальный индекс по
``character_id`` - правило «в двух гильдиях сразу не стоит никто».

Всё каскадит от ``characters``: удалённый персонаж уносит свою строку, удалённый
основатель - всю гильдию. Казна при этом просто исчезает вместе с гильдией -
как исчезает золото удалённого персонажа.

Приглашения в гильдию, как и в отряд, лежат в кэше со сроком, а не здесь
(``Claude.md``, правило 8).

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-27
"""

from __future__ import annotations

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE guilds (
            id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            name       TEXT NOT NULL,
            founder_id BIGINT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            vault_gold BIGINT NOT NULL DEFAULT 0 CHECK (vault_gold >= 0),
            created_at BIGINT NOT NULL DEFAULT 0
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX guilds_name_key ON guilds (lower(name))")
    op.execute(
        """
        CREATE TABLE guild_members (
            guild_id     BIGINT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
            character_id BIGINT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            rank         SMALLINT NOT NULL DEFAULT 0 CHECK (rank BETWEEN 0 AND 2),
            PRIMARY KEY (guild_id, character_id)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX guild_members_one_per_character ON guild_members (character_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS guild_members")
    op.execute("DROP TABLE IF EXISTS guilds")
