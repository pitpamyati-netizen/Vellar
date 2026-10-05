"""Единая транзакция, журнал и безопасный повтор экономического действия."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from typing import Any

from pydantic import TypeAdapter

from mmorpg.application.operations import (
    Operation,
    OperationCommittedError,
    OperationKeyConflictError,
    OperationOutcomeUnknownError,
    _current,
    current_operation,
    json_fallback,
)
from mmorpg.infrastructure.cache.memory_operations import apply_memory_changes
from mmorpg.infrastructure.cache.operations import apply_changes

# Один замок берётся раньше любых строк. Нет обратного порядка участников и
# взаимной блокировки передачи A -> B с передачей B -> A. Укрупнённую границу
# предстоит измерить на полной нагрузке M10; Telegram не держит этот замок.
ECONOMY_LOCK = 0x56454C4C4152
_connection: ContextVar[tuple[object, Any] | None] = ContextVar(
    "operation_connection", default=None
)


class OperationPool:
    """Все адаптеры одной операции используют одно соединение, включая acquire."""

    def __init__(self, pool: Any) -> None:
        self.raw: Any = pool.raw if isinstance(pool, OperationPool) else pool

    def active(self) -> Any:
        bound = _connection.get()
        if bound is not None and bound[0] is self.raw and current_operation() is not None:
            return bound[1]
        return self.raw

    async def fetch(self, query: str, *args: Any) -> Any:
        return await self.active().fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> Any:
        return await self.active().fetchrow(query, *args)

    async def fetchval(self, query: str, *args: Any) -> Any:
        return await self.active().fetchval(query, *args)

    async def execute(self, query: str, *args: Any) -> Any:
        return await self.active().execute(query, *args)

    @asynccontextmanager
    async def acquire(self) -> Any:
        active = self.active()
        if active is not self.raw:
            yield active
        else:
            async with self.raw.acquire() as connection:
                yield connection


async def _deliver(operation: Operation) -> None:
    from mmorpg import economy_log

    for flow, amount, character_id, detail in operation.gold_events:
        economy_log.logger.info(
            "gold_flow", flow=flow, amount=amount, character_id=character_id, detail=detail
        )
    for action in operation.after_commit:
        try:
            await action()
        except Exception as error:
            raise OperationCommittedError(operation.id) from error


class PostgresOperations:
    def __init__(self, pool: OperationPool) -> None:
        self.pool = pool

    async def _recover(self, connection: Any) -> None:
        pending = await connection.fetch(
            "SELECT id, cache_changes FROM economic_operations"
            " WHERE completed AND NOT cache_applied ORDER BY created_at, id"
        )
        cache = getattr(self.pool.raw, "economic_cache", None)
        if pending and cache is None:
            raise OperationCommittedError("Redis is required to finish a saved operation")
        for row in pending:
            await apply_changes(cache, json.loads(row["cache_changes"]))
            await connection.execute(
                "UPDATE economic_operations SET cache_applied = true,"
                " cache_changes = '[]'::jsonb WHERE id = $1",
                row["id"],
            )

    async def recover(self) -> None:
        async with self.pool.raw.acquire() as connection:
            await connection.execute("SELECT pg_advisory_lock($1)", ECONOMY_LOCK)
            try:
                await self._recover(connection)
            finally:
                if not connection.is_closed():
                    await connection.execute("SELECT pg_advisory_unlock($1)", ECONOMY_LOCK)

    async def run[R](
        self,
        action: Callable[[], Awaitable[R]],
        *,
        operation: Operation,
        fingerprint: str,
        codec: TypeAdapter[R],
        participants: tuple[object, ...],
    ) -> R:
        try:
            return await self._run(
                action,
                operation=operation,
                fingerprint=fingerprint,
                codec=codec,
                participants=participants,
            )
        except OperationCommittedError:
            raise
        except Exception as error:
            if operation.sql_committed:
                raise OperationCommittedError(operation.id) from error
            if not operation.commit_attempted:
                raise
            # Ответ COMMIT мог пропасть уже после сохранения. Старое соединение
            # отпущено; на новом проверяем постоянный id, не повторяя списание.
            try:
                async with self.pool.raw.acquire() as connection:
                    await connection.execute("SELECT pg_advisory_lock($1)", ECONOMY_LOCK)
                    try:
                        await self._recover(connection)
                        row = await connection.fetchrow(
                            "SELECT result FROM economic_operations WHERE id = $1", operation.id
                        )
                        if row is not None:
                            operation.sql_committed = True
                            result = codec.validate_json(row["result"])
                            apply_memory_changes(operation)
                    finally:
                        if not connection.is_closed():
                            await connection.execute("SELECT pg_advisory_unlock($1)", ECONOMY_LOCK)
            except Exception as recovery_error:
                exception = (
                    OperationCommittedError
                    if operation.sql_committed
                    else OperationOutcomeUnknownError
                )
                raise exception(operation.id) from recovery_error
            if row is None:
                raise error
            await _deliver(operation)
            return result

    async def _run[R](
        self,
        action: Callable[[], Awaitable[R]],
        *,
        operation: Operation,
        fingerprint: str,
        codec: TypeAdapter[R],
        participants: tuple[object, ...],
    ) -> R:
        for participant in participants:
            other = getattr(participant, "operations", None)
            if not isinstance(other, PostgresOperations) or other.pool.raw is not self.pool.raw:
                raise ValueError("An operation requires repositories from one PostgreSQL pool")
        async with self.pool.raw.acquire() as connection:
            await connection.execute("SELECT pg_advisory_lock($1)", ECONOMY_LOCK)
            try:
                # Незаконченный перенос кэша завершается раньше нового чтения боя.
                await self._recover(connection)
                async with connection.transaction():
                    existing = await connection.fetchrow(
                        "SELECT fingerprint, result FROM economic_operations WHERE id = $1",
                        operation.id,
                    )
                    if existing is not None:
                        if existing["fingerprint"] != fingerprint:
                            raise OperationKeyConflictError(operation.id)
                        operation.replayed = True
                        operation.sql_committed = True
                        return codec.validate_json(existing["result"])
                    await connection.execute(
                        "INSERT INTO economic_operations (id, kind, fingerprint)"
                        " VALUES ($1, $2, $3)",
                        operation.id,
                        operation.kind,
                        fingerprint,
                    )
                    await connection.execute(
                        "SELECT set_config('vellar.operation_id', $1, true)", operation.id
                    )
                    token = _current.set(operation)
                    connection_token = _connection.set((self.pool.raw, connection))
                    try:
                        result = await action()
                        changes = []
                        for staged in operation.redis_changes.values():
                            if staged.client is not getattr(self.pool.raw, "economic_cache", None):
                                raise ValueError(
                                    "The operation Redis must be configured on the pool"
                                )
                            changes.extend(await staged.manifest())
                        for flow, amount, character_id, detail in operation.gold_events:
                            await connection.execute(
                                "INSERT INTO gold_flow"
                                " (at, flow, amount, character_id, detail, operation_id)"
                                " VALUES (extract(epoch FROM now())::bigint, $1, $2, $3, $4, $5)",
                                flow,
                                amount,
                                character_id,
                                detail,
                                operation.id,
                            )
                        await connection.execute(
                            "UPDATE economic_operations SET result = $2::jsonb, completed = true,"
                            " cache_changes = $3::jsonb, cache_applied = $4 WHERE id = $1",
                            operation.id,
                            codec.dump_json(result, fallback=json_fallback).decode(),
                            json.dumps(changes),
                            not changes,
                        )
                        operation.commit_attempted = True
                    finally:
                        _connection.reset(connection_token)
                        _current.reset(token)
                operation.sql_committed = True
                # SQL уже сохранён; незавершённый перенос остаётся в постоянном
                # журнале. Новый запрос и новый процесс сначала доведут его.
                try:
                    await self._recover(connection)
                    apply_memory_changes(operation)
                except Exception as error:
                    raise OperationCommittedError(operation.id) from error
            finally:
                try:
                    if not connection.is_closed():
                        await connection.execute("SELECT pg_advisory_unlock($1)", ECONOMY_LOCK)
                finally:
                    for staged in operation.redis_changes.values():
                        with suppress(Exception):
                            await staged.cleanup()
        # Успех и ответы видны лишь после COMMIT. Обрыв на COMMIT не повторяет
        # транзакцию: повтор с тем же id сначала проверит постоянную запись.
        await _deliver(operation)
        return result


def _copy_containers(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _copy_containers(one) for key, one in value.items()}
    if isinstance(value, list):
        return [_copy_containers(one) for one in value]
    if isinstance(value, set):
        return value.copy()
    return value


def _snapshot(repository: object) -> dict[str, Any]:
    snapshot = {}
    for name, value in vars(repository).items():
        if name == "operations":
            continue
        if isinstance(value, dict | list | set):
            snapshot[name] = _copy_containers(value)
        else:
            snapshot[name] = value
    return snapshot


class MemoryOperations:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.completed: dict[str, tuple[str, bytes]] = {}

    async def run[R](
        self,
        action: Callable[[], Awaitable[R]],
        *,
        operation: Operation,
        fingerprint: str,
        codec: TypeAdapter[R],
        participants: tuple[object, ...],
    ) -> R:
        async with self.lock:
            existing = self.completed.get(operation.id)
            if existing is not None:
                if existing[0] != fingerprint:
                    raise OperationKeyConflictError(operation.id)
                operation.replayed = True
                return codec.validate_json(existing[1])
            snapshots = [(one, _snapshot(one)) for one in participants]
            token = _current.set(operation)
            try:
                result = await action()
                encoded = codec.dump_json(result, fallback=json_fallback)
            except BaseException:
                for one, snapshot in snapshots:
                    for name, value in snapshot.items():
                        setattr(one, name, value)
                raise
            finally:
                _current.reset(token)
            self.completed[operation.id] = (fingerprint, encoded)
            for staged in operation.redis_changes.values():
                await apply_changes(staged.client, await staged.manifest())
                await staged.cleanup()
            apply_memory_changes(operation)
        await _deliver(operation)
        return result
