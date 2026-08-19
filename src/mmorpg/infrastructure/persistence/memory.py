"""In-memory repositories.

Used when ``APP_ENV=local`` and by the whole fast test suite. State lives in plain
dicts and is lost on restart - that is a development convenience, never a
deployment target (``docs/adr/0005-in-memory-adapters.md``).
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType

from mmorpg.domain.entities.character import Character, InventoryEntry
from mmorpg.domain.entities.moderation import Ban, KeeperEntry
from mmorpg.domain.entities.overlay import OverlayKind, OverlayRecord
from mmorpg.domain.entities.trade import Offer, TradeRecord, TradeStatus
from mmorpg.domain.ports.repositories import AccessibilitySettings, Census, User
from mmorpg.domain.rules.group_offers import MAX_OFFER_NUMBER


class InMemoryUserRepository:
    def __init__(self) -> None:
        self._users: dict[int, User] = {}
        # Когда аккаунт последний раз проверяли и когда он заблокировал бота.
        # Ноль в обоих означает «не проверяли» и «не блокировал».
        self._checked: dict[int, int] = {}
        self._blocked: dict[int, int] = {}

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

    async def set_keeper(self, telegram_id: int, keeper: bool) -> None:
        user = self._users.get(telegram_id) or User(telegram_id=telegram_id)
        self._users[telegram_id] = replace(user, keeper=keeper)

    async def unchecked(self, *, limit: int, before: int) -> tuple[int, ...]:
        stale = [
            telegram_id
            for telegram_id in sorted(self._users)
            if self._checked.get(telegram_id, 0) < before and not self._blocked.get(telegram_id)
        ]
        return tuple(stale[:limit])

    async def mark_checked(self, telegram_id: int, *, at: int, blocked: bool) -> None:
        self._checked[telegram_id] = at
        if blocked:
            self._blocked[telegram_id] = at
        else:
            self._blocked.pop(telegram_id, None)

    async def blocked_count(self) -> int:
        return len(self._blocked)

    async def set_ban(self, telegram_id: int, ban: Ban) -> None:
        user = self._users.get(telegram_id) or User(telegram_id=telegram_id)
        self._users[telegram_id] = replace(user, ban=ban)

    async def banned_count(self, *, now: int) -> int:
        return sum(1 for user in self._users.values() if user.ban.forever or user.ban.until > now)

    async def purge_blocked(self) -> int:
        gone = tuple(self._blocked)
        for telegram_id in gone:
            self._users.pop(telegram_id, None)
            self._checked.pop(telegram_id, None)
        self._blocked.clear()
        return len(gone)


class InMemoryKeeperLogRepository:
    """Журнал смотрителя в списке. Свежие записи в конце, читаются с конца."""

    def __init__(self) -> None:
        self._entries: list[KeeperEntry] = []

    async def record(self, entry: KeeperEntry) -> None:
        self._entries.append(entry)

    async def latest(self, *, limit: int = 20) -> tuple[KeeperEntry, ...]:
        return tuple(reversed(self._entries[-limit:]))


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
        # Когда строку последний раз трогали - то же, что ``updated_at`` в SQL.
        # Без него «давно брошенный» ничем не отличается от «только что создан».
        self._touched: dict[int, int] = {}
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
        self._touched[stored.id] = int(time.time())
        self._next_id += 1
        return stored

    async def save(self, character: Character) -> None:
        self._characters[character.id] = character
        self._touched[character.id] = int(time.time())

    async def spend_gold(self, character_id: int, amount: int) -> bool:
        """Списать золото одним шагом. Здесь это и так один шаг: цикл событий
        один, между чтением и записью никто не вклинится."""
        character = self._characters.get(character_id)
        if character is None or amount < 0 or character.gold < amount:
            return False
        await self.save(character.with_gold(-amount))
        return True

    async def grant_gold(self, character_id: int, amount: int) -> None:
        character = self._characters.get(character_id)
        if character is None or amount <= 0:
            return
        await self.save(character.with_gold(amount))

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

    async def turning_tally(self, cycle_id: str) -> Mapping[str, int]:
        counted: Counter[str] = Counter()
        for character in self._characters.values():
            if character.turning_cycle != cycle_id or not character.turning_answer:
                continue
            if character.seals <= 0:
                continue
            counted[character.turning_answer] += character.seals
        return MappingProxyType(dict(counted))

    async def find_by_name(self, name: str) -> Character | None:
        folded = name.strip().casefold()
        for character in self._characters.values():
            if character.name.casefold() == folded:
                return character
        return None

    async def newest(self, *, limit: int = 8) -> tuple[Character, ...]:
        ordered = sorted(self._characters.values(), key=lambda character: -character.id)
        return tuple(ordered[:limit])

    async def census(self, *, day: int, week: int, stale: int) -> Census:
        everybody = tuple(self._characters.values())
        if not everybody:
            return Census(blocked=0)
        levels = [character.level for character in everybody]
        leaders = sorted(everybody, key=lambda character: (-character.level, character.name))[:5]
        return Census(
            characters=len(everybody),
            accounts=len({character.user_id for character in everybody}),
            fresh_day=self._touched_since(day),
            fresh_week=self._touched_since(week),
            abandoned=len(self._abandoned(stale)),
            top_level=max(levels),
            average_level=round(sum(levels) / len(levels)),
            gold_on_hand=sum(character.gold for character in everybody),
            gold_in_bank=sum(character.bank_gold for character in everybody),
            quests_done=sum(len(character.quests.done) for character in everybody),
            arena_fights=sum(
                character.arena_wins + character.arena_losses for character in everybody
            ),
            leaders=tuple((character.name, character.level) for character in leaders),
        )

    async def purge_abandoned(self, *, before: int) -> int:
        gone = self._abandoned(before)
        for character_id in gone:
            del self._characters[character_id]
            self._touched.pop(character_id, None)
        return len(gone)

    async def delete(self, character_id: int) -> bool:
        if character_id not in self._characters:
            return False
        del self._characters[character_id]
        self._touched.pop(character_id, None)
        return True

    def _touched_since(self, moment: int) -> int:
        return sum(1 for touched in self._touched.values() if touched >= moment)

    def _abandoned(self, before: int) -> tuple[int, ...]:
        return tuple(
            character.id
            for character in self._characters.values()
            if character.level == 1
            and character.experience == 0
            and character.tutorial == 0
            and self._touched.get(character.id, 0) < before
        )


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


class InMemoryContentOverlayRepository:
    """Правки смотрителя в словаре.

    Ключ — разновидность и идентификатор: одна сущность правится один раз, а не
    десятью записями, которые пришлось бы складывать в правильном порядке.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], OverlayRecord] = {}

    async def all(self) -> tuple[OverlayRecord, ...]:
        return tuple(
            sorted(self._records.values(), key=lambda record: (record.updated_at, record.entity_id))
        )

    async def put(self, record: OverlayRecord) -> None:
        self._records[(record.kind.value, record.entity_id)] = record

    async def forget(self, kind: OverlayKind, entity_id: str) -> bool:
        return self._records.pop((kind.value, entity_id), None) is not None


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

    async def expire(self, *, before: int, scope: str | None = None) -> tuple[TradeRecord, ...]:
        stale = [
            record
            for record in self._records
            if (scope is None or record.scope == scope)
            and record.is_pending
            and record.offer.created_at <= before
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
