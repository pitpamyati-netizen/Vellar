"""Корень композиции.

Всё связывается здесь и больше нигде: читаются настройки, загружается и
проверяется содержимое, под окружение выбираются адаптеры, открываются пулы,
подключаются мидлвари и роутеры, стартует бот.

Четыре режима запуска (``docs/architecture.md``):

- ``APP_ENV=local`` - long polling с адаптерами в памяти, ни PostgreSQL, ни
  Redis не нужны, и игра играется с одним лишь токеном бота;
- ``APP_ENV=solo``  - long polling против настоящего PostgreSQL, а
  короткоживущее состояние держится в процессе вместо Redis: одна машина, без
  контейнеров (``docs/adr/0010-a-machine-without-containers.md``);
- ``APP_ENV=dev``   - long polling против настоящих PostgreSQL и Redis;
- ``APP_ENV=prod``  - вебхук на aiohttp против настоящих PostgreSQL и Redis.

Цикл событий - ``asyncio.Runner`` из стандартной библиотеки; uvloop отсутствует
нарочно (``docs/adr/0004-no-uvloop.md``).
"""

from __future__ import annotations

import asyncio
import math
import signal
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from typing import Any, cast

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.base import BaseStorage

from mmorpg import economy_log
from mmorpg.application.services.content import ContentRegistry
from mmorpg.application.services.group_trade import release_expired_offers
from mmorpg.application.services.guild import GuildStore
from mmorpg.application.services.party import PartyStore
from mmorpg.config import AppEnv, Settings, load_settings
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.ports.repositories import (
    IdempotencyStore,
    LocationStateCache,
    StateCache,
)
from mmorpg.health import age_seconds, heartbeat, is_alive, probe_services
from mmorpg.infrastructure.cache import (
    InMemoryIdempotencyStore,
    InMemoryLocationStateCache,
    InMemoryStateCache,
)
from mmorpg.infrastructure.cache.memory_operations import AtomicMemoryStorage
from mmorpg.infrastructure.content import load_content
from mmorpg.infrastructure.persistence import (
    InMemoryCharacterRepository,
    InMemoryContentOverlayRepository,
    InMemoryGoldFlowRepository,
    InMemoryGuildRepository,
    InMemoryInventoryRepository,
    InMemoryKeeperLogRepository,
    InMemoryPartyRepository,
    InMemoryPrivacyRepository,
    InMemoryTradeRepository,
    InMemoryUserRepository,
)
from mmorpg.infrastructure.persistence.effects import MemoryEffectState, PostgresEffectState
from mmorpg.infrastructure.persistence.journey import (
    MemoryJourneyRepository,
    PostgresJourneyRepository,
)
from mmorpg.logging import configure_logging, get_logger
from mmorpg.metrics import Metrics, reporting
from mmorpg.monitoring import install_slow_callback_detector
from mmorpg.presentation.telegram.broadcast import ChannelBroadcaster
from mmorpg.presentation.telegram.cleanup import MessageReaper
from mmorpg.presentation.telegram.handlers import combat, creation, group, play
from mmorpg.presentation.telegram.middlewares.audit import AuditMiddleware
from mmorpg.presentation.telegram.middlewares.commands import CommandMiddleware
from mmorpg.presentation.telegram.middlewares.dependencies import (
    Dependencies,
    DependencyMiddleware,
)
from mmorpg.presentation.telegram.middlewares.errors import ErrorMiddleware
from mmorpg.presentation.telegram.middlewares.idempotency import IdempotencyMiddleware
from mmorpg.presentation.telegram.middlewares.journey import JourneyMiddleware
from mmorpg.presentation.telegram.middlewares.metrics import MetricsMiddleware
from mmorpg.presentation.telegram.middlewares.moderation import BanMiddleware
from mmorpg.presentation.telegram.middlewares.reading import ReadingMiddleware
from mmorpg.presentation.telegram.middlewares.retry import RetryRequestMiddleware
from mmorpg.presentation.telegram.middlewares.sending import SendRateMiddleware, SendWindow
from mmorpg.retry import RetryPolicy, keep_trying

logger = get_logger(__name__)


@dataclass(slots=True)
class Application:
    """Собранный бот, готовый к запуску."""

    settings: Settings
    content: GameContent
    bot: Bot
    dispatcher: Dispatcher
    stack: AsyncExitStack
    #: Счётчики того, что обслужено с прошлого отчёта (``mmorpg.metrics``).
    metrics: Metrics
    health_probe: Callable[[], Awaitable[None]] | None = None


