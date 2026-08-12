"""Reply keyboard construction.

`ReplyKeyboardMarkup` is the only markup in the project; inline keyboards are
forbidden outright (``docs/adr/0002-reply-keyboards-only.md``). Markup objects are
cached by layout, so an unchanged screen never rebuilds its keyboard - one of the
measures behind the 100 ms budget.
"""

from __future__ import annotations

from functools import lru_cache

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

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


def cache_info() -> str:
    """Exposed for the latency tests and for operational logging."""
    return str(_build.cache_info())


def clear_cache() -> None:
    _build.cache_clear()
