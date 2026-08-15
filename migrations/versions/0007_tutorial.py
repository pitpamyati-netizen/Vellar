"""How far into the introduction a character has got.

One integer, read as a bitmask by ``mmorpg.domain.rules.tutorial``. Characters
created before this migration start the introduction from the beginning, which is
what the default says; nothing they have already done is lost, because every task
is marked the moment it happens again.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-15
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE characters ADD COLUMN tutorial INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS tutorial")
