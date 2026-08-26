"""Ожидание службы, которой пока нет."""

from __future__ import annotations

import pytest

from mmorpg.config import AppEnv, Settings
from mmorpg.retry import RetryPolicy, keep_trying

PATIENT = RetryPolicy(attempts=5, delay=0.0, max_delay=0.0)


def down(error: BaseException) -> bool:
    return isinstance(error, ConnectionError)


async def test_startup_waits_for_a_service_that_comes_up_late() -> None:
    """Контейнер вполне может опередить PostgreSQL, и это не отказ."""
    tries = 0

    async def connect() -> str:
        nonlocal tries
        tries += 1
        if tries < 3:
            raise ConnectionRefusedError("not listening yet")
        return "ready"

    assert await keep_trying(connect, policy=PATIENT, seconds=5, what="postgres", recoverable=down)
    assert tries == 3


async def test_waiting_stops_when_the_patience_runs_out() -> None:
    async def connect() -> str:
        raise ConnectionRefusedError("still nothing")

    with pytest.raises(ConnectionRefusedError):
        await keep_trying(connect, policy=PATIENT, seconds=0.0, what="postgres", recoverable=down)


async def test_what_cannot_be_waited_out_is_raised_at_once() -> None:
    """Неверный пароль не станет верным оттого, что спросили ещё раз."""
    tries = 0

    async def connect() -> str:
        nonlocal tries
        tries += 1
        raise PermissionError("password authentication failed")

    with pytest.raises(PermissionError):
        await keep_trying(connect, policy=PATIENT, seconds=5, what="postgres", recoverable=down)
    assert tries == 1


def test_the_policy_is_read_from_the_settings() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env=AppEnv.LOCAL,
        reconnect_attempts=7,
        reconnect_delay_seconds=0.5,
        reconnect_max_delay_seconds=9.0,
    )
    assert RetryPolicy.from_settings(settings) == RetryPolicy(attempts=7, delay=0.5, max_delay=9.0)


def test_a_ceiling_below_the_first_wait_is_refused() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="reconnect_max_delay_seconds"):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            reconnect_delay_seconds=5.0,
            reconnect_max_delay_seconds=1.0,
        )
