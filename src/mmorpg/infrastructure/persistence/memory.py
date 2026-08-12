"""In-memory repositories.

Used when ``APP_ENV=local`` and by the whole fast test suite. State lives in plain
dicts and is lost on restart - that is a development convenience, never a
deployment target (``docs/adr/0005-in-memory-adapters.md``).
"""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities.character import Character, InventoryEntry
from mmorpg.domain.ports.repositories import AccessibilitySettings, User


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
