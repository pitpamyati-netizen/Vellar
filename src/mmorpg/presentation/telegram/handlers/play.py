"""Хендлеры меню, мира, города, служб и хождения по локации.

Тонкие по замыслу: прочитать состояние, позвать чистую ветку, сохранить то,
что ветка решила, нарисовать, отправить одно сообщение. Часы живут здесь:
ветка получает момент, переворот лавки и остаток в узлах значениями, и это
делает сборку воспроизводимой (``docs/procgen.md``).

Общее состояние локации живёт тоже здесь: только это место читает, кто где
стоит, и вынимает из узла то, что вынул шаг. Ветка не пишет никогда - всё,
что она решила изменить, приходит в ``PlayState.pending`` и применяется здесь,
в одном месте.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import replace
from uuid import uuid4

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from mmorpg import economy_log
from mmorpg.application.operations import MissingResourceError, atomic_action, current_operation
from mmorpg.application.services import group_trade, keeper_panel, moderation
from mmorpg.application.services.battle import BattleStore
from mmorpg.application.services.city_event import CityEvents
from mmorpg.application.services.content import ContentRegistry
from mmorpg.application.services.guild import GuildStore
from mmorpg.application.services.guild_safety import disband_token, dissolve
from mmorpg.application.services.keeper import set_keeper, sync_keeper
from mmorpg.application.services.long_goal import LongGoals
from mmorpg.application.services.market import Market
from mmorpg.application.services.party import PartyStore
from mmorpg.application.services.recruitment import Recruitment
from mmorpg.application.services.war_score import WarScoring
from mmorpg.config import Settings
from mmorpg.domain.entities.character import Character, InventoryEntry
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.location import Engagement, LocationState, Presence, Roamer
from mmorpg.domain.entities.moderation import Ban, KeeperAction, KeeperEntry
from mmorpg.domain.entities.overlay import OverlayKind
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.entities.trade import TradeRecord
from mmorpg.domain.ports.repositories import (
    CharacterRepository,
    ContentOverlayRepository,
    GoldFlowRepository,
    GoldFlowSlice,
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
from mmorpg.domain.rules import digest as digest_rules
from mmorpg.domain.rules import economy as economy_rules
from mmorpg.domain.rules import guild as guild_rules
from mmorpg.domain.rules import guild_contract as contract_rules
from mmorpg.domain.rules import guild_war as war_rules
from mmorpg.domain.rules import moderation as moderation_rules
from mmorpg.domain.rules import mood as mood_rules
from mmorpg.domain.rules import nodes as node_rules
from mmorpg.domain.rules import party as party_rules
from mmorpg.domain.rules import roamer as roamer_rules
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules import tutorial as tutorial_rules
from mmorpg.domain.rules.economy import buy_price, roll_assortment
from mmorpg.domain.rules.modifiers import collect_modifiers
from mmorpg.domain.rules.stats import derived_stats, primary_stats
from mmorpg.logging import get_logger
from mmorpg.presentation.telegram import broadcast, digest_claim
from mmorpg.presentation.telegram.broadcast import ChannelBroadcaster
from mmorpg.presentation.telegram.flows import keeper as keeper_flow
from mmorpg.presentation.telegram.flows.play import (
    advance,
    begin,
    build_location,
    known_city,
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
from mmorpg.presentation.telegram.handlers import city_event as city_event_handler
from mmorpg.presentation.telegram.handlers import long_goal as goal_handler
from mmorpg.presentation.telegram.handlers import market as market_handler
from mmorpg.presentation.telegram.handlers import recruitment as recruitment_handler
from mmorpg.presentation.telegram.handlers.combat import ENGAGED_TTL, open_fight
from mmorpg.presentation.telegram.handlers.combat import _party_of as fight_companions
from mmorpg.presentation.telegram.handlers.combat import _show as show_battle
from mmorpg.presentation.telegram.handlers.creation import welcome_screen
from mmorpg.presentation.telegram.messaging import send_screen, send_text
from mmorpg.presentation.telegram.routing import Intent, parse_command
from mmorpg.presentation.telegram.screens import city as city_screens
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

# То, что локация помнит о своих узлах, живёт неделю: забыть это - то же, что
# «всё в ней давно наполнилось заново». Присутствие живёт куда меньше - игрок,
# не нажимавший ничего десять минут, ушёл.
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


@atomic_action
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
    gold_flow: GoldFlowRepository | None = None,
    broadcasts: ChannelBroadcaster | None = None,
) -> None:
    if message.from_user is None or message.text is None:
        return

    character = await characters.get_active(message.from_user.id)
    if character is None:
        await state.clear()
        await send_screen(message, welcome_screen())
        return
    battle_store = BattleStore(state_cache)
    if battle_id := await battle_store.busy(character.id):
        session = await battle_store.load(battle_id)
        assert session is not None
        await show_battle(
            message, state, await battle_store.content(session, content), character, session
        )
        return
    # Аккаунт обычно уже прочитан на входе (``middlewares/moderation.py``). Здесь
    # он читается только там, где хендлер вызывают без той двери, - в тестах.
    if user is None:
        user = await users.get(message.from_user.id)
    character = await sync_keeper(
        character,
        message.from_user.id,
        settings,
        characters,
        granted=user is not None and user.keeper,
    )
    # Умение, которого в игре больше нет, забывается и возвращает очки: иначе у
    # игрока остался бы пустой слот и нечем его занять
    # (``domain/rules/skills.reclaim_lost``).
    reclaimed = skill_rules.reclaim_lost(content, character)
    if reclaimed is not None:
        character = reclaimed
        character = await characters.save(character)
        logger.info("skills_reclaimed", character_id=character.id)
    emoji = user.settings.emoji if user is not None else False
    accessibility = user.settings if user is not None else None

    data = await state.get_data()
    flow = PlayState.deserialise(data[STATE_KEY]) if data.get(STATE_KEY) else begin(character)
    # Смотритель мог попросить сбросить этого игрока на главный экран (ADR 0045):
    # флаг живёт в кэше, снимается здесь и роняет сохранённый экран.
    reset_key = keeper_panel.player_reset_key(message.from_user.id)
    if await state_cache.get(reset_key) is not None:
        await state_cache.delete(reset_key)
        flow = begin(character)

    now = int(time.time())
    city_events = CityEvents(content, state_cache, characters, inventory)
    command_text = message.text
    long_goals = LongGoals(content, state_cache, characters, inventory, guilds)
    if goal_handler.requested(command_text, flow):
        operation = current_operation()
        assert operation is not None
        flow, goal_screen = await goal_handler.step(
            command_text, flow, character, long_goals, receipt=operation.id
        )
        if goal_screen is not None:
            await state.set_state(STATE_FOR_SCREEN[flow.screen])
            await state.update_data({STATE_KEY: flow.serialise()})
            await send_screen(message, goal_screen, emoji=emoji)
            return
        command = parse_command(command_text)
        if command is None or command.intent in {Intent.UNKNOWN, Intent.BACK, Intent.MAIN_MENU}:
            command_text = "/осмотреться"
    if market_handler.requested(command_text, flow):
        flow, market_screen = await market_handler.step(
            command_text,
            flow,
            character,
            Market(content, state_cache, characters, inventory),
            now=now,
        )
        if market_screen is not None:
            await state.set_state(STATE_FOR_SCREEN[flow.screen])
            await state.update_data({STATE_KEY: flow.serialise()})
            await send_screen(message, market_screen, emoji=emoji)
            return
        command = parse_command(command_text)
        if command is None or command.intent in {Intent.BACK, Intent.MAIN_MENU}:
            command_text = "/осмотреться"
    if city_event_handler.requested(command_text, flow):
        operation = current_operation()
        assert operation is not None
        flow, event_screen = await city_event_handler.step(
            command_text, flow, character, city_events, receipt=operation.id
        )
        if event_screen is not None:
            await state.set_state(STATE_FOR_SCREEN[flow.screen])
            await state.update_data({STATE_KEY: flow.serialise()})
            await send_screen(message, event_screen, emoji=emoji)
            return
        command = parse_command(command_text)
        if command is None or command.intent in {Intent.BACK, Intent.MAIN_MENU}:
            command_text = "/осмотреться"
    invitation_notice = ""
    invite_actions = {
        "/отряд повторить": ("party", "repeat"),
        "Повторить зов в отряд": ("party", "repeat"),
        "/отряд блокировать": ("party", "block"),
        "Блокировать зов в отряд": ("party", "block"),
        "/гильдия повторить": ("guild", "repeat"),
        "Повторить зов в гильдию": ("guild", "repeat"),
        "/гильдия блокировать": ("guild", "block"),
        "Блокировать зов в гильдию": ("guild", "block"),
    }
    if action := invite_actions.get(command_text.strip()):
        kind, purpose = action
        store = parties.invitations if kind == "party" else guilds.invitations
        call = await store.get(character.id)
        if purpose == "block" and call and call.inviter_id:
            await store.block(character.id, call.inviter_id)
            invitation_notice = "Зов заблокирован. Снять блокировку: /набор разрешить Имя."
        elif purpose == "repeat" and call:
            invited_party = await parties.by_leader(call.group_id) if kind == "party" else None
            guild = await guilds.by_id(call.group_id) if kind == "guild" else None
            valid = bool(
                invited_party and not invited_party.full and await parties.of(character.id) is None
            )
            if valid and invited_party:
                leader = await characters.get(invited_party.leader_id)
                stamp = (
                    await state_cache.get(f"social:party-generation:{invited_party.leader_id}")
                    or ""
                )
                valid = bool(
                    leader
                    and abs(leader.level - character.level) <= party_rules.LEVEL_WINDOW
                    and call.group_stamp == stamp
                    and invited_party.has(call.inviter_id)
                )
            if guild:
                valid = (
                    await guilds.of(character.id) is None
                    and guild.size < guild_rules.standing(content, guild).seats
                    and (not call.inviter_id or guild.can_invite(call.inviter_id))
                )
            renewed = valid and await store.repeat(character.id, now=now)
            invitation_notice = (
                "Приглашение возобновлено на сутки. Теперь можно согласиться."
                if renewed
                else (
                    "Повтор недоступен: проверьте место, состав и блокировку. "
                    "Можно выбрать другой отряд."
                )
            )
        else:
            invitation_notice = (
                "Сохранённого приглашения нет. Попросите новый зов или откройте поиск отряда."
            )
        command_text = "/отряд" if kind == "party" else "/гильдия"
    if recruitment_handler.requested(command_text, flow):
        flow, recruitment_screen = await recruitment_handler.step(
            command_text, flow, character, content, Recruitment(parties, characters), now=now
        )
        if recruitment_screen is not None:
            await state.set_state(STATE_FOR_SCREEN[flow.screen])
            await state.update_data({STATE_KEY: flow.serialise()})
            await send_screen(message, recruitment_screen, emoji=emoji)
            return
        if command_text.startswith("/набор") or command_text in {"/назад", "/back", "Назад"}:
            command_text = "/осмотреться"
    clock = Clock(
        now=now,
        shop_rotation=rotation_index(now, settings.shop_rotation_seconds),
    )
    goods = await _goods(
        content, character, flow, inventory, locations, settings, clock.shop_rotation, now
    )
    company = await _company(flow, character, locations, now)
    here = await _location_state(content, flow, locations, now)
    held = await _fights(flow, locations, content, settings, here, now)
    view = await _keeper_view(
        flow,
        character,
        command_text,
        characters,
        users,
        keeper_log,
        trades,
        registry,
        now,
        settings,
        inventory,
        parties,
        guilds,
        gold_flow,
        state_cache,
    )

    party = await _party_view(flow, character, characters, parties)
    # Часов в игре нет: войну с вышедшим сроком закрывает тот, кто первым на неё
    # заглянул, и делает это до того, как экран нарисован (ADR 0077).
    if flow.screen in _GUILD_SCREENS:
        await _settle_guild_war(message, content, character, characters, guilds, settings, now)
    guild_view = await _guild_view(flow, character, characters, guilds, content, settings, now)

    updated = advance(
        content,
        character,
        flow,
        command_text,
        world_seed=settings.world_seed,
        clock=clock,
        goods=goods,
        settings=accessibility,
        neighbours=company,
        fights=held,
        keeper=view,
        party=party,
        guild=guild_view,
        location_state=here,
        travel_discount=await city_events.travel_discount(character.city_id),
    )
    if invitation_notice:
        updated = updated.with_notice(invitation_notice)
    if updated.screen is ScreenId.CITY_EVENT:
        operation = current_operation()
        assert operation is not None
        updated, event_screen = await city_event_handler.step(
            "/событие", updated, character, city_events, receipt=operation.id
        )
        assert event_screen is not None
        await state.set_state(STATE_FOR_SCREEN[updated.screen])
        await state.update_data({STATE_KEY: updated.serialise()})
        await send_screen(message, event_screen, emoji=emoji)
        return
    if updated.screen in recruitment_handler.SCREENS:
        updated, screen = await recruitment_handler.show(
            updated, character, Recruitment(parties, characters).with_content(content), now=now
        )
        await state.set_state(STATE_FOR_SCREEN[updated.screen])
        await state.update_data({STATE_KEY: updated.serialise()})
        await send_screen(message, screen, emoji=emoji)
        return
    # Ответ со старого экрана не принимает приглашение от другого собравшего.
    if updated.party_action in {"accept", "decline"}:
        call = await parties.invitations.get(character.id)
        if call and flow.party_invitation and call.identity != flow.party_invitation:
            updated = replace(updated.at(ScreenId.PARTY), party_action="").with_notice(
                "Прежнее приглашение сменилось. Проверьте, кто зовёт, и выберите действие заново."
            )
    if updated.guild_action in {"accept", "decline"}:
        call = await guilds.invitations.get(character.id)
        if call and flow.guild_invitation and call.identity != flow.guild_invitation:
            updated = replace(updated.at(ScreenId.GUILD), guild_action="").with_notice(
                "Прежнее приглашение сменилось. Проверьте гильдию и выберите действие заново."
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
            message,
            content,
            character,
            updated.guild_action,
            updated.guild_arg,
            characters,
            guilds,
            settings,
            now,
            inventory=inventory,
        )
        updated = replace(updated, guild_action="", guild_arg="").with_notice(said)
    if updated.vault_amount:
        # Хранилище лежит в базе, поэтому автомат только выбрал вещь и число, а
        # двигает общее добро хендлер (``Claude.md``, правило 5).
        said = await _guild_store_step(
            content, character, updated, inventory, guilds, settings, now
        )
        updated = replace(updated, vault_amount=0).with_notice(said)
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
    before_city = character.city_id
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
        parties=parties,
        guilds=guilds,
        state_cache=state_cache,
        locations=locations,
        broadcasts=broadcasts,
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
    # Обоз со сводки: приход в названный заставой город закрывает дело ``HAUL`` и
    # платит надбавку — раз за переворот (ADR 0053).
    character, updated = await _pay_digest_haul(
        content, character, before_city, updated, characters, state_cache, now, settings
    )
    # Обыск со сводки: отработанный узел названного вида в названной локации
    # закрывает дело ``SEARCH`` и платит надбавку — раз за переворот (ADR 0054).
    character, updated = await _pay_digest_search(
        content, character, flow, updated, characters, locations, state_cache, now, settings
    )
    updated, here = await sync_location(content, updated, flow, character, locations, now, settings)
    if updated.pending.node_take >= 0 and updated.pending.node_kind in {"cache", "shrine", "event"}:
        operation = current_operation()
        assert operation is not None
        notice = await city_events.record(
            character.id, updated.session.city_id, updated.session.slot, "scout", operation.id
        )
        if notice:
            updated = updated.with_notice(f"{updated.notice} {notice}".strip())

    # Спуск в блуждающее подземелье: замок берут здесь, до боя, - подземелье
    # общее, а ветка ничего не читает и не пишет (ADR 0037).
    if updated.fight == "dungeon" and updated.descent.roamer and updated.descent.layer == 0:
        if not updated.descent.encounter_id:
            updated = replace(updated, descent=replace(updated.descent, encounter_id=uuid4().hex))
        blocked = await _claim_roamer(
            character, updated.descent, locations, parties, characters=characters, content=content
        )
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
            locations=locations,
            now=now,
        )
        return

    # Экран, на котором кончился шаг, - не тот, с которого он начался, и сумка по
    # дороге могла измениться, поэтому её читают заново.
    shelf = await _goods(
        content, character, updated, inventory, locations, settings, clock.shop_rotation, now
    )
    company = await _company(updated, character, locations, now)
    engaged = await _fights(updated, locations, content, settings, here, now)
    shown = await _keeper_view(
        updated,
        character,
        "",
        characters,
        users,
        keeper_log,
        trades,
        registry,
        now,
        settings,
        inventory,
        parties,
        guilds,
        gold_flow,
        state_cache,
    )
    gathered = await _party_view(updated, character, characters, parties)
    guild_view = await _guild_view(updated, character, characters, guilds, content, settings, now)
    if gathered.caller and (call := await parties.invitations.get(character.id)):
        updated = replace(updated, party_invitation=call.identity)
    if guild_view.caller and (call := await guilds.invitations.get(character.id)):
        updated = replace(updated, guild_invitation=call.identity)
    if updated.screen is ScreenId.GUILD_DISBAND and not updated.guild_confirmation:
        updated = replace(updated, guild_confirmation=guild_view.disband_token)
    briefing = await _digest_view(
        updated, character, content, locations, state_cache, now, settings
    )
    await state.set_state(STATE_FOR_SCREEN[updated.screen])
    await state.update_data({STATE_KEY: updated.serialise()})
    story = await long_goals.story()
    story_summary = (
        story.outcome.text if story and character.city_id == content.long_goals.city_id else ""
    )
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
        fights=engaged,
        keeper=shown,
        party=gathered,
        guild=guild_view,
        location_state=here,
        digest_view=briefing,
        event_summary="\n".join(
            part for part in (await city_events.summary(), story_summary) if part
        ),
        travel_discount=await city_events.travel_discount(character.city_id),
    )
    # Уровень объявляется вторым сообщением, и это единственное место в игре, где
    # одно действие отвечает дважды (``screens/play.level_up_report``).
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
    fights: Sequence[Engagement] = (),
    keeper: KeeperView | None = None,
    party: party_screens.PartyView | None = None,
    guild: guild_screens.GuildView | None = None,
    location_state: LocationState | None = None,
    digest_view: city_screens.DigestView | None = None,
    event_summary: str = "",
    travel_discount: int = 0,
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
        fights=fights,
        keeper=keeper,
        party=party,
        guild=guild,
        location_state=location_state,
        digest_view=digest_view,
        travel_discount=travel_discount,
    )
    if event_summary and screen.id in {ScreenId.CITY, ScreenId.SUMMARY}:
        screen = replace(screen, lines=(*screen.lines, event_summary))
    await send_screen(message, screen, emoji=emoji)
    return screen


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


async def _fights(
    flow: PlayState,
    locations: LocationStateCache,
    content: GameContent,
    settings: Settings,
    here: LocationState,
    now: int,
) -> tuple[Engagement, ...]:
    """За какие стаи этого узла уже дерутся (ADR 0065). Вне локации - ни за какие."""
    if flow.screen is not ScreenId.LOCATION or not flow.session.active:
        return ()
    if not location_known(content, flow.session):
        return ()
    location = build_location(
        content, settings.world_seed, flow.session, epoch=node_rules.location_epoch(here)
    )
    left = node_rules.standing_at(
        visit_seed(settings.world_seed, flow.session), location, here, flow.session.node, now
    )
    return await locations.engaged_at(
        flow.session.city_id,
        flow.session.slot,
        flow.session.node,
        wave=left.wave,
        now=now,
        ttl=ENGAGED_TTL,
        epoch=node_rules.location_epoch(here),
    )


async def _goods(
    content: GameContent,
    character: Character,
    flow: PlayState,
    inventory: InventoryRepository,
    locations: LocationStateCache,
    settings: Settings,
    rotation: int,
    now: int,
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
    # Прилавок нужен обоим экранам лавки: карточка товара называет цену и берёт
    # её же с кошелька, и без прилавка она называла бы цену эталона - без
    # редкости, без нужды города и без харизмы. Список говорил 511, карточка
    # брала 222.
    if flow.screen not in {ScreenId.SHOP, ScreenId.SHOP_ITEM}:
        return Goods(gold=character.gold, owned=owned)

    city_id = flow.city_id or character.city_id
    # «Нужда» города по состоянию округи вокруг: выбитая и встревоженная земля
    # кормит город хуже, лавка дорожает и сужается (ADR 0055).
    moods = await digest_claim.city_moods(locations, content, city_id, now=now)
    strain = mood_rules.city_strain(moods.values())

    bundle = collect_modifiers(content, character)
    stock = roll_assortment(
        content,
        world_seed=settings.world_seed,
        city_id=city_id,
        rotation=rotation,
        character_level=character.level,
        reputation=bundle.get(economy_rules.REPUTATION_KEY, 0.0),
        strain=strain,
    )
    charisma = primary_stats(content, character)[StatCode.CHA]
    prices = {
        item.id: buy_price(content, item, modifiers=bundle, charisma=charisma, strain=strain)
        for item in stock
    }
    return Goods(gold=character.gold, owned=owned, stock=stock, prices=prices)


async def _digest_view(
    flow: PlayState,
    character: Character,
    content: GameContent,
    locations: LocationStateCache,
    state_cache: StateCache,
    now: int,
    settings: Settings,
) -> city_screens.DigestView | None:
    """Что о сводке знает не домен: бралась ли надбавка и осел ли где роамер.

    Считается только для экрана «Сводка» — на всех прочих это лишние обращения
    к кэшу (ADR 0053).
    """
    if flow.screen is not ScreenId.SUMMARY:
        return None
    city = known_city(content, flow.city_id, character.city_id)
    claimed = await digest_claim.already_claimed(
        state_cache,
        character.id,
        now=now,
        rotation_seconds=settings.shop_rotation_seconds,
    )
    place = ""
    moods: dict[int, mood_rules.LocationMood] = {}
    for location in city.locations:
        state = await locations.state(city.id, location.slot, now=now)
        roamer = await locations.roamer(city.id, location.slot, now=now)
        moods[location.slot] = mood_rules.mood_of(state.with_roamer(roamer))
        if roamer is not None and not place:
            place = location.name
    return city_screens.DigestView(claimed=claimed, roamer_place=place, moods=moods)


async def _named(characters: CharacterRepository, character_id: int) -> str:
    """Имя персонажа по id, или само число, если персонажа уже нет."""
    one = await characters.get(character_id)
    return one.name if one is not None else str(character_id)


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
    inventory: InventoryRepository | None = None,
    parties: PartyStore | None = None,
    guilds: GuildStore | None = None,
    gold_flow: GoldFlowRepository | None = None,
    state_cache: StateCache | None = None,
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
    log_total = 0
    log_target = ""

    if flow.screen is ScreenId.KEEPER_LOG:
        if flow.keeper_target:
            whose = await characters.get(flow.keeper_target)
            if whose is not None:
                log_target = whose.name
        log_total = await keeper_log.count(target=log_target)
        # Страницу берём уже подрезанной под журнал: экран её тоже подрежет, но
        # тогда бы он показывал не ту порцию, что запросили из хранилища.
        pages = max(1, -(-log_total // keeper_screens.LOG_SHOWN))
        page = min(max(1, flow.keeper_page.page), pages)
        log = await keeper_log.latest(
            limit=keeper_screens.LOG_SHOWN,
            offset=(page - 1) * keeper_screens.LOG_SHOWN,
            target=log_target,
        )
    maintenance = ""
    if flow.screen is ScreenId.KEEPER_OPS and state_cache is not None:
        maintenance = await state_cache.get(keeper_panel.MAINTENANCE_KEY) or ""

    if flow.screen is ScreenId.KEEPER_PLAYERS:
        criteria = flow.keeper_player_filter
        if criteria.any:
            # «Заходил за сутки» приходит от автомата как 1: границу ставит здесь
            # тот, у кого есть часы. Заблокированность и гильдию досовмещаем сами
            # (``PlayerFilter``): у поиска по игрокам этих таблиц нет.
            resolved = replace(criteria, active_since=(now - DAY) if criteria.active_since else 0)
            picked = await characters.search(resolved, limit=PLAYERS_SHOWN)
            if criteria.banned:
                banned = await users.banned_ids(now=now)
                picked = tuple(who for who in picked if who.user_id in banned)
            if criteria.guild and guilds is not None:
                needle = criteria.guild.casefold()
                in_guild: list[Character] = []
                for who in picked:
                    guild = await guilds.of(who.id)
                    if guild is not None and needle in guild.name.casefold():
                        in_guild.append(who)
                picked = tuple(in_guild)
            players = picked
        else:
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
            ScreenId.KEEPER_MUTE,
            ScreenId.KEEPER_GOLD_FLOW,
            ScreenId.KEEPER_TRADES,
            ScreenId.KEEPER_TUNE,
            ScreenId.KEEPER_AMOUNT,
            ScreenId.KEEPER_GIVE,
            ScreenId.KEEPER_GIVE_GEAR,
            ScreenId.KEEPER_GIVE_ITEM,
            ScreenId.KEEPER_SKILLS,
            ScreenId.KEEPER_SKILL,
            ScreenId.KEEPER_SKILL_LEARN,
            ScreenId.KEEPER_SKILL_SLOT,
            ScreenId.KEEPER_STATS_EDIT,
            ScreenId.KEEPER_QUESTS,
            ScreenId.KEEPER_QUEST,
            ScreenId.KEEPER_BAG,
            ScreenId.KEEPER_PARTY,
            ScreenId.KEEPER_GUILD,
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
    # персонажей у него может быть несколько, а право одно.
    target_keeper = False
    target_locked = False
    target_ban = Ban()
    target_mute = Ban()
    target_warnings = 0
    if target is not None:
        account = await users.get(target.user_id)
        # Блокировку, мьют и предупреждения читают всегда, когда открыт чужой
        # персонаж: они стоят на карточке строкой, а раздача права - только у
        # того, кто его раздаёт.
        if account is not None:
            target_warnings = account.warnings
            if moderation_rules.is_banned(account.ban, now=now):
                target_ban = account.ban
            if moderation_rules.is_muted(account.mute, now=now):
                target_mute = account.mute
        if granting:
            target_locked = settings.is_admin(target.user_id)
            target_keeper = target_locked or (account is not None and account.keeper)

    journal: tuple[TradeRecord, ...] = ()
    if flow.screen is ScreenId.KEEPER_TRADES and target is not None:
        journal = await trades.journal(target.id, limit=keeper_screens.TRADES_SHOWN)

    bag: tuple[InventoryEntry, ...] = ()
    if flow.screen is ScreenId.KEEPER_BAG and target is not None and inventory is not None:
        bag = await inventory.list_items(target.id)

    gold_flow_slice = GoldFlowSlice()
    if flow.screen is ScreenId.KEEPER_GOLD_FLOW and target is not None and gold_flow is not None:
        gold_flow_slice = await gold_flow.slice(target.id)

    # Отряд и гильдия открытого игрока: на карточке от них зависит, рисовать ли
    # кнопку, а на самих экранах нужен и состав. Имена резолвятся здесь - домену
    # их взять неоткуда.
    group_screens = {ScreenId.KEEPER_PLAYER, ScreenId.KEEPER_PARTY, ScreenId.KEEPER_GUILD}
    named = flow.screen in {ScreenId.KEEPER_PARTY, ScreenId.KEEPER_GUILD}
    target_party = None
    target_party_members: tuple[tuple[int, str], ...] = ()
    target_guild = None
    target_guild_members: tuple[tuple[int, str, guild_rules.GuildRank], ...] = ()
    if target is not None and flow.screen in group_screens:
        if parties is not None and (party := await parties.of(target.id)) is not None:
            target_party = party
            if named:
                party_names: list[tuple[int, str]] = []
                for member_id in party.members:
                    party_names.append((member_id, await _named(characters, member_id)))
                target_party_members = tuple(party_names)
        if guilds is not None and (guild := await guilds.of(target.id)) is not None:
            target_guild = guild
            if named:
                ranked = sorted(guild.members, key=lambda one: -int(one.rank))
                guild_names: list[tuple[int, str, guild_rules.GuildRank]] = []
                for one in ranked:
                    guild_names.append(
                        (one.character_id, await _named(characters, one.character_id), one.rank)
                    )
                target_guild_members = tuple(guild_names)

    return KeeperView(
        records=registry.records,
        players=players,
        trades=journal,
        target=target,
        target_bag=bag,
        target_party=target_party,
        target_party_members=target_party_members,
        target_guild=target_guild,
        target_guild_members=target_guild_members,
        census=census,
        granting=granting,
        target_keeper=target_keeper,
        target_locked=target_locked,
        target_ban=target_ban,
        target_mute=target_mute,
        target_warnings=target_warnings,
        target_gold_flow=gold_flow_slice,
        maintenance=maintenance,
        log=log,
        log_total=log_total,
        log_target=log_target,
        now=now,
    )


@atomic_action
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
    parties: PartyStore,
    guilds: GuildStore,
    state_cache: StateCache,
    locations: LocationStateCache,
    broadcasts: ChannelBroadcaster | None = None,
    granting: bool = False,
) -> str:
    """Сделать то, о чём попросила панель, и сказать числом, что получилось.

    Здесь же пишется журнал: строку складывает панель, а имя нажавшего и момент
    проставляются тут - часов у автомата нет.
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
        for item_id, delta in write.bag_changes:
            if delta > 0:
                await inventory.add(write.other.id, item_id, delta)
            elif delta < 0 and not await inventory.remove(write.other.id, item_id, -delta):
                raise MissingResourceError(item_id)
    if write.remove_character:
        await characters.delete(write.remove_character)
    if write.party_save is not None:
        await parties.save(write.party_save)
    if write.party_disband:
        await parties.disband(party_rules.Party(leader_id=write.party_disband))
    if write.guild_save is not None:
        await guilds.save(write.guild_save)
    if write.guild_disband:
        gone = await guilds.by_id(write.guild_disband)
        if gone is not None and not await guilds.disband(gone):
            raise MissingResourceError("Guild still holds property or an active war")
    if write.guild_vault is not None:
        await _set_vault(guilds, *write.guild_vault)
    if write.keeper_grant is not None and granting:
        # Автомат уже спросил то же самое; здесь оно спрашивается ещё раз, потому
        # что раздача права - единственное, что раздаёт саму панель.
        account, keeper = write.keeper_grant
        await set_keeper(users, characters, account, keeper=keeper, settings=settings)
    if write.ban is not None:
        said.append(await _ban(write.ban, users, keeper_log, characters, stamp, now))
    if write.mute is not None:
        said.append(await _mute(write.mute, users, keeper_log, characters, stamp, now))
    if write.warn is not None:
        telegram_id, delta = write.warn
        await users.warn(telegram_id, delta=delta)
    if write.ops is not None:
        said.append(
            await _live_op(
                write.ops,
                characters=characters,
                state_cache=state_cache,
                locations=locations,
                broadcasts=broadcasts,
                keeper_log=keeper_log,
                stamp=stamp,
                content=registry.current,
            )
        )
    if write.rollback:
        said.append(await _roll_back(write.rollback, trades, characters, inventory))
    if write.grant_item is not None:
        character_id, item_id, delta = write.grant_item
        if delta > 0:
            await inventory.add(character_id, item_id, delta)
        elif delta < 0 and not await inventory.remove(character_id, item_id, -delta):
            raise MissingResourceError(item_id)
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


