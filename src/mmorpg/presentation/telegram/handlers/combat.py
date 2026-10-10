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

import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from uuid import uuid4

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.types import Message

from mmorpg import economy_log
from mmorpg.application.battle_snapshots import decode, encode
from mmorpg.application.operations import atomic_action
from mmorpg.application.services.battle import (
    BATTLE_TTL,
    BattleKind,
    BattleSession,
    BattleStore,
    begin,
    roster_for,
)
from mmorpg.application.services.city_event import CityEvents
from mmorpg.application.services.guild import GuildStore
from mmorpg.application.services.long_goal import LongGoals
from mmorpg.application.services.party import PartyStore
from mmorpg.application.services.recruitment import Recruitment
from mmorpg.application.services.war_score import WarScoring
from mmorpg.config import Settings
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import ActionKind, BattleAction, Combatant, EventKind, Verdict
from mmorpg.domain.entities.content import GameContent, ItemKind
from mmorpg.domain.entities.effects import ActiveEffect
from mmorpg.domain.entities.location import LocationState
from mmorpg.domain.ports.repositories import (
    CharacterRepository,
    InventoryRepository,
    LocationStateCache,
    StateCache,
)
from mmorpg.domain.procgen.seeds import derive, rng, rotation_index
from mmorpg.domain.rules import adventure, expedition, participation, progression, quests
from mmorpg.domain.rules import arena as arena_rules
from mmorpg.domain.rules import digest as digest_rules
from mmorpg.domain.rules import dungeon as dungeon_rules
from mmorpg.domain.rules import guild as guild_rules
from mmorpg.domain.rules import guild_contract as contract_rules
from mmorpg.domain.rules import mood as mood_rules
from mmorpg.domain.rules import nodes as node_rules
from mmorpg.domain.rules import party as party_rules
from mmorpg.domain.rules import pvp as pvp_rules
from mmorpg.domain.rules import roamer as roamer_rules
from mmorpg.domain.rules import tutorial as tutorial_rules
from mmorpg.domain.rules.combat import act, join_battle, joinable
from mmorpg.domain.rules.stats import derived_stats
from mmorpg.domain.rules.tutorial import TutorialTask
from mmorpg.logging import get_logger
from mmorpg.presentation.telegram import digest_claim
from mmorpg.presentation.telegram.flows import combat as fight_flow
from mmorpg.presentation.telegram.flows.play import (
    build_location,
    descent_fight_seed,
    dungeon_run_seed,
    location_known,
    node_pack_seed,
    visit_seed,
)
from mmorpg.presentation.telegram.flows.state import Descent, LocationSession, PlayState
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.messaging import push_screen, send_screen, send_text
from mmorpg.presentation.telegram.screens import arena as arena_screens
from mmorpg.presentation.telegram.screens import combat as combat_screens
from mmorpg.presentation.telegram.screens import dungeon as dungeon_screens
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
        EventKind.ITEM_REFUSED,
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


#: Срок отметки только в временном адаптере. PostgreSQL удерживает занятую
#: встречу вместе с незавершённым боем без часового срока (ADR 0090).
ENGAGED_TTL = BATTLE_TTL


# --- начало боя -------------------------------------------------------


@atomic_action
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
    locations: LocationStateCache | None = None,
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

    if flow.fight.startswith("join:"):
        # Вмешаться - это не завести бой, а войти в чужой (ADR 0065): ни сборки
        # противника, ни занятости стаи здесь нет, стая уже занята тем боем.
        await _join_fight(
            message,
            state,
            content=content,
            settings=settings,
            character=character,
            flow=flow,
            characters=characters,
            store=store,
            locations=locations,
            location_state=location_state or LocationState(),
            storage=storage,
            emoji=emoji,
            now=now,
        )
        return

    allies = await _party_of(character, parties, characters, store, flow=flow, content=content)
    if flow.descent.active and not flow.descent.encounter_id:
        flow = replace(flow, descent=replace(flow.descent, encounter_id=uuid4().hex))
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
        locations=locations,
        now=now,
    )
    if session is None:
        return

    session = replace(
        session,
        play_snapshot=flow.serialise(),
        roster_snapshot=json.dumps(encode(roster), ensure_ascii=False),
        epoch=node_rules.location_epoch(location_state or LocationState()),
        encounter=flow.descent.encounter(character.id) if flow.descent.roamer else "",
        roamer_stamp=flow.descent.stamp,
    )
    session = await store.pin(session, content)
    session = await store.save(session)
    landing = replace(flow, screen=ScreenId.COMBAT, fight="")
    await state.set_state(Play.combat)
    await state.update_data(
        {PLAY_KEY: landing.serialise(), STATE_KEY: session.id, "battle_version": session.version}
    )
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
    *,
    flow: PlayState | None = None,
    content: GameContent | None = None,
) -> tuple[Character, ...]:
    """Кто идёт в этот бой вместе с игроком.

    Занятые чужим боем остаются дома: в двух боях сразу не стоит никто
    (``domain/rules/party.py``).
    """
    party = await parties.of(character.id)
    if party is None:
        return ()
    recruitment = Recruitment(parties, characters)
    listing = await recruitment.for_party(party.leader_id)
    allowed = party.members
    if listing:
        goal_key = ""
        if flow and flow.fight == "dungeon":
            goal_key = (
                f"{flow.descent.city_id}:location:{flow.descent.slot}"
                if flow.descent.roamer
                else f"{flow.descent.city_id}:dungeon:{flow.descent.dungeon_id}"
            )
        elif flow and flow.fight.startswith("node:"):
            goal_key = f"{flow.session.city_id}:location:{flow.session.slot}"
        allowed = await recruitment.companions(party, goal_key=goal_key)
        if character.id not in allowed:
            return ()
        if content and await recruitment.with_content(content)._refusal(listing, character, party):
            return ()
    companions: list[Character] = []
    for member_id in party.members:
        if member_id == character.id or member_id not in allowed:
            continue
        if flow and member_id in flow.descent.excluded:
            continue
        other = await characters.get(member_id)
        if other is None or await store.busy(other.id) is not None:
            continue
        if listing:
            if other.city_id != character.city_id:
                continue
            if content and await recruitment.with_content(content)._refusal(listing, other, party):
                continue
        companions.append(other)
        if len(companions) + 1 >= party_rules.MAX_MEMBERS:
            break
    return tuple(companions)


def _picked_place(fight: str, free: Sequence[int]) -> int:
    """Место стаи, которое назвала ветка (ADR 0065).

    Нажатие со старой клавиатуры может назвать место, с которого стаю уже увели:
    тогда берётся первое стоящее - иначе кнопка вела бы в пустоту.
    """
    standing = tuple(free)
    named = fight.removeprefix("node:")
    place = int(named) if named.isdigit() else -1
    if place in standing:
        return place
    # Вычищенный узел ветка не пускает в бой вовсе (``flows/play``), поэтому
    # здесь остаётся только первое стоящее место - или самое первое, если узел
    # уже пуст.
    return standing[0] if standing else 0


