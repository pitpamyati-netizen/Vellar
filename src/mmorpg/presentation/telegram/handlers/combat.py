"""The fight handler: one press, one turn, one message.

The engine is in ``domain.rules.combat`` and the button mapping is in
``flows.combat``; this module owns the three things neither of them may touch -
where the fight is kept between messages, who generates the opponents, and what
a won or lost fight does to the stored character.

A fight lives in FSM data next to the play state, so it survives a restart with
Redis storage and disappears with the state when it ends.
"""

from __future__ import annotations

import time
from dataclasses import replace

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from mmorpg import economy_log
from mmorpg.config import Settings
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import ActionKind, CombatAction, CombatOutcome
from mmorpg.domain.entities.content import GameContent, ItemKind
from mmorpg.domain.entities.location import EnemyRank, LocationState
from mmorpg.domain.ports.repositories import (
    CharacterRepository,
    InventoryRepository,
    LocationStateCache,
)
from mmorpg.domain.procgen.seeds import derive
from mmorpg.domain.rules import adventure
from mmorpg.domain.rules import arena as arena_rules
from mmorpg.domain.rules import nodes as node_rules
from mmorpg.domain.rules import pvp as pvp_rules
from mmorpg.domain.rules import turning as turning_rules
from mmorpg.domain.rules import tutorial as tutorial_rules
from mmorpg.domain.rules.combat import resolve_turn
from mmorpg.domain.rules.tutorial import TutorialTask
from mmorpg.logging import get_logger
from mmorpg.presentation.telegram.flows import combat as fight_flow
from mmorpg.presentation.telegram.flows.play import (
    build_location,
    descent_fight_seed,
    level_up_line,
    location_known,
    node_fight_seed,
    visit_seed,
)
from mmorpg.presentation.telegram.flows.state import Descent, LocationSession, PlayState
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.messaging import send_screen
from mmorpg.presentation.telegram.screens import arena as arena_screens
from mmorpg.presentation.telegram.screens import combat as combat_screens
from mmorpg.presentation.telegram.screens import tutorial as tutorial_screens
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.states.screens import STATE_FOR_SCREEN, NavigationStack, Play

logger = get_logger(__name__)

STATE_KEY = "combat"
PLAY_KEY = "play"


def build_router() -> Router:
    """A fresh router per application - see handlers.creation.build_router."""
    router = Router(name="combat")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.message.register(fight, StateFilter(Play.combat, Play.combat_bag))
    return router


# --- starting a fight -------------------------------------------------


async def open_fight(
    message: Message,
    state: FSMContext,
    *,
    content: GameContent,
    settings: Settings,
    character: Character,
    flow: PlayState,
    emoji: bool = False,
    characters: CharacterRepository | None = None,
    location_state: LocationState | None = None,
    now: int = 0,
) -> None:
    """Build the fight the play flow asked for and show its first screen."""
    session = await _spawn_duel(content, character, flow, characters)
    if session is None and flow.fight == "arena":
        character, session = await _spawn_arena(content, character, characters)
        if session is None:
            await send_screen(
                message,
                arena_screens.arena_screen(
                    character, notice="На арене сейчас не с кем драться. Зайдите позже."
                ),
                emoji=emoji,
            )
            return
    if session is None:
        session = _spawn(
            content,
            character,
            flow,
            world_seed=settings.world_seed,
            location_state=location_state or LocationState(),
            now=now,
        )
    landing = replace(flow, screen=ScreenId.COMBAT, fight="")
    await state.set_state(Play.combat)
    await state.update_data(
        {PLAY_KEY: landing.serialise(), STATE_KEY: fight_flow.serialise(session)}
    )
    await send_screen(message, fight_flow.render(content, character, session), emoji=emoji)


