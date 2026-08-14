"""Privacy: a profile a player may close, and the people they refuse to deal with.

Both belong to the account rather than to the character. A black list a player
could walk around by rolling a second character would not be one, and neither
would a hidden profile that reopens on the next character (Roadmap 2.5).

``blocks`` holds one row per direction. The pair is closed both ways in the rules,
not in the schema, so lifting a block is always the decision of whoever made it.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-13
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Open by default: privacy is a choice a player makes, not a state they have
    # to leave before anyone can see them.
    op.execute("ALTER TABLE users ADD COLUMN show_profile BOOLEAN NOT NULL DEFAULT TRUE")
    op.execute(
        """
        CREATE TABLE blocks (
            owner_id   BIGINT NOT NULL,
            blocked_id BIGINT NOT NULL,
            created_at BIGINT NOT NULL,
            PRIMARY KEY (owner_id, blocked_id),
            CONSTRAINT blocks_not_self CHECK (owner_id <> blocked_id)
        )
        """
    )
    # Every group command reads the list from both ends: does the author block the
    # target, does the target block the author. The primary key serves the first
    # question, this index the second.
    op.execute("CREATE INDEX blocks_blocked_idx ON blocks (blocked_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS blocks")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS show_profile")
