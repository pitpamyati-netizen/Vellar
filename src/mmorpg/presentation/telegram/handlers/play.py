"""Handlers for menu, world, city, services and location navigation.

Thin by design: load state, call the pure flow, store what the flow decided,
render, send one message. The clock lives here - the flow receives the moment,
the shop rotation and what is left in the nodes as values, which is what keeps
generation reproducible (``docs/procgen.md``).

The shared state of a location lives here too: this is the only place that reads
who is standing where and takes out of a node what a step took - the map itself
is permanent and is never stored (``domain/rules/nodes.py``).

The flow never writes. Everything it decided to change arrives in
``PlayState.pending`` and is applied here, in one place, so there is exactly one
answer to "where does the game store things".
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
from mmorpg.config import Settings
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.location import LocationState, Presence
from mmorpg.domain.entities.moderation import Ban, KeeperAction, KeeperEntry
from mmorpg.domain.entities.overlay import OverlayKind
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.entities.trade import TradeRecord
from mmorpg.domain.ports.repositories import (
    CharacterRepository,
    ContentOverlayRepository,
    InventoryRepository,
    KeeperLogRepository,
    LocationStateCache,
    TradeRepository,
    User,
    UserRepository,
)
from mmorpg.domain.procgen.seeds import rotation_index
from mmorpg.domain.rules import economy as economy_rules
from mmorpg.domain.rules import moderation as moderation_rules
from mmorpg.domain.rules import nodes as node_rules
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

# What a location remembers about its nodes lives a week; forgetting that is the
# same thing as everything in it having refilled long ago. Presence is much
# shorter: a player who has not pressed anything for ten minutes has walked off,
# whatever their last screen says.
LOCATION_TTL = 7 * 24 * 60 * 60
PRESENCE_TTL = 10 * 60


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
    keeper_log: KeeperLogRepository,
    locations: LocationStateCache,
    overlays: ContentOverlayRepository,
    registry: ContentRegistry,
    trades: TradeRepository,
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
            location_state=here,
            now=now,
        )
        return

    # The screen the step landed on is not the screen it started from, and the
    # bag may have changed on the way, so what it shows is read again.
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
    """Draw one play screen, and return it. Used here and by the fight handler.

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
    """Who else is standing on this node. Empty everywhere but in a location."""
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
    """What the bag holds and what the shelf offers, for the screens that need it.

    The assortment is rolled, never stored: same city, same rotation, same shelf
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
        if write.gold_flow:
            # Signed, and counting the strongbox: gold moved into the bank has not
            # left the game, so a deposit is not an outflow (``mmorpg.economy_log``).
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
    """Put the visit and the shared location back in step with each other.

    A location belongs to everybody standing in it: the map is the same map for
    everyone and needs no storage at all, but what is *left* in its nodes is
    shared and does. This is where a step takes its one thing out of a node, and
    where this player is put on the map so the others can see them. Nothing here
    ever reaches PostgreSQL.
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
