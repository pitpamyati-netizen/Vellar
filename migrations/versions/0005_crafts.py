"""Craft work a character has done.

One column, one document (Roadmap 1.7). ``crafts`` maps a craft id to the work
already put into it and to the watch its last gathering happened in::

    {"mining": {"experience": 240, "cycle": 12}}

A rank is never stored: it is counted back from the experience by
``mmorpg.domain.rules.crafts``, exactly like a character level is counted from
theirs. It is read and written whole, for one character at a time, so a table
would buy nothing. Every character created before this migration has learned no
craft, which is what the empty default says.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-14
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE characters ADD COLUMN crafts JSONB NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS crafts")
