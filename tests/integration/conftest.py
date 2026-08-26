"""Настоящие PostgreSQL и Redis - или ничего.

Эти тесты выполняют тот самый SQL и те самые команды Redis, которые шлёт бот.
Остальной набор работает на адаптерах в памяти, а значит, ошибка в самом SQL -
опечатка, колонка, которую PostgreSQL не примет - невидима больше нигде. Этот
пакет существует, чтобы закрыть ту брешь.

Пропускаются, но не падают, когда службы не подняты: ``docker compose up -d
postgres redis`` - и они работают.

Соединения открываются **один раз на весь прогон**, а не на каждый тест.
Открытие стоит здесь секунды две на каждое - разрешение имени, а не база, - и
сорок шесть таких открытий делали этот пакет в шестнадцать раз медленнее всего
остального набора. Делить их безопасно, потому что *через* них не делится
ничего: каждый тест владеет своими строками и убирает их за собой, а пул - это
пул.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from mmorpg.config import Settings

# Общий на прогон цикл событий: пул asyncpg и клиент Redis привязаны к тому
# циклу, в котором созданы, поэтому «на прогон» должно быть и то, и другое.
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def _settings() -> Settings:
    return Settings(app_env="dev", bot_token="0:test")  # type: ignore[call-arg]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def pool() -> AsyncIterator[object]:
    """Пул asyncpg против накатанной базы - или пропуск.

    Обёрнут ровно так, как его оборачивает работающая игра (``ReconnectingPool``),
    поэтому здешний SQL идёт через тот же посредник, что и запросы игроков.
    """
    import asyncpg

    from mmorpg.infrastructure.persistence.reconnect import ReconnectingPool
    from mmorpg.retry import RetryPolicy

    settings = _settings()
    try:
        created = await asyncpg.create_pool(dsn=settings.postgres_dsn, min_size=1, max_size=4)
    except (OSError, asyncpg.PostgresError) as unreachable:
        pytest.skip(f"PostgreSQL is not reachable: {unreachable}")

    assert created is not None
    try:
        # Адаптеры ждут схему из migrations/, а не пустую базу.
        exists = await created.fetchval("SELECT to_regclass('public.users')")
        if exists is None:
            pytest.skip("the database has no schema: run 'alembic upgrade head' first")
        yield ReconnectingPool(created, RetryPolicy.from_settings(settings))
    finally:
        await created.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def redis() -> AsyncIterator[object]:
    """Клиент Redis на отдельной базе, собранный так же, как его собирает игра."""
    from redis.asyncio import Redis

    from mmorpg.infrastructure.persistence.pool import create_redis_client

    settings = _settings()
    client: Redis = create_redis_client(settings)
    try:
        await client.ping()
    # redis-py бросает несколько несвязанных типов на «сервера там нет».
    except Exception as unreachable:
        await client.aclose()
        pytest.skip(f"Redis is not reachable: {unreachable}")

    try:
        yield client
    finally:
        await client.aclose()
