"""Handlers for menu, world, city, services and location navigation.

Thin by design: load state, call the pure flow, store what the flow decided,
render, send one message. The clock lives here - the flow receives the moment,
the shop rotation and the standing generation of a location as values, which is
what keeps generation reproducible (``docs/procgen.md``).

The shared state of a location lives here too: this is the only place that reads
who is standing where, writes back what a step cleared, and rolls a location over
into its next generation once it is emptied.

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
from mmorpg.application.services import keeper_panel
from mmorpg.application.services.content import ContentRegistry
from mmorpg.application.services.keeper import set_keeper, sync_keeper
from mmorpg.config import Settings
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.location import NodeKind, Presence
from mmorpg.domain.entities.overlay import OverlayKind
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.ports.repositories import (
    CharacterRepository,
    ContentOverlayRepository,
    InventoryRepository,
    LocationStateCache,
    UserRepository,
)
from mmorpg.domain.procgen.location import is_cleared
from mmorpg.domain.procgen.seeds import rotation_index
from mmorpg.domain.rules.economy import buy_price, roll_assortment
from mmorpg.domain.rules.stats import primary_stats
from mmorpg.presentation.telegram.flows import keeper as keeper_flow
from mmorpg.presentation.telegram.flows.play import (
    advance,
    begin,
    build_location,
    render,
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
from mmorpg.presentation.telegram.messaging import send_screen
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.screens.keeper import KeeperView
from mmorpg.presentation.telegram.screens.shop import OwnedItem
from mmorpg.presentation.telegram.states.screens import STATE_FOR_SCREEN, Play

STATE_KEY = "play"

# Сколько персонажей показывает список игроков. Больше не нужно: кого нет в
# последних, ищут по имени.
PLAYERS_SHOWN = 24

DAY = 24 * 60 * 60
WEEK = 7 * DAY

# A location holds its map for a week of being ignored; after that it is re-rolled,
# which is the same thing as nobody having been there. Presence is much shorter:
# a player who has not pressed anything for ten minutes has walked off, whatever
# their last screen says.
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
    locations: LocationStateCache,
    overlays: ContentOverlayRepository,
    registry: ContentRegistry,
) -> None:
    if message.from_user is None or message.text is None:
        return

    character = await characters.get_active(message.from_user.id)
    if character is None:
        await state.clear()
        await send_screen(message, welcome_screen())
        return
    user = await users.get(message.from_user.id)
    character = await sync_keeper(
        character,
        message.from_user.id,
        settings,
        characters,
        granted=user is not None and user.keeper,
    )
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
    view = await _keeper_view(
        flow, character, message.text, characters, users, registry, now, settings
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
    )

    character = await _apply(
        updated.pending, character, message.from_user.id, characters, inventory, users
    )
    served = await _serve(
        updated.pending,
        characters=characters,
        users=users,
        overlays=overlays,
        registry=registry,
        bot=message.bot,
        now=now,
        settings=settings,
        granting=settings.is_admin(message.from_user.id),
    )
    if served:
        updated = updated.with_notice(f"{updated.notice} {served}".strip())
    # Правка могла только что изменить мир, а рисовать надо уже изменённый.
    content = registry.current
    updated = await sync_location(content, updated, flow, character, locations, now, settings)

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
        )
        return

    # The screen the step landed on is not the screen it started from, and the
    # bag may have changed on the way, so what it shows is read again.
    shelf = await _goods(content, character, updated, inventory, settings, clock.shop_rotation)
    company = await _company(updated, character, locations, now)
    counted = await _tally(content, updated, characters)
    shown = await _keeper_view(updated, character, "", characters, users, registry, now, settings)
    await state.set_state(STATE_FOR_SCREEN[updated.screen])
    await state.update_data({STATE_KEY: updated.serialise()})
    await render_play(
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
) -> None:
    """Draw one play screen. Used by this handler and by the fight handler."""
    shelf = goods if goods is not None else Goods(gold=character.gold)
    await send_screen(
        message,
        render(
            content,
            character,
            flow,
            world_seed=settings.world_seed,
            goods=shelf,
            clock=clock,
            neighbours=neighbours,
            tally=tally,
            keeper=keeper,
        ),
        emoji=emoji,
    )


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

    stock = roll_assortment(
        content,
        world_seed=settings.world_seed,
        city_id=flow.city_id or character.city_id,
        rotation=rotation,
        character_level=character.level,
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

    if flow.screen is ScreenId.KEEPER_PLAYERS:
        players = await characters.newest(limit=PLAYERS_SHOWN)
        # Имя набирают сообщением, и ищет его тот, у кого есть хранилище: автомат
        # получает уже найденного персонажа или пустоту.
        if flow.keeper_typing == TYPING_NAME and text and not text.startswith("/"):
            target = await characters.find_by_name(text)
    elif flow.screen in {ScreenId.KEEPER_PLAYER, ScreenId.KEEPER_FIELD} and flow.keeper_target:
        target = await characters.get(flow.keeper_target)
    elif flow.screen in {ScreenId.KEEPER_STATS, ScreenId.KEEPER_SERVICE}:
        counted = await characters.census(
            day=now - DAY, week=now - WEEK, stale=now - keeper_panel.ABANDONED_AFTER_DAYS * DAY
        )
        # Заблокировавшие - счёт по аккаунтам, а не по персонажам, поэтому он
        # приходит из другого хранилища и подставляется здесь.
        census = replace(counted, blocked=await users.blocked_count())

    # Право открытого игрока читается по аккаунту, а не по флагу персонажа:
    # персонажей у него может быть несколько, а право одно. Спрашивается только
    # тогда, когда его есть кому увидеть.
    target_keeper = False
    target_locked = False
    if granting and target is not None:
        target_locked = settings.is_admin(target.user_id)
        account = await users.get(target.user_id)
        target_keeper = target_locked or (account is not None and account.keeper)

    return KeeperView(
        records=registry.records,
        players=players,
        target=target,
        census=census,
        granting=granting,
        target_keeper=target_keeper,
        target_locked=target_locked,
    )


async def _serve(
    write: PendingWrite,
    *,
    characters: CharacterRepository,
    users: UserRepository,
    overlays: ContentOverlayRepository,
    registry: ContentRegistry,
    bot: Bot | None,
    now: int,
    settings: Settings,
    granting: bool = False,
) -> str:
    """Сделать то, о чём попросила панель, и сказать числом, что получилось."""
    said: list[str] = []

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
    if write.service:
        said.append(await _sweep(write.service, characters, users, bot, now))
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


async def sync_location(
    content: GameContent,
    updated: PlayState,
    before: PlayState,
    character: Character,
    locations: LocationStateCache,
    now: int,
    settings: Settings,
) -> PlayState:
    """Put the visit and the shared location back in step with each other.

    A location belongs to everybody standing in it: the map that is up, the nodes
    already emptied and the people walking around. This is where a visit reads
    that state on the way in, writes back what this step cleared, rolls the place
    over when the last node falls, and says where this player is standing so the
    others can see them. Nothing here ever reaches PostgreSQL.
    """
    session = updated.session
    if not session.active:
        if before.session.active:
            await locations.leave(before.session.city_id, before.session.slot, character.id)
        return updated

    entered = not before.session.active or before.session.slot != session.slot
    if entered:
        state = await locations.state(session.city_id, session.slot)
        session = replace(session, generation=state.generation, cleared=state.cleared)
    else:
        fresh = session.cleared & ~before.session.cleared
        state = await locations.state(session.city_id, session.slot)
        for index in range(64):
            if fresh & (1 << index):
                state = await locations.mark_cleared(
                    session.city_id, session.slot, session.generation, index, LOCATION_TTL
                )
        session = replace(session, generation=state.generation, cleared=state.cleared | fresh)

    session = await _roll_over_if_cleared(content, session, locations, settings)
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
    return replace(updated, session=session)


async def _roll_over_if_cleared(
    content: GameContent,
    session: LocationSession,
    locations: LocationStateCache,
    settings: Settings,
) -> LocationSession:
    """A location emptied of everything worth doing gets a new map, once.

    The doors do not count: they are never cleared, and waiting for them would
    mean the place never rolled over at all. Whoever finishes the last node walks
    out into a location that has already changed - which is what "the map lives
    until it is cleared" means.
    """
    location = build_location(content, settings.world_seed, session)
    worth_doing = [
        node.index for node in location.nodes if node.kind not in {NodeKind.ENTRANCE, NodeKind.EXIT}
    ]
    if any(not is_cleared(session.cleared, index) for index in worth_doing):
        return session
    rolled = await locations.rotate(session.city_id, session.slot, session.generation, LOCATION_TTL)
    return replace(session, generation=rolled.generation, cleared=rolled.cleared, node=0)
