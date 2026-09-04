"""Вступление в дом и его техника (ADR 0049).

Дом — это объединение надолго, как гильдия, но заводить его нельзя: их семь, и
они NPC. Игрок вступает в один из них в его городе, платит взнос и получает
доступ к технике дома — пассивному свёртку прибавок. Ушёл — техника закрылась.

Всё здесь чистое: проверка возвращает строку отказа или пусто, а свёрток —
словарь, который движок и так умеет складывать (``modifiers.collect_modifiers``).
"""

from __future__ import annotations

from collections.abc import Mapping

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent, House

#: С какого уровня вступают в дом: после обучения, но задолго до эндгейма.
JOIN_LEVEL = 10

#: Вступительный взнос. Уходит из игры (``economy_log.SERVICE``), как грамота
#: гильдии. Уйти из дома бесплатно, а вступить в другой — снова взнос.
JOIN_FEE = 300


def house_of_city(content: GameContent, city_id: str) -> House | None:
    """Дом, чей это город, или ``None`` (Гнездно — ничей)."""
    return content.house_of_city(city_id)


def current_house(content: GameContent, character: Character) -> House | None:
    """Дом игрока, если он в нём и дом ещё существует (``Claude.md``, правило 8)."""
    if character.house_id and content.has_house(character.house_id):
        return content.house(character.house_id)
    return None


def join_refusal(content: GameContent, character: Character, city_id: str) -> str:
    """Пусто, когда в этом городе можно вступить в дом, иначе — почему нельзя."""
    house = house_of_city(content, city_id)
    if house is None:
        return "Здесь нет двора великого дома."
    if character.house_id == house.id:
        return f"Вы уже в доме: {house.name}."
    if character.level < JOIN_LEVEL:
        return f"В дом берут с {JOIN_LEVEL} уровня. Ваш: {character.level}."
    if character.gold < JOIN_FEE:
        return f"Взнос — {JOIN_FEE} золота. У вас {character.gold}."
    return ""


def join(content: GameContent, character: Character, city_id: str) -> Character | None:
    """Вступить: взнос уходит, дом записан. ``None``, когда так нельзя."""
    if join_refusal(content, character, city_id):
        return None
    house = house_of_city(content, city_id)
    assert house is not None
    return character.with_gold(-JOIN_FEE).with_house(house.id)


def leave(character: Character) -> Character | None:
    """Уйти из дома. ``None``, когда игрок и так ни в каком доме."""
    if not character.house_id:
        return None
    return character.with_house("")


def technique_modifiers(content: GameContent, character: Character) -> Mapping[str, float]:
    """Что даёт техника дома игрока. Пусто — он ни в каком доме."""
    house = current_house(content, character)
    if house is None:
        return {}
    return house.technique.modifiers
