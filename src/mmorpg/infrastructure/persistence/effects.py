"""Экономические отметки постоянны; экраны и приглашения сохраняют свой срок.

Период уже входит в ключ. Очистка Redis и истечение TTL не могут вернуть
выплаченную награду или обнулить выемку. Запись участвует в операции M01.
"""

from __future__ import annotations

from typing import Any

from mmorpg.application.operations import atomic_action, current_operation
from mmorpg.domain.ports.repositories import StateCache
from mmorpg.infrastructure.cache.memory_operations import memory_cache_action
from mmorpg.infrastructure.persistence.legacy_effects import bridge_legacy_periods
from mmorpg.infrastructure.persistence.operations import (
    ECONOMY_LOCK,
    MemoryOperations,
    OperationPool,
    PostgresOperations,
)

EFFECT_SCOPES = frozenset(
    {
        "digest",
        "descent-paid",
        "battle-credit",
        "guild-taken",
        "guild-items",
        "guild-contract",
        "guild-contract-paid",
        "guild-war-hit",
        "guild-period",
        "guild-contract-snapshot",
        "guild-taken-v2",
        "guild-items-v2",
        "guild-contract-v2",
        "guild-contract-paid-v2",
        "guild-war-hit-v2",
    }
)


def is_durable_effect(key: str) -> bool:
    return key.partition(":")[0] in EFFECT_SCOPES


def _validate(value: str) -> None:
    if not value.isascii() or not value.isdecimal():
        raise ValueError("Economic state must be a non-negative integer")


class PostgresEffectState:
    """Совместимый StateCache: SQL для ценностей, прежний кэш для остального."""

    def __init__(self, pool: Any, temporary: StateCache) -> None:
        self._pool = OperationPool(pool)
        self.operations = PostgresOperations(self._pool)
        self._temporary = temporary

    async def get(self, key: str) -> str | None:
        if not is_durable_effect(key):
            return await self._temporary.get(key)
        stored = await self._pool.fetchval("SELECT value FROM durable_effects WHERE key = $1", key)
        if stored is not None:
            return str(stored)
        # Сохраняет ещё живые отметки предыдущей версии без изменения награды.
        legacy = await self._temporary.get(key)
        if legacy is not None:
            return await self._import(key, legacy)
        return None

    @atomic_action
    async def _import(self, key: str, value: str) -> str:
        _validate(value)
        await self._pool.execute(
            "INSERT INTO durable_effects (key, value) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            key,
            value,
        )
        return str(
            await self._pool.fetchval("SELECT value FROM durable_effects WHERE key = $1", key)
        )

    async def set(self, key: str, value: str, ttl: int) -> None:
        if not is_durable_effect(key):
            await self._temporary.set(key, value, ttl)
            return
        await self._set(key, value)

    @atomic_action
    async def _set(self, key: str, value: str) -> None:
        _validate(value)
        operation = current_operation()
        assert operation is not None
        await self._pool.execute(
            "INSERT INTO durable_effects (key, value, operation_id) VALUES ($1, $2, $3)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value,"
            " operation_id = EXCLUDED.operation_id, recorded_at = now()",
            key,
            value,
            operation.id,
        )

    async def delete(self, key: str) -> None:
        if is_durable_effect(key):
            # Разовые выплаты не должны становиться доступными после уборки кэша.
            raise ValueError("Economic records cannot be deleted through the state cache")
        await self._temporary.delete(key)

    async def import_legacy(self) -> int:
        """До приёма команд перенести все сохранившиеся Redis-отметки.

        Истёкшие до обновления записи восстановить невозможно. В solo прежняя
        память уже потеряна; новые записи впредь постоянны.
        """
        client = getattr(getattr(self._temporary, "_client", None), "raw", None)
        if client is None:
            return 0
        imported = 0
        async with self._pool.raw.acquire() as connection:
            await connection.execute("SELECT pg_advisory_lock($1)", ECONOMY_LOCK)
            try:
                async with connection.transaction():
                    for scope in sorted(EFFECT_SCOPES):
                        async for raw_key in client.scan_iter(match=f"{scope}:*"):
                            key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
                            raw_value = await client.get(raw_key)
                            if raw_value is None:
                                continue
                            value = (
                                raw_value.decode()
                                if isinstance(raw_value, bytes)
                                else str(raw_value)
                            )
                            _validate(value)
                            result = await connection.execute(
                                "INSERT INTO durable_effects (key, value) VALUES ($1, $2)"
                                " ON CONFLICT DO NOTHING",
                                key,
                                value,
                            )
                            imported += int(result == "INSERT 0 1")
                    await bridge_legacy_periods(connection)
            finally:
                if not connection.is_closed():
                    await connection.execute("SELECT pg_advisory_unlock($1)", ECONOMY_LOCK)
        return imported


class MemoryEffectState:
    """Без TTL для экономических отметок; local всё ещё теряет память при выходе."""

    def __init__(self, temporary: StateCache) -> None:
        self.operations = MemoryOperations()
        self._temporary = temporary
        self._effects: dict[str, str] = {}

    @memory_cache_action
    async def get(self, key: str) -> str | None:
        if not is_durable_effect(key):
            return await self._temporary.get(key)
        if key not in self._effects:
            legacy = await self._temporary.get(key)
            if legacy is not None:
                _validate(legacy)
                self._effects[key] = legacy
        return self._effects.get(key)

    @memory_cache_action
    async def set(self, key: str, value: str, ttl: int) -> None:
        if is_durable_effect(key):
            _validate(value)
            self._effects[key] = value
        else:
            await self._temporary.set(key, value, ttl)

    @memory_cache_action
    async def delete(self, key: str) -> None:
        if is_durable_effect(key):
            raise ValueError("Economic records cannot be deleted through the state cache")
        await self._temporary.delete(key)
