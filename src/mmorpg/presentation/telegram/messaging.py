"""Отправка экранов.

Одно действие игрока порождает ровно **одно** новое сообщение (правило
доступности 3 и бюджет задержки). Сообщения не правятся никогда и не рассыпаются
очередью: если тело действительно не влезает, оно режется на страницы, и
следующую игрок просит сам.

``parse_mode`` везде ``None``: звёздочки и подчёркивания разметки экранный диктор
читает вслух (правило 14).
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
    """Отправить экран одним новым сообщением с прицепленной клавиатурой."""
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
    """Отправить разовый ответ, всё же несущий нынешнюю клавиатуру.

    Берётся для устаревших кнопок: игрок всегда получает и объяснение, *и* те
    кнопки, которые сейчас работают (правило 12).
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
    return sent.message_id
