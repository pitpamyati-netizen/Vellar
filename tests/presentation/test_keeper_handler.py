"""Панель через настоящий хендлер: то, что автомат задумал, действительно случается.

Отдельный файл от ``test_keeper_panel.py``, и по важной причине. Там автомат
проверяется в чистом виде, а роль хендлера играет подделка — она записывает
правку так, как это делает он. Подделка, проверяющая саму себя, зелена и тогда,
когда настоящий хендлер не делает ничего: связка между «панель попросила» и
«хранилище изменилось» там не проверяется вовсе.

Здесь проверяется именно она. Хендлер настоящий, хранилища настоящие (те самые,
на которых игра идёт при ``APP_ENV=local``), реестр содержимого настоящий. Сеть
одна поддельная — Telegram, у которого спрашивают, читает ли человек бота.
"""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, User

from mmorpg.application.services import keeper_panel
from mmorpg.application.services.content import ContentRegistry
from mmorpg.config import Settings
from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.overlay import OverlayKind, OverlayRecord
from mmorpg.domain.ports.repositories import User as Account
from mmorpg.domain.rules.keeper import GOLD_STEP
from mmorpg.infrastructure.cache.memory import InMemoryLocationStateCache
from mmorpg.infrastructure.persistence.memory import (
    InMemoryCharacterRepository,
    InMemoryContentOverlayRepository,
    InMemoryInventoryRepository,
    InMemoryUserRepository,
)
from mmorpg.presentation.telegram.handlers import play as play_handler
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.states.screens import Play

KEEPER_ACCOUNT = 700_001
SETTINGS = Settings(_env_file=None, admin_ids=str(KEEPER_ACCOUNT))  # type: ignore[call-arg]
DAY = 24 * 60 * 60


class Recorder:
    """Стоит вместо send_screen и помнит всё, что хендлер нарисовал."""

    def __init__(self) -> None:
        self.screens: list[Screen] = []

    async def __call__(self, message: Message, screen: Screen, *, emoji: bool = False) -> None:
        self.screens.append(screen)

    @property
    def last(self) -> Screen:
        assert self.screens, "игра ответила молчанием"
        return self.screens[-1]


class FakeTelegram:
    """Telegram, у которого спрашивают одно: читает ли этот человек бота."""

    def __init__(self, refuse: set[int] | None = None, flaky: set[int] | None = None) -> None:
        self.refuse = refuse or set()
        self.flaky = flaky or set()
        self.asked: list[int] = []

    async def send_chat_action(self, *, chat_id: int, action: str) -> bool:
        self.asked.append(chat_id)
        if chat_id in self.refuse:
            raise TelegramForbiddenError(method=None, message="bot was blocked by the user")  # type: ignore[arg-type]
        if chat_id in self.flaky:
            raise TelegramRetryAfter(method=None, message="flood", retry_after=1)  # type: ignore[arg-type]
        return True


class Keeper:
    """Смотритель за настоящей панелью: одно нажатие — один вызов хендлера."""

    def __init__(self, sent: Recorder, telegram: FakeTelegram, **deps: Any) -> None:
        self.sent = sent
        self.telegram = telegram
        self.deps = deps

    async def press(self, *messages: str) -> Screen:
        for text in messages:
            message = Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=KEEPER_ACCOUNT, type="private"),
                from_user=User(id=KEEPER_ACCOUNT, is_bot=False, first_name="Смотритель"),
                text=text,
            ).as_(cast(Bot, self.telegram))
            await play_handler.play(
                message,
                self.deps["state"],
                self.deps["registry"].current,
                SETTINGS,
                self.deps["characters"],
                self.deps["inventory"],
                self.deps["users"],
                self.deps["deltas"],
                self.deps["overlays"],
                self.deps["registry"],
            )
        return self.sent.last

    def buttons(self) -> list[str]:
        return [text for row in self.sent.last.button_texts() for text in row]

    def button_with(self, needle: str) -> str:
        found = [text for text in self.buttons() if needle in text]
        assert found, f"кнопки со словом {needle!r} нет: {self.buttons()}"
        return found[0]


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorder = Recorder()
    monkeypatch.setattr(play_handler, "send_screen", recorder)
    return recorder


@pytest.fixture
def characters() -> InMemoryCharacterRepository:
    return InMemoryCharacterRepository()


@pytest.fixture
def users() -> InMemoryUserRepository:
    return InMemoryUserRepository()


