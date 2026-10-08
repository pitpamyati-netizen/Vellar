"""Версионированные снимки данных боя, без исполняемых объектов."""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from functools import lru_cache
from typing import Any, cast

from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.procgen.items import assemble


def encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return {"enum": f"{type(value).__module__}:{type(value).__name__}", "value": value.value}
    if is_dataclass(value):
        return {
            "type": f"{type(value).__module__}:{type(value).__name__}",
            "fields": {one.name: encode(getattr(value, one.name)) for one in fields(value)},
        }
    if isinstance(value, Mapping):
        return {"map": [[encode(key), encode(one)] for key, one in value.items()]}
    if isinstance(value, tuple | frozenset):
        return {"sequence": [encode(one) for one in value], "frozen": isinstance(value, frozenset)}
    if isinstance(value, list):
        return [encode(one) for one in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise ValueError(f"Unsupported snapshot value: {type(value).__name__}")


def decode(value: Any) -> Any:
    if isinstance(value, list):
        return [decode(one) for one in value]
    if not isinstance(value, dict):
        return value
    if "map" in value:
        return {decode(key): decode(one) for key, one in value["map"]}
    if "sequence" in value:
        decoded = [decode(one) for one in value["sequence"]]
        return frozenset(decoded) if value["frozen"] else tuple(decoded)
    name = str(value.get("type", value.get("enum", "")))
    module, _, kind = name.partition(":")
    if not module.startswith("mmorpg.domain.entities.") or not kind.isidentifier():
        raise ValueError("Unknown snapshot type")
    cls = getattr(importlib.import_module(module), kind)
    if "enum" in value and isinstance(cls, type) and issubclass(cls, Enum):
        return cls(value["value"])
    if not is_dataclass(cls):
        raise ValueError("Snapshot type must be data")
    return cast(Any, cls)(**{key: decode(one) for key, one in value["fields"].items()})


def content_snapshot(content: GameContent) -> tuple[str, str]:
    data = {
        key: encode(getattr(content, key))
        for key in inspect.signature(GameContent.build).parameters
        if key != "assemble"
    }
    raw = json.dumps({"schema": 1, "data": data}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest(), raw


@lru_cache(maxsize=4)
def restore_content(raw: str) -> GameContent:
    saved = json.loads(raw)
    if saved["schema"] != 1:
        raise ValueError("Unsupported content snapshot")
    return GameContent.build(
        **{key: decode(one) for key, one in saved["data"].items()}, assemble=assemble
    )
