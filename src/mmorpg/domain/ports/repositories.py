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
from mmorpg.domain.entities.location import LocationState, Presence
from mmorpg.domain.entities.trade import Offer, TradeRecord, TradeStatus


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
class PrivacyRepository(Protocol):
    """What a player shows in the group, and whom they refuse to deal with.

    Both live on the account rather than on the character: a black list a player
    could walk around by rolling a second character would not be one (Roadmap 2.5).
    A player who never touched any of this is open to everyone, so an account with
    no row at all answers "visible, blocks nobody".
    """

    async def profile_visible(self, telegram_id: int) -> bool: ...

    async def set_profile_visible(self, telegram_id: int, visible: bool) -> None: ...

    async def blocks(self, telegram_id: int, other_id: int) -> bool:
        """Whether ``telegram_id`` put ``other_id`` on their black list."""

    async def block(self, telegram_id: int, other_id: int, *, at: int) -> bool:
        """Add to the black list. False if they were already on it."""

    async def unblock(self, telegram_id: int, other_id: int) -> bool:
        """Take off the black list. False if they were not on it."""


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
class TradeRepository(Protocol):
    """Pending offers and the journal of everything that became of them.

    This is the one part of the group economy that cannot live in a cache: while
    an offer stands, the author's item or gold is held in it, and a store that
    expires by itself would quietly swallow both (Roadmap 2.3).

    ``close`` is the gate that makes a trade atomic. It changes the row only while
    it is still pending and returns what it changed, so two people answering the
    same offer in the same second produce exactly one settlement: the loser gets
    ``None`` back and moves nothing.
    """

    async def open(self, offer: Offer, *, scope: str) -> TradeRecord | None:
        """Publish an offer, assigning the short number players will type.

        ``None`` means no number was free - the group has 999 offers standing.
        """

    async def pending(self, number: int, *, scope: str) -> TradeRecord | None: ...

    async def close(
        self,
        number: int,
        *,
        scope: str,
        status: TradeStatus,
        settled_at: int,
        tax: int = 0,
    ) -> TradeRecord | None: ...

    async def expire(self, *, scope: str, before: int) -> tuple[TradeRecord, ...]:
        """Close every offer made before ``before`` and return them, once each.

        The caller returns each stake to its author; returning a record twice
        would hand out the same item twice, which is why this both reads and
        writes in one step.
        """

    async def journal(self, character_id: int, *, limit: int = 20) -> tuple[TradeRecord, ...]:
        """The latest trades this character was a side of, newest first."""


@runtime_checkable
class LocationStateCache(Protocol):
    """The shared state of a location: its generation, its cleared nodes, its people.

    A location is common ground. Everyone standing in one sees the same map and
    the same emptied nodes, and can see each other. None of it is a source of
    truth - losing Redis re-rolls the map and forgets who was where, which costs
    a visit and never a character (``docs/procgen.md``).
    """

    async def state(self, city_id: str, slot: int) -> LocationState: ...

    async def mark_cleared(
        self, city_id: str, slot: int, generation: int, node: int, ttl: int
    ) -> LocationState: ...

    async def rotate(self, city_id: str, slot: int, generation: int, ttl: int) -> LocationState:
        """Roll the location into its next generation, once, when it is cleared out.

        Passing the generation the caller saw is what makes two players finishing
        the last node at the same time roll it over once, not twice.
        """

    async def arrive(
        self, city_id: str, slot: int, presence: Presence, *, now: int, ttl: int
    ) -> None: ...

    async def leave(self, city_id: str, slot: int, character_id: int) -> None: ...

    async def others_at(
        self, city_id: str, slot: int, node: int, *, exclude: int, now: int, ttl: int
    ) -> tuple[Presence, ...]:
        """Who else is standing on this node right now, freshest first."""


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
