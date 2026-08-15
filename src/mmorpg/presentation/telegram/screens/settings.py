"""Accessibility settings.

Three switches, each stated as a full sentence with its current value, because a
toggle whose state is only visible as a checkmark is invisible to a screen reader.

Emoji are **off by default** (accessibility rule 6).
"""

from __future__ import annotations

from mmorpg.domain.ports.repositories import AccessibilitySettings
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId

TOGGLE_EMOJI = label("Переключить эмодзи")
TOGGLE_VERBOSE = label("Переключить подробные описания")
REPEAT_SCREEN = label("Повторить текущий экран")

ON = "включены"
OFF = "выключены"


def settings_screen(settings: AccessibilitySettings, notice: str = "") -> Screen:
    return Screen(
        id=ScreenId.SETTINGS,
        lines=(
            notice or "Настройки доступности.",
            f"Эмодзи: {ON if settings.emoji else OFF}. "
            "Смысл кнопки всегда есть в тексте, эмодзи только украшают.",
            f"Подробные описания: {ON if settings.verbose else OFF}. "
            "Подробные описания добавляют пояснения к эффектам и характеристикам.",
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
