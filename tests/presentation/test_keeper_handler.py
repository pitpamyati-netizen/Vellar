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
from mmorpg.application.services.guild import GuildStore
from mmorpg.application.services.party import PartyStore
from mmorpg.config import Settings
from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.moderation import KeeperAction
from mmorpg.domain.entities.overlay import OverlayKind, OverlayRecord
from mmorpg.domain.entities.trade import Offer, OfferKind, Party, TradeStatus
from mmorpg.domain.ports.repositories import User as Account
from mmorpg.domain.rules import overlay as overlay_rules
from mmorpg.domain.rules.keeper import GOLD_STEP
from mmorpg.infrastructure.cache.memory import (
    InMemoryLocationStateCache,
    InMemoryStateCache,
)
from mmorpg.infrastructure.persistence.memory import (
    InMemoryCharacterRepository,
    InMemoryContentOverlayRepository,
    InMemoryGuildRepository,
    InMemoryInventoryRepository,
    InMemoryKeeperLogRepository,
    InMemoryPartyRepository,
    InMemoryTradeRepository,
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
    """Смотритель за настоящей панелью: одно нажатие — один вызов хендлера.

    ``account`` — от чьего имени нажимают. По умолчанию это тот, кого назвал
    ``ADMIN_IDS``, но за панель садится и тот, кому право выдали изнутри игры, а
    ему панель показывает не то же самое.
    """

    def __init__(
        self,
        sent: Recorder,
        telegram: FakeTelegram,
        account: int = KEEPER_ACCOUNT,
        **deps: Any,
    ) -> None:
        self.sent = sent
        self.telegram = telegram
        self.account = account
        self.deps = deps

    async def press(self, *messages: str) -> Screen:
        for text in messages:
            message = Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=self.account, type="private"),
                from_user=User(id=self.account, is_bot=False, first_name="Смотритель"),
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
                self.deps["keeper_log"],
                self.deps["deltas"],
                self.deps["overlays"],
                self.deps["registry"],
                self.deps["trades"],
                self.deps["cache"],
                self.deps["parties"],
                self.deps["guilds"],
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
def keeper_log() -> InMemoryKeeperLogRepository:
    return InMemoryKeeperLogRepository()


@pytest.fixture
def registry(content: GameContent) -> ContentRegistry:
    return ContentRegistry(content)


@pytest.fixture
def trades() -> InMemoryTradeRepository:
    return InMemoryTradeRepository()


@pytest.fixture
def cache() -> InMemoryStateCache:
    return InMemoryStateCache()


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
    keeper_log: InMemoryKeeperLogRepository,
    registry: ContentRegistry,
    trades: InMemoryTradeRepository,
    telegram: FakeTelegram,
    cache: InMemoryStateCache,
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
        keeper_log=keeper_log,
        deltas=InMemoryLocationStateCache(),
        overlays=overlays,
        registry=registry,
        trades=trades,
        cache=cache,
        parties=PartyStore(InMemoryPartyRepository(), cache),
        guilds=GuildStore(InMemoryGuildRepository(), cache),
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


# --- черты, ремёсла и рецепты ----------------------------------------


def _trait_field(key: str) -> str:
    spec = next(s for s in overlay_rules.FIELDS[OverlayKind.TRAIT] if s.key == key)
    return spec.name


async def test_a_trait_bonus_is_typed_in_as_a_pair(
    keeper: Keeper, registry: ContentRegistry, content: GameContent
) -> None:
    """Поле «ключ=число»: набранная пара ложится, не заменяя прибавки целиком."""
    before = dict(content.trait("berserker").modifiers)
    await keeper.press(labels.KEEPER.text, labels.KEEPER_WORLD.text, "Черты")
    await keeper.press(keeper.button_with(content.trait("berserker").name))
    await keeper.press(keeper.button_with(_trait_field("modifiers")))
    await keeper.press("crit_chance_percent=7")

    mods = dict(registry.current.trait("berserker").modifiers)
    assert mods["crit_chance_percent"] == 7
    # Старые прибавки на месте: снимок их пред­заполнил, пара их не стёрла.
    assert mods["damage_percent"] == before["damage_percent"]