async def _spawn_duel(
    content: GameContent,
    character: Character,
    flow: PlayState,
    characters: CharacterRepository | None,
) -> fight_flow.CombatSession | None:
    """A fight against another player, fought against a snapshot of them.

    ``None`` when this step is not a duel at all. A target who is gone since the
    button was drawn is also ``None``: the play flow will simply draw the node
    again, and nobody is charged for a fight that did not happen.
    """
    if not flow.fight.startswith("pvp:") or characters is None:
        return None
    target_id = int(flow.fight.removeprefix("pvp:") or 0)
    target = await characters.get(target_id)
    if target is None:
        return None
    seed = derive("duel", character.id, target.id, flow.session.node)
    return fight_flow.begin(
        content,
        character,
        (pvp_rules.as_enemy(content, target),),
        seed=seed,
        node=flow.session.node,
        opponent=target.id,
    )


async def _spawn_arena(
    content: GameContent, character: Character, characters: CharacterRepository | None
) -> tuple[Character, fight_flow.CombatSession | None]:
    """Find somebody of about this level and take the stake before the bell.

    The opponent is never told and never waits: what the player fights is a
    snapshot of a stored character (``domain/rules/arena.py``). Nobody found means
    nobody is charged - the stake is taken only once a fight exists.
    """
    if characters is None:  # pragma: no cover - the handler always passes one
        return character, None
    other = await characters.arena_opponent(
        level=character.level, window=arena_rules.LEVEL_WINDOW, exclude_id=character.id
    )
    if other is None:
        return character, None

    paid, stake = arena_rules.place_stake(character)
    await characters.save(paid)
    seed = derive("arena", paid.id, other.id, paid.arena_wins + paid.arena_losses)
    session = fight_flow.begin(
        content,
        paid,
        (pvp_rules.as_enemy(content, other),),
        seed=seed,
        node=0,
        arena=True,
    )
    logger.info("arena_round_started", character_id=paid.id, opponent_id=other.id, stake=stake)
    economy_log.record(economy_log.ARENA_STAKE, -stake, character_id=paid.id)
    return paid, session


def _spawn(
    content: GameContent,
    character: Character,
    flow: PlayState,
    *,
    world_seed: str,
    location_state: LocationState,
    now: int,
) -> fight_flow.CombatSession:
    """Who is waiting. Both kinds of fight draw from the same generator."""
    if flow.fight == "dungeon" or flow.descent.active:
        descent = flow.descent
        city = content.city(descent.city_id)
        # The descent borrows the biome of the city's deepest location: it is the
        # same ground, only further down.
        biome = city.locations[-1].biome
        seed = descent_fight_seed(world_seed, descent)
        enemies = fight_flow.spawn_for_node(
            content,
            seed=seed,
            biome=biome,
            level=descent.level,
            # The last floor of a descent is what the whole run is for: an epic
            # opponent, and therefore a fight about twice as long as the two above.
            # Where that floor is depends on the Seals: each one opens another
            # layer below the old bottom (``domain/rules/turning.py``).
            rank=(
                EnemyRank.ELITE
                if descent.depth >= turning_rules.descent_depth(character)
                else EnemyRank.NORMAL
            ),
        )
        return fight_flow.begin(content, character, enemies, seed=seed, node=0, depth=descent.depth)

    location = build_location(content, world_seed, flow.session)
    node = location.node(flow.session.node)
    left = node_rules.standing_at(
        visit_seed(world_seed, flow.session), location, location_state, node.index, now
    )
    # The wave and how much of it is already gone are both in the seed: the second
    # pack in a node is not the first one over again (``domain/rules/nodes.py``).
    seed = derive(node_fight_seed(world_seed, flow.session, left.wave), left.taken)
    enemies = fight_flow.spawn_for_node(
        content,
        seed=seed,
        biome=location.biome,
        level=max(1, node.level),
        rank=node.kind.rank,
    )
    return fight_flow.begin(content, character, enemies, seed=seed, node=node.index, wave=left.wave)


# --- one turn ---------------------------------------------------------


