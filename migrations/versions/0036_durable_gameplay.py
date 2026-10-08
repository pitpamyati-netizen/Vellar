"""Постоянный бой, экран и личность занятого места."""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE gameplay_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            revision BIGINT NOT NULL DEFAULT 1 CHECK (revision > 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup: active battles cannot be dropped safely")
