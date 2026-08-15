"""In-memory repositories.

Used when ``APP_ENV=local`` and by the whole fast test suite. State lives in plain
dicts and is lost on restart - that is a development convenience, never a
deployment target (``docs/adr/0005-in-memory-adapters.md``).
"""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities.character import Character, InventoryEntry
from mmorpg.domain.entities.trade import Offer, TradeRecord, TradeStatus
from mmorpg.domain.ports.repositories import AccessibilitySettings, User
from mmorpg.domain.rules.group_offers import MAX_OFFER_NUMBER


class InMemoryUserRepository:
    def __init__(self) -> None:
        self._users: dict[int, User] = {}

    async def get(self, telegram_id: int) -> User | None:
        return self._users.get(telegram_id)

    async def upsert(self, user: User) -> User:
        existing = self._users.get(user.telegram_id)
        stored = user if existing is None else replace(existing, username=user.username)
        self._users[user.telegram_id] = stored
        return stored

    async def save_settings(self, telegram_id: int, settings: AccessibilitySettings) -> None:
        user = self._users.get(telegram_id) or User(telegram_id=telegram_id)
        self._users[telegram_id] = replace(user, settings=settings)


class InMemoryPrivacyRepository:
    """Profile visibility and black lists, by account id.

    Absence is the open state: an account nobody stored anything about shows its
    profile and blocks no one.
    """

    def __init__(self) -> None:
        self._hidden: set[int] = set()
        self._blocks: dict[int, set[int]] = {}

    async def profile_visible(self, telegram_id: int) -> bool:
        return telegram_id not in self._hidden

    async def set_profile_visible(self, telegram_id: int, visible: bool) -> None:
        if visible:
            self._hidden.discard(telegram_id)
        else:
            self._hidden.add(telegram_id)

    async def blocks(self, telegram_id: int, other_id: int) -> bool:
        return other_id in self._blocks.get(telegram_id, set())

    async def block(self, telegram_id: int, other_id: int, *, at: int) -> bool:
        listed = self._blocks.setdefault(telegram_id, set())
        if other_id in listed:
            return False
        listed.add(other_id)
        return True

    async def unblock(self, telegram_id: int, other_id: int) -> bool:
        listed = self._blocks.get(telegram_id, set())
        if other_id not in listed:
            return False
        listed.discard(other_id)
        return True


class InMemoryCharacterRepository:
    def __init__(self) -> None:
        self._characters: dict[int, Character] = {}
        self._next_id = 1

    async def get(self, character_id: int) -> Character | None:
        return self._characters.get(character_id)

    async def get_active(self, telegram_id: int) -> Character | None:
        for character in self._characters.values():
            if character.user_id == telegram_id:
                return character
        return None

    async def list_for_user(self, telegram_id: int) -> tuple[Character, ...]:
        return tuple(
            character for character in self._characters.values() if character.user_id == telegram_id
        )

    async def create(self, character: Character) -> Character:
        stored = replace(character, id=self._next_id)
        self._characters[stored.id] = stored
        self._next_id += 1
        return stored

    async def save(self, character: Character) -> None:
        self._characters[character.id] = character

    async def name_taken(self, name: str) -> bool:
        folded = name.casefold()
        return any(character.name.casefold() == folded for character in self._characters.values())

    async def arena_opponent(self, *, level: int, window: int, exclude_id: int) -> Character | None:
        """The nearest level match. Deterministic here, unlike the SQL one.

        A game running in memory has a handful of characters and no reason to
        randomise: the test that asks for an opponent wants the same one twice.
        """
        pool = [
            character
            for character in self._characters.values()
            if character.id != exclude_id and abs(character.level - level) <= window
        ]
        if not pool:
            return None
        return min(pool, key=lambda character: (abs(character.level - level), character.id))

    async def arena_table(self, *, limit: int = 10) -> tuple[Character, ...]:
        ranked = sorted(
            (character for character in self._characters.values() if character.arena_wins),
            key=lambda character: (-character.arena_wins, -character.level, character.name),
        )
        return tuple(ranked[:limit])


class InMemoryInventoryRepository:
    def __init__(self) -> None:
        self._items: dict[int, dict[str, int]] = {}

    async def list_items(self, character_id: int) -> tuple[InventoryEntry, ...]:
        held = self._items.get(character_id, {})
        return tuple(
            InventoryEntry(item_id=item_id, quantity=quantity)
            for item_id, quantity in sorted(held.items())
            if quantity > 0
        )

    async def add(self, character_id: int, item_id: str, quantity: int = 1) -> None:
        held = self._items.setdefault(character_id, {})
        held[item_id] = held.get(item_id, 0) + quantity

    async def remove(self, character_id: int, item_id: str, quantity: int = 1) -> bool:
        held = self._items.setdefault(character_id, {})
        if held.get(item_id, 0) < quantity:
            return False
        held[item_id] -= quantity
        if held[item_id] <= 0:
            del held[item_id]
        return True

    async def count(self, character_id: int, item_id: str) -> int:
        return self._items.get(character_id, {}).get(item_id, 0)


class InMemoryTradeRepository:
    """Pending offers and the trade journal, in a list.

    Nothing here needs a lock: one process, one event loop, and every method
    below runs to the end without awaiting anything.
    """

    def __init__(self) -> None:
        self._records: list[TradeRecord] = []

    async def open(self, offer: Offer, *, scope: str) -> TradeRecord | None:
        taken = {
            record.number for record in self._records if record.scope == scope and record.is_pending
        }
        free = next((n for n in range(1, MAX_OFFER_NUMBER + 1) if n not in taken), None)
        if free is None:
            return None

        record = TradeRecord(offer=replace(offer, number=free), scope=scope)
        self._records.append(record)
        return record

    async def pending(self, number: int, *, scope: str) -> TradeRecord | None:
        return self._pending(number, scope)

    async def close(
        self,
        number: int,
        *,
        scope: str,
        status: TradeStatus,
        settled_at: int,
        tax: int = 0,
    ) -> TradeRecord | None:
        found = self._pending(number, scope)
        if found is None:
            return None
        closed = replace(found, status=status, tax=tax, settled_at=settled_at)
        self._records[self._records.index(found)] = closed
        return closed

    async def expire(self, *, scope: str, before: int) -> tuple[TradeRecord, ...]:
        stale = [
            record
            for record in self._records
            if record.scope == scope and record.is_pending and record.offer.created_at <= before
        ]
        for record in stale:
            self._records[self._records.index(record)] = replace(
                record, status=TradeStatus.EXPIRED, settled_at=before
            )
        return tuple(stale)

    async def journal(self, character_id: int, *, limit: int = 20) -> tuple[TradeRecord, ...]:
        involved = [
            record
            for record in self._records
            if character_id in (record.offer.author.character_id, record.offer.target.character_id)
        ]
        return tuple(reversed(involved[-limit:]))

    def _pending(self, number: int, scope: str) -> TradeRecord | None:
        for record in self._records:
            if record.scope == scope and record.number == number and record.is_pending:
                return record
        return None
