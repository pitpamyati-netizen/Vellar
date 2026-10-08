"""Сохранить историю гильдии, возвратный вклад и независимые сроки M02.

Revision ID: 0034
Revises: 0033
"""

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE guilds ADD COLUMN disbanded BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE guilds ADD COLUMN archived_members JSONB NOT NULL DEFAULT '[]'")
    op.execute(
        "ALTER TABLE guilds ADD COLUMN recycled_gold BIGINT NOT NULL DEFAULT 0 CHECK "
        "(recycled_gold >= 0)"
    )
    op.execute("DROP INDEX guilds_name_key")
    op.execute("CREATE UNIQUE INDEX guilds_name_key ON guilds (lower(name)) WHERE NOT disbanded")
    op.execute("ALTER TABLE guild_wars ADD COLUMN clock_seconds BOOLEAN NOT NULL DEFAULT FALSE")
    # Старые сроки переводятся при первом чтении с настройкой прежней лавки.
    # Миграция не угадывает длительность, выбранную владельцем сервера.
    # Последний прежний счёт переносится в первый новый период; старый журнал
    # остаётся для проверки. Это консервативный переход без повторной выплаты.
    op.execute("""
        WITH legacy AS (
            SELECT *, split_part(key, ':', 1) AS scope,
                split_part(key, ':', 2) AS guild_id,
                split_part(key, ':', 3) AS subject,
                split_part(key, ':', 4) AS tail
            FROM durable_effects
            WHERE key ~ '^guild-(taken|items):[0-9]+:[0-9]+:[0-9]+$'
        ), latest AS (
            SELECT scope, guild_id, max(tail::bigint) AS period FROM legacy GROUP BY scope, guild_id
        )
        INSERT INTO durable_effects (key, value)
        SELECT l.scope || '-v2:' || l.guild_id || ':' || l.subject || ':' || '0', l.value
        FROM legacy l JOIN latest p ON l.scope=p.scope AND l.guild_id=p.guild_id AND
        l.tail::bigint=p.period
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        WITH legacy AS (
            SELECT *, split_part(key, ':', 1) AS scope, split_part(key, ':', 2) AS guild_id,
                split_part(key, ':', 3)::bigint AS period, split_part(key, ':', 4) AS kind
            FROM durable_effects
            WHERE key ~ '^guild-contract(-paid)?:[0-9]+:[0-9]+:(cull|delve|tithe)$'
        ), latest AS (SELECT guild_id, max(period) AS period FROM legacy GROUP BY guild_id)
        INSERT INTO durable_effects (key, value)
        SELECT l.scope || '-v2:' || l.guild_id || ':' || '0' || ':' || l.kind, l.value
        FROM legacy l JOIN latest p ON l.guild_id=p.guild_id AND l.period=p.period
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO durable_effects (key, value)
        SELECT 'guild-period:' || split_part(key, ':', 2) || ':' || 'contract' || ':' || 'seed',
            max(split_part(key, ':', 3)::bigint)::text
        FROM durable_effects
        WHERE key ~ '^guild-contract(-paid)?:[0-9]+:[0-9]+:(cull|delve|tithe)$'
        GROUP BY split_part(key, ':', 2) ON CONFLICT DO NOTHING
    """)

    op.execute("""
        WITH old AS (
            SELECT key, value, split_part(key, ':', 2) AS war,
                split_part(key, ':', 5)::bigint AS period
            FROM durable_effects
            WHERE key ~ '^guild-war-hit:[0-9]+:[0-9]+:[0-9]+:[0-9]+$'
        ), last AS (SELECT war, max(period) AS period FROM old GROUP BY war)
        INSERT INTO durable_effects (key, value)
        SELECT 'guild-war-hit-v2:' || o.war || ':' || split_part(o.key, ':', 3) || ':' ||
            split_part(o.key, ':', 4) || ':' || '0', o.value
        FROM old o JOIN last p ON o.war=p.war AND o.period=p.period
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    raise RuntimeError(
        "Restore a verified backup to return before M02 safety; archives must not be discarded"
    )
