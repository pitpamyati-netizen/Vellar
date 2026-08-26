"""Посты в канал игры.

Канал несёт **новости об игре**: что изменилось, что открылось, что задумано, и
служебные объявления. Это не лента того, что делали игроки: ни взятых уровней, ни
побед, ни сделок. Им место в группе, где сидят те, кого они касаются. Правила о
словах живут в ``Narrative.md`` («Канал»); те, что можно закрепить кодом, живут
здесь.

Список изменений — это новость, а не список коммитов: он говорит, что игрок
теперь может сделать, и никогда — какой модуль изменился.

Что закреплено здесь:

- заголовок — это всё сообщение для того, кто остановился после первой строки;
- только чистый текст, ``parse_mode=None``: канал читают и экранные дикторы, а
  разметку они произносят вслух (правило доступности 14);
- никакой псевдографики, никаких полос, числа вызывающий пишет как ``X из Y``
  (правило 5);
- не больше одного значка, в начале строки, выбранного по смыслу, а не для
  украшения (правило 6: текст однозначен со снятыми значками);
- жёсткий предел длины, потому что пост в канале на страницы не режется;
- слова игрока, а не команды, которая игру выпускает: пост, называющий модуль,
  коммит или базу, отвергается на отрисовке, до того как его кто-нибудь увидит.

Текст обновления живёт в ``content/changelog.toml`` и читается
``scripts/broadcast.py``; этот модуль только превращает его в пост.

Объявление никогда не блокирует и не ломает игру: несостоявшаяся отправка
пишется в журнал и отбрасывается.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from mmorpg.logging import get_logger

logger = get_logger(__name__)

BROADCAST_LIMIT = 700
# Список обновлений заслуживает больше места, чем объявление: обрезать его значило бы
# разорвать одно обновление на два поста.
CHANGELOG_LIMIT = 2000

# Слова, выдающие пост, написанный для своих, а не для игроков. По названию модуля игрок
# не может сделать ничего, а значит, это не новость: тот же самый факт всегда можно
# сказать как то, что игрок теперь может сделать (``Narrative.md``, раздел 8). Основы
# сверяются с начала слова и достаточно длинны, чтобы быть однозначными; короткие слова
# сверяются целиком, иначе «баг» пометил бы «Багровый».
JARGON_STEMS = (
    "модул",
    "коммит",
    "рефактор",
    "хендлер",
    "хэндлер",
    "эндпоинт",
    "деплой",
    "миграц",
    "бэкенд",
    "бекенд",
    "фронтенд",
    "конфиг",
    "багфикс",
    "хотфикс",
    "postgres",
    "backend",
    "frontend",
    "refactor",
    "endpoint",
    "hotfix",
    "bugfix",
)
JARGON_WORDS = frozenset(
    {"баг", "баги", "багов", "багам", "багами", "фикс", "фиксы", "патч", "патчи", "api", "sql"}
)
_WORD = re.compile(r"[a-zа-яё]+")


class BroadcastKind(StrEnum):
    """Какого рода эта новость. Род выбирает значок и предел длины."""

    NEWS = "news"
    CHANGELOG = "changelog"
    SERVICE = "service"


EMOJI: dict[BroadcastKind, str] = {
    BroadcastKind.NEWS: "📣",
    BroadcastKind.CHANGELOG: "🧾",
    BroadcastKind.SERVICE: "🔔",
}


def limit_for(kind: BroadcastKind) -> int:
    return CHANGELOG_LIMIT if kind is BroadcastKind.CHANGELOG else BROADCAST_LIMIT


@dataclass(frozen=True, slots=True)
class BroadcastEvent:
    """Один пост в канал: заголовок и, если есть, подробности за ним."""

    kind: BroadcastKind
    headline: str
    details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.headline.strip():
            msg = "a broadcast without a headline says nothing"
            raise ValueError(msg)


def jargon_in(text: str) -> str | None:
    """Первое слово, которое выдало бы в посте сообщение коммита."""
    words: list[str] = _WORD.findall(text.lower())
    for word in words:
        if word in JARGON_WORDS or word.startswith(JARGON_STEMS):
            return word
    return None


def render_broadcast(event: BroadcastEvent, *, emoji: bool = True) -> str:
    """Собрать пост. Первая строка стоит сама по себе, подробности идут следом."""
    head = event.headline.strip()
    if emoji:
        head = f"{EMOJI[event.kind]} {head}"
    lines = [head, *(detail.strip() for detail in event.details if detail.strip())]
    text = "\n".join(lines)
    limit = limit_for(event.kind)
    if len(text) > limit:
        msg = f"broadcast is {len(text)} characters, limit is {limit}"
        raise ValueError(msg)
    offender = jargon_in(text)
    if offender is not None:
        msg = f"the channel says what a player can do, not {offender!r}"
        raise ValueError(msg)
    return text


class ChannelSink(Protocol):
    """Единственный вызов Telegram, который делает вещатель. ``aiogram.Bot`` ему отвечает."""

    async def send_message(self, chat_id: int | str, text: str) -> object: ...


def chat_id_of(raw: str) -> int | str:
    """``-1001234567890`` становится числом, ``@vellar`` остаётся строкой."""
    value = raw.strip()
    try:
        return int(value)
    except ValueError:
        return value


@dataclass(slots=True)
class ChannelBroadcaster:
    """Пишет события в канал игры или в никуда, когда канал не настроен.

    Ненастроенный канал - обычное состояние локального запуска, поэтому здесь ничего
    не делают, а не отказывают: играть игра обязана и без канала.
    """

    sink: ChannelSink | None
    chat_id: str = ""
    emoji: bool = True

    @property
    def enabled(self) -> bool:
        return self.sink is not None and bool(self.chat_id.strip())

    async def announce(self, event: BroadcastEvent) -> bool:
        """Опубликовать одно событие. Возвращает, дошло ли оно до Telegram."""
        text = render_broadcast(event, emoji=self.emoji)
        if not self.enabled or self.sink is None:
            logger.debug("broadcast_skipped", kind=event.kind.value, reason="no_channel")
            return False
        try:
            await self.sink.send_message(chat_id_of(self.chat_id), text)
        # Широко нарочно: мёртвый канал, отобранное право администратора или сбой сети
        # не должны ронять тот ход, который породил событие.
        except Exception as error:
            logger.warning("broadcast_failed", kind=event.kind.value, error=str(error))
            return False
        logger.info("broadcast_sent", kind=event.kind.value)
        return True


# --- что канал на самом деле публикует -------------------------------


def news(headline: str, *details: str) -> BroadcastEvent:
    """Новость игры: что-то открылось, изменилось или скоро будет."""
    return BroadcastEvent(kind=BroadcastKind.NEWS, headline=headline, details=details)


def service(headline: str, *details: str) -> BroadcastEvent:
    """Служебное объявление: работы, простой, перезапуск."""
    return BroadcastEvent(kind=BroadcastKind.SERVICE, headline=headline, details=details)


def changelog(
    version: str,
    *,
    headline: str = "",
    added: tuple[str, ...] = (),
    changed: tuple[str, ...] = (),
    fixed: tuple[str, ...] = (),
) -> BroadcastEvent:
    """Обновление, написанное для игроков.

    Каждая запись - то, что игрок теперь может сделать или увидит, и никогда не
    модуль, не функция и не коммит. Пустые разделы выбрасываются, а не остаются
    заголовками, под которыми ничего нет, потому что заголовок экранный диктор всё
    равно прочитает.

    Заголовок пишется в ``content/changelog.toml`` и говорит, о чём обновление,
    потому что для того, кто остановился после первой строки, она и есть весь пост.
    Обновление, которое заголовка не написало, откатывается к голому номеру
    версии.
    """
    details: list[str] = []
    for title, entries in (("Добавлено", added), ("Изменилось", changed), ("Исправлено", fixed)):
        if not entries:
            continue
        details.append(f"{title}:")
        details.extend(f"— {entry}" for entry in entries)
    if not details:
        msg = "a changelog with no entries is not an update"
        raise ValueError(msg)
    return BroadcastEvent(
        kind=BroadcastKind.CHANGELOG,
        headline=headline.strip() or f"Обновление {version}.",
        details=tuple(details),
    )
