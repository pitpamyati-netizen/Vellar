"""Порты: что домену нужно от внешнего мира.

Здесь только объявления ``typing.Protocol`` - никакой реализации и никаких
импортов asyncpg или redis. Каждому из них отвечают две реализации: на
PostgreSQL и Redis для dev и prod и на памяти для ``APP_ENV=local`` и набора
тестов (``docs/adr/0005-in-memory-adapters.md``).

Это единственное место в ``domain/``, где встречается ``async def``: порт
описывает границу с внешним миром, а всё за этой границей асинхронно. Сами
правила и сущности остаются синхронными и чистыми.
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
from mmorpg.domain.rules.guild import Guild
from mmorpg.domain.rules.party import Party


@dataclass(frozen=True, slots=True)
class AccessibilitySettings:
    """Как игрок просит показывать ему игру.

    Значки **выключены по умолчанию** - правило доступности 6.
    """

    emoji: bool = False
    verbose: bool = True
    page_size: int = 8


@dataclass(frozen=True, slots=True)
class User:
    """Пользователь Telegram, отдельно от его персонажей.

    ``keeper`` - право, выданное изнутри игры. Оно лежит на аккаунте, а не на
    персонаже, по той же причине, что и чёрный список: право, которое обходится
    заведением второго персонажа, правом не является. Право, идущее из
    ``ADMIN_IDS``, здесь не хранится вовсе - оно читается из окружения каждый раз
    (``docs/keeper.md``).
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
    """Что игрок показывает в группе и с кем отказывается иметь дело.

    И то и другое лежит на аккаунте, а не на персонаже: чёрный список, который
    обходится заведением второго персонажа, списком не является (Roadmap 2.5).
    Игрок, который ничего из этого не трогал, открыт всем, поэтому аккаунт без
    строки отвечает «виден, не блокирует никого».
    """

    async def profile_visible(self, telegram_id: int) -> bool: ...

    async def set_profile_visible(self, telegram_id: int, visible: bool) -> None: ...

    async def blocks(self, telegram_id: int, other_id: int) -> bool:
        """Занёс ли ``telegram_id`` игрока ``other_id`` в свой чёрный список."""

    async def block(self, telegram_id: int, other_id: int, *, at: int) -> bool:
        """Занести в чёрный список. False, если он там уже был."""

    async def unblock(self, telegram_id: int, other_id: int) -> bool:
        """Убрать из чёрного списка. False, если его там не было."""


@runtime_checkable
class CharacterRepository(Protocol):
    async def get(self, character_id: int) -> Character | None: ...

    async def get_active(self, telegram_id: int) -> Character | None: ...

    async def list_for_user(self, telegram_id: int) -> tuple[Character, ...]: ...

    async def create(self, character: Character) -> Character: ...

    async def save(self, character: Character) -> None: ...

    async def spend_gold(self, character_id: int, amount: int) -> bool:
        """Снять золото с персонажа одним шагом или не снять ничего вовсе.

        ``save`` записывает обратно персонажа целиком, прочитанного несколько шагов
        назад, поэтому между чтением кошелька и обратной записью другое обновление могло
        истратить те же монеты — и запись вернула бы их на место. Сделка закрывается
        против кошелька, который плательщик в это мгновение, возможно, тратит в другом
        месте, а это единственное место в игре, где такой зазор — дыра, а не погрешность
        округления. ``False`` значит, что золота там не было.
        """

    async def grant_gold(self, character_id: int, amount: int) -> None:
        """Положить золото персонажу одним шагом, что бы с ним ни происходило.

        Вторая половина :meth:`spend_gold`: заплатить кому-то, записав обратно
        персонажа, прочитанного мгновением раньше, значило бы отменить всё, что этот
        персонаж успел сделать между делом, - выигранный бой, купленное зелье.
        """

    async def name_taken(self, name: str) -> bool: ...

    async def arena_opponent(self, *, level: int, window: int, exclude_id: int) -> Character | None:
        """Кто-то примерно этого уровня, с чьей копией можно подраться, или ``None``.

        Арена асинхронна: обратно приходит запись персонажа, и этим персонажем в бою
        играет движок (``domain/rules/combat``). Противнику не сообщают ничего, и он
        ничего не ждёт - но дерётся он своим оружием и своими умениями, а не
        выдуманным числом (ADR 0021).
        """

    async def arena_table(self, *, limit: int = 10) -> tuple[Character, ...]:
        """Таблица сезона: больше побед - выше."""

    async def turning_tally(self, cycle_id: str) -> Mapping[str, int]:
        """Голоса за голосование этого цикла: ответ и сколько Печатей за ним.

        Голос весит столько, сколько перерождений совершил подавший
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

        Брошенный — это первый уровень, ноль опыта, ни одного шага обучения и
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
    """Стоящие предложения и журнал всего, чем они кончились.

    Это единственная часть групповой экономики, которой нельзя жить в кэше: пока
    предложение стоит, в нём держится вещь или золото автора, а хранилище,
    истекающее само, тихо проглотило бы и то и другое (Roadmap 2.3).

    ``close`` - ворота, которые делают сделку неделимой. Строка меняется только
    пока она ещё стоит, и возвращается то, что изменилось, поэтому двое, ответившие
    на одно предложение в одну секунду, дают ровно одно закрытие: проигравший
    получает обратно ``None`` и не двигает ничего.
    """

    async def open(self, offer: Offer, *, scope: str) -> TradeRecord | None:
        """Объявить предложение, выдав ему короткий номер, который будут набирать игроки.

        ``None`` значит, что свободного номера не нашлось: в группе стоит 999
        предложений.
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
        """Закрыть все предложения, сделанные раньше ``before``, и вернуть их по разу.

        Вызывающий возвращает каждую ставку её автору; вернуть запись дважды значило бы
        выдать одну и ту же вещь дважды, поэтому здесь и читают, и пишут одним шагом.

        ``scope`` сужает уборку до одной группы, а ``None`` - обычный случай - убирает
        во всех. Номер предложения принадлежит группе, а пять минут его жизни - нет, и
        ставка, которую держит затихшая группа, не должна ждать, пока эта группа снова
        заговорит.
        """

    async def journal(self, character_id: int, *, limit: int = 20) -> tuple[TradeRecord, ...]:
        """Последние сделки, стороной которых был этот персонаж, свежие сверху."""

    async def revert(self, trade_id: int) -> TradeRecord | None:
        """Пометить закрытую сделку откаченной и вернуть её как была. ``None`` - не была.

        Те же ворота, что у ``close``, и по той же причине: откатить можно только
        действительно закрывшуюся сделку и только один раз, поэтому двое смотрителей,
        нажавших кнопку вместе, двинут товар один раз на двоих. Двигать его обратно -
        работа вызывающего (``application/services/group_trade.roll_back``).

        Время закрытия не перетирается, а время отката здесь не держится: это дело
        журнала смотрителя, вместе с тем, кто именно откатил
        (``domain/entities/moderation.KeeperEntry``).
        """


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
class PartyRepository(Protocol):
    """Состав отряда: кто с кем идёт вместе.

    Отряд лежит в базе по той же причине, что персонаж: постоянный состав нельзя
    терять между заходами (ADR 0029). Приглашения - другое дело: они висят в
    кэше со сроком, потому что зов, который нельзя ни принять, ни отменить, хуже,
    чем никакого (``Claude.md``, правило 8).
    """

    async def of(self, character_id: int) -> Party | None:
        """Отряд, в котором стоит этот персонаж. ``None`` - он сам по себе."""

    async def by_leader(self, leader_id: int) -> Party | None: ...

    async def save(self, party: Party) -> None:
        """Записать состав. Собравший всегда в составе (``Party.__post_init__``)."""

    async def disband(self, leader_id: int) -> None:
        """Убрать отряд целиком."""


