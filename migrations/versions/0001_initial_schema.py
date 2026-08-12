"""Initial schema: users, characters, inventory.

Only durable state lives in PostgreSQL. Everything derived (total stats, location
layouts, shop assortments) is recomputed, and everything short-lived (FSM state,
active fights, location deltas) lives in Redis with a TTL - see
docs/architecture.md, "Storage split".

Revision ID: 0001
Revises:
Create Date: 2026-08-12
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE users (
            telegram_id BIGINT PRIMARY KEY,
            username    TEXT NOT NULL DEFAULT '',
            -- Accessibility settings. Emoji are off by default (rule 6).
            emoji       BOOLEAN NOT NULL DEFAULT FALSE,
            verbose     BOOLEAN NOT NULL DEFAULT TRUE,
            page_size   SMALLINT NOT NULL DEFAULT 8,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE characters (
            id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id               BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
            name                  TEXT NOT NULL,
            race_id               TEXT NOT NULL,
            class_id              TEXT NOT NULL,
            level                 INTEGER NOT NULL DEFAULT 1,
            experience            BIGINT NOT NULL DEFAULT 0,
            gold                  BIGINT NOT NULL DEFAULT 0,
            -- Raw allocated points only; totals are recomputed, never stored.
            stat_str              INTEGER NOT NULL DEFAULT 0,
            stat_agi              INTEGER NOT NULL DEFAULT 0,
            stat_end              INTEGER NOT NULL DEFAULT 0,
            stat_int              INTEGER NOT NULL DEFAULT 0,
            stat_wis              INTEGER NOT NULL DEFAULT 0,
            stat_cha              INTEGER NOT NULL DEFAULT 0,
            stat_lck              INTEGER NOT NULL DEFAULT 0,
            trait_ids             TEXT[] NOT NULL DEFAULT '{}',
            loadout               JSONB NOT NULL DEFAULT '{}'::jsonb,
            equipment             JSONB NOT NULL DEFAULT '{}'::jsonb,
            city_id               TEXT NOT NULL DEFAULT 'farhold',
            unspent_stat_points   INTEGER NOT NULL DEFAULT 0,
            unspent_skill_points  INTEGER NOT NULL DEFAULT 0,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT characters_level_range CHECK (level BETWEEN 1 AND 300)
        )
        """
    )
    op.execute("CREATE INDEX characters_user_id_idx ON characters (user_id)")
    op.execute("CREATE UNIQUE INDEX characters_name_key ON characters (lower(name))")

    op.execute(
        """
        CREATE TABLE inventory (
            character_id BIGINT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            item_id      TEXT NOT NULL,
            quantity     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (character_id, item_id),
            CONSTRAINT inventory_quantity_non_negative CHECK (quantity >= 0)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS inventory")
    op.execute("DROP TABLE IF EXISTS characters")
    op.execute("DROP TABLE IF EXISTS users")
