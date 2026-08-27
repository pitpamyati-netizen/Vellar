"""Хендлер боя: одно нажатие - один ход, по сообщению каждому участнику.

Движок в ``domain.rules.combat``, разбор кнопок в ``flows.combat``, сама запись
боя в ``application.services.battle``. Здесь остаётся то, чего не может ни один
из них: кто с кем дерётся, кому писать о случившемся и что выигранный или
проигранный бой делает с сохранённым персонажем.

Бой лежит в общем хранилище, а в данных автомата у игрока - только его номер.
Поэтому в поединке двоих ход одного виден другому сразу, а не пересказывается
ему потом (ADR 0021).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.types import Message

from mmorpg import economy_log
from mmorpg.application.services.battle import (
    BattleKind,
    BattleSession,
    BattleStore,
    begin,
    roster_for,
)
from mmorpg.application.services.party import PartyStore
from mmorpg.config import Settings
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import ActionKind, BattleAction, Combatant, EventKind, Verdict
from mmorpg.domain.entities.content import GameContent, ItemKind
from mmorpg.domain.entities.location import EnemyRank, LocationState
from mmorpg.domain.ports.repositories import (
    CharacterRepository,
    InventoryRepository,
    LocationStateCache,
    StateCache,
)
from mmorpg.domain.procgen.seeds import derive, rng
from mmorpg.domain.rules import adventure, progression
from mmorpg.domain.rules import arena as arena_rules
from mmorpg.domain.rules import nodes as node_rules
from mmorpg.domain.rules import party as party_rules
from mmorpg.domain.rules import pvp as pvp_rules
from mmorpg.domain.rules import turning as turning_rules
from mmorpg.domain.rules import tutorial as tutorial_rules
from mmorpg.domain.rules.combat import act
from mmorpg.domain.rules.stats import derived_stats
from mmorpg.domain.rules.tutorial import TutorialTask
from mmorpg.logging import get_logger
from mmorpg.presentation.telegram.flows import combat as fight_flow
from mmorpg.presentation.telegram.flows.play import (
    build_location,
    descent_fight_seed,
    location_known,
    node_fight_seed,
    visit_seed,
)
from mmorpg.presentation.telegram.flows.state import Descent, LocationSession, PlayState
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.messaging import push_screen, send_screen, send_text
from mmorpg.presentation.telegram.screens import arena as arena_screens
from mmorpg.presentation.telegram.screens import combat as combat_screens
from mmorpg.presentation.telegram.screens import tutorial as tutorial_screens
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.play import level_up_report
from mmorpg.presentation.telegram.states.screens import STATE_FOR_SCREEN, NavigationStack, Play

logger = get_logger(__name__)

#: Номер боя, в котором стоит этот игрок. Сам бой - в общем хранилище.
STATE_KEY = "battle"
PLAY_KEY = "play"

#: События, которые говорят «этого не будет», а не «это случилось». Ход после
#: них не сдвинулся, и рассылать их некому, кроме нажавшего
#: (``Claude.md``, правило 3).
REFUSALS = frozenset(
    {
        EventKind.EMPTY_SLOT,
        EventKind.ON_COOLDOWN,
        EventKind.NOT_ENOUGH_RESOURCE,
        EventKind.WRONG_WEAPON,
        EventKind.NO_TARGET,
    }
)


def build_router() -> Router:
    """Свежий роутер на приложение - см. handlers.creation.build_router."""
    router = Router(name="combat")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.message.register(fight, StateFilter(Play.combat, Play.combat_bag))
    return router


@dataclass(slots=True)
class Payout:
    """Что бой оставил одному участнику, словами и числами для его экрана."""

    experience: int = 0
    gold: int = 0
    gold_lost: int = 0
    loot: tuple[str, ...] = ()
    extra: list[str] = field(default_factory=list)
    rows: list[tuple[Label, ...]] = field(default_factory=list)
    level_up: str = ""


# --- начало боя -------------------------------------------------------


async def open_fight(
    message: Message,
    state: FSMContext,
    *,
    content: GameContent,
    settings: Settings,
    character: Character,
    flow: PlayState,
    emoji: bool = False,
    characters: CharacterRepository,
    state_cache: StateCache,
    parties: PartyStore,
    storage: BaseStorage | None = None,
    location_state: LocationState | None = None,
    now: int = 0,
) -> None:
    """Собрать бой, которого попросил игровой поток, и показать его всем."""
    store = BattleStore(state_cache)

    standing = await store.busy(character.id)
    if standing is not None:
        # Второго боя не бывает: игрока возвращают в тот, в котором он стоит.
        session = await store.load(standing)
        if session is not None:
            await _show(message, state, content, character, session, storage=storage, emoji=emoji)
            return

    allies = await _party_of(character, parties, characters, store)
    session, roster = await _spawn(
        message,
        content=content,
        settings=settings,
        character=character,
        allies=allies,
        flow=flow,
        characters=characters,
        store=store,
        parties=parties,
        location_state=location_state or LocationState(),
        now=now,
    )
    if session is None:
        return

    await store.save(session)
    landing = replace(flow, screen=ScreenId.COMBAT, fight="")
    await state.set_state(Play.combat)
    await state.update_data({PLAY_KEY: landing.serialise(), STATE_KEY: session.id})
    await _broadcast(
        message,
        content=content,
        session=session,
        roster=roster,
        actor_id=character.id,
        storage=storage,
        emoji=emoji,
    )


async def _party_of(
    character: Character,
    parties: PartyStore,
    characters: CharacterRepository,
    store: BattleStore,
) -> tuple[Character, ...]:
    """Кто идёт в этот бой вместе с игроком.

    Занятые чужим боем остаются дома: в двух боях сразу не стоит никто
    (``domain/rules/party.py``).
    """
    party = await parties.of(character.id)
    if party is None:
        return ()
    companions: list[Character] = []
    for member_id in party.members:
        if member_id == character.id:
            continue
        other = await characters.get(member_id)
        if other is None or await store.busy(other.id) is not None:
            continue
        companions.append(other)
        if len(companions) + 1 >= party_rules.MAX_MEMBERS:
            break
    return tuple(companions)


async def _spawn(
    message: Message,
    *,
    content: GameContent,
    settings: Settings,
    character: Character,
    allies: tuple[Character, ...],
    flow: PlayState,
    characters: CharacterRepository,
    store: BattleStore,
    parties: PartyStore,
    location_state: LocationState,
    now: int,
) -> tuple[BattleSession | None, dict[int, Character]]:
    """Кто с кем дерётся. Один сборщик на все виды боя."""
    battle_id = f"{character.id}-{now or int(time.time())}"
    side = [(character, True), *((one, True) for one in allies)]

    if flow.fight.startswith("pvp:"):
        return await _spawn_duel(
            message,
            content=content,
            character=character,
            allies=allies,
            flow=flow,
            characters=characters,
            store=store,
            parties=parties,
            battle_id=battle_id,
        )

    if flow.fight == "arena":
        return await _spawn_arena(
            message,
            content=content,
            character=character,
            characters=characters,
            battle_id=battle_id,
        )

    if flow.fight == "dungeon" or flow.descent.active:
        descent = flow.descent
        city = content.city(descent.city_id)
        # Спуск занимает биом самой глубокой локации города: это та же земля,
        # только ниже.
        biome = city.locations[-1].biome
        seed = descent_fight_seed(settings.world_seed, descent)
        enemies = fight_flow.spawn_for_node(
            content,
            seed=seed,
            biome=biome,
            level=descent.level,
            # Последний этаж - то, ради чего спуск и затевался.
            rank=(
                EnemyRank.ELITE
                if descent.depth >= turning_rules.descent_depth(character)
                else EnemyRank.NORMAL
            ),
        )
        return begin(
            content,
            battle_id=battle_id,
            attackers=side,
            enemies=enemies,
            seed=seed,
            kind=BattleKind.DESCENT,
            owner=character.id,
            depth=descent.depth,
        )

    location = build_location(
        content,
        settings.world_seed,
        flow.session,
        epoch=node_rules.location_epoch(location_state),
    )
    node = location.node(flow.session.node)
    left = node_rules.standing_at(
        visit_seed(settings.world_seed, flow.session), location, location_state, node.index, now
    )
    # Волна и то, сколько из неё уже выбито, обе в семени: вторая стая в узле -
    # не первая заново (``domain/rules/nodes.py``).
    seed = derive(node_fight_seed(settings.world_seed, flow.session, left.wave), left.taken)
    enemies = fight_flow.spawn_for_node(
        content,
        seed=seed,
        biome=location.biome,
        level=max(1, node.level),
        rank=node.kind.rank,
    )
    return begin(
        content,
        battle_id=battle_id,
        attackers=side,
        enemies=enemies,
        seed=seed,
        kind=BattleKind.NODE,
        owner=character.id,
        city_id=flow.session.city_id,
        slot=flow.session.slot,
        node=node.index,
        wave=left.wave,
    )


async def _spawn_duel(
    message: Message,
    *,
    content: GameContent,
    character: Character,
    allies: tuple[Character, ...],
    flow: PlayState,
    characters: CharacterRepository,
    store: BattleStore,
    parties: PartyStore,
    battle_id: str,
) -> tuple[BattleSession | None, dict[int, Character]]:
    """Поединок с живым игроком - и с его отрядом, если он не один.

    Согласия не спрашивают: это вольная земля, и забор здесь другой - уровень,
    окно уровней и ставка из кармана (``domain/rules/pvp.py``). А вот ходить
    защищающийся будет сам: панель боя открывается у обоих.
    """
    target_id = int(flow.fight.removeprefix("pvp:") or 0)
    target = await characters.get(target_id)
    if target is None:
        await message.answer("Этого человека здесь больше нет.")
        return None, {}
    if await store.busy(target.id) is not None:
        await message.answer(f"{target.name} уже в бою. Дождитесь, чем это кончится.")
        return None, {}

    defenders: list[tuple[Character, bool]] = [(target, True)]
    party = await parties.of(target.id)
    if party is not None:
        for member_id in party.members:
            if member_id == target.id or len(defenders) >= party_rules.MAX_MEMBERS:
                continue
            other = await characters.get(member_id)
            if other is None or await store.busy(other.id) is not None:
                continue
            defenders.append((other, True))

    seed = derive("duel", character.id, target.id, flow.session.node, battle_id)
    return begin(
        content,
        battle_id=battle_id,
        attackers=[(character, True), *((one, True) for one in allies)],
        defenders=defenders,
        seed=seed,
        kind=BattleKind.DUEL,
        owner=character.id,
        city_id=flow.session.city_id,
        slot=flow.session.slot,
        node=flow.session.node,
    )


async def _spawn_arena(
    message: Message,
    *,
    content: GameContent,
    character: Character,
    characters: CharacterRepository,
    battle_id: str,
) -> tuple[BattleSession | None, dict[int, Character]]:
    """Круг арены: ставка вперёд, противник - персонаж под управлением движка.

    Ждать на арене по-прежнему некого: за противника ходит движок. Но дерётся он
    теперь своим - своим оружием, своими умениями и своей инициативой, - а не
    выдуманным числом урона, одинаковым для воина и мага (ADR 0021).
    """
    other = await characters.arena_opponent(
        level=character.level, window=arena_rules.LEVEL_WINDOW, exclude_id=character.id
    )
    if other is None:
        await send_screen(
            message,
            arena_screens.arena_screen(
                character, notice="На арене сейчас не с кем драться. Зайдите позже."
            ),
        )
        return None, {}

    paid, stake = arena_rules.place_stake(character)
    await characters.save(paid)
    seed = derive("arena", paid.id, other.id, paid.arena_wins + paid.arena_losses)
    logger.info("arena_round_started", character_id=paid.id, opponent_id=other.id, stake=stake)
    economy_log.record(economy_log.ARENA_STAKE, -stake, character_id=paid.id)
    return begin(
        content,
        battle_id=battle_id,
        attackers=[(paid, True)],
        defenders=[(other, False)],
        seed=seed,
        kind=BattleKind.ARENA,
        owner=paid.id,
    )


# --- один ход ---------------------------------------------------------


async def fight(
    message: Message,
    state: FSMContext,
    content: GameContent,
    settings: Settings,
    characters: CharacterRepository,
    inventory: InventoryRepository,
    locations: LocationStateCache,
    state_cache: StateCache,
    parties: PartyStore,
) -> None:
    """Одно сообщение - один ход. Никогда молчание, никогда два сообщения."""
    if message.from_user is None or message.text is None:
        return

    character = await characters.get_active(message.from_user.id)
    data = await state.get_data()
    flow = PlayState.deserialise(data[PLAY_KEY]) if data.get(PLAY_KEY) else PlayState()
    store = BattleStore(state_cache)

    if character is None:  # pragma: no cover - сюда доходит только с персонажем
        await state.clear()
        return

    battle_id = str(data.get(STATE_KEY) or "")
    session = await store.load(battle_id) if battle_id else None
    if session is None:
        # Боя нет: он кончился, истёк срок или состояние соврало.
        await _leave_to_play(message, state, content, settings, flow, character)
        return

    viewer = session.combatant_of(character.id)
    if viewer is None:  # pragma: no cover - в чужой бой не попадают
        await _leave_to_play(message, state, content, settings, flow, character)
        return

    if session.state.is_over:
        await _after_the_fight(
            message, state, content, settings, character, flow, characters, state_cache, parties
        )
        return

    roster = await _roster(session, characters)

    if await state.get_state() == Play.combat_bag.state:
        await _use_from_bag(
            message,
            state,
            content,
            character,
            session,
            roster,
            viewer,
            inventory,
            characters,
            locations,
            settings,
            state_cache,
        )
        return

    if labels.BAG.matches(message.text) or message.text.strip().casefold() in {"/сумка", "/bag"}:
        await _open_bag(message, state, content, character, inventory)
        return

    before = session
    session, notice = fight_flow.advance(content, roster, session, viewer.id, message.text)
    await _store_and_show(
        message,
        state,
        content,
        character,
        session,
        before,
        roster,
        viewer,
        flow,
        notice,
        characters,
        inventory,
        locations,
        settings,
        state_cache,
    )


async def _roster(session: BattleSession, characters: CharacterRepository) -> dict[int, Character]:
    """Персонажи всех героев боя, прочитанные заново.

    Заново - потому что между двумя ходами игрок мог надеть другой меч или
    выучить умение: панель обязана обещать то, что нажатие сделает.
    """
    loaded: dict[int, Character] = {}
    for one in session.participants():
        stored = await characters.get(one.character_id)
        if stored is not None:
            loaded[one.character_id] = stored
    return roster_for(session, loaded)


def _moved(before: BattleSession, after: BattleSession) -> bool:
    """Случился ли ход на самом деле.

    Отказ - пустой слот, откат, не то оружие - ходом не считается, и остальным
    участникам о нём знать незачем: у них ничего не изменилось.
    """
    if after.state.is_over:
        return True
    if (before.state.round, before.state.cursor) != (after.state.round, after.state.cursor):
        return True
    return any(event.kind not in REFUSALS for event in after.state.events)


async def _store_and_show(
    message: Message,
    state: FSMContext,
    content: GameContent,
    character: Character,
    session: BattleSession,
    before: BattleSession,
    roster: Mapping[int, Character],
    viewer: Combatant,
    flow: PlayState,
    notice: str,
    characters: CharacterRepository,
    inventory: InventoryRepository,
    locations: LocationStateCache,
    settings: Settings,
    state_cache: StateCache,
) -> None:
    """Сохранить то, что ход изменил, и ответить ровно одним экраном каждому."""
    store = BattleStore(state_cache)
    if session.state.is_over:
        await _finish(
            message,
            state,
            content,
            settings,
            session,
            roster,
            character,
            flow,
            characters,
            inventory,
            locations,
            state_cache,
        )
        return

    await store.save(session)
    await state.set_state(Play.combat)
    await state.update_data({STATE_KEY: session.id})
    if not _moved(before, session):
        # Ничего не произошло: отвечаем только тому, кто нажал.
        await send_screen(
            message, fight_flow.render(content, character, session, viewer.id, notice)
        )
        return
    await _broadcast(
        message,
        content=content,
        session=session,
        roster=roster,
        actor_id=character.id,
        notice=notice,
        storage=_storage_of(state),
    )


# --- рассылка ---------------------------------------------------------


def _storage_of(state: FSMContext) -> BaseStorage:
    """Хранилище автомата, в котором стоит и этот игрок, и все остальные."""
    return state.storage


async def _remote_state(bot: Bot, storage: BaseStorage, user_id: int) -> FSMContext:
    """Автомат другого игрока. В личном чате его номер и есть номер чата."""
    return FSMContext(
        storage=storage,
        key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id),
    )


async def _broadcast(
    message: Message,
    *,
    content: GameContent,
    session: BattleSession,
    roster: Mapping[int, Character],
    actor_id: int,
    notice: str = "",
    storage: BaseStorage | None = None,
    emoji: bool = False,
    payouts: Mapping[int, Payout] | None = None,
) -> None:
    """Показать бой каждому, кто в нём стоит.

    Тот, кто нажал, получает ответ на своё сообщение; остальные - новое
    сообщение туда, где они сейчас. Одно действие - одно сообщение каждому
    (``docs/accessibility.md``, правило 3).
    """
    bot = message.bot
    for one in session.live_participants():
        character = roster.get(one.id)
        if character is None:
            continue
        payout = (payouts or {}).get(one.character_id, Payout())
        screen = fight_flow.render(
            content,
            character,
            session,
            one.id,
            notice if one.character_id == actor_id else "",
            extra=payout.extra,
            rows=payout.rows,
            gold_lost=payout.gold_lost,
            experience=payout.experience,
            gold=payout.gold,
            loot=payout.loot,
        )
        if one.character_id == actor_id:
            await send_screen(message, screen, emoji=emoji)
        elif bot is not None:
            if storage is not None:
                remote = await _remote_state(bot, storage, one.user_id)
                await remote.set_state(Play.combat)
                await remote.update_data({STATE_KEY: session.id})
            delivered = await push_screen(bot, one.user_id, screen)
            if not delivered:
                logger.info("battle_screen_undelivered", telegram_id=one.user_id)
        if payout.level_up:
            await _announce_level(message, one, payout.level_up, screen, actor_id=actor_id)


async def _announce_level(
    message: Message, one: Combatant, report: str, screen: Screen, *, actor_id: int
) -> None:
    """Второе сообщение за одно действие, и единственное такое в игре."""
    if one.character_id == actor_id:
        await send_text(message, report, screen)
        return
    bot = message.bot
    if bot is None:  # pragma: no cover - у сообщения всегда есть бот
        return
    with_keyboard = Screen(id=screen.id, lines=(report,), rows=screen.rows)
    await push_screen(bot, one.user_id, with_keyboard)


async def _show(
    message: Message,
    state: FSMContext,
    content: GameContent,
    character: Character,
    session: BattleSession,
    *,
    storage: BaseStorage | None = None,
    emoji: bool = False,
    notice: str = "",
) -> None:
    """Показать бой одному игроку - тому, кто сейчас нажал."""
    one = session.combatant_of(character.id)
    if one is None:  # pragma: no cover
        return
    await state.set_state(Play.combat)
    await state.update_data({STATE_KEY: session.id})
    await send_screen(
        message,
        fight_flow.render(content, character, session, one.id, notice),
        emoji=emoji,
    )


# --- что бой сделал с персонажами -------------------------------------


async def _finish(
    message: Message,
    state: FSMContext,
    content: GameContent,
    settings: Settings,
    session: BattleSession,
    roster: Mapping[int, Character],
    actor: Character,
    flow: PlayState,
    characters: CharacterRepository,
    inventory: InventoryRepository,
    locations: LocationStateCache,
    state_cache: StateCache,
) -> None:
    """Заплатить по кончившемуся бою - один раз, за всех, и показать итог."""
    store = BattleStore(state_cache)
    payouts: dict[int, Payout] = {}
    updated: dict[int, Character] = {}

    heroes = session.participants()
    winners = tuple(one for one in heroes if session.state.verdict_for(one.id) is Verdict.VICTORY)
    losers = tuple(one for one in heroes if session.state.verdict_for(one.id) is Verdict.DEFEAT)

    for one in heroes:
        character = roster.get(one.id)
        if character is None:
            continue
        payouts[one.character_id] = Payout()
        updated[one.character_id] = character

    if session.is_duel:
        await _settle_duel(session, roster, winners, losers, payouts, updated)
        _carry_wounds(content, session, updated)
    elif session.is_arena:
        # Круг арены не стоит десятой доли кошелька: он стоит ставки, и её уже
        # взяли перед боем. Раны при этом остаются - арена лечит только гордость.
        _carry_wounds(content, session, updated)
        _settle_arena(session, roster, payouts, updated)
    else:
        await _settle_world(content, session, roster, winners, losers, payouts, updated, inventory)

    # Спуск и узел считаются по владельцу похода: остальные шли с ним.
    owner = next((one for one in heroes if one.character_id == session.owner), None)
    next_flow = flow
    if owner is not None and session.state.verdict_for(owner.id) is Verdict.VICTORY:
        payout = payouts.get(session.owner, Payout())
        if session.in_descent:
            next_flow = _after_descent(session, flow, payout, updated.get(session.owner))
            if session.depth >= turning_rules.descent_depth(
                updated.get(session.owner, roster[owner.id])
            ):
                bottom = await _pay_the_bottom(
                    content,
                    updated[session.owner],
                    session,
                    payout,
                    inventory,
                    level=max(1, flow.descent.level),
                )
                updated[session.owner] = bottom
        elif session.kind is BattleKind.NODE:
            line = await _take_node(content, session, locations, settings)
            if line:
                payout.extra.append(line)

    for character_id, character in updated.items():
        fighter = session.combatant_of(character_id)
        if fighter is None or not fighter.live:
            # Слепок арены не сохраняется: он дрался, но ничего не терял.
            continue
        before = roster.get(fighter.id)
        await characters.save(character)
        if before is not None:
            grown = progression.growth(content, before.level, character.level)
            if grown is not None:
                payouts[character_id].level_up = level_up_report(
                    content, character, derived_stats(content, character), grown
                )

    finished = replace(session, settled=True)
    await store.release(finished)
    await _land_everyone(message, state, content, finished, updated, flow, next_flow)
    await _broadcast(
        message,
        content=content,
        session=finished,
        roster={
            one.id: updated.get(one.character_id, roster[one.id])
            for one in heroes
            if one.id in roster
        },
        actor_id=actor.id,
        storage=_storage_of(state),
        payouts=payouts,
    )


def _carry_wounds(
    content: GameContent, session: BattleSession, updated: dict[int, Character]
) -> None:
    """Записать раны всем, кого бой потрепал. Поле боя не лечит."""
    for one in session.participants():
        character = updated.get(one.character_id)
        if character is not None:
            updated[one.character_id] = adventure.carry_wounds(
                content, character, session.state, one.id
            )


async def _settle_world(
    content: GameContent,
    session: BattleSession,
    roster: Mapping[int, Character],
    winners: tuple[Combatant, ...],
    losers: tuple[Combatant, ...],
    payouts: dict[int, Payout],
    updated: dict[int, Character],
    inventory: InventoryRepository,
) -> None:
    """Расчёт боя с миром: опыт, золото, добыча - и всё это делится на отряд.

    Отряд не делает бой выгоднее: противник тот же, а плата делится поровну
    (``domain/rules/party.split``). Добыча раздаётся по кругу, чтобы собравший
    отряд не забирал всё ценное только потому, что он первый в списке.
    """
    state = session.state
    if winners:
        experience = party_rules.split(state.experience, len(winners))
        gold = party_rules.split(state.gold, len(winners))
        shares = party_rules.distribute(
            state.loot,
            tuple(one.character_id for one in winners),
            rng(derive(session.seed, "loot")),
        )
        for index, one in enumerate(winners):
            character = updated[one.character_id]
            share_loot = shares.get(one.character_id, ())
            won = adventure.resolve_victory(
                content,
                character,
                state,
                one.id,
                experience=experience[index],
                gold=gold[index],
                loot=share_loot,
            )
            character = won.character
            economy_log.record(economy_log.FIGHT, won.gold, character_id=character.id)
            for item_id in share_loot:
                if content.has_item(item_id):
                    await inventory.add(character.id, item_id, 1)
            payout = payouts[one.character_id]
            payout.experience = won.experience
            payout.gold = won.gold
            payout.loot = tuple(
                content.item(item_id).name for item_id in share_loot if content.has_item(item_id)
            )
            payout.extra.extend(
                f"Задание «{step.quest.name}»: {step.progress} из {step.quest.target_count}."
                for step in won.quest_steps
            )
            # Выигранный бой - один из шагов обучения, и засчитывает его сама
            # победа, где бы она ни случилась.
            marked = tutorial_rules.complete(character, TutorialTask.FIGHT)
            if marked is not None:
                character = marked
                payout.extra.append(tutorial_screens.completion_line(TutorialTask.FIGHT, character))
            updated[one.character_id] = character

    for one in losers:
        character = updated[one.character_id]
        lost = adventure.resolve_defeat(content, character)
        updated[one.character_id] = lost.character
        payouts[one.character_id].gold_lost = lost.gold_lost
        economy_log.record(economy_log.DEFEAT, -lost.gold_lost, character_id=character.id)

    for one in session.participants():
        verdict = state.verdict_for(one.id)
        if verdict in {Verdict.FLED, Verdict.AVOIDED}:
            updated[one.character_id] = adventure.carry_wounds(
                content, updated[one.character_id], state, one.id
            )


async def _settle_duel(
    session: BattleSession,
    roster: Mapping[int, Character],
    winners: tuple[Combatant, ...],
    losers: tuple[Combatant, ...],
    payouts: dict[int, Payout],
    updated: dict[int, Character],
) -> None:
    """Ставка поединка: десятая доля с каждого проигравшего - победителям."""
    if not winners or not losers:
        for one in session.participants():
            payouts[one.character_id].extra.append("Поединок кончился ничем.")
        return

    won, lost, spoils = pvp_rules.settle_sides(
        tuple(updated[one.character_id] for one in winners),
        tuple(updated[one.character_id] for one in losers),
    )
    for one, character in zip(winners, won, strict=True):
        updated[one.character_id] = character
        payouts[one.character_id].gold = spoils.gold // max(1, len(winners))
        payouts[one.character_id].extra.append(
            f"Поединок выигран. С побеждённых снято золота: {spoils.gold}."
        )
        economy_log.record(
            economy_log.DUEL, spoils.gold // max(1, len(winners)), character_id=character.id
        )
    for one, character in zip(losers, lost, strict=True):
        before = updated[one.character_id]
        updated[one.character_id] = character
        taken = before.gold - character.gold
        payouts[one.character_id].gold_lost = taken
        payouts[one.character_id].extra.append(
            f"Поединок проигран. Снято золота: {taken}. Золото в банке не трогают."
        )
        economy_log.record(economy_log.DUEL, -taken, character_id=character.id)


def _settle_arena(
    session: BattleSession,
    roster: Mapping[int, Character],
    payouts: dict[int, Payout],
    updated: dict[int, Character],
) -> None:
    """Круг арены: выплата или ставка, оставшаяся у арены."""
    for one in session.live_participants():
        verdict = session.state.verdict_for(one.id)
        if verdict not in {Verdict.VICTORY, Verdict.DEFEAT}:
            continue
        result = arena_rules.settle(updated[one.character_id], won=verdict is Verdict.VICTORY)
        updated[one.character_id] = result.character
        economy_log.record(
            economy_log.ARENA_PAYOUT,
            result.payout,
            character_id=result.character.id,
            detail=f"held {result.held}",
        )
        payouts[one.character_id].extra.append(arena_screens.round_line(result))


def _after_descent(
    session: BattleSession, flow: PlayState, payout: Payout, character: Character | None
) -> PlayState:
    """Предложить следующий этаж спуска. Узел считает своё в другом месте."""
    if character is None:  # pragma: no cover
        return flow
    depth = turning_rules.descent_depth(character)
    if session.depth >= depth:
        payout.extra.append("Спуск пройден до дна. Дальше только камень.")
        return replace(flow, descent=Descent())

    payout.extra.append(f"Пройдено схваток: {session.depth} из {depth}.")
    payout.rows.append((labels.DUNGEON_DEEPER, labels.DUNGEON_LEAVE))
    return replace(flow, descent=replace(flow.descent, depth=session.depth + 1))


async def _pay_the_bottom(
    content: GameContent,
    character: Character,
    session: BattleSession,
    payout: Payout,
    inventory: InventoryRepository,
    *,
    level: int,
) -> Character:
    """Выдать то, ради чего спуск и затевался.

    Платит дно по уровню спуска, а не по уровню вошедшего: глубокий спуск идёт
    выше по полосе, поэтому и дно у него богаче (ADR 0019, ADR 0028).
    """
    prize = adventure.descent_prize(
        content,
        character,
        level=level,
        seed=derive("descent-prize", session.id, session.depth),
    )
    economy_log.record(economy_log.DESCENT, prize.gold, character_id=prize.character.id)
    if prize.item_id and content.has_item(prize.item_id):
        await inventory.add(prize.character.id, prize.item_id, 1)
        found = f" Со дна поднято: {content.item(prize.item_id).name}."
    else:  # pragma: no cover - содержимое всегда что-то держит на этом уровне
        found = ""
    payout.extra.append(f"Дно спуска: {prize.gold} золота и {prize.experience} опыта.{found}")
    return prize.character


async def _take_node(
    content: GameContent,
    session: BattleSession,
    locations: LocationStateCache,
    settings: Settings,
) -> str:
    """Забрать из узла побеждённую стаю и сказать, что там ещё осталось."""
    from mmorpg.presentation.telegram.handlers.play import take_from_node

    visit = LocationSession(city_id=session.city_id, slot=session.slot, node=session.node)
    if not location_known(content, visit):
        return ""
    now = int(time.time())
    node_state = await take_from_node(
        content, visit, session.node, locations, now, settings, wave=session.wave
    )
    location = build_location(
        content, settings.world_seed, visit, epoch=node_rules.location_epoch(node_state)
    )
    left = node_rules.standing_at(
        visit_seed(settings.world_seed, visit), location, node_state, session.node, now
    )
    if left.empty:
        return "Узел вычищен: новые противники придут сюда через несколько минут."
    return f"В узле осталось противников: {left.left} из {left.size}."


async def _land_everyone(
    message: Message,
    state: FSMContext,
    content: GameContent,
    session: BattleSession,
    updated: Mapping[int, Character],
    flow: PlayState,
    next_flow: PlayState,
) -> None:
    """Куда каждый попадёт, нажав «Назад» с экрана итога."""
    bot = message.bot
    storage = _storage_of(state)
    for one in session.live_participants():
        character = updated.get(one.character_id)
        if character is None:  # pragma: no cover
            continue
        lost = session.state.verdict_for(one.id) is Verdict.DEFEAT
        if one.character_id == session.owner:
            landing = _back_to_city(flow, character) if lost else next_flow
        else:
            landing = _back_to_city(flow, character) if lost else replace(flow, fight="")
        if message.from_user is not None and one.user_id == message.from_user.id:
            await state.update_data({PLAY_KEY: landing.serialise()})
            continue
        if bot is None:  # pragma: no cover
            continue
        remote = await _remote_state(bot, storage, one.user_id)
        await remote.set_state(Play.combat)
        # Номер боя остаётся: экран итога читается по нему, а занятость с
        # персонажа уже снята (``BattleStore.release``).
        await remote.update_data({PLAY_KEY: landing.serialise(), STATE_KEY: session.id})


def _back_to_city(flow: PlayState, character: Character) -> PlayState:
    """Проигранный бой кончает вылазку: ни локации, ни спуска, назад в город."""
    return replace(
        flow,
        session=LocationSession(),
        descent=Descent(),
        city_id=character.city_id,
        screen=ScreenId.MAIN_MENU,
        stack=NavigationStack((ScreenId.MAIN_MENU,)),
        fight="",
    )


async def _tell(message: Message, telegram_id: int, text: str) -> None:
    """Одна строка тому, кто сейчас не смотрит в игру."""
    bot = message.bot
    if bot is None:  # pragma: no cover - у сообщения всегда есть бот
        return
    try:
        await bot.send_message(chat_id=telegram_id, text=text)
    except TelegramAPIError:
        logger.info("battle_notice_undelivered", telegram_id=telegram_id)


# --- когда всё кончилось ----------------------------------------------


async def _after_the_fight(
    message: Message,
    state: FSMContext,
    content: GameContent,
    settings: Settings,
    character: Character,
    flow: PlayState,
    characters: CharacterRepository,
    state_cache: StateCache,
    parties: PartyStore,
) -> None:
    """Экран итога - настоящий экран: он отвечает на каждую кнопку."""
    text = message.text or ""
    if labels.MAIN_MENU.matches(text) or text.strip().casefold() in {"/меню", "/menu"}:
        home = replace(
            flow,
            screen=ScreenId.MAIN_MENU,
            stack=NavigationStack((ScreenId.MAIN_MENU,)),
            fight="",
            notice="",
        )
        await state.update_data({STATE_KEY: "", PLAY_KEY: home.serialise()})
        await state.set_state(Play.main_menu)
        from mmorpg.presentation.telegram.handlers.play import render_play

        await render_play(message, content, settings, home, character)
        return

    if labels.DUNGEON_DEEPER.matches(text) and flow.descent.active:
        await state.update_data({STATE_KEY: ""})
        await open_fight(
            message,
            state,
            content=content,
            settings=settings,
            character=character,
            flow=replace(flow, fight="dungeon"),
            characters=characters,
            state_cache=state_cache,
            parties=parties,
            storage=_storage_of(state),
        )
        return
    await _leave_to_play(message, state, content, settings, flow, character)


async def _leave_to_play(
    message: Message,
    state: FSMContext,
    content: GameContent,
    settings: Settings,
    flow: PlayState,
    character: Character,
) -> None:
    """Бросить бой и вернуть игрока на тот экран, с которого он пришёл."""
    from mmorpg.presentation.telegram.handlers.play import render_play

    stack, previous = flow.stack.pop()
    target = previous or (ScreenId.LOCATION if flow.session.active else ScreenId.CITY)
    if target is ScreenId.LOCATION and not flow.session.active:
        target = ScreenId.CITY
    landing = replace(flow, screen=target, stack=stack, fight="", notice="")
    await state.update_data({STATE_KEY: "", PLAY_KEY: landing.serialise()})
    await state.set_state(STATE_FOR_SCREEN[landing.screen])
    await render_play(message, content, settings, landing, character)


# --- сумка ------------------------------------------------------------


async def _open_bag(
    message: Message,
    state: FSMContext,
    content: GameContent,
    character: Character,
    inventory: InventoryRepository,
) -> None:
    entries = await _consumables(content, character, inventory)
    await state.set_state(Play.combat_bag)
    await send_screen(message, combat_screens.bag_screen(content, entries))


async def _consumables(
    content: GameContent, character: Character, inventory: InventoryRepository
) -> tuple[tuple[str, str, int], ...]:
    held = await inventory.list_items(character.id)
    return tuple(
        (entry.item_id, content.item(entry.item_id).name, entry.quantity)
        for entry in held
        if content.has_item(entry.item_id)
        and content.item(entry.item_id).kind is ItemKind.CONSUMABLE
    )


async def _use_from_bag(
    message: Message,
    state: FSMContext,
    content: GameContent,
    character: Character,
    session: BattleSession,
    roster: Mapping[int, Character],
    viewer: Combatant,
    inventory: InventoryRepository,
    characters: CharacterRepository,
    locations: LocationStateCache,
    settings: Settings,
    state_cache: StateCache,
) -> None:
    """Расходник стоит хода, как и всякое другое действие."""
    entries = await _consumables(content, character, inventory)
    text = (message.text or "").strip()
    chosen = next(
        (item_id for item_id, name, _ in entries if label(f"{name} — использовать").matches(text)),
        None,
    )
    if chosen is None:
        await state.set_state(Play.combat)
        await send_screen(
            message,
            fight_flow.render(
                content, character, session, viewer.id, "Вернулись в бой, ничего не потрачено."
            ),
        )
        return

    current = session.state.active
    if current is None or current.id != viewer.id:
        await state.set_state(Play.combat)
        await send_screen(
            message,
            fight_flow.render(
                content, character, session, viewer.id, "Сейчас не ваш ход. Зелье не тронуто."
            ),
        )
        return

    if not await inventory.remove(character.id, chosen, 1):  # pragma: no cover - только гонка
        await send_screen(message, combat_screens.bag_screen(content, entries, "Этого уже нет."))
        return

    resolved = act(
        content,
        roster,
        session.state,
        BattleAction(kind=ActionKind.ITEM, item_id=chosen),
        session.seed,
    )
    data = await state.get_data()
    flow = PlayState.deserialise(data[PLAY_KEY]) if data.get(PLAY_KEY) else PlayState()
    await state.set_state(Play.combat)
    await _store_and_show(
        message,
        state,
        content,
        character,
        replace(session, state=resolved),
        session,
        roster,
        viewer,
        flow,
        "",
        characters,
        inventory,
        locations,
        settings,
        state_cache,
    )
