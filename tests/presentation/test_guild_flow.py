"""Гильдия, прогнанная через настоящий хендлер (ADR 0030).

Гильдию основывают кнопкой, зовут в неё именем, соглашаются сами; звания раздаёт
основатель, а казна двигается по званию. Здесь проверяется вся связка: главное
меню - экран гильдии - грамота - зов - согласие - казна, - потому что гильдия
лежит в базе, а не в состоянии игрока.

Никакой сети: хендлер зовётся напрямую, а его одно сообщение на шаг
перехватывается.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, User

from mmorpg.application.services.content import ContentRegistry
from mmorpg.application.services.guild import GuildStore
from mmorpg.application.services.party import PartyStore
from mmorpg.config import Settings
from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.rules.guild import FOUND_COST, GuildRank
from mmorpg.infrastructure.cache.memory import InMemoryLocationStateCache, InMemoryStateCache
from mmorpg.infrastructure.persistence.memory import (
    InMemoryCharacterRepository,
    InMemoryContentOverlayRepository,
    InMemoryGuildRepository,
    InMemoryInventoryRepository,
    InMemoryKeeperLogRepository,
    InMemoryPartyRepository,
    InMemoryPrivacyRepository,
    InMemoryTradeRepository,
    InMemoryUserRepository,
)
from mmorpg.presentation.telegram.handlers import play as play_handler
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId

ARGUS_ACCOUNT = 620_001
MIRNA_ACCOUNT = 620_002
SETTINGS = Settings(_env_file=None, shop_rotation_seconds=10**9)  # type: ignore[call-arg]


class Recorder:
    def __init__(self) -> None:
        self.screens: list[Screen] = []

    async def __call__(self, message: Message, screen: Screen, *, emoji: bool = False) -> None:
        self.screens.append(screen)

    @property
    def last(self) -> Screen:
        assert self.screens, "the game answered with silence"
        return self.screens[-1]


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorder = Recorder()
    monkeypatch.setattr(play_handler, "send_screen", recorder)
    return recorder


@pytest.fixture
def characters() -> InMemoryCharacterRepository:
    return InMemoryCharacterRepository()


@pytest.fixture
def cache() -> InMemoryStateCache:
    return InMemoryStateCache()


@pytest.fixture
def registry(content: GameContent) -> ContentRegistry:
    return ContentRegistry(content)


@pytest.fixture
def guilds(cache: InMemoryStateCache) -> GuildStore:
    return GuildStore(InMemoryGuildRepository(), cache)


def a_message(account: int, text: str) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=account, type="private"),
        from_user=User(id=account, is_bot=False, first_name="Игрок"),
        text=text,
    )


class Player:
    def __init__(self, account: int, sent: Recorder, **deps: Any) -> None:
        self.account = account
        self.sent = sent
        self.deps = deps
        self.state = FSMContext(
            storage=MemoryStorage(),
            key=StorageKey(bot_id=1, chat_id=account, user_id=account),
        )

    async def press(self, text: str) -> Screen:
        await play_handler.play(
            a_message(self.account, text),
            self.state,
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
            self.deps["privacy"],
        )
        return self.sent.last


@pytest.fixture
async def table(
    characters: InMemoryCharacterRepository,
    registry: ContentRegistry,
    cache: InMemoryStateCache,
    guilds: GuildStore,
    sent: Recorder,
) -> tuple[Player, Player, Character, Character]:
    deps: dict[str, Any] = {
        "characters": characters,
        "inventory": InMemoryInventoryRepository(),
        "users": InMemoryUserRepository(),
        "keeper_log": InMemoryKeeperLogRepository(),
        "deltas": InMemoryLocationStateCache(),
        "overlays": InMemoryContentOverlayRepository(),
        "registry": registry,
        "trades": InMemoryTradeRepository(),
        "cache": cache,
        "parties": PartyStore(InMemoryPartyRepository(), cache),
        "guilds": guilds,
        "privacy": InMemoryPrivacyRepository(),
    }
    argus = await characters.create(
        Character(
            id=0,
            user_id=ARGUS_ACCOUNT,
            name="Аргус",
            race_id="human",
            class_id="warrior",
            level=20,
            gold=2_000,
        )
    )
    mirna = await characters.create(
        Character(
            id=0,
            user_id=MIRNA_ACCOUNT,
            name="Мирна",
            race_id="human",
            class_id="mage",
            level=20,
            gold=1_000,
        )
    )
    return (
        Player(ARGUS_ACCOUNT, sent, **deps),
        Player(MIRNA_ACCOUNT, sent, **deps),
        argus,
        mirna,
    )


def buttons(screen: Screen) -> set[str]:
    return {one.text for row in screen.rows for one in row}


async def _found(argus: Player, name: str = "Ирисы") -> Screen:
    await argus.press(labels.GUILD.text)
    await argus.press(labels.GUILD_FOUND.text)
    return await argus.press(name)


async def test_the_main_menu_leads_to_the_guild(
    table: tuple[Player, Player, Character, Character],
) -> None:
    argus, _, _, _ = table
    menu = await argus.press("/меню")
    assert labels.GUILD.text in buttons(menu)
    screen = await argus.press(labels.GUILD.text)
    assert screen.id is ScreenId.GUILD
    assert buttons(screen) == {labels.GUILD_FOUND.text}


async def test_a_guild_is_founded_for_gold_and_the_founder_is_its_founder(
    table: tuple[Player, Player, Character, Character],
    characters: InMemoryCharacterRepository,
    guilds: GuildStore,
) -> None:
    argus, _, argus_character, _ = table
    made = await _found(argus)
    assert "основана" in made.text()

    stored = await characters.get(argus_character.id)
    assert stored is not None and stored.gold == argus_character.gold - FOUND_COST

    guild = await guilds.of(argus_character.id)
    assert guild is not None
    assert guild.rank_of(argus_character.id) is GuildRank.FOUNDER
    assert guild.name == "Ирисы"


async def test_a_short_name_is_refused_and_costs_nothing(
    table: tuple[Player, Player, Character, Character],
    characters: InMemoryCharacterRepository,
    guilds: GuildStore,
) -> None:
    argus, _, argus_character, _ = table
    refused = await _found(argus, "ы")
    assert "знаков" in refused.text()
    assert await guilds.of(argus_character.id) is None
    stored = await characters.get(argus_character.id)
    assert stored is not None and stored.gold == argus_character.gold


async def test_an_invite_is_answered_by_the_one_who_was_called_and_ranks_flow(
    table: tuple[Player, Player, Character, Character],
    guilds: GuildStore,
) -> None:
    argus, mirna, _argus_character, mirna_character = table
    await _found(argus)

    await argus.press(labels.GUILD_INVITE.text)
    called = await argus.press("Мирна")
    assert "Зов отправлен: Мирна" in called.text()

    waiting = await mirna.press(labels.GUILD.text)
    assert "зовёт вас к себе" in waiting.text()
    joined = await mirna.press(labels.GUILD_ACCEPT.text)
    assert "Вы в гильдии «Ирисы»" in joined.text()

    # Основатель поднимает Мирну до офицера с экрана состава.
    roster = await argus.press(labels.GUILD_ROSTER.text)
    assert "Мирна — участник." in roster.text()
    promoted = await argus.press(labels.guild_promote_label("Мирна").text)
    assert "теперь офицер" in promoted.text()

    guild = await guilds.of(mirna_character.id)
    assert guild is not None and guild.rank_of(mirna_character.id) is GuildRank.OFFICER


async def test_a_crowded_roster_is_read_page_by_page(
    table: tuple[Player, Player, Character, Character],
    characters: InMemoryCharacterRepository,
    guilds: GuildStore,
) -> None:
    """Состав большой гильдии режется на страницы, а не валится одним куском.

    В гильдию помещается тридцать человек, и тридцать строк со званиями и
    тридцатью рядами кнопок в одно сообщение не читаются
    (``docs/accessibility.md``, правила 7 и 11). Кнопка несёт имя, поэтому
    выгнать можно и того, кто на второй странице.
    """
    argus, _mirna, argus_character, _mirna_character = table
    await _found(argus)

    guild = await guilds.of(argus_character.id)
    assert guild is not None
    crowd = guild
    for number in range(1, 12):
        one = await characters.create(
            Character(
                id=0,
                user_id=630_000 + number,
                name=f"Соклановец{number:02d}",
                race_id="human",
                class_id="warrior",
                level=20,
            )
        )
        crowd = crowd.with_member(one.id, GuildRank.MEMBER)
    await guilds.save(crowd)

    first = await argus.press(labels.GUILD_ROSTER.text)
    assert first.id is ScreenId.GUILD_ROSTER
    assert first.fits_message_limit(), f"{len(first.text())} знаков в одном сообщении"
    assert "страница 1 из 2" in first.text()
    assert labels.NEXT_PAGE.text in buttons(first)
    assert "Соклановец01 — участник." in first.text()
    assert "Соклановец11 — участник." not in first.text()

    second = await argus.press(labels.NEXT_PAGE.text)
    assert second.fits_message_limit()
    assert "Соклановец11 — участник." in second.text()

    # Со второй страницы выгоняют так же, как с первой: кнопка несёт имя.
    kicked = await argus.press(labels.guild_kick_label("Соклановец11").text)
    assert "исключён из гильдии" in kicked.text()
    left = await guilds.of(argus_character.id)
    assert left is not None and left.size == crowd.size - 1


async def test_the_vault_takes_from_officers_and_only_deposits_from_members(
    table: tuple[Player, Player, Character, Character],
    characters: InMemoryCharacterRepository,
    guilds: GuildStore,
) -> None:
    argus, mirna, argus_character, _mirna_character = table
    await _found(argus)
    await argus.press(labels.GUILD_INVITE.text)
    await argus.press("Мирна")
    await mirna.press("/гильдия принять")

    # Участник кладёт в казну.
    vault = await mirna.press(labels.GUILD_VAULT.text)
    assert labels.guild_withdraw_label(50).text not in buttons(vault)
    deposited = await mirna.press(labels.guild_deposit_label(250).text)
    assert "внесено 250" in deposited.text()
    guild = await guilds.of(argus_character.id)
    assert guild is not None and guild.vault_gold == 250

    # Основатель берёт.
    taken = await argus.press(f"{labels.GUILD_VAULT.text}")
    taken = await argus.press(labels.guild_withdraw_label(250).text)
    assert "взято 250" in taken.text()
    stored = await characters.get(argus_character.id)
    assert stored is not None
    assert stored.gold == argus_character.gold - FOUND_COST + 250


async def test_disbanding_returns_the_vault_and_frees_everyone(
    table: tuple[Player, Player, Character, Character],
    characters: InMemoryCharacterRepository,
    guilds: GuildStore,
) -> None:
    argus, mirna, argus_character, mirna_character = table
    await _found(argus)
    await argus.press(labels.GUILD_INVITE.text)
    await argus.press("Мирна")
    await mirna.press("/гильдия принять")
    await mirna.press(labels.GUILD_VAULT.text)
    await mirna.press(labels.guild_deposit_label(1000).text)

    gone = await argus.press(labels.GUILD_DISBAND.text)
    assert "распущена" in gone.text()
    assert await guilds.of(argus_character.id) is None
    assert await guilds.of(mirna_character.id) is None
    stored = await characters.get(argus_character.id)
    assert stored is not None
    assert stored.gold == argus_character.gold - FOUND_COST + 1000


async def test_a_guild_is_still_there_after_a_fresh_start(
    table: tuple[Player, Player, Character, Character],
    sent: Recorder,
) -> None:
    argus, _, _, _ = table
    await _found(argus)
    again = Player(ARGUS_ACCOUNT, sent, **argus.deps)
    screen = await again.press(labels.GUILD.text)
    assert labels.GUILD_ROSTER.text in buttons(screen)
    assert labels.GUILD_DISBAND.text in buttons(screen)


# --- передача вещи соклановцу ---------------------------------------


async def _two_in_a_guild(argus: Player, mirna: Player) -> None:
    await _found(argus)
    await argus.press(labels.GUILD_INVITE.text)
    await argus.press("Мирна")
    await mirna.press("/гильдия принять")


async def test_an_item_is_passed_to_a_guildmate(
    table: tuple[Player, Player, Character, Character],
    content: GameContent,
) -> None:
    argus, mirna, argus_character, mirna_character = table
    await argus.deps["inventory"].add(argus_character.id, "small_healing_potion", 4)
    await _two_in_a_guild(argus, mirna)

    screen = await argus.press(labels.GUILD.text)
    assert labels.GUILD_TRANSFER.text in buttons(screen)

    to_screen = await argus.press(labels.GUILD_TRANSFER.text)
    assert to_screen.id is ScreenId.TRANSFER_TO
    assert "Мирна" in buttons(to_screen)

    bag = await argus.press("Мирна")
    potion = content.item("small_healing_potion").name
    button = next(one for one in buttons(bag) if one.startswith(f"{potion}, штук "))
    await argus.press(button)
    done = await argus.press(labels.TRANSFER_ALL.text)

    assert done.id is ScreenId.GUILD
    assert "передано игроку Мирна" in done.text()
    theirs = {
        e.item_id: e.quantity for e in await mirna.deps["inventory"].list_items(mirna_character.id)
    }
    assert theirs["small_healing_potion"] == 4
    assert not await argus.deps["inventory"].list_items(argus_character.id)


async def test_a_non_guildmate_is_not_offered(
    table: tuple[Player, Player, Character, Character],
) -> None:
    """В списке получателей только гильдия: чужого там нет."""
    argus, _mirna, argus_character, _ = table
    await argus.deps["inventory"].add(argus_character.id, "small_healing_potion", 2)
    await _found(argus)  # гильдия из одного человека

    screen = await argus.press(labels.GUILD.text)
    assert labels.GUILD_TRANSFER.text not in buttons(screen)
