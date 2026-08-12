"""Handlers for menu, world, city and location navigation.

Thin by design: load state, call the pure flow, render, send one message. The
clock lives here - the flow receives the cycle index as a value, which is what
keeps location generation reproducible (``docs/procgen.md``).
"""

from __future__ import annotations

import time

from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from mmorpg.config import Settings
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.ports.repositories import CharacterRepository, UserRepository
from mmorpg.domain.procgen.seeds import cycle_index
from mmorpg.presentation.telegram.flows.play import PlayState, advance, begin, render
from mmorpg.presentation.telegram.handlers.creation import welcome_screen
from mmorpg.presentation.telegram.messaging import send_screen
from mmorpg.presentation.telegram.states.screens import STATE_FOR_SCREEN, Play

router = Router(name="play")

STATE_KEY = "play"


@router.message(StateFilter(Play))
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

    updated = advance(
        content,
        character,
        flow,
        message.text,
        cycle=cycle_index(int(time.time()), settings.cycle_seconds),
        world_seed=settings.world_seed,
    )

    await state.set_state(STATE_FOR_SCREEN[updated.screen])
    await state.update_data({STATE_KEY: updated.serialise()})
    await send_screen(
        message,
        render(content, character, updated, world_seed=settings.world_seed),
        emoji=emoji,
    )
