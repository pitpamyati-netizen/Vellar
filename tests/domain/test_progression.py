"""Кривая опыта и арифметика взятого уровня."""

from __future__ import annotations

import itertools

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.rules.progression import (
    MAX_LEVEL,
    apply_experience,
    experience_into_level,
    experience_reward,
    experience_to_next_level,
    experience_to_reach,
    level_for_experience,
)


def test_level_one_costs_nothing() -> None:
    assert experience_to_reach(1) == 0
    assert level_for_experience(0) == 1


def test_curve_increases_monotonically() -> None:
    costs = [experience_to_next_level(level) for level in range(1, MAX_LEVEL)]
    for earlier, later in itertools.pairwise(costs):
        assert later > earlier


def test_thresholds_and_levels_round_trip() -> None:
    for level in range(1, MAX_LEVEL + 1):
        assert level_for_experience(experience_to_reach(level)) == level


def test_one_point_short_stays_on_the_previous_level() -> None:
    for level in range(2, 60):
        assert level_for_experience(experience_to_reach(level) - 1) == level - 1


@given(st.integers(min_value=0, max_value=500_000_000))
def test_level_is_always_in_range(experience: int) -> None:
    assert 1 <= level_for_experience(experience) <= MAX_LEVEL


def test_progress_inside_a_level() -> None:
    experience = experience_to_reach(10) + 5
    earned, needed = experience_into_level(experience)
    assert earned == 5
    assert needed == experience_to_next_level(10)


def test_max_level_has_no_progress_bar() -> None:
    assert experience_into_level(experience_to_reach(MAX_LEVEL)) == (0, 0)
    assert experience_to_next_level(MAX_LEVEL) == 0


def test_apply_experience_grants_points_per_level() -> None:
    """Очки характеристик - за каждый уровень, очко умений - через уровень."""
    result = apply_experience(
        current_level=1,
        current_experience=0,
        gained=experience_to_reach(4),
        stat_points_per_level=2,
        levels_per_skill_point=2,
    )
    assert result.new_level == 4
    assert result.levels_gained == 3
    assert result.stat_points == 6
    # С первого на четвёртый: очко на втором и очко на четвёртом.
    assert result.skill_points == 2


def test_apply_experience_without_a_level_up() -> None:
    result = apply_experience(
        current_level=5,
        current_experience=experience_to_reach(5),
        gained=1,
        stat_points_per_level=2,
        levels_per_skill_point=2,
    )
    assert result.new_level == 5
    assert result.stat_points == 0


def test_negative_experience_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        apply_experience(
            current_level=1,
            current_experience=0,
            gained=-1,
            stat_points_per_level=2,
            levels_per_skill_point=2,
        )


def test_reward_shrinks_for_low_level_enemies() -> None:
    fair = experience_reward(enemy_level=20, character_level=20)
    trivial = experience_reward(enemy_level=20, character_level=40)
    assert trivial < fair
    assert trivial >= 1


def test_reward_is_not_inflated_by_over_levelled_enemies() -> None:
    """Драться выше своего уровня платит по ставке противника, а не множителем сверху."""
    assert experience_reward(enemy_level=30, character_level=10) == experience_reward(
        enemy_level=30, character_level=30
    )


# --- прибавки, которые полгода не считались --------------------------


def test_experience_bonus_is_counted_and_named(content: GameContent) -> None:
    """``exp_percent`` обещали четыре особенности и раса человека, а читал его
    никто (``Roadmap.md``, ADR 0018)."""
    from dataclasses import replace as _replace

    from mmorpg.domain.rules import progression

    human = Character(id=1, user_id=1, name="Проба", race_id="human", class_id="warrior", level=5)
    dwarf = _replace(human, race_id="dwarf")

    assert progression.earned(content, human, 100) > 100
    assert progression.earned(content, dwarf, 100) == 100

    grown, _ = progression.grant_experience(content, human, 100)
    assert grown.experience == progression.earned(content, human, 100)
