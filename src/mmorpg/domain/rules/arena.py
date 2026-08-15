"""The Debt Circle: a fight you do not have to wait for.

The arena used to be a queue with a sixty-second turn timer, which meant both
players had to be at their phone at the same minute and the slower one lost to a
clock. Nobody could play it, and a timer is exactly what this game promises not
to have (``docs/accessibility.md``, rule 13).

So a round of the Circle is fought against a **snapshot** of another player -
their stats, their gear, their level - driven by the ordinary combat engine
(``domain/rules/pvp.as_enemy``). The other player is not summoned, not told to
hurry, and loses nothing: the stake is between each fighter and the Circle's own
purse, not between the two of them.

- the stake scales with level, so it stays worth something at 200 as at 20;
- winning pays the stake back doubled, losing forfeits it;
- the season table counts wins, and wins are the only thing it counts.
"""

from __future__ import annotations

from dataclasses import dataclass

from mmorpg.domain.entities.character import Character

# What a round costs, by level. A tenth of what an hour of ordinary work pays,
# roughly, so a losing streak is annoying rather than ruinous.
STAKE_BASE = 20
STAKE_PER_LEVEL = 8
# Winning pays the stake back and the same again.
PAYOUT_FACTOR = 2
# Nobody fights the Circle before they have a panel to fight it with.
MIN_LEVEL = 10
# How far apart two levels may be for the Circle to call it a match.
LEVEL_WINDOW = 5


def stake_for(level: int) -> int:
    return STAKE_BASE + STAKE_PER_LEVEL * max(0, level - 1)


def payout_for(level: int) -> int:
    return stake_for(level) * PAYOUT_FACTOR


@dataclass(frozen=True, slots=True)
class Round:
    """What one settled round did to the character who fought it."""

    character: Character
    stake: int
    payout: int = 0
    won: bool = False


def refusal(character: Character) -> str:
    """Empty when a round may be fought, otherwise the reason it may not."""
    if character.level < MIN_LEVEL:
        return (
            f"В Круг выходят с {MIN_LEVEL} уровня. Ваш уровень: {character.level}. "
            "До этого дерутся на дороге."
        )
    stake = stake_for(character.level)
    if character.gold < stake:
        return f"Ставка круга — {stake} золота, у вас {character.gold}."
    return ""


def place_stake(character: Character) -> tuple[Character, int]:
    """Take the stake before the fight: a round nobody paid for is not a round."""
    stake = stake_for(character.level)
    return character.with_gold(-stake), stake


def settle(character: Character, *, won: bool) -> Round:
    """Pay out a finished round. The stake is already gone from the purse."""
    stake = stake_for(character.level)
    counted = character.with_arena_result(won=won)
    if not won:
        return Round(character=counted, stake=stake, payout=0, won=False)
    payout = payout_for(character.level)
    return Round(character=counted.with_gold(payout), stake=stake, payout=payout, won=True)
