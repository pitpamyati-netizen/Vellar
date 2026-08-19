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

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from mmorpg.domain.entities.character import Character, InventoryEntry
from mmorpg.domain.entities.location import LocationState, Presence
from mmorpg.domain.entities.moderation import Ban, KeeperEntry
from mmorpg.domain.entities.overlay import OverlayKind, OverlayRecord
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
    """A Telegram user, independent of their characters.

    ``keeper`` is the right that was handed out from inside the game. It lives on
    the account and not on the character for the same reason a black list does: a
    right a player could walk around by rolling a second character would not be
    one. The right that comes from ``ADMIN_IDS`` is not stored here at all - it is
    read from the environment every time (``docs/keeper.md``).
    """

    telegram_id: int
    username: str = ""
    settings: AccessibilitySettings = field(default_factory=AccessibilitySettings)
    keeper: bool = False
    #: Временное отлучение от игры. Лежит на аккаунте по той же причине, что и
    #: право смотрителя: наказание, от которого можно уйти вторым персонажем,
    #: наказанием не было бы.
    ban: Ban = field(default_factory=Ban)


@dataclass(frozen=True, slots=True)
class Census:
    """Игра в числах: то, что смотритель проверяет, а не то, что интересно считать.

    Всё считается одним запросом на открытие экрана и нигде не хранится: счётчик,
    который живёт отдельно от того, что он считает, однажды расходится с ним
    (``Claude.md``, правило 8).
    """

    characters: int = 0
    accounts: int = 0
    fresh_day: int = 0
    fresh_week: int = 0
    abandoned: int = 0
    blocked: int = 0
    top_level: int = 0
    average_level: int = 0
    gold_on_hand: int = 0
    gold_in_bank: int = 0
    quests_done: int = 0
    arena_fights: int = 0
    banned: int = 0
    leaders: tuple[tuple[str, int], ...] = ()


@runtime_checkable
class UserRepository(Protocol):
    async def get(self, telegram_id: int) -> User | None: ...

    async def upsert(self, user: User) -> User: ...

    async def save_settings(self, telegram_id: int, settings: AccessibilitySettings) -> None: ...

    async def set_keeper(self, telegram_id: int, keeper: bool) -> None:
        """Дать или снять право смотрителя, выданное изнутри игры.

        Аккаунт, о котором ничего не записано, права не имеет, поэтому строку
        приходится заводить: право выдают и тому, кто ещё ни разу не менял
        настройки.
        """

    async def unchecked(self, *, limit: int, before: int) -> tuple[int, ...]:
        """Аккаунты, которых давно не проверяли на «бот заблокирован».

        Проверка стоит одного обращения к Telegram на человека, поэтому она идёт
        порциями и запоминает, кого уже спросили.
        """

    async def mark_checked(self, telegram_id: int, *, at: int, blocked: bool) -> None: ...

    async def blocked_count(self) -> int: ...

    async def set_ban(self, telegram_id: int, ban: Ban) -> None:
        """Наложить блокировку или снять её.

        Строку приходится заводить: блокируют и того, кто ни разу не менял
        настройки, а аккаунта без записи в базе не существует.
        """

    async def banned_count(self, *, now: int) -> int:
        """Сколько аккаунтов заблокировано прямо сейчас. Истёкшие не считаются."""

    async def purge_blocked(self) -> int:
        """Убрать тех, кто заблокировал бота, вместе со всем, что им принадлежит."""