@pytest.fixture
def overlays() -> InMemoryContentOverlayRepository:
    return InMemoryContentOverlayRepository()


@pytest.fixture
def registry(content: GameContent) -> ContentRegistry:
    return ContentRegistry(content)


@pytest.fixture
def telegram() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
async def keeper(
    sent: Recorder,
    content: GameContent,
    characters: InMemoryCharacterRepository,
    users: InMemoryUserRepository,
    overlays: InMemoryContentOverlayRepository,
    registry: ContentRegistry,
    telegram: FakeTelegram,
) -> Keeper:
    await characters.create(
        Character(
            id=0,
            user_id=KEEPER_ACCOUNT,
            name="Смотритель",
            race_id="human",
            class_id="warrior",
            level=5,
            is_admin=True,
        )
    )
    state = FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=KEEPER_ACCOUNT, user_id=KEEPER_ACCOUNT),
    )
    await state.set_state(Play.main_menu)
    return Keeper(
        sent,
        telegram,
        state=state,
        characters=characters,
        inventory=InMemoryInventoryRepository(),
        users=users,
        deltas=InMemoryLocationStateCache(),
        overlays=overlays,
        registry=registry,
    )


# --- правка доходит до хранилища и до мира ------------------------------


async def test_an_edit_reaches_storage_and_the_world(
    keeper: Keeper, overlays: InMemoryContentOverlayRepository, registry: ContentRegistry
) -> None:
    """Связка, которую подделка проверить не может: нажали — записалось — видно."""
    await keeper.press(labels.KEEPER.text, labels.KEEPER_WORLD.text, "Жители")
    await keeper.press(labels.KEEPER_ADD.text)
    await keeper.press(keeper.button_with("Имя"), "Довен")

    stored = await overlays.all()
    assert [record.value("name") for record in stored] == ["Довен"]
    assert registry.current.npcs_in("farhold")[0].name == "Довен"
    # Автор правки записан: потом будет видно, кто это сделал.
    assert stored[0].author_id == KEEPER_ACCOUNT


async def test_the_screen_after_an_edit_is_drawn_from_the_edited_world(keeper: Keeper) -> None:
    """Экран рисуется уже изменённым содержимым, а не тем, что было до нажатия."""
    await keeper.press(labels.KEEPER.text, labels.KEEPER_WORLD.text, "Жители")
    await keeper.press(labels.KEEPER_ADD.text)
    await keeper.press(keeper.button_with("Имя"), "Довен")

    assert "Довен" in keeper.sent.last.text()


async def test_a_half_written_edit_is_stored_and_the_screen_says_why(
    keeper: Keeper, overlays: InMemoryContentOverlayRepository, registry: ContentRegistry
) -> None:
    await keeper.press(labels.KEEPER.text, labels.KEEPER_WORLD.text, "Жители")
    await keeper.press(labels.KEEPER_ADD.text)

    assert len(await overlays.all()) == 1
    assert registry.current.npcs == ()
    assert "Пока не работает в игре:" in keeper.sent.last.text()


async def test_dropping_an_edit_removes_the_row(
    keeper: Keeper, overlays: InMemoryContentOverlayRepository, registry: ContentRegistry
) -> None:
    await keeper.press(labels.KEEPER.text, labels.KEEPER_WORLD.text, "Жители")
    await keeper.press(labels.KEEPER_ADD.text)
    await keeper.press(keeper.button_with("Имя"), "Довен")

    await keeper.press(labels.KEEPER_FORGET.text)

    assert await overlays.all() == ()
    assert registry.current.npcs == ()


async def test_the_list_after_an_edit_is_the_list_of_the_new_world(
    keeper: Keeper, registry: ContentRegistry
) -> None:
    """Экран после правки рисуется уже изменённым миром, а не тем, что был до.

    Проверяется на списке, а не на карточке: карточка знает поля из самой записи
    и осталась бы верной даже с устаревшим содержимым.
    """
    settled = len(registry.current.city("farhold").locations)
    await keeper.press(labels.KEEPER.text, labels.KEEPER_WORLD.text, "Локации")
    await keeper.press(labels.KEEPER_ADD.text)
    await keeper.press(keeper.button_with("Название"), "Брод у Ольхи")
    await keeper.press(keeper.button_with("Местность"))
    await keeper.press(keeper.button_with("1. "))
    assert len(registry.current.city("farhold").locations) == settled + 1

    # «Снять правку» возвращает на список — и список должен быть уже без неё.
    await keeper.press(labels.KEEPER_FORGET.text)

    assert keeper.sent.last.id is ScreenId.KEEPER_LIST
    assert "Брод у Ольхи" not in keeper.sent.last.text()
    assert not any("Брод у Ольхи" in text for text in keeper.buttons())
    assert len(registry.current.city("farhold").locations) == settled