async def fight(
    message: Message,
    state: FSMContext,
    content: GameContent,
    settings: Settings,
    characters: CharacterRepository,
    inventory: InventoryRepository,
    locations: LocationStateCache,
) -> None:
    """One message, one turn. Never silence, never two messages."""
    if message.from_user is None or message.text is None:
        return

    character = await characters.get_active(message.from_user.id)
    data = await state.get_data()
    flow = PlayState.deserialise(data[PLAY_KEY]) if data.get(PLAY_KEY) else PlayState()

    if character is None:  # pragma: no cover - a character is required to be here
        await state.clear()
        return
    if not data.get(STATE_KEY):
        # No fight to continue: the player is somewhere else and the state lied.
        await _leave_to_play(message, state, content, settings, flow, character)
        return

    session = fight_flow.deserialise(data[STATE_KEY])

    if session.state.is_over:
        await _after_the_fight(message, state, content, settings, character, flow)
        return

    if await state.get_state() == Play.combat_bag.state:
        await _use_from_bag(
            message,
            state,
            content,
            character,
            session,
            flow,
            inventory,
            characters,
            locations,
            settings,
        )
        return

    if labels.BAG.matches(message.text) or message.text.strip().casefold() in {"/сумка", "/bag"}:
        await _open_bag(message, state, content, character, inventory)
        return

    updated, notice = fight_flow.advance(content, character, session, message.text)
    await _store_and_show(
        message,
        state,
        content,
        character,
        updated,
        flow,
        notice,
        characters,
        inventory,
        locations,
        settings,
    )


async def _store_and_show(
    message: Message,
    state: FSMContext,
    content: GameContent,
    character: Character,
    session: fight_flow.CombatSession,
    flow: PlayState,
    notice: str,
    characters: CharacterRepository,
    inventory: InventoryRepository,
    locations: LocationStateCache,
    settings: Settings,
) -> None:
    """Persist what the turn changed and answer with exactly one screen."""
    if not session.state.is_over:
        await state.set_state(Play.combat)
        await state.update_data({STATE_KEY: fight_flow.serialise(session)})
        await send_screen(message, fight_flow.render(content, character, session, notice))
        return

    extra: list[str] = []
    rows: list[tuple[Label, ...]] = []
    gold_lost = 0
    updated_flow = flow

    match session.state.outcome:
        case CombatOutcome.VICTORY:
            won = adventure.resolve_victory(content, character, session.state)
            character = won.character
            economy_log.record(economy_log.FIGHT, won.gold, character_id=character.id)
            for item_id in session.state.loot:
                if content.has_item(item_id):
                    await inventory.add(character.id, item_id, 1)
            if won.level_up is not None and won.level_up.levels_gained:
                extra.append(level_up_line(won.level_up))
            extra.extend(
                f"Задание «{step.quest.name}»: {step.progress} из {step.quest.target_count}."
                for step in won.quest_steps
            )
            if session.is_duel:
                character, rows = character, []
                updated_flow = flow
            else:
                updated_flow, rows = _after_victory(session, flow, extra, character)
                if session.in_descent and session.depth >= turning_rules.descent_depth(character):
                    character = await _pay_the_bottom(content, character, flow, extra, inventory)
                elif not session.in_descent and flow.session.active:
                    # The pack that fell is one thing out of the node; what is left
                    # of the wave is what the location screen will say next.
                    extra.append(await _take_node(content, flow, session, locations, settings))
            # Winning a fight is one of the introduction tasks, and it is ticked
            # off by the win itself - wherever it happened.
            marked = tutorial_rules.complete(character, TutorialTask.FIGHT)
            if marked is not None:
                character = marked
                extra.append(tutorial_screens.completion_line(TutorialTask.FIGHT, character))
        case CombatOutcome.DEFEAT:
            lost = adventure.resolve_defeat(content, character)
            character = lost.character
            gold_lost = lost.gold_lost
            economy_log.record(economy_log.DEFEAT, -gold_lost, character_id=character.id)
            updated_flow = _back_to_city(flow, character)
        case _:
            character = adventure.carry_wounds(content, character, session.state)

    if session.arena and session.state.outcome in {CombatOutcome.VICTORY, CombatOutcome.DEFEAT}:
        result = arena_rules.settle(character, won=session.state.outcome is CombatOutcome.VICTORY)
        character = result.character
        economy_log.record(
            economy_log.ARENA_PAYOUT,
            result.payout,
            character_id=character.id,
            detail=f"held {result.held}",
        )
        extra.append(arena_screens.round_line(result))
        updated_flow = _back_to_city(flow, character)
    elif session.is_duel and session.state.outcome in {
        CombatOutcome.VICTORY,
        CombatOutcome.DEFEAT,
    }:
        character = await _settle_duel(
            message, content, character, session, characters, extra, won=session.state.outcome
        )

    await characters.save(character)
    await state.set_state(Play.combat)
    await state.update_data(
        {STATE_KEY: fight_flow.serialise(session), PLAY_KEY: updated_flow.serialise()}
    )
    await send_screen(
        message,
        fight_flow.render(content, character, session, extra=extra, rows=rows, gold_lost=gold_lost),
    )


