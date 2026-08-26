"""Поединок с живым игроком: кому можно, чего стоит и как делится на отряды."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.combat import CombatantKind
from mmorpg.domain.rules import pvp
from mmorpg.domain.rules.combat import hero_combatant


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
    """Тому, кто ещё не заполнил панель, нечем защищаться."""
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
    """Банк существует именно затем, чтобы что-то нельзя было у тебя отнять."""
    saver = replace(veteran, id=2, name="Мерла", gold=100, bank_gold=5000)
    _, beaten, spoils = pvp.settle(veteran, saver)
    assert spoils.gold == 10
    assert beaten.bank_gold == 5000


def test_a_live_opponent_is_a_fighter_and_not_a_copy(
    content: GameContent, veteran: Character
) -> None:
    """Никакого пересчёта и никакой форы: напротив стоит сам персонаж.

    Слепка с выдуманным уроном больше нет вовсе - в бою стоит боец, за которым
    либо живой игрок, либо движок, и дерётся он своим оружием (ADR 0021).
    """
    fighter = hero_combatant(content, veteran, combatant_id=2, side=1, live=True)

    assert fighter.kind is CombatantKind.HERO
    assert fighter.level == veteran.level
    assert fighter.character_id == veteran.id
    assert fighter.user_id == veteran.user_id, "живому игроку приходит его ход"
    assert fighter.enemy is None, "у героя нет породы: у него персонаж"
    assert fighter.race_kind == "humanoid"


def test_a_fighter_the_engine_plays_gets_no_messages(
    content: GameContent, veteran: Character
) -> None:
    """Слепок арены ходит сам, и писать ему некому."""
    fighter = hero_combatant(content, veteran, combatant_id=2, side=1, live=False)
    assert fighter.live is False
    assert fighter.user_id == 0


def test_a_busy_defender_is_refused(veteran: Character) -> None:
    """В два боя сразу не зовут: ходы пришли бы на два экрана разом."""
    refused = pvp.refusal(
        veteran,
        defender_name="Мерла",
        defender_level=20,
        location_allows=True,
        defender_busy=True,
    )
    assert "уже в бою" in refused


def test_sides_settle_as_sides(veteran: Character) -> None:
    """Отряд против отряда платит как отряд, а не как четыре поединка."""
    ally = replace(veteran, id=2, name="Тьен", gold=0)
    first = replace(veteran, id=3, name="Мерла", gold=200)
    second = replace(veteran, id=4, name="Корин", gold=100)

    winners, losers, spoils = pvp.settle_sides((veteran, ally), (first, second))

    assert spoils.gold == 30, "десятая доля с каждого проигравшего"
    assert sum(one.gold for one in winners) == veteran.gold + 30
    assert [one.gold for one in losers] == [180, 90]
    # Делится поровну: пятнадцать и пятнадцать.
    assert winners[1].gold == 15


def test_settling_one_side_alone_moves_nothing(veteran: Character) -> None:
    winners, losers, spoils = pvp.settle_sides((veteran,), ())
    assert spoils.gold == 0
    assert winners[0].gold == veteran.gold
    assert losers == ()
