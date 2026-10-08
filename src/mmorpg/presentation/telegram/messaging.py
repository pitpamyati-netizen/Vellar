"""Отправка экранов.

Одно действие игрока порождает ровно **одно** новое сообщение (правило
доступности 3 и бюджет задержки). Сообщения не правятся никогда и не рассыпаются
очередью: если тело действительно не влезает, оно режется на страницы, и
следующую игрок просит сам.

``parse_mode`` везде ``None``: звёздочки и подчёркивания разметки экранный диктор
читает вслух (правило 14).
"""

from __future__ import annotations

from collections.abc import Callable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, ReplyKeyboardMarkup, ReplyKeyboardRemove, ReplyParameters

from mmorpg.application.operations import after_commit, current_operation
from mmorpg.presentation.telegram import reading
from mmorpg.presentation.telegram.keyboards.reply import (
    dismiss_keyboard,
    keyboard_for,
    selective_keyboard,
)
from mmorpg.presentation.telegram.screens.base import Screen
from mmorpg.presentation.telegram.screens.format import MESSAGE_LIMIT
from mmorpg.presentation.telegram.screens.group import GroupReply


async def send_screen(message: Message, screen: Screen, *, emoji: bool = False) -> None:
    """Отправить экран одним новым сообщением с прицепленной клавиатурой."""
    screen = await reading.prepare(
        message.bot.id if message.bot else 0, message.chat.id, screen, emoji
    )
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
        screen = await reading.prepare(bot.id, chat_id, screen, emoji)
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
    """Отправить разовый ответ, всё же несущий нынешнюю клавиатуру.

    Берётся для устаревших кнопок: игрок всегда получает и объяснение, *и* те
    кнопки, которые сейчас работают (правило 12).
    """
    from dataclasses import replace

    if len(text) > MESSAGE_LIMIT - 160:
        screen = await reading.prepare(
            message.bot.id if message.bot else 0,
            message.chat.id,
            replace(screen, lines=(text, "", *screen.lines)),
            emoji,
        )
        text = screen.body()
    elif len(reading.parts(screen)) > 1:
        screen = reading.page(screen, 1)
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
    on_sent: Callable[[int], None] | None = None,
) -> int:
    """Написать один ответ в группу ответом на сообщение и вернуть идентификатор отправленного.

    Привязка ответом - это то, что заставляет работать ``selective``: Telegram
    покажет клавиатуру отправителю того сообщения, которому отвечают, и больше
    никому. Поэтому предложение привязано к сообщению того, кому предложили, а
    закрывающая записка - к сообщению того, кто ответил.

    ``allow_sending_without_reply`` не даёт удалённой привязке проглотить ответ:
    группе лучше увидеть висящее сообщение, чем не увидеть ничего.
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
    if on_sent is not None:
        operation = current_operation()

        async def notify() -> None:
            number = (
                operation.sent_messages.get(sent.message_id, 0)
                if operation is not None and sent.message_id < 0
                else sent.message_id
            )
            if number > 0:
                on_sent(number)

        await after_commit(notify)
    return sent.message_id
