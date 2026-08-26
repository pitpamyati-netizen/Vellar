"""Разбор того, что игрок набрал в игровой группе.

У группы нет ни экранов, ни состояния: сообщение либо является командой,
направленной другому игроку, либо не касается бота вовсе. Поэтому разборщик строг
и молчалив: он возвращает ``None`` на всё, чего не узнал, а хендлер не говорит
ничего вместо того, чтобы отвечать на чужие разговоры.

Грамматика (``Narrative.md``, раздел 9), всегда ответом на сообщение адресата:

    профиль
    продать <цена> <предмет>
    купить  <цена> <предмет>
    передать <предмет>
    передать <количество> <предмет>
    передать <количество> золота
    блок
    разблок
    принять <номер>
    отказ    <номер>

Две команды говорят об отправителе, а не о том, кому он отвечает, поэтому ответ
им не нужен вовсе — это переключатель приватности (Roadmap 2.5):

    скрыть профиль
    открыть профиль

Вещь пишут так, как её называет игрок, поэтому сверка не различает регистр,
не различает ``ё`` и схлопывает пробелы. Разрешается вещь по сумке говорящего
вызывающим, а не здесь: этот модуль знает грамматику, а не товар.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

MAX_PRICE = 1_000_000
MAX_QUANTITY = 999


class GroupIntent(StrEnum):
    """Чего попросил игрок."""

    PROFILE = "profile"
    SELL = "sell"
    BUY = "buy"
    GIVE_ITEM = "give_item"
    GIVE_GOLD = "give_gold"
    ACCEPT = "accept"
    DECLINE = "decline"
    BLOCK = "block"
    UNBLOCK = "unblock"
    HIDE_PROFILE = "hide_profile"
    SHOW_PROFILE = "show_profile"


# Команды о самом отправителе, а не о том, кому он ответил. Только они и значат что-то,
# выкрикнутые в комнату (``Narrative.md``, раздел 9).
UNADDRESSED = frozenset(
    {GroupIntent.ACCEPT, GroupIntent.DECLINE, GroupIntent.HIDE_PROFILE, GroupIntent.SHOW_PROFILE}
)


@dataclass(frozen=True, slots=True)
class GroupCommand:
    """Разобранная команда. ``amount`` - это цена, число вещей или номер предложения."""

    intent: GroupIntent
    amount: int = 0
    item_query: str = ""


# Written as a player writes it: no slash required, "ё" optional, any spacing.
_VERBS: dict[str, GroupIntent] = {
    "профиль": GroupIntent.PROFILE,
    "продать": GroupIntent.SELL,
    "купить": GroupIntent.BUY,
    "передать": GroupIntent.GIVE_ITEM,
    "принять": GroupIntent.ACCEPT,
    "отказ": GroupIntent.DECLINE,
    "блок": GroupIntent.BLOCK,
    "разблок": GroupIntent.UNBLOCK,
}
# Говорится двумя словами, и только этими двумя: фраза сверяется целиком, поэтому
# «скрыть» в одиночку остаётся чьей-то репликой, а не становится командой.
_PHRASES: dict[str, GroupIntent] = {
    "скрыть профиль": GroupIntent.HIDE_PROFILE,
    "открыть профиль": GroupIntent.SHOW_PROFILE,
    "снять блок": GroupIntent.UNBLOCK,
}
# Команды, после глагола которых не идёт ничего.
_BARE = frozenset({GroupIntent.PROFILE, GroupIntent.BLOCK, GroupIntent.UNBLOCK})
_GOLD_WORDS = frozenset({"золота", "золото", "золотых"})
_SPACES = re.compile(r"\s+")


def normalise(text: str) -> str:
    """В нижний регистр, ``ё`` в ``е``, один пробел между словами."""
    return _SPACES.sub(" ", text.replace("ё", "е").replace("Ё", "Е").strip().lower())


def parse_group_command(text: str) -> GroupCommand | None:
    """Разобрать одно сообщение в группе или ответить ``None``, если это не команда боту."""
    cleaned = normalise(text).removeprefix("/")
    if not cleaned:
        return None

    phrase = _PHRASES.get(cleaned)
    if phrase is not None:
        return GroupCommand(intent=phrase)

    verb, _, tail = cleaned.partition(" ")
    intent = _VERBS.get(verb)
    if intent is None:
        return None
    if intent in _BARE:
        # «профиль» и больше ничего: аргумент значит, что игрок имел в виду то, чего бот
        # не делает, а угадывать было бы хуже.
        return GroupCommand(intent=intent) if not tail else None

    match intent:
        case GroupIntent.SELL | GroupIntent.BUY:
            return _parse_offer(intent, tail)
        case GroupIntent.GIVE_ITEM:
            return _parse_give(tail)
        case GroupIntent.ACCEPT | GroupIntent.DECLINE:
            return _parse_number_only(intent, tail)
        case _:
            # Больше от глагола ничего не идёт: золото — это одна из форм «передать», а
            # переключатель приватности — фраза.
            return None


def _parse_offer(intent: GroupIntent, tail: str) -> GroupCommand | None:
    """``продать 100 кожаная броня`` - сначала цена, потом вещь."""
    price_text, _, item = tail.partition(" ")
    price = _as_int(price_text, ceiling=MAX_PRICE)
    if price is None or price <= 0 or not item.strip():
        return None
    return GroupCommand(intent=intent, amount=price, item_query=item.strip())


def _parse_give(tail: str) -> GroupCommand | None:
    """``передать`` принимает вещь, вещь со счётом или золото."""
    first, _, rest = tail.partition(" ")
    if not first:
        return None

    # Золото считают тысячами, вещи - штуками, поэтому первое число читается по широкому
    # потолку и сужается, когда стало ясно, чем оно было.
    count = _as_int(first, ceiling=MAX_PRICE)
    if count is None:
        # Числа нет вовсе: весь хвост - это вещь, и она одна.
        return GroupCommand(intent=GroupIntent.GIVE_ITEM, amount=1, item_query=tail.strip())
    if count <= 0:
        return None

    item = rest.strip()
    if not item:
        # Голое число — это не золото: «передать 100» с тем же успехом может быть
        # оговоркой, а тихо двигать деньги по оговорке — ровно та ошибка, которой стоит
        # избежать.
        return None
    if item in _GOLD_WORDS:
        return GroupCommand(intent=GroupIntent.GIVE_GOLD, amount=count)
    if count > MAX_QUANTITY:
        return None
    return GroupCommand(intent=GroupIntent.GIVE_ITEM, amount=count, item_query=item)


def _parse_number_only(intent: GroupIntent, tail: str) -> GroupCommand | None:
    number = _as_int(tail.strip(), ceiling=MAX_PRICE)
    if number is None or number <= 0:
        return None
    return GroupCommand(intent=intent, amount=number)


def _as_int(text: str, *, ceiling: int) -> int | None:
    """Только цифры: ``100р``, ``1e3`` и ``-5`` - не те числа, которые игрок имел в виду."""
    if not text.isdecimal():
        return None
    value = int(text)
    return value if value <= ceiling else None
