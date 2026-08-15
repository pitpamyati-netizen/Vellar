"""What a keeper of the game may do to their own character.

A keeper is not a stronger player: everything here is a shortcut through work the
game would otherwise ask for - gold that a contract would have paid, a level a
fight would have brought, wounds a night at an inn would have closed. Nothing
here invents a rule of its own: gold, levels and points arrive by the same
functions the game uses, so a keeper's character stays a legal character.

Who is a keeper is decided outside the domain, by ``ADMIN_IDS``; this module only
answers "and then what happens".
"""

from __future__ import annotations

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
