"""Обходы смотрителя: золото, уровень, вылеченный персонаж, лишние очки.

Смысл каждого здешнего теста в том, что обход даёт *законного* персонажа - того
же самого, какого дала бы долгая дорога.
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.rules import keeper
from mmorpg.domain.rules import skills as skill_rules
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
    """Обход идёт через прогрессию, поэтому очки здесь не выдумываются."""
    rules = content.rules
    grown, level_up = keeper.raise_level(content, hero)

    assert level_up.stat_points == rules.stat_points_per_level
    assert level_up.skill_points == rules.skill_point_per_level
    assert grown.unspent_stat_points == rules.stat_points_per_level


def test_the_last_level_has_nothing_above_it(content: GameContent, hero: Character) -> None:
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


def test_a_named_level_is_climbed_one_step_at_a_time(content: GameContent, hero: Character) -> None:
    """Уровень выдаётся опытом, поэтому очки за каждый шаг приходят все."""
    rules = content.rules
    grown, level_up = keeper.set_level(content, hero, 7)

    assert grown.level == 7
    assert level_up.previous_level == 3
    assert level_up.stat_points == rules.stat_points_per_level * 4
    assert level_up.skill_points == rules.skill_point_per_level * 4
    assert grown.experience == experience_to_reach(7)


def test_a_level_is_never_taken_back(content: GameContent, hero: Character) -> None:
    """Очки уже вложены, умения уже изучены: понижать некуда."""
    grown, level_up = keeper.set_level(content, hero, 1)

    assert grown.level == hero.level
    assert level_up.levels_gained == 0


def test_the_ceiling_holds(content: GameContent, hero: Character) -> None:
    grown, _ = keeper.set_level(content, hero, MAX_LEVEL + 50)

    assert grown.level == MAX_LEVEL


def test_gold_can_be_changed_by_an_arbitrary_signed_amount(hero: Character) -> None:
    assert keeper.grant_gold(hero, 250).gold == 144 + 250
    assert keeper.grant_gold(hero, -1000).gold == 0, "ниже нуля не бывает"


def test_bank_gold_is_set_and_never_negative(hero: Character) -> None:
    assert keeper.set_bank_gold(hero, 500).bank_gold == 500
    assert keeper.set_bank_gold(hero, -20).bank_gold == 0


def test_health_is_set_within_the_maximum(content: GameContent, hero: Character) -> None:
    maximum = derived_stats(content, hero).max_health

    assert keeper.set_health(content, hero, 5).health == 5
    assert keeper.set_health(content, hero, 10_000).health == maximum
    assert keeper.set_health(content, hero, -3).health == 1, "персонажем на нуле не играют"


def test_renaming_touches_nothing_but_the_name(hero: Character) -> None:
    renamed = keeper.rename(hero, "  Дорн  ")

    assert renamed.name == "Дорн"
    assert renamed.level == hero.level
    assert renamed.gold == hero.gold


def test_moving_a_character_changes_only_where_they_stand(hero: Character) -> None:
    moved = keeper.move_to(hero, "dusk_harbor")

    assert moved.city_id == "dusk_harbor"
    assert moved.gold == hero.gold
    assert moved.level == hero.level


def test_a_stat_is_set_by_moving_the_difference_through_unspent_points(hero: Character) -> None:
    rich = replace(hero, unspent_stat_points=10)

    up = keeper.set_stat(rich, StatCode.STR, 4)
    assert up is not None
    assert up.allocated[StatCode.STR] == 4
    assert up.unspent_stat_points == 6

    assert keeper.set_stat(up, StatCode.END, 100) is None, "очков столько нет"
    assert keeper.set_stat(rich, StatCode.STR, -1) is None, "ниже нуля нельзя"

    back = keeper.set_stat(up, StatCode.STR, 1)
    assert back is not None and back.unspent_stat_points == 9


@pytest.fixture
def warrior() -> Character:
    return Character(
        id=2,
        user_id=42,
        name="Дорн",
        race_id="human",
        class_id="warrior",
        level=20,
        unspent_skill_points=3,
        loadout=SkillLoadout(
            actives=("warrior_rassechenie", None, None, None, None, None),
            racial="race_human_second_wind",
            ranks=MappingProxyType({"warrior_rassechenie": 3, "race_human_second_wind": 1}),
            edges=MappingProxyType({"warrior_rassechenie": "warrior_rassechenie_a"}),
        ),
    )


def test_teaching_a_skill_costs_the_keeper_no_points(
    content: GameContent, warrior: Character
) -> None:
    taught = keeper.teach_skill(content, warrior, "warrior_provokatsiya")

    assert taught is not None
    assert taught.loadout.rank_of("warrior_provokatsiya") == 1
    assert taught.unspent_skill_points == warrior.unspent_skill_points
    assert keeper.teach_skill(content, warrior, "warrior_rassechenie") is None, "уже изучено"
    assert keeper.teach_skill(content, warrior, "race_human_second_wind") is None, "расовое"


def test_a_rank_is_set_outright_without_moving_points(
    content: GameContent, warrior: Character
) -> None:
    bumped = keeper.set_skill_rank(content, warrior, "warrior_rassechenie", 5)

    assert bumped is not None and bumped.loadout.rank_of("warrior_rassechenie") == 5
    assert bumped.unspent_skill_points == warrior.unspent_skill_points


def test_a_rank_of_zero_forgets_the_skill_and_frees_its_slot(
    content: GameContent, warrior: Character
) -> None:
    gone = keeper.set_skill_rank(content, warrior, "warrior_rassechenie", 0)

    assert gone is not None
    assert not skill_rules.is_known(gone, "warrior_rassechenie")
    assert "warrior_rassechenie" not in gone.loadout.actives
    assert keeper.set_skill_rank(content, warrior, "race_human_second_wind", 0) is None


def test_lowering_the_rank_past_the_edge_rank_clears_the_edge(
    content: GameContent, warrior: Character
) -> None:
    lowered = keeper.set_skill_rank(content, warrior, "warrior_rassechenie", 1)

    assert lowered is not None
    assert lowered.loadout.edge_of("warrior_rassechenie") is None


def test_an_edge_that_is_not_on_the_skill_is_refused(
    content: GameContent, warrior: Character
) -> None:
    assert keeper.set_skill_edge(content, warrior, "warrior_rassechenie", "нет-такой") is None
    cleared = keeper.set_skill_edge(content, warrior, "warrior_rassechenie", "")
    assert cleared is not None and cleared.loadout.edge_of("warrior_rassechenie") is None


def test_respec_returns_every_class_point_and_keeps_the_racial(
    content: GameContent, warrior: Character
) -> None:
    spent = skill_rules.spent_on(content, warrior, "warrior_rassechenie")
    back = keeper.respec_skills(content, warrior)

    assert back.unspent_skill_points == warrior.unspent_skill_points + spent
    assert not skill_rules.is_known(back, "warrior_rassechenie")
    assert skill_rules.is_known(back, "race_human_second_wind")
    assert all(slot is None for slot in back.loadout.actives)


def test_a_keeper_flag_is_not_a_game_rule(hero: Character) -> None:
    """Ни одно правило этого не читает: флаг только открывает экран."""
    assert hero.is_admin is False
    assert hero.as_admin(True).is_admin is True
    assert hero.as_admin(True).as_admin(False).is_admin is False
