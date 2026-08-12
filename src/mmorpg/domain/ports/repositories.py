"""Ports: what the domain needs from the outside world.

These are ``typing.Protocol`` definitions only - no implementation, no imports of
asyncpg or redis. Two implementations satisfy each of them: a PostgreSQL/Redis one
for dev and prod, and an in-memory one for ``APP_ENV=local`` and the test suite
(``docs/adr/0005-in-memory-adapters.md``).

This is the one place in ``domain/`` where ``async def`` appears: a port describes
a boundary to the outside world, and everything beyond that boundary is
asynchronous. The rules and entities themselves stay synchronous and pure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from mmorpg.domain.entities.character import Character, InventoryEntry


@dataclass(frozen=True, slots=True)
class AccessibilitySettings:
    """Per-player presentation preferences.

    Emoji are **off by default** - accessibility rule 6.
    """

    emoji: bool = False
    verbose: bool = True
    page_size: int = 8


@dataclass(frozen=True, slots=True)
class User:
    """A Telegram user, independent of their characters."""

    telegram_id: int
    username: str = ""
    settings: AccessibilitySettings = field(default_factory=AccessibilitySettings)


@runtime_checkable
class UserRepository(Protocol):
    async def get(self, telegram_id: int) -> User | None: ...

    async def upsert(self, user: User) -> User: ...

    async def save_settings(self, telegram_id: int, settings: AccessibilitySettings) -> None: ...


@runtime_checkable
class CharacterRepository(Protocol):
    async def get(self, character_id: int) -> Character | None: ...

    async def get_active(self, telegram_id: int) -> Character | None: ...

    async def list_for_user(self, telegram_id: int) -> tuple[Character, ...]: ...

    async def create(self, character: Character) -> Character: ...

    async def save(self, character: Character) -> None: ...

    async def name_taken(self, name: str) -> bool: ...


@runtime_checkable
class InventoryRepository(Protocol):
    async def list_items(self, character_id: int) -> tuple[InventoryEntry, ...]: ...

    async def add(self, character_id: int, item_id: str, quantity: int = 1) -> None: ...

    async def remove(self, character_id: int, item_id: str, quantity: int = 1) -> bool: ...

    async def count(self, character_id: int, item_id: str) -> int: ...


@runtime_checkable
class LocationDeltaCache(Protocol):
    """Which nodes a player already cleared in the current world cycle.

    The value is a bitmask and the key expires with the cycle, so PostgreSQL never
    sees any of it (``docs/procgen.md``).
    """

    async def get_mask(self, character_id: int, city_id: str, slot: int, cycle: int) -> int: ...

    async def mark_cleared(
        self, character_id: int, city_id: str, slot: int, cycle: int, node: int, ttl: int
    ) -> int: ...

    async def reset(self, character_id: int, city_id: str, slot: int, cycle: int) -> None: ...


@runtime_checkable
class StateCache(Protocol):
    """Short-lived JSON blobs: the active fight, the current screen, shop rolls."""

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl: int) -> None: ...

    async def delete(self, key: str) -> None: ...


@runtime_checkable
class IdempotencyStore(Protocol):
    """Drops duplicate Telegram updates so a redelivery cannot apply twice."""

    async def seen(self, update_id: int, ttl: int = 300) -> bool: ...