async def _join_fight(
    message: Message,
    state: FSMContext,
    *,
    content: GameContent,
    settings: Settings,
    character: Character,
    flow: PlayState,
    characters: CharacterRepository,
    store: BattleStore,
    locations: LocationStateCache | None,
    location_state: LocationState,
    storage: BaseStorage | None,
    emoji: bool,
    now: int,
) -> None:
    """Вмешаться в бой, который уже идёт за стаю этого узла (ADR 0065).

    Ни стаи, ни платы это не удваивает: бой один, противник тот же, а плата
    делится на всех, кто в нём стоял (``domain/rules/party.py``). Очередь
    вмешавшийся получает со следующего круга - по своей инициативе, как все.
    """
    if locations is None or not location_known(content, flow.session):
        await message.answer("Того боя здесь уже нет.")
        return
    location = build_location(
        content,
        settings.world_seed,
        flow.session,
        epoch=node_rules.location_epoch(location_state),
    )
    left = node_rules.standing_at(
        visit_seed(settings.world_seed, flow.session),
        location,
        location_state,
        flow.session.node,
        now,
    )
    named = flow.fight.removeprefix("join:")
    place = int(named) if named.isdigit() else -1
    held = next(
        (
            one
            for one in await locations.engaged_at(
                flow.session.city_id,
                flow.session.slot,
                flow.session.node,
                wave=left.wave,
                now=now,
                ttl=ENGAGED_TTL,
                epoch=node_rules.location_epoch(location_state),
            )
            if one.slot == place
        ),
        None,
    )
    session = await store.load(held.battle_id) if held is not None else None
    if held is None or session is None or session.state.is_over:
        if held is not None:
            await locations.disengage(
                flow.session.city_id,
                flow.session.slot,
                flow.session.node,
                wave=left.wave,
                place=place,
                epoch=node_rules.location_epoch(location_state),
                battle_id=held.battle_id,
            )
        await message.answer("Тот бой уже кончился. Эта стая стоит свободно, нападайте сами.")
        return
    if not joinable(session.state):
        await message.answer("В том бою уже впятером: больше в строй никого не поставить.")
        return

    content = await store.content(session, content)
    joined, _ = join_battle(content, session.state, character)
    session = replace(session, state=joined)
    snapshot = (
        decode(json.loads(session.roster_snapshot))
        if session.roster_snapshot
        else await _roster(session, characters)
    )
    newcomer = session.combatant_of(character.id)
    assert newcomer is not None
    snapshot[newcomer.id] = character
    session = replace(session, roster_snapshot=json.dumps(encode(snapshot), ensure_ascii=False))
    session = await store.save(session)
    landing = replace(flow, screen=ScreenId.COMBAT, fight="")
    await state.set_state(Play.combat)
    await state.update_data(
        {PLAY_KEY: landing.serialise(), STATE_KEY: session.id, "battle_version": session.version}
    )
    await _broadcast(
        message,
        content=content,
        session=session,
        roster=snapshot,
        actor_id=character.id,
        storage=storage,
        emoji=emoji,
    )


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
    locations: LocationStateCache | None,
    now: int,
) -> tuple[BattleSession | None, dict[int, Character]]:
    """Кто с кем дерётся. Один сборщик на все виды боя."""
    battle_id = f"{character.id}-{uuid4().hex}"
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
        if descent.roamer:
            # Подземелье осело прямо в локации: биом её, а не самой глубокой
            # локации города, и одиночке спутников с собой не брать (ADR 0037).
            # Локацию могли убрать правкой, пока игрок был внутри: заход от этого
            # не падает, а идёт по самой глубокой земле города
            # (``Claude.md``, правило 8).
            biome = (
                city.location(descent.slot).biome
                if city.has_location(descent.slot)
                else city.locations[-1].biome
            )
            group_stakes = roamer_rules.GROUP_STAKES if descent.group else 1.0
            if not descent.group:
                side = [(character, True)]
        else:
            # Биом задаёт выбранное подземелье (ADR 0041). Запись старого образца
            # могла назвать подземелье, которого нет (числовой tier): заход от
            # этого не падает, а идёт по самой глубокой земле города (правило 8).
            biome = (
                city.dungeon(descent.dungeon_id).biome
                if city.has_dungeon(descent.dungeon_id)
                else city.locations[-1].biome
            )
            group_stakes = 1.0
        difficulty = dungeon_rules.difficulty_of(descent.difficulty)
        spec = dungeon_rules.spec_of(difficulty)
        room = dungeon_rules.room_of(descent.room)
        run_seed = dungeon_run_seed(settings.world_seed, descent)
        seed = descent_fight_seed(settings.world_seed, descent)
        conditions = dungeon_rules.conditions_for(run_seed, difficulty)
        enemies = fight_flow.spawn_for_node(
            content,
            seed=seed,
            biome=biome,
            level=descent.level,
            rank=room.rank,
            stakes=spec.stakes * dungeon_rules.ROOM_STAKES[room] * group_stakes,
            bounty=dungeon_rules.bounty_of(conditions) * group_stakes,
            # Городской спуск тянет своих подземных тварей; блуждающее подземелье
            # осело в локации и населено её живностью (ADR 0037, ADR 0042).
            dungeon=not descent.roamer,
            affix_chance=spec.affix_chance,
            affix_count=spec.affix_count,
        )
        encounter = (
            None
            if descent.roamer
            else expedition.meeting(content, descent.city_id, descent.dungeon_id, descent.layer)
        )
        if encounter is not None:
            enemies = expedition.foes(
                content,
                encounter,
                seed=seed,
                level=descent.level,
                stakes=spec.stakes,
                bounty=dungeon_rules.bounty_of(conditions),
                participants=len(side),
            )
        return begin(
            content,
            battle_id=battle_id,
            attackers=side,
            enemies=enemies,
            seed=seed,
            kind=BattleKind.DESCENT,
            owner=character.id,
            city_id=descent.city_id,
            slot=descent.slot,
            depth=descent.layer + 1,
            roamer=descent.roamer,
            opening_effects=_dungeon_opening_effects(conditions),
            participation_rule=descent.participation_rule,
            briefing=f"{encounter.name}. {encounter.briefing}" if encounter else "",
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
    # Место стаи в волне называет ветка: игрок выбрал именно её из тех, что
    # стоят в узле (ADR 0065). Нажатие со старой клавиатуры может назвать место,
    # которого уже нет, - тогда берётся первое стоящее.
    place = _picked_place(flow.fight, left.free)
    # Волна и место в ней - обе в семени: вторая стая в узле не первая заново
    # (``domain/rules/nodes.py``).
    seed = node_pack_seed(
        settings.world_seed,
        flow.session,
        index=node.index,
        wave=left.wave,
        place=place,
    )
    if locations is not None:
        # Стая занимается до боя и одним движением: двое, нажавших на одного и
        # того же волка, не заведут двух боёв с ним (ADR 0065).
        held = await locations.engage(
            flow.session.city_id,
            flow.session.slot,
            node.index,
            wave=left.wave,
            place=place,
            battle_id=battle_id,
            name=character.name,
            character_id=character.id,
            now=now or int(time.time()),
            ttl=ENGAGED_TTL,
            epoch=node_rules.location_epoch(location_state),
        )
        if held is not None:
            if await store.load(held.battle_id) is not None:
                await message.answer(
                    f"За эту стаю уже дерётся {held.name}. "
                    "Вернитесь в узел: в этот бой можно вмешаться."
                )
                return None, {}
            # Бой, державший стаю, истлел: отметка отпускается, и стая наша.
            await locations.disengage(
                flow.session.city_id,
                flow.session.slot,
                node.index,
                wave=left.wave,
                place=place,
                epoch=node_rules.location_epoch(location_state),
                battle_id=held.battle_id,
            )
            await locations.engage(
                flow.session.city_id,
                flow.session.slot,
                node.index,
                wave=left.wave,
                place=place,
                battle_id=battle_id,
                name=character.name,
                character_id=character.id,
                now=now or int(time.time()),
                ttl=ENGAGED_TTL,
                epoch=node_rules.location_epoch(location_state),
            )
    # Прозвище-модификатор бывает только у сильного одиночки и хозяина логова, и
    # никогда у обычной стаи (ADR 0042); эпиков в локации мало (ADR 0034). В
    # выбитой и встревоженной округе эпик и хозяин логова злее (ADR 0055).
    odds = dungeon_rules.affix_odds(node.kind.rank, mood_rules.mood_of(location_state))
    enemies = fight_flow.spawn_for_node(
        content,
        seed=seed,
        biome=location.biome,
        level=max(1, node.level),
        rank=node.kind.rank,
        affix_chance=odds.chance,
        affix_count=odds.count,
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
        place=place,
        participation_rule=1,
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
    paid = await characters.save(paid)
    seed = derive("arena", paid.id, other.id, paid.arena_wins + paid.arena_losses)
    logger.info("arena_round_started", character_id=paid.id, opponent_id=other.id, stake=stake)
    economy_log.record(economy_log.ARENA_STAKE, -stake, character_id=paid.id)
    return begin(
        content,
        battle_id=battle_id,
        attackers=[(paid, True)],
        defenders=[(arena_rules.as_opponent(other), False)],
        seed=seed,
        kind=BattleKind.ARENA,
        owner=paid.id,
    )


# --- один ход ---------------------------------------------------------


@atomic_action
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
    guilds: GuildStore,
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
        await _leave_to_play(message, state, content, settings, flow, character, locations)
        return

    viewer = session.combatant_of(character.id)
    if viewer is None:  # pragma: no cover - в чужой бой не попадают
        await _leave_to_play(message, state, content, settings, flow, character, locations)
        return
    if viewer.character_id in session.departed:
        await _leave_to_play(message, state, content, settings, flow, character, locations)
        return

    if not session.content_version and not session.settled:
        session = await store.pin(session, content)
        owner = await characters.get(session.owner)
        owner_data = (
            await (await _remote_state(message.bot, state.storage, owner.user_id)).get_data()
            if owner is not None and message.bot is not None
            else data
        )
        session = replace(
            session,
            roster_snapshot=json.dumps(encode(await _roster(session, characters))),
            play_snapshot=str(owner_data.get(PLAY_KEY) or flow.serialise()),
        )
        if session.roamer:
            descent = PlayState.deserialise(session.play_snapshot).descent
            session = replace(
                session, encounter=descent.encounter(session.owner), roamer_stamp=descent.stamp
            )
        session = await store.save(session)
    content = await store.content(session, content)
    expected = data.get("battle_version")
    if match := re.search(r" — ход (\d+)$", message.text):
        expected = int(match[1])
        message = message.model_copy(update={"text": message.text[: match.start()]})
    assert message.text is not None
    refresh = labels.BATTLE_REFRESH.matches(message.text) or message.text.strip().casefold() in {
        "/обновить",
        "/refresh",
        "/продолжить",
        "/resume",
    }
    if (
        not session.state.is_over
        and expected is not None
        and int(expected) != session.version
        and not refresh
    ):
        await _show(
            message,
            state,
            content,
            character,
            session,
            notice="Бой уже изменился. Показано текущее состояние; прежнее действие не выполнено.",
        )
        return
    if message.text.strip().casefold() in {"/пауза", "/pause", "пауза"}:
        await _show(
            message,
            state,
            content,
            character,
            session,
            notice="Бой сохранён. Можно вернуться позже командой /продолжить. "
            "Остальные участники могут сдаться без ожидания вашего хода.",
        )
        return

    if session.state.is_over and not session.settled:
        await _finish(
            message,
            state,
            content,
            settings,
            session,
            await _roster(session, characters),
            character,
            flow,
            characters,
            inventory,
            locations,
            state_cache,
            guilds,
        )
        return
    if session.state.is_over:
        if refresh:
            await _show(message, state, content, character, session)
            return
        await _after_the_fight(
            message,
            state,
            content,
            settings,
            character,
            flow,
            characters,
            state_cache,
            parties,
            locations,
        )
        return

    roster = (
        decode(json.loads(session.roster_snapshot))
        if session.roster_snapshot
        else await _roster(session, characters)
    )
    character = roster.get(viewer.id, character)

    if refresh:
        await _show(message, state, content, character, session)
        return

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
            guilds,
        )
        return

    if labels.BAG.matches(message.text) or message.text.strip().casefold() in {"/сумка", "/bag"}:
        await _open_bag(message, state, content, character, inventory, session)
        return

    if fight_flow.wants_breakdown(content, character, session, viewer.id, message.text):
        # «Разбор боя» - не ход: тот же бой, другой экран, счётчик стоит.
        await send_screen(
            message,
            combat_screens.breakdown_screen(
                content, character, session.state, viewer.id, session.briefing
            ),
        )
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
        guilds,
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
    guilds: GuildStore,
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
            guilds,
        )
        return

    if not _moved(before, session):
        await _show(message, state, content, character, session, notice=notice)
        return
    departed = tuple(
        one
        for one in session.participants()
        if one.left and one.character_id not in session.departed and one.live
    )
    for one in departed:
        current = await characters.get(one.character_id)
        if current is not None:
            await characters.save(adventure.carry_wounds(content, current, session.state, one.id))
    if departed:
        session = replace(
            session, departed=(*session.departed, *(one.character_id for one in departed))
        )
    session = await store.save(session)
    await state.set_state(Play.combat)
    await state.update_data({STATE_KEY: session.id, "battle_version": session.version})
    await _broadcast(
        message,
        content=content,
        session=session,
        roster=roster,
        actor_id=character.id,
        notice=notice,
        storage=_storage_of(state),
    )
    if any(one.character_id == character.id for one in departed):
        await send_screen(message, fight_flow.render(content, character, session, viewer.id))


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
        screen = _versioned(screen, session)
        if one.character_id == actor_id:
            if storage is not None and bot is not None:
                own = await _remote_state(bot, storage, one.user_id)
                await own.update_data({"battle_version": session.version})
            await send_screen(message, screen, emoji=emoji)
        elif bot is not None:
            if storage is not None:
                remote = await _remote_state(bot, storage, one.user_id)
                await remote.set_state(Play.combat)
                await remote.update_data({STATE_KEY: session.id, "battle_version": session.version})
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
    if session.roster_snapshot and not session.state.is_over:
        character = decode(json.loads(session.roster_snapshot)).get(one.id, character)
    await state.set_state(Play.combat)
    restored = {STATE_KEY: session.id, "battle_version": session.version}
    if session.play_snapshot and not (await state.get_data()).get(PLAY_KEY):
        restored[PLAY_KEY] = session.play_snapshot
    await state.update_data(restored)
    screen = fight_flow.render(content, character, session, one.id, notice)
    if session.settled and session.results:
        result = json.loads(session.results).get(str(one.character_id))
        if result:
            screen = Screen(
                id=ScreenId.COMBAT,
                lines=tuple(result["lines"]),
                rows=tuple(tuple(label(text) for text in row) for row in result["rows"]),
            )
    await send_screen(message, _versioned(screen, session), emoji=emoji)


