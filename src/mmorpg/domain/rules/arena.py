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
- winning pays the stake back, and the same again out of what the Circle is
  already holding of yours;
- the season table counts wins, and wins are the only thing it counts.

**The Circle does not mint gold.** It used to: a flat double payout meant that
anybody winning more than half their rounds carried gold into the game out of
nothing, and it was the one inflow that did not depend on beating anything the
world put in front of them (Roadmap, "Риски"). Now the Circle pays out of the
debt it holds - the stakes it has taken from this character and not yet given
back - so over a lifetime nobody takes more out of it than they put in. What is
won there is the record, not an income.

A newcomer is given the first round on credit (``WELCOME_ROUNDS``), because a
first win that pays back exactly the stake reads as a bug rather than as a rule.
"""

from __future__ import annotations

from dataclasses import dataclass

from mmorpg.domain.entities.character import Character

# What a round costs, by level. A tenth of what an hour of ordinary work pays,
# roughly, so a losing streak is annoying rather than ruinous.
STAKE_BASE = 20
STAKE_PER_LEVEL = 8
# The best the Circle ever pays: the stake back and the same again.
PAYOUT_FACTOR = 2
# How many rounds' worth of stake the Circle fronts somebody who has never
# fought in it. One: enough that a first win pays like the sign says.
WELCOME_ROUNDS = 1
# Nobody fights the Circle before they have a panel to fight it with.
MIN_LEVEL = 10
# How far apart two levels may be for the Circle to call it a match.
LEVEL_WINDOW = 5


def stake_for(level: int) -> int:
    return STAKE_BASE + STAKE_PER_LEVEL * max(0, level - 1)


def held_for(character: Character) -> int:
    """What the Circle is holding of this character's, before this round's stake.

    Somebody who has never fought a round is holding the welcome instead: the
    Circle fronts them one stake so that a first win pays what the sign says.
    """
    if not character.arena_wins and not character.arena_losses:
        return WELCOME_ROUNDS * stake_for(character.level)
    return character.arena_credit


def payout_of(stake: int, held: int) -> int:
    """What a win pays: the stake back, plus as much of the hold as it doubles.

    ``held`` is what the Circle had of this character's *before* the round. The
    top-up is capped at the stake, so the payout is never more than double - and
    it is capped by the hold, so the Circle never pays out what it never took in.
    """
    return stake + min(stake, max(0, held))


def payout_for(character: Character) -> int:
    """What a win would pay this character if they fought a round right now."""
    return payout_of(stake_for(character.level), held_for(character))


@dataclass(frozen=True, slots=True)
class Round:
    """What one settled round did to the character who fought it."""

    character: Character
    stake: int
    payout: int = 0
    won: bool = False
    #: What the Circle still holds of theirs once this round is settled.
    held: int = 0


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
    """Take the stake before the fight: a round nobody paid for is not a round.

    The stake goes into the Circle's hold on this character, which is where a
    win is paid from later.
    """
    stake = stake_for(character.level)
    staked = character.with_gold(-stake).with_arena_credit(held_for(character) + stake)
    return staked, stake


def settle(character: Character, *, won: bool) -> Round:
    """Pay out a finished round. The stake is already in the Circle's hold."""
    stake = stake_for(character.level)
    counted = character.with_arena_result(won=won)
    # The hold already includes this round's stake: ``place_stake`` put it there.
    held = max(0, counted.arena_credit - stake)
    if not won:
        # The stake stays where it is: the Circle keeps holding it, and that is
        # what a later win is paid out of.
        return Round(character=counted, stake=stake, payout=0, won=False, held=counted.arena_credit)

    payout = payout_of(stake, held)
    paid = counted.with_gold(payout).with_arena_credit(held + stake - payout)
    return Round(character=paid, stake=stake, payout=payout, won=True, held=paid.arena_credit)
