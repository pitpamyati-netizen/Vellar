"""Главный цикл, прогнанный через настоящие хендлеры.

Всё остальное в этом наборе проверяет чистую функцию. Этот файл проверяет
связывание: персонаж входит в локацию, жмёт «Вступить в бой», доводит бой до
конца, и результат доходит до хранилищ. Бой, в который игрок на самом деле не
может войти, — единственный отказ, которого тесты веток не видят, поэтому он
проверяется здесь.

Никакой сети: два хендлера зовутся напрямую, а их одно сообщение на шаг
перехватывается.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, User

from mmorpg.application.services.content import ContentRegistry
from mmorpg.config import Settings
from mmorpg.domain.entities import Character, GameContent, QuestLog, SkillLoadout
from mmorpg.domain.entities.location import NodeKind
from mmorpg.domain.entities.stats import StatBlock
from mmorpg.domain.procgen import location_seed
from mmorpg.domain.rules import nodes as node_rules
from mmorpg.domain.rules import roamer as roamer_rules
from mmorpg.domain.rules.progression import experience_to_reach
from mmorpg.domain.rules.stats import derived_stats, stat_allowance
from mmorpg.infrastructure.persistence.memory import (
    InMemoryCharacterRepository,
    InMemoryContentOverlayRepository,
    InMemoryInventoryRepository,
    InMemoryKeeperLogRepository,
    InMemoryTradeRepository,
    InMemoryUserRepository,
)
from mmorpg.presentation.telegram.flows.play import (
    LocationSession,
    PlayState,
    build_location,
)
from mmorpg.presentation.telegram.handlers import combat as combat_handler
from mmorpg.presentation.telegram.handlers import play as play_handler
from mmorpg.presentation.telegram.screens import play as play_screens
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.states.screens import Play

ACCOUNT = 500_001
# Прилавок, который не перевернётся за время теста: локация, в которую входит игрок,
# обязана быть той, которую тест и посчитал.
SETTINGS = Settings(_env_file=None, shop_rotation_seconds=10**9)  # type: ignore[call-arg]


class Recorder:
    """Заменяет собой send_screen: держит все экраны, которые выдали хендлеры."""

    def __init__(self) -> None:
        self.screens: list[Screen] = []

    async def __call__(self, message: Message, screen: Screen, *, emoji: bool = False) -> None:
        self.screens.append(screen)

    @property
    def last(self) -> Screen:
        assert self.screens, "the game answered with silence"
        return self.screens[-1]


class Aside:
    """Заменяет собой send_text: держит лишние сообщения, которые породил шаг.

    Ровно одно действие в игре отвечает двумя сообщениями - взятый уровень, - и
    здесь ловится именно второе (``screens/play.level_up_report``).
    """

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def __call__(
        self, message: Message, text: str, screen: Screen, *, emoji: bool = False
    ) -> None:
        self.texts.append(text)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorder = Recorder()
    monkeypatch.setattr(play_handler, "send_screen", recorder)
    monkeypatch.setattr(combat_handler, "send_screen", recorder)
    return recorder


@pytest.fixture
def aside(monkeypatch: pytest.MonkeyPatch) -> Aside:
    extra = Aside()
    monkeypatch.setattr(play_handler, "send_text", extra)
    monkeypatch.setattr(combat_handler, "send_text", extra)
    return extra


@pytest.fixture
def characters() -> InMemoryCharacterRepository:
    return InMemoryCharacterRepository()


@pytest.fixture
def inventory() -> InMemoryInventoryRepository:
    return InMemoryInventoryRepository()


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
def deltas() -> Any:
    from mmorpg.infrastructure.cache.memory import InMemoryLocationStateCache

    return InMemoryLocationStateCache()


@pytest.fixture
def cache() -> Any:
    """Общее хранилище: в нём лежит бой (ADR 0021)."""
    from mmorpg.infrastructure.cache.memory import InMemoryStateCache

    return InMemoryStateCache()


@pytest.fixture
def parties(cache: Any) -> Any:
    """Состав отряда - в базе, приглашения - в кэше со сроком (ADR 0029)."""
    from mmorpg.application.services.party import PartyStore
    from mmorpg.infrastructure.persistence.memory import InMemoryPartyRepository

    return PartyStore(InMemoryPartyRepository(), cache)


@pytest.fixture
def guilds(cache: Any) -> Any:
    from mmorpg.application.services.guild import GuildStore
    from mmorpg.infrastructure.persistence.memory import InMemoryGuildRepository

    return GuildStore(InMemoryGuildRepository(), cache)


@pytest.fixture
async def argus(characters: InMemoryCharacterRepository) -> Character:
    return await characters.create(
        Character(
            id=0,
            user_id=ACCOUNT,
            name="Аргус",
            race_id="human",
            class_id="warrior",
            level=4,
            gold=200,
            unspent_skill_points=2,
            loadout=SkillLoadout(
                actives=("warrior_rassechenie", None, None, None, None, None),
                racial="race_human_second_wind",
                ranks={"warrior_rassechenie": 1},
            ),
        )
    )


@pytest.fixture
def state() -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=ACCOUNT, user_id=ACCOUNT),
    )


def a_message(text: str) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=ACCOUNT, type="private"),
        from_user=User(id=ACCOUNT, is_bot=False, first_name="Аргус"),
        text=text,
    )


class Player:
    """Один подопытный игрок: жмёт кнопки, и отвечает правильный хендлер."""

    def __init__(self, state: FSMContext, sent: Recorder, **deps: Any) -> None:
        self.state = state
        self.sent = sent
        self.deps = deps

    async def press(self, text: str) -> Screen:
        current = await self.state.get_state()
        message = a_message(text)
        if current in {Play.combat.state, Play.combat_bag.state}:
            await combat_handler.fight(
                message,
                self.state,
                self.deps["content"],
                SETTINGS,
                self.deps["characters"],
                self.deps["inventory"],
                self.deps["deltas"],
                self.deps["cache"],
                self.deps["parties"],
            )
        else:
            await play_handler.play(
                message,
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
            )
        return self.sent.last

    async def flow(self) -> PlayState:
        data = await self.state.get_data()
        return PlayState.deserialise(data["play"])


@pytest.fixture
async def player(
    state: FSMContext,
    sent: Recorder,
    content: GameContent,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    users: InMemoryUserRepository,
    deltas: Any,
    cache: Any,
    parties: Any,
    guilds: Any,
    overlays: InMemoryContentOverlayRepository,
    registry: ContentRegistry,
    argus: Character,
) -> Player:
    await state.set_state(Play.main_menu)
    return Player(
        state,
        sent,
        content=content,
        characters=characters,
        inventory=inventory,
        users=users,
        keeper_log=InMemoryKeeperLogRepository(),
        deltas=deltas,
        overlays=overlays,
        registry=registry,
        trades=InMemoryTradeRepository(),
        cache=cache,
        parties=parties,
        guilds=guilds,
    )


def path_to(location: Any, kind: NodeKind) -> tuple[list[int], int] | None:
    """Дорога от входа до ближайшего узла этого вида."""
    parents: dict[int, int] = {0: 0}
    queue = deque([0])
    while queue:
        current = queue.popleft()
        if location.node(current).kind is kind and current != 0:
            walk = [current]
            while walk[-1] != 0:
                walk.append(parents[walk[-1]])
            return list(reversed(walk))[1:], current
        for link in location.node(current).links:
            if link not in parents:
                parents[link] = current
                queue.append(link)
    return None


async def walk_to(player: Player, content: GameContent, kind: NodeKind) -> int:
    """Войти в первую локацию первого города и встать на узел вида ``kind``."""
    await player.press("Мир")
    await player.press("Локации")
    await player.press("1. Луга у Заставы")

    flow = await player.flow()
    location = build_location(content, SETTINGS.world_seed, flow.session)
    found = path_to(location, kind)
    assert found is not None, f"this seed produced no {kind} node"
    walk, target = found
    for index in walk:
        node = location.node(index)
        await player.press(f"Узел {node.index}: {node.name}")
    assert (await player.flow()).session.node == target
    return target


# --- цикл -------------------------------------------------------------


async def test_a_fresh_location_reads_as_untouched(
    player: Player, content: GameContent, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Экран локации, в которую только что вошли, называет округу тихой (ADR 0055).

    Блуждающий ход отключён нарочно. Он оседает по окну от стенных часов
    (``domain/rules/roamer.py``), и локация, в которую первым зашёл игрок, в
    трети окон встречает его встревоженной - а «встревожена» на экране отдельной
    строкой не пишется (ADR 0055). Без этого тест проверял бы не тихую округу, а
    время суток.
    """
    monkeypatch.setattr(roamer_rules, "SPAWN_CHANCE", 0.0)
    await player.press("Мир")
    await player.press("Локации")
    screen = await player.press("1. Луга у Заставы")
    assert "Округа тихая" in screen.text()