@runtime_checkable
class GuildRepository(Protocol):
    """Гильдия: имя, состав со званиями и общая казна (ADR 0030).

    Лежит в базе, как персонаж: гильдию нельзя терять между заходами. Казна
    двигается условным ``UPDATE`` — ``deposit`` не спорит ни с кем, ``withdraw``
    не уходит в минус, даже если два офицера нажали разом.
    """

    async def of(self, character_id: int) -> Guild | None:
        """Гильдия, в которой состоит этот персонаж. ``None`` — ни в какой."""

    async def by_id(self, guild_id: int) -> Guild | None: ...

    async def by_name(self, name: str) -> Guild | None:
        """Поиск по имени без учёта регистра — для проверки занятости и зова."""

    async def create(self, name: str, founder_id: int) -> Guild:
        """Завести гильдию. Основатель сразу в составе со званием основателя."""

    async def save(self, guild: Guild) -> None:
        """Записать имя и состав со званиями. Казну этим не трогают."""

    async def disband(self, guild_id: int) -> None: ...

    async def deposit(self, guild_id: int, amount: int) -> None:
        """Положить в казну. Всегда проходит."""

    async def withdraw(self, guild_id: int, amount: int) -> bool:
        """Взять из казны. Ложь — в казне столько не было."""


@runtime_checkable
class LocationStateCache(Protocol):
    """Общее состояние локации: что осталось в узлах и кто в ней ходит.

    Локация - общая земля. Сама карта хранения не требует вовсе - она чистая
    функция от места и номера поколения; общим остаётся то, сколько от волны
    каждого узла ещё стоит и кто по локации ходит.
    Ничто из этого не источник истины: потерянный Redis наполняет каждый узел
    заново и забывает, кто где был, а стоит это прогулки и никогда не персонажа
    (``docs/procgen.md``).
    """

    async def state(self, city_id: str, slot: int, *, now: int) -> LocationState:
        """Каждый узел этой локации в том виде, в каком он стоит на ``now``.

        Узлы, у которых подошёл срок наполнения, по дороге наружу переводятся в
        следующую волну, чтобы вызывающему не приходилось спрашивать дважды.
        """

    async def take(
        self, city_id: str, slot: int, node: int, *, wave: int, size: int, now: int, ttl: int
    ) -> LocationState:
        """Вынуть из узла одно: убитую стаю, горсть собранного.

        ``wave`` - волна, которую видел вызывающий. Нажатие, пришедшее после того, как
        узел уже перевернулся, принадлежит ушедшей волне и не меняет ничего - именно
        это и не даёт двоим, вычищающим последнюю стаю разом, вычистить её дважды.
        """

    async def arrive(
        self, city_id: str, slot: int, presence: Presence, *, now: int, ttl: int
    ) -> None: ...

    async def leave(self, city_id: str, slot: int, character_id: int) -> None: ...

    async def others_at(
        self, city_id: str, slot: int, node: int, *, exclude: int, now: int, ttl: int
    ) -> tuple[Presence, ...]:
        """Кто ещё стоит на этом узле прямо сейчас, свежие сверху."""


@runtime_checkable
class StateCache(Protocol):
    """Короткоживущие JSON-записи: начатый бой, текущий экран, прилавок лавки."""

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl: int) -> None: ...

    async def delete(self, key: str) -> None: ...


@runtime_checkable
class IdempotencyStore(Protocol):
    """Отбрасывает повторное обновление Telegram, чтобы оно не сработало дважды."""

    async def seen(self, update_id: int, ttl: int = 300) -> bool: ...
