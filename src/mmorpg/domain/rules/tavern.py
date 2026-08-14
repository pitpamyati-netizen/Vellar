"""Tavern talk: the watch summary, as the room repeats it.

Every station sends its summary at the end of a watch, and by the middle of the
next one it already lies (``Narrative.md``, section 1). The tavern is where a
player hears it without walking there: the counts below are read off the
locations as they stand in the current cycle, so they change with it - which is
the same thing the summary does.

The module counts nodes; the sentence a player hears is composed in
``presentation``.
"""

from __future__ import annotations

from dataclasses import dataclass

from mmorpg.domain.entities.content import City, Location
from mmorpg.domain.entities.location import NodeKind
from mmorpg.domain.procgen.location import generate_location

RUMOURS = 3


@dataclass(frozen=True, slots=True)
class Rumour:
    """What the room says about one location during one cycle."""

    slot: int
    location_name: str
    level_min: int
    level_max: int
    nodes: int
    elites: int
    caches: int
    shrines: int

    @property
    def quiet(self) -> bool:
        """Nothing worth a detour: no strong enemy, no cache, no shrine."""
        return not (self.elites or self.caches or self.shrines)


def roll_rumours(
    *,
    world_seed: str,
    city: City,
    cycle: int,
    level: int,
    limit: int = RUMOURS,
) -> tuple[Rumour, ...]:
    """Talk about the locations closest to the player's own level.

    A location the player can actually walk into comes first; the rest are
    ordered by how far their band sits from the level, so the summary is about
    where the player might go rather than about the whole city.
    """
    ordered = sorted(
        city.locations,
        key=lambda location: (
            not location.covers(level),
            abs(location.level_min - level),
            location.slot,
        ),
    )
    chosen = sorted(ordered[:limit], key=lambda location: location.slot)
    return tuple(
        _rumour(world_seed=world_seed, location=location, cycle=cycle) for location in chosen
    )


def _rumour(*, world_seed: str, location: Location, cycle: int) -> Rumour:
    generated = generate_location(
        world_seed=world_seed,
        city_id=location.city_id,
        slot=location.slot,
        cycle=cycle,
        name=location.name,
        biome=location.biome,
        level_min=location.level_min,
        level_max=location.level_max,
    )
    kinds = [node.kind for node in generated.nodes]
    return Rumour(
        slot=location.slot,
        location_name=location.name,
        level_min=location.level_min,
        level_max=location.level_max,
        nodes=len(kinds),
        elites=kinds.count(NodeKind.ELITE_BATTLE),
        caches=kinds.count(NodeKind.CACHE),
        shrines=kinds.count(NodeKind.SHRINE),
    )
