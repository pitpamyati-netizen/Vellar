"""Хендлеры меню, мира, города, служб и хождения по локации.

Тонкие по замыслу: прочитать состояние, позвать чистую ветку, сохранить то, что
ветка решила, нарисовать, отправить одно сообщение. Часы живут здесь: ветка
получает момент, переворот лавки и то, что осталось в узлах, значениями, и это
то, что делает сборку воспроизводимой (``docs/procgen.md``).

Общее состояние локации живёт тоже здесь: только это место читает, кто где
стоит, и вынимает из узла то, что вынул шаг, - сама карта постоянна и не
хранится никогда (``domain/rules/nodes.py``).

Ветка не пишет никогда. Всё, что она решила изменить, приходит в
``PlayState.pending`` и применяется здесь, в одном месте, чтобы на вопрос «где
игра сохраняет» был ровно один ответ.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import replace

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from mmorpg import economy_log
from mmorpg.application.services import group_trade, keeper_panel, moderation
from mmorpg.application.services.content import ContentRegistry
from mmorpg.application.services.keeper import set_keeper, sync_keeper
from mmorpg.application.services.party import PartyStore
from mmorpg.config import Settings
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.location import LocationState, Presence
from mmorpg.domain.entities.moderation import Ban, KeeperAction, KeeperEntry
from mmorpg.domain.entities.overlay import OverlayKind
from mmorpg.domain.entities.party import ROLE_DUTIES, PartyRole, role_by_word, role_name
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.entities.trade import TradeRecord
from mmorpg.domain.ports.repositories import (
    CharacterRepository,
    ContentOverlayRepository,
    InventoryRepository,
    KeeperLogRepository,
    LocationStateCache,
    StateCache,
    TradeRepository,
    User,
    UserRepository,
)
from mmorpg.domain.procgen.seeds import rotation_index
from mmorpg.domain.rules import economy as economy_rules
from mmorpg.domain.rules import moderation as moderation_rules
from mmorpg.domain.rules import nodes as node_rules
from mmorpg.domain.rules import party as party_rules
from mmorpg.domain.rules import progression
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules.economy import buy_price, roll_assortment
from mmorpg.domain.rules.modifiers import collect_modifiers
from mmorpg.domain.rules.stats import derived_stats, primary_stats
from mmorpg.logging import get_logger
from mmorpg.presentation.telegram.flows import keeper as keeper_flow
from mmorpg.presentation.telegram.flows.play import (
    advance,
    begin,
    build_location,
    location_known,
    render,
    visit_seed,
)
from mmorpg.presentation.telegram.flows.state import (
    TYPING_NAME,
    Clock,
    Goods,
    LocationSession,
    PendingWrite,
    PlayState,
)
from mmorpg.presentation.telegram.handlers.combat import open_fight
from mmorpg.presentation.telegram.handlers.creation import welcome_screen
from mmorpg.presentation.telegram.messaging import send_screen, send_text
from mmorpg.presentation.telegram.routing import Intent, parse_command
from mmorpg.presentation.telegram.screens import keeper as keeper_screens
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.keeper import KeeperView
from mmorpg.presentation.telegram.screens.play import level_up_report
from mmorpg.presentation.telegram.screens.shop import OwnedItem
from mmorpg.presentation.telegram.states.screens import STATE_FOR_SCREEN, Play

logger = get_logger(__name__)

STATE_KEY = "play"

# Сколько персонажей показывает список игроков. Больше не нужно: кого нет в
# последних, ищут по имени.
PLAYERS_SHOWN = 24

DAY = 24 * 60 * 60
WEEK = 7 * DAY

# То, что локация помнит о своих узлах, живёт неделю; забыть это - то же самое, что «всё
# в ней давно наполнилось заново». Присутствие живёт куда меньше: игрок, не нажимавший
# ничего десять минут, ушёл, что бы ни говорил его последний экран.
LOCATION_TTL = 7 * 24 * 60 * 60
PRESENCE_TTL = 10 * 60


def build_router() -> Router:
    """Свежий роутер на приложение - см. handlers.creation.build_router."""
    router = Router(name="play")
    # Экраны - это личный разговор: один игрок, одна клавиатура, одно сообщение за раз.
    # У группы свой роутер и свои правила.
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
    keeper_log: KeeperLogRepository,
    locations: LocationStateCache,
    overlays: ContentOverlayRepository,
    registry: ContentRegistry,
    trades: TradeRepository,
    state_cache: StateCache,
    user: User | None = None,
) -> None:
    if message.from_user is None or message.text is None:
        return

    character = await characters.get_active(message.from_user.id)
    if character is None:
        await state.clear()
        await send_screen(message, welcome_screen())
        return
    # Аккаунт обычно уже прочитан на входе (``middlewares/moderation.py``), и
    # тогда второй раз его читать незачем. Читается он здесь только там, где
    # хендлер вызывают без той двери, — то есть в тестах.
    if user is None:
        user = await users.get(message.from_user.id)
    character = await sync_keeper(
        character,
        message.from_user.id,
        settings,
        characters,
        granted=user is not None and user.keeper,
    )
    # Умение, которого в игре больше нет, забывается и возвращает очки: иначе
    # выкатка, убравшая умение, оставила бы игрока с пустым слотом и без очка,
    # чтобы занять его заново (``domain/rules/skills.reclaim_lost``).
    reclaimed = skill_rules.reclaim_lost(content, character)
    if reclaimed is not None:
        character = reclaimed
        await characters.save(character)
        logger.info("skills_reclaimed", character_id=character.id)
    emoji = user.settings.emoji if user is not None else False
    accessibility = user.settings if user is not None else None

    data = await state.get_data()
    flow = PlayState.deserialise(data[STATE_KEY]) if data.get(STATE_KEY) else begin(character)

    now = int(time.time())
    clock = Clock(
        now=now,
        shop_rotation=rotation_index(now, settings.shop_rotation_seconds),
        gather_cooldown=settings.gather_cooldown_seconds,
    )
    goods = await _goods(content, character, flow, inventory, settings, clock.shop_rotation)
    company = await _company(flow, character, locations, now)
    here = await _location_state(content, flow, locations, now)
    view = await _keeper_view(
        flow,
        character,
        message.text,
        characters,
        users,
        keeper_log,
        trades,
        registry,
        now,
        settings,
    )

    # Отряд собирается командой, а не экраном: звать и соглашаться приходится из
    # любого места, где игрок сейчас стоит (``domain/rules/party.py``).
    party_answer = await _party_step(message, character, characters, state_cache)
    if party_answer is not None:
        updated = flow.with_notice(party_answer)
    else:
        updated = advance(
            content,
            character,
            flow,
            message.text,
            world_seed=settings.world_seed,
            clock=clock,
            goods=goods,
            settings=accessibility,
            neighbours=company,
            keeper=view,
            location_state=here,
        )

    if updated.invite:
        called = await _call_to_party(
            message, character, updated.invite, company, characters, state_cache
        )
        updated = replace(updated, invite=0).with_notice(called)

    before_level = character.level
    character = await _apply(
        updated.pending, character, message.from_user.id, characters, inventory, users
    )
    served = await _serve(
        updated.pending,
        characters=characters,
        users=users,
        inventory=inventory,
        trades=trades,
        overlays=overlays,
        keeper_log=keeper_log,
        registry=registry,
        bot=message.bot,
        now=now,
        settings=settings,
        acting=character,
        granting=settings.is_admin(message.from_user.id),
    )
    if served:
        updated = updated.with_notice(f"{updated.notice} {served}".strip())
    # Правка могла только что изменить мир, а рисовать надо уже изменённый.
    content = registry.current
    updated, here = await sync_location(content, updated, flow, character, locations, now, settings)

    if updated.fight:
        await open_fight(
            message,
            state,
            content=content,
            settings=settings,
            character=character,
            flow=updated,
            emoji=emoji,
            characters=characters,
            state_cache=state_cache,
            storage=state.storage,
            location_state=here,
            now=now,
        )
        return

    # Экран, на котором кончился шаг, - не тот, с которого он начался, и сумка по дороге
    # могла измениться, поэтому то, что он показывает, читается заново.
    shelf = await _goods(content, character, updated, inventory, settings, clock.shop_rotation)
    company = await _company(updated, character, locations, now)
    counted = await _tally(content, updated, characters)
    shown = await _keeper_view(
        updated, character, "", characters, users, keeper_log, trades, registry, now, settings
    )
    await state.set_state(STATE_FOR_SCREEN[updated.screen])
    await state.update_data({STATE_KEY: updated.serialise()})
    screen = await render_play(
        message,
        content,
        settings,
        updated,
        character,
        emoji=emoji,
        goods=shelf,
        clock=clock,
        neighbours=company,
        tally=counted,
        keeper=shown,
        location_state=here,
    )
    # Уровень объявляется вторым сообщением, и это единственное место в игре,
    # где одно действие отвечает дважды: заданием, узлом или ремеслом уровень
    # берут так же, как боем (``screens/play.level_up_report``).
    grown = progression.growth(content, before_level, character.level)
    if grown is not None:
        await send_text(
            message,
            level_up_report(content, character, derived_stats(content, character), grown),
            screen,
            emoji=emoji,
        )


async def render_play(
    message: Message,
    content: GameContent,
    settings: Settings,
    flow: PlayState,
    character: Character,
    *,
    emoji: bool = False,
    goods: Goods | None = None,
    clock: Clock | None = None,
    neighbours: Sequence[Presence] = (),
    tally: Mapping[str, int] | None = None,
    keeper: KeeperView | None = None,
    location_state: LocationState | None = None,
) -> Screen:
    """Нарисовать один игровой экран и вернуть его. Берётся здесь и боевым хендлером.

    Возвращается тот же экран, что и отправлен: сообщение про новый уровень
    идёт следом и несёт ту же клавиатуру, чтобы игрок остался там же, где стоял.
    """
    shelf = goods if goods is not None else Goods(gold=character.gold)
    screen = render(
        content,
        character,
        flow,
        world_seed=settings.world_seed,
        goods=shelf,
        clock=clock,
        neighbours=neighbours,
        tally=tally,
        keeper=keeper,
        location_state=location_state,
    )
    await send_screen(message, screen, emoji=emoji)
    return screen


async def _tally(
    content: GameContent,
    flow: PlayState,
    characters: CharacterRepository,
) -> Mapping[str, int]:
    """Голоса за открытый вопрос. Считаются только там, где их показывают."""
    if flow.screen is not ScreenId.TURNING:
        return {}
    turning = content.open_turning()
    if turning is None:
        return {}
    return await characters.turning_tally(turning.id)


async def _company(
    flow: PlayState,
    character: Character,
    locations: LocationStateCache,
    now: int,
) -> tuple[Presence, ...]:
    """Кто ещё стоит на этом узле. Везде, кроме локации, - пусто."""
    if flow.screen is not ScreenId.LOCATION or not flow.session.active:
        return ()
    return await locations.others_at(
        flow.session.city_id,
        flow.session.slot,
        flow.session.node,
        exclude=character.id,
        now=now,
        ttl=PRESENCE_TTL,
    )


async def _goods(
    content: GameContent,
    character: Character,
    flow: PlayState,
    inventory: InventoryRepository,
    settings: Settings,
    rotation: int,
) -> Goods:
    """Что лежит в сумке и что предлагает прилавок - для тех экранов, которым это нужно.

    Прилавок бросается, а не хранится: тот же город, тот же переворот - тот же
    прилавок (``docs/procgen.md``).
    """
    held = await inventory.list_items(character.id)
    owned = tuple(
        OwnedItem(item_id=entry.item_id, quantity=entry.quantity)
        for entry in held
        if content.has_item(entry.item_id)
    )
    if flow.screen is not ScreenId.SHOP:
        return Goods(gold=character.gold, owned=owned)

    bundle = collect_modifiers(content, character)
    stock = roll_assortment(
        content,
        world_seed=settings.world_seed,
        city_id=flow.city_id or character.city_id,
        rotation=rotation,
        character_level=character.level,
        reputation=bundle.get(economy_rules.REPUTATION_KEY, 0.0),
    )
    charisma = primary_stats(content, character)[StatCode.CHA]
    prices = {item.id: buy_price(content, item, charisma=charisma) for item in stock}
    return Goods(gold=character.gold, owned=owned, stock=stock, prices=prices)


async def _keeper_view(
    flow: PlayState,
    character: Character,
    text: str | None,
    characters: CharacterRepository,
    users: UserRepository,
    keeper_log: KeeperLogRepository,
    trades: TradeRepository,
    registry: ContentRegistry,
    now: int,
    settings: Settings,
) -> KeeperView:
    """Что панели показать. Для игрока это ноль запросов: ветка не выполняется.

    Считает ровно то, что нужно открытому экрану: список игроков не читается на
    статистике, а перепись не считается на карточке жителя.
    """
    if flow.screen not in keeper_flow.PANEL or not character.is_admin:
        return KeeperView()

    players: tuple[Character, ...] = ()
    target: Character | None = None
    census = None
    granting = settings.is_admin(character.user_id)

    log: tuple[KeeperEntry, ...] = ()

    if flow.screen is ScreenId.KEEPER_LOG:
        log = await keeper_log.latest(limit=keeper_screens.LOG_SHOWN)
    if flow.screen is ScreenId.KEEPER_PLAYERS:
        players = await characters.newest(limit=PLAYERS_SHOWN)
        # Имя набирают сообщением, и ищет его тот, у кого есть хранилище: автомат
        # получает уже найденного персонажа или пустоту.
        if flow.keeper_typing == TYPING_NAME and text and not text.startswith("/"):
            target = await characters.find_by_name(text)
    elif (
        flow.screen
        in {
            ScreenId.KEEPER_PLAYER,
            ScreenId.KEEPER_FIELD,
            ScreenId.KEEPER_BAN,
            ScreenId.KEEPER_TRADES,
        }
        and flow.keeper_target
    ):
        target = await characters.get(flow.keeper_target)
    elif flow.screen in {ScreenId.KEEPER_STATS, ScreenId.KEEPER_SERVICE}:
        counted = await characters.census(
            day=now - DAY, week=now - WEEK, stale=now - keeper_panel.ABANDONED_AFTER_DAYS * DAY
        )
        # Заблокировавшие - счёт по аккаунтам, а не по персонажам, поэтому он
        # приходит из другого хранилища и подставляется здесь.
        census = replace(
            counted,
            blocked=await users.blocked_count(),
            banned=await users.banned_count(now=now),
        )

    # Право открытого игрока читается по аккаунту, а не по флагу персонажа:
    # персонажей у него может быть несколько, а право одно. Спрашивается только
    # тогда, когда его есть кому увидеть.
    target_keeper = False
    target_locked = False
    target_ban = Ban()
    if target is not None:
        account = await users.get(target.user_id)
        # Блокировку читают всегда, когда открыт чужой персонаж: она стоит на
        # карточке строкой, а раздача права - только у того, кто его раздаёт.
        if account is not None and moderation_rules.is_banned(account.ban, now=now):
            target_ban = account.ban
        if granting:
            target_locked = settings.is_admin(target.user_id)
            target_keeper = target_locked or (account is not None and account.keeper)

    journal: tuple[TradeRecord, ...] = ()
    if flow.screen is ScreenId.KEEPER_TRADES and target is not None:
        journal = await trades.journal(target.id, limit=keeper_screens.TRADES_SHOWN)

    return KeeperView(
        records=registry.records,
        players=players,
        trades=journal,
        target=target,
        census=census,
        granting=granting,
        target_keeper=target_keeper,
        target_locked=target_locked,
        target_ban=target_ban,
        log=log,
        now=now,
    )


async def _serve(
    write: PendingWrite,
    *,
    characters: CharacterRepository,
    users: UserRepository,
    inventory: InventoryRepository,
    trades: TradeRepository,
    overlays: ContentOverlayRepository,
    keeper_log: KeeperLogRepository,
    registry: ContentRegistry,
    bot: Bot | None,
    now: int,
    settings: Settings,
    acting: Character,
    granting: bool = False,
) -> str:
    """Сделать то, о чём попросила панель, и сказать числом, что получилось.

    Здесь же пишется журнал: строку журнала складывает панель, а имя того, кто
    нажал, и момент проставляются тут — часов у автомата нет, а имени того, кто
    смотрит, у него быть и не должно.
    """
    said: list[str] = []
    stamp = KeeperEntry(at=now, keeper_id=acting.user_id, keeper_name=acting.name)

    if write.edit is not None:
        why = await keeper_panel.save_edit(overlays, registry, write.edit)
        if why:
            said.append("Пока не в игре: " + " ".join(why))
    if write.forget is not None:
        kind, entity_id = write.forget
        await keeper_panel.drop_edit(overlays, registry, OverlayKind(kind), entity_id)
    if write.reload:
        said.append(f"Правок перечитано: {await registry.reload(overlays)}.")
    if write.other is not None:
        await characters.save(write.other)
    if write.remove_character:
        await characters.delete(write.remove_character)
    if write.keeper_grant is not None and granting:
        # Автомат уже спросил то же самое; здесь оно спрашивается ещё раз, потому
        # что раздача права - единственное, что раздаёт саму панель.
        account, keeper = write.keeper_grant
        await set_keeper(users, characters, account, keeper=keeper, settings=settings)
    if write.ban is not None:
        said.append(await _ban(write.ban, users, keeper_log, characters, stamp, now))
    if write.rollback:
        said.append(await _roll_back(write.rollback, trades, characters, inventory))
    if write.service:
        swept = await _sweep(write.service, characters, users, bot, now)
        said.append(swept)
        await moderation.note(keeper_log, replace(stamp, action=KeeperAction.SWEEP, detail=swept))
    if write.note is not None:
        await moderation.note(
            keeper_log,
            replace(
                write.note, at=stamp.at, keeper_id=stamp.keeper_id, keeper_name=stamp.keeper_name
            ),
        )
    return " ".join(said)


async def _ban(
    order: tuple[int, str, str],
    users: UserRepository,
    keeper_log: KeeperLogRepository,
    characters: CharacterRepository,
    stamp: KeeperEntry,
    now: int,
) -> str:
    """Наложить блокировку или снять её. Срок считается здесь: домен без часов."""
    account, key, reason = order
    sentence = moderation_rules.sentence_of(key) if key else None
    if key and sentence is None:
        return "Такого срока нет, блокировка не наложена."
    ban = (
        moderation_rules.imposed(sentence, reason, now=now)
        if sentence is not None
        else moderation_rules.lifted()
    )
    # Имя для журнала берётся у персонажа, а не у аккаунта: в журнале читают
    # имена, а не числа.
    played = await characters.list_for_user(account)
    named = played[0].name if played else str(account)
    await moderation.set_ban(users, keeper_log, account, ban, by=stamp, target=named)
    if not key:
        return f"{named}: блокировка снята."
    return f"{named}: блокировка наложена."


async def _roll_back(
    trade_id: int,
    trades: TradeRepository,
    characters: CharacterRepository,
    inventory: InventoryRepository,
) -> str:
    """Откатить расчёт и сказать числами, что вернулось.

    Числа названы все, включая невернувшееся: сделка, откаченная наполовину, —
    это работа, которую смотритель должен доделать руками, и узнать об этом он
    должен здесь, а не от игрока через сутки.
    """
    undone = await group_trade.roll_back(
        trade_id, trades=trades, characters=characters, inventory=inventory
    )
    if not undone.done:
        return "Откатывать нечего: расчёт по этой сделке не проходил или уже откачен."
    said = [
        "Вещь вернулась." if undone.item_returned else "Вещи у него уже нет, вернуть нечего.",
    ]
    if undone.gold_returned:
        said.append(f"Возвращено золота: {undone.gold_returned}.")
    if undone.gold_missing:
        said.append(f"Не хватило золота: {undone.gold_missing}. Остаток выдайте вручную.")
    if not undone.gold_returned and not undone.gold_missing:
        said.append("Золото не двигалось: сделка была без цены.")
    return " ".join(said)


async def _sweep(
    service: str,
    characters: CharacterRepository,
    users: UserRepository,
    bot: Bot | None,
    now: int,
) -> str:
    """Одна уборка. Каждая отвечает числом, потому что каждая стирает."""
    if service == keeper_flow.SWEEP_DRAFTS:
        swept = await keeper_panel.sweep_drafts(characters, now=now)
        return f"Убрано брошенных персонажей: {swept.removed}."
    if service == keeper_flow.SWEEP_BLOCKED:
        swept = await keeper_panel.drop_blocked(users)
        return f"Убрано заблокировавших: {swept.removed}."

    async def probe(telegram_id: int) -> bool:
        return await _reachable(bot, telegram_id)

    swept = await keeper_panel.sweep_blocked(users, probe, now=now)
    return f"Проверено аккаунтов: {swept.checked}. Из них заблокировали бота: {swept.blocked}."


async def _tell(message: Message, telegram_id: int, text: str) -> None:
    """Одна строка тому, кто сейчас не смотрит в игру.

    Не дошло - значит не дошло: зов в отряд не стоит того, чтобы уронить ход
    того, кто его отправил.
    """
    bot = message.bot
    if bot is None:  # pragma: no cover - у сообщения всегда есть бот
        return
    try:
        await bot.send_message(chat_id=telegram_id, text=text)
    except TelegramAPIError:
        logger.info("party_notice_undelivered", telegram_id=telegram_id)


async def _reachable(bot: Bot | None, telegram_id: int) -> bool:
    """Читает ли этот человек бота.

    Спрашивается самым дешёвым, что есть: «печатает» не оставляет сообщения в
    переписке. Непонятная ошибка считается «читает»: сеть моргнула — не повод
    стирать человека.
    """
    if bot is None:  # pragma: no cover - у сообщения всегда есть бот
        return True
    try:
        await bot.send_chat_action(chat_id=telegram_id, action="typing")
    except TelegramForbiddenError:
        return False
    except TelegramAPIError:
        return True
    return True


async def _apply(
    write: PendingWrite,
    character: Character,
    telegram_id: int,
    characters: CharacterRepository,
    inventory: InventoryRepository,
    users: UserRepository,
) -> Character:
    """Сохранить всё, что шаг решил изменить. Единственный, кто в ветке пишет."""
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
        if write.gold_flow:
            # Со знаком и с учётом сундука: золото, ушедшее в банк, из игры не ушло,
            # поэтому вклад оттоком не считается (``mmorpg.economy_log``).
            moved = (write.character.gold + write.character.bank_gold) - (
                character.gold + character.bank_gold
            )
            economy_log.record(write.gold_flow, moved, character_id=character.id)
        await characters.save(write.character)
        return write.character
    return character


async def _location_state(
    content: GameContent,
    flow: PlayState,
    locations: LocationStateCache,
    now: int,
) -> LocationState:
    """Что стоит в узлах локации, где игрок находится. Пусто вне локации."""
    if not flow.session.active or not location_known(content, flow.session):
        return LocationState()
    return await locations.state(flow.session.city_id, flow.session.slot, now=now)


async def sync_location(
    content: GameContent,
    updated: PlayState,
    before: PlayState,
    character: Character,
    locations: LocationStateCache,
    now: int,
    settings: Settings,
) -> tuple[PlayState, LocationState]:
    """Свести вылазку и общую локацию обратно в шаг.

    Локация принадлежит всем, кто в ней стоит: карта у всех одна и хранения не
    требует вовсе, а вот то, что *осталось* в её узлах, общее и хранения требует.
    Здесь шаг вынимает из узла своё одно, и здесь этого игрока ставят на карту,
    чтобы остальные его видели. До PostgreSQL отсюда не доходит ничто.
    """
    session = updated.session
    if not session.active:
        if before.session.active:
            await locations.leave(before.session.city_id, before.session.slot, character.id)
        return updated, LocationState()

    if not location_known(content, session):
        return updated, LocationState()

    state = await locations.state(session.city_id, session.slot, now=now)
    index = updated.pending.node_take
    if index >= 0:
        state = await take_from_node(content, session, index, locations, now, settings, state=state)

    await locations.arrive(
        session.city_id,
        session.slot,
        Presence(
            character_id=character.id,
            name=character.name,
            level=character.level,
            node=session.node,
        ),
        now=now,
        ttl=PRESENCE_TTL,
    )
    return updated, state


async def take_from_node(
    content: GameContent,
    session: LocationSession,
    index: int,
    locations: LocationStateCache,
    now: int,
    settings: Settings,
    *,
    state: LocationState | None = None,
    wave: int | None = None,
) -> LocationState:
    """Забрать из узла одну единицу: убитую стаю, горсть руды, свёрток из тайника.

    Волна, которую видел игрок, передаётся вниз: нажатие, опоздавшее к смене
    волны, ничего не забирает (``domain/rules/nodes.py``).
    """
    location = build_location(content, settings.world_seed, session)
    seed = visit_seed(settings.world_seed, session)
    known = state
    if known is None:
        known = await locations.state(session.city_id, session.slot, now=now)
    left = node_rules.standing_at(seed, location, known, index, now)
    if left.empty:
        return known
    return await locations.take(
        session.city_id,
        session.slot,
        index,
        wave=left.wave if wave is None else wave,
        size=left.size,
        now=now,
        ttl=LOCATION_TTL,
    )


# --- отряд ------------------------------------------------------------
#
# Отряд собирается двумя движениями: кнопкой «Позвать в отряд» на своём узле и
# командой у того, кого позвали. Команда - потому что зов приходит туда, где
# человек сейчас стоит, а перерисовывать чужой экран под одну кнопку игра не
# станет: одно действие - одно сообщение (``docs/accessibility.md``, правило 3).


async def _party_names(
    party: party_rules.Party, characters: CharacterRepository
) -> tuple[str, ...]:
    """Кто в отряде и кто на каком месте - в том порядке, в каком собрались."""
    names: list[str] = []
    for member_id in party.members:
        one = await characters.get(member_id)
        if one is not None:
            place = party.role_of(member_id)
            names.append(f"{one.name} — {role_name(place).lower()}" if place else one.name)
    return tuple(names)


def _free_places(party: party_rules.Party) -> str:
    """Какие места ещё свободны, вместе с тем, что каждое из них делает."""
    free = [
        role for role in PartyRole if role is not PartyRole.LEADER and not party.holder_of(role)
    ]
    if not free:
        return "Свободных мест нет."
    listed = "; ".join(f"{role_name(role).lower()} — {ROLE_DUTIES[role]}" for role in free)
    return f"Свободные места: {listed}. Встать: «/отряд щит», уйти с места: «/отряд снять»."


async def _party_step(
    message: Message,
    character: Character,
    characters: CharacterRepository,
    state_cache: StateCache,
) -> str | None:
    """Ответ на команду отряда. ``None`` - это была не она."""
    command = parse_command(message.text or "")
    if command is None:
        return None
    parties = PartyStore(state_cache)

    match command.intent:
        case Intent.PARTY:
            party = await parties.of(character.id)
            caller_id = await parties.called_by(character.id)
            lines = []
            if party is not None:
                names = await _party_names(party, characters)
                lines.append(f"Ваш отряд: {', '.join(names)}.")
                lines.append(_free_places(party))
                lines.append("«/отряд уйти» — уйти из отряда.")
            else:
                lines.append("Вы идёте один. Позвать соседа можно кнопкой на узле локации.")
            if caller_id:
                caller = await characters.get(caller_id)
                if caller is not None:
                    lines.append(
                        f"{caller.name} зовёт вас в отряд: «/отряд принять» или «/отряд отказать»."
                    )
            return " ".join(lines)

        case Intent.PARTY_ACCEPT:
            party = await parties.accept(character.id)
            if party is None:
                return "Вас сейчас никто не зовёт."
            names = await _party_names(party, characters)
            for member_id in party.members:
                if member_id == character.id:
                    continue
                other = await characters.get(member_id)
                if other is not None:
                    await _tell(message, other.user_id, f"{character.name} идёт с вами.")
            return f"Вы в отряде: {', '.join(names)}. Бой у вас теперь общий."

        case Intent.PARTY_ROLE:
            return await _take_place(character, command.argument, parties, characters)

        case Intent.PARTY_DECLINE:
            await parties.forget_call(character.id)
            return "Зов отклонён."

        case Intent.PARTY_LEAVE:
            party = await parties.of(character.id)
            if party is None:
                return "Вы и так идёте один."
            left = await parties.leave(character.id)
            if left is None:
                return "Отряд распущен."
            for member_id in left.members:
                other = await characters.get(member_id)
                if other is not None:
                    await _tell(message, other.user_id, f"{character.name} ушёл из отряда.")
            return "Вы вышли из отряда."

        case _:
            return None


async def _take_place(
    character: Character,
    word: str,
    parties: PartyStore,
    characters: CharacterRepository,
) -> str:
    """Встать на место в отряде или уйти с него.

    Место занимает сам человек: раздавать чужие места вожаку было бы правом
    менять чужой бой, не спросив, - а в бой за игрока в Велларе не ходит никто
    (``docs/accessibility.md``).
    """
    party = await parties.of(character.id)
    if party is None:
        return "Вы идёте один: места раздают только в отряде."

    role = role_by_word(word)
    if role is None:
        left = await parties.take_role(character.id, None)
        return "Вы сошли с места." if left is not None else "Вы идёте один."

    holder_id = party.holder_of(role)
    holder = await characters.get(holder_id) if holder_id else None
    refused = party_rules.role_refusal(
        party, character.id, role, holder.name if holder is not None else ""
    )
    if refused:
        return refused

    await parties.take_role(character.id, role)
    return f"Вы встали на место: {role_name(role).lower()}. В бою это значит: {ROLE_DUTIES[role]}."


async def _call_to_party(
    message: Message,
    character: Character,
    target_id: int,
    company: Sequence[Presence],
    characters: CharacterRepository,
    state_cache: StateCache,
) -> str:
    """Позвать соседа по узлу. Согласие даёт он сам, и другого пути нет."""
    target = await characters.get(target_id)
    if target is None or not any(person.character_id == target_id for person in company):
        return "Этого человека здесь больше нет."

    parties = PartyStore(state_cache)
    party = await parties.of(character.id)
    theirs = await parties.of(target.id)
    refused = party_rules.invite_refusal(
        inviter_level=character.level,
        invitee_name=target.name,
        invitee_level=target.level,
        party=party,
        invitee_in_party=theirs is not None,
    )
    if refused:
        return refused

    leader_id = party.leader_id if party is not None else character.id
    await parties.call(leader_id=leader_id, invitee_id=target.id)
    await _tell(
        message,
        target.user_id,
        f"{character.name}, уровень {character.level}, зовёт вас в отряд. "
        "Наберите «/отряд принять», чтобы пойти вместе, или «/отряд отказать».",
    )
    return f"Зов отправлен: {target.name}. Ответит — пойдёте вместе."
