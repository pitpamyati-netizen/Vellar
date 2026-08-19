"""Composition root.

Everything is wired here and nowhere else: settings are read, content is loaded
and validated, adapters are chosen for the environment, pools are opened,
middlewares and routers are attached, and the bot starts.

Three run modes (``docs/architecture.md``):

- ``APP_ENV=local`` - long polling with in-memory adapters, no PostgreSQL and no
  Redis required, so the game is playable with just a bot token;
- ``APP_ENV=dev``   - long polling against real PostgreSQL and Redis;
- ``APP_ENV=prod``  - aiohttp webhook against real PostgreSQL and Redis.

The event loop is the stdlib ``asyncio.Runner``; uvloop is deliberately absent
(``docs/adr/0004-no-uvloop.md``).
"""

from __future__ import annotations

import asyncio
import signal
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage

from mmorpg.application.services.content import ContentRegistry
from mmorpg.config import AppEnv, Settings, load_settings
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.ports.repositories import IdempotencyStore
from mmorpg.health import heartbeat
from mmorpg.infrastructure.cache import (
    InMemoryIdempotencyStore,
    InMemoryLocationStateCache,
    InMemoryStateCache,
)
from mmorpg.infrastructure.content import load_content
from mmorpg.infrastructure.persistence import (
    InMemoryCharacterRepository,
    InMemoryContentOverlayRepository,
    InMemoryInventoryRepository,
    InMemoryPrivacyRepository,
    InMemoryTradeRepository,
    InMemoryUserRepository,
)
from mmorpg.logging import configure_logging, get_logger
from mmorpg.monitoring import install_slow_callback_detector
from mmorpg.presentation.telegram.broadcast import ChannelBroadcaster
from mmorpg.presentation.telegram.cleanup import MessageReaper
from mmorpg.presentation.telegram.handlers import combat, creation, group, play
from mmorpg.presentation.telegram.middlewares.dependencies import (
    Dependencies,
    DependencyMiddleware,
)
from mmorpg.presentation.telegram.middlewares.errors import ErrorMiddleware
from mmorpg.presentation.telegram.middlewares.idempotency import IdempotencyMiddleware
from mmorpg.presentation.telegram.middlewares.retry import RetryRequestMiddleware
from mmorpg.retry import RetryPolicy, keep_trying

logger = get_logger(__name__)


@dataclass(slots=True)
class Application:
    """The assembled bot, ready to run."""

    settings: Settings
    content: GameContent
    bot: Bot
    dispatcher: Dispatcher
    stack: AsyncExitStack


async def build_application(settings: Settings) -> Application:
    """Load content, choose adapters, wire routers. Nothing runs yet."""
    logger.info("build", ref=settings.vellar_build, env=settings.app_env.value)
    content = load_content(settings.content_dir)
    logger.info(
        "content_loaded",
        races=len(content.races),
        classes=len(content.classes),
        traits=len(content.traits),
        skills=len(content.skills),
        cities=len(content.cities),
        crafts=len(content.crafts),
        recipes=len(content.recipes),
    )

    stack = AsyncExitStack()
    # Правки смотрителя ложатся поверх прочитанного и живут в хранилище, поэтому
    # реестр наполняется здесь же, до первого обновления (``docs/keeper.md``).
    registry = ContentRegistry(content)
    storage, dependencies, idempotency = await _build_adapters(settings, registry, stack)
    edits = await dependencies.registry.reload(dependencies.overlays)
    logger.info("overlay_loaded", edits=edits, broken=len(registry.problems()))

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        # parse_mode stays None: Markdown is read aloud by screen readers.
        default=DefaultBotProperties(parse_mode=None),
    )
    # A screen that died on a broken socket is sent again rather than becoming
    # silence: the player has no other way to tell the two apart.
    bot.session.middleware(RetryRequestMiddleware(RetryPolicy.from_settings(settings)))
    dispatcher = Dispatcher(storage=storage)

    # Order matters: drop duplicates first, then inject, then catch failures
    # around the handler itself.
    dispatcher.update.outer_middleware(IdempotencyMiddleware(idempotency))
    dispatcher.update.outer_middleware(DependencyMiddleware(dependencies))
    dispatcher.message.middleware(ErrorMiddleware())

    dispatcher.include_router(creation.build_router())
    # The fight router goes first: it claims the two combat states, and the play
    # router below filters on the whole Play group, which includes them.
    dispatcher.include_router(combat.build_router())
    dispatcher.include_router(play.build_router())

    # The group router owns the deletion clock for what it posts there. Its tasks
    # are cancelled with the rest of the stack, so a shutdown does not hang on
    # five minutes of pending deletions.
    reaper = MessageReaper()
    stack.push_async_callback(reaper.aclose)
    dispatcher.include_router(group.build_router(reaper))

    dependencies.broadcasts.sink = bot
    logger.info(
        "broadcasts",
        channel=settings.channel_id or "not configured",
        group=settings.group_id or "not configured",
    )

    return Application(
        settings=settings, content=content, bot=bot, dispatcher=dispatcher, stack=stack
    )