def _versioned(screen: Screen, session: BattleSession) -> Screen:
    if session.state.is_over:
        return screen
    return replace(
        screen,
        rows=tuple(
            tuple(label(f"{one.text} — ход {session.version}") for one in row)
            for row in screen.rows
        ),
    )


# --- что бой сделал с персонажами -------------------------------------


@atomic_action
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
    guilds: GuildStore,
    *,
    fault_hook: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """Заплатить по кончившемуся бою - один раз, за всех, и показать итог."""
    store = BattleStore(state_cache)

    async def checkpoint(name: str) -> None:
        if fault_hook is not None:
            await fault_hook(name)

    payouts: dict[int, Payout] = {}
    saved = await store.load(session.id)
    if saved is not None and saved.settled:
        await _show(message, state, content, actor, saved)
        return
    if saved is not None and saved.version != session.version:
        raise ValueError("Battle changed before settlement")
    # Текущий кошелёк нужен для выплаты, а сохранённые боевые умения — для хода.
    roster = await _roster(session, characters)
    if session.play_snapshot:
        flow = PlayState.deserialise(session.play_snapshot)
    updated: dict[int, Character] = {}
    # Гильдии участников боя с миром: их читают один раз, а спрашивают трижды -
    # надбавка, деяния и подряд (ADR 0076, 0077).
    places: Mapping[int, GuildPlace] = {}

    heroes = tuple(
        one for one in session.participants() if one.character_id not in session.departed
    )
    winners = tuple(one for one in heroes if session.state.verdict_for(one.id) is Verdict.VICTORY)
    if session.participation_rule and not session.is_duel and not session.is_arena:
        winners = tuple(one for one in winners if participation.eligible(one))
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
        # Поединок с человеком враждебной гильдии - очко войне (ADR 0077).
        await _score_war(
            guilds,
            settings,
            winners,
            losers,
            payouts,
            characters,
            session.id,
            content.long_goals.war_account_seconds,
        )
    elif session.is_arena:
        # Круг арены не стоит десятой доли кошелька: он стоит ставки, и её уже
        # взяли перед боем. Раны при этом остаются - арена лечит только гордость.
        _carry_wounds(content, session, updated)
        _settle_arena(session, roster, payouts, updated)
    else:
        places = await _guild_places(content, guilds, session, heroes)
        await _settle_world(
            content, session, roster, winners, losers, payouts, updated, inventory, places
        )
        await _record_deeds(guilds, places, winners)

    # Спуск и узел считаются по владельцу похода: остальные шли с ним.
    owner = next((one for one in heroes if one.character_id == session.owner), None)
    next_flow = flow
    if owner is not None and session.state.verdict_for(owner.id) is Verdict.VICTORY:
        payout = payouts.get(session.owner, Payout())
        if session.in_descent:
            next_flow = await _after_dungeon_room(
                content,
                settings,
                session,
                flow,
                payout,
                updated,
                inventory,
                character=updated.get(session.owner, roster[owner.id]),
                payouts=payouts,
                state_cache=state_cache,
            )
        elif session.kind is BattleKind.NODE:
            line = await _take_node(content, session, locations, settings)
            if line:
                payout.extra.append(line)

    if (
        session.in_descent
        and not next_flow.descent.active
        and session.participation_rule
        and not session.roamer
    ):
        full_credit = dict(
            participation.room_credit(flow.descent.credits, winners, flow.descent.layer)
        )
        entitled = tuple(
            one.character_id
            for one in winners
            if full_credit.get(one.character_id) == session.depth
        )
        await LongGoals(content, state_cache, characters, inventory, guilds).record_descent(
            entitled, flow.descent.dungeon_id, session.id
        )
    if session.roamer:
        await _settle_roamer(session, next_flow, owner, payouts, locations)

    if owner is not None and session.state.verdict_for(owner.id) is Verdict.VICTORY:
        for one in winners:
            full = dict(
                participation.room_credit(flow.descent.credits, winners, flow.descent.layer)
            )
            if (
                session.in_descent
                and not next_flow.descent.active
                and session.participation_rule
                and full.get(one.character_id) != session.depth
            ):
                continue
            await _pay_digest(
                content,
                settings,
                replace(session, owner=one.character_id),
                flow,
                next_flow,
                locations,
                state_cache,
                payouts,
                updated,
            )

    if places and winners:
        # Подряд гильдии считается после того, как стало ясно, кончился ли спуск:
        # «пройти спусков до логова» закрывает пройденное логово, а не комната.
        await _work_contracts(
            content,
            settings,
            guilds,
            places,
            session,
            next_flow,
            winners,
            payouts,
            original_flow=flow,
            state_cache=state_cache,
        )

    for character_id, character in updated.items():
        fighter = session.combatant_of(character_id)
        if fighter is None or not fighter.live:
            # Слепок арены не сохраняется: он дрался, но ничего не терял.
            continue
        before = roster.get(fighter.id)
        await characters.save(character)
        await checkpoint("after_character")
        if before is not None:
            grown = progression.growth(content, before.level, character.level)
            if grown is not None:
                payouts[character_id].level_up = level_up_report(
                    content, character, derived_stats(content, character), grown
                )

    if session.kind is BattleKind.NODE:
        events = CityEvents(content, state_cache, characters, inventory)
        event = events.event(session.city_id)
        event_stage = (await events.load(event)).stage if event else -1
        for one in winners:
            notice = await events.record(
                one.character_id,
                session.city_id,
                session.slot,
                "battle",
                session.id,
                expected_stage=event_stage,
            )
            if notice:
                payouts[one.character_id].extra.append(notice)
        # Бой кончился - стая отпущена: победа её забрала, поражение оставило
        # стоять, и в обоих случаях держать её незачем (ADR 0065).
        await locations.disengage(
            session.city_id,
            session.slot,
            session.node,
            wave=session.wave,
            place=session.place,
            battle_id=session.id,
            epoch=session.epoch,
        )
    await checkpoint("after_world")

    result_screens = {}
    for one in heroes:
        if one.id not in roster:
            continue
        payout = payouts.get(one.character_id, Payout())
        screen = fight_flow.render(
            content,
            updated.get(one.character_id, roster[one.id]),
            session,
            one.id,
            extra=payout.extra,
            rows=payout.rows,
            gold_lost=payout.gold_lost,
            experience=payout.experience,
            gold=payout.gold,
            loot=payout.loot,
        )
        result_screens[str(one.character_id)] = {
            "lines": screen.lines,
            "rows": [[one.text for one in row] for row in screen.rows],
        }
    finished = replace(
        session, settled=True, results=json.dumps(result_screens, ensure_ascii=False)
    )
    finished = await store.release(finished)
    await checkpoint("after_result")
    await _land_everyone(message, state, content, finished, updated, flow, next_flow)
    await checkpoint("after_screens")
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
    await checkpoint("after_replies")


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


