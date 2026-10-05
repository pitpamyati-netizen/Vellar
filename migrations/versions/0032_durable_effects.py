"""M02.2: постоянные отметки наград и экономические счётчики.

Revision ID: 0032
Revises: 0031
"""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE durable_effects (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL CHECK (value ~ '^[0-9]+$'),
            operation_id TEXT REFERENCES economic_operations(id),
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE durable_effects")