async def test_a_pair_is_taken_off_by_pressing_its_row(
    keeper: Keeper, registry: ContentRegistry, content: GameContent
) -> None:
    await keeper.press(labels.KEEPER.text, labels.KEEPER_WORLD.text, "Черты")
    await keeper.press(keeper.button_with(content.trait("berserker").name))
    await keeper.press(keeper.button_with(_trait_field("modifiers")))
    await keeper.press("armor_percent=3")
    assert registry.current.trait("berserker").modifiers.get("armor_percent") == 3

    await keeper.press(keeper.button_with("armor_percent = 3"))

    assert "armor_percent" not in registry.current.trait("berserker").modifiers


async def test_a_recipe_is_created_and_its_composition_typed_in(
    keeper: Keeper,
    overlays: InMemoryContentOverlayRepository,
    registry: ContentRegistry,
    content: GameContent,
) -> None:
    inputs_field = next(s for s in overlay_rules.FIELDS[OverlayKind.RECIPE] if s.key == "inputs")
    output_field = next(s for s in overlay_rules.FIELDS[OverlayKind.RECIPE] if s.key == "output")
    out_name = content.item("iron_scrap").name

    await keeper.press(labels.KEEPER.text, labels.KEEPER_WORLD.text, "Рецепты")
    await keeper.press(labels.KEEPER_ADD.text)
    await keeper.press(keeper.button_with(inputs_field.name), "iron_scrap=2")
    # Поле «ключ=число» не разматывается само — за парой набирают следующую.
    await keeper.press(labels.BACK.text)
    await keeper.press(keeper.button_with(output_field.name))
    await keeper.press(keeper.button_with(out_name))

    stored = [record for record in await overlays.all() if record.kind is OverlayKind.RECIPE]
    assert stored and stored[0].pairs("inputs") == (("iron_scrap", "2"),)
    made = next(r for r in registry.current.recipes_of("smithing") if r.id == stored[0].entity_id)
    assert made.output_id == "iron_scrap"


async def test_a_keeper_tunes_a_meta_number_from_the_panel(
    keeper: Keeper, overlays: InMemoryContentOverlayRepository, registry: ContentRegistry
) -> None:
    """Опорные числа — карточка на одну сущность: править, но не заводить и не убирать."""
    before = registry.current.rules.stat_points_per_level
    field = next(
        s for s in overlay_rules.FIELDS[OverlayKind.META] if s.key == "stat_points_per_level"
    )

    await keeper.press(labels.KEEPER.text, labels.KEEPER_WORLD.text, "Опорные числа")
    await keeper.press(keeper.button_with("Опорные числа игры"))
    assert not any(labels.KEEPER_ADD.matches(text) for text in keeper.buttons())
    assert not any(labels.KEEPER_REMOVE.matches(text) for text in keeper.buttons())

    await keeper.press(keeper.button_with(field.name), str(before + 2))

    assert registry.current.rules.stat_points_per_level == before + 2
    stored = [record for record in await overlays.all() if record.kind is OverlayKind.META]
    assert stored and stored[0].value("stat_points_per_level") == str(before + 2)


# --- чужой персонаж ----------------------------------------------------


@pytest.fixture
async def merla(characters: InMemoryCharacterRepository) -> Character:
    return await characters.create(
        Character(id=0, user_id=900_001, name="Мерла", race_id="human", class_id="warrior", level=3)
    )


