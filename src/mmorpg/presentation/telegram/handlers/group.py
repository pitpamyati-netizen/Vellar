"""Единственный хендлер, работающий в игровой группе.

Всё здесь о том, стоит ли боту говорить вообще. Группа — это комната, полная
людей, разговаривающих друг с другом, и по умолчанию там молчат: на сообщение
отвечают, только когда верно всё сразу (``Narrative.md``, раздел 9):

- оно пришло в настроенную группу, а не в какой-то чат, куда бота добавили;
- оно разбирается как команда, целиком и однозначно;
- оно ответ на сообщение другого игрока — этот ответ *и есть* то, как назван
  адресат, поэтому команда, выкрикнутая в комнату, не обращена ни к кому и
  пропускается;
- отправитель только что не залил чат.

Два вида сообщений из правила об ответе исключены, и оба не называют никого
другого. «принять 12» несёт адресата в номере, а просить игрока найти исходное
сообщение, прежде чем он вправе сказать «да», было бы жестоко при экранном
дикторе; «скрыть профиль» касается одного говорящего (``UNADDRESSED``).

Больше здесь не решается ничего. Команду разбирает домен, исполняет
``application.services.group_trade``, а высказывает ``screens.group``; этот
модуль сводит их троих и откладывает удаление сказанного.
"""

from __future__ import annotations

import time

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Chat, Message

from mmorpg.application.services.group_trade import GroupResult, GroupTrade
from mmorpg.application.services.party import PartyStore
from mmorpg.config import ANY_GROUP, Settings
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.ports.repositories import (
    CharacterRepository,
    InventoryRepository,
    PrivacyRepository,
    StateCache,
    TradeRepository,
)
from mmorpg.domain.rules.group_commands import UNADDRESSED, GroupIntent, parse_group_command
from mmorpg.domain.rules.group_offers import Refusal
from mmorpg.logging import get_logger
from mmorpg.presentation.telegram.broadcast import chat_id_of
from mmorpg.presentation.telegram.cleanup import Deleter, MessageReaper
from mmorpg.presentation.telegram.messaging import send_group_reply
from mmorpg.presentation.telegram.screens.group import REFUSALS, GroupReply, render
from mmorpg.presentation.telegram.throttle import RateLimiter

logger = get_logger(__name__)

# Группы, уже записанные через ``announce_chat_id``. На процесс и только идентификаторы
# чатов - это записка тому, кто читает журнал, а не состояние, которым игра пользуется.
_SEEN_CHATS: set[int] = set()

# Ответы, закрывающие предложение, а значит, забирающие обе кнопки.
ANSWERS = (GroupIntent.ACCEPT, GroupIntent.DECLINE)
# Итоги, после которых не остаётся ничего висящего, чьи бы они ни были.
CLOSED = (GroupResult.OFFER_ACCEPTED, GroupResult.OFFER_DECLINED, GroupResult.REFUSED)


