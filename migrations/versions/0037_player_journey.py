"""M04: приватные счётчики первой сессии и понятная история результата.

Revision ID: 0037
Revises: 0036
"""

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE player_journey (
            bot_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
            first_at BIGINT NOT NULL, last_at BIGINT NOT NULL,
            character_id BIGINT NOT NULL DEFAULT 0, screen TEXT NOT NULL,
            tutorial INTEGER NOT NULL DEFAULT 0,
            reached JSONB NOT NULL DEFAULT '[]', completed_at BIGINT NOT NULL DEFAULT 0,
            sessions INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(bot_id, user_id)
        )
    """)
    op.execute("CREATE INDEX player_journey_inactive ON player_journey(bot_id, last_at)")
    op.execute("""
        CREATE TABLE player_activity (
            id BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
            operation_id TEXT NOT NULL, bot_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
            at BIGINT NOT NULL, gold BIGINT NOT NULL DEFAULT 0,
            bank BIGINT NOT NULL DEFAULT 0, experience BIGINT NOT NULL DEFAULT 0,
            levels INTEGER NOT NULL DEFAULT 0, tutorial_steps INTEGER NOT NULL DEFAULT 0,
            quests INTEGER NOT NULL DEFAULT 0, items JSONB NOT NULL DEFAULT '[]',
            PRIMARY KEY(operation_id, bot_id, user_id)
        )
    """)
    op.execute(
        "CREATE INDEX player_activity_history ON player_activity(bot_id,user_id,at DESC,id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE player_activity")
    op.execute("DROP TABLE player_journey")