@pytest.fixture
async def argus(characters: InMemoryCharacterRepository) -> Character:
    return await characters.create(
        Character(id=0, user_id=900_002, name="Аргус", race_id="human", class_id="warrior", level=4)
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


async def test_a_warning_is_counted_on_the_account_and_written_down(
    keeper: Keeper,
    users: InMemoryUserRepository,
    keeper_log: InMemoryKeeperLogRepository,
    merla: Character,
) -> None:
    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(keeper.button_with("Мерла"))

    await keeper.press(labels.KEEPER_WARN.text)
    account = await users.get(merla.user_id)
    assert account is not None and account.warnings == 1
    assert (await keeper_log.latest())[0].action == KeeperAction.WARN

    # Снять — счётчик вернулся к нулю, и кнопки «Снять предупреждение» больше нет.
    card = await keeper.press(labels.KEEPER_UNWARN.text)
    account = await users.get(merla.user_id)
    assert account is not None and account.warnings == 0
    assert "Предупреждений: 0" in card.text()
    assert not any(labels.KEEPER_UNWARN.matches(text) for text in keeper.buttons())


async def test_deleting_a_player_takes_two_presses_and_then_really_deletes(
    keeper: Keeper, characters: InMemoryCharacterRepository, merla: Character
) -> None:
    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(keeper.button_with("Мерла"))

    await keeper.press(labels.KEEPER_DELETE.text)
    assert await characters.get(merla.id) is not None

    await keeper.press(labels.KEEPER_DELETE.text)
    assert await characters.get(merla.id) is None


async def test_a_guild_member_is_taken_out_from_the_card_and_it_reaches_storage(
    keeper: Keeper, characters: InMemoryCharacterRepository, merla: Character
) -> None:
    from mmorpg.domain.rules.guild import GuildRank

    argus = await characters.create(
        Character(id=0, user_id=900_002, name="Аргус", race_id="human", class_id="warrior", level=3)
    )
    guilds = keeper.deps["guilds"]
    guild = await guilds.create("Клинки", merla.id)
    await guilds.save(guild.with_member(argus.id, GuildRank.MEMBER))

    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(keeper.button_with("Мерла"))
    await keeper.press(labels.KEEPER_GUILD_BTN.text)
    await keeper.press(keeper.button_with("Вывести"))

    stored = await guilds.of(merla.id)
    assert stored is not None and not stored.has(argus.id)


async def test_a_party_is_disbanded_from_the_card_and_it_reaches_storage(
    keeper: Keeper, characters: InMemoryCharacterRepository, merla: Character
) -> None:
    parties = keeper.deps["parties"]
    await parties.create(merla.id)

    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(keeper.button_with("Мерла"))
    await keeper.press(labels.KEEPER_PARTY_BTN.text)
    await keeper.press(labels.KEEPER_PARTY_DISBAND.text)

    assert await parties.of(merla.id) is None


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


# --- само право --------------------------------------------------------


async def _seat(keeper: Keeper, account: int) -> Keeper:
    """Тот же бот и те же хранилища, но за панелью другой человек."""
    state = FSMContext(
        storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=account, user_id=account)
    )
    await state.set_state(Play.main_menu)
    return Keeper(keeper.sent, keeper.telegram, account, **{**keeper.deps, "state": state})


async def test_the_right_is_handed_out_and_lands_on_the_account(
    keeper: Keeper,
    users: InMemoryUserRepository,
    characters: InMemoryCharacterRepository,
    merla: Character,
) -> None:
    """Выдача права: она пишется аккаунту, а персонажу — только зеркало."""
    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(keeper.button_with("Мерла"))
    assert "Права смотрителя: нет." in keeper.sent.last.text()

    await keeper.press(labels.KEEPER_PROMOTE.text)

    assert "Мерла теперь смотритель." in keeper.sent.last.text()
    account = await users.get(merla.user_id)
    assert account is not None and account.keeper is True
    stored = await characters.get(merla.id)
    assert stored is not None and stored.is_admin is True
    # Карточка сразу показывает новое положение дел, и кнопка на ней обратная.
    assert "Права смотрителя: есть." in keeper.sent.last.text()
    assert labels.KEEPER_DEMOTE.text in keeper.buttons()


async def test_the_right_is_taken_back_the_same_way(
    keeper: Keeper,
    users: InMemoryUserRepository,
    characters: InMemoryCharacterRepository,
    merla: Character,
) -> None:
    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(keeper.button_with("Мерла"))
    await keeper.press(labels.KEEPER_PROMOTE.text)

    await keeper.press(labels.KEEPER_DEMOTE.text)

    assert "Мерла больше не смотритель." in keeper.sent.last.text()
    account = await users.get(merla.user_id)
    assert account is not None and account.keeper is False
    stored = await characters.get(merla.id)
    assert stored is not None and stored.is_admin is False


async def test_a_keeper_who_got_the_right_from_the_panel_sees_the_panel(
    keeper: Keeper, merla: Character
) -> None:
    """Выданное право работает: панель у неё открывается с того же нажатия."""
    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(keeper.button_with("Мерла"))
    await keeper.press(labels.KEEPER_PROMOTE.text)

    second = await _seat(keeper, merla.user_id)
    await second.press(labels.KEEPER.text)

    assert second.sent.last.id is ScreenId.KEEPER