async def _live_op(
    order: tuple[str, str],
    *,
    characters: CharacterRepository,
    state_cache: StateCache,
    locations: LocationStateCache,
    broadcasts: ChannelBroadcaster | None,
    keeper_log: KeeperLogRepository,
    stamp: KeeperEntry,
    content: GameContent,
) -> str:
    """Живая операция смотрителя (ADR 0045). Каждая пишется в журнал."""
    action, arg = order

    async def logged(detail: str, target: str = "") -> None:
        await moderation.note(
            keeper_log, replace(stamp, action=KeeperAction.OPS, target=target, detail=detail)
        )

    if action == "maint_on":
        await state_cache.set(
            keeper_panel.MAINTENANCE_KEY,
            arg.strip() or "Скоро вернёмся.",
            keeper_panel.MAINTENANCE_TTL,
        )
        await logged("режим обслуживания включён")
        return "Режим обслуживания включён: игроки стоят, смотрители проходят."
    if action == "maint_off":
        await state_cache.delete(keeper_panel.MAINTENANCE_KEY)
        await logged("режим обслуживания снят")
        return "Режим обслуживания снят."
    if action == "announce":
        posted = await broadcast.announce_service(broadcasts, arg)
        await logged(f"объявление в канал: {arg.strip()}")
        return (
            "Объявление сохранено для отправки в канал."
            if posted
            else "Канал не настроен — не ушло."
        )
    if action in {"free_battle", "reset_player"}:
        target = await characters.find_by_name(arg)
        if target is None:
            return f"Персонажа «{arg.strip()}» в игре нет."
        if action == "free_battle":
            store = BattleStore(state_cache)
            freed = await store.free(target.id)
            if not freed and await store.busy(target.id):
                return f"{target.name}: бой ещё идёт. Вернитесь в него или выберите «Сдаться»."
            await logged("снят замок боя", target=target.name)
            return f"{target.name}: замок боя {'снят' if freed else 'и так не стоял'}."
        await state_cache.set(
            keeper_panel.player_reset_key(target.user_id), "1", keeper_panel.PLAYER_RESET_TTL
        )
        await logged("сброс экрана", target=target.name)
        return f"{target.name}: экран сбросится на следующем нажатии."
    # reset_location: «город слот»
    city_id, _, slot_raw = arg.strip().partition(" ")
    if not content.has_city(city_id) or not slot_raw.strip().isdigit():
        return "Наберите «ключ_города номер_локации», например farhold 1."
    slot = int(slot_raw.strip())
    await locations.reset(city_id, slot)
    await logged(f"сброс локации {city_id} {slot}")
    return f"Локация {content.city(city_id).name}, место {slot}: волны и замки сброшены."


