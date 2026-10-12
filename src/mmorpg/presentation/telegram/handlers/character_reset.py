"""Предпросмотр нового героя и отдельное подтверждение потери прогресса."""

from dataclasses import replace

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from mmorpg.application.dto.creation import CharacterDraft
from mmorpg.application.operations import atomic_action
from mmorpg.application.services.character_reset import CharacterReset
from mmorpg.application.services.guild import GuildStore
from mmorpg.application.services.party import PartyStore
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.ports.repositories import (
    CharacterRepository,
    InventoryRepository,
    StateCache,
    TradeRepository,
    UserRepository,
)
from mmorpg.domain.rules.stats import derived_stats
from mmorpg.presentation.telegram.flows.creation import CreationState, advance, render
from mmorpg.presentation.telegram.flows.play import begin as begin_play
from mmorpg.presentation.telegram.flows.state import PlayState
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.messaging import send_screen
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.play import main_menu_screen
from mmorpg.presentation.telegram.states.screens import NavigationStack, Play

OPEN = "Сбросить персонажа"
CHOOSE = "Выбрать расу и класс заново"
CONFIRM = "Подтвердить полный сброс"
KEY = "character_reset"


class Reset(StatesGroup):
    review = State()
    selection = State()


def build_router() -> Router:
    router = Router(name="character_reset")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.message.register(step, StateFilter(Reset))
    router.message.register(open_reset, Command("сброс"), StateFilter(Play, None))
    router.message.register(open_reset, F.text == OPEN, StateFilter(Play))
    return router


def warning(actor: Character, notice: str = "") -> Screen:
    return Screen(
        ScreenId.CREATE_CONFIRM,
        (
            f"Полный сброс персонажа {actor.name}.",
            *([notice] if notice else []),
            "Уровень станет первым. Исчезнут опыт, золото в кошельке и банке, "
            "все прежние вещи, умения, ранги, грани, таланты, специализации, "
            "черты, характеристики, задания, ремесленный опыт и результаты арены.",
            "Принадлежность к великому дому исчезнет. Личные долгие цели начнутся заново.",
            "Имя сохранится. Вы заново выберете расу, класс, начальные черты и "
            "характеристики; получите только обычные стартовые оружие, нагрудник и умения.",
            "Обучение будет завершено без повторных наград. Разовые награды общих "
            "событий, уже полученные награды долгих целей и ограничения не обновятся.",
            "Сейчас ничего не потеряно. Сброс произойдёт только после отдельного "
            "подтверждения нового выбора. Отменить: /назад или /меню.",
            "Выбрать расу и класс заново: /сброс выбрать.",
        ),
        rows=((labels.label(CHOOSE),),),
    )


def begin_selection(actor: Character) -> CreationState:
    return CreationState(
        screen=ScreenId.CREATE_RACE,
        draft=CharacterDraft(name=actor.name),
        stack=NavigationStack((ScreenId.CREATE_RACE,)),
    )


def selection_screen(content: GameContent, flow: CreationState) -> Screen:
    screen = render(content, flow)
    if flow.screen is ScreenId.CREATE_CONFIRM:
        return replace(
            screen,
            lines=(
                "Подтверждение полного сброса.",
                *screen.lines[1:-1],
                "Прежнее имущество и прогресс будут потеряны без возможности "
                "отменить сброс. Уровень: 1. Обучение повторно не включается.",
                "Подтвердить: /сброс подтвердить. Отменить: /меню.",
            ),
            rows=((labels.label(CONFIRM),),),
        )
    choices = tuple(button for row in screen.rows for button in row)
    return replace(
        screen,
        lines=(
            "Новый выбор перед сбросом. Пока прежний герой сохранён.",
            *screen.lines,
            "Кнопки также доступны командами:",
            *(f"{button.text}: /сброс выбор {number}." for number, button in enumerate(choices, 1)),
        ),
    )


async def menu(
    message: Message,
    state: FSMContext,
    content: GameContent,
    actor: Character,
    users: UserRepository,
    notice: str,
) -> None:
    account = await users.get(actor.user_id)
    await state.clear()
    await state.set_state(Play.main_menu)
    await state.update_data(play=begin_play(actor).serialise())
    await send_screen(
        message,
        main_menu_screen(content, actor, derived_stats(content, actor), notice),
        emoji=account.settings.emoji if account else False,
    )