async def build_application(settings: Settings) -> Application:
    """Загрузить содержимое, выбрать адаптеры, связать роутеры. Ничего ещё не работает."""
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
    broken = registry.problems()
    logger.info("overlay_loaded", edits=edits, broken=len(broken))
    # Счёт сломанных правок говорит, что беда есть, и молчит о том, какая: правка
    # переживает и код, и содержимое, поэтому ступень, вещь или житель под ней
    # могут исчезнуть через месяц после того, как её записали. Смотритель должен
    # узнать об этом отсюда, а не от игрока, не нашедшего обещанного.
    for record, why in broken:
        logger.warning(
            "overlay_broken", kind=record.kind.value, entity=record.entity_id, why="; ".join(why)
        )

    # Ставка по предложению, которое никто не принял, возвращается автору здесь и
    # сейчас, а не тогда, когда группа снова заговорит: группа может и замолчать
    # навсегда (``application.services.group_trade.release_expired_offers``).
    released = await release_expired_offers(
        trades=dependencies.trades,
        characters=dependencies.characters,
        inventory=dependencies.inventory,
        now=int(time.time()),
    )
    if released:
        logger.info("offers_released", offers=released)

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        # parse_mode остаётся None: разметку экранный диктор читает вслух.
        default=DefaultBotProperties(parse_mode=None),
    )
    # Экран, умерший на оборванном сокете, отправляется заново, а не превращается в
    # тишину: отличить одно от другого игроку больше нечем.
    from mmorpg.infrastructure.persistence.delivery import PostgresDeliveryQueue
    from mmorpg.infrastructure.persistence.memory_delivery import MemoryDeliveryQueue
    from mmorpg.presentation.telegram.delivery import DeliveryWorker, DurableSendMiddleware
    from mmorpg.presentation.telegram.middlewares.operations import EconomicRecoveryMiddleware

    reaper = MessageReaper()
    stack.push_async_callback(reaper.aclose)
    delivery_queue = (
        PostgresDeliveryQueue(cast(Any, dependencies.characters)._pool)
        if settings.uses_postgres
        else MemoryDeliveryQueue()
    )
    boundary = getattr(dependencies.characters, "operations", None)
    worker = DeliveryWorker(
        delivery_queue,
        bot,
        sends_per_second=settings.telegram_sends_per_second,
        reaper=reaper,
        recover=getattr(boundary, "recover", None),
    )
    bot.session.middleware(DurableSendMiddleware(delivery_queue, worker))
    bot.session.middleware(RetryRequestMiddleware(RetryPolicy.from_settings(settings)))
    # Ниже неё, а значит ближе к сокету, - очередь, которая держит бота внутри счёта
    # отправок в секунду. Подождать несколько миллисекунд дешевле, чем услышать «подожди
    # несколько секунд».
    bot.session.middleware(SendRateMiddleware(SendWindow(limit=settings.telegram_sends_per_second)))
    dispatcher = Dispatcher(storage=storage)
    metrics = Metrics()
    # FSM читает состояние до выбора роутера. Незаконченный перенос экрана
    # должен завершиться раньше этого чтения, а не уже внутри старого роутера.
    dispatcher.update.outer_middleware.unregister(dispatcher.fsm)
    dispatcher.update.outer_middleware(MetricsMiddleware(metrics))
    dispatcher.update.outer_middleware(AuditMiddleware())
    # Отказ ловится снаружи транзакции: ошибка должна сначала отменить все записи.
    dispatcher.update.outer_middleware(ErrorMiddleware(metrics))
    dispatcher.update.outer_middleware(IdempotencyMiddleware(idempotency, completed_only=True))
    dispatcher.update.outer_middleware(
        EconomicRecoveryMiddleware(getattr(boundary, "recover", None), propagate_errors=True)
    )
    dispatcher.update.outer_middleware(
        CommandMiddleware(dependencies, reaper, delivery=delivery_queue, wake_delivery=worker.wake)
    )
    dispatcher.update.outer_middleware(dispatcher.fsm)

    # Постоянный результат проверен до чтения FSM. Зависимости и действующий
    # контент подставляются только для новой команды, внутри общей операции.
    dispatcher.update.outer_middleware(DependencyMiddleware(dependencies))
    # Внешняя, а не внутренняя: заблокированный не должен дойти ни до одного
    # роутера, а внутренние обёртки диспетчера до вложенных роутеров не доходят.
    dispatcher.message.outer_middleware(BanMiddleware())
    dispatcher.message.outer_middleware(ReadingMiddleware(dependencies.state_cache))
    if dependencies.journey is not None:
        dispatcher.message.outer_middleware(JourneyMiddleware(dependencies.journey))

    dispatcher.include_router(creation.build_router())
    # Роутер боя идёт первым: он забирает два боевых состояния, а роутер игры ниже
    # фильтрует по всей группе Play, куда они входят.
    dispatcher.include_router(combat.build_router())
    dispatcher.include_router(play.build_router())

    # Роутер группы владеет часами удаления того, что он там пишет. Его задачи
    # отменяются вместе со всем стеком, так что остановка не виснет на пяти минутах
    # отложенных удалений.
    dispatcher.include_router(group.build_router(reaper))

    dependencies.broadcasts.sink = bot
    worker.start()
    stack.push_async_callback(worker.aclose)

    async def health_probe() -> None:
        if not worker.healthy(settings.heartbeat_stale_after):
            raise RuntimeError("Delivery worker stopped progressing")
        pool = cast(Any, dependencies.characters)._pool if settings.uses_postgres else None
        client = pool.raw.economic_cache if pool is not None and settings.uses_redis else None
        await probe_services(pool, client, str(bot.id))

    logger.info(
        "broadcasts",
        channel=settings.channel_id or "not configured",
        group=settings.group_id or "not configured",
    )

    return Application(
        settings=settings,
        content=content,
        bot=bot,
        dispatcher=dispatcher,
        stack=stack,
        metrics=metrics,
        health_probe=health_probe,
    )


