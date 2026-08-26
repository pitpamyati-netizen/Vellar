"""Кэши в памяти, которые сами следят за сроком.

Срок соблюдается по смыслу, а не фоновым уборщиком: у записи есть отметка
истечения, и на чтении просроченная выбрасывается. Часы подставляются извне,
поэтому тесты двигают время, не засыпая.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from types import MappingProxyType

from mmorpg.domain.entities.location import LocationState, NodeState, Presence
from mmorpg.domain.rules.nodes import refreshed, taken_one


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
    """Общее состояние всех локаций - для игры, работающей без Redis."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._states: dict[str, tuple[dict[int, NodeState], float]] = {}
        self._people: dict[str, dict[int, tuple[Presence, int]]] = {}

    @staticmethod
    def _key(city_id: str, slot: int) -> str:
        return f"loc:{city_id}:{slot}"

    def _live_nodes(self, city_id: str, slot: int) -> dict[int, NodeState]:
        entry = self._states.get(self._key(city_id, slot))
        if entry is None:
            return {}
        nodes, expires_at = entry
        # Локацию, в которую никто не заходил сутками, лучше наполнить заново, чем
        # хранить.
        return nodes if expires_at > self._clock() else {}

    async def state(self, city_id: str, slot: int, *, now: int) -> LocationState:
        nodes = {
            index: refreshed(node, now) for index, node in self._live_nodes(city_id, slot).items()
        }
        return LocationState(nodes=MappingProxyType(nodes))

    async def take(
        self, city_id: str, slot: int, node: int, *, wave: int, size: int, now: int, ttl: int
    ) -> LocationState:
        nodes = dict(self._live_nodes(city_id, slot))
        current = refreshed(nodes.get(node, NodeState()), now)
        # Нажатие, называющее прежнюю волну, принадлежит узлу, который уже перевернулся:
        # это не ошибка, оно просто ничего не меняет.
        nodes[node] = taken_one(current, size, now) if current.wave == wave else current
        self._states[self._key(city_id, slot)] = (nodes, self._clock() + ttl)
        return LocationState(nodes=MappingProxyType(dict(nodes)))

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
        """True, когда это обновление уже обработано.

        Просроченные записи выметаются по дороге, поэтому словарь не может расти без
        предела в долго живущем процессе.
        """
        now = self._clock()
        expired = [key for key, expires_at in self._seen.items() if expires_at <= now]
        for key in expired:
            del self._seen[key]

        if update_id in self._seen:
            return True
        self._seen[update_id] = now + ttl
        return False
