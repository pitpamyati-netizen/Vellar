"""PostgreSQL: сообщения входят в операцию, отправка не держит её соединение."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from mmorpg.application.delivery import (
    Delivery,
    DeliveryKeyConflictError,
    DeliveryPolicy,
    DeliveryPriority,
    DeliveryStatus,
)
from mmorpg.infrastructure.persistence.operations import OperationPool


def _delivery(row: Any) -> Delivery:
    payload = json.loads(row["payload"])
    return Delivery(
        key=row["key"],
        sequence=row["sequence"],
        bot_id=row["bot_id"],
        chat_id=payload["chat_id"],
        payload=payload,
        priority=DeliveryPriority(row["priority"]),
        status=DeliveryStatus(row["status"]),
        attempts=row["attempts"],
        available_at=row["available_at"],
        lease_until=row["lease_until"],
        lease_token=row["lease_token"],
        response=json.loads(row["response"]) if row["response"] is not None else None,
        last_error=row["last_error"],
        operation_id=row["operation_id"],
    )


async def _lock(connection: Any, bot_id: int) -> None:
    await connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('vellar:delivery:' || $1::text, 0))",
        str(bot_id),
    )


class PostgresDeliveryQueue:
    def __init__(self, pool: Any) -> None:
        self.pool = OperationPool(pool)

    async def enqueue(
        self,
        *,
        key: str,
        bot_id: int,
        chat_id: int | str,
        payload: dict[str, Any],
        priority: DeliveryPriority = DeliveryPriority.REPLY,
        operation_id: str | None = None,
    ) -> Delivery:
        if payload.get("chat_id") != chat_id:
            raise ValueError("Delivery destination must match its payload")
        # OperationPool.acquire возвращает соединение уже начатой команды.
        # Вложенная transaction — savepoint, SQL и ответ коммитятся вместе.
        async with self.pool.acquire() as connection, connection.transaction():
            await _lock(connection, bot_id)
            row = await connection.fetchrow(
                "INSERT INTO message_delivery"
                " (key, bot_id, chat_id, payload, priority, operation_id)"
                " VALUES($1, $2, $3, $4::jsonb, $5, $6) ON CONFLICT(key) DO NOTHING RETURNING *",
                key,
                bot_id,
                str(chat_id),
                json.dumps(payload, ensure_ascii=False),
                int(priority),
                operation_id,
            )
            if row is None:
                row = await connection.fetchrow("SELECT * FROM message_delivery WHERE key=$1", key)
                existing = _delivery(row)
                if (
                    existing.bot_id != bot_id
                    or existing.chat_id != chat_id
                    or existing.payload != payload
                    or existing.priority != priority
                    or existing.operation_id != operation_id
                ):
                    raise DeliveryKeyConflictError(key)
                return existing
            return _delivery(row)

    async def get(self, key: str) -> Delivery | None:
        row = await self.pool.fetchrow("SELECT * FROM message_delivery WHERE key=$1", key)
        return _delivery(row) if row is not None else None

    async def pending_for_operation(
        self, operation_id: str, bot_id: int, chat_id: int | str
    ) -> bool:
        return bool(
            await self.pool.fetchval(
                "SELECT EXISTS(SELECT 1 FROM message_delivery"
                " WHERE operation_id=$1 AND bot_id=$2 AND chat_id=$3"
                " AND status IN ('queued', 'sending'))",
                operation_id,
                bot_id,
                str(chat_id),
            )
        )

    async def claim(self, bot_id: int, policy: DeliveryPolicy) -> Delivery | None:
        async with self.pool.raw.acquire() as connection, connection.transaction():
            await _lock(connection, bot_id)
            now = await connection.fetchval("SELECT extract(epoch FROM clock_timestamp())::float8")
            horizon = max(1.0, policy.private_window, policy.group_window)
            await connection.execute(
                "DELETE FROM message_delivery_attempts WHERE bot_id=$1 AND at<=$2",
                bot_id,
                now - horizon,
            )
            cooldown = await connection.fetchval(
                "SELECT cooldown_until FROM message_delivery_budget WHERE bot_id=$1", bot_id
            )
            count = await connection.fetchval(
                "SELECT count(*) FROM message_delivery_attempts WHERE bot_id=$1 AND at>$2",
                bot_id,
                now - 1.0,
            )
            if (cooldown is not None and cooldown > now) or count >= policy.sends_per_second:
                return None
            row = await connection.fetchrow(
                "SELECT d.* FROM message_delivery d"
                " WHERE d.bot_id=$1 AND d.status IN ('queued', 'sending')"
                " AND d.available_at<=$2 AND d.lease_until<=$2"
                " AND (d.operation_id IS NULL OR EXISTS(SELECT 1 FROM economic_operations o"
                " WHERE o.id=d.operation_id AND o.completed AND o.cache_applied))"
                " AND NOT EXISTS(SELECT 1 FROM message_delivery older"
                " WHERE older.bot_id=d.bot_id AND older.chat_id=d.chat_id"
                " AND older.sequence<d.sequence AND older.status IN ('queued', 'sending'))"
                " AND (SELECT count(*) FROM message_delivery_attempts a"
                " WHERE a.bot_id=d.bot_id AND a.chat_id=d.chat_id"
                " AND a.at>$2-CASE WHEN jsonb_typeof(d.payload->'chat_id')='number'"
                " AND d.chat_id ~ '^[1-9][0-9]*$'"
                " THEN $3::float8 ELSE $4::float8 END)"
                " < CASE WHEN jsonb_typeof(d.payload->'chat_id')='number'"
                " AND d.chat_id ~ '^[1-9][0-9]*$' THEN $5::int ELSE $6::int END"
                " ORDER BY d.priority, d.sequence LIMIT 1 FOR UPDATE OF d SKIP LOCKED",
                bot_id,
                now,
                policy.private_window,
                policy.group_window,
                policy.private_limit,
                policy.group_limit,
            )
            if row is None:
                return None
            token = str(uuid4())
            row = await connection.fetchrow(
                "UPDATE message_delivery SET status='sending', attempts=attempts+1,"
                " lease_until=$2, lease_token=$3 WHERE key=$1 RETURNING *",
                row["key"],
                now + policy.lease_seconds,
                token,
            )
            await connection.execute(
                "INSERT INTO message_delivery_attempts(bot_id, chat_id, at) VALUES($1, $2, $3)",
                bot_id,
                row["chat_id"],
                now,
            )
            return _delivery(row)

    async def complete(self, key: str, token: str, response: dict[str, Any]) -> bool:
        row = await self.pool.fetchval(
            "UPDATE message_delivery SET status='sent', response=$3::jsonb,"
            " lease_token='', lease_until=0 WHERE key=$1 AND lease_token=$2"
            " AND status='sending' RETURNING key",
            key,
            token,
            json.dumps(response, ensure_ascii=False),
        )
        return row is not None

    async def retry(
        self, key: str, token: str, *, delay: float, error: str, cooldown: bool = False
    ) -> bool:
        async with self.pool.raw.acquire() as connection, connection.transaction():
            # Везде порядок один: бюджет бота, затем строка сообщения.
            bot_id = await connection.fetchval(
                "SELECT bot_id FROM message_delivery WHERE key=$1", key
            )
            if bot_id is None:
                return False
            await _lock(connection, bot_id)
            now = await connection.fetchval("SELECT extract(epoch FROM clock_timestamp())::float8")
            available = now + max(0.0, delay)
            row = await connection.fetchval(
                "UPDATE message_delivery SET status='queued', available_at=$3, last_error=$4,"
                " lease_token='', lease_until=0 WHERE key=$1 AND lease_token=$2"
                " AND status='sending' RETURNING key",
                key,
                token,
                available,
                error,
            )
            if row is not None and cooldown:
                await connection.execute(
                    "INSERT INTO message_delivery_budget(bot_id, cooldown_until) VALUES($1, $2)"
                    " ON CONFLICT(bot_id) DO UPDATE SET cooldown_until="
                    " greatest(message_delivery_budget.cooldown_until, excluded.cooldown_until)",
                    bot_id,
                    available,
                )
            return row is not None

    async def fail(self, key: str, token: str, *, error: str) -> bool:
        row = await self.pool.fetchval(
            "UPDATE message_delivery SET status='failed', last_error=$3, lease_token='',"
            " lease_until=0 WHERE key=$1 AND lease_token=$2 AND status='sending' RETURNING key",
            key,
            token,
            error,
        )
        return row is not None