async def test_rereading_the_edits_reports_how_many_there_are(
    keeper: Keeper, overlays: InMemoryContentOverlayRepository, registry: ContentRegistry
) -> None:
    """Правку положили мимо панели — кнопка её подхватывает."""
    await keeper.press(labels.KEEPER.text, labels.KEEPER_WORLD.text)
    assert registry.current.npcs == ()

    await overlays.put(
        OverlayRecord(
            kind=OverlayKind.NPC,
            entity_id="keeper_npc_7",
            fields={"name": "Мерла", "city": "farhold"},
        )
    )

    await keeper.press(labels.KEEPER_RELOAD.text)

    assert registry.current.has_npc("keeper_npc_7")
    assert "Правок перечитано: 1." in keeper.sent.last.text()


# --- чужой персонаж ----------------------------------------------------


@pytest.fixture
async def merla(characters: InMemoryCharacterRepository) -> Character:
    return await characters.create(
        Character(id=0, user_id=900_001, name="Мерла", race_id="human", class_id="warrior", level=3)
    )


async def test_a_player_is_found_by_name_and_paid(
    keeper: Keeper, characters: InMemoryCharacterRepository, merla: Character
) -> None:
    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(labels.KEEPER_FIND.text, "мерла")

    await keeper.press(labels.KEEPER_GOLD.text)

    stored = await characters.get(merla.id)
    assert stored is not None
    assert stored.gold == merla.gold + GOLD_STEP
    # Персонаж самого смотрителя не тронут.
    mine = await characters.get_active(KEEPER_ACCOUNT)
    assert mine is not None and mine.gold == 0


async def test_the_newest_players_are_listed_without_a_search(
    keeper: Keeper, merla: Character
) -> None:
    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)

    assert any("Мерла" in text for text in keeper.buttons())


async def test_a_player_is_moved_and_the_move_is_stored(
    keeper: Keeper, characters: InMemoryCharacterRepository, merla: Character
) -> None:
    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(keeper.button_with("Мерла"))
    await keeper.press(labels.KEEPER_MOVE.text)
    await keeper.press(keeper.button_with("Сурож"))

    stored = await characters.get(merla.id)
    assert stored is not None and stored.city_id != merla.city_id


async def test_deleting_a_player_takes_two_presses_and_then_really_deletes(
    keeper: Keeper, characters: InMemoryCharacterRepository, merla: Character
) -> None:
    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(keeper.button_with("Мерла"))

    await keeper.press(labels.KEEPER_DELETE.text)
    assert await characters.get(merla.id) is not None

    await keeper.press(labels.KEEPER_DELETE.text)
    assert await characters.get(merla.id) is None


# --- статистика --------------------------------------------------------


async def test_statistics_count_what_is_actually_stored(
    keeper: Keeper, characters: InMemoryCharacterRepository, merla: Character
) -> None:
    await characters.save(replace(merla, gold=140, bank_gold=60))

    await keeper.press(labels.KEEPER.text, labels.KEEPER_STATS.text)
    said = keeper.sent.last.text()

    assert "Персонажей: 2." in said
    assert "Аккаунтов: 2." in said
    assert "на руках 140" in said, said
    assert "в ячейках 60" in said, said
    assert "Мерла, уровень 3." in said


# --- уборка ------------------------------------------------------------


async def test_abandoned_characters_are_swept_and_counted(
    keeper: Keeper, characters: InMemoryCharacterRepository
) -> None:
    """Брошенный уходит, игравший остаётся, и число в ответе — настоящее."""
    abandoned = await characters.create(
        Character(id=0, user_id=900_002, name="Брошенный", race_id="human", class_id="warrior")
    )
    played = await characters.create(
        Character(
            id=0,
            user_id=900_003,
            name="Игравший",
            race_id="human",
            class_id="warrior",
            level=4,
            experience=900,
        )
    )
    old = int(time.time()) - (keeper_panel.ABANDONED_AFTER_DAYS + 1) * DAY
    for character_id in (abandoned.id, played.id):
        characters._touched[character_id] = old

    await keeper.press(labels.KEEPER.text, labels.KEEPER_SERVICE.text)
    await keeper.press(labels.KEEPER_SWEEP_DRAFTS.text)

    assert "Убрано брошенных персонажей: 1." in keeper.sent.last.text()
    assert await characters.get(abandoned.id) is None
    assert await characters.get(played.id) is not None


