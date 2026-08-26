"""Корень композиции собирает работающего бота, не трогая сети."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mmorpg.config import AppEnv, Settings
from mmorpg.main import build_application
from mmorpg.presentation.telegram.middlewares.dependencies import DependencyMiddleware
from mmorpg.presentation.telegram.middlewares.idempotency import IdempotencyMiddleware

FAKE_TOKEN = "123456:AAHfake-token-for-tests-only-not-real"


def local_settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env=AppEnv.LOCAL,
        bot_token=FAKE_TOKEN,
        **overrides,  # type: ignore[arg-type]
    )


@pytest.fixture
async def app():
    application = await build_application(local_settings())
    yield application
    await application.bot.session.close()
    await application.stack.aclose()


async def test_local_mode_needs_no_database_or_redis(app) -> None:
    """Весь смысл APP_ENV=local: нужен один лишь токен бота."""
    assert app.settings.uses_postgres is False
    assert app.settings.uses_redis is False
    assert app.content.races


async def test_solo_keeps_the_session_in_the_process() -> None:
    """APP_ENV=solo ставит одну службу, а не две: до Redis не тянется ничто.

    Мир по-прежнему уходит в PostgreSQL, поэтому здесь собирается только сессионная
    половина - см. ``docs/adr/0010-a-machine-without-containers.md``.
    """
    from contextlib import AsyncExitStack

    from aiogram.fsm.storage.memory import MemoryStorage

    from mmorpg.infrastructure.cache import (
        InMemoryIdempotencyStore,
        InMemoryLocationStateCache,
        InMemoryStateCache,
    )
    from mmorpg.main import _build_session_state

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env=AppEnv.SOLO,
        bot_token=FAKE_TOKEN,
    )
    assert settings.uses_postgres is True

    async with AsyncExitStack() as stack:
        storage, state_cache, locations, idempotency = await _build_session_state(settings, stack)

    assert isinstance(storage, MemoryStorage)
    assert isinstance(state_cache, InMemoryStateCache)
    assert isinstance(locations, InMemoryLocationStateCache)
    assert isinstance(idempotency, InMemoryIdempotencyStore)


async def test_content_is_validated_before_the_bot_starts(app) -> None:
    assert len(app.content.races) == 16
    assert len(app.content.classes) == 8
    assert len(app.content.cities) == 15


async def test_routers_are_registered(app) -> None:
    names = {router.name for router in app.dispatcher.sub_routers}
    assert {"creation", "play", "group"} <= names


async def test_the_screens_and_the_group_never_share_a_chat(app) -> None:
    """Личный роутер, сработавший в группе, ответил бы комнате клавиатурой.

    Опаснее всего создание персонажа: у ``/start`` нет фильтра по состоянию, поэтому
    без этого первый же ``/start`` в группе втянул бы кого-нибудь в создание
    персонажа у всех на глазах.
    """
    from aiogram.enums import ChatType
    from aiogram.types import Chat

    routers = {router.name: router for router in app.dispatcher.sub_routers}
    group = Chat(id=-1001234567890, type=ChatType.SUPERGROUP)
    private = Chat(id=42, type=ChatType.PRIVATE)

    for name, allowed in (("creation", private), ("play", private), ("group", group)):
        refused = group if allowed is private else private
        assert await _accepts(routers[name], allowed), f"{name} refuses its own chat"
        assert not await _accepts(routers[name], refused), f"{name} would fire in the wrong chat"


async def _accepts(router, chat) -> bool:
    """Пропускают ли собственные фильтры роутера сообщение из этого чата."""
    from aiogram.types import Message

    probe = Message(message_id=1, date=datetime.now(UTC), chat=chat, text="/start")
    accepted, _ = await router.message.check_root_filters(probe)
    return bool(accepted)


async def test_middlewares_are_installed_in_order(app) -> None:
    """Повторы отбрасываются раньше, чем обновления коснётся что-либо ещё."""
    outer = app.dispatcher.update.outer_middleware
    installed = [type(middleware) for middleware in outer]
    assert installed.index(IdempotencyMiddleware) < installed.index(DependencyMiddleware)


async def test_markdown_is_never_the_default_parse_mode(app) -> None:
    """Звёздочки и подчёркивания экранный диктор произносит вслух."""
    assert app.bot.default.parse_mode is None


async def test_broken_content_stops_startup(tmp_path) -> None:
    """Опечатка в содержимом обязана громко падать на старте, а не тихо посреди игры."""
    from mmorpg.infrastructure.content import ContentError

    empty = tmp_path / "content"
    empty.mkdir()
    with pytest.raises(ContentError):
        await build_application(local_settings(content_dir=empty))


def test_prod_requires_a_webhook_secret() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="webhook_secret"):
        Settings(_env_file=None, app_env=AppEnv.PROD, bot_token=FAKE_TOKEN)  # type: ignore[call-arg]


# --- ни одно нажатие не остаётся без ответа ----------------------------


async def _feed(app, text: str, monkeypatch, *, account: int, update_id: int) -> list:
    """Прогнать сообщение через настоящий диспетчер и вернуть отправленные экраны."""
    from aiogram.types import Chat, Message, Update, User

    from mmorpg.presentation.telegram.handlers import creation as creation_handler
    from mmorpg.presentation.telegram.handlers import play as play_handler

    sent: list = []

    async def record(message, screen, *, emoji: bool = False) -> None:
        sent.append(screen)

    monkeypatch.setattr(creation_handler, "send_screen", record)
    monkeypatch.setattr(play_handler, "send_screen", record)

    update = Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime.now(UTC),
            chat=Chat(id=account, type="private"),
            from_user=User(id=account, is_bot=False, first_name="Игрок"),
            text=text,
        ),
    )
    await app.dispatcher.feed_update(app.bot, update)
    return sent


def _dependencies(app):
    """Хранилища, которыми собран этот бот."""
    for middleware in app.dispatcher.update.outer_middleware:
        if isinstance(middleware, DependencyMiddleware):
            return middleware._dependencies
    raise AssertionError("dependency middleware is not installed")


async def test_the_first_word_of_a_new_player_starts_creation(app, monkeypatch) -> None:
    from mmorpg.presentation.telegram.screens.base import ScreenId

    sent = await _feed(app, "/start", monkeypatch, account=77, update_id=1)

    assert [screen.id for screen in sent] == [ScreenId.START, ScreenId.CREATE_NAME]


async def test_a_button_pressed_with_no_screen_behind_it_is_still_answered(
    app, monkeypatch
) -> None:
    """Экран живёт в процессе и кончается вместе с ним (ADR 0010).

    Клавиатура у игрока переживает перезапуск, поэтому первое нажатие после него
    приходит без всякого состояния. Отвечать на такое было нечем: ни один роутер
    сообщение не брал, и игрок получал молчание - тот единственный ответ,
    которого игра дать не может (``docs/accessibility.md``, правило 12).
    """
    from mmorpg.domain.entities.character import Character
    from mmorpg.presentation.telegram.screens.base import ScreenId

    await _dependencies(app).characters.create(
        Character(id=0, user_id=88, name="Аргус", race_id="human", class_id="warrior")
    )

    sent = await _feed(app, "Главное меню", monkeypatch, account=88, update_id=2)

    assert sent, "нажатие без состояния осталось без ответа"
    assert sent[-1].id is ScreenId.MAIN_MENU
    assert "Прежний экран не сохранился" in sent[-1].text()


async def test_a_stranger_without_a_character_is_led_into_creation(app, monkeypatch) -> None:
    """Первое слово вместо ``/start`` - это тоже начало разговора, а не ошибка."""
    from mmorpg.presentation.telegram.screens.base import ScreenId

    sent = await _feed(app, "привет", monkeypatch, account=99, update_id=3)

    assert [screen.id for screen in sent] == [ScreenId.START, ScreenId.CREATE_NAME]
