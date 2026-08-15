"""Redis-backed caches.

Everything stored here is short-lived and reconstructible: FSM state, the active
fight, location deltas, shop rolls, update deduplication. Nothing here is a source
of truth, so losing Redis costs a player their current screen, not their character.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from mmorpg.domain.entities.location import LocationState, Presence

if TYPE_CHECKING:  # pragma: no cover - typing only
    from redis.asyncio import Redis


def _text(value: object) -> str:
    """Redis hands back bytes unless told otherwise; both are read the same way."""
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


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


class RedisLocationStateCache:
    """One location, shared: which map is standing, what is cleared, who is there.

    Two hashes per location - the state and the people in it - both with a time to
    live, because a location nobody has visited for days is better re-rolled than
    kept for ever (``Claude.md``, rule 8: every key expires).
    """

    def __init__(self, client: Redis) -> None:
        self._client = client

    @staticmethod
    def _state_key(city_id: str, slot: int) -> str:
        return f"loc:{city_id}:{slot}"

    @staticmethod
    def _people_key(city_id: str, slot: int) -> str:
        return f"loc:{city_id}:{slot}:who"

    async def state(self, city_id: str, slot: int) -> LocationState:
        raw = await self._client.hgetall(self._state_key(city_id, slot))
        if not raw:
            return LocationState()
        values = {_text(key): _text(value) for key, value in raw.items()}
        return LocationState(
            generation=int(values.get("generation", 0)),
            cleared=int(values.get("cleared", 0)),
        )

    async def mark_cleared(
        self, city_id: str, slot: int, generation: int, node: int, ttl: int
    ) -> LocationState:
        current = await self.state(city_id, slot)
        if current.generation != generation:
            # The map rolled over while this fight was going on: the node that was
            # just emptied belongs to a location that no longer exists.
            return current
        updated = LocationState(generation=generation, cleared=current.cleared | (1 << node))
        await self._write(city_id, slot, updated, ttl)
        return updated

    async def rotate(self, city_id: str, slot: int, generation: int, ttl: int) -> LocationState:
        current = await self.state(city_id, slot)
        if current.generation != generation:
            return current
        rolled = LocationState(generation=generation + 1, cleared=0)
        await self._write(city_id, slot, rolled, ttl)
        return rolled

    async def _write(self, city_id: str, slot: int, state: LocationState, ttl: int) -> None:
        key = self._state_key(city_id, slot)
        await self._client.hset(
            key, mapping={"generation": state.generation, "cleared": state.cleared}
        )
        await self._client.expire(key, max(1, ttl))

    async def arrive(
        self, city_id: str, slot: int, presence: Presence, *, now: int, ttl: int
    ) -> None:
        key = self._people_key(city_id, slot)
        value = json.dumps(
            {
                "node": presence.node,
                "name": presence.name,
                "level": presence.level,
                "seen": now,
            },
            ensure_ascii=False,
        )
        await self._client.hset(key, str(presence.character_id), value)
        await self._client.expire(key, max(1, ttl * 4))

    async def leave(self, city_id: str, slot: int, character_id: int) -> None:
        await self._client.hdel(self._people_key(city_id, slot), str(character_id))

    async def others_at(
        self, city_id: str, slot: int, node: int, *, exclude: int, now: int, ttl: int
    ) -> tuple[Presence, ...]:
        key = self._people_key(city_id, slot)
        raw = await self._client.hgetall(key)
        seen: list[tuple[Presence, int]] = []
        stale: list[str] = []
        for field, value in raw.items():
            character_id = int(_text(field))
            entry = json.loads(_text(value))
            if int(entry.get("seen", 0)) + ttl <= now:
                stale.append(_text(field))
                continue
            if character_id == exclude or int(entry.get("node", -1)) != node:
                continue
            seen.append(
                (
                    Presence(
                        character_id=character_id,
                        name=str(entry.get("name", "")),
                        level=int(entry.get("level", 1)),
                        node=int(entry.get("node", 0)),
                    ),
                    int(entry.get("seen", 0)),
                )
            )
        if stale:
            await self._client.hdel(key, *stale)
        seen.sort(key=lambda item: item[1], reverse=True)
        return tuple(presence for presence, _ in seen)


class RedisIdempotencyStore:
    """SET NX is the whole implementation: the first writer wins, the rest are dupes."""

    def __init__(self, client: Redis) -> None:
        self._client = client

    async def seen(self, update_id: int, ttl: int = 300) -> bool:
        stored = await self._client.set(f"upd:{update_id}", "1", ex=ttl, nx=True)
        return not bool(stored)
