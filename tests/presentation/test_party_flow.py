"""Отряд, прогнанный через настоящий хендлер.

Отряд заводят кнопкой, зовут в него именем и соглашаются сами. Здесь проверяется
вся дорога целиком: главное меню - экран отряда - зов - согласие, - потому что
отряд лежит в общем хранилище, а не в состоянии игрока, и ошибиться в связывании
куда легче, чем в правиле (``domain/rules/party.py``, ADR 0026).

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

ARGUS_ACCOUNT = 600_001
MIRNA_ACCOUNT = 600_002
SETTINGS = Settings(_env_file=None, shop_rotation_seconds=10**9)  # type: ignore[call-arg]


class Recorder:
    """Заменяет собой send_screen: держит экраны, которые выдал хендлер."""

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
def parties(cache: InMemoryStateCache) -> PartyStore:
    return PartyStore(InMemoryPartyRepository(), cache)


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
    """Один подопытный игрок: жмёт кнопки, и отвечает игровой хендлер."""

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
    parties: PartyStore,
    guilds: GuildStore,
    sent: Recorder,
) -> tuple[Player, Player, Character, Character]:
    """Двое игроков одного уровня, каждый со своим автоматом."""
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
        "parties": parties,
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
            level=6,
        )
    )
    mirna = await characters.create(
        Character(
            id=0,
            user_id=MIRNA_ACCOUNT,
            name="Мирна",
            race_id="human",
            class_id="mage",
            level=6,
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


# --- дорога от главного меню -------------------------------------------


async def test_the_main_menu_leads_to_the_party(
    table: tuple[Player, Player, Character, Character],
) -> None:
    """Отряд начинается кнопкой в главном меню, а не выученной командой."""
    argus, _, _, _ = table
    menu = await argus.press("/меню")
    assert labels.PARTY.text in buttons(menu)

    screen = await argus.press(labels.PARTY.text)
    assert screen.id is ScreenId.PARTY
    assert buttons(screen) == {labels.PARTY_CREATE.text}


async def test_a_party_is_created_and_then_disbanded(
    table: tuple[Player, Player, Character, Character],
    parties: PartyStore,
) -> None:
    """Заведённый отряд виден сразу, и расформировать его может тот, кто завёл."""
    argus, _, argus_character, _ = table
    await argus.press(labels.PARTY.text)

    created = await argus.press(labels.PARTY_CREATE.text)
    assert created.id is ScreenId.PARTY
    assert "Отряд создан" in created.text()
    assert buttons(created) == {labels.PARTY_INVITE.text, labels.PARTY_DISBAND.text}
    assert await parties.of(argus_character.id) is not None

    gone = await argus.press(labels.PARTY_DISBAND.text)
    assert "Отряд расформирован" in gone.text()
    assert buttons(gone) == {labels.PARTY_CREATE.text}
    assert await parties.of(argus_character.id) is None


async def test_nobody_is_called_before_the_party_exists(
    table: tuple[Player, Player, Character, Character],
) -> None:
    """Звать некуда, пока отряда нет, и игре есть что об этом сказать."""
    argus, _, _, _ = table
    await argus.press(labels.PARTY.text)
    refused = await argus.press("/отряд пригласить")
    assert refused.id is ScreenId.PARTY_INVITE

    answer = await argus.press("Мирна")
    assert "Создайте его" in answer.text()


async def test_a_party_is_still_there_after_a_fresh_start(
    table: tuple[Player, Player, Character, Character],
    sent: Recorder,
) -> None:
    """Отряд переживает выход из игры: состав в базе, а не в состоянии игрока (ADR 0029)."""
    argus, _, _, _ = table
    await argus.press(labels.PARTY.text)
    await argus.press(labels.PARTY_CREATE.text)

    # Тот же аккаунт заходит заново - чистый автомат, ничего не помнящий.
    again = Player(ARGUS_ACCOUNT, sent, **argus.deps)
    screen = await again.press(labels.PARTY.text)
    assert buttons(screen) == {labels.PARTY_INVITE.text, labels.PARTY_DISBAND.text}


# --- зов именем ---------------------------------------------------------


async def test_a_call_by_name_is_answered_by_the_one_who_was_called(
    table: tuple[Player, Player, Character, Character],
    parties: PartyStore,
) -> None:
    """Позвали именем, согласился сам, и отряд стал общим для двоих."""
    argus, mirna, argus_character, mirna_character = table
    await argus.press(labels.PARTY.text)
    await argus.press(labels.PARTY_CREATE.text)
    await argus.press(labels.PARTY_INVITE.text)

    called = await argus.press("Мирна")
    assert "Зов отправлен: Мирна" in called.text()

    waiting = await mirna.press(labels.PARTY.text)
    assert "Аргус зовёт вас в отряд" in waiting.text()
    assert labels.PARTY_ACCEPT.text in buttons(waiting)

    joined = await mirna.press(labels.PARTY_ACCEPT.text)
    assert "Вы в отряде: Аргус, Мирна" in joined.text()

    party = await parties.of(mirna_character.id)
    assert party is not None and party.members == (argus_character.id, mirna_character.id)

    # У позванного своя дверь: он уходит, а отряд собравшего остаётся.
    left = await mirna.press(labels.PARTY_LEAVE.text)
    assert "Вы вышли из отряда" in left.text()
    assert await parties.of(mirna_character.id) is None
    assert await parties.of(argus_character.id) is not None


async def test_a_call_declined_leaves_nothing_hanging(
    table: tuple[Player, Player, Character, Character],
    parties: PartyStore,
) -> None:
    argus, mirna, _, mirna_character = table
    await argus.press("/отряд создать")
    await argus.press(labels.PARTY_INVITE.text)
    await argus.press("Мирна")

    refused = await mirna.press("/отряд отказать")
    assert "Зов отклонён" in refused.text()
    assert await parties.called_by(mirna_character.id) == 0
    assert await parties.of(mirna_character.id) is None


async def test_a_name_nobody_carries_is_answered_plainly(
    table: tuple[Player, Player, Character, Character],
) -> None:
    argus, _, _, _ = table
    await argus.press("/отряд создать")
    await argus.press(labels.PARTY_INVITE.text)
    answer = await argus.press("Никого")
    assert "Никого" in answer.text()
    assert "нет" in answer.text()


# --- передача вещи соратнику -----------------------------------------


async def _gathered(argus: Player, mirna: Player) -> None:
    await argus.press("/отряд создать")
    await argus.press(labels.PARTY_INVITE.text)
    await argus.press("Мирна")
    await mirna.press(labels.PARTY_ACCEPT.text)
    await argus.press(labels.PARTY.text)


def _bag(player: Player, character_id: int) -> Any:
    return player.deps["inventory"].list_items(character_id)


async def test_a_stack_is_passed_after_asking_how_many(
    table: tuple[Player, Player, Character, Character],
    content: GameContent,
) -> None:
    """Кому → что → сколько: стопку передают, спросив число."""
    argus, mirna, argus_character, mirna_character = table
    inventory = argus.deps["inventory"]
    await inventory.add(argus_character.id, "small_healing_potion", 5)
    await _gathered(argus, mirna)

    screen = await argus.press(labels.PARTY.text)
    assert labels.PARTY_TRANSFER.text in buttons(screen)

    to_screen = await argus.press(labels.PARTY_TRANSFER.text)
    assert to_screen.id is ScreenId.TRANSFER_TO
    assert "Мирна" in buttons(to_screen)
    assert "Аргус" not in buttons(to_screen)

    bag = await argus.press("Мирна")
    assert bag.id is ScreenId.TRANSFER_ITEM
    potion = content.item("small_healing_potion").name
    button = next(one for one in buttons(bag) if one.startswith(f"{potion}, штук "))

    amount = await argus.press(button)
    assert amount.id is ScreenId.TRANSFER_AMOUNT

    done = await argus.press("2")
    assert done.id is ScreenId.PARTY
    assert "передано игроку Мирна" in done.text()

    mine = {e.item_id: e.quantity for e in await _bag(argus, argus_character.id)}
    theirs = {e.item_id: e.quantity for e in await _bag(mirna, mirna_character.id)}
    assert mine["small_healing_potion"] == 3
    assert theirs["small_healing_potion"] == 2


async def test_a_single_item_skips_the_amount_step(
    table: tuple[Player, Player, Character, Character],
    content: GameContent,
) -> None:
    argus, mirna, argus_character, mirna_character = table
    inventory = argus.deps["inventory"]
    await inventory.add(argus_character.id, "sword@1#common", 1)
    await _gathered(argus, mirna)

    await argus.press(labels.PARTY_TRANSFER.text)
    bag = await argus.press("Мирна")
    sword = content.item("sword@1#common").name
    button = next(one for one in buttons(bag) if one.startswith(f"{sword}, штук "))

    done = await argus.press(button)
    assert done.id is ScreenId.PARTY
    assert "передано игроку Мирна" in done.text()
    theirs = {e.item_id: e.quantity for e in await _bag(mirna, mirna_character.id)}
    assert theirs["sword@1#common"] == 1


async def test_a_blocked_pair_does_not_pass_items(
    table: tuple[Player, Player, Character, Character],
    content: GameContent,
) -> None:
    argus, mirna, argus_character, mirna_character = table
    await argus.deps["inventory"].add(argus_character.id, "small_healing_potion", 3)
    await argus.deps["privacy"].block(MIRNA_ACCOUNT, ARGUS_ACCOUNT, at=0)
    await _gathered(argus, mirna)

    await argus.press(labels.PARTY_TRANSFER.text)
    bag = await argus.press("Мирна")
    potion = content.item("small_healing_potion").name
    button = next(one for one in buttons(bag) if one.startswith(f"{potion}, штук "))
    await argus.press(button)
    refused = await argus.press("1")

    assert "закрыты дела" in refused.text()
    assert not await _bag(mirna, mirna_character.id)
    mine = {e.item_id: e.quantity for e in await _bag(argus, argus_character.id)}
    assert mine["small_healing_potion"] == 3
