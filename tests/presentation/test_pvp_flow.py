"""Поединок двух живых игроков, прогнанный через настоящие хендлеры.

Это то, ради чего движок переписан: у обоих открывается панель боя, ходят они по
очереди, и ход одного виден другому сразу - не пересказом постфактум, как было
со слепком (ADR 0021).

Сети здесь нет: экраны, которые игра отправила бы каждому, перехватываются.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, User

from mmorpg.application.services.battle import BattleStore
from mmorpg.application.services.party import PartyStore
from mmorpg.config import Settings
from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.rules import pvp as pvp_rules
from mmorpg.infrastructure.cache.memory import InMemoryLocationStateCache, InMemoryStateCache
from mmorpg.infrastructure.persistence.memory import (
    InMemoryCharacterRepository,
    InMemoryInventoryRepository,
)
from mmorpg.presentation.telegram.flows.state import LocationSession, PlayState
from mmorpg.presentation.telegram.handlers import combat as combat_handler
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.states.screens import Play

ATTACKER = 700_001
DEFENDER = 700_002
BOT_ID = 1
SETTINGS = Settings(_env_file=None, shop_rotation_seconds=10**9)  # type: ignore[call-arg]


class FakeBot:
    """Столько бота, сколько нужно хендлеру: номер и отправка сообщений."""

    id = BOT_ID

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **_: Any) -> None:
        self.sent.append((chat_id, text))


class Screens:
    """Все экраны, которые игра отправила: свои через ответ, чужие - в чат."""

    def __init__(self) -> None:
        self.answered: list[Screen] = []
        self.pushed: list[tuple[int, Screen]] = []

    async def answer(self, message: Message, screen: Screen, *, emoji: bool = False) -> None:
        self.answered.append(screen)

    async def push(self, bot: Any, chat_id: int, screen: Screen, *, emoji: bool = False) -> bool:
        self.pushed.append((chat_id, screen))
        return True

    def last_for(self, telegram_id: int) -> Screen:
        for chat_id, screen in reversed(self.pushed):
            if chat_id == telegram_id:
                return screen
        raise AssertionError(f"игроку {telegram_id} ничего не отправляли")


@pytest.fixture
def screens(monkeypatch: pytest.MonkeyPatch) -> Screens:
    recorder = Screens()
    monkeypatch.setattr(combat_handler, "send_screen", recorder.answer)
    monkeypatch.setattr(combat_handler, "push_screen", recorder.push)
    return recorder


@pytest.fixture
def bot() -> FakeBot:
    return FakeBot()


@pytest.fixture
def storage() -> MemoryStorage:
    """Одно хранилище на обоих: хендлер правит автомат второго игрока сам."""
    return MemoryStorage()


@pytest.fixture
def characters() -> InMemoryCharacterRepository:
    return InMemoryCharacterRepository()


@pytest.fixture
def cache() -> InMemoryStateCache:
    return InMemoryStateCache()


def a_fighter(name: str, account: int) -> Character:
    return Character(
        id=0,
        user_id=account,
        name=name,
        race_id="human",
        class_id="warrior",
        level=15,
        gold=500,
        loadout=SkillLoadout(
            actives=("warrior_cleave", None, None, None, None, None),
            racial="race_human_second_wind",
        ),
    )


@pytest.fixture
async def attacker(characters: InMemoryCharacterRepository) -> Character:
    return await characters.create(a_fighter("Аргус", ATTACKER))


@pytest.fixture
async def defender(characters: InMemoryCharacterRepository) -> Character:
    return await characters.create(a_fighter("Мирна", DEFENDER))


def context(storage: MemoryStorage, account: int) -> FSMContext:
    return FSMContext(
        storage=storage, key=StorageKey(bot_id=BOT_ID, chat_id=account, user_id=account)
    )


def a_message(account: int, text: str, bot: FakeBot) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=account, type="private"),
        from_user=User(id=account, is_bot=False, first_name="Игрок"),
        text=text,
    ).as_(cast(Bot, bot))


def a_flow(target_id: int = 0) -> PlayState:
    return PlayState(
        city_id="farhold",
        session=LocationSession(city_id="farhold", slot=1, node=0),
        fight=f"pvp:{target_id}" if target_id else "",
    )


async def open_duel(
    content: GameContent,
    storage: MemoryStorage,
    cache: InMemoryStateCache,
    characters: InMemoryCharacterRepository,
    bot: FakeBot,
    attacker: Character,
    defender: Character,
) -> None:
    state = context(storage, ATTACKER)
    await state.set_state(Play.main_menu)
    await combat_handler.open_fight(
        a_message(ATTACKER, "Напасть: Мирна", bot),
        state,
        content=content,
        settings=SETTINGS,
        character=attacker,
        flow=a_flow(defender.id),
        characters=characters,
        state_cache=cache,
        storage=storage,
    )


async def press(
    content: GameContent,
    storage: MemoryStorage,
    cache: InMemoryStateCache,
    characters: InMemoryCharacterRepository,
    bot: FakeBot,
    account: int,
    text: str,
) -> None:
    state = context(storage, account)
    await combat_handler.fight(
        a_message(account, text, bot),
        state,
        content,
        SETTINGS,
        characters,
        InMemoryInventoryRepository(),
        InMemoryLocationStateCache(),
        cache,
    )


async def whose_turn(cache: InMemoryStateCache, storage: MemoryStorage) -> int:
    """Аккаунт того, чьего нажатия бой сейчас ждёт."""
    battle_id = (await context(storage, ATTACKER).get_data()).get("battle")
    session = await BattleStore(cache).load(str(battle_id))
    assert session is not None
    current = session.state.active
    assert current is not None
    return current.user_id


# --- поединок ----------------------------------------------------------


async def test_both_players_get_the_battle_panel(
    content: GameContent,
    storage: MemoryStorage,
    cache: InMemoryStateCache,
    characters: InMemoryCharacterRepository,
    screens: Screens,
    bot: FakeBot,
    attacker: Character,
    defender: Character,
) -> None:
    """У защищающегося открывается панель боя, а не приходит весть постфактум."""
    await open_duel(content, storage, cache, characters, bot, attacker, defender)

    assert screens.answered, "нападающий видит бой"
    assert screens.answered[-1].id is ScreenId.COMBAT
    theirs = screens.last_for(DEFENDER)
    assert theirs.id is ScreenId.COMBAT
    assert "Аргус" in theirs.text(), "он видит, кто перед ним"

    # И у обоих автомат стоит на бою с одним и тем же номером.
    mine = await context(storage, ATTACKER).get_data()
    yours = await context(storage, DEFENDER).get_data()
    assert mine["battle"] and mine["battle"] == yours["battle"]
    assert await context(storage, DEFENDER).get_state() == Play.combat.state


async def test_the_defender_is_marked_busy(
    content: GameContent,
    storage: MemoryStorage,
    cache: InMemoryStateCache,
    characters: InMemoryCharacterRepository,
    screens: Screens,
    bot: FakeBot,
    attacker: Character,
    defender: Character,
) -> None:
    """Пока идёт бой, во второй никого не зовут."""
    await open_duel(content, storage, cache, characters, bot, attacker, defender)
    store = BattleStore(cache)
    assert await store.busy(attacker.id) is not None
    assert await store.busy(defender.id) is not None


async def test_the_turn_belongs_to_one_of_them_at_a_time(
    content: GameContent,
    storage: MemoryStorage,
    cache: InMemoryStateCache,
    characters: InMemoryCharacterRepository,
    screens: Screens,
    bot: FakeBot,
    attacker: Character,
    defender: Character,
) -> None:
    """Чужой ход не проходит: он ждёт своей очереди, и ему это говорят."""
    await open_duel(content, storage, cache, characters, bot, attacker, defender)
    waiting = DEFENDER if await whose_turn(cache, storage) == ATTACKER else ATTACKER

    before = await whose_turn(cache, storage)
    await press(content, storage, cache, characters, bot, waiting, "Атака")
    assert await whose_turn(cache, storage) == before, "очередь не сдвинулась"
    assert "не ваш ход" in screens.answered[-1].text().casefold()


async def test_a_move_reaches_the_other_player(
    content: GameContent,
    storage: MemoryStorage,
    cache: InMemoryStateCache,
    characters: InMemoryCharacterRepository,
    screens: Screens,
    bot: FakeBot,
    attacker: Character,
    defender: Character,
) -> None:
    """Ход одного приходит другому сразу же, своим сообщением."""
    await open_duel(content, storage, cache, characters, bot, attacker, defender)
    acting = await whose_turn(cache, storage)
    watching = DEFENDER if acting == ATTACKER else ATTACKER
    seen = len(screens.pushed)

    await press(content, storage, cache, characters, bot, acting, "Атака")

    fresh = [entry for entry in screens.pushed[seen:] if entry[0] == watching]
    assert fresh, "второй игрок узнал о чужом ходе"
    assert await whose_turn(cache, storage) == watching, "и теперь ход его"


async def test_a_duel_ends_and_the_stake_changes_hands(
    content: GameContent,
    storage: MemoryStorage,
    cache: InMemoryStateCache,
    characters: InMemoryCharacterRepository,
    screens: Screens,
    bot: FakeBot,
    attacker: Character,
    defender: Character,
) -> None:
    """Поединок доигрывается до конца, и десятая доля кошелька меняет хозяина."""
    await open_duel(content, storage, cache, characters, bot, attacker, defender)

    for _ in range(200):
        battle_id = (await context(storage, ATTACKER).get_data()).get("battle")
        session = await BattleStore(cache).load(str(battle_id))
        if session is None or session.state.is_over:
            break
        acting = await whose_turn(cache, storage)
        await press(content, storage, cache, characters, bot, acting, "Атака")
    else:  # pragma: no cover - поединок, который не кончается, и есть эта ошибка
        pytest.fail("поединок не кончился за двести ходов")

    first = await characters.get(attacker.id)
    second = await characters.get(defender.id)
    assert first is not None and second is not None
    moved = abs(first.gold - attacker.gold)
    assert moved > 0, "ставка перешла из рук в руки"
    assert first.gold + second.gold == attacker.gold + defender.gold, "золото не печатают"
    assert moved == pvp_rules.spoils_from(max(attacker.gold, defender.gold))
    # Раны записаны обоим: бой был настоящий.
    assert first.health > 0 and second.health > 0
    assert min(first.health, second.health) < max(first.health, second.health)


async def test_yielding_ends_a_duel_nobody_answers(
    content: GameContent,
    storage: MemoryStorage,
    cache: InMemoryStateCache,
    characters: InMemoryCharacterRepository,
    screens: Screens,
    bot: FakeBot,
    attacker: Character,
    defender: Character,
) -> None:
    """Таймера нет, и ждать можно вечно: дверь наружу - «Сдаться» (ADR 0021)."""
    await open_duel(content, storage, cache, characters, bot, attacker, defender)
    acting = await whose_turn(cache, storage)
    waiting = DEFENDER if acting == ATTACKER else ATTACKER

    await press(content, storage, cache, characters, bot, waiting, "Сдаться")

    battle_id = (await context(storage, ATTACKER).get_data()).get("battle")
    session = await BattleStore(cache).load(str(battle_id))
    assert session is not None and session.state.is_over
    assert await BattleStore(cache).busy(attacker.id) is None
    assert await BattleStore(cache).busy(defender.id) is None


async def test_a_party_goes_into_the_duel_together(
    content: GameContent,
    storage: MemoryStorage,
    cache: InMemoryStateCache,
    characters: InMemoryCharacterRepository,
    screens: Screens,
    bot: FakeBot,
    attacker: Character,
    defender: Character,
) -> None:
    """Отряд нападающего входит в бой целиком: двое против одного - тот же бой."""
    ally = await characters.create(a_fighter("Тьен", 700_003))
    parties = PartyStore(cache)
    await parties.call(leader_id=attacker.id, invitee_id=ally.id)
    await parties.accept(ally.id)

    await open_duel(content, storage, cache, characters, bot, attacker, defender)

    battle_id = (await context(storage, ATTACKER).get_data()).get("battle")
    session = await BattleStore(cache).load(str(battle_id))
    assert session is not None
    sides = {one.side for one in session.state.combatants}
    assert sides == {0, 1}
    assert len(session.state.living(0)) == 2, "нападающий пришёл с товарищем"
    assert len(session.state.living(1)) == 1
    assert {one.character_id for one in session.live_participants()} == {
        attacker.id,
        ally.id,
        defender.id,
    }


async def test_a_pve_fight_still_opens_for_one(
    content: GameContent,
    storage: MemoryStorage,
    cache: InMemoryStateCache,
    characters: InMemoryCharacterRepository,
    screens: Screens,
    bot: FakeBot,
    attacker: Character,
) -> None:
    """Одиночный бой с миром идёт через тот же движок и тот же хендлер."""
    state = context(storage, ATTACKER)
    await state.set_state(Play.main_menu)
    await combat_handler.open_fight(
        a_message(ATTACKER, "Вступить в бой", bot),
        state,
        content=content,
        settings=SETTINGS,
        character=attacker,
        flow=replace(a_flow(), fight="node"),
        characters=characters,
        state_cache=cache,
        storage=storage,
    )
    screen = screens.answered[-1]
    assert screen.id is ScreenId.COMBAT
    assert "Против вас:" in screen.text()
    assert not screens.pushed, "писать некому: игрок в бою один"


async def test_looking_again_costs_no_turn(
    content: GameContent,
    storage: MemoryStorage,
    cache: InMemoryStateCache,
    characters: InMemoryCharacterRepository,
    screens: Screens,
    bot: FakeBot,
    attacker: Character,
    defender: Character,
) -> None:
    """«Что там в бою» перечитывает бой и ничего в нём не двигает."""
    await open_duel(content, storage, cache, characters, bot, attacker, defender)
    waiting = DEFENDER if await whose_turn(cache, storage) == ATTACKER else ATTACKER
    before = await whose_turn(cache, storage)

    await press(content, storage, cache, characters, bot, waiting, "Что там в бою")

    assert await whose_turn(cache, storage) == before
    answered = screens.answered[-1].text()
    assert "Ход:" in answered
    assert "Не узнал действие" not in answered
