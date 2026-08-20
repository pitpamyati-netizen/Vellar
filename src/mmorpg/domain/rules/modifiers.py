"""Collecting modifiers from every source into one bundle.

Traits, known passives, equipment and active effects all speak the same
vocabulary (``traits.toml [meta].modifier_keys``). Percentages from different
sources add up and are applied **once** at the end, so ordering never changes the
result and applying the same source twice is impossible by construction.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.effects import EffectStack
from mmorpg.domain.entities.stats import StatBlock, StatCode
from mmorpg.domain.rules import edges as edge_rules
from mmorpg.domain.rules import equipment as gear
from mmorpg.domain.rules import skills as skill_rules

STAT_MODIFIER_PREFIX = "stat_"


def merge(*bundles: Mapping[str, float]) -> dict[str, float]:
    """Sum modifier bundles key by key."""
    total: dict[str, float] = {}
    for bundle in bundles:
        for key, value in bundle.items():
            total[key] = total.get(key, 0.0) + value
    return total


def trait_modifiers(content: GameContent, trait_ids: Iterable[str]) -> dict[str, float]:
    return merge(*(content.trait(trait_id).modifiers for trait_id in trait_ids))


def equipment_modifiers(
    content: GameContent, item_ids: Iterable[str], hero_level: int = 0
) -> dict[str, float]:
    """Что даёт надетое: своими числами, своим родом и тем, чего стоит чужое.

    Прибавки к характеристикам лежат на вещи числом (``Item.stat_bonuses``) — их
    даёт редкость, — и попадают сюда теми же ключами ``stat_STR``, которыми
    говорят особенности: словарь один на всех, и складывать его умеет один и тот
    же ``merge``.

    Надетое переживает содержимое так же, как панель: вещь, которой больше нет,
    ничего не даёт и ничего не роняет.
    """
    worn = tuple(item_ids)
    bundles: list[Mapping[str, float]] = []
    for item_id in worn:
        item = gear.worn_item(content, item_id, hero_level)
        if item is None:
            continue
        bundles.append(item.modifiers)
        bundles.append(
            {
                f"{STAT_MODIFIER_PREFIX}{code}": float(value)
                for code, value in item.stat_bonuses.items()
            }
        )
    bundles.append(gear.type_modifiers(content, worn))
    return merge(*bundles)


def passive_modifiers(content: GameContent, character: Character) -> dict[str, float]:
    """Modifiers from every passive skill the character has learned.

    Изучено - значит работает. Раньше их укладывали в три слота из шести, и
    очко, вложенное в седьмое постоянное умение, не делало ровно ничего: игрок
    платил за прибавку, которую игра не считала.

    Грань постоянного умения считается здесь и больше нигде: у постоянного умения
    нет ни хода, ни цели, поэтому всё, что грань может ему сделать, - поднять его
    собственную прибавку и добавить свою. Долго не делалось и этого: половина
    выбранных граней в игре была надписью без последствий.
    """
    bundles: list[Mapping[str, float]] = []
    # Изученное переживает содержимое: умения, которого больше нет, здесь просто
    # нет (``Claude.md``, правило 8) - ``known_passives`` отбирает по реестру.
    for skill in skill_rules.known_passives(content, character):
        rank = character.loadout.rank_of(skill.code)
        edge = skill_rules.chosen_edge(character, skill)
        bundles.append({skill.effect: skill.power_at_rank(rank) * edge_rules.power_factor(edge)})
        if edge is not None and edge.self_modifiers:
            bundles.append(dict(edge.self_modifiers))
    return merge(*bundles)


def collect_modifiers(
    content: GameContent,
    character: Character,
    effects: EffectStack | None = None,
) -> dict[str, float]:
    """Every modifier acting on a character right now."""
    return merge(
        trait_modifiers(content, character.trait_ids),
        passive_modifiers(content, character),
        equipment_modifiers(content, character.equipment.item_ids(), character.level),
        # Чужая вещь не запрещена — она дорога, и цена берётся здесь же, вместе
        # со всем остальным, что на персонаже сейчас висит.
        gear.proficiency_penalty(content, character),
        effects.modifiers() if effects is not None else {},
    )


def stat_bonuses(modifiers: Mapping[str, float]) -> StatBlock:
    """Extract flat stat bonuses (``stat_STR`` and friends) from a bundle."""
    values: dict[str, int] = {}
    for key, value in modifiers.items():
        if not key.startswith(STAT_MODIFIER_PREFIX):
            continue
        code = key.removeprefix(STAT_MODIFIER_PREFIX)
        if code in {stat.value for stat in StatCode}:
            values[code] = values.get(code, 0) + int(value)
    return StatBlock.from_mapping(values)


def percent(modifiers: Mapping[str, float], key: str) -> float:
    """A percentage modifier as a multiplier: 12 -> 1.12, -8 -> 0.92."""
    return 1.0 + modifiers.get(key, 0.0) / 100.0


def flat(modifiers: Mapping[str, float], key: str) -> float:
    return modifiers.get(key, 0.0)
