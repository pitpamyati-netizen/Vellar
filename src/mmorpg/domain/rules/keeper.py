"""What a keeper of the game may do to a character - their own or somebody else's.

A keeper is not a stronger player: everything here is a shortcut through work the
game would otherwise ask for - gold that a contract would have paid, a level a
fight would have brought, wounds a night at an inn would have closed. Nothing
here invents a rule of its own: gold, levels and points arrive by the same
functions the game uses, so a keeper's character stays a legal character.

The same shortcuts are what a keeper applies to a player who wrote to say
something went wrong, which is why every function here takes the character it
changes rather than assuming it is the keeper's own.

Who is a keeper is decided outside the domain, by ``ADMIN_IDS``; this module only
answers "and then what happens".
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

# One step of each grant. Round numbers, because a keeper presses the button
# again when they want more.
GOLD_STEP = 1000
POINTS_STEP = 5


def grant_gold(character: Character, amount: int = GOLD_STEP) -> Character:
    return character.with_gold(amount)


def raise_level(content: GameContent, character: Character) -> tuple[Character, LevelUp]:
    """Exactly one level, paid for with the experience it actually costs.

    The experience is granted rather than the level set, so the points that come
    with a level come from the one place that hands them out.
    """
    if character.level >= MAX_LEVEL:
        return character, LevelUp(
            previous_level=character.level, new_level=character.level, stat_points=0, skill_points=0
        )
    needed = experience_to_reach(character.level + 1) - character.experience
    return grant_experience(content, character, max(0, needed))


def heal(content: GameContent, character: Character) -> Character:
    """Close every wound the character is carrying."""
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
