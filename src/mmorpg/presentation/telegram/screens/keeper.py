"""The keeper screen: a service door, not a part of the game.

It is drawn only for the Telegram ids listed in ``ADMIN_IDS``, and it says so on
its first line, because a screen a player was never meant to hear must announce
itself before it offers anything. Every button here is a shortcut through work
the game would otherwise ask for, and each one reports the number it changed -
a keeper is checking the game, so they need the result, not a reassurance.
"""

from __future__ import annotations

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.rules.keeper import GOLD_STEP, POINTS_STEP
from mmorpg.domain.rules.stats import DerivedStats
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import amount, gold


def keeper_screen(
    content: GameContent, character: Character, stats: DerivedStats, notice: str = ""
) -> Screen:
    lines = (
        notice or "Смотритель. Служебный экран, игроки его не видят.",
        f"{character.name}, уровень {character.level}, "
        f"{content.race(character.race_id).name}, "
        f"{content.character_class(character.class_id).name}.",
        f"Здоровье: {amount(character.health_or(stats.max_health), stats.max_health)}.",
        f"Золото: {gold(character.gold)}.",
        f"Нераспределено: очков характеристик {character.unspent_stat_points}, "
        f"очков умений {character.unspent_skill_points}.",
        f"Кнопки дают: {GOLD_STEP} золота, один уровень, "
        f"полное здоровье, по {POINTS_STEP} очков каждого рода.",
        "Города открыты все, независимо от уровня.",
    )
    return Screen(
        id=ScreenId.KEEPER,
        lines=lines,
        rows=(
            (labels.KEEPER_GOLD, labels.KEEPER_LEVEL),
            (labels.KEEPER_HEAL, labels.KEEPER_POINTS),
        ),
    )