async def test_a_battle_node_actually_starts_a_fight(player: Player, content: GameContent) -> None:
    """То единственное, на чём стоит всё остальное: бой открывается."""
    await walk_to(player, content, NodeKind.BATTLE)
    screen = await player.press(play_screens.NODE_ACTIONS[NodeKind.BATTLE])
    assert screen.id is ScreenId.COMBAT
    assert screen.text().startswith("Бой. Круг 1.")
    # Первая кнопка - обычный удар; её надпись теперь называет след, который он
    # оставляет.
    assert next(row[0].text for row in screen.rows).startswith("Атака")


async def test_a_fight_ends_and_the_result_is_stored(
    player: Player,
    content: GameContent,
    characters: InMemoryCharacterRepository,
    argus: Character,
) -> None:
    await walk_to(player, content, NodeKind.BATTLE)
    await player.press(play_screens.NODE_ACTIONS[NodeKind.BATTLE])

    for _ in range(40):
        screen = await player.press("Атака")
        text = screen.text()
        if text.startswith(("Победа.", "Поражение.")):
            break
    else:  # pragma: no cover - бой, который не кончается, и есть та ошибка, которую это ловит
        pytest.fail("the fight never finished in 40 turns")

    stored = await characters.get_active(ACCOUNT)
    assert stored is not None
    if text.startswith("Победа."):
        assert stored.experience > argus.experience
        assert stored.gold >= argus.gold
        assert "Опыт:" in text
    else:
        assert stored.gold < argus.gold
    # Раны переживают бой, чем бы он ни кончился.
    assert 0 < stored.health <= derived_stats(content, stored).max_health


