"""Начатый бой сохраняет данные способностей и прежнее содержимое."""

import json
from dataclasses import replace

import pytest

from mmorpg.application.battle_snapshots import content_snapshot, decode, encode, restore_content
from mmorpg.application.services.battle import begin, deserialise, serialise
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.effects import EffectStack, status_effect
from mmorpg.domain.entities.location import LocationState, NodeState
from mmorpg.domain.entities.statuses import StatusKind
from mmorpg.presentation.telegram.flows.play import advance
from mmorpg.presentation.telegram.flows.state import LocationSession, PlayState
from mmorpg.presentation.telegram.screens.base import ScreenId


@pytest.fixture
def warrior():
    return Character(id=1, user_id=100, name="Тест", race_id="human", class_id="warrior")


def test_snapshot_rebuilds_rules_and_generated_items_without_original_catalogue(content):
    version, raw = content_snapshot(content)
    saved = restore_content(raw)
    assert saved.skills == content.skills
    assert saved.rules == content.rules
    assert saved.item("sword@1#common").name == content.item("sword@1#common").name
    changed = content.rebuilt(rules=replace(content.rules, active_slots=1))
    assert content_snapshot(changed)[0] != version
    assert saved.rules.active_slots != changed.rules.active_slots


def test_roster_and_combatant_powers_roundtrip(content, warrior):
    session, roster = begin(content, battle_id="snapshot", attackers=[(warrior, True)], seed=b"m03")
    one = replace(
        session.state.combatants[0],
        powers={"overheal": 25.0},
        ways=frozenset({"test"}),
        master_id=5,
        effects=EffectStack().apply(status_effect(StatusKind.POISON, turns=3, source="test")),
    )
    session = replace(
        session,
        state=session.state.replace_combatant(one),
        version=8,
        roster_snapshot=json.dumps(encode(roster)),
    )
    saved = deserialise(serialise(session))
    assert saved.state.combatants == session.state.combatants
    assert saved.version == 8
    assert decode(json.loads(saved.roster_snapshot)) == roster


def test_new_map_generation_rejects_old_action_and_explains_return(content, warrior):
    flow = PlayState(
        screen=ScreenId.LOCATION,
        session=LocationSession(city_id="farhold", slot=1, node=1, epoch=0),
    )
    world = LocationState(nodes={2: NodeState(wave=12)})
    updated = advance(
        content, warrior, flow, "Вступить в бой 1", world_seed="test", location_state=world
    )
    assert updated.session.node == 0 and updated.session.epoch == 1
    assert "Округа изменилась" in updated.notice
    assert not updated.fight and updated.pending.empty
    assert PlayState.deserialise(updated.serialise()).session == updated.session


def test_old_session_is_read_with_unknown_generation_then_can_be_bound():
    raw = PlayState(session=LocationSession("farhold", 1, 2)).serialise()
    data = json.loads(raw)
    data["session"] = data["session"][:3]
    assert PlayState.deserialise(json.dumps(data)).session.epoch == -1


def test_unknown_snapshot_version_does_not_guess_rules(content):
    _, raw = content_snapshot(content)
    data = json.loads(raw)
    data["schema"] = 999
    with pytest.raises(ValueError, match="Unsupported"):
        restore_content(json.dumps(data))


def test_two_roamer_entries_in_same_second_have_distinct_saved_identity():
    from mmorpg.presentation.telegram.flows.state import Descent

    first = Descent(city_id="farhold", roamer=True, started_at=10, stamp=20, encounter_id="first")
    second = replace(first, encounter_id="second")
    assert first.encounter(1) != second.encounter(1)
    restored = PlayState.deserialise(PlayState(descent=second).serialise())
    assert restored.descent.encounter(1) == second.encounter(1)
    assert replace(first, encounter_id="").encounter(1) == "1:10:20"
