"""M02.3: очередь сообщений и общий бюджет Telegram.

Revision ID: 0033
Revises: 0032
"""

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE message_delivery (
            key TEXT PRIMARY KEY,
            sequence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
            bot_id BIGINT NOT NULL,
            chat_id TEXT NOT NULL,
            payload JSONB NOT NULL,
            priority INTEGER NOT NULL CHECK (priority IN (0, 10)),
            operation_id TEXT REFERENCES economic_operations(id),
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'sending', 'sent', 'failed')),
            attempts INTEGER NOT NULL DEFAULT 0,
            available_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch FROM clock_timestamp()),
            lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,
            lease_token TEXT NOT NULL DEFAULT '',
            response JSONB,
            last_error TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX message_delivery_pending ON message_delivery (bot_id, priority, sequence)
        WHERE status IN ('queued', 'sending')
    """)
    op.execute("""
        CREATE INDEX message_delivery_chat ON message_delivery (bot_id, chat_id, sequence)
        WHERE status IN ('queued', 'sending')
    """)
    op.execute("""
        CREATE TABLE message_delivery_budget (
            bot_id BIGINT PRIMARY KEY,
            cooldown_until DOUBLE PRECISION NOT NULL DEFAULT 0
        )
    """)
    op.execute("""
        CREATE TABLE message_delivery_attempts (
            bot_id BIGINT NOT NULL,
            chat_id TEXT NOT NULL,
            at DOUBLE PRECISION NOT NULL
        )
    """)
    op.execute(
        "CREATE INDEX message_delivery_attempts_bot ON message_delivery_attempts (bot_id, at)"
    )
    op.execute(
        "CREATE INDEX message_delivery_attempts_chat"
        " ON message_delivery_attempts (bot_id, chat_id, at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE message_delivery_attempts")
    op.execute("DROP TABLE message_delivery_budget")
    op.execute("DROP TABLE message_delivery")
