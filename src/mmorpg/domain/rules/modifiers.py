"""Collecting modifiers from every source into one bundle.

Traits, equipped passives, equipment and active effects all speak the same
vocabulary (``traits.toml [meta].modifier_keys``). Percentages from different
sources add up and are applied **once** at the end, so ordering never changes the
result and applying the same source twice is impossible by construction.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent, SkillKind
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


def equipment_modifiers(content: GameContent, item_ids: Iterable[str]) -> dict[str, float]:
    """Что даёт надетое: своими прибавками и прибавками своего рода.

    Надетое переживает содержимое так же, как панель: вещь, которой больше нет,
    ничего не даёт и ничего не роняет.
    """
    worn = tuple(item_ids)
    return merge(
        *(content.item(item_id).modifiers for item_id in worn if content.has_item(item_id)),
        gear.type_modifiers(content, worn),
    )


def passive_modifiers(content: GameContent, character: Character) -> dict[str, float]:
    """Modifiers from the three equipped passive skills, scaled by their rank.

    Грань постоянного умения считается здесь и больше нигде: у постоянного умения
    нет ни хода, ни цели, поэтому всё, что грань может ему сделать, - поднять его
    собственную прибавку и добавить свою. Долго не делалось и этого: половина
    выбранных граней в игре была надписью без последствий.
    """
    bundles: list[Mapping[str, float]] = []
    for code in character.loadout.equipped_passives():
        # Панель переживает содержимое: умение, которого больше нет, ничего не
        # даёт и ничего не роняет (``Claude.md``, правило 8).
        if not content.has_skill(code):
            continue
        skill = content.skill(code)
        if skill.kind is not SkillKind.PASSIVE:
            continue
        rank = character.loadout.rank_of(code)
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
        equipment_modifiers(content, character.equipment.item_ids()),
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
