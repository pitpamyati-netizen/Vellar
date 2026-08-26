"""Кэши поверх Redis.

Всё, что здесь лежит, короткоживущее и собирается заново: состояние автомата,
начатый бой, изменения в локации, прилавок лавки, отсев повторных обновлений.
Источником истины здесь не является ничто, поэтому потерянный Redis стоит игроку
текущего экрана, а не персонажа.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from mmorpg.domain.entities.location import LocationState, NodeState, Presence
from mmorpg.domain.rules.nodes import refreshed, taken_one

if TYPE_CHECKING:  # pragma: no cover - только для типов
    from redis.asyncio import Redis


def _text(value: object) -> str:
    """Redis отдаёт байты, если не попросить иначе; читаются оба одинаково."""
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _encode(node: NodeState) -> str:
    return f"{node.wave}:{node.taken}:{node.emptied_at}"


def _decode(raw: Mapping[Any, Any], now: int) -> dict[int, NodeState]:
    """Сохранённые узлы, каждый уже переведённый на ``now``."""
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
    """Одна локация, общая: что осталось в её узлах и кто по ней ходит.

    По две хеш-таблицы на локацию - узлы и люди в них, - и обе со сроком, потому что
    локацию, в которую никто не заходил сутками, лучше наполнить заново, чем хранить
    вечно (``Claude.md``, правило 8: у каждого ключа есть срок). Сама карта не
    хранится никогда: она каждый раз одна и та же.
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
        # Нажатие, называющее прежнюю волну, принадлежит узлу, который уже перевернулся:
        # это не ошибка, оно просто ничего не меняет.
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
    """SET NX - и вся реализация: первый записавший выигрывает, остальные повторы."""

    def __init__(self, client: Redis) -> None:
        self._client = client

    async def seen(self, update_id: int, ttl: int = 300) -> bool:
        stored = await self._client.set(f"upd:{update_id}", "1", ex=ttl, nx=True)
        return not bool(stored)