async def test_a_won_fight_takes_one_pack_out_of_the_node(
    player: Player, content: GameContent, deltas: Any
) -> None:
    """Узел - не рубильник: в нём стоит несколько стай, и он их отсчитывает."""
    node = await walk_to(player, content, NodeKind.BATTLE)
    await player.press(play_screens.NODE_ACTIONS[NodeKind.BATTLE])
    for _ in range(40):
        outcome = (await player.press("Атака")).text()
        if outcome.startswith(("Победа.", "Поражение.")):
            break

    flow = await player.flow()
    if not flow.session.active:
        # Проигранный бой вместо этого отправляет игрока обратно в город.
        assert flow.session == LocationSession()
        return

    stored = await deltas.state("farhold", 1, now=0)
    assert stored.node(node).taken == 1, "a won fight takes one thing out of the node"
    back = await player.press("Назад")
    assert back.id is ScreenId.LOCATION


async def test_an_emptied_node_says_when_it_fills_up_again(
    player: Player, content: GameContent, deltas: Any
) -> None:
    node = await walk_to(player, content, NodeKind.BATTLE)
    location = build_location(content, SETTINGS.world_seed, (await player.flow()).session)
    size = node_rules.wave_size(
        location_seed(SETTINGS.world_seed, "farhold", 1), node, location.node(node).kind, 0
    )
    for _ in range(size):
        await deltas.take("farhold", 1, node, wave=0, size=size, now=int(time.time()), ttl=600)

    refused = await player.press(play_screens.NODE_ACTIONS[NodeKind.BATTLE])
    assert "пусто" in refused.text()
    assert "минут" in refused.text()


async def test_the_service_row_still_works_on_an_outcome_screen(
    player: Player, content: GameContent
) -> None:
    """A fight that swallowed "Главное меню" would be a trap, not a screen."""
    await walk_to(player, content, NodeKind.BATTLE)
    await player.press(play_screens.NODE_ACTIONS[NodeKind.BATTLE])
    for _ in range(40):
        if (await player.press("Атака")).text().startswith(("Победа.", "Поражение.")):
            break

    home = await player.press("Главное меню")
    assert home.id is ScreenId.MAIN_MENU
    assert (await player.flow()).screen is ScreenId.MAIN_MENU
    assert await player.state.get_state() == Play.main_menu.state


async def test_a_quiet_node_pays_and_is_remembered(
    player: Player,
    content: GameContent,
    characters: InMemoryCharacterRepository,
    argus: Character,
) -> None:
    kinds = (NodeKind.CACHE, NodeKind.GATHER, NodeKind.EVENT, NodeKind.SHRINE)
    for kind in kinds:
        flow_before = await player.flow() if (await player.state.get_data()) else None
        if flow_before is not None and flow_before.session.active:
            await player.press("Покинуть локацию")
        try:
            await walk_to(player, content, kind)
        except AssertionError:
            continue
        screen = await player.press(play_screens.NODE_ACTIONS[kind])
        assert "сделано" in screen.text()
        assert "Опыт:" in screen.text()
        stored = await characters.get_active(ACCOUNT)
        assert stored is not None
        assert stored.experience > argus.experience
        return
    pytest.fail("this seed produced no quiet node at all")  # pragma: no cover


