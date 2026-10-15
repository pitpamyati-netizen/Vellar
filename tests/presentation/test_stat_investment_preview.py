"""Экран объясняет следующий вложенный балл настоящими числами героя."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.entities.stats import StatBlock, StatCode
from mmorpg.domain.rules import milestones, stats, subclass
from mmorpg.domain.rules.combat import blow_of
from mmorpg.presentation.telegram.screens.format import number
from mmorpg.presentation.telegram.screens.play import stat_effect_lines


def hero(**changes: object) -> Character:
    return replace(
        Character(id=1, user_id=1, name="Проверка", race_id="human", class_id="warrior", level=100),
        **changes,
    )


@pytest.mark.parametrize("agility", [10, 90, 300])
def test_agility_preview_uses_the_current_softened_gain(content: GameContent, agility: int) -> None:
    character = hero(allocated=StatBlock.from_mapping({"AGI": agility}))
    following = replace(character, allocated=character.allocated.with_change(StatCode.AGI, 1))
    before, after = stats.derived_stats(content, character), stats.derived_stats(content, following)
    line = next(one for one in stat_effect_lines(content, character) if one.startswith("Ловкость:"))
    assert f"точность плюс {number(after.accuracy - before.accuracy)}" in line
    assert f"шанс уклонения плюс {number(after.dodge - before.dodge)}" in line
    assert "точность 1,2" not in line


def test_specialization_and_rounding_change_the_actual_endurance_gain(content: GameContent) -> None:
    character = hero(
        subclass_ids=("warrior_knight",), allocated=StatBlock.from_mapping({"END": 30})
    )
    following = replace(character, allocated=character.allocated.with_change(StatCode.END, 1))
    before, after = stats.derived_stats(content, character), stats.derived_stats(content, following)
    line = next(
        one for one in stat_effect_lines(content, character) if one.startswith("Выносливость:")
    )
    assert f"максимальное здоровье плюс {after.max_health - before.max_health}" in line
    assert f"броня плюс {after.armor - before.armor}" in line
    assert "броня 1,35" not in line
    assert subclass.class_of(content, character).scaling_of(StatCode.END).health == 6.6


def test_charisma_preview_stops_promising_a_discount_at_the_cap(content: GameContent) -> None:
    character = hero(allocated=StatBlock.from_mapping({"CHA": 100}))
    line = next(one for one in stat_effect_lines(content, character) if one.startswith("Харизма:"))
    assert "скидка" not in line
    assert "после округления не изменятся" in line


def test_strength_preview_accounts_for_its_milestone(content: GameContent) -> None:
    character = hero(level=1)
    own = milestones.invested_stats(content, character)[StatCode.STR]
    character = replace(character, allocated=StatBlock.from_mapping({"STR": 29 - own}))
    following = replace(character, allocated=character.allocated.with_change(StatCode.STR, 1))
    line = next(one for one in stat_effect_lines(content, character) if one.startswith("Сила:"))
    assert "веха «Хватка»" in line
    assert "физический урон" in line
    blow_gain = number(blow_of(content, following) - blow_of(content, character))
    assert f"основа обычного удара плюс {blow_gain}" in line


def test_luck_preview_respects_the_combined_critical_chance_cap(content: GameContent) -> None:
    passive = next(
        one
        for one in content.skills_of("class:warrior")
        if not one.is_active and one.effect == "crit_chance_percent"
    )
    character = hero(
        allocated=StatBlock.from_mapping({"LCK": 2000}),
        trait_ids=("keen_edge",),
        loadout=SkillLoadout(ranks={passive.code: 5}),
    )
    assert stats.derived_stats(content, character).crit_chance == stats.MAX_CRIT_CHANCE
    line = next(one for one in stat_effect_lines(content, character) if one.startswith("Удача:"))
    assert "шанс критического удара" not in line
    assert "критический урон" in line
