"""The vault: gold a character left with a city bank.

Kept apart from ``gold`` on purpose. The purse is what a fight, a duty or a trade
can reach; the vault is what none of them can, and a single column holding both
would make that promise impossible to keep (Roadmap 1.5).

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
    # Nobody has stored anything yet, so every existing character starts at zero.
    op.execute(
        "ALTER TABLE characters ADD COLUMN bank_gold BIGINT NOT NULL DEFAULT 0"
        " CONSTRAINT characters_bank_gold_non_negative CHECK (bank_gold >= 0)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS bank_gold")