async def test_a_new_level_arrives_as_its_own_message(
    player: Player,
    aside: Aside,
    content: GameContent,
    characters: InMemoryCharacterRepository,
    argus: Character,
) -> None:
    """Уровень стоил строки посреди отчёта и терялся между добычей и здоровьем.

    Теперь это отдельное сообщение, и в нём есть всё причитающееся: очки,
    здоровье, открывшиеся умения и город.
    """
    almost = replace(argus, experience=experience_to_reach(argus.level + 1) - 1)
    await characters.save(almost)

    for kind in (NodeKind.CACHE, NodeKind.GATHER, NodeKind.EVENT, NodeKind.SHRINE):
        flow_before = await player.flow() if (await player.state.get_data()) else None
        if flow_before is not None and flow_before.session.active:
            await player.press("Покинуть локацию")
        try:
            await walk_to(player, content, kind)
        except AssertionError:
            continue
        screen = await player.press(play_screens.NODE_ACTIONS[kind])
        break
    else:  # pragma: no cover - у этого сида тихий узел есть всегда
        pytest.fail("this seed produced no quiet node at all")

    grown = await characters.get_active(ACCOUNT)
    assert grown is not None
    assert grown.level == argus.level + 1

    assert len(aside.texts) == 1, "уровень объявляется ровно одним сообщением"
    said = aside.texts[0]
    assert f"Новый уровень: {grown.level}." in said
    assert "Очков характеристик" in said
    assert "Очков умений" in said
    assert str(derived_stats(content, grown).max_health) in said
    # Экран действия про уровень больше не говорит: об этом есть чему сказать
    # отдельно, и дважды об одном игра не говорит.
    assert "Новый уровень" not in screen.text()


async def test_a_level_taken_in_a_fight_is_announced_too(
    player: Player,
    aside: Aside,
    content: GameContent,
    characters: InMemoryCharacterRepository,
    argus: Character,
) -> None:
    """Бой - главный источник опыта, и уровень от него объявляется так же."""
    almost = replace(argus, experience=experience_to_reach(argus.level + 1) - 1)
    await characters.save(almost)

    await walk_to(player, content, NodeKind.BATTLE)
    await player.press(play_screens.NODE_ACTIONS[NodeKind.BATTLE])
    for _ in range(40):
        text = (await player.press("Атака")).text()
        if text.startswith(("Победа.", "Поражение.")):
            break
    else:  # pragma: no cover - бой, который не кончается, это другая ошибка
        pytest.fail("the fight never finished in 40 turns")

    grown = await characters.get_active(ACCOUNT)
    assert grown is not None
    if not text.startswith("Победа."):  # pragma: no cover - этот сид выигрывает
        pytest.skip("этот сид кончился поражением: опыта за него не платят")

    assert grown.level == argus.level + 1
    assert len(aside.texts) == 1
    assert f"Новый уровень: {grown.level}." in aside.texts[0]
    assert "Новый уровень" not in text


async def test_a_step_that_takes_no_level_answers_once(
    player: Player, aside: Aside, content: GameContent
) -> None:
    """Второе сообщение приходит только тогда, когда есть о чём."""
    await walk_to(player, content, NodeKind.BATTLE)
    await player.press(play_screens.NODE_ACTIONS[NodeKind.BATTLE])
    assert aside.texts == []


# --- город ------------------------------------------------------------


async def test_the_inn_sells_health_and_the_bank_keeps_gold(
    player: Player, content: GameContent, characters: InMemoryCharacterRepository
) -> None:
    hurt = await characters.get_active(ACCOUNT)
    assert hurt is not None
    await characters.save(hurt.with_health(5, derived_stats(content, hurt).max_health))

    await player.press("Мир")
    await player.press("Таверна")
    screen = await player.press("Снять комнату")
    assert "здоровье полное" in screen.text()
    rested = await characters.get_active(ACCOUNT)
    assert rested is not None
    assert rested.health == derived_stats(content, rested).max_health
    assert rested.gold < 200

    await player.press("Назад")
    await player.press("Банк")
    kept = await player.press("Положить 50")
    assert "В ячейке теперь 50" in kept.text()
    banked = await characters.get_active(ACCOUNT)
    assert banked is not None
    assert banked.bank_gold == 50


async def test_a_contract_is_taken_counted_and_paid(
    player: Player, content: GameContent, characters: InMemoryCharacterRepository
) -> None:
    await player.press("Мир")
    await player.press("Таверна")
    board = await player.press("Доска заданий")
    assert "Столбы на Тракте" in board.text()

    quest = content.quest("farhold_tallies")
    offer = await player.press(f"{quest.name} — уровень {quest.level}, плата {quest.reward_gold}")
    assert quest.giver in offer.text()
    assert "Уйти" in [item.text for row in offer.rows for item in row]

    await player.press("Согласиться")
    holder = await characters.get_active(ACCOUNT)
    assert holder is not None
    assert holder.quests.is_taken(quest.id)

    # Сдать уже досчитанным: как двигается счётчик, проверяет домен, а здесь важно, что
    # плата приходит.
    counted = await characters.get_active(ACCOUNT)
    assert counted is not None
    await characters.save(replace(counted, quests=QuestLog(taken={quest.id: quest.target_count})))
    await player.press("Назад")
    paid = await player.press("Сдать задание")
    assert "закрыт" in paid.text()
    settled = await characters.get_active(ACCOUNT)
    assert settled is not None
    assert settled.quests.is_done(quest.id)
    assert settled.gold >= 200 + quest.reward_gold - 1