@runtime_checkable
class KeeperLogRepository(Protocol):
    """Журнал того, что смотрители сделали.

    Только дописывается и только читается: строку журнала нельзя ни исправить,
    ни стереть из игры, иначе он перестал бы быть тем, ради чего заведён.
    """

    async def record(self, entry: KeeperEntry) -> None: ...

    async def latest(self, *, limit: int = 20) -> tuple[KeeperEntry, ...]:
        """Последние записи, свежие сначала."""


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

    async def spend_gold(self, character_id: int, amount: int) -> bool:
        """Take gold off a character in one step, or take nothing at all.

        ``save`` writes back a whole character that was read some steps earlier,
        so between reading the purse and writing it back another update may have
        spent the same coins - and the write would put them back. A trade settles
        against a purse the payer may be spending elsewhere in that instant, which
        is the one place in the game where that gap is a hole and not a rounding
        error (Roadmap, "Риски"). ``False`` means the gold was not there.
        """

    async def grant_gold(self, character_id: int, amount: int) -> None:
        """Put gold on a character in one step, whatever else is happening to it.

        The other half of :meth:`spend_gold`: paying somebody by writing back a
        whole character read a moment ago would undo whatever that character did
        in between - a fight won, a potion bought.
        """

    async def name_taken(self, name: str) -> bool: ...

    async def arena_opponent(self, *, level: int, window: int, exclude_id: int) -> Character | None:
        """Somebody of about this level to fight a copy of, or ``None``.

        The arena is asynchronous: what comes back is a character record, and the
        fight is against a snapshot of it (``domain/rules/pvp.as_enemy``). The
        opponent is never told and never waits.
        """

    async def arena_table(self, *, limit: int = 10) -> tuple[Character, ...]:
        """The season table: most wins first."""

    async def turning_tally(self, cycle_id: str) -> Mapping[str, int]:
        """Голоса за счётный вопрос этого цикла: ответ и сколько Печатей за ним.

        Голос весит столько, сколько Оборотов совершил подавший
        (``domain/rules/turning.py``). Ответ на прошлый вопрос в этом счёте не
        участвует: цикл назван прямо в запросе.
        """

    async def find_by_name(self, name: str) -> Character | None:
        """Персонаж по имени, без учёта регистра. Имена в игре уникальны."""

    async def newest(self, *, limit: int = 8) -> tuple[Character, ...]:
        """Кого завели последними: с этого списка смотритель обычно и начинает."""

    async def census(self, *, day: int, week: int, stale: int) -> Census:
        """Игра в числах на этот момент. Границы приходят снаружи: домен без часов."""

    async def purge_abandoned(self, *, before: int) -> int:
        """Убрать персонажей, которых завели и бросили, не начав играть.

        Брошенный — это первый уровень, ноль опыта, ни одного задания обучения и
        давно не тронут. Такой персонаж занимает имя, которое кому-то нужно.
        """

    async def delete(self, character_id: int) -> bool: ...


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

    async def expire(self, *, before: int, scope: str | None = None) -> tuple[TradeRecord, ...]:
        """Close every offer made before ``before`` and return them, once each.

        The caller returns each stake to its author; returning a record twice
        would hand out the same item twice, which is why this both reads and
        writes in one step.

        ``scope`` narrows the sweep to one group, and ``None`` - the usual case -
        sweeps all of them. An offer number belongs to a group; the five minutes
        it lives do not, and a stake held by a group that has gone quiet must not
        wait for that group to speak again to come home.
        """

    async def journal(self, character_id: int, *, limit: int = 20) -> tuple[TradeRecord, ...]:
        """The latest trades this character was a side of, newest first."""


@runtime_checkable
class ContentOverlayRepository(Protocol):
    """Правки смотрителя поверх ``content/``.

    Это единственное содержимое, которое живёт в базе, и оно живёт там по той же
    причине, по которой персонаж живёт в базе: потерять его нельзя. Файлы в
    ``content/`` остаются нетронутыми, поэтому любую правку можно снять, а
    исходная строка при этом никуда не девалась (``docs/keeper.md``).
    """

    async def all(self) -> tuple[OverlayRecord, ...]:
        """Все правки, старые сначала. Читается на старте и после каждой записи."""

    async def put(self, record: OverlayRecord) -> None: ...

    async def forget(self, kind: OverlayKind, entity_id: str) -> bool:
        """Снять правку целиком. Ложь — правки и не было."""


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
