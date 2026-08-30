"""Что читает заблокированный.

Экрана здесь нет нарочно: у заблокированного нет ни одной кнопки, которая
что-нибудь сделала бы, а клавиатура из кнопок, ведущих в отказ, — худшее, что
можно предложить тому, кто слушает экран. Поэтому — одно предложение, и в нём
сказано главное: почему и до каких пор.
"""

from __future__ import annotations

from mmorpg.domain.entities.moderation import Ban
from mmorpg.domain.rules import moderation as rules
from mmorpg.presentation.telegram.screens.format import duration


def banned_text(ban: Ban, *, now: int) -> str:
    """Одно предложение о блокировке, обращённое к тому, кого заблокировали."""
    left = (
        "Она бессрочная."
        if ban.forever
        else f"Осталось: {duration(rules.remaining(ban, now=now))}."
    )
    because = f" Причина: {ban.reason}." if ban.reason else " Причина не названа."
    return f"Вы заблокированы смотрителем. {left}{because} Игра продолжится, когда срок выйдет."


def maintenance_text(reason: str = "") -> str:
    """Одна строка на время обслуживания. Её слышат все, кроме смотрителей."""
    tail = f" {reason.strip()}" if reason.strip() else ""
    return f"Игра на обслуживании, зайдите чуть позже.{tail}"
