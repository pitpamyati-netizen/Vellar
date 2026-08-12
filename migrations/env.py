"""Alembic environment.

No ORM metadata: migrations are explicit SQL, so autogenerate is deliberately not
wired up (docs/adr/0001-no-orm.md). The connection URL comes from POSTGRES_DSN and
is rewritten to the asyncpg dialect so the same DSN works for the app and for
migrations.
"""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

config = context.config

dsn = os.environ.get("POSTGRES_DSN")
if dsn:
    config.set_main_option("sqlalchemy.url", dsn.replace("postgresql://", "postgresql+asyncpg://"))

target_metadata = None


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of talking to a database."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
