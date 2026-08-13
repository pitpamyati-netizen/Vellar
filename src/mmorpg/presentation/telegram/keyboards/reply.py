"""Reply keyboard construction.

`ReplyKeyboardMarkup` is the only markup in the project; inline keyboards are
forbidden outright (``docs/adr/0002-reply-keyboards-only.md``). Markup objects are
cached by layout, so an unchanged screen never rebuilds its keyboard - one of the
measures behind the 100 ms budget.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from mmorpg.presentation.telegram.screens.base import Screen

KEYBOARD_CACHE_SIZE = 512


@lru_cache(maxsize=KEYBOARD_CACHE_SIZE)
def _build(layout: tuple[tuple[str, ...], ...]) -> ReplyKeyboardMarkup:
    """Cached by exact layout: same buttons in the same order, same object."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=text) for text in row] for row in layout],
        resize_keyboard=True,
        is_persistent=True,
        one_time_keyboard=False,
        input_field_placeholder=None,
    )


def keyboard_for(screen: Screen, *, emoji: bool = False) -> ReplyKeyboardMarkup:
    """The markup for a screen, honouring the player's emoji setting."""
    return _build(screen.button_texts(emoji=emoji))


@lru_cache(maxsize=KEYBOARD_CACHE_SIZE)
def _build_selective(buttons: tuple[str, ...]) -> ReplyKeyboardMarkup:
    """One row, shown to one person.

    ``selective`` means Telegram displays the keyboard only to the sender of the
    message this one replies to - which is exactly the target of an offer. It is
    a convenience, not a permission check: the handler refuses a stranger's press
    regardless of who could see the button.

    ``one_time_keyboard`` because the answer is a single decision, and leaving the
    buttons in a group chat would attach them to unrelated conversation.
    """
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=text) for text in buttons]],
        resize_keyboard=True,
        is_persistent=False,
        one_time_keyboard=True,
        selective=True,
        input_field_placeholder=None,
    )


def selective_keyboard(buttons: Sequence[str]) -> ReplyKeyboardMarkup:
    """The two answer buttons of a pending offer."""
    return _build_selective(tuple(buttons))


def dismiss_keyboard() -> ReplyKeyboardRemove:
    """Take the answer buttons back once the offer is closed."""
    return ReplyKeyboardRemove(selective=True)


def cache_info() -> str:
    """Exposed for the latency tests and for operational logging."""
    return str(_build.cache_info())


def clear_cache() -> None:
    _build.cache_clear()