async def test_a_keeper_who_got_the_right_cannot_pass_it_on(
    keeper: Keeper,
    users: InMemoryUserRepository,
    characters: InMemoryCharacterRepository,
    merla: Character,
) -> None:
    """Ни кнопки, ни строки — и набранная руками надпись ничего не даёт."""
    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(keeper.button_with("Мерла"))
    await keeper.press(labels.KEEPER_PROMOTE.text)
    other = await characters.create(
        Character(id=0, user_id=900_500, name="Тишь", race_id="human", class_id="warrior")
    )

    second = await _seat(keeper, merla.user_id)
    await second.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await second.press(second.button_with("Тишь"))
    said = second.sent.last.text()
    assert labels.KEEPER_PROMOTE.text not in second.buttons()
    assert "Права смотрителя" not in said

    await second.press(labels.KEEPER_PROMOTE.text)

    assert "Нажмите кнопку панели." in second.sent.last.text()
    account = await users.get(other.user_id)
    assert account is None or account.keeper is False


async def test_the_right_from_the_setting_is_not_taken_away_from_the_panel(
    keeper: Keeper, characters: InMemoryCharacterRepository
) -> None:
    """Оно живёт в окружении: снять его отсюда значило бы соврать экрану."""
    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(labels.KEEPER_FIND.text, "смотритель")
    said = keeper.sent.last.text()
    assert "Права смотрителя: есть, из настройки." in said
    assert labels.KEEPER_DEMOTE.text not in keeper.buttons()

    await keeper.press(labels.KEEPER_DEMOTE.text)

    mine = await characters.get_active(KEEPER_ACCOUNT)
    assert mine is not None and mine.is_admin is True


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
        InMemoryKeeperLogRepository(),
        InMemoryLocationStateCache(),
        overlays,
        registry,
        InMemoryTradeRepository(),
        InMemoryStateCache(),
        PartyStore(InMemoryPartyRepository(), InMemoryStateCache()),
        GuildStore(InMemoryGuildRepository(), InMemoryStateCache()),
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


# --- блокировка --------------------------------------------------------


async def test_a_ban_lands_on_the_account_and_is_written_down(
    keeper: Keeper,
    users: InMemoryUserRepository,
    keeper_log: InMemoryKeeperLogRepository,
    merla: Character,
) -> None:
    """Наказание доходит до базы, а не только до экрана."""
    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(keeper.button_with("Мерла"))
    assert "Блокировка: нет." in keeper.sent.last.text()

    await keeper.press(labels.KEEPER_BAN.text, labels.KEEPER_REASON.text, "обман в сделке")
    await keeper.press("На сутки")

    account = await users.get(merla.user_id)
    assert account is not None
    assert account.ban.reason == "обман в сделке"
    assert account.ban.until > int(time.time())
    # Карточка после наказания говорит о нём, и кнопка на ней обратная.
    assert "Блокировка: есть" in keeper.sent.last.text()
    assert labels.KEEPER_UNBAN.text in keeper.buttons()

    written = await keeper_log.latest()
    assert [entry.action for entry in written] == [KeeperAction.BAN]
    assert written[0].target == "Мерла"
    assert written[0].keeper_name == "Смотритель"


async def test_a_ban_is_lifted_and_that_is_written_down_too(
    keeper: Keeper,
    users: InMemoryUserRepository,
    keeper_log: InMemoryKeeperLogRepository,
    merla: Character,
) -> None:
    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(keeper.button_with("Мерла"))
    await keeper.press(labels.KEEPER_BAN.text, "Навсегда")

    await keeper.press(labels.KEEPER_UNBAN.text)

    account = await users.get(merla.user_id)
    assert account is not None and account.ban.until == 0
    assert [entry.action for entry in await keeper_log.latest()] == [
        KeeperAction.UNBAN,
        KeeperAction.BAN,
    ]


async def test_the_journal_shows_what_was_done_and_by_whom(
    keeper: Keeper, keeper_log: InMemoryKeeperLogRepository, merla: Character
) -> None:
    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(keeper.button_with("Мерла"))
    await keeper.press(labels.KEEPER_GOLD.text)

    screen = await keeper.press(labels.MAIN_MENU.text, labels.KEEPER.text, labels.KEEPER_LOG.text)

    assert screen.id is ScreenId.KEEPER_LOG
    assert "Смотритель выдал золото Мерла" in screen.text()
    assert (await keeper_log.latest())[0].keeper_id == KEEPER_ACCOUNT


