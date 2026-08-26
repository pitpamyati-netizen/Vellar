"""Что смотритель игры вправе сделать с персонажем - своим или чужим.

Смотритель - не игрок посильнее: всё здесь - обход работы, которую игра иначе
попросила бы сделать: золото, которое заплатило бы задание, уровень, который
принёс бы бой, раны, которые закрыла бы ночь на постоялом дворе. Ничто здесь не
выдумывает собственных правил: золото, уровни и очки приходят теми же функциями,
какими пользуется игра, поэтому персонаж смотрителя остаётся законным
персонажем.

Те же обходы смотритель применяет к игроку, написавшему, что что-то пошло не
так, - поэтому каждая функция здесь принимает персонажа, которого меняет, а не
считает его собственным персонажем смотрителя.

Кто смотритель, решается вне домена, через ``ADMIN_IDS``; этот модуль отвечает
только на вопрос «и что тогда происходит».
"""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.rules.progression import (
    MAX_LEVEL,
    LevelUp,
    experience_to_reach,
    grant_experience,
)
from mmorpg.domain.rules.stats import derived_stats

# Один шаг каждой выдачи. Круглые числа, потому что смотритель нажмёт кнопку ещё раз,
# если захочет больше.
GOLD_STEP = 1000
POINTS_STEP = 5


def grant_gold(character: Character, amount: int = GOLD_STEP) -> Character:
    return character.with_gold(amount)


def raise_level(content: GameContent, character: Character) -> tuple[Character, LevelUp]:
    """Ровно один уровень, оплаченный тем опытом, которого он и правда стоит.

    Выдаётся опыт, а не ставится уровень, поэтому очки, идущие с уровнем, приходят из
    единственного места, где их раздают.
    """
    if character.level >= MAX_LEVEL:
        return character, LevelUp(
            previous_level=character.level, new_level=character.level, stat_points=0, skill_points=0
        )
    needed = experience_to_reach(character.level + 1) - character.experience
    return grant_experience(content, character, max(0, needed))


def heal(content: GameContent, character: Character) -> Character:
    """Закрыть все раны, которые несёт персонаж."""
    maximum = derived_stats(content, character).max_health
    return character.with_health(maximum, maximum)


def grant_points(
    character: Character, stat_points: int = POINTS_STEP, skill_points: int = POINTS_STEP
) -> Character:
    return character.with_level(character.level, stat_points=stat_points, skill_points=skill_points)


def move_to(character: Character, city_id: str) -> Character:
    """Перевести персонажа в город.

    Единственная правка чужого персонажа, которая не выдаёт ничего: игрок, чей
    экран остался в снесённом городе, стоит там, пока его оттуда не выведут.
    """
    return replace(character, city_id=city_id)


def set_level(content: GameContent, character: Character, level: int) -> tuple[Character, LevelUp]:
    """Поднять до названного уровня, опытом и по одному.

    Понизить нельзя: очки уже вложены, умения уже изучены, и отобрать уровень
    значило бы оставить персонажа с тем, чего он на этом уровне иметь не может.
    """
    wanted = max(character.level, min(level, MAX_LEVEL))
    grown = character
    gained_stat = 0
    gained_skill = 0
    while grown.level < wanted:
        grown, step = raise_level(content, grown)
        gained_stat += step.stat_points
        gained_skill += step.skill_points
    return grown, LevelUp(
        previous_level=character.level,
        new_level=grown.level,
        stat_points=gained_stat,
        skill_points=gained_skill,
    )
