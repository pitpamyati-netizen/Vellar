"""An answer that died on the way to Telegram is sent again, not swallowed.

Silence is the one thing a screen reader user cannot read, so the rule here is
the opposite of the database one: a repeated screen is noise, a missing screen is
a dead end.
"""

from __future__ import annotations

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods import GetUpdates, SendMessage

from mmorpg.presentation.telegram.middlewares.retry import RetryRequestMiddleware
from mmorpg.retry import RetryPolicy

QUICK = RetryPolicy(attempts=3, delay=0.0, max_delay=0.0)
SCREEN = SendMessage(chat_id=1, text="Вы в городе Дубно.")


def sending(*outcomes: object):
    """A request that answers with each outcome in turn; exceptions are raised."""
    made: list[object] = []

    async def make_request(bot: object, method: object) -> object:
        made.append(method)
        outcome = outcomes[min(len(made), len(outcomes)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return make_request, made


async def test_a_screen_lost_on_a_broken_socket_is_sent_again() -> None:
    make_request, made = sending(
        TelegramNetworkError(method=SCREEN, message="connection reset"), "sent"
    )

    answer = await RetryRequestMiddleware(QUICK)(make_request, None, SCREEN)

    assert answer == "sent"
    assert len(made) == 2


async def test_telegram_falling_over_is_waited_out() -> None:
    make_request, made = sending(TelegramServerError(method=SCREEN, message="bad gateway"), "sent")

    assert await RetryRequestMiddleware(QUICK)(make_request, None, SCREEN) == "sent"
    assert len(made) == 2


async def test_repeats_are_bounded() -> None:
    make_request, made = sending(TelegramNetworkError(method=SCREEN, message="still down"))

    with pytest.raises(TelegramNetworkError):
        await RetryRequestMiddleware(QUICK)(make_request, None, SCREEN)

    assert len(made) == 4  # the first send and three repeats


@pytest.mark.parametrize(
    "refusal",
    [
        TelegramBadRequest(method=SCREEN, message="message text is empty"),
        TelegramForbiddenError(method=SCREEN, message="bot was blocked by the user"),
    ],
)
async def test_what_telegram_refused_is_not_sent_again(refusal: Exception) -> None:
    """A refusal is an answer, and it will be the same answer next time."""
    make_request, made = sending(refusal)

    with pytest.raises(type(refusal)):
        await RetryRequestMiddleware(QUICK)(make_request, None, SCREEN)

    assert len(made) == 1


async def test_a_short_flood_wait_is_absorbed() -> None:
    policy = RetryPolicy(attempts=3, delay=0.0, max_delay=1.0)
    make_request, made = sending(
        TelegramRetryAfter(method=SCREEN, message="flood", retry_after=0), "sent"
    )

    assert await RetryRequestMiddleware(policy)(make_request, None, SCREEN) == "sent"
    assert len(made) == 2


async def test_a_long_flood_wait_is_reported_instead_of_slept_through() -> None:
    """A player is waiting on the other end; a minute of silence is not an answer."""
    make_request, made = sending(TelegramRetryAfter(method=SCREEN, message="flood", retry_after=60))

    with pytest.raises(TelegramRetryAfter):
        await RetryRequestMiddleware(QUICK)(make_request, None, SCREEN)

    assert len(made) == 1


async def test_fetching_updates_is_left_to_aiogram() -> None:
    """The polling loop has its own endless backoff; two of them only add delay."""
    updates = GetUpdates()
    make_request, made = sending(TelegramNetworkError(method=updates, message="down"))

    with pytest.raises(TelegramNetworkError):
        await RetryRequestMiddleware(QUICK)(make_request, None, updates)

    assert len(made) == 1
