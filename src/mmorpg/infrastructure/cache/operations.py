"""Изменения Redis готовятся отдельно и применяются после COMMIT PostgreSQL.

Постоянный список изменений позволяет повторить незаконченный перенос. До него
следующая операция игры не начинается. Сроки кэша этим не отменяются (M02/M03).
"""

from __future__ import annotations

import base64
import hashlib
import time
from typing import Any
from uuid import uuid4

from mmorpg.application.operations import current_operation

READS = frozenset({"get", "hget", "hgetall", "zrangebyscore", "exists", "ttl", "pttl"})
WRITES = frozenset(
    {
        "set",
        "delete",
        "hset",
        "hsetnx",
        "hdel",
        "zadd",
        "zrem",
        "zremrangebyscore",
        "expire",
        "pexpire",
    }
)


class RedisChanges:
    def __init__(self, client: Any) -> None:
        self.client = client
        self.prefix = f"vellar:staged:{uuid4()}:"
        self.keys: dict[str, str] = {}
        self.expires: dict[str, float | None] = {}

    async def prepare(self, key: str) -> str:
        if key in self.keys:
            return self.keys[key]
        staged = self.prefix + hashlib.sha256(key.encode()).hexdigest()
        data = await self.client.dump(key)
        ttl = await self.client.pttl(key)
        if data is not None and ttl != -2:
            await self.client.restore(staged, 600_000, data, replace=True)
        self.keys[key] = staged
        self.expires[key] = time.time() + ttl / 1000 if ttl >= 0 else None
        return staged

    async def write(
        self, name: str, key: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Any:
        staged = await self.prepare(key)
        result = await getattr(self.client, name)(staged, *args, **kwargs)
        if name == "set" and result:
            if kwargs.get("ex") is not None:
                self.expires[key] = time.time() + float(kwargs["ex"])
            elif kwargs.get("px") is not None:
                self.expires[key] = time.time() + float(kwargs["px"]) / 1000
            elif not kwargs.get("keepttl"):
                self.expires[key] = None
        elif name in ("expire", "pexpire") and result:
            self.expires[key] = time.time() + float(args[0]) / (1000 if name == "pexpire" else 1)
        # Временные копии никогда не остаются навсегда после остановки процесса.
        await self.client.pexpire(staged, 600_000)
        return result

    async def manifest(self) -> list[dict[str, Any]]:
        changes = []
        for key, staged in self.keys.items():
            data = await self.client.dump(staged)
            changes.append(
                {
                    "key": key,
                    "data": base64.b64encode(data).decode() if data is not None else None,
                    "expires": self.expires[key],
                }
            )
        return changes

    async def cleanup(self) -> None:
        if self.keys:
            await self.client.delete(*self.keys.values())


async def apply_changes(client: Any, changes: list[dict[str, Any]]) -> None:
    if not changes:
        return
    # MULTI/EXEC применяет все ключи вместе. Повтор RESTORE REPLACE безопасен.
    async with client.pipeline(transaction=True) as pipeline:
        for change in changes:
            expiry = change["expires"]
            ttl = max(1, int((expiry - time.time()) * 1000)) if expiry is not None else 0
            if change["data"] is None or (expiry is not None and expiry <= time.time()):
                pipeline.delete(change["key"])
            else:
                pipeline.restore(change["key"], ttl, base64.b64decode(change["data"]), replace=True)
        await pipeline.execute()


class TransactionalRedis:
    def __init__(self, client: Any) -> None:
        self.raw: Any = client.raw if isinstance(client, TransactionalRedis) else client

    def __getattr__(self, name: str) -> Any:
        if name not in READS | WRITES:
            return getattr(self.raw, name)

        async def call(key: str, *args: Any, **kwargs: Any) -> Any:
            operation = current_operation()
            if operation is None:
                return await getattr(self.raw, name)(key, *args, **kwargs)
            changes = operation.redis_changes.setdefault(self.raw, RedisChanges(self.raw))
            if name in READS:
                return await getattr(self.raw, name)(changes.keys.get(key, key), *args, **kwargs)
            if name == "delete" and args:
                return sum([await changes.write(name, one, (), kwargs) for one in (key, *args)])
            return await changes.write(name, key, args, kwargs)

        return call
