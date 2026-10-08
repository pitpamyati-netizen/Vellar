"""SQL хранит бой и экран в той же операции, что ценности и ответы."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any, Literal, cast

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

from mmorpg.application.operations import atomic_action, current_operation
from mmorpg.domain.ports.repositories import StateCache
from mmorpg.infrastructure.persistence.operations import (
    ECONOMY_LOCK,
    OperationPool,
    PostgresOperations,
)


class StaleBattleError(RuntimeError):
    """Сохранение боя рассчитано по прежнему ходу."""


class GameplayValues:
    def __init__(self, pool: Any) -> None:
        self._pool = OperationPool(pool)
        self.operations = PostgresOperations(self._pool)

    async def get(self, key: str) -> str | None:
        value = await self._pool.fetchval("SELECT value FROM gameplay_state WHERE key=$1", key)
        return str(value) if value is not None else None

    async def set(self, key: str, value: str, **kwargs: Any) -> bool:
        if kwargs.get("nx"):
            result = await self._pool.execute(
                "INSERT INTO gameplay_state(key,value) VALUES($1,$2) ON CONFLICT(key)"
                " DO UPDATE SET value=EXCLUDED.value,revision=gameplay_state.revision+1,"
                " updated_at=now() WHERE gameplay_state.value=''",
                key,
                value,
            )
            return bool(result == "INSERT 0 1")
        await self._pool.execute(
            "INSERT INTO gameplay_state(key,value) VALUES($1,$2) ON CONFLICT(key)"
            " DO UPDATE SET value=EXCLUDED.value, revision=gameplay_state.revision+1,"
            " updated_at=now()",
            key,
            value,
        )
        return True

    async def delete(self, key: str, *keys: str) -> None:
        # Запись остаётся: повторный импорт не возвращает прежние узлы/захват.
        for one in (key, *keys):
            await self.set(one, "")

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(json.loads(await self.get(key) or "{}"))

    async def hget(self, key: str, field: str) -> str | None:
        return (await self.hgetall(key)).get(field)

    async def hset(self, key: str, field: str, value: str) -> None:
        data = await self.hgetall(key)
        data[field] = value
        await self.set(key, json.dumps(data, ensure_ascii=False))

    async def hsetnx(self, key: str, field: str, value: str) -> bool:
        data = await self.hgetall(key)
        if field in data:
            return False
        data[field] = value
        await self.set(key, json.dumps(data, ensure_ascii=False))
        return True

    async def hdel(self, key: str, *fields: str) -> None:
        data = await self.hgetall(key)
        for field in fields:
            data.pop(field, None)
        await self.set(key, json.dumps(data, ensure_ascii=False))

    async def expire(self, key: str, ttl: int) -> None:
        # Поколение карты, владение и начатый бой не истекают по сроку кэша.
        pass


class PostgresGameplayState:
    durable = True

    def __init__(self, pool: Any, temporary: StateCache) -> None:
        self._values = GameplayValues(pool)
        self.operations = self._values.operations
        self._temporary = temporary

    @staticmethod
    def persistent(key: str) -> bool:
        return key.partition(":")[0] in {
            "battle",
            "battle-of",
            "battle-content",
            "screen-reading",
            "recruitment",
            "social",
        }

    async def get(self, key: str) -> str | None:
        # Чтение снимка не создаёт собственную запись результата команды.
        # Все игровые изменения уже находятся во внешней операции M01/M02.
        if not self.persistent(key):
            return await self._temporary.get(key)
        value = await self._values.get(key)
        if value is None:
            value = await self._temporary.get(key)
            if value is not None:
                await self._values.set(key, value, nx=True)
        return value

    @atomic_action
    async def set(self, key: str, value: str, ttl: int) -> None:
        if not self.persistent(key):
            await self._temporary.set(key, value, ttl)
            return
        if key.startswith("battle:"):
            saved = await self._values.get(key)
            expected = int(json.loads(value).get("version", 1)) - 1
            if saved is not None and (
                not saved or int(json.loads(saved).get("version", 0)) != expected
            ):
                raise StaleBattleError(key)
            if saved is None and expected != 0:
                raise StaleBattleError(key)
        await self._values.set(key, value)

    @atomic_action
    async def delete(self, key: str) -> None:
        if self.persistent(key):
            # Пустая постоянная отметка запрещает воскресить старый Redis-замок.
            await self._values.set(key, "")
        else:
            await self._temporary.delete(key)

    async def import_legacy(self, client: Any) -> int:
        if client is None:
            return 0
        count = 0
        async with self._values._pool.raw.acquire() as connection:
            await connection.execute("SELECT pg_advisory_lock($1)", ECONOMY_LOCK)
            try:
                async with connection.transaction():
                    for pattern in ("battle:*", "battle-of:*", "fsm:*", "loc:*"):
                        async for raw_key in client.scan_iter(match=pattern):
                            key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
                            kind = await client.type(raw_key)
                            kind = kind.decode() if isinstance(kind, bytes) else kind
                            if kind == "hash":
                                raw = await client.hgetall(raw_key)
                                value = json.dumps(
                                    {
                                        k.decode() if isinstance(k, bytes) else str(k): v.decode()
                                        if isinstance(v, bytes)
                                        else str(v)
                                        for k, v in raw.items()
                                    },
                                    ensure_ascii=False,
                                )
                            elif kind == "string":
                                raw = await client.get(raw_key)
                                if raw is None:
                                    continue
                                value = raw.decode() if isinstance(raw, bytes) else str(raw)
                            else:
                                continue
                            # Строки FSM получают тот же ключ, что новый Storage.
                            result = await connection.execute(
                                "INSERT INTO gameplay_state(key,value) VALUES($1,$2)"
                                " ON CONFLICT DO NOTHING",
                                key,
                                value,
                            )
                            count += int(result == "INSERT 0 1")
                            if result == "INSERT 0 1" and key.endswith(":roamer"):
                                ttl = await client.ttl(raw_key)
                                if ttl > 0:
                                    await connection.execute(
                                        "INSERT INTO gameplay_state(key,value) VALUES($1,$2)"
                                        " ON CONFLICT DO NOTHING",
                                        key + ":expires",
                                        str(int(time.time()) + ttl),
                                    )
            finally:
                await connection.execute("SELECT pg_advisory_unlock($1)", ECONOMY_LOCK)
        return count


class PostgresStorage(BaseStorage):
    def __init__(self, pool: Any, legacy: BaseStorage | None = None) -> None:
        self._values = GameplayValues(pool)
        self.operations = self._values.operations
        self._legacy = legacy

    @staticmethod
    def key_of(key: StorageKey, part: str) -> str:
        # DefaultKeyBuilder Redis не включал bot_id. Новый ключ изолирует ботов.
        return "session:" + json.dumps(
            [
                key.bot_id,
                key.chat_id,
                key.user_id,
                key.thread_id,
                key.business_connection_id,
                key.destiny,
                part,
            ],
            separators=(",", ":"),
        )

    async def _get(self, key: StorageKey, part: Literal["state", "data"]) -> str | None:
        saved = await self._values.get(self.key_of(key, part))
        if saved is not None:
            return saved
        from aiogram.fsm.storage.base import DefaultKeyBuilder

        old = await self._values.get(DefaultKeyBuilder().build(key, part))
        if old is not None:
            return old
        if self._legacy is None:
            return None
        if part == "state":
            return await self._legacy.get_state(key)
        return json.dumps(await self._legacy.get_data(key), ensure_ascii=False)

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        async def write() -> None:
            await self._values.set(
                self.key_of(key, "state"),
                (state.state if isinstance(state, State) else state) or "",
            )

        await self._write(write)

    async def get_state(self, key: StorageKey) -> str | None:
        return await self._get(key, "state") or None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        async def write() -> None:
            await self._values.set(
                self.key_of(key, "data"), json.dumps(dict(data), ensure_ascii=False)
            )

        await self._write(write)

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        return dict(json.loads(await self._get(key, "data") or "{}"))

    async def update_data(self, key: StorageKey, data: Mapping[str, Any]) -> dict[str, Any]:
        async def write() -> dict[str, Any]:
            updated = (await self.get_data(key)) | dict(data)
            await self.set_data(key, updated)
            return updated

        return cast(dict[str, Any], await self._write(write))

    async def _write(self, action: Any) -> Any:
        if current_operation() is not None:
            return await action()
        return await self._outside(action)

    @atomic_action
    async def _outside(self, action: Any) -> Any:
        return await action()

    async def close(self) -> None:
        pass
