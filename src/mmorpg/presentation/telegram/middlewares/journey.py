"""Сводка, история и обезличенный отчёт об этапах внутри записи команды."""

from __future__ import annotations

import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import Message, TelegramObject

from mmorpg.application.journey import Activity, JourneyRepository, Visit
from mmorpg.application.operations import current_operation
from mmorpg.application.services.battle import BattleStore
from mmorpg.domain.entities.character import Character
from mmorpg.domain.rules import tutorial
from mmorpg.presentation.telegram.flows.play import begin
from mmorpg.presentation.telegram.flows.state import Descent, PlayState
from mmorpg.presentation.telegram.messaging import send_screen
from mmorpg.presentation.telegram.reading import requested_number
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.screens.journey import (
    CONTINUE,
    HISTORY,
    RETURN,
    history_screen,
    returning_screen,
)
from mmorpg.presentation.telegram.states.screens import NavigationStack, Play

HISTORY_COMMAND = re.compile(r"^/(?:история|history)(?:\s+(\d+))?$", re.IGNORECASE)


def changed(
    before: Character | None,
    after: Character | None,
    at: int,
    items: tuple[tuple[str, int], ...] = (),
) -> Activity | None:
    if before is None or after is None or before.id != after.id:
        return None
    result = Activity(
        at,
        after.gold - before.gold,
        after.bank_gold - before.bank_gold,
        after.experience - before.experience,
        after.level - before.level,
        tutorial.done_count(after) - tutorial.done_count(before),
        len(after.quests.done) - len(before.quests.done),
        items,
    )
    return (
        result
        if any(
            (
                result.gold,
                result.bank,
                result.experience,
                result.levels,
                result.tutorial_steps,
                result.quests,
                result.items,
            )
        )
        else None
    )


async def return_to_game(
    message: Message,
    state: Any,
    content: Any,
    character: Character,
    cache: Any,
    journey: JourneyRepository | None,
    *,
    emoji: bool = False,
) -> None:
    data = await state.get_data()
    flow = PlayState.deserialise(data["play"]) if data.get("play") else begin(character)
    battle_line = ""
    if cache is not None:
        store = BattleStore(cache)
        if battle_id := await store.busy(character.id):
            session = await store.load(battle_id)
            if session:
                actor = session.state.active
                battle_line = (
                    f"Бой сохранён. Круг {session.state.round}. "
                    + (f"Ход: {actor.name}. " if actor else "")
                    + "«Продолжить дело» вернёт в бой без нового хода."
                )
    history = (
        await journey.history(message.bot.id if message.bot else 0, character.user_id, limit=1)
        if journey
        else ()
    )
    await send_screen(
        message,
        returning_screen(
            content, character, flow, battle=battle_line, latest=history[0] if history else None
        ),
        emoji=emoji,
    )


class JourneyMiddleware(BaseMiddleware):
    def __init__(self, journey: JourneyRepository) -> None:
        self.journey = journey

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if (
            not isinstance(event, Message)
            or event.chat.type != ChatType.PRIVATE
            or not event.from_user
        ):
            return await handler(event, data)
        characters = data["characters"]
        user_id = event.from_user.id
        bot_id = data["bot"].id
        before = await characters.get_active(user_id)
        inventory = data["inventory"]
        before_bag = await inventory.list_items(before.id) if before else ()
        now = int(time.time())
        text = (event.text or "").strip()
        history_command = HISTORY_COMMAND.fullmatch(text)
        user = data.get("user")
        emoji = user.settings.emoji if user else False
        if before and (text in {RETURN.text, "/возвращение", "/return"}):
            await return_to_game(
                event,
                data["state"],
                data["content"],
                before,
                data["state_cache"],
                self.journey,
                emoji=emoji,
            )
            result = None
        elif before and (text == HISTORY.text or history_command):
            number = requested_number(history_command[1] or "1") if history_command else 1
            # Очень большое число не вызывает переполнение параметра PostgreSQL.
            number = min(number, 1_000_000)
            found = await self.journey.history(
                bot_id, before.user_id, limit=6, offset=(number - 1) * 5
            )
            await send_screen(
                event,
                history_screen(
                    found[:5], page=number, more=len(found) > 5, content=data["content"]
                ),
                emoji=emoji,
            )
            result = None
        else:
            if before and text in {CONTINUE.text, "/дело", "/continue"}:
                current = await data["state"].get_state()
                command = (
                    "/продолжить"
                    if current in {Play.combat.state, Play.combat_bag.state}
                    else "/осмотреться"
                )
                event = event.model_copy(update={"text": command}).as_(event.bot)
            if before and text.casefold() in {"/обучение", "/tutorial"}:
                state = data["state"]
                saved = await state.get_data()
                battle_id = saved.get("battle")
                store = BattleStore(data["state_cache"])
                battle = await store.load(battle_id) if battle_id else None
                if battle and battle.state.is_over and battle.settled:
                    flow = (
                        PlayState.deserialise(saved["play"]) if saved.get("play") else begin(before)
                    )
                    if flow.descent.roamer:
                        await data["locations"].release_roamer(
                            flow.descent.city_id,
                            flow.descent.slot,
                            encounter=flow.descent.encounter(before.id),
                        )
                    flow = replace(
                        flow,
                        screen=ScreenId.MAIN_MENU,
                        stack=NavigationStack((ScreenId.MAIN_MENU,)),
                        descent=Descent(),
                        fight="",
                        notice="",
                    )
                    await state.update_data({"battle": "", "play": flow.serialise()})
                    await state.set_state(Play.main_menu)
                    data["raw_state"] = Play.main_menu.state
            result = await handler(event, data)
        after = await characters.get_active(user_id)
        after_bag = await inventory.list_items(after.id) if after else ()
        old_items = Counter({one.item_id: one.quantity for one in before_bag})
        new_items = Counter({one.item_id: one.quantity for one in after_bag})
        if before:
            old_items.update(before.equipment.item_ids())
        if after:
            new_items.update(after.equipment.item_ids())
        item_changes = tuple(
            (item, new_items[item] - old_items[item])
            for item in sorted(old_items.keys() | new_items.keys())
            if new_items[item] != old_items[item]
        )
        state = data.get("state")
        phase = ((await state.get_state()) or "start").split(":")[-1] if state else "start"
        # Никаких строк ввода, поисковых слов, имён, кнопок или текста отказа.
        visit = Visit(
            bot_id,
            user_id,
            phase,
            now,
            after.id if after else 0,
            after.tutorial if after else 0,
        )
        operation = current_operation()
        await self.journey.observe(
            visit,
            changed(before, after, now, item_changes),
            operation.id if operation else f"telegram:{bot_id}:{event.chat.id}:{event.message_id}",
        )
        return result
