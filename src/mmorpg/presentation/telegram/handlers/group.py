"""The one handler that runs in the game group.

Everything here is about deciding whether the bot should speak at all. The group
is a room full of people talking to each other, and the default is silence: a
message is answered only when all of these hold (``Narrative.md``, section 9):

- it arrives in the configured group, not in some chat the bot was added to;
- it parses as a command, whole and unambiguous;
- it is a reply to another player's message - that reply *is* how the target is
  named, so a command shouted into the room addresses nobody and is ignored;
- the sender has not just flooded the chat.

Answering an offer is the one exception to the reply rule: "принять 12" carries
its target in the number, and asking a player to find the original message before
they may say yes would be cruel with a screen reader.

Nothing is decided here beyond that. The command is parsed by the domain, carried
out by ``application.services.group_trade`` and worded by ``screens.group``; this
module joins the three and schedules the deletion of what it said.
"""

from __future__ import annotations

import time

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.types import Chat, Message

from mmorpg.application.services.group_trade import GroupResult, GroupTrade
from mmorpg.config import Settings
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.ports.repositories import (
    CharacterRepository,
    InventoryRepository,
    TradeRepository,
)
from mmorpg.domain.rules.group_commands import GroupIntent, parse_group_command
from mmorpg.domain.rules.group_offers import Refusal
from mmorpg.presentation.telegram.broadcast import chat_id_of
from mmorpg.presentation.telegram.cleanup import Deleter, MessageReaper
from mmorpg.presentation.telegram.messaging import send_group_reply
from mmorpg.presentation.telegram.screens.group import REFUSALS, GroupReply, render
from mmorpg.presentation.telegram.throttle import RateLimiter

# Answers that close an offer, and so take the two buttons back.
ANSWERS = (GroupIntent.ACCEPT, GroupIntent.DECLINE)
# Results that leave nothing pending, whoever they belong to.
CLOSED = (GroupResult.OFFER_ACCEPTED, GroupResult.OFFER_DECLINED, GroupResult.REFUSED)


def build_router(reaper: MessageReaper, limiter: RateLimiter | None = None) -> Router:
    """A fresh router per application - see ``handlers.creation.build_router``.

    The rate limiter is owned by the router rather than by the dependency
    container: it is per-process state about behaviour, not a service anyone else
    has a use for.
    """
    router = Router(name="group")
    router.message.filter(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
    limits = limiter if limiter is not None else RateLimiter()

    async def entry(
        message: Message,
        bot: Bot,
        settings: Settings,
        content: GameContent,
        characters: CharacterRepository,
        inventory: InventoryRepository,
        trades: TradeRepository,
    ) -> None:
        await handle_group_message(
            message,
            bot=bot,
            settings=settings,
            content=content,
            characters=characters,
            inventory=inventory,
            trades=trades,
            limiter=limits,
            reaper=reaper,
        )

    router.message.register(entry)
    return router


def is_game_group(chat: Chat, configured: str) -> bool:
    """Whether this chat is *the* group. Accepts a numeric id or an @username."""
    wanted = chat_id_of(configured)
    if isinstance(wanted, int):
        return chat.id == wanted
    return bool(chat.username) and chat.username == wanted.lstrip("@")


async def handle_group_message(
    message: Message,
    *,
    bot: Bot,
    settings: Settings,
    content: GameContent,
    characters: CharacterRepository,
    inventory: InventoryRepository,
    trades: TradeRepository,
    limiter: RateLimiter,
    reaper: MessageReaper,
    now: int | None = None,
) -> int | None:
    """Answer one group message, or stay quiet. Returns the id of what was sent."""
    author = message.from_user
    if message.text is None or author is None or author.is_bot:
        return None
    if not settings.group_chat_enabled or not is_game_group(message.chat, settings.group_id):
        return None

    command = parse_group_command(message.text)
    if command is None:
        return None

    answering = command.intent in ANSWERS
    target = message.reply_to_message
    target_user = target.from_user if target is not None else None
    if not answering and (target is None or target_user is None or target_user.is_bot):
        # Not addressed to a player. The bot has no business in this sentence.
        return None

    if not limiter.allow(author.id):
        if not limiter.should_warn(author.id):
            return None
        return await _say(
            bot,
            message,
            GroupReply(text=REFUSALS[Refusal.TOO_MANY_COMMANDS]),
            anchor=message.message_id,
            reaper=reaper,
        )

    trade = GroupTrade(
        content=content,
        characters=characters,
        inventory=inventory,
        trades=trades,
        scope=str(message.chat.id),
    )
    outcome = await trade.run(
        command,
        author_id=author.id,
        target_id=target_user.id if target_user is not None else None,
        now=now if now is not None else int(time.time()),
    )
    reply = render(content, outcome)

    # An offer is anchored to the target's message so that only they see the
    # buttons; everything else answers the person who spoke.
    anchor = message.message_id
    if reply.awaits_answer and target is not None:
        anchor = target.message_id

    return await _say(
        bot,
        message,
        reply,
        anchor=anchor,
        reaper=reaper,
        dismiss=answering and outcome.result in CLOSED,
    )


async def _say(
    bot: Bot,
    message: Message,
    reply: GroupReply,
    *,
    anchor: int,
    reaper: MessageReaper,
    dismiss: bool = False,
) -> int:
    """Post the answer and put it on the clock (``cleanup.MessageReaper``)."""
    sent = await send_group_reply(
        bot,
        chat_id=message.chat.id,
        reply=reply,
        answering=anchor,
        dismiss=dismiss,
    )
    reaper.schedule(_deleter(bot), message.chat.id, sent)
    return sent


def _deleter(bot: Bot) -> Deleter:
    """The single call the reaper makes, bound to this bot."""

    async def delete(chat_id: int, message_id: int) -> None:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)

    return delete
