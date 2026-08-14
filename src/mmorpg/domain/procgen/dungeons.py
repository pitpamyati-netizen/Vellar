"""Dungeons: the deep runs under a city.

A dungeon is not a location. A location is redrawn every watch, because the land
does not rewrite itself and the summary goes stale; a dug passage stays where it
was dug. So a dungeon is generated from the world seed and the city alone, with
no cycle in the chain: its name, its floors and its fee are the same for every
player and for every watch.

Going down is a party matter and is not wired yet (Roadmap 1.5). What works
already is the entrance: the first floor is a normal generated graph, so a player
can be told what waits below before anyone agrees to pay for it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from mmorpg.domain.entities.location import GeneratedLocation
from mmorpg.domain.procgen.location import generate_location
from mmorpg.domain.procgen.seeds import derive, rng

# Dungeon slots start far above the five location slots a city has, so a dungeon
# floor and a location can never derive the same seed.
DUNGEON_SLOT_BASE = 100
DUNGEONS_PER_CITY = 2
FLOORS_MIN, FLOORS_MAX = 2, 4
PARTY_MIN, PARTY_MAX = 2, 4
FEE_PER_LEVEL = 8
BIOME = "подземелье"

# Name = mark plus function, the way the people digging it would say it
# (``Narrative.md``, section 2).
MARKS: tuple[str, ...] = (
    "Затопленный",
    "Просевший",
    "Соляной",
    "Дымный",
    "Мерный",
    "Обвальный",
    "Тесный",
)
PLACES: tuple[str, ...] = ("Ход", "Спуск", "Штрек", "Подкоп", "Колодец", "Подвал")


@dataclass(frozen=True, slots=True)
class Dungeon:
    """One dug run under a city."""

    city_id: str
    index: int
    name: str
    level_min: int
    level_max: int
    floors: int
    party: int
    fee: int
    biome: str = BIOME

    @property
    def slot(self) -> int:
        return DUNGEON_SLOT_BASE + self.index

    def covers(self, level: int) -> bool:
        return self.level_min <= level <= self.level_max


def dungeon_seed(world_seed: str, city_id: str) -> bytes:
    return derive(world_seed, "dungeon", city_id)


def roll_dungeons(
    *,
    world_seed: str,
    city_id: str,
    level_min: int,
    level_max: int,
    count: int = DUNGEONS_PER_CITY,
) -> tuple[Dungeon, ...]:
    """The dungeons of one city. Same seed, same list, forever."""
    source = rng(dungeon_seed(world_seed, city_id))
    names = _names(source, count)
    low = level_min + (level_max - level_min) // 2
    high = max(low + 1, level_max)
    return tuple(
        Dungeon(
            city_id=city_id,
            index=index,
            name=name,
            level_min=low + index,
            # A band is never a single level: the deeper run of a small city
            # would otherwise open and close on the same number.
            level_max=max(high, low + index + 1),
            floors=source.randint(FLOORS_MIN, FLOORS_MAX),
            party=source.randint(PARTY_MIN, PARTY_MAX),
            fee=FEE_PER_LEVEL * (low + index),
        )
        for index, name in enumerate(names)
    )


def dungeon_floor(*, world_seed: str, dungeon: Dungeon, floor: int) -> GeneratedLocation:
    """The graph of one floor.

    Floors are told apart by their slot rather than by a cycle: a dungeon does not
    rotate, so the cycle in the seed chain is fixed at zero here.
    """
    return generate_location(
        world_seed=world_seed,
        city_id=dungeon.city_id,
        slot=dungeon.slot * 10 + floor,
        cycle=0,
        name=f"{dungeon.name}, ярус {floor}",
        biome=dungeon.biome,
        level_min=dungeon.level_min,
        level_max=dungeon.level_max,
    )


def _names(source: random.Random, count: int) -> tuple[str, ...]:
    """Distinct names: two dungeons of one city never share a button label."""
    pairs = [(mark, place) for mark in MARKS for place in PLACES]
    chosen = source.sample(pairs, k=min(count, len(pairs)))
    return tuple(f"{mark} {place}" for mark, place in chosen)
