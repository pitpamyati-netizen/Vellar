"""Очередь перед отправкой: бот не выходит за счёт Telegram.

Часы и сон здесь поддельные, поэтому проверка секундного окна не стоит секунды.
"""

from __future__ import annotations

from typing import Any

from aiogram.methods import GetUpdates, SendMessage

from mmorpg.presentation.telegram.middlewares.sending import (
    SendRateMiddleware,
    SendWindow,
    is_send,
)


class FakeClock:
    """Время, которое идёт только тогда, когда его двигают."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


async def test_the_window_lets_the_first_thirty_through_without_waiting() -> None:
    clock = FakeClock()
    window = SendWindow(limit=30, window=1.0, clock=clock, sleep=clock.sleep)

    for _ in range(30):
        assert await window.take() == 0.0

    assert clock.slept == []


async def test_the_thirty_first_send_waits_for_the_window_to_move() -> None:
    clock = FakeClock()
    window = SendWindow(limit=3, window=1.0, clock=clock, sleep=clock.sleep)

    for _ in range(3):
        await window.take()
    clock.now = 0.4
    waited = await window.take()

    assert waited == 0.6
    assert clock.now == 1.0


async def test_a_send_costs_nothing_once_the_second_has_passed() -> None:
    clock = FakeClock()
    window = SendWindow(limit=2, window=1.0, clock=clock, sleep=clock.sleep)

    await window.take()
    await window.take()
    clock.now = 1.5

    assert await window.take() == 0.0
    assert window.waiting == 1


def test_only_sends_are_counted() -> None:
    """Опрос обновлений в счёт не входит и не должен придерживать ответы игрокам."""
    assert is_send(SendMessage(chat_id=1, text="хорошо"))
    assert not is_send(GetUpdates())


async def test_the_middleware_holds_a_send_and_lets_a_poll_straight_through() -> None:
    clock = FakeClock()
    window = SendWindow(limit=1, window=1.0, clock=clock, sleep=clock.sleep)
    middleware = SendRateMiddleware(window)
    made: list[str] = []

    async def make_request(bot: Any, method: Any) -> Any:
        made.append(type(method).__name__)
        return None

    await middleware(make_request, None, SendMessage(chat_id=1, text="раз"))  # type: ignore[arg-type]
    await middleware(make_request, None, GetUpdates())  # type: ignore[arg-type]
    assert clock.slept == []

    await middleware(make_request, None, SendMessage(chat_id=1, text="два"))  # type: ignore[arg-type]

    assert made == ["SendMessage", "GetUpdates", "SendMessage"]
    assert clock.slept == [1.0]
