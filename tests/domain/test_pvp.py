"""Fighting another player: who may, what it costs, and what a snapshot is."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.location import EnemyKind, EnemyRank
from mmorpg.domain.rules import pvp
from mmorpg.domain.rules.stats import derived_stats


@pytest.fixture
def veteran() -> Character:
    return Character(
        id=1,
        user_id=42,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=20,
        gold=1000,
    )


def test_a_peaceful_location_refuses_every_attack(veteran: Character) -> None:
    refused = pvp.refusal(veteran, defender_name="Мерла", defender_level=20, location_allows=False)
    assert "не дерутся" in refused


def test_newcomers_are_not_touched_on_either_side(veteran: Character) -> None:
    """Somebody who has not filled their panel yet has nothing to defend with."""
    young = replace(veteran, level=pvp.SAFE_LEVEL - 1)
    assert pvp.refusal(young, defender_name="Мерла", defender_level=20, location_allows=True), (
        "a character below the floor cannot attack"
    )
    assert pvp.refusal(
        veteran,
        defender_name="Мерла",
        defender_level=pvp.SAFE_LEVEL - 1,
        location_allows=True,
    ), "a character below the floor cannot be attacked"


def test_the_level_window_is_narrow(veteran: Character) -> None:
    inside = pvp.refusal(
        veteran,
        defender_name="Мерла",
        defender_level=veteran.level + pvp.LEVEL_WINDOW,
        location_allows=True,
    )
    outside = pvp.refusal(
        veteran,
        defender_name="Мерла",
        defender_level=veteran.level + pvp.LEVEL_WINDOW + 1,
        location_allows=True,
    )
    assert inside == ""
    assert "Разница уровней" in outside


def test_the_stake_is_a_tenth_of_what_is_carried(veteran: Character) -> None:
    loser = replace(veteran, id=2, name="Мерла", gold=250)
    winner, beaten, spoils = pvp.settle(veteran, loser)

    assert spoils.gold == 25
    assert winner.gold == veteran.gold + 25
    assert beaten.gold == 225


def test_an_empty_purse_pays_nothing(veteran: Character) -> None:
    broke = replace(veteran, id=2, name="Мерла", gold=0)
    winner, beaten, spoils = pvp.settle(veteran, broke)
    assert (spoils.gold, winner.gold, beaten.gold) == (0, veteran.gold, 0)


def test_the_bank_is_not_part_of_the_stake(veteran: Character) -> None:
    """The bank exists precisely so that something cannot be taken from you."""
    saver = replace(veteran, id=2, name="Мерла", gold=100, bank_gold=5000)
    _, beaten, spoils = pvp.settle(veteran, saver)
    assert spoils.gold == 10
    assert beaten.bank_gold == 5000


def test_a_snapshot_is_exactly_as_strong_as_the_player(
    content: GameContent, veteran: Character
) -> None:
    """No scaling, no handicap: the opponent is the character, as they stand."""
    stats = derived_stats(content, veteran)
    enemy = pvp.as_enemy(content, veteran)

    assert enemy.level == veteran.level
    assert enemy.max_health == stats.max_health
    assert enemy.armor == stats.armor
    assert enemy.kind is EnemyKind.HUMANOID
    # Another adventurer is not a boss: the fight lasts as long as any other.
    assert enemy.rank is EnemyRank.NORMAL
    # Nothing drops off a player: the stake is gold, and it is settled separately.
    assert enemy.loot == ()
    assert enemy.gold == 0
    assert veteran.name in enemy.name