async def test_a_skill_point_buys_a_skill_and_a_slot_holds_it(
    player: Player, content: GameContent, characters: InMemoryCharacterRepository
) -> None:
    skills = await player.press("Умения")
    assert "Очков умений: 2" in skills.text()

    from mmorpg.domain.rules import skills as skill_rules
    from mmorpg.presentation.telegram.screens import skills as skill_screens

    holder = await characters.get_active(ACCOUNT)
    assert holder is not None
    fresh = next(
        skill
        for skill in skill_rules.teachable(content, holder)
        if not skill_rules.is_known(holder, skill.code) and skill.is_active
    )
    await player.press(skill_screens.skill_entry_text(content, holder, fresh))
    learned = await characters.get_active(ACCOUNT)
    assert learned is not None
    assert skill_rules.is_known(learned, fresh.code)
    assert learned.unspent_skill_points == 1

    await player.press("Слоты умений")
    await player.press(skill_screens.slot_label(content, learned, 1).text)
    await player.press(f"{fresh.name} — ранг 1")
    equipped = await characters.get_active(ACCOUNT)
    assert equipped is not None
    assert equipped.loadout.actives[1] == fresh.code


# --- локация - общая земля --------------------------------------------


async def test_what_one_player_took_is_gone_for_everybody(
    player: Player,
    content: GameContent,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    users: InMemoryUserRepository,
    deltas: Any,
    cache: Any,
    parties: Any,
    guilds: Any,
    overlays: InMemoryContentOverlayRepository,
    registry: ContentRegistry,
    state: FSMContext,
    sent: Recorder,
) -> None:
    """Локация - не личная копия: работа одного игрока в ней видна."""
    quiet = next(
        kind
        for kind in (NodeKind.GATHER, NodeKind.CACHE, NodeKind.EVENT, NodeKind.SHRINE)
        if path_to(
            build_location(
                content,
                SETTINGS.world_seed,
                LocationSession(city_id="farhold", slot=1),
            ),
            kind,
        )
        is not None
    )
    node = await walk_to(player, content, quiet)
    await player.press(play_screens.NODE_ACTIONS[quiet])

    stored = await deltas.state("farhold", 1, now=int(time.time()))
    assert stored.node(node).taken == 1, "the node was worked and nobody was told"

    # Второй игрок идёт той же дорогой и находит тайник уже обысканным.
    other_account = ACCOUNT + 1
    other = await characters.create(
        Character(
            id=0,
            user_id=other_account,
            name="Мерла",
            race_id="human",
            class_id="warrior",
            level=4,
        )
    )
    assert other.id != 0
    second_state = FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=other_account, user_id=other_account),
    )
    await second_state.set_state(Play.main_menu)

    def their_message(text: str) -> Message:
        return Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=other_account, type="private"),
            from_user=User(id=other_account, is_bot=False, first_name="Мерла"),
            text=text,
        )

    for text in ("Мир", "Локации", "1. Луга у Заставы"):
        await play_handler.play(
            their_message(text),
            second_state,
            content,
            SETTINGS,
            characters,
            inventory,
            users,
            InMemoryKeeperLogRepository(),
            deltas,
            overlays,
            registry,
            InMemoryTradeRepository(),
            cache,
            parties,
            guilds,
        )
    data = await second_state.get_data()
    theirs = PlayState.deserialise(data["play"])
    assert theirs.session.active, "the second player is standing in the same place"
    shared = await deltas.state("farhold", 1, now=int(time.time()))
    assert shared.node(node).taken == 1, "the second player got a fresh copy of the place"


