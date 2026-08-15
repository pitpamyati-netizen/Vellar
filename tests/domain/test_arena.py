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
    assert arena.payout_for(fighter.level) == arena.stake_for(fighter.level) * 2


def test_nobody_fights_before_they_have_a_panel(fighter: Character) -> None:
    young = replace(fighter, level=arena.MIN_LEVEL - 1)
    assert f"с {arena.MIN_LEVEL} уровня" in arena.refusal(young)
    assert arena.refusal(fighter) == ""


def test_a_round_nobody_can_pay_for_is_refused(fighter: Character) -> None:
    broke = replace(fighter, gold=arena.stake_for(fighter.level) - 1)
    assert "Ставка круга" in arena.refusal(broke)


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
