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
from mmorpg.domain.rules import repair

# Коэффициенты производных значений. Держатся здесь, а не в содержимом: это постоянные
# формул, а не ручки баланса, которые автор содержимого правит на каждую расу.
# Выносливость держит броню, которая есть у всякого, - но только её. Всё остальное
# приносит доспех, и приносит числом (``domain/rules/equipment.py``): до этого надетое
# умело менять броню лишь процентами от полутора десятков, и латы ощущались как
# стёганка.
ARMOR_PER_ENDURANCE = 1.6
ACCURACY_BASE = 80.0
ACCURACY_PER_AGILITY = 1.2
DODGE_PER_AGILITY = 0.55
CRIT_CHANCE_BASE = 3.0
CRIT_CHANCE_PER_LUCK = 0.45
CRIT_DAMAGE_BASE = 150.0
CRIT_DAMAGE_PER_LUCK = 0.45

# Убывающая отдача вместо стены. Раньше у крита, уклонения и точности стоял
# прямой потолок, и характеристика упиралась в него на середине пути: пятьдесят
# процентов крита набирались сотней удачи, а всё вложенное сверх - и весь второй
# конец полосы - не значило ничего (ADR 0058). Теперь характеристика идёт к
# своему пределу и не доходит: каждое очко прибавляет, но следующее прибавляет
# чуть меньше предыдущего - так это считают и в других играх, где вторичные
# числа не должны обгонять первичные.
#
# ``*_CEILING`` - предел, к которому идёт одна характеристика; ``*_SOFTENER`` -
# насколько быстро она к нему идёт (при raw, равном смягчителю, набрана ровно
# половина предела). Прибавки от вещей и умений складываются **после** и обещают
# ровно свои проценты: «плюс пять к криту» обязано давать пять (правило 7).
ACCURACY_SOFTENER = 120.0
ACCURACY_CEILING = 25.0
DODGE_SOFTENER = 110.0
DODGE_CEILING = 45.0
CRIT_CHANCE_SOFTENER = 90.0
CRIT_CHANCE_CEILING = 45.0
CRIT_DAMAGE_SOFTENER = 150.0
CRIT_DAMAGE_CEILING = 150.0

# Общие потолки. Они больше не про характеристику - они про то, чтобы ни одна
# сборка вместе с вещами и умениями не ушла в неуязвимость.
MAX_CRIT_CHANCE = 75.0
MAX_CRIT_DAMAGE = 300.0
MAX_DODGE = 75.0
# Инициатива - это очередь удара, и мерить её приходится в той же шкале, в
# какой она есть у противника (``procgen/enemies.INITIATIVE_BASE``): у породы
# она растёт с уровнем, а у героя росла только с ловкостью, и маг трёхсотого
# уровня оказывался вчетверо медленнее камня. База и уровень выравнивают шкалы,
# ловкость по-прежнему решает, кто быстрее среди равных (ADR 0021).
INITIATIVE_BASE = 19.65
INITIATIVE_PER_LEVEL = 0.7
INITIATIVE_PER_AGILITY = 0.8
#: Что мудрость даёт запасу. Проценты от максимума за ход, с убывающей отдачей и
#: своим пределом: запас растёт с уровнем, и прибавка числом сделала бы Чары
#: бездонными к середине пути (ADR 0058).
RESOURCE_REGEN_PER_WISDOM = 0.4
RESOURCE_REGEN_SOFTENER = 120.0
RESOURCE_REGEN_CEILING = 12.0


def softened(raw: float, softener: float, ceiling: float) -> float:
    """Убывающая отдача: ``ceiling * raw / (raw + softener)``.

    Гладкая кривая без ступеней и без стены: первые очки характеристики стоят
    дорого, поздние - дёшево, и предел не достигается никогда. Значение
    отрицательного ``raw`` - ноль: характеристика в минусе не даёт отрицательного
    крита, она просто не даёт ничего.
    """
    if raw <= 0.0 or softener <= 0.0:
        return 0.0
    return ceiling * raw / (raw + softener)


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
        StatBlock.uniform(rules.innate_stat_value(character.level))
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

    # Сломанное снаряжение брони не держит: сточенная до конца вещь не даёт
    # ничего, пока её не починят в кузнице (``domain/rules/repair.py``, ADR 0057).
    worn = gear.worn_armor(content, repair.working_ids(content, character), character.level)
    # Плоская броня прибавляется после процентов нарочно: закрывшемуся обещано
    # ровно «уровень, взятый трижды», и доспех этого числа не двигает.
    armor = (stats[StatCode.END] * ARMOR_PER_ENDURANCE + worn) * mods.percent(
        modifiers, "armor_percent"
    ) + mods.flat(modifiers, "armor_flat")

    accuracy = (
        ACCURACY_BASE
        + softened(stats[StatCode.AGI] * ACCURACY_PER_AGILITY, ACCURACY_SOFTENER, ACCURACY_CEILING)
    ) * mods.percent(modifiers, "accuracy_percent")
    dodge = softened(
        stats[StatCode.AGI] * DODGE_PER_AGILITY, DODGE_SOFTENER, DODGE_CEILING
    ) + mods.flat(modifiers, "dodge_percent")
    crit_chance = (
        CRIT_CHANCE_BASE
        + softened(
            stats[StatCode.LCK] * CRIT_CHANCE_PER_LUCK,
            CRIT_CHANCE_SOFTENER,
            CRIT_CHANCE_CEILING,
        )
        + mods.flat(modifiers, "crit_chance_percent")
    )
    crit_damage = (
        CRIT_DAMAGE_BASE
        + softened(
            stats[StatCode.LCK] * CRIT_DAMAGE_PER_LUCK,
            CRIT_DAMAGE_SOFTENER,
            CRIT_DAMAGE_CEILING,
        )
        + mods.flat(modifiers, "crit_damage_percent")
    )
    initiative = (
        INITIATIVE_BASE
        + INITIATIVE_PER_LEVEL * level
        + stats[StatCode.AGI] * INITIATIVE_PER_AGILITY
    ) * mods.percent(modifiers, "initiative_percent")
    # Восстановление запаса объявлено долей самого запаса - и у класса
    # (``regen_per_turn``), и у мудрости: числом оно значило что-то ровно на
    # первом уровне (ADR 0058). Игроку по-прежнему называется число: доля - это
    # правило, а на экране стоит то, что вернётся за ход.
    regen_share = resource.regen_per_turn + softened(
        stats[StatCode.WIS] * RESOURCE_REGEN_PER_WISDOM,
        RESOURCE_REGEN_SOFTENER,
        RESOURCE_REGEN_CEILING,
    )
    resource_regen = (
        max_resource * regen_share / 100.0 * mods.percent(modifiers, "resource_regen_percent")
    )

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


def innate_stats(content: GameContent, level: int) -> StatBlock:
    """То, что персонаж этого уровня имеет в каждой характеристике сам по себе.

    Рост общий и приходит без спроса (``ProgressionRules.stat_growth_per_level``,
    ADR 0058): он и делает так, что удача, ловкость и мудрость воина растут
    вместе с ним, а не остаются пятёркой создания навсегда.
    """
    return StatBlock.uniform(content.rules.innate_stat_value(level))


def stat_allowance(content: GameContent, level: int) -> int:
    """Сколько всего очков характеристик выдано персонажу этого уровня."""
    rules = content.rules
    return rules.free_points_at_creation + rules.stat_points_per_level * (level - 1)


def skill_point_allowance(content: GameContent, level: int) -> int:
    """Сколько всего очков умений выдано персонажу этого уровня."""
    return content.rules.skill_point_per_level * (level - 1)