async def _build_adapters(
    settings: Settings, registry: ContentRegistry, stack: AsyncExitStack
) -> tuple[BaseStorage, Dependencies, IdempotencyStore]:
    """В памяти - для local, PostgreSQL - дальше вверх, Redis - для dev и prod.

    В режимах с PostgreSQL мир, бой и экран постоянны (ADR 0090).
    Redis ускоряет временные данные в dev/prod; solo держит их в памяти.
    """
    if not settings.uses_postgres:
        logger.warning(
            "using_in_memory_adapters",
            detail="APP_ENV=local: state is lost on restart, never deploy this way",
        )
        memory_cache = MemoryEffectState(InMemoryStateCache())
        from mmorpg.infrastructure.persistence.instances import MemoryItemInstances

        instances = MemoryItemInstances(registry.current)
        dependencies = Dependencies(
            settings=settings,
            registry=registry,
            users=InMemoryUserRepository(),
            characters=InMemoryCharacterRepository(instances),
            inventory=InMemoryInventoryRepository(instances),
            trades=InMemoryTradeRepository(),
            privacy=InMemoryPrivacyRepository(),
            keeper_log=InMemoryKeeperLogRepository(),
            # В local денежный журнал в базу не пишется (ADR 0044): пустой срез
            # на карточке — не беда, а само хранилище живёт вместе с процессом.
            gold_flow=InMemoryGoldFlowRepository(),
            state_cache=memory_cache,
            locations=InMemoryLocationStateCache(),
            overlays=InMemoryContentOverlayRepository(),
            parties=PartyStore(InMemoryPartyRepository(), memory_cache),
            guilds=GuildStore(InMemoryGuildRepository(), memory_cache),
            # Приёмник подключается, как только появился Bot; до тех пор - и всегда,
            # когда CHANNEL_ID пуст, - объявление ничего не делает.
            broadcasts=ChannelBroadcaster(sink=None, chat_id=settings.channel_id),
            journey=MemoryJourneyRepository(),
        )
        return AtomicMemoryStorage(), dependencies, InMemoryIdempotencyStore()

    from mmorpg.infrastructure.persistence.pool import create_postgres_pool
    from mmorpg.infrastructure.persistence.postgres import (
        PostgresCharacterRepository,
        PostgresContentOverlayRepository,
        PostgresGoldFlowRepository,
        PostgresGuildRepository,
        PostgresInventoryRepository,
        PostgresKeeperLogRepository,
        PostgresPartyRepository,
        PostgresPrivacyRepository,
        PostgresTradeRepository,
        PostgresUserRepository,
    )

    # Ожидание терпеливое: стек, который поднимается разом, поднимается не по порядку, и
    # база, которой нужно ещё пять секунд, - не повод выходить.
    pool = await create_postgres_pool(settings)
    stack.push_async_callback(pool.close)
    storage, state_cache, locations, idempotency = await _build_session_state(settings, stack)
    pool.economic_cache = getattr(getattr(state_cache, "_client", None), "raw", None)
    from mmorpg.infrastructure.persistence.operations import OperationPool, PostgresOperations

    await PostgresOperations(OperationPool(pool)).recover()
    effect_state = PostgresEffectState(pool, state_cache)
    imported = await effect_state.import_legacy()
    state_cache = effect_state
    from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState, PostgresStorage
    from mmorpg.infrastructure.persistence.world import PostgresLocationState

    gameplay = PostgresGameplayState(pool, state_cache)
    await gameplay.import_legacy(pool.economic_cache)
    state_cache = gameplay
    storage = PostgresStorage(pool, storage)
    locations = PostgresLocationState(pool)
    if imported:
        logger.info("economic_marks_imported", records=imported)

    logger.info(
        "pools_ready",
        postgres_max=settings.postgres_pool_max,
        redis=settings.uses_redis,
        repeats=settings.reconnect_attempts,
        max_wait=settings.reconnect_max_delay_seconds,
    )

    gold_flow = PostgresGoldFlowRepository(pool)
    # Второй приёмник денежного журнала: та же строка, что в economy_log, но в
    # базе (ADR 0044). Мимо повторов и не дожидаясь записи — терять её можно.
    economy_log.use_sink(
        lambda flow, amount, character_id, detail: gold_flow.record(
            at=int(time.time()),
            flow=flow,
            amount=amount,
            character_id=character_id,
            detail=detail,
        )
    )

    dependencies = Dependencies(
        settings=settings,
        registry=registry,
        users=PostgresUserRepository(pool),
        characters=PostgresCharacterRepository(pool, registry.current),
        inventory=PostgresInventoryRepository(pool, registry.current),
        trades=PostgresTradeRepository(pool),
        privacy=PostgresPrivacyRepository(pool),
        keeper_log=PostgresKeeperLogRepository(pool),
        gold_flow=gold_flow,
        state_cache=state_cache,
        locations=locations,
        overlays=PostgresContentOverlayRepository(pool),
        parties=PartyStore(PostgresPartyRepository(pool), state_cache),
        guilds=GuildStore(PostgresGuildRepository(pool), state_cache),
        broadcasts=ChannelBroadcaster(sink=None, chat_id=settings.channel_id),
        journey=PostgresJourneyRepository(pool),
    )
    return storage, dependencies, idempotency


