"""Trades: the escrow that holds a pending offer, and the journal of settled ones.

Until now an offer lived only in Redis and moved nothing until it was answered.
It now holds the author's item or gold from the moment it is published, and value
a player has parted with may not depend on a cache that expires on its own - so
trades get a table (Roadmap 2.3).

The time columns are unix seconds rather than TIMESTAMPTZ. The rules take ``now``
as an argument and know nothing about clocks (Claude.md, rule 1), and expiry has
to mean exactly the same thing here and in the in-memory adapter. ``logged_at``
is the one wall-clock column, and it exists for whoever reads this journal after
a dispute - it is never read by the game.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-13
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE trades (
            id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            -- The group the offer was made in: two groups never fight over the
            -- short numbers players type at each other.
            scope        TEXT NOT NULL,
            number       SMALLINT NOT NULL,
            kind         TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'pending',
            price        BIGINT NOT NULL,
            -- The duty, charged once on settlement and credited to nobody.
            tax          BIGINT NOT NULL DEFAULT 0,
            item_id      TEXT NOT NULL,
            -- The name as it was shown, so the journal still reads correctly
            -- after the content files are renamed (Roadmap 1.4).
            item_name    TEXT NOT NULL,
            quantity     INTEGER NOT NULL DEFAULT 1,
            author_user_id      BIGINT NOT NULL,
            author_character_id BIGINT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            author_name         TEXT NOT NULL,
            target_user_id      BIGINT NOT NULL,
            target_character_id BIGINT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            target_name         TEXT NOT NULL,
            created_at   BIGINT NOT NULL,
            settled_at   BIGINT,
            logged_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT trades_kind CHECK (kind IN ('sell', 'buy')),
            CONSTRAINT trades_status
                CHECK (status IN ('pending', 'accepted', 'declined', 'expired')),
            CONSTRAINT trades_number_range CHECK (number BETWEEN 1 AND 999),
            CONSTRAINT trades_price_non_negative CHECK (price >= 0 AND tax >= 0),
            CONSTRAINT trades_quantity_positive CHECK (quantity > 0)
        )
        """
    )

    # One offer per number at a time, and no more: this index is what turns a
    # race between two proposals into a rejected insert instead of two live
    # offers a player cannot tell apart. Closed rows keep their number for the
    # journal, which is why the index is partial.
    op.execute(
        "CREATE UNIQUE INDEX trades_pending_number_key ON trades (scope, number)"
        " WHERE status = 'pending'"
    )
    # The sweep that returns stakes reads only pending rows, oldest first.
    op.execute(
        "CREATE INDEX trades_pending_age_idx ON trades (scope, created_at) WHERE status = 'pending'"
    )
    op.execute("CREATE INDEX trades_author_idx ON trades (author_character_id)")
    op.execute("CREATE INDEX trades_target_idx ON trades (target_character_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS trades")
