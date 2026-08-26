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
from mmorpg.application.services.party import PartyStore
from mmorpg.config import Settings
from mmorpg.domain.entities import Character, GameContent
from mmorpg.infrastructure.cache.memory import InMemoryLocationStateCache, InMemoryStateCache
from mmorpg.infrastructure.persistence.memory import (
    InMemoryCharacterRepository,
    InMemoryContentOverlayRepository,
    InMemoryInventoryRepository,
    InMemoryKeeperLogRepository,
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
        )
        return self.sent.last


@pytest.fixture
async def table(
    characters: InMemoryCharacterRepository,
    registry: ContentRegistry,
    cache: InMemoryStateCache,
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
    cache: InMemoryStateCache,
) -> None:
    """Заведённый отряд виден сразу, и расформировать его может тот, кто завёл."""
    argus, _, argus_character, _ = table
    await argus.press(labels.PARTY.text)

    created = await argus.press(labels.PARTY_CREATE.text)
    assert created.id is ScreenId.PARTY
    assert "Отряд создан" in created.text()
    assert buttons(created) == {labels.PARTY_INVITE.text, labels.PARTY_DISBAND.text}
    assert await PartyStore(cache).of(argus_character.id) is not None

    gone = await argus.press(labels.PARTY_DISBAND.text)
    assert "Отряд расформирован" in gone.text()
    assert buttons(gone) == {labels.PARTY_CREATE.text}
    assert await PartyStore(cache).of(argus_character.id) is None


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


# --- зов именем ---------------------------------------------------------


async def test_a_call_by_name_is_answered_by_the_one_who_was_called(
    table: tuple[Player, Player, Character, Character],
    cache: InMemoryStateCache,
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

    parties = PartyStore(cache)
    party = await parties.of(mirna_character.id)
    assert party is not None and party.members == (argus_character.id, mirna_character.id)

    # У позванного своя дверь: он уходит, а отряд собравшего остаётся.
    left = await mirna.press(labels.PARTY_LEAVE.text)
    assert "Вы вышли из отряда" in left.text()
    assert await parties.of(mirna_character.id) is None
    assert await parties.of(argus_character.id) is not None


async def test_a_call_declined_leaves_nothing_hanging(
    table: tuple[Player, Player, Character, Character],
    cache: InMemoryStateCache,
) -> None:
    argus, mirna, _, mirna_character = table
    await argus.press("/отряд создать")
    await argus.press(labels.PARTY_INVITE.text)
    await argus.press("Мирна")

    refused = await mirna.press("/отряд отказать")
    assert "Зов отклонён" in refused.text()
    assert await PartyStore(cache).called_by(mirna_character.id) == 0
    assert await PartyStore(cache).of(mirna_character.id) is None


async def test_a_name_nobody_carries_is_answered_plainly(
    table: tuple[Player, Player, Character, Character],
) -> None:
    argus, _, _, _ = table
    await argus.press("/отряд создать")
    await argus.press(labels.PARTY_INVITE.text)
    answer = await argus.press("Никого")
    assert "Никого" in answer.text()
    assert "нет" in answer.text()
