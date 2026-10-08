"""Постоянное поколение округи и владение встречей под общей транзакцией."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from typing import Any

from mmorpg.application.operations import atomic_action
from mmorpg.domain.entities.location import Engagement, LocationState, Roamer
from mmorpg.domain.rules.nodes import location_epoch
from mmorpg.infrastructure.cache.redis_cache import RedisLocationStateCache
from mmorpg.infrastructure.persistence.gameplay import GameplayValues


class PostgresLocationState(RedisLocationStateCache):
    def __init__(self, pool: Any) -> None:
        self._client: Any = GameplayValues(pool)
        self.operations = self._client.operations

    async def state(self, city_id: str, slot: int, *, now: int) -> LocationState:
        state = await super().state(city_id, slot, now=now)
        offset = await self._client.get(self._state_key(city_id, slot) + ":generation")
        return replace(state, epoch_offset=int(offset or 0))

    @atomic_action
    async def engage(self, *args: Any, **kwargs: Any) -> Engagement | None:
        # Срок замка определяется завершением боя, а не последним нажатием.
        kwargs["ttl"] = 2**62
        return await super().engage(*args, **kwargs)

    async def engaged_at(self, *args: Any, **kwargs: Any) -> tuple[Engagement, ...]:
        kwargs["ttl"] = 2**62
        return await super().engaged_at(*args, **kwargs)

    @atomic_action
    async def disengage(
        self,
        city_id: str,
        slot: int,
        node: int,
        *,
        wave: int,
        place: int,
        battle_id: str = "",
        epoch: int = 0,
    ) -> None:
        key = self._fights_key(city_id, slot)
        field = self._fight_field(node, wave, place, epoch)
        raw = await self._client.hget(key, field)
        if raw and (not battle_id or json.loads(raw)["battle"] == battle_id):
            await self._client.hdel(key, field)

    async def _claim(self, city_id: str, slot: int) -> dict[str, Any]:
        raw = await self._client.get(self._hold_key(city_id, slot))
        if not raw:
            return {}
        # Совместимость с импортированным замком прежней версии.
        decoded = json.loads(raw)
        return decoded if isinstance(decoded, dict) else {"holder": int(decoded), "encounter": ""}

    async def _holder(self, city_id: str, slot: int) -> int:
        return int((await self._claim(city_id, slot)).get("holder", 0))

    @staticmethod
    def _owns(held: dict[str, Any], encounter: str) -> bool:
        if not encounter:
            return True
        if held.get("encounter"):
            return bool(held["encounter"] == encounter)
        # Старый замок сохранял лишь владельца. Чужой старый бой не снимает его.
        return str(held.get("holder")) == encounter.partition(":")[0]

    @atomic_action
    async def claim_roamer(
        self,
        city_id: str,
        slot: int,
        character_id: int,
        *,
        ttl: int,
        encounter: str = "",
        stamp: int = 0,
    ) -> bool:
        if await self.roamer(city_id, slot, now=int(time.time())) is None:
            return False
        raw = await self._client.get(self._roamer_key(city_id, slot))
        if raw is None or (stamp and int(json.loads(raw)["stamp"]) != stamp):
            return False
        held = await self._claim(city_id, slot)
        if held and (
            held["holder"] != character_id or held.get("encounter", "") not in ("", encounter)
        ):
            return False
        await self._client.set(
            self._hold_key(city_id, slot),
            json.dumps(
                {
                    "holder": character_id,
                    "encounter": encounter,
                    "stamp": stamp,
                }
            ),
        )
        return True

    @atomic_action
    async def hold_roamer(
        self,
        city_id: str,
        slot: int,
        character_id: int,
        *,
        ttl: int,
        encounter: str = "",
        stamp: int = 0,
    ) -> None:
        await self.claim_roamer(
            city_id, slot, character_id, ttl=ttl, encounter=encounter, stamp=stamp
        )

    @atomic_action
    async def release_roamer(self, city_id: str, slot: int, *, encounter: str = "") -> None:
        held = await self._claim(city_id, slot)
        if held and self._owns(held, encounter):
            await self._client.delete(self._hold_key(city_id, slot))

    @atomic_action
    async def clear_roamer(self, city_id: str, slot: int, *, encounter: str = "") -> None:
        held = await self._claim(city_id, slot)
        if encounter and (not held or not self._owns(held, encounter)):
            return
        await super().clear_roamer(city_id, slot)

    @atomic_action
    async def spawn_roamer(self, city_id: str, slot: int, roamer: Roamer, *, ttl: int) -> Roamer:
        existing = await self.roamer(city_id, slot, now=int(time.time()))
        if existing is not None:
            return existing
        result = await super().spawn_roamer(city_id, slot, roamer, ttl=ttl)
        await self._client.set(
            self._roamer_key(city_id, slot) + ":expires", str(int(time.time()) + ttl)
        )
        return result

    @atomic_action
    async def roamer(self, city_id: str, slot: int, *, now: int) -> Roamer | None:
        expiry = await self._client.get(self._roamer_key(city_id, slot) + ":expires")
        if expiry and int(expiry) <= now and not await self._holder(city_id, slot):
            await self._client.delete(self._roamer_key(city_id, slot))
            return None
        return await super().roamer(city_id, slot, now=now)

    @atomic_action
    async def others_at(self, *args: Any, **kwargs: Any) -> Any:
        return await super().others_at(*args, **kwargs)

    @atomic_action
    async def take(
        self,
        city_id: str,
        slot: int,
        node: int,
        *,
        wave: int,
        size: int,
        now: int,
        ttl: int,
        place: int = -1,
    ) -> LocationState:
        await super().take(city_id, slot, node, wave=wave, size=size, now=now, ttl=ttl, place=place)
        return await self.state(city_id, slot, now=now)

    @atomic_action
    async def arrive(self, *args: Any, **kwargs: Any) -> None:
        await super().arrive(*args, **kwargs)

    @atomic_action
    async def leave(self, *args: Any, **kwargs: Any) -> None:
        await super().leave(*args, **kwargs)

    @atomic_action
    async def reset(self, city_id: str, slot: int) -> None:
        # Узлы обновляются; действующие встречи сохраняют собственное владение.
        current = await self.state(city_id, slot, now=int(time.time()))
        await self._client.set(
            self._state_key(city_id, slot) + ":generation", str(location_epoch(current) + 1)
        )
        await self._client.delete(self._state_key(city_id, slot))
