"""Гильдия, прогнанная через настоящий хендлер (ADR 0030, 0077).

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
from mmorpg.domain.rules import guild as guild_rules
from mmorpg.domain.rules import guild_war as war_rules
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
def inventory() -> InMemoryInventoryRepository:
    return InMemoryInventoryRepository()


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
    inventory: InMemoryInventoryRepository,
    sent: Recorder,
) -> tuple[Player, Player, Character, Character]:
    deps: dict[str, Any] = {
        "characters": characters,
        "inventory": inventory,
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
    assert "Мирна — новик, вклад 0." in roster.text()
    # Звание двигают на ступень: новик - участник - ветеран - старейшина.
    promoted = await argus.press(labels.guild_promote_label("Мирна").text)
    assert "теперь участник" in promoted.text()
    promoted = await argus.press(labels.guild_promote_label("Мирна").text)
    assert "теперь ветеран" in promoted.text()

    guild = await guilds.of(mirna_character.id)
    assert guild is not None and guild.rank_of(mirna_character.id) is GuildRank.VETERAN

    lowered = await argus.press(labels.guild_demote_label("Мирна").text)
    assert "теперь участник" in lowered.text()


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
    assert "Соклановец01 — участник, вклад 0." in first.text()
    assert "Соклановец11 — участник, вклад 0." not in first.text()

    second = await argus.press(labels.NEXT_PAGE.text)
    assert second.fits_message_limit()
    assert "Соклановец11 — участник, вклад 0." in second.text()

    # Со второй страницы выгоняют так же, как с первой: кнопка несёт имя.
    kicked = await argus.press(labels.guild_kick_label("Соклановец11").text)
    assert "исключён из гильдии" in kicked.text()
    left = await guilds.of(argus_character.id)
    assert left is not None and left.size == crowd.size - 1


async def test_the_vault_takes_by_rank_and_a_recruit_only_deposits(
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


# --- гильдия растёт (ADR 0076) ---------------------------------------


async def test_a_deposit_is_written_to_the_guild_as_deeds(
    table: tuple[Player, Player, Character, Character],
    guilds: GuildStore,
) -> None:
    """Внесённое золото меряется боями своего уровня и растит гильдию."""
    argus, _mirna, argus_character, _ = table
    await _found(argus)

    await argus.press(labels.GUILD_VAULT.text)
    deposited = await argus.press(labels.guild_deposit_label(250).text)
    expected = guild_rules.deeds_for_deposit(250, argus_character.level)
    assert expected > 0
    assert f"деяний: {expected}" in deposited.text()

    guild = await guilds.of(argus_character.id)
    assert guild is not None
    assert guild.deeds == expected
    assert guild.contributed_by(argus_character.id) == expected

    # Вклад виден в составе, а ступень - на самом экране гильдии.
    roster = await argus.press(labels.GUILD_ROSTER.text)
    assert f"вклад {expected}." in roster.text()
    screen = await argus.press(labels.GUILD.text)
    assert "Ступень 1: Товарищество." in screen.text()
    assert f"Деяний: {expected}." in screen.text()


async def test_the_vault_holds_a_rank_to_its_share_per_rotation(
    table: tuple[Player, Player, Character, Character],
    characters: InMemoryCharacterRepository,
    guilds: GuildStore,
) -> None:
    """Казна, из которой один человек выносит всё, - это не общая казна."""
    argus, mirna, argus_character, mirna_character = table
    await _found(argus)
    await argus.press(labels.GUILD_INVITE.text)
    await argus.press("Мирна")
    await mirna.press("/гильдия принять")

    # Новик кладёт, но ряда выемки у него нет вовсе.
    vault = await mirna.press(labels.GUILD_VAULT.text)
    assert labels.guild_withdraw_label(50).text not in buttons(vault)
    assert "не берёт" in vault.text()
    await mirna.press(labels.guild_deposit_label(1000).text)

    # Участнику ряд рисуют, и предел он слышит числом.
    guild = await guilds.of(argus_character.id)
    assert guild is not None
    await guilds.save(guild.with_rank(mirna_character.id, GuildRank.MEMBER))
    limit = guild_rules.withdraw_limit(GuildRank.MEMBER, mirna_character.level)
    assert limit is not None and 250 < limit < 1000

    vault = await mirna.press(labels.GUILD_VAULT.text)
    assert labels.guild_withdraw_label(250).text in buttons(vault)
    assert f"Вам положено за переворот: {limit}." in vault.text()

    await mirna.press(labels.guild_withdraw_label(250).text)
    await mirna.press(labels.guild_withdraw_label(250).text)
    refused = await mirna.press(labels.guild_withdraw_label(250).text)
    assert "не больше" in refused.text(), "предел переворота держит третью выемку"

    stored = await characters.get(mirna_character.id)
    assert stored is not None
    assert stored.gold == mirna_character.gold - 1000 + 500

    # А основатель берёт без предела: он же за казну и отвечает.
    taken = await argus.press(labels.GUILD_VAULT.text)
    assert "без предела" in taken.text()
    took = await argus.press(labels.guild_withdraw_label(50).text)
    assert "Из казны взято 50" in took.text()


async def test_the_rise_screen_names_every_step_and_where_the_guild_stands(
    table: tuple[Player, Player, Character, Character],
    content: GameContent,
) -> None:
    argus, _mirna, _, _ = table
    await _found(argus)

    rise = await argus.press(labels.GUILD_TIERS.text)
    assert rise.id is ScreenId.GUILD_TIERS
    assert rise.fits_message_limit()
    for tier in content.guild_tiers:
        assert tier.name in rise.text()
    assert "взята" in rise.text()


async def test_the_guild_is_handed_over_and_the_old_founder_may_leave(
    table: tuple[Player, Player, Character, Character],
    guilds: GuildStore,
) -> None:
    argus, mirna, argus_character, mirna_character = table
    await _found(argus)
    await argus.press(labels.GUILD_INVITE.text)
    await argus.press("Мирна")
    await mirna.press("/гильдия принять")

    screen = await argus.press(labels.GUILD.text)
    assert labels.GUILD_SUCCEED.text in buttons(screen)
    asking = await argus.press(labels.GUILD_SUCCEED.text)
    assert asking.id is ScreenId.GUILD_SUCCEED
    handed = await argus.press("Мирна")
    assert "Гильдия передана: Мирна" in handed.text()

    guild = await guilds.of(argus_character.id)
    assert guild is not None
    assert guild.founder_id == mirna_character.id
    assert guild.rank_of(argus_character.id) is GuildRank.ELDER

    # Прежний основатель больше не заперт в своей гильдии.
    left = await argus.press(labels.GUILD_LEAVE.text)
    assert "Вы вышли из гильдии." in left.text()
    assert await guilds.of(argus_character.id) is None


async def test_only_the_founder_hands_the_guild_over(
    table: tuple[Player, Player, Character, Character],
) -> None:
    argus, mirna, _, _ = table
    await _found(argus)
    await argus.press(labels.GUILD_INVITE.text)
    await argus.press("Мирна")
    await mirna.press("/гильдия принять")

    screen = await mirna.press(labels.GUILD.text)
    assert labels.GUILD_SUCCEED.text not in buttons(screen)
    refused = await mirna.press("/гильдия наследник")
    assert refused.id is ScreenId.GUILD_SUCCEED
    said = await mirna.press("Аргус")
    assert "только основатель" in said.text()


# --- хранилище, подряд и война (ADR 0077) ----------------------------


POTION = "small_healing_potion"


async def test_a_thing_goes_into_the_store_and_comes_back_out(
    table: tuple[Player, Player, Character, Character],
    inventory: InMemoryInventoryRepository,
    guilds: GuildStore,
    content: GameContent,
) -> None:
    """Общая сумка: кладёт каждый, берут званием - и вещь и правда переезжает."""
    argus, _, argus_character, _ = table
    await _found(argus)
    await inventory.add(argus_character.id, POTION, 5)
    name = content.item(POTION).name

    empty = await argus.press(labels.GUILD_STORE.text)
    assert empty.id is ScreenId.GUILD_STORE
    assert "пусто" in empty.text()

    bag = await argus.press(labels.GUILD_STOW.text)
    assert bag.id is ScreenId.GUILD_STORE_PUT
    assert f"{name}, штук 5" in buttons(bag)

    how_many = await argus.press(f"{name}, штук 5")
    assert how_many.id is ScreenId.GUILD_STORE_AMOUNT
    stored = await argus.press(labels.GUILD_STOW_ALL.text)
    assert "В хранилище положено" in stored.text()
    assert await inventory.count(argus_character.id, POTION) == 0

    guild = await guilds.of(argus_character.id)
    assert guild is not None
    assert await guilds.stock(guild.id) == ((POTION, 5),)

    shelf = await argus.press(labels.GUILD_STORE.text)
    assert f"{name}, штук 5" in buttons(shelf)
    await argus.press(f"{name}, штук 5")
    back = await argus.press(labels.GUILD_TAKE_ALL.text)
    assert "Из хранилища взято" in back.text()
    assert await inventory.count(argus_character.id, POTION) == 5
    assert await guilds.stock(guild.id) == ()


async def test_a_recruit_may_stow_but_never_take(
    table: tuple[Player, Player, Character, Character],
    inventory: InMemoryInventoryRepository,
    guilds: GuildStore,
    content: GameContent,
) -> None:
    """Право на общее добро зарабатывают званием - как и право на казну."""
    argus, mirna, argus_character, mirna_character = table
    await _found(argus)
    await argus.press(labels.GUILD_INVITE.text)
    await argus.press("Мирна")
    await mirna.press("/гильдия принять")

    guild = await guilds.of(argus_character.id)
    assert guild is not None
    await guilds.stow(guild.id, POTION, 4)
    await inventory.add(mirna_character.id, POTION, 2)
    name = content.item(POTION).name

    shelf = await mirna.press(labels.GUILD_STORE.text)
    assert f"{name}, штук 4" not in buttons(shelf), "новик из хранилища не берёт"
    assert "не берёт" in shelf.text()

    await mirna.press(labels.GUILD_STOW.text)
    await mirna.press(f"{name}, штук 2")
    put = await mirna.press(labels.GUILD_STOW_ALL.text)
    assert "В хранилище положено" in put.text()
    assert dict(await guilds.stock(guild.id))[POTION] == 6


async def test_the_contract_is_asked_of_the_guild_and_closes_itself(
    table: tuple[Player, Player, Character, Character],
    guilds: GuildStore,
) -> None:
    """Подряд говорит, чего просят и сколько сделано; кнопки «сдать» нет."""
    argus, _, argus_character, _ = table
    await _found(argus)
    board = await argus.press(labels.GUILD_CONTRACT.text)
    assert board.id is ScreenId.GUILD_CONTRACT
    assert not board.rows, "подряд закрывается сам"
    assert "Сделано: 0" in board.text()

    guild = await guilds.of(argus_character.id)
    assert guild is not None
    await argus.press(labels.GUILD_VAULT.text)
    paid = await argus.press(labels.guild_deposit_label(1000).text)
    assert "Подряд закрыт" in paid.text(), "взнос закрывает дело о золоте"
    grown = await guilds.by_id(guild.id)
    assert grown is not None and grown.deeds > guild.deeds


async def test_a_war_is_declared_accepted_and_paid_for_from_both_vaults(
    table: tuple[Player, Player, Character, Character],
    guilds: GuildStore,
    content: GameContent,
) -> None:
    """Война - вызов, согласие и две ставки: ни одна не снимается раньше времени."""
    argus, mirna, argus_character, mirna_character = table
    await _found(argus, "Ирисы")
    await _found(mirna, "Медный Крест")
    ours = await guilds.of(argus_character.id)
    theirs = await guilds.of(mirna_character.id)
    assert ours is not None and theirs is not None
    stake = war_rules.war_stake(content.guild_tiers, guild_rules.standing(content, ours))
    await guilds.pay_vault(ours.id, stake * 2)
    await guilds.pay_vault(theirs.id, stake * 2)

    quiet = await argus.press(labels.GUILD_WAR.text)
    assert quiet.id is ScreenId.GUILD_WAR
    assert "ни с кем не воюет" in quiet.text()

    await argus.press(labels.GUILD_WAR_DECLARE.text)
    called = await argus.press("Медный Крест")
    assert "Вызов послан" in called.text()
    # Вызов ставки не трогает: замороженная казна - это не вызов, а залог.
    still = await guilds.by_id(ours.id)
    assert still is not None and still.vault_gold == stake * 2

    invited = await mirna.press(labels.GUILD_WAR.text)
    assert "зовёт вас на войну" in invited.text()
    started = await mirna.press(labels.GUILD_WAR_ACCEPT.text)
    assert "Война с гильдией «Ирисы» началась" in started.text()

    war = await guilds.war_of(ours.id)
    assert war is not None and war.stake == stake
    for guild_id in (ours.id, theirs.id):
        after = await guilds.by_id(guild_id)
        assert after is not None and after.vault_gold == stake, "ставка снята с обеих казён"

    seen = await argus.press(labels.GUILD_WAR.text)
    assert "воюет с гильдией «Медный Крест»" in seen.text()
    assert "наших очков 0" in seen.text()


async def test_a_challenge_is_declined_and_nothing_moves(
    table: tuple[Player, Player, Character, Character],
    guilds: GuildStore,
    content: GameContent,
) -> None:
    argus, mirna, argus_character, _ = table
    await _found(argus, "Ирисы")
    await _found(mirna, "Медный Крест")
    ours = await guilds.of(argus_character.id)
    assert ours is not None
    stake = war_rules.war_stake(content.guild_tiers, guild_rules.standing(content, ours))
    await guilds.pay_vault(ours.id, stake)

    await argus.press(labels.GUILD_WAR_DECLARE.text)
    await argus.press("Медный Крест")
    refused = await mirna.press(labels.GUILD_WAR_DECLINE.text)
    assert "отклонён" in refused.text()
    assert await guilds.war_of(ours.id) is None
    kept = await guilds.by_id(ours.id)
    assert kept is not None and kept.vault_gold == stake


async def test_a_war_without_a_stake_in_the_vault_is_not_declared(
    table: tuple[Player, Player, Character, Character],
) -> None:
    argus, mirna, _, _ = table
    await _found(argus, "Ирисы")
    await _found(mirna, "Медный Крест")
    await argus.press(labels.GUILD_WAR_DECLARE.text)
    refused = await argus.press("Медный Крест")
    assert "Ставка войны" in refused.text()


async def test_a_war_with_a_guild_that_is_not_there_says_so(
    table: tuple[Player, Player, Character, Character],
) -> None:
    argus, _, _, _ = table
    await _found(argus, "Ирисы")
    await argus.press(labels.GUILD_WAR_DECLARE.text)
    refused = await argus.press("Кого нет")
    assert "в Велларе нет" in refused.text()


async def test_a_war_whose_time_ran_out_is_settled_by_whoever_looks(
    table: tuple[Player, Player, Character, Character],
    guilds: GuildStore,
    content: GameContent,
) -> None:
    """Часов в игре нет: войну закрывает первый взгляд после срока (ADR 0077)."""
    argus, mirna, argus_character, mirna_character = table
    await _found(argus, "Ирисы")
    await _found(mirna, "Медный Крест")
    ours = await guilds.of(argus_character.id)
    theirs = await guilds.of(mirna_character.id)
    assert ours is not None and theirs is not None
    war = await guilds.open_war(
        challenger_id=ours.id, defender_id=theirs.id, stake=300, started=0, ends=0
    )
    await guilds.score_war(
        war,
        guild_id=ours.id,
        winner_id=argus_character.id,
        loser_id=mirna_character.id,
        now=0,
        rotation_seconds=SETTINGS.shop_rotation_seconds,
    )
    before = await guilds.by_id(ours.id)
    assert before is not None

    settled = await argus.press(labels.GUILD_WAR.text)
    assert "ни с кем не воюет" in settled.text()
    assert await guilds.war_of(ours.id) is None
    after = await guilds.by_id(ours.id)
    assert after is not None
    assert after.vault_gold == before.vault_gold + 600, "победившая забирает обе ставки"
    assert after.deeds > before.deeds, "выигранная война - деяния гильдии"


async def test_the_challenged_guild_is_told_the_stake_it_will_actually_pay(
    table: tuple[Player, Player, Character, Character],
    guilds: GuildStore,
    content: GameContent,
) -> None:
    """Ставку называет вызывающий, и его число — то, что снимут с обеих казён."""
    argus, mirna, argus_character, mirna_character = table
    await _found(argus, "Ирисы")
    await _found(mirna, "Медный Крест")
    ours = await guilds.of(argus_character.id)
    theirs = await guilds.of(mirna_character.id)
    assert ours is not None and theirs is not None
    # Вызывающая гильдия выросла на ступень: её ставка выше, чем у вызванной.
    await guilds.add_deeds(ours.id, content.guild_tiers[1].deeds)
    grown = await guilds.by_id(ours.id)
    assert grown is not None
    stake = war_rules.war_stake(content.guild_tiers, guild_rules.standing(content, grown))
    assert stake > war_rules.war_stake(content.guild_tiers, guild_rules.standing(content, theirs))
    await guilds.pay_vault(ours.id, stake)
    await guilds.pay_vault(theirs.id, stake)

    await argus.press(labels.GUILD_WAR_DECLARE.text)
    await argus.press("Медный Крест")
    invited = await mirna.press(labels.GUILD_WAR.text)
    assert str(stake) in invited.text(), "экран называет то число, которое и снимут"

    await mirna.press(labels.GUILD_WAR_ACCEPT.text)
    war = await guilds.war_of(theirs.id)
    assert war is not None and war.stake == stake
