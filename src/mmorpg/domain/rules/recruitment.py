"""Цель набора проверяется по действующему содержимому и уровню героя."""

from __future__ import annotations

from dataclasses import dataclass

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.rules.dungeon import dungeon_unlocked
from mmorpg.domain.rules.party import LEVEL_WINDOW

PACES = ("Спокойно, с перерывами", "Обычный темп")


@dataclass(frozen=True, slots=True)
class Goal:
    key: str
    city_id: str
    name: str
    level: int
    kind: str
    target: str


def goals(content: GameContent, character: Character) -> tuple[Goal, ...]:
    city = next((one for one in content.cities if one.id == character.city_id), None)
    if city is None:
        return ()
    if character.level < city.unlock_level:
        return ()
    result = [
        Goal(
            f"{city.id}:location:{one.slot}",
            city.id,
            one.name,
            one.level_min,
            "location",
            str(one.slot),
        )
        for one in city.locations
        if character.level >= one.level_min
    ]
    result.extend(
        Goal(f"{city.id}:dungeon:{one.id}", city.id, one.name, one.level, "dungeon", one.id)
        for one in city.dungeons
        if dungeon_unlocked(
            deep=one.deep,
            unlock_level=one.unlock_level,
            char_level=character.level,
            deep_threshold=city.locations[-1].level_min,
        )
    )
    return tuple(result)


def level_refusal(level: int, leader_level: int, low: int, high: int) -> str:
    if not 1 <= low <= high <= 150 or not low <= leader_level <= high:
        return "Укажите уровни от 1 до 150; ваш уровень должен входить в выбранный диапазон."
    if high - low > 2 * LEVEL_WINDOW:
        return f"Диапазон набора слишком широк: выберите не более {2 * LEVEL_WINDOW + 1} уровней."
    if not low <= level <= high or abs(level - leader_level) > LEVEL_WINDOW:
        return "Уровень героя не подходит этому набору. Выберите другой отряд."
    return ""