@dataclass(frozen=True, slots=True)
class GuildPlace:
    """Гильдия героя на момент расчёта боя: имя и ступень (ADR 0076).

    Читается один раз на бой: и надбавка за победу, и записанное деяние берутся
    отсюда, а не двумя запросами.
    """

    guild_id: int
    name: str
    standing: guild_rules.Standing


async def _guild_places(
    content: GameContent,
    guilds: GuildStore,
    session: BattleSession,
    heroes: Sequence[Combatant],
) -> dict[int, GuildPlace]:
    """Гильдии участников боя с миром. Поединок и арена гильдии не касаются.

    Поединок - это чужое золото, а не добыча мира: платить с него гильдейскую
    надбавку и записывать деяния значило бы растить гильдию об игроков.
    """
    if session.is_duel or session.is_arena:
        return {}
    found: dict[int, GuildPlace] = {}
    for one in heroes:
        guild = await guilds.of(one.character_id)
        if guild is not None:
            found[one.character_id] = GuildPlace(
                guild_id=guild.id, name=guild.name, standing=guild_rules.standing(content, guild)
            )
    return found


async def _record_deeds(
    guilds: GuildStore,
    places: Mapping[int, GuildPlace],
    winners: Sequence[Combatant],
) -> None:
    """Выигранный бой - деяние гильдии и вклад того, кто его выиграл (ADR 0076)."""
    groups: dict[int, list[int]] = {}
    for one in winners:
        place = places.get(one.character_id)
        if place is not None:
            groups.setdefault(place.guild_id, []).append(one.character_id)
    for guild_id, members in groups.items():
        await guilds.record_group_deed(guild_id, tuple(members))


