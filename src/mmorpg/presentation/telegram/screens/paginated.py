"""The one paginated list used by every long list in the game.

Inventory, equipment, skills, traits, the shop, quests, the city list and the
location list all render through this component, so they behave identically: same
page size, same header wording, same navigation row in the same position
(spec section 13).

Layout, top to bottom:

    <title>. <filters>. Найдено N записей, страница X из Y.
    [entry 1]                     one entry per row, 8 per page
    ...
    [Предыдущая страница] [Страница X из Y] [Следующая страница]
    [Фильтры] [Сбросить фильтры] [Поиск]
    [Назад] [Осмотреться] [Главное меню]

The page button is not decoration: pressing it asks for a page number, which is
the fastest way to move through a long list by ear.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from mmorpg.presentation.telegram.keyboards.labels import (
    FILTERS,
    NEXT_PAGE,
    PREVIOUS_PAGE,
    RESET_FILTERS,
    SEARCH,
    Label,
    label,
)
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import found, plural

PAGE_SIZE = 8


@dataclass(frozen=True, slots=True)
class ListEntry:
    """One row of a list: its button label and the id it maps back to."""

    key: str
    text: str
    detail: str = ""

    def as_label(self, *, prefix: str = "") -> Label:
        return label(f"{prefix}{self.text}")


@dataclass(frozen=True, slots=True)
class ListFilters:
    """Filter and sort state. Lives in FSM data, so it survives leaving the screen."""

    category: str = ""
    query: str = ""
    level_min: int = 0
    level_max: int = 0
    rarity: str = ""
    available_only: bool = False
    sort: str = "по названию"

    @property
    def active(self) -> bool:
        return bool(
            self.category
            or self.query
            or self.level_min
            or self.level_max
            or self.rarity
            or self.available_only
        )

    def describe(self) -> str:
        parts: list[str] = []
        if self.category:
            parts.append(f"категория «{self.category}»")
        if self.query:
            parts.append(f"поиск «{self.query}»")
        if self.rarity:
            parts.append(f"редкость «{self.rarity}»")
        if self.level_min or self.level_max:
            parts.append(f"уровни с {self.level_min or 1} по {self.level_max or 300}")
        if self.available_only:
            parts.append("только доступное мне")
        parts.append(f"сортировка «{self.sort}»")
        return ", ".join(parts)

    def cleared(self) -> ListFilters:
        return ListFilters(sort=self.sort)


@dataclass(frozen=True, slots=True)
class PageState:
    """Where the player is in a list."""

    page: int = 1
    filters: ListFilters = field(default_factory=ListFilters)

    def clamped(self, total_pages: int) -> PageState:
        return replace(self, page=max(1, min(self.page, max(1, total_pages))))

    def moved(self, delta: int, total_pages: int) -> PageState:
        return replace(self, page=self.page + delta).clamped(total_pages)

    def jumped(self, page: int, total_pages: int) -> PageState:
        return replace(self, page=page).clamped(total_pages)


def total_pages(count: int, page_size: int = PAGE_SIZE) -> int:
    return max(1, -(-count // page_size))


def page_slice(
    entries: Sequence[ListEntry], state: PageState, page_size: int = PAGE_SIZE
) -> tuple[ListEntry, ...]:
    pages = total_pages(len(entries), page_size)
    current = state.clamped(pages).page
    start = (current - 1) * page_size
    return tuple(entries[start : start + page_size])


def page_label(page: int, pages: int) -> Label:
    return label(f"Страница {page} из {pages}")


def paginated_screen(
    *,
    screen_id: ScreenId,
    title: str,
    entries: Sequence[ListEntry],
    state: PageState,
    page_size: int = PAGE_SIZE,
    extra_rows: Sequence[tuple[Label, ...]] = (),
    empty_text: str = "Здесь пока пусто.",
    lead_lines: Sequence[str] = (),
    show_filters: bool = True,
) -> Screen:
    """Render a list page as a screen.

    The header always says what the list is, which filters are on, how many
    entries matched and which page this is - so a player who hears only this
    message knows exactly where they are.
    """
    pages = total_pages(len(entries), page_size)
    current = state.clamped(pages)
    visible = page_slice(entries, current, page_size)

    header = f"{title}. {current.filters.describe()}."
    counts = f"{found(len(entries))}, страница {current.page} из {pages}."

    lines: list[str] = [header, counts, *lead_lines]
    if not entries:
        lines.append(empty_text)
    for entry in visible:
        if entry.detail:
            lines.append(f"{entry.text} — {entry.detail}")

    rows: list[tuple[Label, ...]] = [(entry.as_label(),) for entry in visible]
    # The navigation row keeps its position even on a single page, so the layout
    # never shifts under the player's fingers (accessibility rule 7).
    rows.append((PREVIOUS_PAGE, page_label(current.page, pages), NEXT_PAGE))
    if show_filters:
        rows.append((FILTERS, RESET_FILTERS, SEARCH))
    rows.extend(extra_rows)

    return Screen(
        id=screen_id,
        lines=tuple(lines),
        rows=tuple(rows),
        metadata={"page": str(current.page), "pages": str(pages), "count": str(len(entries))},
    )


def describe_selection(chosen: int, required: int) -> str:
    """ "Выбрано: 1 из 2." - stated in words, never as a progress bar."""
    return f"Выбрано: {chosen} из {required} {plural(required, 'позиции', 'позиций', 'позиций')}."