async def _build_adapters(
    settings: Settings, registry: ContentRegistry, stack: AsyncExitStack
) -> tuple[BaseStorage, Dependencies, IdempotencyStore]:
    """In-memory for local, PostgreSQL and Redis otherwise (ADR 0005)."""
    if not settings.uses_external_storage:
        logger.warning(
            "using_in_memory_adapters",
            detail="APP_ENV=local: state is lost on restart, never deploy this way",
        )
        dependencies = Dependencies(
            settings=settings,
            registry=registry,
            users=InMemoryUserRepository(),
            characters=InMemoryCharacterRepository(),
            inventory=InMemoryInventoryRepository(),
            trades=InMemoryTradeRepository(),
            privacy=InMemoryPrivacyRepository(),
            state_cache=InMemoryStateCache(),
            locations=InMemoryLocationStateCache(),
            overlays=InMemoryContentOverlayRepository(),
            # The sink is attached once the Bot exists; until then, and whenever
            # CHANNEL_ID is empty, announcing is a no-op.
            broadcasts=ChannelBroadcaster(sink=None, chat_id=settings.channel_id),
        )
        return MemoryStorage(), dependencies, InMemoryIdempotencyStore()

    from aiogram.fsm.storage.redis import RedisStorage

    from mmorpg.infrastructure.cache.redis_cache import (
        RedisIdempotencyStore,
        RedisLocationStateCache,
        RedisStateCache,
    )
    from mmorpg.infrastructure.persistence.pool import (
        create_postgres_pool,
        create_redis_client,
        wait_for_redis,
    )
    from mmorpg.infrastructure.persistence.postgres import (
        PostgresCharacterRepository,
        PostgresContentOverlayRepository,
        PostgresInventoryRepository,
        PostgresPrivacyRepository,
        PostgresTradeRepository,
        PostgresUserRepository,
    )

    # Both waits are patient: a stack that comes up together does not come up in
    # order, and a database that needs five more seconds is not a reason to exit.
    pool = await create_postgres_pool(settings)
    stack.push_async_callback(pool.close)
    redis = create_redis_client(settings)
    stack.push_async_callback(redis.aclose)
    await wait_for_redis(redis, settings)

    logger.info(
        "pools_ready",
        postgres_max=settings.postgres_pool_max,
        repeats=settings.reconnect_attempts,
        max_wait=settings.reconnect_max_delay_seconds,
    )

    dependencies = Dependencies(
        settings=settings,
        registry=registry,
        users=PostgresUserRepository(pool),
        characters=PostgresCharacterRepository(pool),
        inventory=PostgresInventoryRepository(pool),
        trades=PostgresTradeRepository(pool),
        privacy=PostgresPrivacyRepository(pool),
        state_cache=RedisStateCache(redis),
        locations=RedisLocationStateCache(redis),
        overlays=PostgresContentOverlayRepository(pool),
        broadcasts=ChannelBroadcaster(sink=None, chat_id=settings.channel_id),
    )
    return RedisStorage(redis), dependencies, RedisIdempotencyStore(redis)


