"""Состояние округи: одно слово по тому, сколько по ней ходили (ADR 0055)."""

from __future__ import annotations

from types import MappingProxyType

from mmorpg.domain.entities.location import LocationState, NodeState, Roamer
from mmorpg.domain.rules.mood import WORKED_AT, LocationMood, mood_of, worked_units
from mmorpg.domain.rules.nodes import REGROWTH_WAVES


def _state(
    *, waves: dict[int, tuple[int, int]] | None = None, roamer: Roamer | None = None
) -> LocationState:
    nodes = {index: NodeState(wave=w, taken=t) for index, (w, t) in (waves or {}).items()}
    return LocationState(nodes=MappingProxyType(nodes), roamer=roamer)


def test_fresh_location_is_untouched() -> None:
    assert mood_of(_state()) is LocationMood.UNTOUCHED
    assert mood_of(_state(waves={0: (0, 1), 1: (0, 1)})) is LocationMood.UNTOUCHED


def test_worked_units_counts_waves_and_current_take() -> None:
    assert worked_units(_state(waves={0: (2, 1), 1: (0, 3)})) == 6


def test_a_few_cleared_nodes_read_as_worked() -> None:
    assert mood_of(_state(waves={0: (0, WORKED_AT)})) is LocationMood.WORKED


def test_a_relaid_district_reads_as_depleted() -> None:
    assert mood_of(_state(waves={0: (REGROWTH_WAVES, 0)})) is LocationMood.DEPLETED


def test_a_roamer_makes_the_district_restless() -> None:
    roamer = Roamer(node=1, group=False, difficulty="delve", level=3, stamp=1)
    assert mood_of(_state(roamer=roamer)) is LocationMood.RESTLESS
    # Самый громкий след: перебивает и выработку.
    relaid = _state(waves={0: (REGROWTH_WAVES, 0)}, roamer=roamer)
    assert mood_of(relaid) is LocationMood.RESTLESS
