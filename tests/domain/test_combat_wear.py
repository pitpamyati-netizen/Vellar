"""Износ следует за настоящими действиями и сохраняется внутри общего боя."""

from dataclasses import replace

import pytest

from mmorpg.application.services.battle import BattleSession, deserialise, serialise
from mmorpg.domain.entities.character import Character, Equipment, ItemWear, SkillLoadout
from mmorpg.domain.entities.combat import ActionKind, BattleAction, BattleState, EventKind
from mmorpg.domain.entities.effects import EffectStack, status_effect
from mmorpg.domain.entities.statuses import StatusKind
from mmorpg.domain.rules import adventure, combat, repair

SWORD = "sword@5#common!101"
ARMOR = "heavy_body@5#common!102"
TOOL = "pick@1#common!103"


def heroes(content, wear=0):
    first = Character(
        id=1,
        user_id=11,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=10,
        equipment=Equipment({"weapon": SWORD, "body": ARMOR, "tool": TOOL}),
        wear=ItemWear({SWORD: wear}),
        loadout=SkillLoadout(actives=("warrior_rassechenie", None, None, None, None, None)),
    )
    second = replace(first, id=2, user_id=12, name="Мирна", wear=ItemWear())
    fighters = tuple(
        combat.hero_combatant(content, hero, combatant_id=hero.id, side=hero.id - 1)
        for hero in (first, second)
    )
    return {1: first, 2: second}, BattleState(combatants=fighters, order=(1, 2))


def perform(content, roster, state, action=BattleAction(ActionKind.ATTACK), *, landed=True):
    for number in range(100):
        result = combat.act(content, roster, state, action, str(number).encode())
        hit = any(event.kind in {EventKind.DAMAGE, EventKind.CRIT} for event in result.events)
        if hit == landed:
            return result
    raise AssertionError("Не найден контрольный исход")


def test_attack_wears_weapon_and_landed_hit_wears_target_gear(content):
    roster, state = heroes(content)
    result = perform(content, roster, state)
    assert dict(result.by_id(1).gear_wear) == {SWORD: 1}
    assert dict(result.by_id(2).gear_wear) == {ARMOR: 1}
    for hero in (1, 2):
        assert repair.character_with_wear(roster[hero], result.by_id(hero)).wear.spent(TOOL) == 0


def test_missed_attack_wears_weapon_but_does_not_wear_target(content):
    roster, state = heroes(content)
    result = perform(content, roster, state, landed=False)
    assert dict(result.by_id(1).gear_wear) == {SWORD: 1}
    assert not result.by_id(2).gear_wear


@pytest.mark.parametrize("kind", [ActionKind.DEFEND, ActionKind.FOCUS, ActionKind.YIELD])
def test_non_attack_does_not_wear_weapon(content, kind):
    roster, state = heroes(content)
    result = combat.act(content, roster, state, BattleAction(kind, target=2), b"wear")
    assert all(not one.gear_wear for one in result.combatants)


def test_refused_skill_does_not_wear_anything(content):
    roster, state = heroes(content)
    result = combat.act(content, roster, state, BattleAction(ActionKind.SKILL, slot=5), b"wear")
    assert EventKind.EMPTY_SLOT in {event.kind for event in result.events}
    assert all(not one.gear_wear for one in result.combatants)


def test_damaging_skill_wears_weapon(content):
    roster, state = heroes(content)
    result = perform(content, roster, state, BattleAction(ActionKind.SKILL, slot=0))
    assert dict(result.by_id(1).gear_wear) == {SWORD: 1}
    assert dict(result.by_id(2).gear_wear) == {ARMOR: 1}


def test_barrier_stops_health_loss_but_equipment_still_takes_hit(content):
    roster, state = heroes(content)
    second = state.by_id(2)
    state = state.replace_combatant(replace(second, barrier=100000))
    result = perform(content, roster, state)
    assert result.by_id(2).health == second.health
    assert result.by_id(2).gear_wear[ARMOR] == 1


def test_immunity_stops_equipment_wear(content):
    roster, state = heroes(content)
    second = state.by_id(2)
    effect = status_effect(StatusKind.INVULNERABILITY, turns=3, source="test")
    state = state.replace_combatant(replace(second, effects=EffectStack().apply(effect)))
    result = combat.act(content, roster, state, BattleAction(ActionKind.ATTACK), b"wear")
    assert result.by_id(1).gear_wear[SWORD] == 1
    assert not result.by_id(2).gear_wear