async def _settle_duel(
    message: Message,
    content: GameContent,
    character: Character,
    session: fight_flow.CombatSession,
    characters: CharacterRepository,
    extra: list[str],
    *,
    won: CombatOutcome,
) -> Character:
    """Move the stake of a duel, and tell the other side what happened to them.

    The defender was not at their phone - that is the whole point of fighting a
    snapshot - so they learn about it from a message, not from a screen they were
    staring at. If they cannot be reached, the gold still moved: the fight
    happened either way.
    """
    other = await characters.get(session.opponent)
    if other is None:  # pragma: no cover - the other character was deleted mid-fight
        return character

    if won is CombatOutcome.VICTORY:
        character, other, spoils = pvp_rules.settle(character, other)
        economy_log.record(economy_log.DUEL, spoils.gold, character_id=character.id)
        economy_log.record(economy_log.DUEL, -spoils.gold, character_id=other.id)
        extra.append(f"Поединок выигран. С {other.name} снято {spoils.gold} золота.")
        told = (
            f"На вас напал {character.name}, уровень {character.level}. "
            f"Вы проиграли поединок и потеряли {spoils.gold} золота. "
            "Золото в банке не трогают."
        )
    else:
        other, character, spoils = pvp_rules.settle(other, character)
        economy_log.record(economy_log.DUEL, -spoils.gold, character_id=character.id)
        economy_log.record(economy_log.DUEL, spoils.gold, character_id=other.id)
        extra.append(f"Поединок проигран. {other.name} забрал {spoils.gold} золота.")
        told = (
            f"На вас напал {character.name}, уровень {character.level}, и проиграл. "
            f"Вам досталось {spoils.gold} золота."
        )

    await characters.save(other)
    await _tell(message, other.user_id, told)
    return character


async def _tell(message: Message, telegram_id: int, text: str) -> None:
    """One line to somebody who is not looking at the game right now."""
    bot = message.bot
    if bot is None:  # pragma: no cover - a message always carries its bot
        return
    try:
        await bot.send_message(chat_id=telegram_id, text=text)
    except TelegramAPIError:
        # Blocked the bot, deleted the chat, never started it: their gold still
        # moved, and a duel is not worth failing a turn over.
        logger.info("duel_notice_undelivered", telegram_id=telegram_id)


async def _pay_the_bottom(
    content: GameContent,
    character: Character,
    flow: PlayState,
    extra: list[str],
    inventory: InventoryRepository,
) -> Character:
    """Hand over what the bottom of a descent is for.

    The descent screen has always promised a reward "внизу и целиком"; until now
    the only thing down there was a longer fight (Roadmap, "Риски"). The seed is
    the run's own, so the prize is decided by the descent and not by the moment
    the last blow landed.
    """
    descent = flow.descent
    prize = adventure.descent_prize(
        content,
        character,
        level=descent.level,
        seed=derive("descent-prize", descent.city_id, descent.started_at, descent.level),
    )
    economy_log.record(economy_log.DESCENT, prize.gold, character_id=prize.character.id)
    if prize.item_id and content.has_item(prize.item_id):
        await inventory.add(prize.character.id, prize.item_id, 1)
        found = f" Со дна поднято: {content.item(prize.item_id).name}."
    else:  # pragma: no cover - content always has something at this level
        found = ""
    extra.append(f"Дно спуска: {prize.gold} золота и {prize.experience} опыта.{found}")
    if prize.level_up is not None and prize.level_up.levels_gained:
        extra.append(level_up_line(prize.level_up))
    return prize.character


