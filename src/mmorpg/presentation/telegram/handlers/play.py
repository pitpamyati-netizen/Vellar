"""Handlers for menu, world, city, services and location navigation.

Thin by design: load state, call the pure flow, store what the flow decided,
render, send one message. The clock lives here - the flow receives the cycle
index as a value, which is what keeps location generation reproducible
(``docs/procgen.md``).

The flow never writes. Everything it decided to change arrives in
``PlayState.pending`` and is applied here, in one place, so there is exactly one
answer to "where does the game store things".
"""

from __future__ import annotations

import time
from dataclasses import replace

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from mmorpg.config import Settings
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.ports.repositories import (
    CharacterRepository,
    InventoryRepository,
    LocationDeltaCache,
    UserRepository,
)
from mmorpg.domain.procgen.location import cleared_mask
from mmorpg.domain.procgen.seeds import cycle_index, seconds_left_in_cycle
from mmorpg.domain.rules.economy import buy_price, roll_assortment
from mmorpg.domain.rules.stats import primary_stats
from mmorpg.presentation.telegram.flows.play import (
    Goods,
    PendingWrite,
    PlayState,
    advance,
    begin,
    render,
)
from mmorpg.presentation.telegram.handlers.combat import open_fight
from mmorpg.presentation.telegram.handlers.creation import welcome_screen
from mmorpg.presentation.telegram.messaging import send_screen
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.screens.shop import OwnedItem
from mmorpg.presentation.telegram.states.screens import STATE_FOR_SCREEN, Play

STATE_KEY = "play"


def build_router() -> Router:
    """A fresh router per application - see handlers.creation.build_router."""
    router = Router(name="play")
    # The screens are a private conversation: one player, one keyboard, one
    # message at a time. The group has its own router and its own rules.
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.message.register(play, StateFilter(Play))
    return router


async def play(
    message: Message,
    state: FSMContext,
    content: GameContent,
    settings: Settings,
    characters: CharacterRepository,
    inventory: InventoryRepository,
    users: UserRepository,
    location_deltas: LocationDeltaCache,
) -> None:
    if message.from_user is None or message.text is None:
        return

    character = await characters.get_active(message.from_user.id)
    if character is None:
        await state.clear()
        await send_screen(message, welcome_screen())
        return

    user = await users.get(message.from_user.id)
    emoji = user.settings.emoji if user is not None else False
    accessibility = user.settings if user is not None else None

    data = await state.get_data()
    flow = PlayState.deserialise(data[STATE_KEY]) if data.get(STATE_KEY) else begin(character)

    now = int(time.time())
    cycle = cycle_index(now, settings.cycle_seconds)
    goods = await _goods(content, character, flow, inventory, settings, cycle)

    updated = advance(
        content,
        character,
        flow,
        message.text,
        cycle=cycle,
        world_seed=settings.world_seed,
        goods=goods,
        settings=accessibility,
    )

    character = await _apply(
        updated.pending, character, message.from_user.id, characters, inventory, users
    )
    updated = await _sync_cleared(updated, flow, character, location_deltas, now, settings)

    if updated.fight:
        await open_fight(
            message,
            state,
            content=content,
            settings=settings,
            character=character,
            flow=updated,
            emoji=emoji,
        )
        return

    # The screen the step landed on is not the screen it started from, and the
    # bag may have changed on the way, so what it shows is read again.
    shelf = await _goods(content, character, updated, inventory, settings, cycle)
    await state.set_state(STATE_FOR_SCREEN[updated.screen])
    await state.update_data({STATE_KEY: updated.serialise()})
    await render_play(message, content, settings, updated, character, emoji=emoji, goods=shelf)


async def render_play(
    message: Message,
    content: GameContent,
    settings: Settings,
    flow: PlayState,
    character: Character,
    *,
    emoji: bool = False,
    goods: Goods | None = None,
) -> None:
    """Draw one play screen. Used by this handler and by the fight handler."""
    shelf = goods if goods is not None else Goods(gold=character.gold)
    await send_screen(
        message,
        render(content, character, flow, world_seed=settings.world_seed, goods=shelf),
        emoji=emoji,
    )


async def _goods(
    content: GameContent,
    character: Character,
    flow: PlayState,
    inventory: InventoryRepository,
    settings: Settings,
    cycle: int,
) -> Goods:
    """What the bag holds and what the shelf offers, for the screens that need it.

    The assortment is rolled, never stored: same city, same watch, same shelf
    (``docs/procgen.md``).
    """
    held = await inventory.list_items(character.id)
    owned = tuple(
        OwnedItem(item_id=entry.item_id, quantity=entry.quantity)
        for entry in held
        if content.has_item(entry.item_id)
    )
    if flow.screen is not ScreenId.SHOP:
        return Goods(gold=character.gold, owned=owned)

    stock = roll_assortment(
        content,
        world_seed=settings.world_seed,
        city_id=flow.city_id or character.city_id,
        cycle=cycle,
        character_level=character.level,
    )
    charisma = primary_stats(content, character)[StatCode.CHA]
    prices = {item.id: buy_price(content, item, charisma=charisma) for item in stock}
    return Goods(gold=character.gold, owned=owned, stock=stock, prices=prices)


async def _apply(
    write: PendingWrite,
    character: Character,
    telegram_id: int,
    characters: CharacterRepository,
    inventory: InventoryRepository,
    users: UserRepository,
) -> Character:
    """Store everything one step decided to change. The only writer in the flow."""
    if write.empty:
        return character

    for item_id, delta in write.items:
        if delta > 0:
            await inventory.add(character.id, item_id, delta)
        elif delta < 0:
            await inventory.remove(character.id, item_id, -delta)

    if write.settings is not None:
        await users.save_settings(telegram_id, write.settings)

    if write.character is not None:
        await characters.save(write.character)
        return write.character
    return character


async def _sync_cleared(
    updated: PlayState,
    before: PlayState,
    character: Character,
    location_deltas: LocationDeltaCache,
    now: int,
    settings: Settings,
) -> PlayState:
    """Keep the cleared-node marks for the whole watch, not just for one visit.

    The mask lives in the cache with the cycle's own time to live, so a player
    who walks out and back in finds the nodes they already worked through
    (``docs/procgen.md``); nothing about it ever reaches PostgreSQL.
    """
    session = updated.session
    if not session.active:
        return updated

    if not before.session.active or before.session.slot != session.slot:
        stored = await location_deltas.get_mask(
            character.id, session.city_id, session.slot, session.cycle
        )
        return replace(updated, session=replace(session, cleared=session.cleared | stored))

    fresh = session.cleared & ~before.session.cleared
    if not fresh:
        return updated
    ttl = max(60, seconds_left_in_cycle(now, settings.cycle_seconds))
    for index in range(64):
        if fresh & cleared_mask([index]):
            await location_deltas.mark_cleared(
                character.id, session.city_id, session.slot, session.cycle, index, ttl
            )
    return updated
