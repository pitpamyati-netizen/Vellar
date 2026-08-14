"""Tempo: the intent, the trail and the breach.

Three rules give a fight a shape without adding a single button (Roadmap 1.1):

- **Intent.** The enemy side says out loud what it is preparing: press, guard or
  aim. It is announced before the player acts, so the announcement is
  information, not a surprise.
- **Trail.** Every action a player takes carries one of those same three tags.
  Two identical tags in a row build *momentum* and hit harder; three different
  tags in a row make a *break*, and the enemy loses its answer that turn.
- **Breach.** The tag that counters the announced intent strips the armour off
  the target for that action.

The counter cycle is closed: press beats aim, aim beats guard, guard beats press.
Nothing here is random except the enemy's next intent, and that is rolled from
the turn seed like everything else.
"""

from __future__ import annotations

import random
from enum import StrEnum

from mmorpg.domain.rules.skill_effects import EffectCategory, EffectSpec

MOMENTUM_DAMAGE_BONUS = 0.25
MOMENTUM_AT = 2
BREAK_AT = 3
INTENT_PRESS_DAMAGE = 1.3
INTENT_GUARD_REDUCTION = 0.3


class Tag(StrEnum):
    """What an action is, in the one dimension the tempo rules care about."""

    PRESS = "press"
    GUARD = "guard"
    AIM = "aim"


# What each tag beats. Reading it the other way round - who beats me - is the
# breach test, so it is written once and used from both ends.
BEATS: dict[Tag, Tag] = {
    Tag.PRESS: Tag.AIM,
    Tag.AIM: Tag.GUARD,
    Tag.GUARD: Tag.PRESS,
}

INTENT_WEIGHTS: tuple[tuple[Tag, int], ...] = (
    (Tag.PRESS, 45),
    (Tag.AIM, 30),
    (Tag.GUARD, 25),
)


def counters(tag: Tag, intent: Tag) -> bool:
    """Whether acting with ``tag`` breaks an announced ``intent``."""
    return BEATS[tag] is intent


def tag_of_attack() -> Tag:
    """A plain attack is always a press: it is the simplest thing to do."""
    return Tag.PRESS


def tag_of_spec(spec: EffectSpec | None) -> Tag:
    """Which tag a skill carries, read off what the skill actually does.

    Content never declares a tag: a skill that heals is a guard whether or not
    anybody wrote that down, and a skill that pierces armour is an aim.
    """
    if spec is None:
        return Tag.PRESS
    if spec.category in {EffectCategory.HEAL, EffectCategory.SHIELD, EffectCategory.CLEANSE}:
        return Tag.GUARD
    if spec.category in {EffectCategory.BUFF, EffectCategory.SPECIAL}:
        return Tag.GUARD
    if spec.category is EffectCategory.DEBUFF:
        return Tag.AIM
    # Damage splits by how it is delivered: precise work is an aim, the rest is
    # a press.
    if spec.pierce or spec.execute_scaling or spec.crit_bonus or spec.guaranteed_crit:
        return Tag.AIM
    if spec.stun_turns or spec.target_modifiers:
        return Tag.AIM
    return Tag.PRESS


def extended(trail: tuple[str, ...], tag: Tag) -> tuple[str, ...]:
    """The trail with one more action on it, keeping only what the rules read."""
    return (*trail, tag.value)[-BREAK_AT:]


def streak(trail: tuple[str, ...]) -> int:
    """How many identical tags the trail ends with."""
    if not trail:
        return 0
    last = trail[-1]
    count = 0
    for tag in reversed(trail):
        if tag != last:
            break
        count += 1
    return count


def has_momentum(trail: tuple[str, ...]) -> bool:
    return streak(trail) >= MOMENTUM_AT


def is_break(trail: tuple[str, ...]) -> bool:
    """Three different tags in a row: the enemy loses the thread and its turn."""
    return len(trail) >= BREAK_AT and len(set(trail[-BREAK_AT:])) == BREAK_AT


def damage_factor(trail: tuple[str, ...]) -> float:
    return 1.0 + MOMENTUM_DAMAGE_BONUS if has_momentum(trail) else 1.0


def roll_intent(source: random.Random) -> Tag:
    """What the enemy side prepares for the next turn."""
    population = [tag for tag, _ in INTENT_WEIGHTS]
    weights = [weight for _, weight in INTENT_WEIGHTS]
    return source.choices(population, weights=weights)[0]
