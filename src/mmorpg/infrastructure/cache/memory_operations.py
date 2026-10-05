"""Кэш режима local/solo также не меняется до завершения операции ценностей."""

from __future__ import annotations

import copy
from collections import defaultdict
from collections.abc import Callable, Coroutine
from dataclasses import fields, is_dataclass
from functools import wraps
from typing import Any, cast

from aiogram.fsm.storage.base import StateType, StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from mmorpg.application.operations import Operation, current_operation


def copy_values(value: Any) -> Any:
    if isinstance(value, dict):
        result = {key: copy_values(one) for key, one in value.items()}
        if isinstance(value, defaultdict):
            return defaultdict(value.default_factory, result)
        return result
    if isinstance(value, tuple):
        return tuple(copy_values(one) for one in value)
    if isinstance(value, list):
        return [copy_values(one) for one in value]
    if isinstance(value, set):
        return value.copy()
    if is_dataclass(value) and not getattr(
        getattr(value, "__dataclass_params__", None), "frozen", True
    ):
        cloned = copy.copy(value)
        for one in fields(value):
            setattr(cloned, one.name, copy_values(getattr(value, one.name)))
        return cloned
    return value


def memory_cache_action[**P, R](
    function: Callable[P, Coroutine[Any, Any, R]],
) -> Callable[P, Coroutine[Any, Any, R]]:
    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        operation = current_operation()
        owner: Any = args[0]
        if operation is None or getattr(owner, "_operation_copy", None) is operation:
            return await function(*args, **kwargs)
        if owner not in operation.memory_changes:
            cloned = copy.copy(owner)
            for name, value in vars(owner).items():
                setattr(cloned, name, copy_values(value))
            cloned._operation_copy = operation
            operation.memory_changes[owner] = cloned
        bound = getattr(operation.memory_changes[owner], function.__name__)
        return cast(R, await bound(*args[1:], **kwargs))

    return wrapped


def apply_memory_changes(operation: Operation) -> None:
    for owner, cloned in operation.memory_changes.items():
        for name, value in vars(cloned).items():
            if name != "_operation_copy":
                setattr(owner, name, value)


class AtomicMemoryStorage(MemoryStorage):
    @memory_cache_action
    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        await super().set_state(key, state)

    @memory_cache_action
    async def get_state(self, key: StorageKey) -> str | None:
        return await super().get_state(key)

    @memory_cache_action
    async def set_data(self, key: StorageKey, data: Any) -> None:
        await super().set_data(key, data)

    @memory_cache_action
    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        return await super().get_data(key)