async def _settle_world(
    content: GameContent,
    session: BattleSession,
    roster: Mapping[int, Character],
    winners: tuple[Combatant, ...],
    losers: tuple[Combatant, ...],
    payouts: dict[int, Payout],
    updated: dict[int, Character],
    inventory: InventoryRepository,
    places: Mapping[int, GuildPlace],
) -> None:
    """Расчёт боя с миром: опыт, золото, добыча - и всё это делится на отряд.

    Новый бой использует единый фонд ADR 0093, прежний сохраняет деление ADR 0026.
    Добыча раздаётся по кругу, чтобы собравший
    отряд не забирал всё ценное только потому, что он первый в списке.
    """
    state = session.state
    if winners:
        splitter = participation.reward_shares if session.participation_rule else party_rules.split
        experience = splitter(state.experience, len(winners))
        gold = splitter(state.gold, len(winners))
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
                city_id=session.city_id,
                location_slot=session.slot if not session.in_descent else 0,
            )
            character = won.character
            economy_log.record(economy_log.FIGHT, won.gold, character_id=character.id)
            # Надбавка гильдии ложится поверх посчитанной платы и отдельной
            # строкой: гильдия - объединение, а не свёрток прибавок (ADR 0076).
            place = places.get(one.character_id)
            if place is not None and place.standing.pays:
                extra_exp, extra_gold = guild_rules.fight_bonus(
                    place.standing, experience=experience[index], gold=won.gold
                )
                if extra_gold:
                    character = character.with_gold(extra_gold)
                    economy_log.record(
                        economy_log.FIGHT, extra_gold, character_id=character.id, detail="guild"
                    )
                if extra_exp:
                    # Считается надбавка от доли боя, а прибавка к опыту - один
                    # раз, внутри ``grant_experience``; названное игроку число и
                    # есть то, что он получит (``progression.earned``).
                    granted = progression.earned(content, character, extra_exp)
                    character, _ = progression.grant_experience(content, character, extra_exp)
                    extra_exp = granted
                if extra_exp or extra_gold:
                    payouts[one.character_id].extra.append(
                        f"Гильдия «{place.name}»: сверх боя {extra_exp} опыта и "
                        f"{extra_gold} золота."
                    )
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
            # Сломанное остаётся надетым, но не даёт ничего, и узнать об этом
            # игрок обязан сразу, а не на экране характеристик (ADR 0057).
            payout.extra.extend(
                f"Сточено до конца: {name}. Вещь не даёт ничего, пока её не починят в кузнице."
                for name in won.broken
            )
            # Шкуры с туш: свежевание случается там, где случился бой, и только у
            # того, у кого в слоте нож (ADR 0062).
            if won.skinned_id and content.has_item(won.skinned_id):
                await inventory.add(character.id, won.skinned_id, won.skinned_count)
                payout.extra.append(
                    f"Снято шкур: {content.item(won.skinned_id).name}, "
                    f"{won.skinned_count} штук. Работы записано: {won.skinned_work}."
                )
            if won.knife_broken:
                payout.extra.append("Нож свежевателя сточился и рассыпался. Новый берут в лавке.")
            # Выигранный бой - один из шагов обучения, и засчитывает его сама
            # победа, где бы она ни случилась. За шаг платят тут же (ADR 0038);
            # уровень от опыта подхватит ``progression.growth`` в ``_finish``.
            marked = tutorial_rules.complete(character, TutorialTask.FIGHT)
            if marked is not None:
                reward = adventure.apply_tutorial_rewards(
                    content, marked, frozenset({TutorialTask.FIGHT})
                )
                character = reward.character
                for item_id, count in reward.items:
                    if content.has_item(item_id):
                        await inventory.add(character.id, item_id, count)
                if reward.gold:
                    economy_log.record(economy_log.TUTORIAL, reward.gold, character_id=character.id)
                payout.extra.append(tutorial_screens.completion_line(TutorialTask.FIGHT, character))
                payout.extra.extend(reward.lines)
            updated[one.character_id] = character

    for one in losers:
        character = updated[one.character_id]
        lost = adventure.resolve_defeat(content, character)
        updated[one.character_id] = lost.character
        payouts[one.character_id].gold_lost = lost.gold_lost
        payouts[one.character_id].extra.extend(
            f"Сточено до конца: {name}. Вещь не даёт ничего, пока её не починят в кузнице."
            for name in lost.broken
        )
        economy_log.record(economy_log.DEFEAT, -lost.gold_lost, character_id=character.id)

    for one in session.participants():
        verdict = state.verdict_for(one.id)
        if verdict is Verdict.VICTORY and one not in winners and one.character_id in updated:
            updated[one.character_id] = adventure.carry_wounds(
                content, updated[one.character_id], state, one.id
            )
            payouts[one.character_id].extra.append(
                "Награда и личный зачёт не начислены: в этой встрече вы не совершили действия."
            )
        if verdict in {Verdict.FLED, Verdict.AVOIDED} and one.character_id in updated:
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
        received = character.gold - updated[one.character_id].gold
        updated[one.character_id] = character
        payouts[one.character_id].gold = received
        payouts[one.character_id].extra.append(
            f"Поединок выигран. С побеждённых снято золота: {spoils.gold}. Ваша доля: {received}."
        )
        economy_log.record(economy_log.DUEL, received, character_id=character.id)
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