def build_router(reaper: MessageReaper, limiter: RateLimiter | None = None) -> Router:
    """Свежий роутер на приложение - см. ``handlers.creation.build_router``.

    Ограничитель принадлежит роутеру, а не хранилищу зависимостей: это состояние о
    поведении внутри процесса, а не служба, которая ещё кому-то нужна.
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
        privacy: PrivacyRepository,
        state_cache: StateCache,
    ) -> None:
        await handle_group_message(
            message,
            bot=bot,
            settings=settings,
            content=content,
            characters=characters,
            inventory=inventory,
            trades=trades,
            privacy=privacy,
            state_cache=state_cache,
            limiter=limits,
            reaper=reaper,
        )

    router.message.register(entry)
    return router


def is_game_group(chat: Chat, configured: str) -> bool:
    """*Та* ли это группа. Принимается числовой id или @username.

    ``*`` принимает любую группу. Кто-то должен иметь возможность попробовать
    групповую половину игры до того, как узнает id той группы, в которой стоит.
    """
    if configured.strip() == ANY_GROUP:
        return True
    wanted = chat_id_of(configured)
    if isinstance(wanted, int):
        return chat.id == wanted
    return bool(chat.username) and chat.username == wanted.lstrip("@")


def announce_chat_id(chat: Chat, configured: str) -> None:
    """Записать id группы, в которой бот состоит, но не отвечает.

    Единственным, чего не хватало для проверки команд в группе, был номер, которого
    никто не видит: id нет в интерфейсе клиента, а бот его знал и молчал. Один раз на
    чат за запуск, чтобы оживлённая группа не забила журнал одной и той же строкой.
    """
    if chat.id in _SEEN_CHATS:
        return
    _SEEN_CHATS.add(chat.id)
    logger.info(
        "group_not_configured",
        chat_id=chat.id,
        title=chat.title or "",
        configured=configured or "(пусто)",
        hint="поставьте это значение в GROUP_ID, или GROUP_ID=* для любой группы",
    )


async def handle_group_message(
    message: Message,
    *,
    bot: Bot,
    settings: Settings,
    content: GameContent,
    characters: CharacterRepository,
    inventory: InventoryRepository,
    trades: TradeRepository,
    privacy: PrivacyRepository,
    state_cache: StateCache,
    limiter: RateLimiter,
    reaper: MessageReaper,
    now: int | None = None,
) -> int | None:
    """Ответить на одно сообщение в группе или промолчать. Возвращает id отправленного."""
    author = message.from_user
    if message.text is None or author is None or author.is_bot:
        return None
    if not settings.group_chat_enabled or not is_game_group(message.chat, settings.group_id):
        announce_chat_id(message.chat, settings.group_id)
        return None

    command = parse_group_command(message.text)
    if command is None:
        return None

    answering = command.intent in ANSWERS
    target = message.reply_to_message
    target_user = target.from_user if target is not None else None
    if command.intent not in UNADDRESSED and (
        target is None or target_user is None or target_user.is_bot
    ):
        # Обращено не к игроку. Боту в этой фразе делать нечего.
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
        privacy=privacy,
        parties=PartyStore(state_cache),
        scope=str(message.chat.id),
    )
    outcome = await trade.run(
        command,
        author_id=author.id,
        target_id=target_user.id if target_user is not None else None,
        now=now if now is not None else int(time.time()),
    )
    reply = render(content, outcome)
    # Зов, о котором сказали только в группе, ждёт человека там, где он его
    # услышит: бот пишет позванному в личные сообщения, и там же тот отвечает.
    if outcome.result is GroupResult.PARTY_INVITED and outcome.invited_user_id:
        await _whisper(
            bot,
            outcome.invited_user_id,
            f"{outcome.author_name} зовёт вас в отряд. "
            "Наберите «/отряд принять», чтобы пойти вместе, или «/отряд отказать».",
        )

    # Предложение привязано к сообщению того, кому предложили, чтобы кнопки видел только
    # он; всё остальное отвечает тому, кто заговорил.
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


async def _whisper(bot: Bot, telegram_id: int, text: str) -> None:
    """Одна строка тому, кого позвали. Не дошло - значит не дошло.

    Зов остался лежать в хранилище, и в игре его всё равно видно: ронять ход
    того, кто звал, из-за закрытых личных сообщений не за что.
    """
    try:
        await bot.send_message(chat_id=telegram_id, text=text)
    except TelegramAPIError:
        logger.info("party_notice_undelivered", telegram_id=telegram_id)


async def _say(
    bot: Bot,
    message: Message,
    reply: GroupReply,
    *,
    anchor: int,
    reaper: MessageReaper,
    dismiss: bool = False,
) -> int:
    """Написать ответ и поставить его на часы (``cleanup.MessageReaper``)."""
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
    """Единственный вызов, который делает уборщик, привязанный к этому боту."""

    async def delete(chat_id: int, message_id: int) -> None:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)

    return delete
