"""The composition root assembles a working bot without touching the network."""

from __future__ import annotations

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
    assert app.settings.uses_external_storage is False
    assert app.content.races


async def test_content_is_validated_before_the_bot_starts(app) -> None:
    assert len(app.content.races) == 16
    assert len(app.content.classes) == 8
    assert len(app.content.cities) == 15


async def test_routers_are_registered(app) -> None:
    names = {router.name for router in app.dispatcher.sub_routers}
    assert {"creation", "play"} <= names


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
