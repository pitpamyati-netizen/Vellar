"""What the Debt Circle holds of a character.

A win used to pay double out of nothing, which made the Circle the one place in
the game where gold appeared without anybody beating anything (Roadmap, "Риски").
Now the top-up above the returned stake comes out of the stakes the Circle has
already taken from this character, and this column is that hold.

Existing characters start at zero: what they lost into the Circle before this
migration was never held anywhere, and crediting it now would invent exactly the
gold this change exists to stop inventing.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-16
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE characters ADD COLUMN arena_credit INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS arena_credit")
