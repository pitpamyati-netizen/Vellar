"""Сборка reply-клавиатур.

`ReplyKeyboardMarkup` - единственная разметка в проекте; inline-клавиатуры
запрещены прямо (``docs/adr/0002-reply-keyboards-only.md``). Объекты разметки
кэшируются по раскладке, поэтому неизменившийся экран не собирает клавиатуру
заново - одна из мер, стоящих за бюджетом в 100 мс.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from mmorpg.presentation.telegram.screens.base import Screen

KEYBOARD_CACHE_SIZE = 512


@lru_cache(maxsize=KEYBOARD_CACHE_SIZE)
def _build(layout: tuple[tuple[str, ...], ...]) -> ReplyKeyboardMarkup:
    """Кэшируется по точной раскладке: те же кнопки в том же порядке - тот же объект."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=text) for text in row] for row in layout],
        resize_keyboard=True,
        is_persistent=True,
        one_time_keyboard=False,
        input_field_placeholder=None,
    )


def keyboard_for(screen: Screen, *, emoji: bool = False) -> ReplyKeyboardMarkup:
    """Разметка экрана, с учётом того, что игрок выбрал про значки."""
    return _build(screen.button_texts(emoji=emoji))


@lru_cache(maxsize=KEYBOARD_CACHE_SIZE)
def _build_selective(buttons: tuple[str, ...]) -> ReplyKeyboardMarkup:
    """Один ряд, показанный одному человеку.

    ``selective`` значит, что Telegram покажет клавиатуру только отправителю того
    сообщения, которому это отвечает, - а это ровно тот, кому предложили. Это
    удобство, а не проверка права: чужое нажатие хендлер отвергнет независимо от
    того, кто мог видеть кнопку.

    ``one_time_keyboard`` - потому что ответ здесь одно решение, а оставленные в
    группе кнопки прицепились бы к разговору, к которому не относятся.
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
    """Две кнопки ответа у стоящего предложения."""
    return _build_selective(tuple(buttons))


def dismiss_keyboard() -> ReplyKeyboardRemove:
    """Убрать кнопки ответа, когда предложение закрылось."""
    return ReplyKeyboardRemove(selective=True)


def cache_info() -> str:
    """Открыто наружу ради тестов задержки и служебного журнала."""
    return str(_build.cache_info())


def clear_cache() -> None:
    _build.cache_clear()
