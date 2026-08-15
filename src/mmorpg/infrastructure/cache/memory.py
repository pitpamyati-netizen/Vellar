"""In-memory caches with TTL bookkeeping.

The TTL is honoured logically rather than by a background sweeper: entries carry
an expiry stamp and are dropped on read. The clock is injected, so tests advance
time without sleeping.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace

from mmorpg.domain.entities.location import LocationState, Presence


class InMemoryStateCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._values: dict[str, tuple[str, float]] = {}

    async def get(self, key: str) -> str | None:
        entry = self._values.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at <= self._clock():
            del self._values[key]
            return None
        return value

    async def set(self, key: str, value: str, ttl: int) -> None:
        self._values[key] = (value, self._clock() + ttl)

    async def delete(self, key: str) -> None:
        self._values.pop(key, None)


class InMemoryLocationStateCache:
    """The shared state of every location, for a game running without Redis."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._states: dict[str, tuple[LocationState, float]] = {}
        self._people: dict[str, dict[int, tuple[Presence, int]]] = {}

    @staticmethod
    def _key(city_id: str, slot: int) -> str:
        return f"loc:{city_id}:{slot}"

    async def state(self, city_id: str, slot: int) -> LocationState:
        entry = self._states.get(self._key(city_id, slot))
        if entry is None:
            return LocationState()
        state, expires_at = entry
        # An untouched location goes back to its first generation eventually,
        # which is the same thing as being generated fresh.
        return state if expires_at > self._clock() else LocationState()

    async def mark_cleared(
        self, city_id: str, slot: int, generation: int, node: int, ttl: int
    ) -> LocationState:
        current = await self.state(city_id, slot)
        if current.generation != generation:
            return current
        updated = replace(current, cleared=current.cleared | (1 << node))
        self._states[self._key(city_id, slot)] = (updated, self._clock() + ttl)
        return updated

    async def rotate(self, city_id: str, slot: int, generation: int, ttl: int) -> LocationState:
        current = await self.state(city_id, slot)
        if current.generation != generation:
            return current
        rolled = LocationState(generation=generation + 1, cleared=0)
        self._states[self._key(city_id, slot)] = (rolled, self._clock() + ttl)
        return rolled

    async def arrive(
        self, city_id: str, slot: int, presence: Presence, *, now: int, ttl: int
    ) -> None:
        people = self._people.setdefault(self._key(city_id, slot), {})
        people[presence.character_id] = (presence, now)

    async def leave(self, city_id: str, slot: int, character_id: int) -> None:
        people = self._people.get(self._key(city_id, slot))
        if people is not None:
            people.pop(character_id, None)

    async def others_at(
        self, city_id: str, slot: int, node: int, *, exclude: int, now: int, ttl: int
    ) -> tuple[Presence, ...]:
        people = self._people.get(self._key(city_id, slot), {})
        fresh = [
            (presence, seen)
            for character_id, (presence, seen) in people.items()
            if character_id != exclude and presence.node == node and seen + ttl > now
        ]
        fresh.sort(key=lambda item: item[1], reverse=True)
        return tuple(presence for presence, _ in fresh)


class InMemoryIdempotencyStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._seen: dict[int, float] = {}

    async def seen(self, update_id: int, ttl: int = 300) -> bool:
        """True when this update was already handled.

        Expired entries are swept on the way through, so the dict cannot grow
        without bound in a long-running process.
        """
        now = self._clock()
        expired = [key for key, expires_at in self._seen.items() if expires_at <= now]
        for key in expired:
            del self._seen[key]

        if update_id in self._seen:
            return True
        self._seen[update_id] = now + ttl
        return False