def test_weapon_breaks_after_its_last_attack_and_next_attack_uses_fists(content):
    limit = content.item(SWORD).durability
    roster, state = heroes(content, limit - 1)
    result = perform(content, roster, state)
    assert result.by_id(1).gear_wear[SWORD] == limit
    assert sum(event.kind is EventKind.GEAR_BROKEN for event in result.events) == 1
    assert result.by_id(1).damage_type.is_magic is False
    after = repair.character_with_wear(roster[1], result.by_id(1))
    from mmorpg.domain.rules.equipment import weapon_of

    assert weapon_of(content, after) is None
    again = combat.act(
        content, roster, replace(result, cursor=0), BattleAction(ActionKind.ATTACK), b"again"
    )
    assert again.by_id(1).gear_wear[SWORD] == limit
    assert not any(event.kind is EventKind.GEAR_BROKEN for event in again.events)
    restricted = {
        **roster,
        1: replace(
            roster[1],
            loadout=SkillLoadout(actives=("warrior_vikhr_klinkov", None, None, None, None, None)),
        ),
    }
    refused = combat.act(
        content,
        restricted,
        replace(result, cursor=0),
        BattleAction(ActionKind.SKILL, slot=0),
        b"again",
    )
    assert EventKind.WRONG_WEAPON in {event.kind for event in refused.events}


def test_snapshot_preserves_absolute_wear_and_old_snapshot_uses_old_character_wear(content):
    roster, state = heroes(content, 7)
    result = perform(content, roster, state)
    saved = BattleSession("wear", result, b"wear")
    restored = deserialise(serialise(saved))
    assert restored.state.by_id(1).gear_wear[SWORD] == 8
    worn = repair.character_with_wear(roster[1], restored.state.by_id(1))
    assert repair.character_with_wear(worn, restored.state.by_id(1)).wear.spent(SWORD) == 8
    import json

    raw = json.loads(serialise(saved))
    for fighter in raw["combatants"]:
        fighter.pop("gear_wear")
    old = deserialise(json.dumps(raw))
    assert repair.character_with_wear(roster[1], old.state.by_id(1)).wear.spent(SWORD) == 7


def test_victory_and_defeat_add_no_wear(content):
    roster, state = heroes(content, 7)
    won = adventure.resolve_victory(content, roster[1], state, 1, experience=0, gold=0, loot=())
    lost = adventure.resolve_defeat(content, roster[1])
    assert won.character.wear.spent(SWORD) == lost.character.wear.spent(SWORD) == 7


def test_armor_breaks_on_last_hit_and_stops_protecting_in_this_battle(content):
    roster, state = heroes(content)
    roster[2] = replace(roster[2], wear=ItemWear({ARMOR: content.item(ARMOR).durability - 1}))
    result = perform(content, roster, state)
    second = result.by_id(2)
    assert second.gear_wear[ARMOR] == content.item(ARMOR).durability
    assert any(
        event.kind is EventKind.GEAR_BROKEN and event.target_id == 2 for event in result.events
    )
    assert combat._armor_of(content, roster, second) < combat._armor_of(
        content, roster, state.by_id(2)
    )


def test_damage_over_time_does_not_wear_gear(content):
    roster, state = heroes(content)
    first = state.by_id(1)
    effect = status_effect(StatusKind.BLEEDING, turns=3, source="test", magnitude=2)
    state = state.replace_combatant(replace(first, effects=EffectStack().apply(effect)))
    result = combat.act(content, roster, state, BattleAction(ActionKind.DEFEND), b"wear")
    assert result.by_id(1).health < first.health
    assert not result.by_id(1).gear_wear


def test_arena_snapshot_does_not_wear_real_opponents_equipment(content):
    roster, state = heroes(content)
    state = state.replace_combatant(replace(state.by_id(1), live=False))
    result = perform(content, roster, state)
    assert not result.by_id(1).gear_wear


def test_counterattack_wears_defenders_weapon_once(content):
    from mmorpg.domain.entities.effects import ActiveEffect
    from mmorpg.domain.rules.skill_effects import COUNTER

    roster, state = heroes(content)
    second = state.by_id(2)
    effect = ActiveEffect(
        id="counter", name="Ответный выпад", modifiers={COUNTER: 100}, turns_left=3
    )
    state = state.replace_combatant(replace(second, effects=EffectStack().apply(effect)))
    result = perform(content, roster, state)
    assert result.by_id(2).gear_wear[SWORD] == 1
    assert result.by_id(1).gear_wear[SWORD] == 1


def test_attack_on_multiple_targets_spends_one_weapon_point(content):
    roster, state = heroes(content)
    roster[1] = replace(
        roster[1],
        loadout=SkillLoadout(actives=("warrior_vikhr_klinkov", None, None, None, None, None)),
    )
    third = replace(roster[2], id=3, user_id=13, name="Мерла")
    roster[3] = third
    state = replace(
        state,
        combatants=(
            *state.combatants,
            combat.hero_combatant(content, third, combatant_id=3, side=1),
        ),
        order=(1, 2, 3),
    )
    result = perform(content, roster, state, BattleAction(ActionKind.SKILL, slot=0))
    assert result.by_id(1).gear_wear[SWORD] == 1
    attempts = [
        event
        for event in result.events
        if event.kind
        in {EventKind.DAMAGE, EventKind.CRIT, EventKind.MISS, EventKind.DODGE, EventKind.IMMUNE}
    ]
    assert {event.target_id for event in attempts} == {2, 3}
