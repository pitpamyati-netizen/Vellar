"""Таланты меняют реальный бой и не отнимают старое развитие."""

import json
from dataclasses import replace
from itertools import pairwise

import pytest

from mmorpg.application.battle_snapshots import content_snapshot, decode, encode, restore_content
from mmorpg.domain.entities.character import Character, SkillLoadout
from mmorpg.domain.entities.combat import ActionKind, BattleAction, BattleState, EventKind
from mmorpg.domain.entities.effects import status_effect
from mmorpg.domain.entities.statuses import StatusKind
from mmorpg.domain.rules import skill_mastery, skills, talents
from mmorpg.domain.rules.combat import (
    act,
    hero_combatant,
    monster_combatant,
    skill_cooldown,
    skill_price,
)
from mmorpg.domain.rules.skill_effects import EffectCategory, Inflict, spec_for
from tests.domain.test_combat import make_enemy


def hero(class_id="warrior", level=150, code="warrior_sekushchiy_roscherk", rank=1):
    return Character(
        1,
        101,
        "Проба",
        "human",
        class_id,
        level=level,
        loadout=SkillLoadout(actives=(code, None, None, None, None, None), ranks={code: rank}),
    )


def arena(content, character):
    actor = hero_combatant(content, character, combatant_id=1, side=0)
    enemy = monster_combatant(make_enemy(level=character.level, health=100000), combatant_id=2)
    return BattleState(combatants=(actor, enemy), order=(1, 2))


def test_choice_is_free_exclusive_and_reversible(content):
    old = hero()
    first = talents.choose(content, old, "warrior_1_1")
    assert first is not None and old.loadout.talents == ()
    assert first.unspent_skill_points == old.unspent_skill_points
    assert first.gold == old.gold and first.loadout.ranks == old.loadout.ranks
    second = talents.choose(content, first, "warrior_1_2")
    assert second.loadout.talents == ("warrior_1_2",)
    assert talents.choose(content, second, "warrior_1_2") is None
    assert talents.choose(content, old, "cleric_1_1") is None
    assert talents.choose(content, hero(level=9), "warrior_1_1") is None
    assert talents.choose(content, old, "removed") is None


@pytest.mark.parametrize(
    "class_id", ["warrior", "barbarian", "paladin", "ranger", "rogue", "mage", "cleric", "druid"]
)
def test_all_class_rows_have_real_choices_and_no_free_duplicates(content, class_id):
    character = hero(class_id)
    for level in talents.LEVELS:
        row = [one for one in talents.pool(content, character) if one.level == level]
        assert len(row) == 3
        for one in row:
            character = talents.choose(content, character, one.code)
            assert character is not None
            assert (
                len([old for old in talents.selected(content, character) if old.level == level])
                == 1
            )
    assert len(talents.selected(content, character)) == 5


def test_invalid_or_removed_saved_choices_do_not_grant_power(content):
    character = replace(
        hero(level=10),
        loadout=SkillLoadout(
            talents=("missing", "cleric_1_1", "warrior_1_1", "warrior_1_2", "warrior_5_1")
        ),
    )
    assert [one.code for one in talents.selected(content, character)] == ["warrior_1_1"]


def test_old_and_new_battle_snapshots_keep_progress(content):
    character = talents.choose(content, hero(), "warrior_1_1")
    saved = decode(json.loads(json.dumps(encode(character))))
    assert saved.loadout == character.loadout
    old = encode(character.loadout)
    del old["fields"]["talents"]
    restored = decode(old)
    assert restored.talents == () and restored.ranks == character.loadout.ranks
    _, raw = content_snapshot(content)
    assert restore_content(raw).talents == content.talents


def damage(content, character):
    state = arena(content, character)
    done = act(
        content,
        {1: character},
        state,
        BattleAction(ActionKind.SKILL, slot=0, target=2),
        b"talent-hit",
    )
    return sum(
        event.amount
        for event in done.events
        if event.kind in {EventKind.DAMAGE, EventKind.CRIT} and event.actor_id == 1
    )


