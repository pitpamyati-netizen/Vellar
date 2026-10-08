"""Граница одного изменения ценностей. Хранилища предоставляют её реализацию."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Protocol, cast, get_type_hints
from uuid import uuid4

from pydantic import TypeAdapter


class StaleCharacterError(RuntimeError):
    """Сохранение рассчитано по версии, которую уже изменили."""


class OperationKeyConflictError(RuntimeError):
    """Один номер операции прислали с другим содержимым."""


class MissingResourceError(RuntimeError):
    """Ожидаемая вещь или участник исчезли: действие надо отменить целиком."""


class OperationCommittedError(RuntimeError):
    """Ценности сохранены, но завершение ответа или кэша требует повтора."""


class OperationOutcomeUnknownError(RuntimeError):
    """Ответ COMMIT потерян, а постоянный результат пока недоступен."""


@dataclass(slots=True)
class Operation:
    id: str
    kind: str
    task: asyncio.Task[Any] | None = field(default_factory=asyncio.current_task)
    gold_events: list[tuple[str, int, int, str]] = field(default_factory=list)
    after_commit: list[Callable[[], Awaitable[Any]]] = field(default_factory=list)
    redis_changes: dict[object, Any] = field(default_factory=dict)
    memory_changes: dict[object, Any] = field(default_factory=dict)
    replayed: bool = False
    commit_attempted: bool = False
    sql_committed: bool = False
    participants: tuple[object, ...] = ()
    command: bool = False
    reply_chat_id: int | None = None
    replies: list[dict[str, Any]] = field(default_factory=list)
    legacy_id: str | None = None
    sent_messages: dict[int, int] = field(default_factory=dict)


def json_fallback(value: Any) -> Any:
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"Cannot encode operation result: {type(value).__name__}")


_current: ContextVar[Operation | None] = ContextVar("economic_operation", default=None)


def current_operation() -> Operation | None:
    operation = _current.get()
    # create_task наследует контекст, но не вправе пользоваться чужим соединением.
    return operation if operation is not None and operation.task is asyncio.current_task() else None


async def after_commit(action: Callable[[], Awaitable[Any]]) -> None:
    operation = current_operation()
    if operation is None:
        await action()
    else:
        operation.after_commit.append(action)


class OperationBoundary(Protocol):
    async def run[R](
        self,
        action: Callable[[], Awaitable[R]],
        *,
        operation: Operation,
        fingerprint: str,
        codec: TypeAdapter[R],
        participants: tuple[object, ...],
    ) -> R: ...


def _repositories(values: list[Any]) -> tuple[object, ...]:
    found: list[object] = []
    for value in values:
        for candidate in (value, getattr(value, "_roster", None), getattr(value, "_cache", None)):
            if (
                candidate is not None
                and hasattr(candidate, "operations")
                and all(candidate is not previous for previous in found)
            ):
                found.append(candidate)
        for name in ("characters", "inventory", "trades", "privacy", "instances"):
            candidate = getattr(value, name, None)
            if (
                candidate is not None
                and hasattr(candidate, "operations")
                and all(candidate is not previous for previous in found)
            ):
                found.append(candidate)
    for repository in tuple(found):
        instances = getattr(repository, "instances", None)
        if instances is not None and all(instances is not previous for previous in found):
            found.append(instances)
    return tuple(sorted(found, key=lambda one: "CharacterRepository" not in type(one).__name__))


def atomic_action[**P, R](function: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Сервис или обработчик исполняется целиком в операции его хранилищ."""
    signature = inspect.signature(function)
    codec: TypeAdapter[R] | None = None

    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        nonlocal codec
        bound = signature.bind(*args, **kwargs)
        participants = _repositories(list(bound.arguments.values()))
        active = current_operation()
        if active is not None:
            if any(all(one is not known for known in active.participants) for one in participants):
                raise ValueError("Nested repositories must participate in the outer operation")
            return await function(*args, **kwargs)
        if not participants:
            return await function(*args, **kwargs)
        boundary = cast(OperationBoundary, getattr(participants[0], "operations", None))
        key = bound.arguments.get("operation_id")
        message = bound.arguments.get("message")
        if key is None and message is not None:
            bot = getattr(message, "bot", None)
            if bot is not None and getattr(bot, "id", None) is not None:
                key = f"telegram:{bot.id}:{message.chat.id}:{message.message_id}"
        kind = f"{function.__module__}.{function.__qualname__}"
        # Только хеш запроса: личный текст не попадает в журнал операций.
        request = [kind]
        for name in ("command", "author_id", "target_id", "trade_id"):
            request.append(str(bound.arguments.get(name, "")))
        if message is not None:
            request.extend(
                (str(message.from_user.id if message.from_user else 0), message.text or "")
            )
        owner = bound.arguments.get("self")
        request.append(str(getattr(owner, "scope", "")))
        fingerprint = hashlib.sha256("\n".join(request).encode()).hexdigest()
        if codec is None:
            codec = TypeAdapter(get_type_hints(function).get("return", Any))
        operation = Operation(id=str(key or uuid4()), kind=kind, participants=participants)
        result = await boundary.run(
            lambda: function(*args, **kwargs),
            operation=operation,
            fingerprint=fingerprint,
            codec=codec,
            participants=participants,
        )
        if operation.replayed and message is not None and result is None:
            await message.answer(
                "Это действие уже сохранено. Нажмите «Главное меню», чтобы продолжить.",
                parse_mode=None,
            )
        return result

    return wrapped
