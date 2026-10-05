"""Сохраняемый исход отправки; правила очереди общие для SQL и режима local."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any, Protocol


class DeliveryPriority(IntEnum):
    REPLY = 0
    ANNOUNCEMENT = 10


class DeliveryStatus(StrEnum):
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"


class DeliveryKeyConflictError(RuntimeError):
    """Один номер отправки нельзя использовать для другого сообщения."""


class DeliveryFailedError(RuntimeError):
    """Telegram постоянно отверг сообщение; строка очереди сохранена."""


@dataclass(frozen=True, slots=True)
class DeliveryPolicy:
    sends_per_second: int = 30
    private_limit: int = 3
    private_window: float = 3.0
    group_limit: int = 20
    group_window: float = 60.0
    lease_seconds: float = 90.0

    def __post_init__(self) -> None:
        if min(self.sends_per_second, self.private_limit, self.group_limit) < 1:
            raise ValueError("Delivery limits must be positive")
        if min(self.private_window, self.group_window, self.lease_seconds) <= 0:
            raise ValueError("Delivery windows must be positive")

    def chat_window(self, chat_id: int | str) -> tuple[int, float]:
        if isinstance(chat_id, int) and chat_id > 0:
            return self.private_limit, self.private_window
        return self.group_limit, self.group_window


@dataclass(frozen=True, slots=True)
class Delivery:
    key: str
    sequence: int
    bot_id: int
    chat_id: int | str
    payload: dict[str, Any]
    priority: DeliveryPriority = DeliveryPriority.REPLY
    status: DeliveryStatus = DeliveryStatus.QUEUED
    attempts: int = 0
    available_at: float = 0.0
    lease_until: float = 0.0
    lease_token: str = ""
    response: dict[str, Any] | None = None
    last_error: str = ""
    operation_id: str | None = None


@dataclass(slots=True)
class DeliveryBudget:
    attempts: list[tuple[float, str]] = field(default_factory=list)
    cooldown_until: float = 0.0


class DeliveryQueue(Protocol):
    async def enqueue(
        self,
        *,
        key: str,
        bot_id: int,
        chat_id: int | str,
        payload: dict[str, Any],
        priority: DeliveryPriority = DeliveryPriority.REPLY,
        operation_id: str | None = None,
    ) -> Delivery: ...

    async def get(self, key: str) -> Delivery | None: ...

    async def pending_for_operation(
        self, operation_id: str, bot_id: int, chat_id: int | str
    ) -> bool: ...

    async def claim(self, bot_id: int, policy: DeliveryPolicy) -> Delivery | None: ...

    async def complete(self, key: str, token: str, response: dict[str, Any]) -> bool: ...

    async def retry(
        self, key: str, token: str, *, delay: float, error: str, cooldown: bool = False
    ) -> bool: ...

    async def fail(self, key: str, token: str, *, error: str) -> bool: ...
