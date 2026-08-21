"""Sending screens.

One player action produces exactly **one** new message (accessibility rule 3 and
the latency budget). Messages are never edited and never split into a burst -
if a body genuinely cannot fit, it is paged and the player asks for the next page.

``parse_mode`` is ``None`` everywhere: Markdown asterisks and underscores are read
aloud by screen readers (rule 14).
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, ReplyKeyboardMarkup, ReplyKeyboardRemove, ReplyParameters

from mmorpg.presentation.telegram.keyboards.reply import (
    dismiss_keyboard,
    keyboard_for,
    selective_keyboard,
)
from mmorpg.presentation.telegram.screens.base import Screen
from mmorpg.presentation.telegram.screens.group import GroupReply


async def send_screen(message: Message, screen: Screen, *, emoji: bool = False) -> None:
    """Send a screen as a single new message with its keyboard attached."""
    await message.answer(
        text=screen.body(),
        reply_markup=keyboard_for(screen, emoji=emoji),
        parse_mode=None,
    )


async def push_screen(bot: Bot, chat_id: int, screen: Screen, *, emoji: bool = False) -> bool:
    """Отправить экран тому, кто сейчас не нажимал ничего.

    Так приходит чужой ход в поединке: игрок не спрашивал, но узнать обязан, а
    другого способа сказать ему об этом нет - редактировать сообщения игра не
    умеет и не будет (``docs/accessibility.md``, правило 2).

    Ложь в ответе значит «не дошло»: заблокировал бота, удалил чат, не начинал
    его. Бой из-за этого не падает - у оставшегося есть «Сдаться».
    """
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=screen.body(),
            reply_markup=keyboard_for(screen, emoji=emoji),
            parse_mode=None,
        )
    except TelegramAPIError:
        return False
    return True


async def send_text(message: Message, text: str, screen: Screen, *, emoji: bool = False) -> None:
    """Send a one-off answer that still carries the current keyboard.

    Used for stale buttons: the player always gets an explanation *and* the
    buttons that actually work right now (rule 12).
    """
    await message.answer(
        text=text,
        reply_markup=keyboard_for(screen, emoji=emoji),
        parse_mode=None,
    )


async def send_group_reply(
    bot: Bot,
    *,
    chat_id: int,
    reply: GroupReply,
    answering: int,
    dismiss: bool = False,
) -> int:
    """Post one group answer as a reply, and return the id of what was sent.

    The reply anchor is what makes a ``selective`` keyboard work: Telegram shows
    it to the sender of the message being answered and to nobody else. So an offer
    is anchored to the target's message, and the closing note to the message of
    whoever answered.

    ``allow_sending_without_reply`` keeps a deleted anchor from swallowing the
    answer: the group would rather see a floating message than nothing at all.
    """
    markup: ReplyKeyboardMarkup | ReplyKeyboardRemove | None = None
    if reply.buttons:
        markup = selective_keyboard(reply.buttons)
    elif dismiss:
        markup = dismiss_keyboard()

    sent = await bot.send_message(
        chat_id=chat_id,
        text=reply.text,
        reply_parameters=ReplyParameters(message_id=answering, allow_sending_without_reply=True),
        reply_markup=markup,
        parse_mode=None,
    )
    return sent.message_id