def test_damage_talent_and_each_rank_change_actual_damage(content):
    character = hero()
    assert (
        damage(content, talents.choose(content, character, "warrior_1_1"))
        > damage(content, character)
        > 0
    )
    damages = [damage(content, hero(rank=rank)) for rank in range(1, 6)]
    assert all(later > earlier for earlier, later in pairwise(damages))
    assert damages[-1] >= damages[0] * 2


def test_talents_change_actual_price_and_cooldown(content):
    character = hero(code="warrior_rassechenie")
    skill = content.skill(character.loadout.actives[0])
    actor = arena(content, character).by_id(1)
    economical = talents.choose(content, character, "warrior_1_2")
    quick = talents.choose(content, character, "warrior_2_2")
    assert skill_price(content, economical, skill, actor) < skill_price(
        content, character, skill, actor
    )
    assert skill_cooldown(content, quick, skill, actor) < skill_cooldown(
        content, character, skill, actor
    )

    def remaining(c):
        state = arena(content, c)
        actor = state.by_id(1)
        state = state.replace_combatant(replace(actor, resource=actor.max_resource // 2))
        done = act(
            content, {1: c}, state, BattleAction(ActionKind.SKILL, slot=0, target=2), b"cost"
        )
        return done.by_id(1)

    # Одинаковое восстановление в конце хода не скрывает разницу в расходе.
    expected = skill_price(content, character, skill, actor) - skill_price(
        content, economical, skill, actor
    )
    assert remaining(economical).resource - remaining(character).resource == expected
    assert remaining(quick).cooldowns[skill.code] < remaining(character).cooldowns[skill.code]


def test_final_conditional_talent_adds_power_instead_of_hitting_a_hidden_cap(content):
    character = talents.choose(content, hero(), "warrior_1_1")
    character = talents.choose(content, character, "warrior_2_1")
    stronger = talents.choose(content, character, "warrior_5_2")
    actor = arena(content, character).by_id(1)
    actor = replace(actor, effects=actor.effects.apply(status_effect(StatusKind.GUARD, turns=2)))
    target = arena(content, character).by_id(2)
    target = replace(target, health=target.max_health // 4)
    skill = content.skill(character.loadout.actives[0])
    assert talents.power_factor(content, character, skill, actor, target) == pytest.approx(1.3)
    assert talents.power_factor(content, stronger, skill, actor, target) == pytest.approx(1.55)
    target = replace(target, health=target.max_health)
    assert talents.power_factor(content, stronger, skill, actor, target) == pytest.approx(1.3)


def test_duration_and_rank_grow_numeric_extras_without_extending_control(content):
    base = spec_for("damage_stun")
    ranked = skills.at_rank(base, 5)
    assert ranked.stun_turns == base.stun_turns
    for skill in content.skills:
        if skill.is_active:
            raw = spec_for(skill.effect)
            grown = skills.at_rank(raw, 5, skill.rank_step)
            for before, after in zip(raw.inflicts, grown.inflicts, strict=True):
                if before.kind is StatusKind.TAUNT:
                    assert before.value == after.value
    # У барьера довеском сила теперь растёт вместе с главным действием.
    one = replace(base, bonus_barrier=10)
    assert skills.at_rank(one, 2).bonus_barrier > one.bonus_barrier
    frenzy = spec_for("buff_frenzy")
    assert skills.at_rank(frenzy, 5).self_modifiers[1].value == -30
    slowed = skills.at_rank(spec_for("damage_slow"), 5)
    assert slowed.inflicts[0].value > spec_for("damage_slow").inflicts[0].value


def test_long_guard_talent_extends_help_but_never_steals_an_extra_turn(content):
    character = talents.choose(content, hero(), "warrior_3_1")
    skill = next(s for s in content.skills_of("class:warrior") if s.effect == "buff_armor")
    before = skills.effective_spec(content, hero(), skill)
    after = skills.effective_spec(content, character, skill)
    assert after.duration == before.duration + 1
    assert talents.applied(content, character, skill, spec_for("damage_stun")).stun_turns == 1
    control = replace(
        spec_for("damage_stun"),
        inflicts=(Inflict(StatusKind.STUN, 2),),
        holds=(Inflict(StatusKind.GUARD, 2),),
    )
    grown = talents.applied(content, character, skill, control)
    assert grown.inflicts[0].turns == 2 and grown.holds[0].turns == 3
    assert talents.cooldown(content, character, skill, 0) == 0


@pytest.mark.parametrize(
    "class_id, code",
    [
        ("barbarian", "barbarian_2_1"),
        ("paladin", "paladin_2_1"),
        ("rogue", "rogue_2_1"),
        ("druid", "druid_2_1"),
    ],
)
def test_conditional_choices_have_visible_boundaries(content, class_id, code):
    character = hero(class_id)
    one = next(one for one in content.talents if one.code == code)
    actor = arena(content, character).by_id(1)
    target = arena(content, character).by_id(2)
    if one.condition == "actor_health_below":
        actor = replace(actor, health=actor.max_health)
        assert not talents.active(one, actor, target)
        actor = replace(actor, health=1)
    elif one.condition == "actor_barrier":
        assert not talents.active(one, actor, target)
        actor = replace(actor, barrier=1)
    elif one.condition == "target_status":
        assert not talents.active(one, actor, target)
        target = replace(
            target, effects=target.effects.apply(status_effect(one.statuses[0], turns=2))
        )
    elif one.condition == "actor_status":
        assert not talents.active(one, actor, target)
        actor = replace(actor, effects=actor.effects.apply(status_effect(one.statuses[0], turns=2)))
    assert talents.active(one, actor, target)
    assert not talents.active(replace(one, condition="gone"), actor, target)


def test_passive_talent_changes_stats_but_not_racial_skill_modifiers(content):
    character = hero()
    tougher = talents.choose(content, character, "warrior_1_3")
    assert (
        arena(content, tougher).by_id(1).max_health > arena(content, character).by_id(1).max_health
    )
    racial = content.racial_active("human")
    assert talents.matching(content, tougher, racial) == ()


def test_rank_keeps_the_existing_duration_of_every_facet(content):
    for skill in content.skills:
        for facet in skill.masteries:
            character = replace(
                hero(code=skill.code, rank=5),
                loadout=SkillLoadout(
                    ranks={skill.code: 5},
                    masteries={skill.code: (facet.code,)},
                ),
            )
            original = skill_mastery.applied(skill, spec_for(skill.effect), (facet.code,))
            previous = skills.at_rank(original, 5, 0)
            current = skills.effective_spec(content, character, skill)
            assert (current.duration, current.dot_turns, current.barrier_turns) == (
                previous.duration,
                previous.dot_turns,
                previous.barrier_turns,
            ), facet.code
            assert [(one.kind, one.turns) for one in current.inflicts] == [
                (one.kind, one.turns) for one in previous.inflicts
            ], facet.code
            assert [(one.kind, one.turns) for one in current.holds] == [
                (one.kind, one.turns) for one in previous.holds
            ], facet.code


@pytest.mark.parametrize("class_id", ["paladin", "cleric", "druid"])
def test_support_power_talent_changes_actual_ally_effect(content, class_id):
    category = EffectCategory.BARRIER if class_id == "paladin" else EffectCategory.HEAL
    skill = next(
        s
        for s in content.skills_of(f"class:{class_id}")
        if s.is_active and s.target == "ally" and spec_for(s.effect).category is category
    )
    character = hero(class_id, code=skill.code)
    friend = hero_combatant(content, hero(), combatant_id=3, side=0)
    friend = replace(friend, health=1)

    def amount(c):
        state = arena(content, c)
        state = replace(state, combatants=(*state.combatants, friend), order=(1, 3, 2))
        done = act(
            content, {1: c}, state, BattleAction(ActionKind.SKILL, slot=0, target=3), b"aid-talent"
        )
        helped = done.by_id(3)
        if spec_for(skill.effect).special == "heal_over_time":
            done = act(
                content, {1: c, 3: hero()}, done, BattleAction(ActionKind.DEFEND), b"aid-tick"
            )
            helped = done.by_id(3)
        return helped.barrier if category is EffectCategory.BARRIER else helped.health

    assert amount(talents.choose(content, character, f"{class_id}_1_1")) > amount(character)