async def test_the_journal_from_a_player_card_is_scoped_to_that_player(
    keeper: Keeper, merla: Character, argus: Character
) -> None:
    # Правим двоих, потом открываем журнал с карточки одного.
    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(keeper.button_with("Мерла"))
    await keeper.press(labels.KEEPER_GOLD.text)
    await keeper.press(labels.MAIN_MENU.text, labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(keeper.button_with("Аргус"))
    await keeper.press(labels.KEEPER_HEAL.text)

    scoped = await keeper.press(labels.KEEPER_PLAYER_LOG.text)

    assert scoped.id is ScreenId.KEEPER_LOG
    assert "по цели «Аргус»" in scoped.text()
    assert "залечил раны" in scoped.text()
    assert "выдал золото" not in scoped.text()


async def test_the_panel_counts_who_is_banned(
    keeper: Keeper, users: InMemoryUserRepository, merla: Character
) -> None:
    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(keeper.button_with("Мерла"))
    await keeper.press(labels.KEEPER_BAN.text, "На неделю")

    screen = await keeper.press(labels.MAIN_MENU.text, labels.KEEPER.text, labels.KEEPER_STATS.text)

    assert "Заблокировано смотрителем: 1." in screen.text()


# --- откат сделки ------------------------------------------------------


async def a_settled_sale(
    trades: InMemoryTradeRepository,
    inventory: InMemoryInventoryRepository,
    seller: Character,
    buyer: Character,
) -> int:
    """Расчёт, который уже прошёл: у покупателя вещь, у продавца золото."""
    record = await trades.open(
        Offer(
            number=0,
            kind=OfferKind.SELL,
            author=Party(user_id=seller.user_id, character_id=seller.id, name=seller.name),
            target=Party(user_id=buyer.user_id, character_id=buyer.id, name=buyer.name),
            item_id="sword@1#common",
            item_name="Ветхий меч",
            price=100,
        ),
        scope="group",
    )
    assert record is not None
    closed = await trades.close(
        record.number, scope="group", status=TradeStatus.ACCEPTED, settled_at=1, tax=5
    )
    assert closed is not None
    await inventory.add(buyer.id, "sword@1#common", 1)
    return record.id


async def test_a_keeper_rolls_a_trade_back_and_it_is_written_down(
    keeper: Keeper,
    characters: InMemoryCharacterRepository,
    trades: InMemoryTradeRepository,
    keeper_log: InMemoryKeeperLogRepository,
    merla: Character,
) -> None:
    """Сквозь всю панель: нажали дважды - вещь и золото вернулись, запись есть."""
    seller = await characters.create(
        Character(id=0, user_id=900_002, name="Аргус", race_id="human", class_id="warrior", gold=95)
    )
    inventory = keeper.deps["inventory"]
    await a_settled_sale(trades, inventory, seller, merla)

    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(keeper.button_with("Мерла"), labels.KEEPER_TRADES.text)
    row = keeper.button_with("Ветхий меч")
    armed = await keeper.press(row)
    assert "ещё раз" in armed.text()
    # Пока не подтвердили - ничего не двинулось.
    assert await inventory.count(merla.id, "sword@1#common") == 1

    screen = await keeper.press(row)

    assert "Вещь вернулась" in screen.text()
    assert await inventory.count(seller.id, "sword@1#common") == 1
    assert await inventory.count(merla.id, "sword@1#common") == 0
    paid = await characters.get(merla.id)
    assert paid is not None and paid.gold == merla.gold + 95
    written = await keeper_log.latest()
    assert [entry.action for entry in written] == [KeeperAction.ROLLBACK]
    assert written[0].target == "Мерла"


async def test_a_trade_nobody_settled_is_not_rolled_back(
    keeper: Keeper,
    characters: InMemoryCharacterRepository,
    trades: InMemoryTradeRepository,
    merla: Character,
) -> None:
    """Предложение, по которому расчёта не было, откатывать нечего."""
    seller = await characters.create(
        Character(id=0, user_id=900_003, name="Борх", race_id="human", class_id="warrior")
    )
    record = await trades.open(
        Offer(
            number=0,
            kind=OfferKind.SELL,
            author=Party(user_id=seller.user_id, character_id=seller.id, name=seller.name),
            target=Party(user_id=merla.user_id, character_id=merla.id, name=merla.name),
            item_id="sword@1#common",
            item_name="Ветхий меч",
            price=100,
        ),
        scope="group",
    )
    assert record is not None

    await keeper.press(labels.KEEPER.text, labels.KEEPER_PLAYERS.text)
    await keeper.press(keeper.button_with("Мерла"), labels.KEEPER_TRADES.text)
    screen = await keeper.press(keeper.button_with("Ветхий меч"))

    assert "расчёт не проходил" in screen.text()
