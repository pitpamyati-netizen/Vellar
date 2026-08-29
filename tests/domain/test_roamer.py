"""Блуждающее подземелье: появление детерминировано окном, сид отличен от городского.

Тесты свойств здесь и есть спецификация появления (``domain/rules/roamer.py``,
ADR 0037).
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from mmorpg.domain.entities.location import NodeKind
from mmorpg.domain.procgen import generate_location, location_seed
from mmorpg.domain.rules import dungeon, roamer

WORLD_SEED = "vellar-test"


def _location(slot: int = 1):
    return generate_location(
        world_seed=WORLD_SEED,
        city_id="farhold",
        slot=slot,
        name="Луга у Заставы",
        biome="луга",
        level_min=5,
        level_max=9,
        epoch=0,
    )


def _seed(slot: int = 1) -> bytes:
    return location_seed(WORLD_SEED, "farhold", slot)


def test_window_of_buckets_by_quarter_hour() -> None:
    assert roamer.window_of(0) == 0
    assert roamer.window_of(roamer.ROAMER_WINDOW - 1) == 0
    assert roamer.window_of(roamer.ROAMER_WINDOW) == 1


def test_spawn_is_deterministic_for_the_same_window() -> None:
    location = _location()
    first = roamer.roll_spawn(_seed(), location, epoch=0, window=42)
    second = roamer.roll_spawn(_seed(), location, epoch=0, window=42)
    assert first == second


@given(window=st.integers(min_value=0, max_value=5000))
def test_spawned_roamer_sits_on_an_ordinary_node(window: int) -> None:
    location = _location()
    doors = {
        node.index
        for node in location.nodes
        if node.kind in {NodeKind.ENTRANCE, NodeKind.EXIT, NodeKind.BOSS_BATTLE}
    }
    rolled = roamer.roll_spawn(_seed(), location, epoch=0, window=window)
    if rolled is None:
        return
    assert rolled.node not in doors
    assert rolled.holder == 0
    assert rolled.stamp == window
    assert rolled.level == location.level_min
    assert dungeon.difficulty_of(rolled.difficulty) is not dungeon.Difficulty.RECON


def test_some_windows_spawn_and_some_do_not() -> None:
    location = _location()
    outcomes = [
        roamer.roll_spawn(_seed(), location, epoch=0, window=window) is not None
        for window in range(200)
    ]
    assert any(outcomes), "подземелье обязано хоть когда-то объявляться"
    assert not all(outcomes), "и не в каждом окне сразу"


def test_both_solo_and_group_roamers_appear() -> None:
    location = _location()
    kinds = {
        rolled.group
        for window in range(400)
        if (rolled := roamer.roll_spawn(_seed(), location, epoch=0, window=window)) is not None
    }
    assert kinds == {True, False}


def test_epoch_and_place_change_the_roll() -> None:
    location = _location()
    base = [roamer.roll_spawn(_seed(), location, epoch=0, window=w) for w in range(60)]
    other_epoch = [roamer.roll_spawn(_seed(), location, epoch=1, window=w) for w in range(60)]
    other_slot = [
        roamer.roll_spawn(_seed(slot=2), _location(slot=2), epoch=0, window=w) for w in range(60)
    ]
    assert base != other_epoch
    assert base != other_slot


def test_run_seed_is_stable_and_unlike_the_city_descent() -> None:
    mine = roamer.run_seed(WORLD_SEED, "farhold", 1, 7, dungeon.Difficulty.DELVE)
    assert mine == roamer.run_seed(WORLD_SEED, "farhold", 1, 7, dungeon.Difficulty.DELVE)
    assert mine != roamer.run_seed(WORLD_SEED, "farhold", 1, 8, dungeon.Difficulty.DELVE)
    city = dungeon.run_seed(
        WORLD_SEED, "farhold", "farhold_first_adit", dungeon.Difficulty.DELVE, 7
    )
    assert mine != city
