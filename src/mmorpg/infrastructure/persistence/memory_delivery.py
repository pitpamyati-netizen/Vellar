"""Очередь local: те же порядок, бюджеты и повторы; процесс её не переживает."""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

from mmorpg.application.delivery import (
    Delivery,
    DeliveryBudget,
    DeliveryKeyConflictError,
    DeliveryPolicy,
    DeliveryPriority,
    DeliveryStatus,
)
from mmorpg.application.operations import current_operation


class _MemoryDeliveryCommit:
    """Применение memory_changes добавляет новые строки, не заменяя всю очередь."""

    def __init__(self, queue: MemoryDeliveryQueue) -> None:
        self._queue = queue

    @property
    def rows(self) -> list[Delivery]:
        return []

    @rows.setter
    def rows(self, rows: list[Delivery]) -> None:
        for row in rows:
            self._queue._rows[row.key] = row


class MemoryDeliveryQueue:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._rows: dict[str, Delivery] = {}
        self._budgets: dict[int, DeliveryBudget] = {}
        self._sequence = 0
        self._lock = asyncio.Lock()

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
        async with self._lock:
            operation = current_operation()
            staged = None
            if operation is not None:
                for owner, state in operation.memory_changes.items():
                    if isinstance(owner, _MemoryDeliveryCommit) and owner._queue is self:
                        staged = state
                        break
                if staged is None:
                    staged = SimpleNamespace(rows=[])
                    operation.memory_changes[_MemoryDeliveryCommit(self)] = staged
            existing = self._rows.get(key)
            if staged is not None:
                existing = next((row for row in staged.rows if row.key == key), existing)
            if existing is not None:
                if (
                    existing.bot_id != bot_id
                    or existing.chat_id != chat_id
                    or existing.payload != payload
                    or existing.priority != priority
                    or existing.operation_id != operation_id
                ):
                    raise DeliveryKeyConflictError(key)
                return existing
            self._sequence += 1
            row = Delivery(
                key=key,
                sequence=self._sequence,
                bot_id=bot_id,
                chat_id=chat_id,
                payload=copy.deepcopy(payload),
                priority=priority,
                available_at=self._clock(),
                operation_id=operation_id,
            )
            if staged is None:
                self._rows[key] = row
            else:
                staged.rows.append(row)
            return row

    async def get(self, key: str) -> Delivery | None:
        operation = current_operation()
        if operation is not None:
            for owner, state in operation.memory_changes.items():
                if isinstance(owner, _MemoryDeliveryCommit) and owner._queue is self:
                    staged = next((row for row in state.rows if row.key == key), None)
                    if staged is not None:
                        return cast(Delivery, staged)
        return self._rows.get(key)

    async def pending_for_operation(
        self, operation_id: str, bot_id: int, chat_id: int | str
    ) -> bool:
        return any(
            row.operation_id == operation_id
            and row.bot_id == bot_id
            and row.chat_id == chat_id
            and row.status in (DeliveryStatus.QUEUED, DeliveryStatus.SENDING)
            for row in self._rows.values()
        )

    async def claim(self, bot_id: int, policy: DeliveryPolicy) -> Delivery | None:
        async with self._lock:
            now = self._clock()
            budget = self._budgets.setdefault(bot_id, DeliveryBudget())
            horizon = max(1.0, policy.private_window, policy.group_window)
            budget.attempts = [(at, chat) for at, chat in budget.attempts if at > now - horizon]
            if (
                budget.cooldown_until > now
                or sum(at > now - 1.0 for at, _ in budget.attempts) >= policy.sends_per_second
            ):
                return None
            heads: dict[str, Delivery] = {}
            for row in sorted(self._rows.values(), key=lambda one: one.sequence):
                if row.bot_id == bot_id and row.status in (
                    DeliveryStatus.QUEUED,
                    DeliveryStatus.SENDING,
                ):
                    heads.setdefault(str(row.chat_id), row)
            for row in sorted(heads.values(), key=lambda one: (one.priority, one.sequence)):
                if row.available_at > now or row.lease_until > now:
                    continue
                limit, window = policy.chat_window(row.chat_id)
                count = sum(
                    chat == str(row.chat_id) and at > now - window for at, chat in budget.attempts
                )
                if count >= limit:
                    continue
                claimed = replace(
                    row,
                    status=DeliveryStatus.SENDING,
                    attempts=row.attempts + 1,
                    lease_token=str(uuid4()),
                    lease_until=now + policy.lease_seconds,
                )
                self._rows[row.key] = claimed
                budget.attempts.append((now, str(row.chat_id)))
                return claimed
            return None

    async def complete(self, key: str, token: str, response: dict[str, Any]) -> bool:
        async with self._lock:
            row = self._owned(key, token)
            if row is None:
                return False
            self._rows[key] = replace(
                row,
                status=DeliveryStatus.SENT,
                response=copy.deepcopy(response),
                lease_token="",
                lease_until=0.0,
            )
            return True

    async def retry(
        self, key: str, token: str, *, delay: float, error: str, cooldown: bool = False
    ) -> bool:
        async with self._lock:
            row = self._owned(key, token)
            if row is None:
                return False
            available = self._clock() + max(0.0, delay)
            self._rows[key] = replace(
                row,
                status=DeliveryStatus.QUEUED,
                available_at=available,
                lease_token="",
                lease_until=0.0,
                last_error=error,
            )
            if cooldown:
                budget = self._budgets.setdefault(row.bot_id, DeliveryBudget())
                budget.cooldown_until = max(budget.cooldown_until, available)
            return True

    async def fail(self, key: str, token: str, *, error: str) -> bool:
        async with self._lock:
            row = self._owned(key, token)
            if row is None:
                return False
            self._rows[key] = replace(
                row,
                status=DeliveryStatus.FAILED,
                lease_token="",
                lease_until=0.0,
                last_error=error,
            )
            return True

    def _owned(self, key: str, token: str) -> Delivery | None:
        row = self._rows.get(key)
        return (
            row
            if row is not None and row.status == DeliveryStatus.SENDING and row.lease_token == token
            else None
        )
