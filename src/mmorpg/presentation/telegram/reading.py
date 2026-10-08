"""Полный длинный экран читается по запросу, с сохранёнными действиями."""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from dataclasses import replace

from pydantic import TypeAdapter

from mmorpg.domain.ports.repositories import StateCache
from mmorpg.presentation.telegram.keyboards.labels import label
from mmorpg.presentation.telegram.screens.base import Screen
from mmorpg.presentation.telegram.screens.format import MESSAGE_LIMIT, paginate_text

reader_cache: ContextVar[StateCache | None] = ContextVar("reader_cache", default=None)
SCREEN_CODEC = TypeAdapter(Screen)
READ_COMMAND = re.compile(r"^/(?:текст|text)\s+(\d+)$", re.IGNORECASE)
NEXT = "Читать дальше"
PREVIOUS = "Предыдущая часть"


def requested_number(text: str) -> int:
    """Невероятно длинный номер тоже ведёт на доступную часть, без переполнения."""
    value = text.lstrip("0")
    return 1_000_000 if len(value) > 6 else max(1, int(value or "0"))


def key(bot_id: int, chat_id: int) -> str:
    return f"screen-reading:{bot_id}:{chat_id}"


def parts(screen: Screen) -> tuple[str, ...]:
    # Заголовок части и подсказки тоже должны умещаться; UTF-16 используется
    # консервативно, чтобы имена с символами вне BMP не превышали лимит Telegram.
    limit = (
        (MESSAGE_LIMIT - 160) // 2
        if any(ord(c) > 0xFFFF for c in screen.text())
        else MESSAGE_LIMIT - 160
    )
    return paginate_text(screen.text(), limit)


def page(screen: Screen, number: int) -> Screen:
    pages = parts(screen)
    if len(pages) == 1:
        return screen
    number = min(max(number, 1), len(pages))
    navigation = []
    if number > 1:
        navigation.append(label(PREVIOUS))
    if number < len(pages):
        navigation.append(label(NEXT))
    rows = screen.rows if number == len(pages) else ()
    if navigation:
        rows = (*rows, tuple(navigation))
    return replace(
        screen,
        lines=(
            f"Часть {number} из {len(pages)}.",
            pages[number - 1],
            f"Чтение: /текст {number + 1 if number < len(pages) else 1}. "
            "Действия экрана — в последней части.",
        ),
        rows=rows,
    )


async def prepare(bot_id: int, chat_id: int, screen: Screen, emoji: bool) -> Screen:
    cache = reader_cache.get()
    if len(parts(screen)) > 1:
        if cache is None:
            raise RuntimeError("A long screen requires the reading middleware")
        await cache.set(
            key(bot_id, chat_id),
            json.dumps(
                {
                    "screen": SCREEN_CODEC.dump_python(screen, mode="json"),
                    "number": 1,
                    "emoji": emoji,
                },
                ensure_ascii=False,
            ),
            ttl=365 * 24 * 60 * 60,
        )
        return page(screen, 1)
    if cache is not None:
        await cache.delete(key(bot_id, chat_id))
    return screen
