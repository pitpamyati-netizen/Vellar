"""Постоянное время отдыха и дробное восстановление здоровья."""

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE characters
        ADD COLUMN health_recovered_at bigint,
        ADD COLUMN health_recovery_remainder bigint NOT NULL DEFAULT 0
            CHECK (health_recovery_remainder >= 0 AND health_recovery_remainder < 60000000)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE characters DROP COLUMN health_recovery_remainder,
        DROP COLUMN health_recovered_at
    """)
