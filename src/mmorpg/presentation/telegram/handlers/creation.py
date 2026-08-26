"""Хендлеры создания персонажа.

Хендлер тонкий нарочно: прочитать состояние, позвать чистую ветку, нарисовать,
отправить. Все правила живут в ``flows.creation``, и ни одно из них не живёт
здесь (``docs/architecture.md``, «Логика - не в хендлерах»).
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from mmorpg.application.services.keeper import sync_keeper
from mmorpg.config import Settings
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.ports.repositories import CharacterRepository, User, UserRepository
from mmorpg.domain.rules.stats import derived_stats
from mmorpg.presentation.telegram.flows.creation import (
    CreationState,
    advance,
    begin,
    render,
)
from mmorpg.presentation.telegram.messaging import send_screen
from mmorpg.presentation.telegram.screens import play as play_screens
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.states.screens import STATE_FOR_SCREEN, Creation, Play

STATE_KEY = "creation"


def build_router() -> Router:
    """Свежий роутер на приложение.

    Роутеры в aiogram одноразовые: подключить один можно только к одному
    диспетчеру, поэтому раздача общего образца на уровне модуля ломала бы сборку
    приложения дважды в одном процессе - а именно так делают тесты.
    """
    router = Router(name="creation")
    # Только личные переписки. Тот же ``/start`` в группе иначе втянул бы игрока в
    # создание персонажа у всех на глазах.
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.message.register(start, CommandStart())
    router.message.register(step, StateFilter(Creation))
    # Последний, и он ловит всё оставшееся: личное сообщение, не принадлежащее ни одному
    # экрану. Без него такое сообщение не доходит ни до одного хендлера, и игрок
    # получает тишину — единственный ответ, которого игра давать не вправе
    # (``docs/accessibility.md``, правило 12).
    router.message.register(resume, StateFilter(None))
    return router


def welcome_screen() -> Screen:
    """Первое, что игра говорит, — про мир, а не про себя.

    Здесь стояло «текстовая игра, рассчитанная на прослушивание»: правда про
    устройство игры, сказанная вместо правды про Веллар, и первое, что слышал
    человек, открывший бота. Устройство он и так почувствует с первой кнопки.
    """
    return Screen(
        id=ScreenId.START,
        lines=(
            "Веллар — полоса между хребтом и морем. Через неё идёт одна дорога, "
            "и на ней стоят пятнадцать городов.",
            "Дорогу держат сообща: за проезд берут пошлину, за опасную работу платят.",
            "Вы приезжаете по подорожной, выписанной в долг. Возвращать его вам.",
            "Персонажа у вас пока нет — сейчас заведём.",
            "Всякий шаг проходится кнопкой или командой: /назад, /осмотреться, /меню.",
        ),
    )


def created_screen(name: str, city_name: str) -> Screen:
    return Screen(
        id=ScreenId.START,
        lines=(
            f"Персонаж {name} готов.",
            f"Вы в городе {city_name}, у начала дороги.",
            "Нажмите «Главное меню» — оттуда открыто всё остальное.",
        ),
    )


async def start(
    message: Message,
    state: FSMContext,
    content: GameContent,
    settings: Settings,
    users: UserRepository,
    characters: CharacterRepository,
) -> None:
    """Точка входа. Существующий персонаж пропускает создание целиком."""
    if message.from_user is None:  # pragma: no cover - Telegram ставит это всегда
        return
    account = await users.upsert(
        User(telegram_id=message.from_user.id, username=message.from_user.username or "")
    )

    existing = await characters.get_active(message.from_user.id)
    if existing is not None:
        # ``/start`` - единственная минута, через которую проходит каждый игрок, поэтому
        # именно здесь флаг смотрителя сверяется с настройкой и с тем, что аккаунту
        # выдали изнутри игры.
        existing = await sync_keeper(
            existing, message.from_user.id, settings, characters, granted=account.keeper
        )
        await state.set_state(Play.main_menu)
        city = play_screens.standing_in(content, existing)
        await send_screen(message, created_screen(existing.name, city.name))
        return

    flow = begin()
    await state.set_state(Creation.name)
    await state.update_data({STATE_KEY: flow.serialise()})
    await send_screen(message, welcome_screen())
    await send_screen(message, render(content, flow))


async def resume(
    message: Message,
    state: FSMContext,
    content: GameContent,
    settings: Settings,
    users: UserRepository,
    characters: CharacterRepository,
) -> None:
    """Нажатие, которому не соответствует ни один экран.

    Так бывает чаще, чем кажется. Клавиатура живёт в переписке и переживает всё:
    перезапуск игры, у которого экран лежит в процессе и кончается вместе с ним
    (``docs/adr/0010-a-machine-without-containers.md``), обрыв, обновление. Игрок
    после этого нажимает кнопку, которая была верной минуту назад.

    Ответ тот же, что и на любую устаревшую кнопку: сказать первой строкой, что
    случилось, и вернуть на живой экран (``Claude.md``, правило 8). Молчания
    здесь быть не может: для того, кто слушает экран, оно неотличимо от сломанной
    игры.
    """
    if message.from_user is None:  # pragma: no cover - Telegram ставит это всегда
        return
    character = await characters.get_active(message.from_user.id)
    if character is None:
        # Персонажа нет вовсе - значит, это первый разговор, а не потерянный
        # экран, и вести его надо тем же путём, что и ``/start``.
        await start(message, state, content, settings, users, characters)
        return

    account = await users.get(message.from_user.id)
    character = await sync_keeper(
        character,
        message.from_user.id,
        settings,
        characters,
        granted=account is not None and account.keeper,
    )
    await state.set_state(Play.main_menu)
    await send_screen(
        message,
        play_screens.main_menu_screen(
            content,
            character,
            derived_stats(content, character),
            "Прежний экран не сохранился — вы в главном меню.",
        ),
        emoji=account.settings.emoji if account is not None else False,
    )


async def step(
    message: Message,
    state: FSMContext,
    content: GameContent,
    settings: Settings,
    characters: CharacterRepository,
) -> None:
    """Один шаг создания на одно пришедшее сообщение."""
    if message.from_user is None or message.text is None:
        return

    data = await state.get_data()
    flow = CreationState.deserialise(data.get(STATE_KEY, "")) if data.get(STATE_KEY) else begin()

    # Единственная часть состояния, которую чистая ветка сама знать не может.
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
        character = await sync_keeper(character, message.from_user.id, settings, characters)
        await state.clear()
        await state.set_state(Play.main_menu)
        city = play_screens.standing_in(content, character)
        await send_screen(message, created_screen(character.name, city.name))
        return

    await state.set_state(STATE_FOR_SCREEN[updated.screen])
    await state.update_data({STATE_KEY: updated.serialise()})
    await send_screen(message, render(content, updated))