async def test_the_map_relays_when_the_district_is_worked_out(
    player: Player, content: GameContent, deltas: Any
) -> None:
    """Карта не стоит на месте: выработанная округа заселяется заново и иначе (ADR 0035)."""
    await player.press("Мир")
    await player.press("Локации")
    await player.press("1. Луга у Заставы")

    flow = await player.flow()
    location = build_location(content, SETTINGS.world_seed, flow.session)
    seed = location_seed(SETTINGS.world_seed, "farhold", 1)
    now = int(time.time())

    # Проработать округу: вычистить каждый узел и дать волнам встать заново.
    inside = [
        node.index for node in location.nodes if node.kind not in {NodeKind.ENTRANCE, NodeKind.EXIT}
    ]
    for node in location.nodes:
        size = node_rules.wave_size(seed, node.index, node.kind, 0)
        for _ in range(size):
            await deltas.take("farhold", 1, node.index, wave=0, size=size, now=now, ttl=600)

    later = now + node_rules.RESPAWN_SECONDS
    worked = await deltas.state("farhold", 1, now=later)
    assert all(worked.node(index).wave == 1 for index in inside)
    epoch = node_rules.location_epoch(worked)
    assert epoch >= 1, "плотная вылазка сменила поколение"

    relaid = build_location(content, SETTINGS.world_seed, flow.session, epoch=epoch)
    assert [n.links for n in relaid.nodes] != [n.links for n in location.nodes], "тропы легли иначе"

    # Босс по-прежнему держит конец, граф связен, выход достижим мимо логова.
    bosses = [n for n in relaid.nodes if n.kind is NodeKind.BOSS_BATTLE]
    assert len(bosses) == 1
    assert bosses[0].index == len(relaid.nodes) - 2
    assert relaid.is_connected
    assert relaid.exit_node.index in relaid.reachable_from(0)


# --- Круг долгов ------------------------------------------------------


