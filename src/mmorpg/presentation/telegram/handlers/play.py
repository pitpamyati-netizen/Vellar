"""Handlers for menu, world, city and location navigation.

Thin by design: load state, call the pure flow, render, send one message. The
clock lives here - the flow receives the cycle index as a value, which is what
keeps location generation reproducible (``docs/procgen.md``).
"""

from __future__ import annotations

import time

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from mmorpg.config import Settings
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.ports.repositories import CharacterRepository, UserRepository
from mmorpg.domain.procgen.seeds import cycle_index
from mmorpg.domain.rules.bank import apply_transfer
from mmorpg.domain.rules.training import train_stat
from mmorpg.presentation.telegram.flows.play import PlayState, advance, begin, render
from mmorpg.presentation.telegram.handlers.creation import welcome_screen
from mmorpg.presentation.telegram.messaging import send_screen
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
    users: UserRepository,
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

    data = await state.get_data()
    flow = PlayState.deserialise(data[STATE_KEY]) if data.get(STATE_KEY) else begin(character)

    cycle = cycle_index(int(time.time()), settings.cycle_seconds)
    updated = advance(
        content,
        character,
        flow,
        message.text,
        cycle=cycle,
        world_seed=settings.world_seed,
    )
    character = await _settle(characters, character, updated)

    await state.set_state(STATE_FOR_SCREEN[updated.screen])
    await state.update_data({STATE_KEY: updated.serialise()})
    await send_screen(
        message,
        render(content, character, updated, world_seed=settings.world_seed, cycle=cycle),
        emoji=emoji,
    )


async def _settle(
    characters: CharacterRepository, character: Character, state: PlayState
) -> Character:
    """Perform what the flow decided.

    The flow records an intent and stays pure; the write happens here, through the
    same domain function that produced the sentence the player was told. One save
    per update at most, and none at all when nothing changed.
    """
    changed = character
    if state.pending_stat:
        trained = train_stat(changed, StatCode(state.pending_stat))
        if trained is not None:
            changed = trained
    if state.pending_transfer is not None:
        changed = apply_transfer(changed, state.pending_transfer)

    if changed is not character:
        await characters.save(changed)
    return changed