async def _build_session_state(
    settings: Settings, stack: AsyncExitStack
) -> tuple[BaseStorage, StateCache, LocationStateCache, IdempotencyStore]:
    """Временные адаптеры и перенос старых записей.

    Сборщик PostgreSQL заменяет экран, бой и карту постоянными адаптерами M03.
    Только APP_ENV=local оставляет игровые данные в памяти.
    """
    if not settings.uses_redis:
        logger.info(
            "temporary_cache_in_memory",
            detail="PostgreSQL gameplay and screens are installed by the outer builder",
        )
        return (
            AtomicMemoryStorage(),
            InMemoryStateCache(),
            InMemoryLocationStateCache(),
            InMemoryIdempotencyStore(),
        )

    from aiogram.fsm.storage.redis import RedisStorage

    from mmorpg.infrastructure.cache.operations import TransactionalRedis
    from mmorpg.infrastructure.cache.redis_cache import (
        RedisIdempotencyStore,
        RedisLocationStateCache,
        RedisStateCache,
    )
    from mmorpg.infrastructure.persistence.pool import create_redis_client, wait_for_redis

    redis: Any = TransactionalRedis(create_redis_client(settings))
    stack.push_async_callback(redis.aclose)
    await wait_for_redis(redis, settings)
    return (
        RedisStorage(redis),
        RedisStateCache(redis),
        RedisLocationStateCache(redis),
        RedisIdempotencyStore(redis),
    )


