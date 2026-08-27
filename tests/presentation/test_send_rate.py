"""Очередь перед отправкой: бот не выходит за счёт Telegram.

Часы и сон здесь поддельные, поэтому проверка секундного окна не стоит секунды.
"""

from __future__ import annotations

from typing import Any

from aiogram.methods import GetUpdates, SendMessage

from mmorpg.presentation.telegram.middlewares.sending import (
    PerChatWindow,
    SendRateMiddleware,
    SendWindow,
    chat_of,
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
    roomy = PerChatWindow(limit=99, window=1.0, clock=clock, sleep=clock.sleep)
    middleware = SendRateMiddleware(window, roomy)
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


async def test_a_chat_gets_paced_after_its_burst() -> None:
    """Барабанная дробь в один чат растягивается до одного ответа в секунду."""
    clock = FakeClock()
    window = PerChatWindow(limit=3, window=3.0, clock=clock, sleep=clock.sleep)

    for _ in range(3):
        assert await window.take(555) == 0.0
    assert clock.slept == []

    waited = await window.take(555)

    assert waited == 3.0
    assert window.waiting(555) == 1


async def test_one_loud_chat_does_not_hold_up_another() -> None:
    clock = FakeClock()
    window = PerChatWindow(limit=1, window=3.0, clock=clock, sleep=clock.sleep)

    await window.take(111)
    assert await window.take(222) == 0.0


async def test_a_silent_chat_is_forgotten() -> None:
    clock = FakeClock()
    window = PerChatWindow(limit=1, window=2.0, clock=clock, sleep=clock.sleep)

    await window.take(111)
    clock.now = 5.0
    await window.take(222)

    assert 111 not in window._sent


async def test_the_middleware_paces_a_flooding_chat_but_not_a_poll() -> None:
    clock = FakeClock()
    roomy = SendWindow(limit=99, window=1.0, clock=clock, sleep=clock.sleep)
    per_chat = PerChatWindow(limit=1, window=2.0, clock=clock, sleep=clock.sleep)
    middleware = SendRateMiddleware(roomy, per_chat)
    made: list[str] = []

    async def make_request(bot: Any, method: Any) -> Any:
        made.append(type(method).__name__)
        return None

    await middleware(make_request, None, SendMessage(chat_id=7, text="раз"))  # type: ignore[arg-type]
    await middleware(make_request, None, GetUpdates())  # type: ignore[arg-type]
    assert clock.slept == []

    await middleware(make_request, None, SendMessage(chat_id=7, text="два"))  # type: ignore[arg-type]

    assert made == ["SendMessage", "GetUpdates", "SendMessage"]
    assert clock.slept == [2.0]


def test_only_private_chats_are_paced_per_chat() -> None:
    """У групп и каналов счёт свой; строковый ``@username`` — это канал."""
    assert chat_of(SendMessage(chat_id=42, text="да")) == 42
    assert chat_of(SendMessage(chat_id=-1001234, text="да")) is None
    assert chat_of(SendMessage(chat_id="@vellar_channel", text="да")) is None
    assert chat_of(GetUpdates()) is None
