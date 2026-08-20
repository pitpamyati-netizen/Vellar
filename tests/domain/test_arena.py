"""The Debt Circle: the stake, the payout and who is let in."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character
from mmorpg.domain.rules import arena


@pytest.fixture
def fighter() -> Character:
    return Character(
        id=1,
        user_id=42,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=20,
        gold=1000,
    )


def test_the_stake_grows_with_the_level(fighter: Character) -> None:
    """A round has to stay worth something at 200 as it was at 20."""
    assert arena.stake_for(1) < arena.stake_for(20) < arena.stake_for(200)
    # Nobody has fought yet, so the Circle is fronting the welcome and a first
    # win pays what the sign over the Circle says.
    assert arena.payout_for(fighter) == arena.stake_for(fighter.level) * 2


def test_nobody_fights_before_they_have_a_panel(fighter: Character) -> None:
    young = replace(fighter, level=arena.MIN_LEVEL - 1)
    assert f"с {arena.MIN_LEVEL} уровня" in arena.refusal(young)
    assert arena.refusal(fighter) == ""


def test_a_round_nobody_can_pay_for_is_refused(fighter: Character) -> None:
    broke = replace(fighter, gold=arena.stake_for(fighter.level) - 1)
    assert "Ставка арены" in arena.refusal(broke)


def test_the_stake_leaves_the_purse_before_the_bell(fighter: Character) -> None:
    paid, stake = arena.place_stake(fighter)
    assert stake == arena.stake_for(fighter.level)
    assert paid.gold == fighter.gold - stake


def test_winning_pays_the_stake_back_doubled(fighter: Character) -> None:
    paid, stake = arena.place_stake(fighter)
    result = arena.settle(paid, won=True)

    assert result.won is True
    assert result.payout == stake * 2
    # Out one stake, in two: a won round is worth exactly one stake.
    assert result.character.gold == fighter.gold + stake
    assert result.character.arena_wins == 1
    assert result.character.arena_losses == 0
    # The welcome is spent: the Circle now holds nothing of theirs.
    assert result.held == 0


def test_the_circle_never_pays_out_what_it_never_took_in(fighter: Character) -> None:
    """The whole point of the hold: winning cannot mint gold.

    A fighter who never loses gets the welcome once and then draws even - the
    Circle hands back the stake and nothing more, because there is nothing of
    theirs left in it.
    """
    purse = fighter
    for _ in range(6):
        staked, _ = arena.place_stake(purse)
        purse = arena.settle(staked, won=True).character

    welcome = arena.WELCOME_ROUNDS * arena.stake_for(fighter.level)
    assert purse.gold == fighter.gold + welcome


def test_a_lost_stake_is_what_a_later_win_pays_back(fighter: Character) -> None:
    """Losing is not a hole in the pocket: the Circle keeps holding the stake."""
    staked, stake = arena.place_stake(fighter)
    after_welcome = arena.settle(staked, won=True).character  # spends the welcome

    lost = arena.settle(arena.place_stake(after_welcome)[0], won=False)
    assert lost.held == stake

    won = arena.settle(arena.place_stake(lost.character)[0], won=True)
    assert won.payout == stake * 2
    # Lost one stake, won it back: level with where the losing streak started.
    assert won.character.gold == after_welcome.gold


def test_losing_forfeits_the_stake_and_nothing_else(fighter: Character) -> None:
    paid, stake = arena.place_stake(fighter)
    result = arena.settle(paid, won=False)

    assert result.won is False
    assert result.payout == 0
    assert result.character.gold == fighter.gold - stake
    assert result.character.arena_losses == 1
    # A round never costs a level, an item or a contract.
    assert result.character.level == fighter.level
    assert result.character.experience == fighter.experience