def _dungeon_opening_effects(
    conditions: tuple[dungeon_rules.Condition, ...],
) -> dict[int, list[ActiveEffect]]:
    """Условия захода как эффекты по сторонам: 0 - герой, 1 - враги (ADR 0036)."""
    hero: list[ActiveEffect] = []
    foes: list[ActiveEffect] = []
    for one in conditions:
        hero.extend(one.hero_effects)
        foes.extend(one.enemy_effects)
    result: dict[int, list[ActiveEffect]] = {}
    if hero:
        result[0] = hero
    if foes:
        result[1] = foes
    return result


def _heal_room_winners(
    content: GameContent, session: BattleSession, updated: dict[int, Character], percent: int
) -> None:
    """Победа в комнате латает раны: пассивного восстановления в данже нет."""
    if percent <= 0:
        return
    for one in session.participants():
        if session.state.verdict_for(one.id) is not Verdict.VICTORY:
            continue
        character = updated.get(one.character_id)
        if character is None:
            continue
        stats = derived_stats(content, character)
        current = character.health_or(stats.max_health)
        if current >= stats.max_health:
            continue
        restored = min(stats.max_health - current, max(1, stats.max_health * percent // 100))
        updated[one.character_id] = character.with_health(current + restored, stats.max_health)


async def _pay_digest(
    content: GameContent,
    settings: Settings,
    session: BattleSession,
    flow: PlayState,
    next_flow: PlayState,
    locations: LocationStateCache,
    state_cache: StateCache,
    payouts: dict[int, Payout],
    updated: dict[int, Character],
) -> None:
    """Закрыть дело со сводки, если победа его закрыла, и выдать надбавку (ADR 0053).

    Победа в названной локации закрывает ``HUNT``/``CULL``, пройденное логово
    названного спуска или блуждающий ход — ``DELVE``. Раз за переворот прилавка:
    разовость держит ключ со сроком в кэше (``digest_claim``). Строка идёт в
    ``extra``, как и счёт по заданиям.
    """
    hero = updated.get(session.owner)
    if hero is None:  # pragma: no cover - у похода всегда есть владелец
        return
    if not content.has_city(session.city_id):
        # Арена и поединок сюда не приводят (не NODE и не спуск), но бой без
        # города — не повод падать: сводка привязана к городу.
        return
    now = int(time.time())
    rotation = rotation_index(now, settings.shop_rotation_seconds)
    moods = await digest_claim.city_moods(locations, content, session.city_id, now=now)
    deeds = digest_rules.digest(
        content, settings.world_seed, session.city_id, rotation, hero.level, moods=moods
    )

    deed = None
    if session.in_descent and not next_flow.descent.active:
        deed = digest_claim.delve_deed(
            deeds,
            dungeon_id="" if session.roamer else flow.descent.dungeon_id,
            roamer_cleared=session.roamer,
        )
    elif session.kind is BattleKind.NODE and not session.in_descent:
        deed = digest_claim.cull_deed(deeds, session.slot)
        if deed is None:
            fallen = tuple(
                one.enemy.archetype_id
                for one in session.state.combatants
                if one.enemy is not None and not one.alive
            )
            deed = digest_claim.hunt_deed(deeds, slot=session.slot, archetype_ids=fallen)
    if deed is None:
        return

    claimed = await digest_claim.claim(
        state_cache,
        content,
        hero,
        deed,
        now=now,
        rotation_seconds=settings.shop_rotation_seconds,
    )
    if claimed is None:
        return
    updated[session.owner] = claimed.character
    payouts.setdefault(session.owner, Payout()).extra.append(claimed.line)


async def _settle_roamer(
    session: BattleSession,
    next_flow: PlayState,
    owner: Combatant | None,
    payouts: dict[int, Payout],
    locations: LocationStateCache,
) -> None:
    """Что стало с блуждающим подземельем после боя в нём (ADR 0037).

    Логово пройдено (заход кончился победой) - подземелье осыпается и исчезает.
    Победа в обычной комнате - замок продлевается, владелец всё ещё внутри.
    Поражение - замок снят, но само подземелье остаётся: в него зайдёт следующий.
    """
    won = owner is not None and session.state.verdict_for(owner.id) is Verdict.VICTORY
    completed = won and session.in_descent and not next_flow.descent.active
    if completed:
        await locations.clear_roamer(session.city_id, session.slot, encounter=session.encounter)
        payouts.setdefault(session.owner, Payout()).extra.append(
            "Ход за спиной осыпался: блуждающего подземелья больше нет."
        )
    elif won:
        await locations.hold_roamer(
            session.city_id,
            session.slot,
            session.owner,
            ttl=roamer_rules.ROAMER_HOLD_TTL,
            encounter=session.encounter,
            stamp=session.roamer_stamp,
        )
    else:
        await locations.release_roamer(session.city_id, session.slot, encounter=session.encounter)


async def _after_dungeon_room(
    content: GameContent,
    settings: Settings,
    session: BattleSession,
    flow: PlayState,
    payout: Payout,
    updated: dict[int, Character],
    inventory: InventoryRepository,
    *,
    character: Character,
    payouts: dict[int, Payout] | None = None,
    state_cache: StateCache | None = None,
) -> PlayState:
    """Что даёт выигранная комната и куда развилка ведёт дальше (ADR 0036)."""
    descent = flow.descent
    difficulty = dungeon_rules.difficulty_of(descent.difficulty)
    room = dungeon_rules.room_of(descent.room)
    final = dungeon_rules.final_layer(dungeon_rules.DESCENT_DEPTH, difficulty)
    run_seed = dungeon_run_seed(settings.world_seed, descent)
    conditions = dungeon_rules.conditions_for(run_seed, difficulty)
    encounter = (
        None
        if descent.roamer
        else expedition.meeting(content, descent.city_id, descent.dungeon_id, descent.layer)
    )
    winners = tuple(
        one
        for one in session.participants()
        if one.character_id not in session.departed
        and session.state.verdict_for(one.id) is Verdict.VICTORY
    )
    credited = participation.room_credit(descent.credits, winners, descent.layer)
    continued = replace(
        descent,
        credits=credited,
        excluded=tuple(
            sorted(
                set(descent.excluded)
                | set(session.departed)
                | {one.character_id for one in session.participants() if one.left}
            )
        ),
    )

    _heal_room_winners(
        content,
        session,
        updated,
        encounter.heal_percent if encounter else dungeon_rules.ROOM_HEAL_PERCENT[room],
    )

    if room is dungeon_rules.RoomKind.LAIR or (encounter and encounter.rank == "boss"):
        entitled = tuple(key for key, count in credited if count == session.depth)
        if not session.participation_rule:
            entitled = (session.owner,)
        for index, key in enumerate(entitled):
            target_payout = (payouts or {}).get(key, payout)
            entitlement = f"descent-paid:{descent.encounter(session.owner)}:{key}"
            if state_cache is not None:
                if await state_cache.get(entitlement) is not None:
                    target_payout.extra.append("Дно этого захода уже оплачено.")
                    continue
                await state_cache.set(entitlement, "1", BATTLE_TTL)
            updated[key] = await _pay_the_bottom(
                content,
                updated.get(key, character),
                session,
                target_payout,
                inventory,
                level=max(1, descent.level),
                bounty=dungeon_rules.spec_of(difficulty).stakes
                * dungeon_rules.bounty_of(conditions),
                members=len(entitled),
                share_index=index,
            )
            log, steps = quests.record_descent(
                content,
                updated[key],
                city_id=session.city_id,
                dungeon_id="" if session.roamer else descent.dungeon_id,
            )
            updated[key] = replace(updated[key], quests=log)
            target_payout.extra.extend(
                f"Задание «{step.quest.name}»: {step.progress} из {step.quest.target_count}."
                for step in steps
            )
        if payouts:
            for one in winners:
                if one.character_id not in entitled:
                    payouts[one.character_id].extra.append(
                        "Дно и зачёт полного спуска не начислены: "
                        "пропущено участие в одной из встреч."
                    )
        payout.extra.append("Логово пройдено. Заход окончен — наверх, к свету.")
        return replace(flow, descent=Descent())

    next_layer = descent.layer + 1
    upcoming = (
        None
        if descent.roamer
        else expedition.meeting(content, descent.city_id, descent.dungeon_id, next_layer)
    )
    options: tuple[dungeon_rules.RoomKind, ...]
    if upcoming:
        next_kind = (
            dungeon_rules.RoomKind.LAIR
            if upcoming.rank == "boss"
            else dungeon_rules.RoomKind.SKIRMISH
        )
        options = (next_kind, dungeon_rules.RoomKind.STAIRS)
    else:
        options = dungeon_rules.room_options(run_seed, next_layer, final)
    payout.extra.append(f"Пройдено комнат: {descent.layer + 1}. Впереди развилка.")
    if descent.layer == 0:
        # На входе называем, что несёт этот заход: дальше о том же напомнит
        # список состояний в панели боя.
        payout.extra.extend(dungeon_screens.condition_lines(conditions))
    payout.extra.extend(dungeon_screens.fork_lines(options))
    payout.rows.extend(dungeon_screens.fork_rows(options))
    if upcoming:
        payout.extra.append(f"Следующая встреча: {upcoming.name}. {upcoming.briefing}")
    return replace(flow, descent=continued)


async def _pay_the_bottom(
    content: GameContent,
    character: Character,
    session: BattleSession,
    payout: Payout,
    inventory: InventoryRepository,
    *,
    level: int,
    bounty: float = 1.0,
    members: int = 1,
    share_index: int = 0,
) -> Character:
    """Выдать то, ради чего заход и затевался.

    Платит дно по уровню спуска, а не по уровню вошедшего (ADR 0019, ADR 0028);
    ``bounty`` - множитель сложности: гиблый спуск и дно платит вдвое (ADR 0036).
    """
    prize = adventure.descent_prize(
        content,
        character,
        level=level,
        seed=derive("descent-prize", session.id, session.depth),
        bounty=bounty,
        members=members,
        share_index=share_index,
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
    current = await locations.state(session.city_id, session.slot, now=now)
    if node_rules.location_epoch(current) != session.epoch:
        return "Округа уже изменилась. Победа сохранена; новый узел не затронут."
    node_state = await take_from_node(
        content,
        visit,
        session.node,
        locations,
        now,
        settings,
        wave=session.wave,
        place=session.place,
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
            # Заход принадлежит тому, кто его начал: спутник идёт с ним, но
            # своего захода не ведёт. Оставленный спутнику ``descent`` собрал бы
            # его следующий бой как комнату чужого данжа (``_spawn``).
            landing = (
                _back_to_city(flow, character)
                if lost
                else replace(flow, fight="", descent=Descent())
            )
        if message.from_user is not None and one.user_id == message.from_user.id:
            await state.update_data(
                {PLAY_KEY: landing.serialise(), "battle_version": session.version}
            )
            continue
        if bot is None:  # pragma: no cover
            continue
        remote = await _remote_state(bot, storage, one.user_id)
        await remote.set_state(Play.combat)
        # Номер боя остаётся: экран итога читается по нему, а занятость с
        # персонажа уже снята (``BattleStore.release``).
        await remote.update_data(
            {
                PLAY_KEY: landing.serialise(),
                STATE_KEY: session.id,
                "battle_version": session.version,
            }
        )


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
    locations: LocationStateCache,
) -> None:
    """Экран итога - настоящий экран: он отвечает на каждую кнопку."""
    text = message.text or ""
    if labels.MAIN_MENU.matches(text) or text.strip().casefold() in {"/меню", "/menu"}:
        if flow.descent.roamer:
            await locations.release_roamer(
                flow.descent.city_id,
                flow.descent.slot,
                encounter=flow.descent.encounter(character.id),
            )
        home = replace(
            flow,
            screen=ScreenId.MAIN_MENU,
            stack=NavigationStack((ScreenId.MAIN_MENU,)),
            descent=Descent(),
            fight="",
            notice="",
        )
        await state.update_data({STATE_KEY: "", PLAY_KEY: home.serialise()})
        await state.set_state(Play.main_menu)
        from mmorpg.presentation.telegram.handlers.play import render_play

        await render_play(message, content, settings, home, character)
        return

    if flow.descent.active:
        picked = _dungeon_fork(content, settings, character, flow, text)
        if picked is not None:
            if picked.descent.room == dungeon_rules.RoomKind.STAIRS.value:
                # Ход наверх - это не бой: заход кончается с тем, что уже взято.
                # Замок подземелья снимает ``_leave_to_play`` (ADR 0037).
                await state.update_data({STATE_KEY: ""})
                await _leave_to_play(message, state, content, settings, flow, character, locations)
                return
            await state.update_data({STATE_KEY: ""})
            await open_fight(
                message,
                state,
                content=content,
                settings=settings,
                character=character,
                flow=replace(picked, fight="dungeon"),
                characters=characters,
                state_cache=state_cache,
                parties=parties,
                storage=_storage_of(state),
                locations=locations,
            )
            return
    await _leave_to_play(message, state, content, settings, flow, character, locations)


def _dungeon_fork(
    content: GameContent,
    settings: Settings,
    character: Character,
    flow: PlayState,
    text: str,
) -> PlayState | None:
    """Разобрать нажатую дверь развилки. ``None`` - нажали не её.

    Возможные двери этого слоя считаются заново из сида захода, поэтому чужую
    кнопку (со старой клавиатуры, с другого слоя) не примут: слой можно пройти
    только вперёд и только в одну из тех комнат, что игра предложила.
    """
    descent = flow.descent
    difficulty = dungeon_rules.difficulty_of(descent.difficulty)
    final = dungeon_rules.final_layer(dungeon_rules.DESCENT_DEPTH, difficulty)
    next_layer = descent.layer + 1
    options = dungeon_rules.room_options(
        dungeon_run_seed(settings.world_seed, descent), next_layer, final
    )
    upcoming = (
        None
        if descent.roamer
        else expedition.meeting(content, descent.city_id, descent.dungeon_id, next_layer)
    )
    if upcoming:
        options = (
            dungeon_rules.RoomKind.LAIR
            if upcoming.rank == "boss"
            else dungeon_rules.RoomKind.SKIRMISH,
            dungeon_rules.RoomKind.STAIRS,
        )
    for kind in options:
        if dungeon_screens.room_label(kind).matches(text):
            return replace(flow, descent=replace(descent, layer=next_layer, room=kind.value))
    return None


async def _leave_to_play(
    message: Message,
    state: FSMContext,
    content: GameContent,
    settings: Settings,
    flow: PlayState,
    character: Character,
    locations: LocationStateCache | None = None,
) -> None:
    """Бросить бой и вернуть игрока на тот экран, с которого он пришёл."""
    from mmorpg.presentation.telegram.handlers.play import render_play

    # Брошенный заход отпускает замок: само блуждающее подземелье остаётся для
    # других (ADR 0037).
    if flow.descent.roamer and locations is not None:
        await locations.release_roamer(
            flow.descent.city_id, flow.descent.slot, encounter=flow.descent.encounter(character.id)
        )
    # Уйти из боя - это и кончить заход: продолжают его только дверью развилки на
    # экране итога, и другого пути внутрь нет. Незакрытый заход, оставшийся в
    # состоянии, следующий бой - хоть в узле, хоть на арене - собирал бы как
    # комнату данжа (``_spawn`` смотрит на ``descent.active``).
    if flow.descent.active:
        flow = replace(flow, descent=Descent())

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
    session: BattleSession | None = None,
) -> None:
    entries = await _consumables(content, character, inventory)
    await state.set_state(Play.combat_bag)
    screen = combat_screens.bag_screen(content, entries)
    await send_screen(message, _versioned(screen, session) if session else screen)


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
    guilds: GuildStore,
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

    resolved = act(
        content,
        roster,
        session.state,
        BattleAction(kind=ActionKind.ITEM, item_id=chosen),
        session.seed,
    )
    if any(event.kind is EventKind.ITEM_REFUSED for event in resolved.events):
        await send_screen(
            message, combat_screens.bag_screen(content, entries, resolved.events[-1].effect_name)
        )
        return
    if not await inventory.remove(character.id, chosen, 1):
        await send_screen(message, combat_screens.bag_screen(content, entries, "Этого уже нет."))
        return
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
        guilds,
    )


async def _work_contracts(
    content: GameContent,
    settings: Settings,
    guilds: GuildStore,
    places: Mapping[int, GuildPlace],
    session: BattleSession,
    next_flow: PlayState,
    winners: Sequence[Combatant],
    payouts: dict[int, Payout],
    *,
    original_flow: PlayState | None = None,
    state_cache: StateCache | None = None,
) -> None:
    """Записать выигранный бой и пройденный спуск в подряд гильдии (ADR 0077).

    Подряд закрывается сам, и говорит о нём тот, чьё движение его закрыло:
    кнопки «сдать подряд» нет нарочно (``Claude.md``, правило 9).
    """
    now = int(time.time())
    delved = session.in_descent and not next_flow.descent.active
    counted: set[tuple[int, contract_rules.ContractKind]] = set()
    full: dict[int, int] = {}
    if original_flow is not None:
        full = dict(
            participation.room_credit(
                original_flow.descent.credits, winners, original_flow.descent.layer
            )
        )
    for one in winners:
        place = places.get(one.character_id)
        if place is None:
            continue
        kinds = [contract_rules.ContractKind.CULL]
        if delved and (
            not session.participation_rule or full.get(one.character_id) == session.depth
        ):
            kinds.append(contract_rules.ContractKind.DELVE)
        for kind in kinds:
            identity = (place.guild_id, kind)
            if identity in counted:
                continue
            counted.add(identity)
            key = f"battle-credit:{session.id}:{place.guild_id}:{kind.value}"
            if state_cache is not None:
                if await state_cache.get(key) is not None:
                    continue
                await state_cache.set(key, "1", BATTLE_TTL)
            closed = await guilds.work_on_contract(
                content,
                guild_id=place.guild_id,
                place=place.standing,
                kind=kind,
                amount=1,
                world_seed=settings.world_seed,
                now=now,
                rotation_seconds=settings.guild_contract_seconds,
                contributors=tuple(
                    member.character_id
                    for member in winners
                    if places.get(member.character_id) == place
                    and (
                        kind is contract_rules.ContractKind.CULL
                        or not session.participation_rule
                        or full.get(member.character_id) == session.depth
                    )
                ),
                receipt=session.id,
            )
            if closed is None:
                continue
            economy_log.record(
                economy_log.GUILD_CONTRACT,
                closed.reward_gold,
                character_id=one.character_id,
                detail=kind.value,
            )
            payouts[one.character_id].extra.append(
                f"Подряд гильдии «{place.name}» закрыт: {closed.line} Гильдии - {closed.pay}."
            )


async def _score_war(
    guilds: GuildStore,
    settings: Settings,
    winners: Sequence[Combatant],
    losers: Sequence[Combatant],
    payouts: dict[int, Payout],
    characters: CharacterRepository,
    battle_id: str,
    account_seconds: int,
) -> None:
    """Один общий поединок даёт максимум одно очко каждой действующей войне."""
    now = int(time.time())
    scored: set[int] = set()
    for one in winners:
        mine = await guilds.of(one.character_id)
        if mine is None:
            continue
        war = await guilds.timed_war(
            mine.id,
            now=now,
            legacy_seconds=settings.shop_rotation_seconds,
            duration=settings.guild_war_seconds,
        )
        if war is None or war.id in scored:
            continue
        side = tuple(member.character_id for member in winners if member.actions > 0)
        foes = tuple(member.character_id for member in losers if member.actions > 0)
        note = await WarScoring(guilds, characters).score(
            war, mine.id, side, foes, battle_id, now=now, account_seconds=account_seconds
        )
        scored.add(war.id)
        note = note.replace("вашей гильдии", f"гильдии «{mine.name}»")
        for member in (*winners, *losers):
            if member.character_id in payouts:
                payouts[member.character_id].extra.append("Война гильдий: " + note)
