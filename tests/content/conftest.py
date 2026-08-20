"""Content is loaded once per test session - it is immutable, so sharing is safe."""

from __future__ import annotations

# The black list from Narrative.md, section 2, as stems: one word here is enough
# to make a name sound like the generic fantasy set the world is written against.
#
# «легендарн» ушло из списка сознательно: это ступень редкости, а не краска на
# названии. Игрок читает её на карточке рядом с «обычный» и «редкий» — там это
# слово интерфейса, которое он знает по любой другой игре, и заменять его на
# синоним значило бы объяснять заново то, что и так понятно. Запрет остаётся в
# силе для всего, что зовётся легендарным ради красоты: имён, описаний, текстов.
FORBIDDEN_WORDS = (
    "вечн",
    "древн",
    "проклят",
    "тёмн властелин",
    "кровав",
    "бездн",
    "пустот",
    "реальност",
    "судьб",
    "избранн",
)
