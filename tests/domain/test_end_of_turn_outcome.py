"""Последний периодический урон завершает бой до следующего запроса игрока."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.combat import (
    ActionKind,
    BattleAction,
    BattleOutcome,
    BattleState,
    EventKind,
)
from mmorpg.domain.entities.effects import EffectStack, status_effect
from mmorpg.domain.entities.location import Enemy, EnemyKind
from mmorpg.domain.entities.statuses import CONTROL_STATUSES, DOT_STATUSES, StatusKind
from mmorpg.domain.rules.combat import act, hero_combatant, monster_combatant

SEED = bytes.fromhex("693eee9dabc243324e6d4d599e7948a6")
DEFEND = BattleAction(kind=ActionKind.DEFEND)


def fight(content: GameContent, *, extra_hero: bool = False, extra_enemy: bool = False):
    character = Character(id=1, user_id=1, name="Герой", race_id="human", class_id="warrior")
    foe = Enemy(
        archetype_id="grey_wolf",
        name="Противник",
        kind=EnemyKind.BEAST,
        level=1,
        max_health=50,
        damage=1,
        armor=0,
        initiative=0,
        loot=("wolf_pelt",),
        gold=13,
    )
    fighters = [
        hero_combatant(content, character, combatant_id=1, side=0),
        monster_combatant(foe, combatant_id=2, side=1),
    ]
    roster = {1: character}
    if extra_hero:
        companion = replace(character, id=3, user_id=3, name="Товарищ")
        fighters.append(hero_combatant(content, companion, combatant_id=3, side=0))
        roster[3] = companion
    if extra_enemy:
        fighters.append(monster_combatant(foe, combatant_id=4, side=1))
    return BattleState(combatants=tuple(fighters), order=tuple(one.id for one in fighters)), roster


def afflicted(state: BattleState, actor_id: int, dot: StatusKind, control: StatusKind | None):
    actor = state.by_id(actor_id)
    effects = EffectStack().apply(status_effect(dot, turns=1, magnitude=10))
    if control is not None:
        effects = effects.apply(status_effect(control, turns=2))
    return state.replace_combatant(replace(actor, health=1, effects=effects))


@pytest.mark.parametrize("dot", sorted(DOT_STATUSES))
@pytest.mark.parametrize("control", [None, *sorted(CONTROL_STATUSES)])
def test_final_enemy_dies_from_the_tick_and_completes_the_same_action(
    content: GameContent, dot: StatusKind, control: StatusKind | None
) -> None:
    state, roster = fight(content, extra_hero=True)
    before = afflicted(state, 2, dot, control)
    after = act(content, roster, before, DEFEND, SEED)
    assert after.outcome is BattleOutcome.DECIDED
    assert after.winner == 0
    assert after.by_id(2).health == 0
    assert all(one.alive for one in after.heroes(0))
    assert after.by_id(3).actions == 0
    assert after.active is None
    assert after.gold == 13 and after.experience > 0
    assert "wolf_pelt" in after.loot
    assert any(event.kind is EventKind.DEFEATED and event.target_id == 2 for event in after.events)
    if control is not None:
        assert any(
            event.kind is EventKind.TURN_SKIPPED and event.actor_id == 2 for event in after.events
        )
    replay = act(content, roster, after, DEFEND, SEED)
    assert (replay.gold, replay.experience, replay.loot) == (
        after.gold,
        after.experience,
        after.loot,
    )


@pytest.mark.parametrize("control", [None, StatusKind.FREEZE])
def test_the_last_hero_dying_from_a_tick_does_not_give_the_enemy_an_extra_turn(
    content: GameContent, control: StatusKind | None
) -> None:
    state, roster = fight(content)
    before = afflicted(state, 1, StatusKind.POISON, control)
    after = act(content, roster, before, DEFEND, SEED)
    assert after.outcome is BattleOutcome.DECIDED and after.winner == 1
    assert after.by_id(1).health == 0
    assert after.by_id(2).health == state.by_id(2).health
    assert not any(event.actor_id == 2 for event in after.events)
    assert (after.gold, after.experience, after.loot) == (0, 0, ())


def test_a_surviving_enemy_preserves_the_next_live_players_turn(content: GameContent) -> None:
    state, roster = fight(content, extra_hero=True, extra_enemy=True)
    before = afflicted(state, 2, StatusKind.BURNING, StatusKind.STUN)
    after = act(content, roster, before, DEFEND, SEED)
    assert not after.is_over
    assert after.by_id(2).health == 0
    assert after.active.id == 3
    assert after.by_id(3).actions == 0
    assert after.by_id(4).health == state.by_id(4).health
    assert (after.gold, after.experience, after.loot) == (0, 0, ())


def test_two_fatal_ticks_on_the_same_enemy_do_not_duplicate_defeat_or_rewards(
    content: GameContent,
) -> None:
    state, roster = fight(content)
    before = afflicted(state, 2, StatusKind.POISON, StatusKind.FREEZE)
    foe = before.by_id(2)
    before = before.replace_combatant(
        replace(
            foe, effects=foe.effects.apply(status_effect(StatusKind.BURNING, turns=1, magnitude=10))
        )
    )
    after = act(content, roster, before, DEFEND, SEED)
    assert after.outcome is BattleOutcome.DECIDED and after.winner == 0
    assert (
        sum(event.kind is EventKind.DEFEATED and event.target_id == 2 for event in after.events)
        == 1
    )
    assert after.gold == 13
    assert after.loot.count("wolf_pelt") == 1


def test_two_poisoned_sides_finish_on_the_first_death_without_an_extra_tick(
    content: GameContent,
) -> None:
    state, roster = fight(content)
    before = afflicted(state, 1, StatusKind.POISON, StatusKind.STUN)
    before = afflicted(before, 2, StatusKind.BURNING, StatusKind.FREEZE)
    after = act(content, roster, before, DEFEND, SEED)
    assert after.outcome is BattleOutcome.DECIDED and after.winner == 1
    assert after.by_id(1).health == 0
    assert after.by_id(2).health == 1
    assert not any(
        event.kind is EventKind.TURN_SKIPPED and event.actor_id == 2 for event in after.events
    )


@pytest.mark.parametrize("empty_order", [False, True])
def test_an_old_ongoing_record_with_both_sides_dead_is_resolved_as_a_draw(
    content: GameContent, empty_order: bool
) -> None:
    state, roster = fight(content)
    fallen = replace(
        state,
        combatants=tuple(replace(one, health=0) for one in state.combatants),
        order=() if empty_order else state.order,
    )
    after = act(content, roster, fallen, DEFEND, SEED)
    assert after.outcome is BattleOutcome.DECIDED and after.winner == -1
    assert (after.gold, after.experience, after.loot) == (0, 0, ())
