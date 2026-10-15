"""Старые законные грани действуют, повреждённые записи не складывают силу."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.rules import skill_mastery as masteries
from mmorpg.domain.rules import skills
from mmorpg.domain.rules.skill_effects import spec_for
from mmorpg.presentation.telegram.screens.skills import mastery_words

CODE = "warrior_sekushchiy_roscherk"


def hero(rank: int, choices: tuple[str, ...]) -> Character:
    return Character(
        id=1,
        user_id=1,
        name="Проверка",
        race_id="human",
        class_id="warrior",
        level=1,
        loadout=SkillLoadout(ranks={CODE: rank}, masteries={CODE: choices}),
    )


@pytest.mark.parametrize("rank", [1, 2, 3, 4, 5])
def test_saved_masteries_require_the_actual_paid_rank(content: GameContent, rank: int) -> None:
    skill = content.skill(CODE)
    first, second = skill.masteries[0], skill.masteries[2]
    character = hero(rank, (first.code, second.code))
    expected = tuple(
        one.code for one in (first, second) if rank >= masteries.rank_of_tier(one.tier)
    )
    assert skills.masteries_of(character, skill) == expected
    clean = replace(character, loadout=replace(character.loadout, masteries={CODE: expected}))
    assert skills.effective_spec(content, character, skill) == skills.effective_spec(
        content, clean, skill
    )
    assert character.loadout.masteries_of(CODE) == (first.code, second.code)


def test_a_repeated_saved_code_is_applied_once(content: GameContent) -> None:
    skill = content.skill(CODE)
    one = skill.masteries[0]
    character = hero(5, (one.code, one.code))
    clean = hero(5, (one.code,))
    assert skills.effective_spec(content, character, skill) == skills.effective_spec(
        content, clean, skill
    )
    assert skills.masteries_of(character, skill) == (one.code,)


def test_two_alternatives_of_one_tier_are_ignored_without_hiding_the_legal_other_tier(
    content: GameContent,
) -> None:
    skill = content.skill(CODE)
    first, rival, second = skill.masteries[0], skill.masteries[1], skill.masteries[2]
    character = hero(5, (first.code, rival.code, second.code))
    clean = hero(5, (second.code,))
    assert skills.masteries_of(character, skill) == (second.code,)
    assert skills.effective_spec(content, character, skill) == skills.effective_spec(
        content, clean, skill
    )
    assert "несовместимы" in mastery_words(content, character, skill)
    assert character.loadout.masteries_of(CODE) == (first.code, rival.code, second.code)
    assert skills.choose_mastery(content, character, skill, rival.code) is None
    forgotten = skills.forget(content, character, skill)
    assert forgotten is not None and forgotten.loadout.masteries_of(CODE) == ()


def test_old_valid_masteries_are_not_removed_by_new_character_level_gates(
    content: GameContent,
) -> None:
    skill = content.skill(CODE)
    choices = (skill.masteries[0].code, skill.masteries[2].code)
    character = hero(5, choices)
    assert skills.masteries_of(character, skill) == choices
    assert masteries.applied(skill, spec_for(skill.effect), choices, rank=5) != spec_for(
        skill.effect
    )
