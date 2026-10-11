"""Роли классов, адресная помощь и сохранение ранее вложенных очков."""

from __future__ import annotations

import json
import random
from dataclasses import replace

import pytest

from mmorpg.application.battle_snapshots import content_snapshot, restore_content
from mmorpg.application.services.battle import BattleSession, deserialise, serialise
from mmorpg.domain.entities.character import Character, SkillLoadout
from mmorpg.domain.entities.combat import ActionKind, BattleAction, BattleState, EventKind
from mmorpg.domain.entities.content import ClassTactic, OwnerKind
from mmorpg.domain.entities.effects import status_effect
from mmorpg.domain.entities.statuses import StatusKind
from mmorpg.domain.rules import class_tactics, skills
from mmorpg.domain.rules.combat import (
    _apply_spec,
    _chosen_by_engine,
    act,
    hero_combatant,
    monster_combatant,
)
from mmorpg.domain.rules.skill_effects import spec_for
from tests.domain.test_combat import make_enemy


def group(content, class_id="cleric", code="cleric_slovo_istseleniya"):
    caster = Character(
        id=1,
        user_id=101,
        name="Лада",
        race_id="human",
        class_id=class_id,
        level=50,
        loadout=SkillLoadout(actives=(code, None, None, None, None, None)),
    )
    ally = Character(id=2, user_id=102, name="Борис", race_id="human", class_id="warrior", level=50)
    actor = hero_combatant(content, caster, combatant_id=1, side=0)
    friend = hero_combatant(content, ally, combatant_id=2, side=0)
    enemy = monster_combatant(make_enemy(level=50, health=100000), combatant_id=3)
    friend = replace(friend, health=friend.max_health // 5)
    return BattleState(combatants=(actor, friend, enemy), order=(1, 2, 3)), {1: caster, 2: ally}


@pytest.mark.parametrize(
    "class_id,code",
    [
        ("warrior", "warrior_provokatsiya"),
        ("paladin", "paladin_dlan_pomoshchi"),
        ("ranger", "ranger_lovushka"),
        ("cleric", "cleric_slovo_istseleniya"),
        ("druid", "druid_dykhanie_roshchi"),
    ],
)
def test_role_is_available_at_level_three(content, class_id, code):
    hero = Character(id=1, user_id=1, name="Проба", race_id="human", class_id=class_id, level=3)
    assert content.skill(code) in skills.teachable(content, hero)
    assert content.skill(code).level == 3


def test_old_character_keeps_moved_skill_rank_and_mastery(content):
    hero = Character(
        id=1,
        user_id=1,
        name="Проба",
        race_id="human",
        class_id="warrior",
        level=3,
        loadout=SkillLoadout(
            actives=("warrior_rassechenie", None, None, None, None, None),
            ranks={"warrior_rassechenie": 3},
            masteries={"warrior_rassechenie": ("3a",)},
        ),
    )
    before = hero.loadout
    assert content.skill("warrior_rassechenie") in skills.teachable(content, hero)
    assert content.skill("warrior_rassechenie") in skills.equippable(content, hero, slot=0)
    assert skills.taken_masteries(hero, content.skill("warrior_rassechenie"))
    assert hero.loadout == before


def test_ally_selection_is_not_a_turn_or_enemy_target_change(content):
    state, roster = group(content)
    selected = act(content, roster, state, BattleAction(ActionKind.AID_FOCUS, target=2), b"roles")
    assert selected.cursor == state.cursor
    assert selected.round == state.round
    assert selected.by_id(1).actions == 0
    assert selected.by_id(1).resource == state.by_id(1).resource
    assert selected.by_id(1).aid_focus == 2
    assert selected.by_id(1).focus == state.by_id(1).focus


@pytest.mark.parametrize("target", [3, 999])
def test_wrong_ally_is_refused_before_spending(content, target):
    state, roster = group(content)
    done = act(
        content, roster, state, BattleAction(ActionKind.SKILL, slot=0, target=target), b"roles"
    )
    assert done.combatants == state.combatants
    assert done.cursor == state.cursor
    assert done.events[0].kind is EventKind.NO_TARGET


def test_departed_ally_is_refused_before_spending(content):
    state, roster = group(content)
    state = state.replace_combatant(replace(state.by_id(2), left=True))
    done = act(content, roster, state, BattleAction(ActionKind.SKILL, slot=0, target=2), b"roles")
    assert done.combatants == state.combatants
    assert done.events[0].kind is EventKind.NO_TARGET


def test_heal_uses_selected_ally_and_its_max_health(content):
    state, roster = group(content)
    state = state.replace_combatant(replace(state.by_id(1), aid_focus=2))
    before = state.by_id(2)
    done = act(content, roster, state, BattleAction(ActionKind.SKILL, slot=0), b"roles")
    healed = [e for e in done.events if e.kind is EventKind.HEAL]
    assert healed and all(e.actor_id == 2 for e in healed)
    assert done.by_id(2).health > before.health
    assert done.by_id(1).health == state.by_id(1).health
    assert healed[0].amount >= round(before.max_health * 0.18 * 1.25)


def test_ally_heal_block_is_respected(content):
    state, roster = group(content)
    friend = state.by_id(2)
    friend = replace(
        friend, effects=friend.effects.apply(status_effect(StatusKind.HEAL_BLOCK, turns=3))
    )
    state = state.replace_combatant(friend)
    done = act(content, roster, state, BattleAction(ActionKind.SKILL, slot=0, target=2), b"roles")
    assert done.by_id(2).health == friend.health
    assert not any(e.kind is EventKind.HEAL for e in done.events)


@pytest.mark.parametrize(
    "class_id,code",
    [
        ("paladin", "paladin_shchit_very"),
        ("cleric", "cleric_pokrov"),
        ("paladin", "paladin_svet_vo_tme"),
    ],
)
def test_barrier_and_cleanse_go_to_the_same_ally(content, class_id, code):
    state, roster = group(content, class_id, code)
    friend = state.by_id(2)
    friend = replace(
        friend, effects=friend.effects.apply(status_effect(StatusKind.SILENCE, turns=3))
    )
    state = state.replace_combatant(friend)
    done = act(content, roster, state, BattleAction(ActionKind.SKILL, slot=0, target=2), b"roles")
    assert done.by_id(2).barrier > 0
    assert done.by_id(1).barrier == 0
    if code == "paladin_svet_vo_tme":
        assert not done.by_id(2).effects.has(StatusKind.SILENCE)


def test_druid_regeneration_is_given_to_ally(content):
    state, roster = group(content, "druid", "druid_dykhanie_roshchi")
    done = act(content, roster, state, BattleAction(ActionKind.SKILL, slot=0, target=2), b"roles")
    assert done.by_id(2).effects.has(StatusKind.HEALTH_REGEN)
    assert not done.by_id(1).effects.has(StatusKind.HEALTH_REGEN)


def test_group_mastery_does_not_need_a_single_valid_ally(content):
    state, roster = group(content)
    hero = roster[1]
    hero = replace(hero, loadout=hero.loadout.with_rank("cleric_slovo_istseleniya", 5))
    spread = next(
        m for m in content.skill("cleric_slovo_istseleniya").masteries if m.way == "spread"
    )
    hero = replace(hero, loadout=hero.loadout.with_mastery("cleric_slovo_istseleniya", spread.code))
    roster[1] = hero
    state = state.replace_combatant(replace(state.by_id(1), health=10, aid_focus=999))
    done = act(content, roster, state, BattleAction(ActionKind.SKILL, slot=0), b"roles")
    assert done.by_id(1).health > 10
    assert done.by_id(2).health > state.by_id(2).health


def test_tactics_use_visible_conditions_and_never_stack(content):
    state, _ = group(content)
    actor, target = state.by_id(1), state.by_id(2)
    for klass in content.classes:
        assert klass.tactics
        for tactic in klass.tactics:
            triggered_actor, triggered_target = actor, target
            if tactic.condition == "actor_status":
                triggered_actor = replace(
                    actor, effects=actor.effects.apply(status_effect(tactic.statuses[0], turns=2))
                )
            elif tactic.condition == "target_status":
                triggered_target = replace(
                    target, effects=target.effects.apply(status_effect(tactic.statuses[0], turns=2))
                )
            elif tactic.condition == "actor_health_below":
                triggered_actor = replace(actor, health=1)
            elif tactic.condition == "actor_barrier":
                triggered_actor = replace(actor, barrier=1)
            assert class_tactics.active(tactic, triggered_actor, triggered_target)
            value = class_tactics.factor(
                (tactic, tactic), tactic.category, triggered_actor, triggered_target
            )
            assert value == 1 + tactic.bonus / 100
            assert value <= 1.25
            assert str(round(tactic.bonus)) in class_tactics.description(tactic)
    boundary = ClassTactic("Порог", "heal", "target_health_below", 25, threshold=40)
    assert not class_tactics.active(boundary, actor, replace(target, health=40, max_health=100))
    assert not class_tactics.active(replace(boundary, condition="unknown"), actor, target)


def test_tactic_changes_real_skill_damage_but_not_racial(content):
    state, roster = group(content, "barbarian", "barbarian_dikiy_zamakh")
    actor = state.by_id(1)
    state = state.replace_combatant(replace(actor, health=1))
    skill = content.skill("barbarian_dikiy_zamakh")
    spec = replace(spec_for(skill.effect), always_hits=True)
    full = _apply_spec(
        content,
        roster,
        state.replace_combatant(actor),
        1,
        skill,
        spec,
        skill.power,
        3,
        random.Random(1),
    )
    low = _apply_spec(content, roster, state, 1, skill, spec, skill.power, 3, random.Random(1))
    racial = replace(skill, owner_kind=OwnerKind.RACE)
    racial_low = _apply_spec(
        content, roster, state, 1, racial, spec, skill.power, 3, random.Random(1)
    )
    assert low.by_id(3).health < full.by_id(3).health
    assert racial_low.by_id(3).health == full.by_id(3).health


def test_engine_can_heal_instead_of_aiming_support_at_enemy(content):
    state, roster = group(content)
    choices = [
        _chosen_by_engine(content, roster, state, state.by_id(1), i.to_bytes(8)) for i in range(50)
    ]
    used = [one for one in choices if one.kind is ActionKind.SKILL]
    assert used and all(one.target == 2 for one in used)


def test_snapshot_keeps_new_rules_and_reads_old_fields(content):
    state, _ = group(content)
    state = state.replace_combatant(replace(state.by_id(1), aid_focus=2))
    session = BattleSession(id="roles", state=state, seed=b"roles")
    assert deserialise(serialise(session)).state.by_id(1).aid_focus == 2
    data = json.loads(serialise(session))
    for one in data["combatants"]:
        one.pop("aid_focus", None)
    assert deserialise(json.dumps(data)).state.by_id(1).aid_focus == 0
    _, raw = content_snapshot(content)
    assert restore_content(raw).classes == content.classes
    old = json.loads(raw)
    for one in old["data"]["classes"]["sequence"]:
        one["fields"].pop("tactics", None)
    assert all(not one.tactics for one in restore_content(json.dumps(old)).classes)
