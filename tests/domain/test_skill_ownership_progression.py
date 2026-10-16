"""Принадлежность и первое изучение проверяются; ранги не ограничены уровнем."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent, OwnerKind, SkillKind, SkillLoadout
from mmorpg.domain.entities.combat import ActionKind, BattleAction, EventKind
from mmorpg.domain.entities.location import Enemy, EnemyKind
from mmorpg.domain.rules import modifiers, skills, subclass
from mmorpg.domain.rules.combat import (
    _resolve_skill,
    act,
    hero_combatant,
    monster_combatant,
    open_battle,
)
from mmorpg.presentation.telegram.screens import skills as skill_screens

CLASSES = ("warrior", "barbarian", "paladin", "ranger", "rogue", "mage", "cleric", "druid")


def hero(class_id: str, *, level: int = 150, **changes: object) -> Character:
    character = Character(
        id=1,
        user_id=1,
        name="Проверка",
        race_id="human",
        class_id=class_id,
        level=level,
        unspent_skill_points=75,
    )
    return replace(character, **changes)


@pytest.mark.parametrize("class_id", CLASSES)
def test_foreign_active_cannot_be_learned_equipped_or_used(
    content: GameContent, class_id: str
) -> None:
    foreign = next(
        one
        for one in content.skills
        if one.owner_kind is OwnerKind.CLASS and one.owner_id != class_id and one.is_active
    )
    character = hero(class_id)
    assert not skills.learnable(content, character, foreign)
    assert skills.learn(content, character, foreign) is None

    preserved = replace(
        character,
        loadout=SkillLoadout(actives=(foreign.code, None, None, None, None, None)),
    )
    assert foreign not in skills.equippable(content, preserved, 0)
    assert skills.put_in_slot(content, preserved, 1, foreign.code) is None
    assert _resolve_skill(content, preserved, BattleAction(kind=ActionKind.SKILL, slot=0)) is None
    assert preserved.loadout.actives[0] == foreign.code
    assert "недоступно" in skill_screens.slot_label(content, preserved, 0).text
    # Не удаляем прежнее вложение: наставник может вернуть его очки.
    forgotten = skills.forget(content, preserved, foreign)
    assert forgotten is not None
    assert forgotten.unspent_skill_points == preserved.unspent_skill_points + 1


@pytest.mark.parametrize("class_id", CLASSES)
def test_foreign_passive_stays_saved_without_granting_its_effect(
    content: GameContent, class_id: str
) -> None:
    foreign = next(
        one
        for one in content.skills
        if one.owner_kind is OwnerKind.CLASS and one.owner_id != class_id and not one.is_active
    )
    character = hero(class_id)
    preserved = replace(character, loadout=SkillLoadout(ranks={foreign.code: 5}))
    assert skills.known_passives(content, preserved) == ()
    assert modifiers.collect_modifiers(content, preserved) == modifiers.collect_modifiers(
        content, character
    )
    assert preserved.loadout.rank_of(foreign.code) == 5
    assert skills.mastery_choices(content, preserved, foreign) == ()


@pytest.mark.parametrize("rank", [2, 3, 4, 5])
@pytest.mark.parametrize("class_id", CLASSES)
@pytest.mark.parametrize("kind", [SkillKind.ACTIVE, SkillKind.PASSIVE])
def test_known_skill_can_reach_every_rank_at_level_one(
    content: GameContent, rank: int, class_id: str, kind: SkillKind
) -> None:
    skill = content.skills_of(f"class:{class_id}", kind)[0]
    character = hero(class_id, level=1, loadout=SkillLoadout(ranks={skill.code: rank - 1}))
    grown = skills.learn(content, character, skill)
    assert grown is not None
    assert grown.loadout.rank_of(skill.code) == rank
    assert grown.unspent_skill_points == character.unspent_skill_points - 1
    assert "доступно с уровня" not in skill_screens.rank_offer(content, character, skill)
    assert skills.learn(content, replace(character, unspent_skill_points=0), skill) is None


def test_first_learning_also_requires_the_skills_own_level(content: GameContent) -> None:
    skill = next(one for one in content.skills_of("class:warrior") if one.level >= 30)
    character = hero("warrior", level=skill.level - 1)
    assert skills.next_rank_level(content, character, skill) == skill.level
    assert skills.learn(content, character, skill) is None
    assert skills.learn(content, replace(character, level=skill.level), skill) is not None


def test_old_rank_thresholds_do_not_restore_level_restrictions(content: GameContent) -> None:
    custom = content.rebuilt(rules=replace(content.rules, rank_unlock_levels=(1, 12, 35, 70, 120)))
    skill = custom.skills_of("class:warrior", SkillKind.ACTIVE)[0]
    character = hero("warrior", level=11, loadout=SkillLoadout(ranks={skill.code: 1}))
    assert skills.next_rank_level(custom, character, skill) == 1
    assert skills.learn(custom, replace(character, level=1), skill) is not None


def test_paid_own_skill_and_rank_survive_a_later_opening_level(content: GameContent) -> None:
    skill = next(
        one for one in content.skills_of("class:warrior") if one.is_active and one.level > 30
    )
    character = hero(
        "warrior",
        level=10,
        loadout=SkillLoadout(actives=(skill.code, None, None, None, None, None)),
    )
    assert skill in skills.teachable(content, character)
    assert _resolve_skill(content, character, BattleAction(kind=ActionKind.SKILL, slot=0)) is skill
    assert skills.learn(content, character, skill) is not None
    paid = replace(character, level=1, loadout=character.loadout.with_rank(skill.code, 5))
    assert _resolve_skill(content, paid, BattleAction(kind=ActionKind.SKILL, slot=0)) is skill
    assert skills.learn(content, paid, skill) is None


def test_racial_skill_belongs_to_its_race_and_its_separate_slot(content: GameContent) -> None:
    own = content.racial_active("human")
    foreign = next(
        one
        for one in content.skills
        if one.owner_kind is OwnerKind.RACE and one.owner_id != "human"
    )
    character = hero("warrior", loadout=SkillLoadout(ranks={own.code: 1, foreign.code: 1}))
    assert skills.learn(content, character, own) is not None
    assert skills.learn(content, character, foreign) is None
    assert skills.put_in_slot(content, character, 0, own.code) is None
    assert skills.put_in_slot(content, character, 0, foreign.code) is None
    assert _resolve_skill(content, character, BattleAction(kind=ActionKind.RACIAL)) is own
    corrupted = replace(character, loadout=replace(character.loadout, racial=foreign.code))
    assert _resolve_skill(content, corrupted, BattleAction(kind=ActionKind.RACIAL)) is None


def test_unselected_specialization_skill_does_not_work(content: GameContent) -> None:
    branch = content.subclass("warrior_gladiator")
    skill = content.skill(branch.skill_code)
    character = hero(
        "warrior", loadout=SkillLoadout(actives=(skill.code, None, None, None, None, None))
    )
    assert skills.learn(content, character, skill) is None
    assert skills.put_in_slot(content, character, 1, skill.code) is None
    assert skills.mastery_choices(content, character, skill) == ()
    assert _resolve_skill(content, character, BattleAction(kind=ActionKind.SKILL, slot=0)) is None
    chosen = replace(character, subclass_ids=(branch.id,))
    assert skills.learn(content, chosen, skill) is not None
    assert _resolve_skill(content, chosen, BattleAction(kind=ActionKind.SKILL, slot=0)) is skill


@pytest.mark.parametrize("kind", [SkillKind.ACTIVE, SkillKind.PASSIVE])
def test_ineligible_saved_slot_cannot_spend_a_turn_or_resource_in_real_combat(
    content: GameContent, kind: SkillKind
) -> None:
    skill = next(one for one in content.skills_of("class:mage", kind))
    character = hero(
        "warrior", loadout=SkillLoadout(actives=(skill.code, None, None, None, None, None))
    )
    opponent = Enemy(
        archetype_id="practice",
        name="Мишень",
        kind=EnemyKind.BEAST,
        level=1,
        max_health=10_000,
        damage=1,
        armor=0,
        initiative=0,
        loot=(),
        gold=0,
    )
    roster = {1: character}
    before = open_battle(
        content,
        roster,
        [
            hero_combatant(content, character, combatant_id=1, side=0),
            monster_combatant(opponent, combatant_id=2, side=1),
        ],
        b"ownership",
    )
    after = act(content, roster, before, BattleAction(kind=ActionKind.SKILL, slot=0), b"ownership")
    assert after.combatants == before.combatants
    assert after.cursor == before.cursor
    assert after.round == before.round
    assert after.events[-1].kind is EventKind.EMPTY_SLOT


@pytest.mark.parametrize(
    "saved,level,expected",
    [
        (("warrior_gladiator",), 29, ()),
        (("warrior_duelist",), 150, ()),
        (("warrior_gladiator", "warrior_knight"), 150, ()),
        (("warrior_gladiator", "warrior_sentinel"), 150, ("warrior_gladiator",)),
        (("warrior_gladiator", "warrior_gladiator"), 150, ("warrior_gladiator",)),
        (("mage_elementalist", "warrior_gladiator"), 150, ("warrior_gladiator",)),
        (
            ("warrior_gladiator", "warrior_duelist", "warrior_blademaster"),
            150,
            ("warrior_gladiator", "warrior_duelist", "warrior_blademaster"),
        ),
    ],
)
def test_only_a_valid_continuous_specialization_road_grants_power(
    content: GameContent, saved: tuple[str, ...], level: int, expected: tuple[str, ...]
) -> None:
    character = hero("warrior", level=level, subclass_ids=saved)
    assert tuple(one.id for one in subclass.taken(content, character)) == expected
    assert character.subclass_ids == saved
