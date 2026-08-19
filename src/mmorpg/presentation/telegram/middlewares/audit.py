"""Строка на действие: кто, что нажал, чем это кончилось и за сколько.

Метрики (``mmorpg.metrics``) отвечают на вопрос «как игре живётся» — сколько
обновлений в минуту и какая задержка. На вопрос «что случилось у этого игрока
вчера вечером» они не отвечают вовсе, а именно он и приходит: у человека пропало
золото, экран не открылся, кнопка промолчала. Поэтому здесь пишется одна короткая
строка на каждое обновление, которое игра действительно обслужила::

    action who=4242 chat=private did=Атака result=ok ms=14

``result`` — исход, каким его видит игра: ``ok``, ``failed`` (упало и игрок
получил извинение), ``duplicate`` (Telegram прислал то же самое дважды),
``banned`` (аккаунт заблокирован), ``ignored`` (ни один роутер не взял кнопку —
для игрока это молчание, то есть ошибка, которую иначе никто не увидит).

Исход ставит не эта обёртка: она заводит :class:`Note`, кладёт её в данные
обновления, и каждый, кто обрывает путь до хендлера, отмечается в ней сам. Список
получается один и цельный вместо трёх разных строк об одном нажатии.

Чего здесь нет — переписки. В личке пишется каждое обновление: там всё сказанное
обращено к игре. В группе — только падение и закрытая дверь, потому что молчание
бота там норма, а разговор игроков между собой игру не касается. Текст обрезается
до :data:`MAX_TEXT` символов — кнопка в него влезает целиком, а свободный ввод
попадает в журнал ровно настолько, насколько нужно, чтобы понять действие.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.enums import ChatType
from aiogram.types import Message, TelegramObject, Update

from mmorpg.logging import KEPT_RESULTS, get_logger
from mmorpg.metrics import Stopwatch

logger = get_logger(__name__)

#: Под каким именем блокнот лежит в данных обновления.
KEY = "audit"

#: Сколько символов нажатия попадает в журнал.
MAX_TEXT = 40

OK = "ok"
FAILED = "failed"
IGNORED = "ignored"


@dataclass(slots=True)
class Note:
    """Блокнот одного обновления.

    Изменяемый объект, а не ключ в словаре: aiogram передаёт данные по цепочке
    распаковкой, поэтому словарь по дороге пересобирается, а вот значение в нём
    остаётся тем же самым — и отметка, поставленная где угодно ниже, доходит
    обратно сюда.
    """

    result: str = OK

    def done(self, result: str) -> None:
        """Отметить исход. Первая отметка сильнее прочих: она ближе к причине."""
        if self.result == OK:
            self.result = result


def note_of(data: dict[str, Any]) -> Note | None:
    """Блокнот текущего обновления, если журнал включён."""
    note = data.get(KEY)
    return note if isinstance(note, Note) else None


class AuditMiddleware(BaseMiddleware):
    """Пишет одну строку о каждом обслуженном обновлении."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        note = Note()
        data[KEY] = note
        watch = Stopwatch()
        try:
            result = await handler(event, data)
        except Exception:
            # Извинение игроку и трассировка — дело ``ErrorMiddleware``; здесь
            # только запись, и она нужна тем более, что дальше падение летит
            # наружу.
            note.done(FAILED)
            self._write(event, note, watch)
            raise
        if result is UNHANDLED:
            note.done(IGNORED)
        self._write(event, note, watch)
        return result

    def _write(self, event: TelegramObject, note: Note, watch: Stopwatch) -> None:
        message = _message_of(event)
        if message is None or message.from_user is None:
            return
        private = message.chat.type == ChatType.PRIVATE
        if not private and note.result not in KEPT_RESULTS:
            # Разговор игроков между собой игру не касается, а неотвеченное в
            # группе — это норма, а не находка: бот там молчит на всё, что к нему
            # не обращено (``Claude.md``, правило 9). Пишется только то, что и так
            # хранится вечно: падение и закрытая дверь.
            return
        logger.info(
            "action",
            who=message.from_user.id,
            chat="private" if private else "group",
            did=_did(message),
            result=note.result,
            ms=round(watch.seconds * 1000),
        )


def _message_of(event: TelegramObject) -> Message | None:
    if isinstance(event, Message):
        return event
    if isinstance(event, Update):
        return event.message or event.edited_message
    return None


def _did(message: Message) -> str:
    """Что игрок сделал, в одну строку.

    Нажатие — это текст кнопки, и он же текстовая команда (правило доступности
    5), поэтому больше ничего и не нужно. Всё нетекстовое называется словом:
    игра не понимает ни картинок, ни голосовых, но знать, что их присылают,
    полезно.
    """
    text = (message.text or "").strip()
    if not text:
        return message.content_type
    return text[:MAX_TEXT]