async def _set_vault(guilds: GuildStore, guild_id: int, amount: int) -> None:
    """Выставить казну гильдии числом.

    Абсолютного «выставить» у хранилища нет нарочно: казна двигается условным
    ``UPDATE`` (``Claude.md``, правило 8). Разницу до цели закрывают тем же
    ``deposit``/``withdraw``, что и игроки.
    """
    guild = await guilds.by_id(guild_id)
    if guild is None:
        return
    delta = max(0, amount) - guild.vault_gold
    if delta > 0:
        await guilds.deposit(guild, delta)
    elif delta < 0:
        await guilds.withdraw(guild, -delta)


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


async def _mute(
    order: tuple[int, str, str],
    users: UserRepository,
    keeper_log: KeeperLogRepository,
    characters: CharacterRepository,
    stamp: KeeperEntry,
    now: int,
) -> str:
    """Замолчать в группе или вернуть слово. Срок считается здесь, как и у ``_ban``."""
    account, key, reason = order
    sentence = moderation_rules.sentence_of(key) if key else None
    if key and sentence is None:
        return "Такого срока нет, мьют не наложен."
    mute = (
        moderation_rules.imposed(sentence, reason, now=now)
        if sentence is not None
        else moderation_rules.lifted()
    )
    played = await characters.list_for_user(account)
    named = played[0].name if played else str(account)
    await moderation.set_mute(users, keeper_log, account, mute, by=stamp, target=named)
    if not key:
        return f"{named}: слово в группе возвращено."
    return f"{named}: замолчан в группе."


