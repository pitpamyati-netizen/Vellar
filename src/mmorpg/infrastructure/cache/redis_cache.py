"""Redis-backed caches.

Everything stored here is short-lived and reconstructible: FSM state, the active
fight, location deltas, shop rolls, update deduplication. Nothing here is a source
of truth, so losing Redis costs a player their current screen, not their character.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from mmorpg.domain.entities.location import LocationState, NodeState, Presence
from mmorpg.domain.rules.nodes import refreshed, taken_one

if TYPE_CHECKING:  # pragma: no cover - typing only
    from redis.asyncio import Redis


def _text(value: object) -> str:
    """Redis hands back bytes unless told otherwise; both are read the same way."""
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _encode(node: NodeState) -> str:
    return f"{node.wave}:{node.taken}:{node.emptied_at}"


def _decode(raw: Mapping[Any, Any], now: int) -> dict[int, NodeState]:
    """The stored nodes, each already carried forward to ``now``."""
    nodes: dict[int, NodeState] = {}
    for field, value in raw.items():
        wave, taken, emptied_at = (int(part) for part in _text(value).split(":"))
        nodes[int(_text(field))] = refreshed(
            NodeState(wave=wave, taken=taken, emptied_at=emptied_at), now
        )
    return nodes


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
    """One location, shared: what is left in its nodes and who is walking in it.

    Two hashes per location - the nodes and the people in them - both with a time
    to live, because a location nobody has visited for days is better refilled
    than kept for ever (``Claude.md``, rule 8: every key expires). The map itself
    is never stored: it is the same map every time.
    """

    def __init__(self, client: Redis) -> None:
        self._client = client

    @staticmethod
    def _state_key(city_id: str, slot: int) -> str:
        return f"loc:{city_id}:{slot}"

    @staticmethod
    def _people_key(city_id: str, slot: int) -> str:
        return f"loc:{city_id}:{slot}:who"

    async def state(self, city_id: str, slot: int, *, now: int) -> LocationState:
        raw = await self._client.hgetall(self._state_key(city_id, slot))
        return LocationState(nodes=MappingProxyType(_decode(raw, now)))

    async def take(
        self, city_id: str, slot: int, node: int, *, wave: int, size: int, now: int, ttl: int
    ) -> LocationState:
        key = self._state_key(city_id, slot)
        nodes = _decode(await self._client.hgetall(key), now)
        current = nodes.get(node, NodeState())
        # A press that names an older wave belongs to a node that has already
        # rolled over: it is not an error, it just changes nothing.
        nodes[node] = taken_one(current, size, now) if current.wave == wave else current
        await self._client.hset(key, str(node), _encode(nodes[node]))
        await self._client.expire(key, max(1, ttl))
        return LocationState(nodes=MappingProxyType(nodes))

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
