"""Вехи характеристик: пороги, за которыми очко перестаёт быть просто числом.

Ровный рост ничего не решает. Сто очков силы отличаются от девяноста девяти
только тем, что их сто, и раздача очков сводилась к арифметике: лей в ключевую,
пока не кончатся. Веха превращает раздачу в **решение** - за порогом приходит
именованная черта, и взять её можно, только собрав характеристику, а не размазав
очки по семи (ADR 0068).

ЧЕМ ВЕХА ПЛАТИТ. Обычной чертой из ``traits.toml``, и это нарочно. Черта в
Vellar не даёт ни кнопки, ни боевого умения, ни экрана - только прибавки
(``Claude.md``, правило 2). Значит вехи можно завести, ничего не прибавив к
тому, что игрок слушает: панель остаётся шесть плюс один, экранов не
прибавляется, а сборка становится глубже.

ОТ ЧЕГО ВЕХА СЧИТАЕТСЯ. От **вложенного**, а не от итогового: основа, рост по
уровню, раса, класс, розданные очки и подкласс - но не снаряжение и не эффекты.
Две причины, и обе главные.

Первая - смысл. Веха обязана быть достижением, а не следствием надетого кольца:
порог, который берут и теряют, снимая перчатки, - это не веха, а мерцание.

Вторая - постройка. ``stats.primary_stats`` считает итог через
``modifiers.collect_modifiers``, а прибавки вехи входят в тот же свёрток. Считай
веха от итога, свёрток спрашивал бы сам себя. Здесь этого не может случиться по
самой постройке: вложенное не знает о прибавках вовсе.
"""

from __future__ import annotations

from collections.abc import Iterable

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent, StatMilestone
from mmorpg.domain.entities.stats import StatBlock


def invested_stats(content: GameContent, character: Character) -> StatBlock:
    """Характеристики, нажитые самим персонажем, без снаряжения и эффектов.

    Основа с ростом по уровню, раса, класс и розданное руками. Ровно то, что
    игрок не может ни надеть, ни снять.
    """
    rules = content.rules
    total = StatBlock.uniform(rules.innate_stat_value(character.level))
    if content.has_race(character.race_id):
        total = total + content.race(character.race_id).bonuses
    klass = content.character_class(character.class_id)
    return grown(content, character, total + klass.bonuses + character.allocated)


def grown(content: GameContent, character: Character, stats: StatBlock) -> StatBlock:
    """Характеристики, поднятые прибавкой за взятые имена (ADR 0070).

    Уход возвращает розданные очки нерозданными и платит за это процентом ко
    всему сразу - навсегда. Считается процент здесь, в одном месте, и потому
    одинаково входит и в веху, и в порог подкласса, и в итоговые числа: иначе
    прибавка работала бы в одних формулах и молчала в других.

    Проценты не складываются по пройденным ступеням: у второй написано, каким ты
    стал ПОСЛЕ неё. Ступень, которой в содержимом больше нет, не прибавляет
    ничего (``Claude.md``, правило 8).
    """
    step = content.rebirth_at(character.remorts) if character.remorts else None
    if step is None or step.stat_bonus <= 0:
        return stats
    return stats.scaled(1.0 + step.stat_bonus / 100.0)


def reached(content: GameContent, character: Character) -> tuple[StatMilestone, ...]:
    """Вехи, которые этот персонаж уже взял, по порядку порогов.

    Вехи объявлены у класса: одна и та же сотня силы значит разное для воина и
    для мага, и обещать им одну черту было бы тем же обманом, что общая сетка
    (``entities/content.StatMilestone``).
    """
    klass = content.character_class(character.class_id)
    if not klass.milestones:
        return ()
    stats = invested_stats(content, character)
    taken = [
        milestone for milestone in klass.milestones if stats[milestone.stat] >= milestone.threshold
    ]
    taken.sort(key=lambda milestone: (milestone.stat.value, milestone.threshold))
    return tuple(taken)


def pending(content: GameContent, character: Character) -> tuple[tuple[StatMilestone, int], ...]:
    """Ближайшая невзятая веха каждой характеристики и сколько до неё очков.

    Экран характеристик говорит игроку, за что стоит доливать: «до Несокрушимой
    хватки семь очков силы» - это и есть та цель, ради которой характеристику
    собирают, а не размазывают.
    """
    klass = content.character_class(character.class_id)
    if not klass.milestones:
        return ()
    stats = invested_stats(content, character)
    nearest: dict[str, tuple[StatMilestone, int]] = {}
    for milestone in klass.milestones:
        short = milestone.threshold - stats[milestone.stat]
        if short <= 0:
            continue
        key = milestone.stat.value
        if key not in nearest or short < nearest[key][1]:
            nearest[key] = (milestone, short)
    return tuple(nearest[key] for key in sorted(nearest))


def trait_ids(content: GameContent, character: Character) -> tuple[str, ...]:
    """Черты, выданные вехами. Черты, которой в содержимом больше нет, здесь нет.

    Содержимое переживает персонажа так же, как панель и надетое
    (``Claude.md``, правило 8): веха, чью черту вычеркнули из ``traits.toml``,
    молча не даёт ничего вместо того, чтобы уронить экран.
    """
    return tuple(
        milestone.trait_id
        for milestone in reached(content, character)
        if milestone.trait_id and content.has_trait(milestone.trait_id)
    )


def named(milestones: Iterable[StatMilestone]) -> tuple[str, ...]:
    """Имена вех, словами игрока."""
    return tuple(milestone.name for milestone in milestones)