def _after_victory(
    session: fight_flow.CombatSession,
    flow: PlayState,
    extra: list[str],
    character: Character,
) -> tuple[PlayState, list[tuple[Label, ...]]]:
    """Offer the next step of a descent. A node keeps its own count elsewhere."""
    if not session.in_descent:
        return flow, []

    depth = turning_rules.descent_depth(character)
    if session.depth >= depth:
        extra.append("Спуск пройден до дна. Дальше только камень.")
        return replace(flow, descent=Descent()), []

    extra.append(f"Пройдено схваток: {session.depth} из {depth}.")
    deeper = replace(flow, descent=replace(flow.descent, depth=session.depth + 1))
    return deeper, [(labels.DUNGEON_DEEPER, labels.DUNGEON_LEAVE)]


def _back_to_city(flow: PlayState, character: Character) -> PlayState:
    """A lost fight ends the trip: no location, no descent, back to the city."""
    return replace(
        flow,
        session=LocationSession(),
        descent=Descent(),
        city_id=character.city_id,
        screen=ScreenId.MAIN_MENU,
        stack=NavigationStack((ScreenId.MAIN_MENU,)),
    )


# --- what happens once it is over -------------------------------------


async def _after_the_fight(
    message: Message,
    state: FSMContext,
    content: GameContent,
    settings: Settings,
    character: Character,
    flow: PlayState,
) -> None:
    """The outcome screen is a real screen: it answers every button."""
    text = message.text or ""
    if labels.MAIN_MENU.matches(text) or text.strip().casefold() in {"/меню", "/menu"}:
        # The service row means what it says, even on the way out of a fight.
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
        await open_fight(
            message,
            state,
            content=content,
            settings=settings,
            character=character,
            flow=replace(flow, fight="dungeon"),
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
    """Drop the fight and hand the player back to the screen they came from."""
    from mmorpg.presentation.telegram.handlers.play import render_play

    stack, previous = flow.stack.pop()
    target = previous or (ScreenId.LOCATION if flow.session.active else ScreenId.CITY)
    if target is ScreenId.LOCATION and not flow.session.active:
        target = ScreenId.CITY
    landing = replace(flow, screen=target, stack=stack, fight="", notice="")
    await state.update_data({STATE_KEY: "", PLAY_KEY: landing.serialise()})
    await state.set_state(STATE_FOR_SCREEN[landing.screen])
    await render_play(message, content, settings, landing, character)


# --- the bag ----------------------------------------------------------


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
    session: fight_flow.CombatSession,
    flow: PlayState,
    inventory: InventoryRepository,
    characters: CharacterRepository,
    locations: LocationStateCache,
    settings: Settings,
) -> None:
    """Using a consumable costs the turn, like every other action."""
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
            fight_flow.render(content, character, session, "Вернулись в бой, ничего не потрачено."),
        )
        return

    if not await inventory.remove(character.id, chosen, 1):  # pragma: no cover - a race only
        await send_screen(message, combat_screens.bag_screen(content, entries, "Этого уже нет."))
        return

    resolved = resolve_turn(
        content,
        character,
        session.state,
        CombatAction(kind=ActionKind.ITEM, item_id=chosen),
        session.turn_seed(),
    )
    await _store_and_show(
        message,
        state,
        content,
        character,
        replace(session, state=resolved),
        flow,
        "",
        characters,
        inventory,
        locations,
        settings,
    )


async def _take_node(
    content: GameContent,
    flow: PlayState,
    session: fight_flow.CombatSession,
    locations: LocationStateCache,
    settings: Settings,
) -> str:
    """Забрать из узла побеждённую стаю и сказать, что там ещё осталось."""
    from mmorpg.presentation.telegram.handlers.play import take_from_node

    if not location_known(content, flow.session):
        return ""
    now = int(time.time())
    state = await take_from_node(
        content, flow.session, session.node, locations, now, settings, wave=session.wave
    )
    location = build_location(content, settings.world_seed, flow.session)
    left = node_rules.standing_at(
        visit_seed(settings.world_seed, flow.session), location, state, session.node, now
    )
    if left.empty:
        return "Узел вычищен: новые противники придут сюда через несколько минут."
    return f"В узле осталось противников: {left.left} из {left.size}."
