"""Настройки игры.

Три переключателя, и каждый называет своё нынешнее положение целой фразой:
галочка сама по себе ничего не говорит тому, кто читает экран не глядя.

Эмодзи выключены по умолчанию (правило доступности 6).
"""

from __future__ import annotations

from mmorpg.domain.ports.repositories import AccessibilitySettings
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import head

TOGGLE_EMOJI = label("Переключить эмодзи")
TOGGLE_VERBOSE = label("Переключить подробные описания")
REPEAT_SCREEN = label("Повторить текущий экран")

ON = "включены"
OFF = "выключены"


def settings_screen(settings: AccessibilitySettings, notice: str = "") -> Screen:
    return Screen(
        id=ScreenId.SETTINGS,
        lines=(
            *head("Настройки.", notice),
            f"Эмодзи: {ON if settings.emoji else OFF}. "
            "Смысл кнопки всегда написан словами, эмодзи её только украшают.",
            f"Подробные описания: {ON if settings.verbose else OFF}. "
            "Когда включены, характеристики говорят, что даёт вложенное в них очко.",
            f"Позиций на странице списка: {settings.page_size}.",
            "Команда /осмотреться присылает текущий экран заново, если он потерялся в переписке.",
        ),
        rows=((TOGGLE_EMOJI,), (TOGGLE_VERBOSE,), (REPEAT_SCREEN,)),
    )


def toggled(settings: AccessibilitySettings, pressed: str) -> tuple[AccessibilitySettings, str]:
    """Apply a switch. Returns the new settings and the sentence to read back."""
    from dataclasses import replace

    if TOGGLE_EMOJI.matches(pressed):
        updated = replace(settings, emoji=not settings.emoji)
        return updated, f"Эмодзи теперь {ON if updated.emoji else OFF}."
    if TOGGLE_VERBOSE.matches(pressed):
        updated = replace(settings, verbose=not settings.verbose)
        return updated, f"Подробные описания теперь {ON if updated.verbose else OFF}."
    return settings, ""


def switches() -> tuple[Label, ...]:
    return (TOGGLE_EMOJI, TOGGLE_VERBOSE, REPEAT_SCREEN)
