"""The fight handler: one press, one turn, one message.

The engine is in ``domain.rules.combat`` and the button mapping is in
``flows.combat``; this module owns the three things neither of them may touch -
where the fight is kept between messages, who generates the opponents, and what
a won or lost fight does to the stored character.

A fight lives in FSM data next to the play state, so it survives a restart with
Redis storage and disappears with the state when it ends.
"""

from __future__ import annotations

from dataclasses import replace

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from mmorpg.config import Settings
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import ActionKind, CombatAction, CombatOutcome
from mmorpg.domain.entities.content import GameContent, ItemKind
from mmorpg.domain.entities.location import EnemyRank
from mmorpg.domain.ports.repositories import CharacterRepository, InventoryRepository
from mmorpg.domain.procgen.location import cleared_mask
from mmorpg.domain.rules import adventure
from mmorpg.domain.rules.combat import resolve_turn
from mmorpg.presentation.telegram.flows import combat as fight_flow
from mmorpg.presentation.telegram.flows.play import (
    DUNGEON_DEPTH,
    Descent,
    LocationSession,
    PlayState,
    build_location,
    descent_fight_seed,
    level_up_line,
    node_fight_seed,
)
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.messaging import send_screen
from mmorpg.presentation.telegram.screens import combat as combat_screens
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.states.screens import STATE_FOR_SCREEN, NavigationStack, Play

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
) -> None:
    """Build the fight the play flow asked for and show its first screen."""
    session = _spawn(content, character, flow, world_seed=settings.world_seed)
    landing = replace(flow, screen=ScreenId.COMBAT, fight="")
    await state.set_state(Play.combat)
    await state.update_data(
        {PLAY_KEY: landing.serialise(), STATE_KEY: fight_flow.serialise(session)}
    )
    await send_screen(message, fight_flow.render(content, character, session), emoji=emoji)


def _spawn(
    content: GameContent, character: Character, flow: PlayState, *, world_seed: str
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
            rank=EnemyRank.ELITE if descent.depth >= DUNGEON_DEPTH else EnemyRank.NORMAL,
        )
        return fight_flow.begin(content, character, enemies, seed=seed, node=0, depth=descent.depth)

    location = build_location(content, world_seed, flow.session)
    node = location.node(flow.session.node)
    seed = node_fight_seed(world_seed, flow.session)
    enemies = fight_flow.spawn_for_node(
        content,
        seed=seed,
        biome=location.biome,
        level=max(1, node.level),
        rank=node.kind.rank,
    )
    return fight_flow.begin(content, character, enemies, seed=seed, node=node.index)


# --- one turn ---------------------------------------------------------


async def fight(
    message: Message,
    state: FSMContext,
    content: GameContent,
    settings: Settings,
    characters: CharacterRepository,
    inventory: InventoryRepository,
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
            message, state, content, character, session, flow, inventory, characters
        )
        return

    if labels.BAG.matches(message.text) or message.text.strip().casefold() in {"/сумка", "/bag"}:
        await _open_bag(message, state, content, character, inventory)
        return

    updated, notice = fight_flow.advance(content, character, session, message.text)
    await _store_and_show(
        message, state, content, character, updated, flow, notice, characters, inventory
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
            for item_id in session.state.loot:
                if content.has_item(item_id):
                    await inventory.add(character.id, item_id, 1)
            if won.level_up is not None and won.level_up.levels_gained:
                extra.append(level_up_line(won.level_up))
            extra.extend(
                f"Подряд «{step.quest.name}»: {step.progress} из {step.quest.target_count}."
                for step in won.quest_steps
            )
            updated_flow, rows = _after_victory(session, flow, extra)
        case CombatOutcome.DEFEAT:
            lost = adventure.resolve_defeat(content, character)
            character = lost.character
            gold_lost = lost.gold_lost
            updated_flow = _back_to_city(flow, character)
        case _:
            character = adventure.carry_wounds(content, character, session.state)

    await characters.save(character)
    await state.set_state(Play.combat)
    await state.update_data(
        {STATE_KEY: fight_flow.serialise(session), PLAY_KEY: updated_flow.serialise()}
    )
    await send_screen(
        message,
        fight_flow.render(content, character, session, extra=extra, rows=rows, gold_lost=gold_lost),
    )


def _after_victory(
    session: fight_flow.CombatSession, flow: PlayState, extra: list[str]
) -> tuple[PlayState, list[tuple[Label, ...]]]:
    """Mark the node as done, or offer the next step of a descent."""
    if not session.in_descent:
        cleared = flow.session.cleared | cleared_mask([session.node])
        return replace(flow, session=replace(flow.session, cleared=cleared)), []

    if session.depth >= DUNGEON_DEPTH:
        extra.append("Спуск пройден до дна. Дальше только камень.")
        return replace(flow, descent=Descent()), []

    extra.append(f"Пройдено схваток: {session.depth} из {DUNGEON_DEPTH}.")
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
    )
