"""Formatting helpers for screen text.

Two rules drive everything here (``docs/accessibility.md``):

- **no pseudo-graphics** - never ``[####----]``, always "42 из 120, это 35 процентов";
- **the key fact first** - the caller composes lines, these helpers keep each one
  short and speakable.
"""

from __future__ import annotations

MESSAGE_LIMIT = 900


def amount(current: int, maximum: int, *, with_percent: bool = True) -> str:
    """Render a bar-like value as speech: ``42 из 120, это 35 процентов``."""
    if maximum <= 0:
        return f"{current}"
    if not with_percent:
        return f"{current} из {maximum}"
    percent = round(current * 100 / maximum)
    return f"{current} из {maximum}, это {percent} процентов"


def percent(value: float) -> str:
    """A percentage as words, with an explicit sign for penalties."""
    rounded = round(value, 1)
    whole = int(rounded) if rounded == int(rounded) else rounded
    if value < 0:
        return f"минус {abs(whole)} процентов"
    return f"{whole} процентов"


def plural(count: int, one: str, few: str, many: str) -> str:
    """Russian pluralisation: 1 ход, 2 хода, 5 ходов."""
    tail_two = abs(count) % 100
    tail = abs(count) % 10
    if 11 <= tail_two <= 14:
        return many
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def turns(count: int) -> str:
    return f"{count} {plural(count, 'ход', 'хода', 'ходов')}"


def items(count: int) -> str:
    return f"{count} {plural(count, 'предмет', 'предмета', 'предметов')}"


def found(count: int) -> str:
    return f"Найдено {count} {plural(count, 'запись', 'записи', 'записей')}"


def gold(value: int) -> str:
    return f"{value} {plural(value, 'золотой', 'золотых', 'золотых')}"


def paginate_text(text: str, limit: int = MESSAGE_LIMIT) -> tuple[str, ...]:
    """Split an over-long message on line boundaries.

    Used only for content that genuinely cannot be shortened; screens are expected
    to page their data instead (accessibility rule 11).
    """
    if len(text) <= limit:
        return (text,)

    pages: list[str] = []
    current: list[str] = []
    length = 0
    for line in text.split("\n"):
        addition = len(line) + 1
        if length + addition > limit and current:
            pages.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += addition
    if current:
        pages.append("\n".join(current))
    return tuple(pages)


def join_lines(*lines: str) -> str:
    """Drop empty entries and join with single newlines."""
    return "\n".join(line for line in lines if line)