async def _roll_back(
    trade_id: int,
    trades: TradeRepository,
    characters: CharacterRepository,
    inventory: InventoryRepository,
) -> str:
    """Откатить расчёт и сказать числами, что вернулось.

    Числа названы все, включая невернувшееся: сделка, откаченная наполовину, -
    это работа, которую смотритель должен доделать руками.
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
    переписке. Непонятная ошибка считается «читает».
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


@atomic_action
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
        elif delta < 0 and not await inventory.remove(character.id, item_id, -delta):
            raise MissingResourceError(item_id)

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
        return await characters.save(write.character)
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
    payout = replace(payout, character=await characters.save(payout.character))
    for item_id, count in payout.items:
        if content.has_item(item_id):
            await inventory.add(payout.character.id, item_id, count)
    if payout.gold:
        economy_log.record(economy_log.TUTORIAL, payout.gold, character_id=payout.character.id)
    said = " ".join(payout.lines)
    return payout.character, updated.with_notice(f"{updated.notice} {said}".strip())


async def _pay_digest_haul(
    content: GameContent,
    character: Character,
    before_city: str,
    updated: PlayState,
    characters: CharacterRepository,
    state_cache: StateCache,
    now: int,
    settings: Settings,
) -> tuple[Character, PlayState]:
    """Закрыть дело ``HAUL`` со сводки города-отправления, если игрок дошёл куда просили.

    Дорожный переход помечает кошелёк ``SERVICE`` и меняет ``city_id`` - по этой
    паре его и узнают. Сводку считаем для города, из которого вышли: обоз шёл
    оттуда (``domain/rules/digest.py``, ADR 0053).
    """
    if updated.pending.gold_flow != economy_log.SERVICE:
        return character, updated
    if not before_city or character.city_id == before_city or not content.has_city(before_city):
        return character, updated

    rotation = rotation_index(now, settings.shop_rotation_seconds)
    deeds = digest_rules.digest(
        content, settings.world_seed, before_city, rotation, character.level
    )
    deed = digest_claim.haul_deed(deeds, character.city_id)
    if deed is None:
        return character, updated

    claimed = await digest_claim.claim(
        state_cache,
        content,
        character,
        deed,
        now=now,
        rotation_seconds=settings.shop_rotation_seconds,
    )
    if claimed is None:
        return character, updated
    claimed = replace(claimed, character=await characters.save(claimed.character))
    return claimed.character, updated.with_notice(f"{updated.notice} {claimed.line}".strip())


async def _pay_digest_search(
    content: GameContent,
    character: Character,
    flow: PlayState,
    updated: PlayState,
    characters: CharacterRepository,
    locations: LocationStateCache,
    state_cache: StateCache,
    now: int,
    settings: Settings,
) -> tuple[Character, PlayState]:
    """Закрыть дело ``SEARCH`` со сводки, если шаг отработал названный узел (ADR 0054).

    Узел, из которого шаг забрал единицу волны, помечен в ``pending`` парой
    ``node_take`` / ``node_kind``. Сводку считаем для локации, в которой игрок
    стоит: дело зовёт именно туда. ``moods`` — то же живое состояние округи, что
    видит экран сводки (ADR 0055).
    """
    index = updated.pending.node_take
    node_kind = updated.pending.node_kind
    if index < 0 or not node_kind or not location_known(content, flow.session):
        return character, updated

    rotation = rotation_index(now, settings.shop_rotation_seconds)
    moods = await digest_claim.city_moods(locations, content, flow.session.city_id, now=now)
    deeds = digest_rules.digest(
        content,
        settings.world_seed,
        flow.session.city_id,
        rotation,
        character.level,
        moods=moods,
    )
    deed = digest_claim.search_deed(deeds, slot=flow.session.slot, node_kind=node_kind)
    if deed is None:
        return character, updated

    claimed = await digest_claim.claim(
        state_cache,
        content,
        character,
        deed,
        now=now,
        rotation_seconds=settings.shop_rotation_seconds,
    )
    if claimed is None:
        return character, updated
    claimed = replace(claimed, character=await characters.save(claimed.character))
    return claimed.character, updated.with_notice(f"{updated.notice} {claimed.line}".strip())


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

    Карта у всех одна и хранения не требует, а вот остаток в её узлах общий и
    хранения требует. Здесь шаг вынимает из узла своё одно и ставит этого игрока
    на карту, чтобы остальные его видели. В PostgreSQL-режимах это часть
    постоянной операции команды M03.
    """
    session = updated.session
    if not session.active:
        if before.session.active:
            await locations.leave(before.session.city_id, before.session.slot, character.id)
        return updated, LocationState()

    if not location_known(content, session):
        return updated, LocationState()

    state = await locations.state(session.city_id, session.slot, now=now)
    if session.epoch < 0:
        session = replace(session, epoch=node_rules.location_epoch(state))
        updated = replace(updated, session=session)
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
    *,
    characters: CharacterRepository | None = None,
    content: GameContent | None = None,
) -> str:
    """Взять замок подземелья перед заходом. Пустая строка - можно идти.

    Одиночное подземелье не пускает того, кто ведёт отряд, групповое - одиночку.
    Замок - ``SET NX`` в кэше локации: двое, нажавшие «Спуститься» разом, внутри
    вместе не окажутся (ADR 0037).
    """
    party = await parties.of(character.id)
    in_party = party is not None and len(party.members) >= 2
    if party and characters:
        recruited = Recruitment(parties, characters)
        if content and await recruited.for_party(party.leader_id):
            companions = await fight_companions(
                character,
                parties,
                characters,
                BattleStore(parties._cache),
                flow=PlayState(fight="dungeon", descent=descent),
                content=content,
            )
            in_party = bool(companions)
        else:
            ready = await recruited.companions(
                party, goal_key=f"{descent.city_id}:location:{descent.slot}"
            )
            in_party = character.id in ready and len(ready) >= 2
    if descent.group and not in_party:
        return "Это подземелье рассчитано на отряд. В одиночку туда не спускаются."
    if not descent.group and in_party:
        return "Подземелье для одного: с отрядом сюда не спускаются, ищите то, что на отряд."

    took = await locations.claim_roamer(
        descent.city_id,
        descent.slot,
        character.id,
        ttl=roamer_rules.ROAMER_HOLD_TTL,
        encounter=descent.encounter(character.id),
        stamp=descent.stamp,
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
    place: int = -1,
) -> LocationState:
    """Забрать из узла одну единицу: убитую стаю, горсть руды, свёрток из тайника.

    Волна, которую видел игрок, передаётся вниз: нажатие, опоздавшее к смене
    волны, ничего не забирает (``domain/rules/nodes.py``).

    ``place`` - какое место волны освободилось (ADR 0065); ``-1`` значит «первое
    занятое», и так берут там, где мест не выбирают: жила, тайник, святилище.
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
        place=place,
    )


# --- отряд ------------------------------------------------------------
#
# Отряд лежит в общем хранилище, а автомат ничего не читает и не пишет: он
# приносит намерение (``flows/play._party_intent``), хендлер его исполняет и
# говорит, чем кончилось. Экран при этом не меняется - зов приходит туда, где
# позванный стоит (``docs/accessibility.md``, правило 3).


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
        invitation_notice=await parties.invitations.explanation(character.id),
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
            await Recruitment(parties, characters).leave(character.id)
            word = "Отряд расформирован." if leading else f"{character.name} ушёл из отряда."
            await _tell_party(message, characters, party.members, character.id, word)
            return "Отряд расформирован." if leading else "Вы вышли из отряда."

        case "accept":
            call = await parties.invitations.get(character.id)
            caller = await characters.get(call.group_id) if call else None
            if caller and abs(caller.level - character.level) > party_rules.LEVEL_WINDOW:
                return "Уровни изменились: этот отряд больше не подходит. Выберите набор на доске."
            party = await parties.accept(character.id)
            if party is None:
                return await parties.invitations.explanation(character.id) or (
                    "Приглашение больше недоступно. Откройте «Поиск отряда» и выберите набор."
                )
            if not party.has(character.id):
                return "В отряде не осталось мест. Можно вернуться позже или выбрать другой набор."
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

    refusal = await parties.call(
        leader_id=party.leader_id, invitee_id=target.id, inviter_id=character.id
    )
    if refusal:
        return refusal
    await _tell(
        message,
        target.user_id,
        f"{character.name}, уровень {character.level}, зовёт вас в отряд. "
        "Приглашение действует сутки. Наберите «/отряд принять», чтобы пойти вместе, "
        "или «/отряд отказать». После истечения — «/отряд повторить».",
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
        ScreenId.GUILD_DISBAND,
        ScreenId.GUILD_FOUND,
        ScreenId.GUILD_INVITE,
        ScreenId.GUILD_ROSTER,
        ScreenId.GUILD_VAULT,
        ScreenId.GUILD_TIERS,
        ScreenId.GUILD_SUCCEED,
        ScreenId.GUILD_STORE,
        ScreenId.GUILD_STORE_PUT,
        ScreenId.GUILD_STORE_AMOUNT,
        ScreenId.GUILD_CONTRACT,
        ScreenId.GUILD_WAR,
        ScreenId.GUILD_WAR_DECLARE,
    }
)

#: Экраны хранилища: только на них игра читает, что в нём лежит. Общая сумка -
#: это запрос к базе, и делать его на каждом шаге игрока незачем.
_STORE_SCREENS = frozenset(
    {
        ScreenId.GUILD_STORE,
        ScreenId.GUILD_STORE_PUT,
        ScreenId.GUILD_STORE_AMOUNT,
        ScreenId.GUILD_DISBAND,
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
    content: GameContent,
    settings: Settings,
    now: int,
) -> guild_screens.GuildView:
    """Что показать на экранах гильдии. Везде, кроме них, - пусто.

    Ступень считается из деяний (``guild_rules.standing``), а предел выемки - из
    звания смотрящего и его уровня; сколько он уже вынес за переворот, помнит
    кэш (ADR 0076).
    """
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
        return guild_screens.GuildView(
            my_gold=character.gold,
            caller=caller,
            tiers=content.guild_tiers,
            invitation_notice=await guilds.invitations.explanation(character.id, now=now),
        )
    members: list[tuple[str, guild_rules.GuildRank, int]] = []
    for one in guild.members:
        who = await characters.get(one.character_id)
        if who is not None:
            members.append((who.name, one.rank, one.contributed))
    # Состав идёт званием вниз, а внутри звания - по вкладу: список гильдии
    # отвечает на «кто её держит», а не на «кто раньше пришёл».
    members.sort(key=lambda one: (-int(one[1]), -one[2], one[0]))
    rank = guild.rank_of(character.id)
    place = guild_rules.standing(content, guild)

    # Хранилище, подряд и война читаются только там, где их показывают: три
    # лишних запроса на каждый шаг игрока - это три лишних запроса (ADR 0077).
    stored: tuple[tuple[str, str, int], ...] = ()
    raw_stock = await guilds.stock(guild.id)
    items_taken = 0
    if flow.screen in _STORE_SCREENS:
        stored = tuple(
            (
                item_id,
                content.item(item_id).name
                if content.has_item(item_id)
                else "Вещь из прежней версии",
                held,
            )
            for item_id, held in raw_stock
        )
        items_taken = await guilds.taken_items(
            guild.id, character.id, now=now, rotation_seconds=settings.guild_limit_seconds
        )

    deals: tuple[contract_rules.Contract, ...] = ()
    progress: tuple[int, ...] = ()
    if flow.screen is ScreenId.GUILD_CONTRACT:
        deals = await guilds.current_contracts(
            content,
            place,
            world_seed=settings.world_seed,
            guild_id=guild.id,
            now=now,
            seconds=settings.guild_contract_seconds,
        )
        done = await guilds.contract_progress(
            guild.id, now=now, rotation_seconds=settings.guild_contract_seconds
        )
        progress = tuple(done.get(one.kind, 0) for one in deals)

    war = await guilds.timed_war(
        guild.id,
        now=now,
        legacy_seconds=settings.shop_rotation_seconds,
        duration=settings.guild_war_seconds,
    )
    war_caller = ""
    # Ставку называет вызывающий, и по его ступени её и снимут с обеих казён:
    # экран, показавший свою, обещал бы не то число (``Claude.md``, правило 7).
    stake_place = place
    if war is None:
        called = await guilds.war_called_by(guild.id)
        challenger = await guilds.by_id(called) if called else None
        if challenger is not None:
            war_caller = challenger.name
            stake_place = guild_rules.standing(content, challenger)
    foe = await guilds.by_id(war.foe_of(guild.id)) if war is not None else None

    return guild_screens.GuildView(
        name=guild.name,
        my_rank=rank,
        members=tuple(members),
        vault_gold=guild.vault_gold,
        my_gold=character.gold,
        caller=caller,
        place=place,
        tiers=content.guild_tiers,
        my_taken=await guilds.taken(
            guild.id,
            character.id,
            now=now,
            rotation_seconds=settings.guild_limit_seconds,
        ),
        my_limit=(guild_rules.withdraw_limit(rank, character.level) if rank is not None else 0),
        stored=stored,
        my_items_taken=items_taken,
        my_items_limit=(guild_rules.store_take_limit(rank) if rank is not None else 0),
        vault_action=flow.vault_action,
        contracts=deals,
        contract_progress=progress,
        at_war=war is not None,
        war_foe=foe.name if foe is not None else "",
        war_mine=war.score_of(guild.id) if war is not None else 0,
        war_theirs=war.score_of(war.foe_of(guild.id)) if war is not None else 0,
        war_left=max(0, war.ends - now) if war is not None else 0,
        war_stake=(
            war.stake if war is not None else war_rules.war_stake(content.guild_tiers, stake_place)
        ),
        war_caller=war_caller,
        disband_token=disband_token(guild, raw_stock) if guild is not None else "",
        contract_left=(
            await guilds.period(
                guild.id, "contract", now=now, seconds=settings.guild_contract_seconds
            )
        )[1]
        - now
        if guild is not None
        else 0,
        limit_left=(
            await guilds.period(guild.id, "limits", now=now, seconds=settings.guild_limit_seconds)
        )[1]
        - now
        if guild is not None
        else 0,
        war_duration=settings.guild_war_seconds,
        war_call_duration=settings.guild_war_call_seconds,
    )


@atomic_action
async def _guild_step(
    message: Message,
    content: GameContent,
    character: Character,
    action: str,
    arg: str,
    characters: CharacterRepository,
    guilds: GuildStore,
    settings: Settings,
    now: int,
    *,
    inventory: InventoryRepository | None = None,
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
                place=guild_rules.standing(content, guild),
                inviter_id=character.id,
                invitee_name=target.name,
                invitee_in_guild=await guilds.of(target.id) is not None,
            )
            if refusal:
                return refusal, character
            refusal = await guilds.call(
                guild_id=guild.id, invitee_id=target.id, inviter_id=character.id
            )
            if refusal:
                return refusal, character
            await _tell(
                message,
                target.user_id,
                f"Гильдия «{guild.name}» зовёт вас к себе. Наберите «/гильдия принять», "
                "чтобы вступить, или «/гильдия отклонить». Срок — сутки; "
                "после истечения — «/гильдия повторить».",
            )
            return f"Зов отправлен: {target.name}.", character

        case "accept":
            joined = await guilds.accept(character.id, content)
            if joined is None:
                return (
                    await guilds.invitations.explanation(character.id)
                    or "Приглашение больше недоступно. "
                    "Попросите новый зов или выберите другую гильдию.",
                    character,
                )
            if not joined.has(character.id):
                return f"В гильдии «{joined.name}» не осталось мест.", character
            rank = joined.rank_of(character.id)
            title = rank.title if rank is not None else guild_rules.GuildRank.RECRUIT.title
            return f"Вы в гильдии «{joined.name}». Ваше звание: {title}.", character

        case "decline":
            await guilds.forget_call(character.id)
            return "Зов отклонён.", character

        case "leave":
            if guild is None:
                return "Вы не в гильдии.", character
            if guild.founder_id == character.id:
                return (
                    "Вы основатель: гильдию можно распустить или передать другому.",
                    character,
                )
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
            if inventory is None:
                return "Откройте роспуск гильдии и подтвердите передачу имущества.", character
            said = await dissolve(
                guilds, characters, inventory, guild.id, actor_id=character.id, expected=arg
            )
            if await guilds.by_id(guild.id) is not None:
                return said, character
            economy_log.record(economy_log.GUILD_VAULT, guild.vault_gold, character_id=character.id)
            await _tell_party(
                message,
                characters,
                [one.character_id for one in guild.members],
                character.id,
                f"Гильдия «{guild.name}» распущена.",
            )
            return said, await fresh()

        case "succeed":
            target = await characters.find_by_name(arg)
            if target is None:
                return f"Игрока с именем {arg} в Велларе нет.", character
            refusal = guild_rules.succeed_refusal(
                guild=guild, actor_id=character.id, target_id=target.id
            )
            if refusal:
                return refusal, character
            assert guild is not None
            await guilds.save(guild.succeeded_by(target.id))
            await _tell(
                message,
                target.user_id,
                f"Гильдия «{guild.name}» теперь ваша: вы её основатель.",
            )
            return (
                f"Гильдия передана: {target.name}. Вы остались в ней "
                f"{guild_rules.GuildRank.ELDER.title}ой."
            ), character

        case "promote" | "demote" | "kick":
            return await _guild_rank_step(
                message, character, action, arg, characters, guilds, guild
            )

        case "deposit" | "withdraw":
            return await _guild_vault_step(
                content, character, action, arg, characters, guilds, guild, settings, now
            )

        case "war_declare" | "war_accept" | "war_decline":
            said = await _guild_war_step(
                message, content, character, action, arg, characters, guilds, settings, now
            )
            return said, character

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
    # Звание двигают на ступень: пять званий, и «повысить» значит «на одну выше»,
    # а не «сразу в старейшины» (ADR 0076).
    current = guild.rank_of(target.id) or guild_rules.GuildRank.RECRUIT
    to = guild_rules.raised(current) if action == "promote" else guild_rules.lowered(current)
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


@atomic_action
async def _guild_vault_step(
    content: GameContent,
    character: Character,
    action: str,
    arg: str,
    characters: CharacterRepository,
    guilds: GuildStore,
    guild: guild_rules.Guild | None,
    settings: Settings,
    now: int,
) -> tuple[str, Character]:
    """Вклад и выемка. Вклад платит гильдии деяниями, выемка знает свой предел.

    Предел — за переворот прилавка и по званию (ADR 0076, M02.2).
    Постоянный счёт включает номер переворота и сохраняется вместе с выемкой.
    """
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
        credited = await guilds.credit_deposit(guild, amount)
        economy_log.record(economy_log.GUILD_VAULT, -amount, character_id=character.id)
        deeds = guild_rules.deeds_for_deposit(credited, character.level)
        if deeds:
            await guilds.record_deeds(guild.id, character.id, deeds)
        # Внесённое идёт и в подряд: «снести в казну золота» - одно из трёх дел,
        # которые застава просит у гильдии на переворот (ADR 0077).
        closed = await guilds.work_on_contract(
            content,
            guild_id=guild.id,
            place=guild_rules.standing(content, guild),
            kind=contract_rules.ContractKind.TITHE,
            amount=credited,
            world_seed=settings.world_seed,
            now=now,
            rotation_seconds=settings.guild_contract_seconds,
            contributors=(character.id,),
            receipt=operation.id if (operation := current_operation()) else "",
        )
        said = f"В казну внесено {amount}."
        if credited < amount:
            said += (
                f" Новый вклад: {credited}. Возврат ранее вынесенного золота "
                "не даёт деяний и не продвигает подряд."
            )
        if deeds:
            said += f" Гильдии записано деяний: {deeds}."
        if closed is not None:
            economy_log.record(
                economy_log.GUILD_CONTRACT,
                closed.reward_gold,
                character_id=character.id,
                detail="tithe",
            )
            said += f" Подряд закрыт: гильдии {closed.pay}."
        return said, await fresh()

    taken = await guilds.taken(
        guild.id, character.id, now=now, rotation_seconds=settings.guild_limit_seconds
    )
    refusal = guild_rules.withdraw_refusal(
        guild=guild, actor_id=character.id, amount=amount, level=character.level, taken=taken
    )
    if refusal:
        return refusal, character
    if not await guilds.withdraw(guild, amount):
        return "В казне столько не набралось.", character
    await guilds.recycle_withdrawal(guild, amount)
    await guilds.note_taken(
        guild.id,
        character.id,
        amount,
        now=now,
        rotation_seconds=settings.guild_limit_seconds,
    )
    await characters.grant_gold(character.id, amount)
    economy_log.record(economy_log.GUILD_VAULT, amount, character_id=character.id)
    return f"Из казны взято {amount}.", await fresh()


# --- передача вещи в отряде и гильдии --------------------------------
#
# Адресный подарок соратнику или соклановцу, минуя игровую группу: мгновенный
# и без подтверждения получателя. Эскроу и пошлины нет, ``gold_flow`` тут
# ничего не пишет. Проверки - состав, чёрный список, занятость боем - требуют
# хранилищ, поэтому живут здесь (``Claude.md``, правило 5).


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


@atomic_action
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


# --- хранилище, подряд и война (ADR 0077) ----------------------------


@atomic_action
async def _guild_store_step(
    content: GameContent,
    character: Character,
    flow: PlayState,
    inventory: InventoryRepository,
    guilds: GuildStore,
    settings: Settings,
    now: int,
) -> str:
    """Положить вещь в хранилище гильдии или взять её оттуда. Ответ - фразой.

    Место в хранилище считается видами, а предел выемки - штуками за переворот
    (``domain/rules/guild``): общее добро, которое один человек выносит целиком,
    - это не общее добро.
    """
    guild = await guilds.of(character.id)
    if guild is None:
        return "Вы не в гильдии."
    item_id, want = flow.vault_item, flow.vault_amount
    if not content.has_item(item_id) or want <= 0:
        return "Не понял, что положить или взять."
    name = content.item(item_id).name
    place = guild_rules.standing(content, guild)
    stock = dict(await guilds.stock(guild.id))

    if flow.vault_action == "take":
        taken = await guilds.taken_items(
            guild.id, character.id, now=now, rotation_seconds=settings.guild_limit_seconds
        )
        refusal = guild_rules.take_refusal(
            guild=guild,
            actor_id=character.id,
            amount=want,
            stored=stock.get(item_id, 0),
            taken=taken,
        )
        if refusal:
            return refusal
        if not await guilds.unstow(guild.id, item_id, want):
            return "В хранилище столько не набралось."
        await inventory.add(character.id, item_id, want)
        await guilds.note_taken_items(
            guild.id,
            character.id,
            want,
            now=now,
            rotation_seconds=settings.guild_limit_seconds,
        )
        return f"Из хранилища взято: {name}, штук {want}."

    held = await inventory.count(character.id, item_id)
    refusal = guild_rules.stow_refusal(
        guild=guild,
        actor_id=character.id,
        amount=want,
        held=held,
        place=place,
        kinds=len({key.partition("!")[0] for key in stock}),
        known=any(key.partition("!")[0] == item_id.partition("!")[0] for key in stock),
    )
    if refusal:
        return refusal
    if not await inventory.remove(character.id, item_id, want):
        return "Вещь не удалось забрать из сумки. Попробуйте заново."
    await guilds.stow(guild.id, item_id, want)
    return f"В хранилище положено: {name}, штук {want}."


@atomic_action
async def _guild_war_step(
    message: Message,
    content: GameContent,
    character: Character,
    action: str,
    arg: str,
    characters: CharacterRepository,
    guilds: GuildStore,
    settings: Settings,
    now: int,
) -> str:
    """Объявить войну, принять вызов или отклонить его (ADR 0077).

    Ставку снимают с обеих казён в минуту согласия, а не вызова: вызов, который
    держит золото неизвестно сколько, - это замороженная казна.
    """
    guild = await guilds.of(character.id)
    if guild is None:
        return "У вас нет гильдии."
    place = guild_rules.standing(content, guild)
    mine = await guilds.war_of(guild.id)

    if action == "war_decline":
        called = await guilds.war_called_by(guild.id)
        if not called:
            return "Вас никто не вызывал."
        await guilds.forget_war_call(guild.id)
        challenger = await guilds.by_id(called)
        return f"Вызов гильдии «{challenger.name}» отклонён." if challenger else "Вызов отклонён."

    if action == "war_declare":
        foe = await guilds.by_name(arg)
        stake = war_rules.war_stake(content.guild_tiers, place)
        refusal = war_rules.declare_refusal(
            guild=guild,
            actor_id=character.id,
            foe=foe,
            foe_name=arg.strip(),
            stake=stake,
            at_war=mine is not None,
            foe_at_war=foe is not None and await guilds.war_of(foe.id) is not None,
            called=foe is not None and await guilds.war_called_by(foe.id) == guild.id,
        )
        if refusal:
            return refusal
        assert foe is not None
        await guilds.call_war(
            challenger_id=guild.id, defender_id=foe.id, seconds=settings.guild_war_call_seconds
        )
        await _tell_party(
            message,
            characters,
            [one.character_id for one in foe.members],
            character.id,
            f"Гильдия «{guild.name}» вызывает вас на войну. Ставка - {stake} золота с "
            "каждой стороны. Ответить может основатель: «/гильдия сразиться» или "
            "«/гильдия отступить».",
        )
        return f"Вызов послан гильдии «{foe.name}». Слово за ней."

    called = await guilds.war_called_by(guild.id)
    challenger = await guilds.by_id(called) if called else None
    stake = (
        war_rules.war_stake(content.guild_tiers, guild_rules.standing(content, challenger))
        if challenger is not None
        else 0
    )
    refusal = war_rules.accept_refusal(
        guild=guild,
        actor_id=character.id,
        challenger=challenger,
        stake=stake,
        at_war=mine is not None,
        challenger_at_war=challenger is not None and await guilds.war_of(challenger.id) is not None,
    )
    if refusal:
        return refusal
    assert challenger is not None
    if not await guilds.withdraw(challenger, stake):
        return f"В казне гильдии «{challenger.name}» ставки уже нет."
    if not await guilds.withdraw(guild, stake):
        # Чужая ставка уже снята: вернуть её - не любезность, а обязанность.
        await guilds.pay_vault(challenger.id, stake)
        return "В вашей казне ставки не набралось."
    await guilds.forget_war_call(guild.id)
    war = await guilds.open_war(
        challenger_id=challenger.id,
        defender_id=guild.id,
        stake=stake,
        started=now,
        ends=now + settings.guild_war_seconds,
        clock_seconds=True,
    )
    await WarScoring(guilds, characters).begin(war)
    for side, foe_name in ((challenger, guild.name), (guild, challenger.name)):
        await _tell_party(
            message,
            characters,
            [one.character_id for one in side.members],
            character.id,
            f"Война с гильдией «{foe_name}» началась. Очко берут за выигранный поединок "
            f"с её человеком; срок: {guild_screens.duration(settings.guild_war_seconds)}.",
        )
    return f"Война с гильдией «{challenger.name}» началась. Ставка с каждой стороны: {war.stake}."


async def _settle_guild_war(
    message: Message,
    content: GameContent,
    character: Character,
    characters: CharacterRepository,
    guilds: GuildStore,
    settings: Settings,
    now: int,
) -> None:
    """Подвести войну, у которой вышел срок, - лениво и один раз (ADR 0077).

    Часов в игре нет, и войну закрывает тот, кто первым на неё заглянул. Итог
    приходит вестью обеим гильдиям: экран, на котором война просто исчезла, не
    сказал бы, чем она кончилась.
    """
    guild = await guilds.of(character.id)
    if guild is None:
        return
    war = await guilds.timed_war(
        guild.id,
        now=now,
        legacy_seconds=settings.shop_rotation_seconds,
        duration=settings.guild_war_seconds,
    )
    if war is None:
        return
    settled = await guilds.settle_war(content, war, now)
    if settled is None:
        return
    champion = war_rules.winner_of(settled)
    for side_id in (settled.challenger_id, settled.defender_id):
        side = await guilds.by_id(side_id)
        if side is None:
            continue
        foe = await guilds.by_id(settled.foe_of(side_id))
        foe_name = foe.name if foe is not None else "другой гильдией"
        if champion == 0:
            said = f"Война с гильдией «{foe_name}» кончилась поровну. Ставка вернулась в казну."
        elif champion == side_id:
            said = (
                f"Война с гильдией «{foe_name}» выиграна: счёт "
                f"{settled.score_of(side_id)} на {settled.score_of(settled.foe_of(side_id))}. "
                f"Обе ставки - {settled.stake * 2} золота - в вашей казне."
            )
        else:
            said = (
                f"Война с гильдией «{foe_name}» проиграна: счёт "
                f"{settled.score_of(side_id)} на {settled.score_of(settled.foe_of(side_id))}. "
                "Ставка ушла победившей."
            )
        await _tell_party(message, characters, [one.character_id for one in side.members], 0, said)
