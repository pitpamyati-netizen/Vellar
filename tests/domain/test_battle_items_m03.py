"""Предметы сообщают фактическую пользу и отказывают до начала хода."""

from dataclasses import replace

import pytest

from mmorpg.domain.entities.combat import ActionKind, BattleAction, BattleState, EventKind
from mmorpg.domain.entities.effects import ActiveEffect, EffectStack, status_effect
from mmorpg.domain.entities.statuses import StatusKind
from mmorpg.domain.rules.combat import _use_item, act, hero_combatant


def item_of(content, kind):
    return next(one for one in content.items if one.effect and one.effect.kind == kind)


def setup(content, warrior, **changes):
    actor = replace(hero_combatant(content, warrior, combatant_id=1, side=0), **changes)
    foe = replace(actor, id=2, side=1, character_id=2, user_id=2)
    return actor, BattleState(combatants=(actor, foe), order=(1, 2)), {1: warrior, 2: warrior}


@pytest.mark.parametrize("kind", ["heal_percent", "restore_resource_percent", "cleanse"])
def test_useless_item_keeps_turn_and_all_resources(content, warrior, kind):
    _actor, state, roster = setup(content, warrior)
    item = item_of(content, kind)
    done = act(content, roster, state, BattleAction(ActionKind.ITEM, item_id=item.id), b"m03")
    assert done.combatants == state.combatants
    assert (done.round, done.cursor) == (state.round, state.cursor)
    assert done.events[-1].kind is EventKind.ITEM_REFUSED


@pytest.mark.parametrize("missing", [1, 5, 999])
def test_resource_message_is_actual_delta(content, warrior, missing):
    actor, state, roster = setup(content, warrior)
    actor = replace(actor, resource=max(0, actor.max_resource - missing))
    state = state.replace_combatant(actor)
    item = item_of(content, "restore_resource_percent")
    done = _use_item(content, roster, state, actor, BattleAction(ActionKind.ITEM, item_id=item.id))
    event = done.events[-1]
    assert event.kind is EventKind.RESOURCE
    assert event.amount == done.by_id(actor.id).resource - actor.resource
    assert event.amount <= missing


@pytest.mark.parametrize("count", [1, 2, 3])
def test_cleansing_reports_number_actually_removed(content, warrior, count):
    item = item_of(content, "cleanse")
    stack = EffectStack()
    for kind in (StatusKind.POISON, StatusKind.BURNING, StatusKind.WEAKNESS)[:count]:
        stack = stack.apply(status_effect(kind, turns=3))
    actor, state, roster = setup(content, warrior, effects=stack)
    done = _use_item(content, roster, state, actor, BattleAction(ActionKind.ITEM, item_id=item.id))
    removed = len(actor.effects) - len(done.by_id(actor.id).effects)
    assert done.events[-1].amount == removed > 0


def test_same_buff_without_extension_is_refused_but_shorter_buff_can_be_renewed(content, warrior):
    item = item_of(content, "buff_damage_percent")
    effect = ActiveEffect(
        id=f"item:{item.id}",
        name=item.name,
        modifiers={"damage_percent": item.effect.power},
        turns_left=item.effect.turns,
    )
    actor, state, roster = setup(content, warrior, effects=EffectStack().apply(effect))
    action = BattleAction(ActionKind.ITEM, item_id=item.id)
    done = act(content, roster, state, action, b"m03")
    assert done.events[-1].kind is EventKind.ITEM_REFUSED
    shorter = state.replace_combatant(
        replace(actor, effects=EffectStack().apply(replace(effect, turns_left=1)))
    )
    assert act(content, roster, shorter, action, b"m03").events[0].kind is EventKind.EFFECT_APPLIED


def test_heal_block_does_not_consume_item_or_turn(content, warrior):
    _actor, state, roster = setup(
        content,
        warrior,
        health=1,
        effects=EffectStack().apply(status_effect(StatusKind.HEAL_BLOCK, turns=3)),
    )
    item = item_of(content, "heal_percent")
    done = act(content, roster, state, BattleAction(ActionKind.ITEM, item_id=item.id), b"m03")
    assert done.combatants == state.combatants
    assert done.events[-1].kind is EventKind.ITEM_REFUSED
