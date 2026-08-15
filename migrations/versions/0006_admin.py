"""Who keeps the game.

``is_admin`` mirrors the ``ADMIN_IDS`` setting onto the character, so the screens
can ask the character instead of the settings object. The environment stays the
source of truth: the column is rewritten from it on every ``/start``, and nobody
can grant it to themselves from inside the game. Every character created before
this migration is an ordinary player, which is what the default says.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-15
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE characters ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS is_admin")