async def test_the_sweep_asks_telegram_and_remembers_who_refused(
    keeper: Keeper, users: InMemoryUserRepository, telegram: FakeTelegram
) -> None:
    for telegram_id in (900_010, 900_011, 900_012):
        await users.upsert(Account(telegram_id=telegram_id))
    telegram.refuse = {900_011}

    await keeper.press(labels.KEEPER.text, labels.KEEPER_SERVICE.text)
    await keeper.press(labels.KEEPER_CHECK_BLOCKED.text)

    said = keeper.sent.last.text()
    assert "Проверено аккаунтов: 3." in said, said
    assert "заблокировали бота: 1." in said
    assert telegram.asked == [900_010, 900_011, 900_012]
    assert await users.blocked_count() == 1

    # Второе нажатие тем же часом никого не переспрашивает.
    await keeper.press(labels.KEEPER_CHECK_BLOCKED.text)
    assert "Проверено аккаунтов: 0." in keeper.sent.last.text()


async def test_a_moment_of_bad_network_never_costs_anybody_their_account(
    keeper: Keeper, users: InMemoryUserRepository, telegram: FakeTelegram
) -> None:
    """Непонятная ошибка Telegram — не «заблокировал»."""
    await users.upsert(Account(telegram_id=900_020))
    telegram.flaky = {900_020}

    await keeper.press(labels.KEEPER.text, labels.KEEPER_SERVICE.text)
    await keeper.press(labels.KEEPER_CHECK_BLOCKED.text)

    assert await users.blocked_count() == 0
    assert "заблокировали бота: 0." in keeper.sent.last.text()


async def test_blocked_accounts_and_everything_they_owned_are_removed(
    keeper: Keeper,
    users: InMemoryUserRepository,
    characters: InMemoryCharacterRepository,
    telegram: FakeTelegram,
) -> None:
    await users.upsert(Account(telegram_id=900_030))
    telegram.refuse = {900_030}
    await keeper.press(labels.KEEPER.text, labels.KEEPER_SERVICE.text)
    await keeper.press(labels.KEEPER_CHECK_BLOCKED.text)

    await keeper.press(labels.KEEPER_DROP_BLOCKED.text)

    assert "Убрано заблокировавших: 1." in keeper.sent.last.text()
    assert await users.get(900_030) is None
    # Смотритель на месте: его не спрашивали, потому что он только что писал.
    assert await characters.get_active(KEEPER_ACCOUNT) is not None


# --- дверь остаётся закрытой -------------------------------------------


async def test_a_player_who_is_not_a_keeper_gets_nothing_from_the_panel(
    sent: Recorder,
    content: GameContent,
    characters: InMemoryCharacterRepository,
    users: InMemoryUserRepository,
    overlays: InMemoryContentOverlayRepository,
    registry: ContentRegistry,
    telegram: FakeTelegram,
) -> None:
    """ADMIN_IDS их не называет, поэтому флаг снимается на загрузке персонажа."""
    account = 900_100
    await characters.create(
        Character(id=0, user_id=account, name="Чужой", race_id="human", class_id="warrior")
    )
    state = FSMContext(
        storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=account, user_id=account)
    )
    await state.set_state(Play.main_menu)
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=account, type="private"),
        from_user=User(id=account, is_bot=False, first_name="Чужой"),
        text=labels.KEEPER.text,
    ).as_(cast(Bot, telegram))

    await play_handler.play(
        message,
        state,
        registry.current,
        SETTINGS,
        characters,
        InMemoryInventoryRepository(),
        users,
        InMemoryLocationStateCache(),
        overlays,
        registry,
    )

    assert sent.last.id is ScreenId.MAIN_MENU
    assert await overlays.all() == ()


# --- обычная игра ничего лишнего не читает ------------------------------


async def test_ordinary_play_costs_the_panel_nothing(
    keeper: Keeper, characters: InMemoryCharacterRepository
) -> None:
    """Ветка панели не выполняется вне панели: у игрока она бесплатна."""
    counted: list[str] = []
    original = characters.census

    async def counting(**kwargs: int) -> Any:
        counted.append("census")
        return await original(**kwargs)

    characters.census = counting  # type: ignore[method-assign]
    await keeper.press("Мир", "Дубно")

    assert counted == []
