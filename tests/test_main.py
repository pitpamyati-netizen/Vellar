"""The composition root assembles a working bot without touching the network."""

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
    """The whole point of APP_ENV=local: a bot token is the only requirement."""
    assert app.settings.uses_postgres is False
    assert app.settings.uses_redis is False
    assert app.content.races


async def test_solo_keeps_the_session_in_the_process() -> None:
    """APP_ENV=solo installs one service, not two: nothing reaches for Redis.

    The world still goes to PostgreSQL, which is why only the session half is
    built here - see ``docs/adr/0010-a-machine-without-containers.md``.
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
    """A private router firing in the group would answer a room with a keyboard.

    Character creation is the dangerous one: ``/start`` has no state filter, so
    without this the first ``/start`` in the group would drag someone into
    creation in public.
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
    """Whether the router's own filters let a message from this chat through."""
    from aiogram.types import Message

    probe = Message(message_id=1, date=datetime.now(UTC), chat=chat, text="/start")
    accepted, _ = await router.message.check_root_filters(probe)
    return bool(accepted)


async def test_middlewares_are_installed_in_order(app) -> None:
    """Duplicates are dropped before anything else touches the update."""
    outer = app.dispatcher.update.outer_middleware
    installed = [type(middleware) for middleware in outer]
    assert installed.index(IdempotencyMiddleware) < installed.index(DependencyMiddleware)


async def test_markdown_is_never_the_default_parse_mode(app) -> None:
    """Asterisks and underscores are spoken aloud by screen readers."""
    assert app.bot.default.parse_mode is None


async def test_broken_content_stops_startup(tmp_path) -> None:
    """A content typo must fail loudly at boot, not silently mid-game."""
    from mmorpg.infrastructure.content import ContentError

    empty = tmp_path / "content"
    empty.mkdir()
    with pytest.raises(ContentError):
        await build_application(local_settings(content_dir=empty))


def test_prod_requires_a_webhook_secret() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="webhook_secret"):
        Settings(_env_file=None, app_env=AppEnv.PROD, bot_token=FAKE_TOKEN)  # type: ignore[call-arg]
