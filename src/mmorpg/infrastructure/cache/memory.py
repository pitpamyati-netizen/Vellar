"""In-memory caches with TTL bookkeeping.

The TTL is honoured logically rather than by a background sweeper: entries carry
an expiry stamp and are dropped on read. The clock is injected, so tests advance
time without sleeping.
"""

from __future__ import annotations

import time
from collections.abc import Callable


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


class InMemoryLocationDeltaCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._masks: dict[str, tuple[int, float]] = {}

    @staticmethod
    def _key(character_id: int, city_id: str, slot: int, cycle: int) -> str:
        return f"loc:{city_id}:{slot}:{cycle}:{character_id}"

    async def get_mask(self, character_id: int, city_id: str, slot: int, cycle: int) -> int:
        entry = self._masks.get(self._key(character_id, city_id, slot, cycle))
        if entry is None:
            return 0
        mask, expires_at = entry
        if expires_at <= self._clock():
            return 0
        return mask

    async def mark_cleared(
        self, character_id: int, city_id: str, slot: int, cycle: int, node: int, ttl: int
    ) -> int:
        key = self._key(character_id, city_id, slot, cycle)
        current = await self.get_mask(character_id, city_id, slot, cycle)
        updated = current | (1 << node)
        self._masks[key] = (updated, self._clock() + ttl)
        return updated

    async def reset(self, character_id: int, city_id: str, slot: int, cycle: int) -> None:
        self._masks.pop(self._key(character_id, city_id, slot, cycle), None)


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
