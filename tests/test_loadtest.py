"""Замер с потерянными действиями не должен считаться успешной проверкой."""

from argparse import Namespace
from contextlib import AsyncExitStack
from types import SimpleNamespace

import pytest
from scripts import loadtest

from mmorpg.config import Settings
from mmorpg.domain.entities.character import Character
from mmorpg.infrastructure.persistence import InMemoryCharacterRepository, InMemoryUserRepository


def settings(*, budget: float = 60.0) -> Settings:
    return Settings(  # type: ignore[call-arg]
        app_env="local", bot_token="0:test", slow_callback_seconds=budget, _env_file=None
    )


def options() -> Namespace:
    return Namespace(players=1, actions=2, pause=0.0, seed="m00-check", keep=False)


async def test_a_successful_load_check_returns_zero() -> None:
    assert await loadtest.run(options(), settings()) == 0


async def test_a_failed_action_fails_the_check_and_cleans_up(monkeypatch) -> None:
    class InterruptedReads(InMemoryCharacterRepository):
        interrupted = False

        async def get_active(self, user_id: int) -> Character | None:
            if not self.interrupted:
                self.interrupted = True
                raise RuntimeError("planned storage failure")
            return await super().get_active(user_id)

    characters = InterruptedReads()
    users = InMemoryUserRepository()

    async def repositories(settings: Settings, stack: AsyncExitStack):
        return characters, users

    monkeypatch.setattr(loadtest, "_repositories", repositories)
    assert await loadtest.run(options(), settings()) == 2
    assert await characters.list_for_user(loadtest.ACCOUNT_BASE) == ()


async def test_slow_successful_actions_have_a_separate_result(monkeypatch) -> None:
    monkeypatch.setattr(loadtest, "Stopwatch", lambda: SimpleNamespace(seconds=0.5))
    assert await loadtest.run(options(), settings(budget=0.1)) == 3


@pytest.mark.parametrize(
    "arguments",
    [
        ["--players", "0"],
        ["--actions", "-1"],
        ["--pause", "-0.5"],
        ["--pause", "nan"],
        ["--pause", "inf"],
    ],
)
def test_empty_or_invalid_load_is_rejected_before_storage(arguments, monkeypatch) -> None:
    def unexpected_settings():
        pytest.fail("Invalid load must be rejected before reading storage settings")

    monkeypatch.setattr(loadtest, "load_settings", unexpected_settings)
    with pytest.raises(SystemExit) as error:
        loadtest.main(arguments)
    assert error.value.code == 2
