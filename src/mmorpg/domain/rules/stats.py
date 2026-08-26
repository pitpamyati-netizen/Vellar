"""Итоговые характеристики персонажа.

Ничто здесь не хранится. По сырой записи персонажа и реестру содержимого эти
функции собирают заново каждое число, которое игра показывает:

    итог = основа + раса + класс + розданное + черты + снаряжение + эффекты

Функции чистые, поэтому одни и те же входные данные всегда дают один и тот же
ответ, а применить один источник прибавок дважды нельзя: источники собираются, а
не накапливаются со временем.
"""

from __future__ import annotations

from dataclasses import dataclass

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.effects import EffectStack
from mmorpg.domain.entities.stats import StatBlock, StatCode
from mmorpg.domain.rules import equipment as gear
from mmorpg.domain.rules import modifiers as mods

# Коэффициенты производных значений. Держатся здесь, а не в содержимом: это постоянные
# формул, а не ручки баланса, которые автор содержимого правит на каждую расу.
# Выносливость держит броню, которая есть у всякого, - но только её. Всё остальное
# приносит доспех, и приносит числом (``domain/rules/equipment.py``): до этого надетое
# умело менять броню лишь процентами от полутора десятков, и латы ощущались как
# стёганка.
ARMOR_PER_ENDURANCE = 1.6
ACCURACY_BASE = 75.0
ACCURACY_PER_AGILITY = 1.2
DODGE_PER_AGILITY = 0.55
CRIT_CHANCE_BASE = 3.0
CRIT_CHANCE_PER_LUCK = 0.45
CRIT_DAMAGE_BASE = 150.0
CRIT_DAMAGE_PER_LUCK = 0.45
# Обе половины крита ограничены. Без потолка удача умножала шанс на урон, и сборка на
# удачу била втрое сильнее всех прочих - это не сборка, а дыра. С потолком она стоит
# примерно в полтора раза больше.
MAX_CRIT_CHANCE = 50.0
MAX_CRIT_DAMAGE = 250.0
MAX_DODGE = 75.0
# Инициатива - это очередь удара, и мерить её приходится в той же шкале, в
# какой она есть у противника (``procgen/enemies.INITIATIVE_BASE``): у породы
# она растёт с уровнем, а у героя росла только с ловкостью, и маг трёхсотого
# уровня оказывался вчетверо медленнее камня. База и уровень выравнивают шкалы,
# ловкость по-прежнему решает, кто быстрее среди равных (ADR 0021).
INITIATIVE_BASE = 20.0
INITIATIVE_PER_LEVEL = 0.35
INITIATIVE_PER_AGILITY = 0.8
RESOURCE_REGEN_PER_WISDOM = 0.4


@dataclass(frozen=True, slots=True)
class DerivedStats:
    """Всё, что нужно боевому движку и экранам, уже посчитанное."""

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
    """Основа плюс раса, класс, розданное и плоские прибавки к характеристикам."""
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
    """Посчитать все производные числа по сырой записи персонажа."""
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

    worn = gear.worn_armor(content, character.equipment.item_ids(), character.level)
    armor = (stats[StatCode.END] * ARMOR_PER_ENDURANCE + worn) * mods.percent(
        modifiers, "armor_percent"
    )

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
        INITIATIVE_BASE
        + INITIATIVE_PER_LEVEL * level
        + stats[StatCode.AGI] * INITIATIVE_PER_AGILITY
    ) * mods.percent(modifiers, "initiative_percent")
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
    """Сколько всего очков характеристик выдано персонажу этого уровня."""
    rules = content.rules
    return rules.free_points_at_creation + rules.stat_points_per_level * (level - 1)


def skill_point_allowance(content: GameContent, level: int) -> int:
    """Сколько всего очков умений выдано персонажу этого уровня."""
    return content.rules.skill_point_per_level * (level - 1)
