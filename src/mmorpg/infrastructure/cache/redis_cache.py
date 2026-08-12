"""Redis-backed caches.

Everything stored here is short-lived and reconstructible: FSM state, the active
fight, location deltas, shop rolls, update deduplication. Nothing here is a source
of truth, so losing Redis costs a player their current screen, not their character.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from redis.asyncio import Redis


class RedisStateCache:
    def __init__(self, client: Redis) -> None:
        self._client = client

    async def get(self, key: str) -> str | None:
        value = await self._client.get(key)
        if value is None:
            return None
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    async def set(self, key: str, value: str, ttl: int) -> None:
        await self._client.set(key, value, ex=ttl)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)


class RedisLocationDeltaCache:
    """Cleared nodes as a bitmask that expires with the world cycle."""

    def __init__(self, client: Redis) -> None:
        self._client = client

    @staticmethod
    def _key(character_id: int, city_id: str, slot: int, cycle: int) -> str:
        return f"loc:{city_id}:{slot}:{cycle}:{character_id}"

    async def get_mask(self, character_id: int, city_id: str, slot: int, cycle: int) -> int:
        value = await self._client.get(self._key(character_id, city_id, slot, cycle))
        return int(value) if value is not None else 0

    async def mark_cleared(
        self, character_id: int, city_id: str, slot: int, cycle: int, node: int, ttl: int
    ) -> int:
        key = self._key(character_id, city_id, slot, cycle)
        current = await self.get_mask(character_id, city_id, slot, cycle)
        updated = current | (1 << node)
        await self._client.set(key, updated, ex=max(1, ttl))
        return updated

    async def reset(self, character_id: int, city_id: str, slot: int, cycle: int) -> None:
        await self._client.delete(self._key(character_id, city_id, slot, cycle))


class RedisIdempotencyStore:
    """SET NX is the whole implementation: the first writer wins, the rest are dupes."""

    def __init__(self, client: Redis) -> None:
        self._client = client

    async def seen(self, update_id: int, ttl: int = 300) -> bool:
        stored = await self._client.set(f"upd:{update_id}", "1", ex=ttl, nx=True)
        return not bool(stored)