async def run_polling(app: Application) -> None:
    """Long polling: транспорт локальной игры и стека Docker.

    Только один процесс. Telegram отдаёт ``getUpdates`` одному потребителю, и
    вторая копия дралась бы с первой за каждое обновление.
    """
    settings = app.settings
    logger.info(
        "starting_polling",
        env=settings.app_env.value,
        concurrency_limit=settings.concurrency_limit,
    )
    await _wait_for_the_previous_copy(settings)
    async with (
        app.stack,
        heartbeat(settings, probe=app.health_probe),
        reporting(app.metrics, settings.metrics_seconds),
    ):
        try:
            await _greet_telegram(app)
            await app.bot.delete_webhook(drop_pending_updates=True)
            # Здесь aiogram ставит собственные обработчики SIGINT/SIGTERM, чтобы «docker
            # stop» дал доработать обновлениям в полёте, а не оборвал их.
            await app.dispatcher.start_polling(
                app.bot,
                tasks_concurrency_limit=settings.concurrency_limit,
            )
        finally:
            await app.bot.session.close()


async def _wait_for_the_previous_copy(settings: Settings) -> None:
    """Дать доиграть ещё живой копии, прежде чем просить у Telegram обновления.

    Telegram отдаёт ``getUpdates`` одному потребителю, поэтому второму процессу на
    каждую попытку отвечают ``TelegramConflictError``. Ответ знает сердцебиение:
    чистая остановка его удаляет, после жёсткой оно протухает через
    ``heartbeat_stale_after`` секунд, а свежий удар значит, что сейчас обслуживает
    кто-то другой.

    Не отказ, а ожидание: закрытое мышкой окно оставляет файл на месте.
    """
    if not is_alive(settings):
        return
    logger.warning(
        "another_copy_is_running",
        detail=(
            "Другая копия игры ещё отвечает на обновления. Эта подождёт, пока та "
            "закончит, иначе Telegram ответит обеим отказом."
        ),
        heartbeat=str(settings.heartbeat_path),
    )
    # Считанные проверки по одному удару сердца, а не ожидание события: то, чего
    # мы ждём, пишет другой процесс в файл, и узнать об этом можно только посмотрев.
    beats = math.ceil(settings.heartbeat_stale_after / settings.heartbeat_seconds) + 1
    for _ in range(beats):
        if not is_alive(settings):
            break
        await asyncio.sleep(settings.heartbeat_seconds)
    if is_alive(settings):
        logger.warning(
            "previous_copy_still_beating",
            detail=(
                "Прошлая копия так и не остановилась. Если это её остаток, "
                "закройте её окно; Telegram будет отказывать, пока их две."
            ),
            age_seconds=age_seconds(settings.heartbeat_path),
        )


async def _greet_telegram(app: Application) -> None:
    """Упасть сразу и по-человечески на неверном токене.

    Без этого первый же вызов API падает в глубине aiogram, и вместо «у вас
    неверный токен» запускавший получает трассировку. Недоступный Telegram бота
    не останавливает: старт его пережидает, как ждёт базу.
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
    """Событие, которое ставят SIGINT или SIGTERM.

    Опросу его выдаёт aiogram; вебхуку приходится просить самому, а без него
    ``docker stop`` рвал бы открытые соединения вместо того, чтобы дать стеку
    выхода закрыть пулы.
    """
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # На Windows обработчиков сигналов у цикла нет; Ctrl+C там всё равно бросает
        # исключение.
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    return stop


async def run_webhook(app: Application) -> None:
    """Боевой транспорт: aiohttp, обслуживающий вебхук."""
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

    async with (
        app.stack,
        heartbeat(settings, probe=app.health_probe),
        reporting(app.metrics, settings.metrics_seconds),
    ):
        stop = _stop_event()
        try:
            await site.start()
            logger.info("webhook_serving", host=settings.webhook_host, port=settings.webhook_port)
            await stop.wait()
            logger.info("shutdown_requested")
        finally:
            # aiohttp даёт доработать запросам в полёте до того, как это вернётся.
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
    """Точка входа. Стандартный runner, без uvloop (ADR 0004)."""
    try:
        with asyncio.Runner() as runner:
            runner.run(_amain())
    except (KeyboardInterrupt, SystemExit) as stop:
        logger.info("shutdown", reason=type(stop).__name__)


if __name__ == "__main__":
    main()