async def test_a_descent_pays_at_the_bottom_and_not_before(
    player: Player,
    content: GameContent,
    characters: InMemoryCharacterRepository,
    argus: Character,
) -> None:
    """Заход - это комнаты с развилками, а дно платит лишь дошедшему до логова.

    Экран обещал награду «внизу»; в игре её не платило ничто. Теперь каждая
    комната платит за себя, а логово сверх того отдаёт дно — золото, опыт и
    находку (ADR 0036).
    """
    # Очки потрачены так, как их потратил бы игрок: логово - босс, и голому
    # персонажу двенадцатого уровня выигрывать там нечего.
    allowance = stat_allowance(content, 12)
    strong = replace(
        argus,
        level=12,
        gold=1_000,
        health=0,
        allocated=StatBlock(STR=allowance // 2, END=allowance - allowance // 2),
    )
    await characters.save(strong)

    await player.press("Мир")
    screen = await player.press("Подземелья")
    assert screen.id is ScreenId.DUNGEON
    assert "дно" in screen.text().casefold()

    await player.press("Первая штольня")
    screen = await player.press("Разведка")
    doors = ("Логово хозяина", "Дальше — схватка", "Дальше — затишье", "Дальше — крупный зверь")
    bottom = ""
    for _ in range(200):
        text = screen.text()
        if "Логово пройдено" in text:
            bottom = text
            break
        if text.startswith("Поражение."):
            pytest.skip("the descent was lost; the prize is for whoever gets down")
        if "Впереди развилка" in text or "Логово хозяина:" in text:
            door = next(one for one in doors if one in text)
            screen = await player.press(door)
            continue
        screen = await player.press("Атака")
    else:  # pragma: no cover - заход, который не кончается, и есть та ошибка, которую это ловит
        pytest.fail("the descent never reached the bottom")

    assert "Дно спуска:" in bottom
    stored = await characters.get_active(ACCOUNT)
    assert stored is not None
    assert stored.gold > strong.gold


async def test_leaving_a_descent_leaves_it_behind(
    player: Player,
    content: GameContent,
    characters: InMemoryCharacterRepository,
    argus: Character,
) -> None:
    """Уйти из захода - значит кончить его.

    Заход продолжают только дверью развилки на экране итога, и другого пути
    внутрь нет. Незакрытый ``descent``, оставшийся в состоянии, собирал бы
    следующий бой - хоть в узле локации - как комнату данжа
    (``handlers/combat._spawn`` смотрит на ``descent.active``).
    """
    allowance = stat_allowance(content, 12)
    strong = replace(
        argus,
        level=12,
        gold=1_000,
        health=0,
        allocated=StatBlock(STR=allowance // 2, END=allowance - allowance // 2),
    )
    await characters.save(strong)

    await player.press("Мир")
    await player.press("Подземелья")
    await player.press("Первая штольня")
    screen = await player.press("Разведка")
    for _ in range(200):
        text = screen.text()
        if "Впереди развилка" in text:
            break
        if text.startswith("Поражение."):
            pytest.skip("the run was lost; leaving is then decided by the defeat")
        screen = await player.press("Атака")
    else:  # pragma: no cover - заход, который не кончается, ловится соседним тестом
        pytest.fail("the descent never offered a fork")

    assert (await player.flow()).descent.active, "заход должен стоять, пока игрок в нём"

    await player.press("Назад")
    left = await player.flow()
    assert left.descent.active is False
    assert left.fight == ""


# --- блуждающее подземелье (ADR 0037) -----------------------------------


async def _seed_roamer(deltas: Any, node: int, *, group: bool = False, holder: int = 0) -> None:
    from mmorpg.domain.entities.location import Roamer

    # Хождение по локации могло уже бросить подземелье само (``_roaming_here``); тест
    # ставит свой на известный узел.
    await deltas.clear_roamer("farhold", 1)
    await deltas.spawn_roamer(
        "farhold",
        1,
        Roamer(node=node, group=group, difficulty="delve", level=3, stamp=1),
        ttl=600,
    )
    if holder:
        await deltas.claim_roamer("farhold", 1, holder, ttl=600)


async def test_a_roamer_shows_up_in_the_location_and_a_solo_run_can_be_entered(
    player: Player, content: GameContent, deltas: Any
) -> None:
    node = await walk_to(player, content, NodeKind.GATHER)
    await _seed_roamer(deltas, node)

    shown = await player.press("Осмотреться")
    assert "подземелье" in shown.text().casefold()
    assert "Спуститься в подземелье" in [item.text for row in shown.rows for item in row]

    screen = await player.press("Спуститься в подземелье")
    assert screen.id is ScreenId.COMBAT
    flow = await player.flow()
    assert flow.descent.roamer is True
    assert flow.descent.slot == 1
    assert flow.descent.group is False
    # Пока игрок внутри - подземелье занято, чужому вход закрыт.
    held = await deltas.roamer("farhold", 1, now=0)
    assert held is not None and held.holder != 0
    assert await deltas.claim_roamer("farhold", 1, 424_242, ttl=600) is False


async def test_a_taken_roamer_shows_no_button_and_refuses_entry(
    player: Player, content: GameContent, deltas: Any
) -> None:
    node = await walk_to(player, content, NodeKind.GATHER)
    await _seed_roamer(deltas, node, holder=999_999)

    shown = await player.press("Осмотреться")
    assert "уже спустились" in shown.text().casefold()
    assert "Спуститься в подземелье" not in [item.text for row in shown.rows for item in row]


async def test_a_group_roamer_turns_a_lone_adventurer_away(
    player: Player, content: GameContent, deltas: Any
) -> None:
    node = await walk_to(player, content, NodeKind.GATHER)
    await _seed_roamer(deltas, node, group=True)
    await player.press("Осмотреться")

    screen = await player.press("Спуститься в подземелье")
    assert screen.id is ScreenId.LOCATION
    assert "отряд" in screen.text().casefold()
    # Замок не взят: одиночку не пустили.
    here = await deltas.roamer("farhold", 1, now=0)
    assert here is not None and here.holder == 0


async def test_a_roamer_run_carried_to_the_lair_makes_the_rift_vanish(
    player: Player,
    content: GameContent,
    characters: InMemoryCharacterRepository,
    deltas: Any,
    argus: Character,
) -> None:
    """Пройденное до логова подземелье осыпается и исчезает (ADR 0037)."""
    allowance = stat_allowance(content, 12)
    strong = replace(
        argus,
        level=12,
        gold=1_000,
        health=0,
        allocated=StatBlock(STR=allowance // 2, END=allowance - allowance // 2),
    )
    await characters.save(strong)

    node = await walk_to(player, content, NodeKind.GATHER)
    await _seed_roamer(deltas, node)
    await player.press("Осмотреться")
    screen = await player.press("Спуститься в подземелье")

    doors = ("Логово хозяина", "Дальше — схватка", "Дальше — затишье", "Дальше — крупный зверь")
    for _ in range(200):
        text = screen.text()
        if "блуждающего подземелья больше нет" in text.casefold():
            assert await deltas.roamer("farhold", 1, now=0) is None
            return
        if text.startswith("Поражение."):
            pytest.skip("the run was lost; the rift stays for the next one")
        if "Впереди развилка" in text or "Логово хозяина:" in text:
            screen = await player.press(next(one for one in doors if one in text))
            continue
        screen = await player.press("Атака")
    pytest.fail("the roamer run never reached the lair")


def test_a_grim_descent_names_a_hazard_and_a_boon() -> None:
    """«Гиблый спуск» несёт два условия — одну беду и одно благо (ADR 0036)."""
    from mmorpg.domain.rules import dungeon as dungeon_rules
    from mmorpg.presentation.telegram.screens import dungeon as dungeon_screens

    seed = dungeon_rules.run_seed(
        "vellar-test", "farhold", "farhold_first_adit", dungeon_rules.Difficulty.GRIM, 7
    )
    conditions = dungeon_rules.conditions_for(seed, dungeon_rules.Difficulty.GRIM)
    lines = dungeon_screens.condition_lines(conditions)
    assert len(lines) == 2
    assert any(line.startswith("Беда «") for line in lines)
    assert any(line.startswith("Благо «") for line in lines)
    assert dungeon_screens.condition_lines(()) == ()


async def test_a_round_of_the_circle_is_fought_and_paid_out(
    player: Player,
    content: GameContent,
    characters: InMemoryCharacterRepository,
    argus: Character,
) -> None:
    """Ни очереди, ни таймера: ставка, слепок с кого-то и ответ."""
    from mmorpg.domain.rules import arena as arena_rules

    rich = replace(argus, level=12, gold=5_000)
    await characters.save(rich)
    await characters.create(
        Character(
            id=0,
            user_id=ACCOUNT + 7,
            name="Мерла",
            race_id="human",
            class_id="warrior",
            level=12,
        )
    )

    await player.press("Мир")
    screen = await player.press("Арена")
    assert screen.id is ScreenId.ARENA
    stake = arena_rules.stake_for(rich.level)
    assert f"Ставка: {stake}" in screen.text()

    opened = await player.press("Выйти на арену")
    assert opened.id is ScreenId.COMBAT
    # Ставку берут в ту минуту, когда бой возник, а не когда он кончился.
    charged = await characters.get(rich.id)
    assert charged is not None
    assert charged.gold == rich.gold - stake

    for _ in range(60):
        screen = await player.press("Атака")
        if screen.text().startswith(("Победа.", "Поражение.")):
            break
    else:  # pragma: no cover - бой, который не кончается, и есть та ошибка, которую это ловит
        raise AssertionError("the arena fight never finished")

    settled = await characters.get(rich.id)
    assert settled is not None
    assert settled.arena_wins + settled.arena_losses == 1
    if settled.arena_wins:
        assert "Бой выигран" in screen.text()
        assert settled.gold == rich.gold + stake
    else:
        assert "Бой проигран" in screen.text()
        assert settled.gold == rich.gold - stake


async def test_an_empty_circle_says_so_and_charges_nothing(
    player: Player,
    characters: InMemoryCharacterRepository,
    argus: Character,
) -> None:
    """Копировать некого - значит, нет круга, и ставки за него не берут."""
    rich = replace(argus, level=12, gold=5_000)
    await characters.save(rich)

    await player.press("Мир")
    await player.press("Арена")
    screen = await player.press("Выйти на арену")

    assert "не с кем драться" in screen.text()
    unchanged = await characters.get(rich.id)
    assert unchanged is not None
    assert unchanged.gold == rich.gold


# --- обучение платит (ADR 0038) --------------------------------------


async def test_a_tutorial_step_pays_experience_and_gold(
    player: Player, characters: InMemoryCharacterRepository, argus: Character
) -> None:
    """Кнопка «Обучение» ведёт к шагу, а закрытый шаг тут же платит."""
    from mmorpg.domain.rules import tutorial as tutorial_rules

    screen = await player.press("Обучение")
    assert screen.id is ScreenId.STATS  # без экрана-обзора между меню и делом

    paid = await characters.get_active(ACCOUNT)
    assert paid is not None
    assert tutorial_rules.is_done(paid, tutorial_rules.TutorialTask.STATS)
    assert paid.gold == argus.gold + tutorial_rules.STEP_REWARD.gold
    assert paid.experience > argus.experience
    assert "Награда за обучение" in screen.text()


async def test_finishing_the_tutorial_hands_over_the_full_kit(
    player: Player,
    inventory: InMemoryInventoryRepository,
    characters: InMemoryCharacterRepository,
    argus: Character,
) -> None:
    """Последний шаг закрывает обучение и выдаёт набор: доспех, зелья, опыт, золото."""
    from mmorpg.domain.rules import tutorial as tutorial_rules

    # Всё, кроме лавки, уже позади (маска без бита TRADE); TRADE закроет покупка.
    await characters.save(replace(argus, gold=1_000, tutorial=0b101111))

    await player.press("Мир")
    shop = await player.press("Лавка")
    buy = next(
        item.text
        for row in shop.rows
        for item in row
        if item.text not in {"Назад", "Главное меню", "Продать вещи"}
    )
    await player.press(buy)
    bought = await player.press("Купить")

    done = await characters.get_active(ACCOUNT)
    assert done is not None
    assert tutorial_rules.finished(done)
    for slot in ("head", "hands", "feet"):
        assert done.equipment.item_in(slot) is not None
    held = {row.item_id: row.quantity for row in await inventory.list_items(done.id)}
    for item_id, count in tutorial_rules.COMPLETION_REWARD.items:
        assert held.get(item_id, 0) >= count
    assert "Обучение пройдено" in bought.text()
