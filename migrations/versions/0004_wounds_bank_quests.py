"""Wounds that outlive a fight, a strongbox, and the contract ledger.

Three things a character now carries between sessions (Roadmap 1.3, 1.5):

``health``    - what is left after the last fight. Zero means "as good as new",
                which is what every character created before this migration was,
                so the default needs no backfill.
``bank_gold`` - gold in the city strongbox. A lost fight takes a share of what is
                on the character, and never touches this.
``quests``    - the contract ledger: taken contracts with their counters, and the
                ids of the ones already paid out. It is a document, not a
                relation: it is only ever read and written whole, for one
                character at a time, so a table would buy nothing.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-14
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE characters ADD COLUMN health INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE characters ADD COLUMN bank_gold BIGINT NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE characters ADD COLUMN quests JSONB NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS quests")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS bank_gold")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS health")