@atomic_action
async def open_reset(
    message: Message,
    state: FSMContext,
    content: GameContent,
    characters: CharacterRepository,
    inventory: InventoryRepository,
    state_cache: StateCache,
    trades: TradeRepository,
    parties: PartyStore,
    guilds: GuildStore,
    users: UserRepository,
) -> None:
    if message.from_user is None:
        return
    actor = await characters.get_active(message.from_user.id)
    if actor is None:
        await send_screen(message, Screen(ScreenId.START, ("Сначала создайте персонажа: /start.",)))
        return
    service = CharacterReset(content, characters, inventory, state_cache, trades, parties, guilds)
    data = await state.get_data()
    flow = PlayState.deserialise(data["play"]) if data.get("play") else begin_play(actor)
    refused = await service.refusal(actor.id)
    if not refused and (flow.session.active or flow.descent.active):
        refused = "Сначала покиньте локацию или подземелье и вернитесь в город."
    account = await users.get(actor.user_id)
    if not refused:
        await state.set_state(Reset.review)
        await state.update_data({KEY: {"revision": actor.revision}})
    screen = (
        warning(actor)
        if not refused
        else Screen(
            ScreenId.CREATE_CONFIRM,
            ("Сброс пока недоступен.", refused, "Затем вернитесь к /сброс."),
        )
    )
    await send_screen(message, screen, emoji=account.settings.emoji if account else False)


@atomic_action
async def step(
    message: Message,
    state: FSMContext,
    content: GameContent,
    characters: CharacterRepository,
    inventory: InventoryRepository,
    state_cache: StateCache,
    trades: TradeRepository,
    parties: PartyStore,
    guilds: GuildStore,
    users: UserRepository,
) -> None:
    if message.from_user is None or message.text is None:
        return
    actor = await characters.get_active(message.from_user.id)
    if actor is None:
        await state.clear()
        await send_screen(
            message, Screen(ScreenId.START, ("Персонаж не найден. Начните с /start.",))
        )
        return
    text = message.text.strip()
    if text.casefold() in {"/меню", "/menu", "/start"} or labels.MAIN_MENU.matches(text):
        await menu(message, state, content, actor, users, "Сброс отменён. Персонаж сохранён.")
        return
    data = await state.get_data()
    saved = data.get(KEY, {})
    flow = CreationState.deserialise(saved["flow"]) if saved.get("flow") else None
    if text.casefold() == "/сброс":
        await state.set_state(Reset.review)
        await state.update_data({KEY: {"revision": actor.revision}})
        await send_screen(message, warning(actor))
        return
    if flow is None:
        if text in {CHOOSE, "/сброс выбрать"}:
            flow = begin_selection(actor)
            await state.set_state(Reset.selection)
        elif text.casefold() in {"/назад", "/back"} or labels.BACK.matches(text):
            await menu(message, state, content, actor, users, "Сброс отменён. Персонаж сохранён.")
            return
        else:
            await send_screen(message, warning(actor, "Начните новый выбор или отмените сброс."))
            return
    else:
        if text in {CONFIRM, "/сброс подтвердить"} and flow.screen is ScreenId.CREATE_CONFIRM:
            service = CharacterReset(
                content, characters, inventory, state_cache, trades, parties, guilds
            )
            fresh, notice = await service.confirm(actor.user_id, int(saved["revision"]), flow.draft)
            await menu(message, state, content, fresh or actor, users, notice)
            return
        # Обычное «Подтвердить» со старой клавиатуры не запускает сброс.
        if (
            flow.screen is ScreenId.CREATE_CONFIRM
            and not labels.BACK.matches(text)
            and text
            not in {
                "/назад",
                "/back",
                "Назад",
                "/осмотреться",
                "/look",
            }
        ):
            await send_screen(message, selection_screen(content, flow))
            return
        if text.casefold().startswith("/сброс выбор "):
            choices = tuple(button for row in render(content, flow).rows for button in row)
            value = text.rsplit(" ", 1)[-1]
            if value.isdecimal() and len(value) < 5 and 1 <= int(value) <= len(choices):
                text = choices[int(value) - 1].text
            else:
                flow = flow.with_notice(
                    "Такого номера кнопки нет. Выберите номер из текущего экрана."
                )
                await send_screen(message, selection_screen(content, flow))
                return
        flow = advance(content, flow, text)
        if flow.exited:
            await menu(message, state, content, actor, users, "Сброс отменён. Персонаж сохранён.")
            return
    saved["flow"] = flow.serialise()
    await state.update_data({KEY: saved})
    account = await users.get(actor.user_id)
    await send_screen(
        message, selection_screen(content, flow), emoji=account.settings.emoji if account else False
    )
