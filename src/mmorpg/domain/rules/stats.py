"""Total character statistics.

Nothing here is stored. Given the raw character record and the content registry,
these functions rebuild every number the game shows:

    total = base + race + class + allocated + traits + equipment + active effects

The functions are pure, so the same inputs always produce the same output, and
applying the same modifier source twice is impossible - sources are collected, not
accumulated over time.
"""

from __future__ import annotations

from dataclasses import dataclass

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.effects import EffectStack
from mmorpg.domain.entities.stats import StatBlock, StatCode
from mmorpg.domain.rules import modifiers as mods

# Derived value coefficients. Kept here rather than in content: they are formula
# constants, not balance knobs a content author edits per race.
ARMOR_PER_ENDURANCE = 1.6
ACCURACY_BASE = 75.0
ACCURACY_PER_AGILITY = 1.2
DODGE_PER_AGILITY = 0.55
CRIT_CHANCE_BASE = 3.0
CRIT_CHANCE_PER_LUCK = 0.45
CRIT_DAMAGE_BASE = 150.0
CRIT_DAMAGE_PER_LUCK = 0.45
# Both halves of a critical hit are capped. Uncapped, luck multiplied chance by
# damage and a luck build ended up hitting three times as hard as anyone else -
# not a build, an exploit. Capped, it is worth about half again as much.
MAX_CRIT_CHANCE = 50.0
MAX_CRIT_DAMAGE = 250.0
MAX_DODGE = 75.0
INITIATIVE_PER_AGILITY = 1.5
RESOURCE_REGEN_PER_WISDOM = 0.4


@dataclass(frozen=True, slots=True)
class DerivedStats:
    """Everything the combat engine and the screens need, already computed."""

    max_health: int
    max_resource: int
    resource_name: str
    armor: int
    accuracy: float
    dodge: float
    crit_chance: float
    crit_damage: float
    initiative: float
    resource_regen: float
    health_regen_percent: float


def primary_stats(
    content: GameContent,
    character: Character,
    effects: EffectStack | None = None,
) -> StatBlock:
    """Base + race + class + allocated + flat stat modifiers."""
    rules = content.rules
    race = content.race(character.race_id)
    klass = content.character_class(character.class_id)
    modifiers = mods.collect_modifiers(content, character, effects)

    return (
        StatBlock.uniform(rules.base_stat_value)
        + race.bonuses
        + klass.bonuses
        + character.allocated
        + mods.stat_bonuses(modifiers)
    )


def derived_stats(
    content: GameContent,
    character: Character,
    effects: EffectStack | None = None,
) -> DerivedStats:
    """Compute every derived value from the raw character record."""
    klass = content.character_class(character.class_id)
    modifiers = mods.collect_modifiers(content, character, effects)
    stats = primary_stats(content, character, effects)
    level = character.level

    raw_health = (
        klass.health.base
        + klass.health.per_level * (level - 1)
        + klass.health.per_endurance * stats[StatCode.END]
    )
    max_health = raw_health * mods.percent(modifiers, "health_percent")

    resource = klass.resource
    raw_resource = (
        resource.base + resource.per_level * (level - 1) + resource.per_stat * stats[resource.stat]
    )
    max_resource = raw_resource * mods.percent(modifiers, "resource_percent")

    armor = stats[StatCode.END] * ARMOR_PER_ENDURANCE * mods.percent(modifiers, "armor_percent")

    accuracy = (ACCURACY_BASE + stats[StatCode.AGI] * ACCURACY_PER_AGILITY) * mods.percent(
        modifiers, "accuracy_percent"
    )
    dodge = stats[StatCode.AGI] * DODGE_PER_AGILITY + mods.flat(modifiers, "dodge_percent")
    crit_chance = (
        CRIT_CHANCE_BASE
        + stats[StatCode.LCK] * CRIT_CHANCE_PER_LUCK
        + mods.flat(modifiers, "crit_chance_percent")
    )
    crit_damage = (
        CRIT_DAMAGE_BASE
        + stats[StatCode.LCK] * CRIT_DAMAGE_PER_LUCK
        + mods.flat(modifiers, "crit_damage_percent")
    )
    initiative = (
        stats[StatCode.AGI] * INITIATIVE_PER_AGILITY * mods.percent(modifiers, "initiative_percent")
    )
    resource_regen = (
        resource.regen_per_turn + stats[StatCode.WIS] * RESOURCE_REGEN_PER_WISDOM
    ) * mods.percent(modifiers, "resource_regen_percent")

    return DerivedStats(
        max_health=max(1, round(max_health)),
        max_resource=max(0, round(max_resource)),
        resource_name=resource.name,
        armor=max(0, round(armor)),
        accuracy=round(accuracy, 2),
        dodge=round(min(dodge, MAX_DODGE), 2),
        crit_chance=round(min(crit_chance, MAX_CRIT_CHANCE), 2),
        crit_damage=round(min(crit_damage, MAX_CRIT_DAMAGE), 2),
        initiative=round(initiative, 2),
        resource_regen=round(resource_regen, 2),
        health_regen_percent=round(mods.flat(modifiers, "regen_per_turn_percent"), 2),
    )


def stat_allowance(content: GameContent, level: int) -> int:
    """Total stat points a character of this level has been given."""
    rules = content.rules
    return rules.free_points_at_creation + rules.stat_points_per_level * (level - 1)


def skill_point_allowance(content: GameContent, level: int) -> int:
    """Total skill points a character of this level has been given."""
    return content.rules.skill_point_per_level * (level - 1)
