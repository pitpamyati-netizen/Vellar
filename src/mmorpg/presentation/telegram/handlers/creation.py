"""Character creation handlers.

The handler is deliberately thin: load state, call the pure flow, render, send.
All the rules live in ``flows.creation``; none of them live here
(``docs/architecture.md``, "No business logic in handlers").
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.ports.repositories import CharacterRepository, User, UserRepository
from mmorpg.presentation.telegram.flows.creation import (
    CreationState,
    advance,
    begin,
    render,
)
from mmorpg.presentation.telegram.messaging import send_screen
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.states.screens import STATE_FOR_SCREEN, Creation, Play

STATE_KEY = "creation"


def build_router() -> Router:
    """A fresh router per application.

    Routers are single-use in aiogram: one can only ever be attached to one
    dispatcher, so handing out a module-level singleton would make building the
    application twice in one process - as the tests do - fail.
    """
    router = Router(name="creation")
    router.message.register(start, CommandStart())
    router.message.register(step, StateFilter(Creation))
    return router


def welcome_screen() -> Screen:
    return Screen(
        id=ScreenId.START,
        lines=(
            "Веллар. Текстовая игра, рассчитанная на прослушивание.",
            "У вас пока нет персонажа. Сейчас создадим.",
            "Каждый шаг можно пройти кнопками или командами: /назад, /осмотреться, /меню.",
        ),
    )


def created_screen(name: str, city_name: str) -> Screen:
    return Screen(
        id=ScreenId.START,
        lines=(
            f"Персонаж {name} создан.",
            f"Вы находитесь в городе {city_name}.",
            "Нажмите «Главное меню», чтобы начать игру.",
        ),
    )


async def start(
    message: Message,
    state: FSMContext,
    content: GameContent,
    users: UserRepository,
    characters: CharacterRepository,
) -> None:
    """Entry point. An existing character skips creation entirely."""
    if message.from_user is None:  # pragma: no cover - Telegram always sets it
        return
    await users.upsert(
        User(telegram_id=message.from_user.id, username=message.from_user.username or "")
    )

    existing = await characters.get_active(message.from_user.id)
    if existing is not None:
        await state.set_state(Play.main_menu)
        city = content.city(existing.city_id)
        await send_screen(message, created_screen(existing.name, city.name))
        return

    flow = begin()
    await state.set_state(Creation.name)
    await state.update_data({STATE_KEY: flow.serialise()})
    await send_screen(message, welcome_screen())
    await send_screen(message, render(content, flow))


async def step(
    message: Message,
    state: FSMContext,
    content: GameContent,
    characters: CharacterRepository,
) -> None:
    """One creation step per incoming message."""
    if message.from_user is None or message.text is None:
        return

    data = await state.get_data()
    flow = CreationState.deserialise(data.get(STATE_KEY, "")) if data.get(STATE_KEY) else begin()

    # The only piece of state the pure flow cannot know by itself.
    name_taken = False
    if flow.screen is ScreenId.CREATE_NAME and not message.text.startswith("/"):
        name_taken = await characters.name_taken(message.text.strip())

    updated = advance(content, flow, message.text, name_taken=name_taken)

    if updated.exited:
        await state.clear()
        await send_screen(message, welcome_screen())
        return

    if updated.finished:
        character = await characters.create(
            updated.draft.to_character(content, message.from_user.id)
        )
        await state.clear()
        await state.set_state(Play.main_menu)
        city = content.city(character.city_id)
        await send_screen(message, created_screen(character.name, city.name))
        return

    await state.set_state(STATE_FOR_SCREEN[updated.screen])
    await state.update_data({STATE_KEY: updated.serialise()})
    await send_screen(message, render(content, updated))
