"""Действие относится к месту и наполнению, показанным игроку."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.location import Engagement, LocationState, NodeKind, NodeState, Roamer
from mmorpg.presentation.telegram.flows.play import (
    Clock,
    LocationSession,
    PlayState,
    advance,
    build_location,
    node_foes,
    node_pack_seed,
    node_standing,
    render,
)
from mmorpg.presentation.telegram.screens import play as ui
from mmorpg.presentation.telegram.screens.base import ScreenId

SEED = "vellar-object-ownership"
CLOCK = Clock(now=1_000)


@pytest.fixture
def hero() -> Character:
    return Character(id=1, user_id=42, name="Аргус", race_id="human", class_id="warrior")


def visit(content: GameContent, kind: NodeKind) -> PlayState:
    for place in content.city("farhold").locations:
        session = LocationSession(city_id="farhold", slot=place.slot, epoch=0, wave=0)
        location = build_location(content, SEED, session)
        node = next((one for one in location.nodes if one.kind is kind), None)
        if node is not None:
            return PlayState(screen=ScreenId.LOCATION, session=replace(session, node=node.index))
    raise AssertionError(f"No {kind} node in the test city's locations")


def action(content: GameContent, hero: Character, state: PlayState) -> str:
    screen = render(content, hero, state, world_seed=SEED, clock=CLOCK)
    return screen.rows[0][0].text


def press(
    content: GameContent,
    hero: Character,
    state: PlayState,
    text: str,
    shared: LocationState = LocationState(),
) -> PlayState:
    return advance(content, hero, state, text, world_seed=SEED, clock=CLOCK, location_state=shared)


@pytest.mark.parametrize(
    "kind", [NodeKind.BATTLE, NodeKind.CACHE, NodeKind.EVENT, NodeKind.SHRINE, NodeKind.GATHER]
)
def test_another_nodes_action_cannot_use_the_current_object(
    content: GameContent, hero: Character, kind: NodeKind
) -> None:
    state = visit(content, kind)
    original = action(content, hero, state)
    # Другой объект того же вида: старое общее действие могло бы сработать здесь.
    moved = None
    for city in content.cities:
        for place in city.locations:
            session = LocationSession(city_id=city.id, slot=place.slot, epoch=0, wave=0)
            location = build_location(content, SEED, session)
            other = next(
                (
                    one
                    for one in location.nodes
                    if one.kind is kind
                    and (city.id, place.slot, one.index)
                    != (state.session.city_id, state.session.slot, state.session.node)
                ),
                None,
            )
            if other is not None:
                moved = replace(state, session=replace(session, node=other.index))
                break
        if moved is not None:
            break
    assert moved is not None
    result = press(content, hero, moved, original)
    assert result.screen is ScreenId.LOCATION
    assert result.pending.empty
    assert not result.fight
    assert "текущему узлу или объекту" in result.notice


def test_old_transition_cannot_move_from_a_different_origin(
    content: GameContent, hero: Character
) -> None:
    state = visit(content, NodeKind.BATTLE)
    location = build_location(content, SEED, state.session)
    destination = next(one for one in location.nodes if len(one.links) >= 2)
    origin, other = destination.links[:2]
    moved = replace(state, session=replace(state.session, node=other))
    old = ui.node_button(destination, origin, location.name).text
    result = press(content, hero, moved, old)
    assert result.session.node == other
    assert result.pending.empty


@pytest.mark.parametrize("kind", [NodeKind.BATTLE, NodeKind.CACHE, NodeKind.GATHER])
def test_old_wave_never_selects_a_replacement(
    content: GameContent, hero: Character, kind: NodeKind
) -> None:
    state = visit(content, kind)
    original = action(content, hero, state)
    new_wave = LocationState(nodes={state.session.node: NodeState(wave=1)})
    result = press(content, hero, state, original, new_wave)
    assert not result.fight
    assert result.pending.empty
    assert "появились новые" in result.notice


def test_missing_selected_pack_does_not_fall_back_to_first_free(
    content: GameContent, hero: Character
) -> None:
    state = visit(content, NodeKind.BATTLE)
    screen = render(content, hero, state, world_seed=SEED, clock=CLOCK)
    second = screen.rows[1][0].text
    shared = LocationState(nodes={state.session.node: NodeState(taken_slots=1 << 1)})
    result = press(content, hero, state, second, shared)
    assert not result.fight
    assert result.pending.empty


def test_node_action_needs_its_exact_caption(content: GameContent, hero: Character) -> None:
    state = visit(content, NodeKind.CACHE)
    forged = ui.NODE_ACTIONS[NodeKind.CACHE] + ": чужой тайник"
    result = press(content, hero, state, forged)
    assert result.pending.empty


def test_empty_battle_nodes_own_button_explains_the_empty_place(
    content: GameContent, hero: Character
) -> None:
    state = visit(content, NodeKind.BATTLE)
    shared = LocationState(
        nodes={state.session.node: NodeState(taken_slots=0xFF, emptied_at=CLOCK.now - 1)}
    )
    screen = render(content, hero, state, world_seed=SEED, clock=CLOCK, location_state=shared)
    result = press(content, hero, state, screen.rows[0][0].text, shared)
    assert "уже всё разобрали" in result.notice
    assert "Не узнал" not in result.notice
    assert not result.fight
    assert result.pending.empty


def test_changed_enemy_properties_require_a_new_choice(
    content: GameContent, hero: Character
) -> None:
    state = visit(content, NodeKind.BATTLE)
    location = build_location(content, SEED, state.session)
    foes = node_foes(
        content,
        world_seed=SEED,
        session=state.session,
        location=location,
        index=state.session.node,
        standing=node_standing(content, SEED, state.session, LocationState(), CLOCK.now),
        state=LocationState(),
    )
    state = replace(
        state,
        session=replace(state.session, foes=tuple((one.place, one.signature) for one in foes)),
    )
    original = action(content, hero, state)
    changed = content.rebuilt(
        enemy_archetypes=tuple(
            replace(one, health=one.health * 2) for one in content.enemy_archetypes
        )
    )
    result = press(changed, hero, state, original)
    assert not result.fight
    assert "свойства этой стаи изменились" in result.notice
    assert press(content, hero, state, original).fight == "node:0"


def test_new_generation_returns_to_entrance_without_acting(
    content: GameContent, hero: Character
) -> None:
    state = visit(content, NodeKind.BATTLE)
    shared = LocationState(epoch_offset=1)
    result = press(content, hero, state, action(content, hero, state), shared)
    assert result.session.node == 0
    assert result.session.epoch == 1
    assert not result.fight
    assert result.pending.empty


def test_old_generation_does_not_occupy_a_new_pack(content: GameContent) -> None:
    state = visit(content, NodeKind.BATTLE)
    location = build_location(content, SEED, state.session)
    foreign = Engagement(
        node=state.session.node,
        wave=0,
        slot=0,
        battle_id="old",
        name="Другой",
        character_id=2,
        epoch=1,
    )
    foes = node_foes(
        content,
        world_seed=SEED,
        session=state.session,
        location=location,
        index=state.session.node,
        standing=node_standing(content, SEED, state.session, LocationState(), CLOCK.now),
        state=LocationState(),
        fights=(foreign,),
    )
    assert foes and all(not one.busy for one in foes)


def test_enemy_reference_includes_every_part_of_its_place() -> None:
    session = LocationSession(city_id="farhold", slot=1, node=2, epoch=0)

    def seed(one: LocationSession, wave: int = 0, place: int = 0) -> bytes:
        return node_pack_seed(SEED, one, index=one.node, wave=wave, place=place)

    choices = (
        seed(session),
        seed(replace(session, city_id="dubno")),
        seed(replace(session, slot=2)),
        seed(replace(session, node=3)),
        seed(replace(session, epoch=1)),
        seed(session, wave=1),
        seed(session, place=1),
    )
    assert len(set(choices)) == len(choices)
    assert seed(replace(session, epoch=-1)) == seed(session)


def test_roamer_replacement_requires_a_new_choice(content: GameContent, hero: Character) -> None:
    state = visit(content, NodeKind.GATHER)
    state = replace(state, session=replace(state.session, roamer_stamp=4))
    location = build_location(content, SEED, state.session)
    roamer = Roamer(node=state.session.node, group=False, difficulty="recon", level=1, stamp=5)
    result = press(
        content,
        hero,
        state,
        ui.at_node("Спуститься в подземелье", state.session.node, location.name).text,
        LocationState(roamer=roamer),
    )
    assert not result.fight
    assert "Прежний вход" in result.notice


def test_location_view_survives_save_and_old_saves_remain_readable(content: GameContent) -> None:
    state = visit(content, NodeKind.BATTLE)
    state = replace(
        state, session=replace(state.session, wave=7, roamer_stamp=11, foes=((2, "signature"),))
    )
    assert PlayState.deserialise(state.serialise()).session == state.session
    saved = json.loads(state.serialise())
    saved["session"] = saved["session"][:4]
    legacy = PlayState.deserialise(json.dumps(saved))
    assert legacy.session.wave == -1
    assert legacy.session.roamer_stamp == -1


def test_same_node_number_in_another_location_has_different_actions(
    content: GameContent, hero: Character
) -> None:
    state = visit(content, NodeKind.BATTLE)
    location = build_location(content, SEED, state.session)
    foe = ui.NodeFoe(place=1, line="Один враг", level=1)
    first = ui.foe_label(NodeKind.BATTLE, foe, index=state.session.node, location=location.name)
    second = ui.foe_label(NodeKind.BATTLE, foe, index=state.session.node, location="Иная округа")
    assert first.text != second.text
    assert not press(content, hero, state, second.text).fight


async def test_last_check_before_battle_keeps_a_working_location_screen(
    content: GameContent, hero: Character
) -> None:
    from mmorpg.config import Settings
    from mmorpg.infrastructure.cache.memory import InMemoryLocationStateCache
    from mmorpg.presentation.telegram.handlers.play import sync_location

    before = visit(content, NodeKind.BATTLE)
    before = replace(before, session=replace(before.session, foes=((0, "old properties"),)))
    attempted = replace(before, fight="node:0").at(ScreenId.COMBAT)
    result, _ = await sync_location(
        content,
        attempted,
        before,
        hero,
        InMemoryLocationStateCache(),
        CLOCK.now,
        Settings(_env_file=None, world_seed=SEED),  # type: ignore[call-arg]
    )
    assert result.screen is ScreenId.LOCATION
    assert result.stack.current is ScreenId.LOCATION
    assert ScreenId.COMBAT not in result.stack.screens
    assert not result.fight
    assert result.session.node == before.session.node
    assert result.session.foes != before.session.foes
    assert "Бой не начат" in result.notice
