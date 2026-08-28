"""Хендлеры меню, мира, города, служб и хождения по локации.

Тонкие по замыслу: прочитать состояние, позвать чистую ветку, сохранить то, что
ветка решила, нарисовать, отправить одно сообщение. Часы живут здесь: ветка
получает момент, переворот лавки и то, что осталось в узлах, значениями, и это
то, что делает сборку воспроизводимой (``docs/procgen.md``).

Общее состояние локации живёт тоже здесь: только это место читает, кто где
стоит, и вынимает из узла то, что вынул шаг, - сама карта не хранится никогда,
она собирается из места и номера поколения (``domain/rules/nodes.py``).

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
from mmorpg.application.services.battle import BattleStore
from mmorpg.application.services.content import ContentRegistry
from mmorpg.application.services.guild import GuildStore
from mmorpg.application.services.keeper import set_keeper, sync_keeper
from mmorpg.application.services.party import PartyStore
from mmorpg.config import Settings
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.location import LocationState, Presence, Roamer
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
    PrivacyRepository,
    StateCache,
    TradeRepository,
    User,
    UserRepository,
)
from mmorpg.domain.procgen.seeds import rotation_index
from mmorpg.domain.rules import adventure, progression
from mmorpg.domain.rules import economy as economy_rules
from mmorpg.domain.rules import guild as guild_rules
from mmorpg.domain.rules import moderation as moderation_rules
from mmorpg.domain.rules import nodes as node_rules
from mmorpg.domain.rules import party as party_rules
from mmorpg.domain.rules import roamer as roamer_rules
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules import tutorial as tutorial_rules
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
    Descent,
    Goods,
    LocationSession,
    PendingWrite,
    PlayState,
    go_back,
)
from mmorpg.presentation.telegram.handlers.combat import open_fight
from mmorpg.presentation.telegram.handlers.creation import welcome_screen
from mmorpg.presentation.telegram.messaging import send_screen, send_text
from mmorpg.presentation.telegram.screens import guild as guild_screens
from mmorpg.presentation.telegram.screens import keeper as keeper_screens
from mmorpg.presentation.telegram.screens import party as party_screens
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
    parties: PartyStore,
    guilds: GuildStore,
    privacy: PrivacyRepository | None = None,
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

    party = await _party_view(flow, character, characters, parties)
    guild_view = await _guild_view(flow, character, characters, guilds)

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
        party=party,
        guild=guild_view,
        location_state=here,
    )

    # Отряд лежит в общем хранилище, поэтому автомат его только просит: завести,
    # расформировать, позвать, согласиться. Делает всё это хендлер, и он же
    # говорит, чем кончилось (``domain/rules/party.py``).
    if updated.party_action:
        said = await _party_step(message, character, updated.party_action, characters, parties)
        updated = replace(updated, party_action="").with_notice(said)
    if updated.invite:
        called = await _call_to_party(
            message, character, updated.invite, company, characters, parties
        )
        updated = replace(updated, invite=0).with_notice(called)
    if updated.invite_name:
        called = await _invite_by_name(message, character, updated.invite_name, characters, parties)
        updated = replace(updated, invite_name="").with_notice(called)
    if updated.guild_action:
        said, character = await _guild_step(
            message, character, updated.guild_action, updated.guild_arg, characters, guilds
        )
        updated = replace(updated, guild_action="", guild_arg="").with_notice(said)
    if updated.transfer_amount:
        said = await _transfer_step(
            message,
            content,
            character,
            updated.transfer_scope,
            updated.transfer_to,
            updated.transfer_item,
            updated.transfer_amount,
            characters,
            inventory,
            parties,
            guilds,
            privacy,
            state_cache,
        )
        updated = replace(updated, transfer_amount=0, transfer_to="", transfer_item="").with_notice(
            said
        )

    before_level = character.level
    before_tutorial = character.tutorial
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
    # За закрытый шаг обучения платят здесь, отдельной записью: опыт, золото,
    # зелья, а на завершении — доспех в пустые слоты (ADR 0038). Уровень от
    # опыта подхватит общий механизм второго сообщения ниже.
    character, updated = await _pay_tutorial(
        content, character, before_tutorial, updated, characters, inventory
    )
    updated, here = await sync_location(content, updated, flow, character, locations, now, settings)

    # Спуск в блуждающее подземелье: замок берут здесь, до боя, - подземелье
    # общее, а ветка ничего не читает и не пишет (ADR 0037).
    if updated.fight == "dungeon" and updated.descent.roamer and updated.descent.layer == 0:
        blocked = await _claim_roamer(character, updated.descent, locations, parties)
        if blocked:
            updated = go_back(replace(updated, fight="", descent=Descent())).with_notice(blocked)
            await state.set_state(STATE_FOR_SCREEN[updated.screen])
            await state.update_data({STATE_KEY: updated.serialise()})
            await render_play(
                message, content, settings, updated, character, emoji=emoji, location_state=here
            )
            return

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
            parties=parties,
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
    gathered = await _party_view(updated, character, characters, parties)
    guild_view = await _guild_view(updated, character, characters, guilds)
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
        party=gathered,
        guild=guild_view,
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
    party: party_screens.PartyView | None = None,
    guild: guild_screens.GuildView | None = None,
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
        party=party,
        guild=guild,
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
            ScreenId.KEEPER_TUNE,
            ScreenId.KEEPER_AMOUNT,
            ScreenId.KEEPER_GIVE,
            ScreenId.KEEPER_GIVE_GEAR,
            ScreenId.KEEPER_GIVE_ITEM,
            ScreenId.KEEPER_SKILLS,
            ScreenId.KEEPER_SKILL,
            ScreenId.KEEPER_SKILL_LEARN,
            ScreenId.KEEPER_SKILL_EDGE,
            ScreenId.KEEPER_SKILL_SLOT,
            ScreenId.KEEPER_STATS_EDIT,
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
    if write.grant_item is not None:
        character_id, item_id, delta = write.grant_item
        if delta > 0:
            await inventory.add(character_id, item_id, delta)
        elif delta < 0:
            await inventory.remove(character_id, item_id, -delta)
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


async def _pay_tutorial(
    content: GameContent,
    character: Character,
    before_mask: int,
    updated: PlayState,
    characters: CharacterRepository,
    inventory: InventoryRepository,
) -> tuple[Character, PlayState]:
    """Начислить награду за шаги обучения, закрытые этим действием (ADR 0038).

    ``character`` здесь уже с новыми битами (их сохранил ``_apply``); награда
    ложится отдельной записью, чтобы своя метка золотого журнала шага —
    покупка, сдача задания — не путалась с наградой.
    """
    newly = tutorial_rules.newly_done(before_mask, character.tutorial)
    if not newly:
        return character, updated

    payout = adventure.apply_tutorial_rewards(content, character, newly)
    await characters.save(payout.character)
    for item_id, count in payout.items:
        if content.has_item(item_id):
            await inventory.add(payout.character.id, item_id, count)
    if payout.gold:
        economy_log.record(economy_log.TUTORIAL, payout.gold, character_id=payout.character.id)
    said = " ".join(payout.lines)
    return payout.character, updated.with_notice(f"{updated.notice} {said}".strip())


async def _location_state(
    content: GameContent,
    flow: PlayState,
    locations: LocationStateCache,
    now: int,
) -> LocationState:
    """Что стоит в узлах локации, где игрок находится. Пусто вне локации."""
    if not flow.session.active or not location_known(content, flow.session):
        return LocationState()
    state = await locations.state(flow.session.city_id, flow.session.slot, now=now)
    roamer = await locations.roamer(flow.session.city_id, flow.session.slot, now=now)
    return state.with_roamer(roamer)


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

    roamer = await _roaming_here(content, session, locations, state, now, settings)
    state = state.with_roamer(roamer)

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


async def _claim_roamer(
    character: Character,
    descent: Descent,
    locations: LocationStateCache,
    parties: PartyStore,
) -> str:
    """Взять замок подземелья перед заходом. Пустая строка - можно идти.

    Одиночное подземелье не пускает того, кто ведёт отряд: спутники всё равно
    остались бы снаружи, а замок занялся бы без нужды. Групповое не пускает
    одиночку. Замок — ``SET NX`` в кэше локации: двое, нажавшие «Спуститься»
    разом, не окажутся внутри вместе (ADR 0037).
    """
    party = await parties.of(character.id)
    in_party = party is not None and len(party.members) >= 2
    if descent.group and not in_party:
        return "Это подземелье рассчитано на отряд. В одиночку туда не спускаются."
    if not descent.group and in_party:
        return "Подземелье для одного: с отрядом сюда не спускаются, ищите то, что на отряд."

    took = await locations.claim_roamer(
        descent.city_id, descent.slot, character.id, ttl=roamer_rules.ROAMER_HOLD_TTL
    )
    if not took:
        return "В подземелье только что спустились. Дождитесь, пока выйдут."
    return ""


async def _roaming_here(
    content: GameContent,
    session: LocationSession,
    locations: LocationStateCache,
    state: LocationState,
    now: int,
    settings: Settings,
) -> Roamer | None:
    """Блуждающее подземелье этой локации: либо уже есть, либо сейчас объявится.

    Появление детерминировано местом, поколением округи и окном (ADR 0037):
    первый, кто зашёл в это окно, и заводит подземелье, остальные видят то же.
    """
    existing = await locations.roamer(session.city_id, session.slot, now=now)
    if existing is not None:
        return existing
    epoch = node_rules.location_epoch(state)
    location = build_location(content, settings.world_seed, session, epoch=epoch)
    rolled = roamer_rules.roll_spawn(
        visit_seed(settings.world_seed, session),
        location,
        epoch=epoch,
        window=roamer_rules.window_of(now),
    )
    if rolled is None:
        return None
    return await locations.spawn_roamer(session.city_id, session.slot, rolled, ttl=LOCATION_TTL)


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
    seed = visit_seed(settings.world_seed, session)
    known = state
    if known is None:
        known = await locations.state(session.city_id, session.slot, now=now)
    location = build_location(
        content, settings.world_seed, session, epoch=node_rules.location_epoch(known)
    )
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
# Отряд лежит в общем хранилище, а автомат ничего не читает и не пишет, поэтому
# всё, что с отрядом делают, делается здесь: автомат приносит намерение
# (``flows/play._party_intent``), хендлер его исполняет и говорит, чем кончилось.
#
# Экран при этом не меняется: зов приходит туда, где позванный сейчас стоит, и
# согласиться он должен там же - одно действие, одно сообщение
# (``docs/accessibility.md``, правило 3).


async def _party_view(
    flow: PlayState,
    character: Character,
    characters: CharacterRepository,
    parties: PartyStore,
) -> party_screens.PartyView:
    """Что показать на экране отряда. Везде, кроме него, - пусто.

    Экраны передачи вещи общие для отряда и гильдии: состав отряда нужен им
    только когда передают из отряда (``flow.transfer_scope``).
    """
    on_party = flow.screen in (ScreenId.PARTY, ScreenId.PARTY_INVITE)
    on_transfer = flow.screen in _TRANSFER_SCREENS and flow.transfer_scope == "party"
    if not on_party and not on_transfer:
        return party_screens.PartyView()
    party = await parties.of(character.id)
    caller_id = await parties.called_by(character.id)
    caller = await characters.get(caller_id) if caller_id else None
    return party_screens.PartyView(
        members=await _party_names(party, characters) if party is not None else (),
        leader=party is not None and party.leader_id == character.id,
        caller=caller.name if caller is not None else "",
    )


async def _party_names(
    party: party_rules.Party, characters: CharacterRepository
) -> tuple[str, ...]:
    """Кто в отряде - в том порядке, в каком собрались. Первый собрал."""
    names: list[str] = []
    for member_id in party.members:
        one = await characters.get(member_id)
        if one is not None:
            names.append(one.name)
    return tuple(names)


async def _party_step(
    message: Message,
    character: Character,
    action: str,
    characters: CharacterRepository,
    parties: PartyStore,
) -> str:
    """Исполнить то, что игрок попросил сделать с отрядом. Ответ - целой фразой."""
    match action:
        case "create":
            if await parties.create(character.id) is None:
                return "Вы уже в отряде. Из него можно уйти или расформировать его."
            return "Отряд создан. Позовите в него того, с кем пойдёте: «Пригласить в отряд»."

        case "disband":
            party = await parties.of(character.id)
            if party is None:
                return "У вас нет отряда."
            if party.leader_id != character.id:
                return "Отряд собрали не вы. Из него можно уйти."
            await parties.disband(party)
            await _tell_party(
                message, characters, party.members, character.id, "Отряд расформирован."
            )
            return "Отряд расформирован."

        case "leave":
            party = await parties.of(character.id)
            if party is None:
                return "Вы и так идёте один."
            leading = party.leader_id == character.id
            await parties.leave(character.id)
            word = "Отряд расформирован." if leading else f"{character.name} ушёл из отряда."
            await _tell_party(message, characters, party.members, character.id, word)
            return "Отряд расформирован." if leading else "Вы вышли из отряда."

        case "accept":
            party = await parties.accept(character.id)
            if party is None:
                return "Вас сейчас никто не зовёт."
            names = await _party_names(party, characters)
            await _tell_party(
                message, characters, party.members, character.id, f"{character.name} идёт с вами."
            )
            return f"Вы в отряде: {', '.join(names)}. Бой у вас теперь общий."

        case "decline":
            await parties.forget_call(character.id)
            return "Зов отклонён."

        case _:  # pragma: no cover - других слов у отряда нет
            return ""


async def _tell_party(
    message: Message,
    characters: CharacterRepository,
    members: Sequence[int],
    except_id: int,
    text: str,
) -> None:
    """Сказать одну строку всем в отряде, кроме того, кто её вызвал."""
    for member_id in members:
        if member_id == except_id:
            continue
        other = await characters.get(member_id)
        if other is not None:
            await _tell(message, other.user_id, text)


async def _invite_by_name(
    message: Message,
    character: Character,
    name: str,
    characters: CharacterRepository,
    parties: PartyStore,
) -> str:
    """Позвать по набранному имени. Так зовут того, кто стоит не рядом."""
    target = await characters.find_by_name(name)
    if target is None:
        return f"Игрока с именем {name} в Велларе нет."
    if target.id == character.id:
        return "Так нельзя: зов самому себе."
    return await _invite(message, character, target, parties)


async def _invite(
    message: Message,
    character: Character,
    target: Character,
    parties: PartyStore,
) -> str:
    """Один зов - одна дорога: проверить, положить и сказать обоим.

    Дорог, которыми зовут, три - имя, кнопка на узле и ответ в игровой группе, -
    а правило одно, и живёт оно здесь (``domain/rules/party.invite_refusal``).
    """
    party = await parties.of(character.id)
    theirs = await parties.of(target.id)
    refused = party_rules.invite_refusal(
        inviter_level=character.level,
        invitee_name=target.name,
        invitee_level=target.level,
        party=party,
        invitee_in_party=theirs is not None,
    )
    if refused or party is None:
        return refused

    await parties.call(leader_id=party.leader_id, invitee_id=target.id)
    await _tell(
        message,
        target.user_id,
        f"{character.name}, уровень {character.level}, зовёт вас в отряд. "
        "Наберите «/отряд принять», чтобы пойти вместе, или «/отряд отказать».",
    )
    return f"Зов отправлен: {target.name}. Ответит — пойдёте вместе."


async def _call_to_party(
    message: Message,
    character: Character,
    target_id: int,
    company: Sequence[Presence],
    characters: CharacterRepository,
    parties: PartyStore,
) -> str:
    """Позвать соседа по узлу. Согласие даёт он сам, и другого пути нет."""
    target = await characters.get(target_id)
    if target is None or not any(person.character_id == target_id for person in company):
        return "Этого человека здесь больше нет."
    return await _invite(message, character, target, parties)


# --- гильдия (``domain/rules/guild.py``, ADR 0030) --------------------
# Гильдия лежит в базе, поэтому автомат её только просит - словом ``guild_action``
# на состоянии, - а делает всё хендлер: он двигает золото, зовёт и говорит, чем
# кончилось (``Claude.md``, правило 5).


_GUILD_SCREENS = frozenset(
    {
        ScreenId.GUILD,
        ScreenId.GUILD_FOUND,
        ScreenId.GUILD_INVITE,
        ScreenId.GUILD_ROSTER,
        ScreenId.GUILD_VAULT,
    }
)

#: Экраны адресной передачи. Общие для отряда и гильдии; чей это состав, говорит
#: ``PlayState.transfer_scope``.
_TRANSFER_SCREENS = frozenset(
    {ScreenId.TRANSFER_TO, ScreenId.TRANSFER_ITEM, ScreenId.TRANSFER_AMOUNT}
)


async def _guild_view(
    flow: PlayState,
    character: Character,
    characters: CharacterRepository,
    guilds: GuildStore,
) -> guild_screens.GuildView:
    """Что показать на экранах гильдии. Везде, кроме них, - пусто."""
    on_transfer = flow.screen in _TRANSFER_SCREENS and flow.transfer_scope == "guild"
    if flow.screen not in _GUILD_SCREENS and not on_transfer:
        return guild_screens.GuildView()
    guild = await guilds.of(character.id)
    caller = ""
    called_to = await guilds.called_to(character.id)
    if called_to:
        from_guild = await guilds.by_id(called_to)
        caller = from_guild.name if from_guild is not None else ""
    if guild is None:
        return guild_screens.GuildView(my_gold=character.gold, caller=caller)
    members: list[tuple[str, guild_rules.GuildRank]] = []
    for one in guild.members:
        who = await characters.get(one.character_id)
        if who is not None:
            members.append((who.name, one.rank))
    return guild_screens.GuildView(
        name=guild.name,
        my_rank=guild.rank_of(character.id),
        members=tuple(members),
        vault_gold=guild.vault_gold,
        my_gold=character.gold,
        caller=caller,
    )


async def _guild_step(
    message: Message,
    character: Character,
    action: str,
    arg: str,
    characters: CharacterRepository,
    guilds: GuildStore,
) -> tuple[str, Character]:
    """Исполнить то, что игрок попросил сделать с гильдией. Ответ - целой фразой.

    Возвращает и персонажа: грамота, вклад и выемка двигают его золото, и
    сохранены они уже здесь.
    """
    guild = await guilds.of(character.id)

    async def fresh() -> Character:
        return await characters.get(character.id) or character

    match action:
        case "found":
            refusal = guild_rules.found_refusal(
                level=character.level,
                gold=character.gold,
                in_guild=guild is not None,
                name_taken=await guilds.by_name(arg) is not None,
                name=arg,
            )
            if refusal:
                return refusal, character
            if not await characters.spend_gold(character.id, guild_rules.FOUND_COST):
                return "Золота на грамоту не хватило.", await fresh()
            economy_log.record(
                economy_log.SERVICE, -guild_rules.FOUND_COST, character_id=character.id
            )
            made = await guilds.create(arg.strip(), character.id)
            return f"Гильдия «{made.name}» основана. Вы её основатель.", await fresh()

        case "invite":
            if guild is None:
                return "У вас нет гильдии.", character
            target = await characters.find_by_name(arg)
            if target is None:
                return f"Игрока с именем {arg} в Велларе нет.", character
            if target.id == character.id:
                return "Так нельзя: зов самому себе.", character
            refusal = guild_rules.invite_refusal(
                guild=guild,
                inviter_id=character.id,
                invitee_name=target.name,
                invitee_in_guild=await guilds.of(target.id) is not None,
            )
            if refusal:
                return refusal, character
            await guilds.call(guild_id=guild.id, invitee_id=target.id)
            await _tell(
                message,
                target.user_id,
                f"Гильдия «{guild.name}» зовёт вас к себе. Наберите «/гильдия принять», "
                "чтобы вступить, или «/гильдия отклонить».",
            )
            return f"Зов отправлен: {target.name}.", character

        case "accept":
            joined = await guilds.accept(character.id)
            if joined is None:
                return "Вас сейчас никакая гильдия не зовёт.", character
            return f"Вы в гильдии «{joined.name}».", character

        case "decline":
            await guilds.forget_call(character.id)
            return "Зов отклонён.", character

        case "leave":
            if guild is None:
                return "Вы не в гильдии.", character
            if guild.founder_id == character.id:
                return "Вы основатель: гильдию можно только распустить.", character
            await guilds.leave(character.id)
            await _tell_party(
                message,
                characters,
                [one.character_id for one in guild.members],
                character.id,
                f"{character.name} вышел из гильдии «{guild.name}».",
            )
            return "Вы вышли из гильдии.", character

        case "disband":
            if guild is None:
                return "У вас нет гильдии.", character
            if guild.founder_id != character.id:
                return "Гильдию основали не вы. Из неё можно выйти.", character
            if guild.vault_gold:
                await characters.grant_gold(character.id, guild.vault_gold)
                economy_log.record(
                    economy_log.GUILD_VAULT, guild.vault_gold, character_id=character.id
                )
            await guilds.disband(guild)
            await _tell_party(
                message,
                characters,
                [one.character_id for one in guild.members],
                character.id,
                f"Гильдия «{guild.name}» распущена.",
            )
            return "Гильдия распущена. Казна вернулась к вам.", await fresh()

        case "promote" | "demote" | "kick":
            return await _guild_rank_step(
                message, character, action, arg, characters, guilds, guild
            )

        case "deposit" | "withdraw":
            return await _guild_vault_step(character, action, arg, characters, guilds, guild)

    return "Не понял, что сделать с гильдией.", character  # pragma: no cover


async def _guild_rank_step(
    message: Message,
    character: Character,
    action: str,
    name: str,
    characters: CharacterRepository,
    guilds: GuildStore,
    guild: guild_rules.Guild | None,
) -> tuple[str, Character]:
    if guild is None:
        return "У вас нет гильдии.", character
    target = await characters.find_by_name(name)
    if target is None or guild.rank_of(target.id) is None:
        return f"{name} — не в вашей гильдии.", character
    if action == "kick":
        refusal = guild_rules.kick_refusal(guild=guild, actor_id=character.id, target_id=target.id)
        if refusal:
            return refusal, character
        await guilds.save(guild.without(target.id))
        await _tell(message, target.user_id, f"Вас исключили из гильдии «{guild.name}».")
        return f"{target.name} исключён из гильдии.", character
    to = guild_rules.GuildRank.OFFICER if action == "promote" else guild_rules.GuildRank.MEMBER
    refusal = guild_rules.rank_change_refusal(
        guild=guild, actor_id=character.id, target_id=target.id, to=to
    )
    if refusal:
        return refusal, character
    await guilds.save(guild.with_rank(target.id, to))
    await _tell(
        message, target.user_id, f"В гильдии «{guild.name}» ваше звание теперь: {to.title}."
    )
    return f"{target.name} теперь {to.title}.", character


async def _guild_vault_step(
    character: Character,
    action: str,
    arg: str,
    characters: CharacterRepository,
    guilds: GuildStore,
    guild: guild_rules.Guild | None,
) -> tuple[str, Character]:
    if guild is None:
        return "У вас нет гильдии.", character
    amount = int(arg) if arg.isdigit() else 0
    if amount <= 0:
        return "Назовите сумму.", character

    async def fresh() -> Character:
        return await characters.get(character.id) or character

    if action == "deposit":
        if not await characters.spend_gold(character.id, amount):
            return f"На руках только {character.gold}.", await fresh()
        await guilds.deposit(guild, amount)
        economy_log.record(economy_log.GUILD_VAULT, -amount, character_id=character.id)
        return f"В казну внесено {amount}.", await fresh()

    refusal = guild_rules.withdraw_refusal(guild=guild, actor_id=character.id, amount=amount)
    if refusal:
        return refusal, character
    if not await guilds.withdraw(guild, amount):
        return "В казне столько не набралось.", character
    await characters.grant_gold(character.id, amount)
    economy_log.record(economy_log.GUILD_VAULT, amount, character_id=character.id)
    return f"Из казны взято {amount}.", await fresh()


# --- передача вещи в отряде и гильдии --------------------------------
#
# Адресный подарок соратнику или соклановцу, минуя игровую группу. Мгновенный и
# без подтверждения получателя — как `передать` в группе (``Narrative.md``,
# раздел 9). Эскроу и пошлины нет: передача из рук в руки ими не облагается, и
# ``gold_flow`` тут ничего не пишет.
#
# Проверки — состав, чёрный список, занятость боем — оркестровка, требующая
# хранилищ, поэтому живут здесь, рядом с ``_guild_step`` (``Claude.md``,
# правило 5).


async def _fellow_ids(
    scope: str,
    character: Character,
    parties: PartyStore,
    guilds: GuildStore,
) -> tuple[list[int], str] | None:
    """Кто с игроком в одном объединении и как оно зовётся. ``None`` — он не в нём."""
    if scope == "guild":
        guild = await guilds.of(character.id)
        if guild is None:
            return None
        return [one.character_id for one in guild.members], "гильдии"
    party = await parties.of(character.id)
    if party is None:
        return None
    return list(party.members), "отряде"


async def _transfer_step(
    message: Message,
    content: GameContent,
    character: Character,
    scope: str,
    to_name: str,
    item_id: str,
    amount: int,
    characters: CharacterRepository,
    inventory: InventoryRepository,
    parties: PartyStore,
    guilds: GuildStore,
    privacy: PrivacyRepository | None,
    state_cache: StateCache,
) -> str:
    """Передать вещь соратнику или соклановцу. Ответ — целой фразой.

    ``privacy`` в бою приходит из посредника зависимостей и всегда есть; ``None``
    бывает только в тестах, не трогающих передачу, — как и у ``user`` в ``play``.
    """
    if amount <= 0 or not to_name or not item_id or not content.has_item(item_id):
        return "Передача не сложилась. Начните заново."

    if await BattleStore(state_cache).busy(character.id) is not None:
        return "Сейчас вы в бою. Передать вещь можно после него."

    fellows = await _fellow_ids(scope, character, parties, guilds)
    if fellows is None:
        return "Вы уже не в отряде." if scope == "party" else "Вы уже не в гильдии."
    member_ids, where = fellows

    recipient: Character | None = None
    for member_id in member_ids:
        if member_id == character.id:
            continue
        who = await characters.get(member_id)
        if who is not None and who.name == to_name:
            recipient = who
            break
    if recipient is None:
        return f"{to_name} — не в вашей {where}."

    # Чёрный список закрывает пару в обе стороны — так же, как в группе
    # (``Narrative.md``, раздел 9).
    if privacy is not None and (
        await privacy.blocks(recipient.user_id, character.user_id)
        or await privacy.blocks(character.user_id, recipient.user_id)
    ):
        return f"С {to_name} у вас закрыты дела: передача не проходит."

    name = content.item(item_id).name
    held = await inventory.count(character.id, item_id)
    if held < amount:
        return f"{name}: у вас {held} из {amount}, столько не передать."
    if not await inventory.remove(character.id, item_id, amount):
        return "Вещь не удалось забрать из сумки. Попробуйте заново."
    await inventory.add(recipient.id, item_id, amount)

    await _tell(
        message,
        recipient.user_id,
        f"{character.name} передал вам: {name}, штук {amount}.",
    )
    return f"{name}, штук {amount} — передано игроку {to_name}."
