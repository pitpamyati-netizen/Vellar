"""Keeper shortcuts: gold, a level, a healed character, spare points.

The point of every test here is that a shortcut produces a *legal* character -
the same one the long way round would have produced.
"""

from __future__ import annotations

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.rules import keeper
from mmorpg.domain.rules.progression import MAX_LEVEL, experience_to_reach
from mmorpg.domain.rules.stats import derived_stats


@pytest.fixture
def hero() -> Character:
    return Character(
        id=1,
        user_id=42,
        name="Креан",
        race_id="high_elf",
        class_id="mage",
        level=3,
        experience=350,
        gold=144,
        health=54,
    )


def test_gold_arrives_in_one_step(hero: Character) -> None:
    assert keeper.grant_gold(hero).gold == 144 + keeper.GOLD_STEP


def test_a_level_costs_the_experience_it_actually_costs(
    content: GameContent, hero: Character
) -> None:
    grown, level_up = keeper.raise_level(content, hero)

    assert grown.level == 4
    assert level_up.levels_gained == 1
    assert grown.experience == experience_to_reach(4), "the bar starts the next level empty"


def test_a_level_brings_the_points_a_level_brings(content: GameContent, hero: Character) -> None:
    """The shortcut goes through progression, so the points are not invented here."""
    rules = content.rules
    grown, level_up = keeper.raise_level(content, hero)

    assert level_up.stat_points == rules.stat_points_per_level
    assert level_up.skill_points == rules.skill_point_per_level
    assert grown.unspent_stat_points == rules.stat_points_per_level


def test_the_last_level_has_nothing_above_it(content: GameContent, hero: Character) -> None:
    from dataclasses import replace

    topped = replace(hero, level=MAX_LEVEL)
    grown, level_up = keeper.raise_level(content, topped)

    assert level_up.levels_gained == 0
    assert grown.level == MAX_LEVEL


def test_healing_fills_health_to_the_maximum_and_no_further(
    content: GameContent, hero: Character
) -> None:
    healed = keeper.heal(content, hero)
    maximum = derived_stats(content, hero).max_health

    assert healed.health == maximum
    assert keeper.heal(content, healed).health == maximum


def test_points_are_added_and_the_level_stays_where_it_was(hero: Character) -> None:
    granted = keeper.grant_points(hero)

    assert granted.level == hero.level
    assert granted.unspent_stat_points == keeper.POINTS_STEP
    assert granted.unspent_skill_points == keeper.POINTS_STEP


def test_a_keeper_flag_is_not_a_game_rule(hero: Character) -> None:
    """Nothing in the rules reads it: it only opens a screen."""
    assert hero.is_admin is False
    assert hero.as_admin(True).is_admin is True
    assert hero.as_admin(True).as_admin(False).is_admin is False
