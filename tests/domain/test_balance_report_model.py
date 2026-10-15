"""Модель баланса тратит настоящие бюджеты и точно наблюдает бой."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

import pytest
from scripts.character_balance_report import (
    LEVELS,
    SEED,
    DecisionMemory,
    action_for,
    developed,
    observe_events,
    observed_battle,
    with_rare_gear,
)

from mmorpg.domain.entities import GameContent
from mmorpg.domain.entities.combat import ActionKind, BattleEvent, BattleState, EventKind
from mmorpg.domain.entities.location import EnemyRank
from mmorpg.domain.entities.statuses import StatusKind
from mmorpg.domain.procgen.enemies import generate_enemy, generate_group
from mmorpg.domain.procgen.seeds import derive
from mmorpg.domain.rules import skills, talents
from mmorpg.domain.rules.combat import act, hero_combatant, monster_combatant, open_battle
from mmorpg.domain.rules.progression import skill_points_between
from mmorpg.domain.rules.skill_effects import EffectCategory


@pytest.mark.parametrize("partial", [False, True])
def test_all_model_builds_obey_budget_forks_identity_and_six_slots(
    content: GameContent, partial: bool
) -> None:
    for klass in content.classes:
        for level in LEVELS:
            hero = developed(content, klass.id, level, partial=partial)
            known = [
                content.skill(code)
                for code in hero.loadout.ranks
                if content.skill(code).owner_kind.value != "race"
            ]
            budget = skill_points_between(1, level, content.rules.levels_per_skill_point)
            spent = sum(hero.loadout.rank_of(one.code) for one in known) - 1
            assert spent + hero.unspent_skill_points == budget
            assert spent <= (budget // 2 if partial else budget)
            assert all(skills.belongs_to(content, hero, one) for one in known)
            forks = Counter((one.owner, one.fork) for one in known if one.fork)
            assert all(value == 1 for value in forks.values())
            assert len(hero.equipment.items) == 6
            assert talents.spent(content, hero) <= (
                talents.points(hero) // 2 if partial else talents.points(hero)
            )
            if partial:
                for code, codes in hero.loadout.masteries.items():
                    assert all(
                        one.tier == 1 for one in content.skill(code).masteries if one.code in codes
                    )
            rare = with_rare_gear(content, hero)
            assert len(rare.equipment.items) == 6
            assert all(content.item(one).rarity == "rare" for one in rare.equipment.item_ids())


def test_observer_records_transient_damage_and_exact_barrier_and_resource(
    content: GameContent,
) -> None:
    hero = developed(content, "mage", 30)
    actor = replace(
        hero_combatant(content, hero, combatant_id=1, side=0, live=True),
        max_health=100,
        health=100,
        max_resource=100,
        resource=100,
        barrier=50,
    )
    state = BattleState(combatants=(actor,), order=(1,))
    original = BattleState.replace_combatant
    with observed_battle({1: hero}) as measured:
        hurt, _ = actor.damaged(70)
        state = state.replace_combatant(hurt)
        healed, _ = hurt.healed(20)
        state = state.replace_combatant(replace(healed, resource=20))
        state = state.replace_combatant(replace(state.by_id(1), resource=80))
        state = state.with_events(BattleEvent(kind=EventKind.DAMAGE, target_id=1, amount=70))
        observe_events(state, measured)
    assert BattleState.replace_combatant is original
    result = measured[1]
    assert result.minimum_health_ratio == 0.8
    assert result.received_damage == 70
    assert result.barrier_absorbed == 50
    assert result.used_resource == 80 and result.restored_resource == 60


def test_proactive_shield_can_be_used_at_full_health_but_cannot_repeat_next_round(
    content: GameContent,
) -> None:
    hero = developed(content, "mage", 100)
    barrier = next(
        one
        for one in skills.teachable(content, hero)
        if one.is_active and skills.spec_of(one).category is EffectCategory.BARRIER
    )
    hero = replace(
        hero, loadout=replace(hero.loadout, actives=(barrier.code, None, None, None, None, None))
    )
    actor = hero_combatant(content, hero, combatant_id=1, side=0, live=True)
    enemy = generate_enemy(
        b"shield-test", archetypes=content.enemy_archetypes, biome="*", level=100
    )
    foe = replace(
        monster_combatant(enemy, combatant_id=2, side=1), max_health=1_000_000, health=1_000_000
    )
    state = BattleState(combatants=(actor, foe), order=(1, 2))
    memory = DecisionMemory()
    chosen = action_for(content, hero, state, memory)
    assert chosen.slot == 0
    following = action_for(content, hero, replace(state, round=2), memory)
    assert following.slot is None


def test_rogue_uses_stealth_then_a_learned_backstab_in_an_actual_fight(
    content: GameContent,
) -> None:
    hero = developed(content, "rogue", 10)
    seed = derive(SEED, "rogue", 10, "normal", 1)
    enemies = generate_group(
        seed,
        archetypes=content.enemy_archetypes,
        biome="*",
        level=10,
        rank=EnemyRank.NORMAL,
        elite_titles=content.elite_titles,
    )
    fighters = [
        hero_combatant(content, hero, combatant_id=1, side=0, live=True),
        *(
            monster_combatant(one, combatant_id=index + 2, side=1)
            for index, one in enumerate(enemies)
        ),
    ]
    state = open_battle(content, {1: hero}, fighters, seed)
    memory = DecisionMemory()
    first = action_for(content, hero, state, memory)
    assert first.kind is ActionKind.SKILL
    assert content.skill(hero.loadout.actives[first.slot]).effect == "buff_vanish"
    hidden = act(content, {1: hero}, state, first, derive(seed, "action", 1))
    assert hidden.by_id(1).effects.has(StatusKind.UNSEEN)
    followup = action_for(content, hero, hidden, memory)
    assert followup.kind is ActionKind.SKILL
    assert content.skill(hero.loadout.actives[followup.slot]).requires_stealth
    struck = act(content, {1: hero}, hidden, followup, derive(seed, "action", 2))
    assert any(
        one.kind in {EventKind.DAMAGE, EventKind.CRIT} and one.actor_id == 1
        for one in struck.events
    )
    assert not any(one.kind is EventKind.NEEDS_STEALTH for one in struck.events)