async def run_polling(app: Application) -> None:
    """Long polling: the transport for local play and for the Docker stack.

    One process only. Telegram hands ``getUpdates`` to a single consumer, so a
    second replica would fight the first for every update.
    """
    settings = app.settings
    logger.info(
        "starting_polling",
        env=settings.app_env.value,
        concurrency_limit=settings.concurrency_limit,
    )
    async with app.stack, heartbeat(settings):
        try:
            await _greet_telegram(app)
            await app.bot.delete_webhook(drop_pending_updates=True)
            # aiogram installs its own SIGINT/SIGTERM handlers here, so
            # "docker stop" drains in-flight updates instead of severing them.
            await app.dispatcher.start_polling(
                app.bot,
                tasks_concurrency_limit=settings.concurrency_limit,
            )
        finally:
            await app.bot.session.close()


async def _greet_telegram(app: Application) -> None:
    """Fail fast and in plain language on a bad token.

    Without this the first API call raises deep inside aiogram and the operator
    gets a stack trace instead of "your token is wrong".

    A wrong token is the only thing that stops the bot here. Telegram being
    unreachable is not: the start waits it out, the same way it waits for the
    database, because a network that is down at boot is usually up a moment later.
    """
    from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError

    try:
        me = await keep_trying(
            app.bot.get_me,
            policy=RetryPolicy.from_settings(app.settings),
            seconds=app.settings.startup_wait_seconds,
            what="telegram",
            recoverable=lambda error: isinstance(error, TelegramNetworkError | OSError),
        )
    except TelegramUnauthorizedError as error:
        msg = (
            "Telegram rejected BOT_TOKEN. Check the value in .env - "
            "get a fresh token from @BotFather if you are unsure."
        )
        raise SystemExit(msg) from error
    logger.info("connected", bot=me.username or str(me.id))


def _stop_event() -> asyncio.Event:
    """An event set by SIGINT or SIGTERM.

    Polling gets this from aiogram; the webhook runner has to ask for it, and
    without it ``docker stop`` would sever open connections instead of letting
    the exit stack close the pools.
    """
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Windows has no loop-level signal handlers; Ctrl+C still raises there.
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    return stop


async def run_webhook(app: Application) -> None:
    """Production transport: aiohttp serving the webhook."""
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
    from aiohttp import web

    settings = app.settings
    secret = settings.webhook_secret.get_secret_value()

    await _greet_telegram(app)
    await app.bot.set_webhook(
        url=settings.webhook_url,
        secret_token=secret,
        drop_pending_updates=True,
    )
    logger.info("webhook_registered", url=settings.webhook_url)

    server = web.Application()
    SimpleRequestHandler(dispatcher=app.dispatcher, bot=app.bot, secret_token=secret).register(
        server, path=settings.webhook_path
    )
    setup_application(server, app.dispatcher, bot=app.bot)

    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, host=settings.webhook_host, port=settings.webhook_port)

    async with app.stack, heartbeat(settings):
        stop = _stop_event()
        try:
            await site.start()
            logger.info("webhook_serving", host=settings.webhook_host, port=settings.webhook_port)
            await stop.wait()
            logger.info("shutdown_requested")
        finally:
            # aiohttp drains the in-flight requests before this returns.
            await runner.cleanup()
            await app.bot.session.close()


async def _amain() -> None:
    settings = load_settings()
    configure_logging(settings)

    if not settings.bot_token.get_secret_value():
        msg = "BOT_TOKEN is not set. Copy .env.example to .env and put your token in it."
        raise SystemExit(msg)

    install_slow_callback_detector(asyncio.get_running_loop(), settings)

    app = await build_application(settings)
    if settings.app_env is AppEnv.PROD:
        await run_webhook(app)
    else:
        await run_polling(app)


def main() -> None:
    """Entry point. Stdlib runner, no uvloop (ADR 0004)."""
    try:
        with asyncio.Runner() as runner:
            runner.run(_amain())
    except (KeyboardInterrupt, SystemExit) as stop:
        logger.info("shutdown", reason=type(stop).__name__)


if __name__ == "__main__":
    main()
