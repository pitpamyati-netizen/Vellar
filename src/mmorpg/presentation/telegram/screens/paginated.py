"""The one paginated list used by every long list in the game.

Inventory, equipment, skills, traits, the shop, quests, the city list and the
location list all render through this component, so they behave identically: same
page size, same header wording, same navigation row in the same position
(spec section 13).

Layout, top to bottom:

    <title>. Найдено N записей, страница X из Y.
    [entry 1]                     one entry per row, 8 per page
    ...
    [Предыдущая страница] [Страница X из Y] [Следующая страница]
    [Фильтры] [Поиск] [Сбросить фильтры]
    [Назад] [Главное меню]

The entries come first and the machinery after them: a bag is opened to reach the
things in it, not to page through it. The paging row appears only when there is
more than one page, and the filter row only when the list is long enough to need
filtering or a filter is already on - on a list of three items both rows are
noise, and the entries themselves never move. "Фильтры" is there only where the
list has sections to cut it by, and "Сбросить фильтры" only where something is
actually filtered - the same rule, one row down.

A direction that leads nowhere is not shown. On page 8 of 8 there is no
"Следующая страница" and on page 1 there is no "Предыдущая": a button that
answers "вы уже в конце" is a button that wasted a press to say so.

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
from mmorpg.presentation.telegram.screens.format import MESSAGE_LIMIT, found, plural

PAGE_SIZE = 8

#: Запас под строку «Найдено N записей, страница X из Y.» - её длину надо знать
#: до того, как страницы посчитаны, иначе счёт ходит по кругу.
COUNTS_LINE = 48


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
        """The filters in words. Empty when nothing is filtered.

        The sort order used to be stated on every list header, including the ones
        nobody had filtered - a sentence the player had to hear past on every
        single page to reach the contents. It is said only when it is news.
        """
        if not self.active:
            return ""
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


def entry_line(entry: ListEntry) -> str:
    """Строка записи в теле сообщения. Пусто - у записи нечего рассказывать."""
    return f"{entry.text} — {entry.detail}" if entry.detail else ""


def entries_per_page(
    entries: Sequence[ListEntry],
    *,
    overhead: int = 0,
    page_size: int = PAGE_SIZE,
    limit: int = MESSAGE_LIMIT,
) -> int:
    """Сколько записей списка помещается в одно сообщение.

    Восемь записей - это потолок, а не обещание. У списка умений строка записи
    длиной под двести знаков, и восемь таких не влезали в предел сообщения: у
    страницы было восемь кнопок и пять описаний, а три умения игрок слышал
    только как кнопку - без того, что она делает и что стоит. Резать описание
    нельзя (оно и есть содержимое списка), молча терять хвост - тем более, и
    остаётся третье: на такой странице записей меньше.

    Число одно на весь список, по самой длинной его записи: страница из трёх
    записей и страница из восьми в одном списке означали бы, что «четвёртое
    умение» - это разное умение на разных страницах (правило доступности 7).
    """
    if not entries:
        return page_size
    longest = max((len(entry_line(entry)) for entry in entries), default=0)
    if longest <= 0:
        return page_size
    room = max(0, limit - max(0, overhead))
    return max(1, min(page_size, room // (longest + 1)))


def page_slice(
    entries: Sequence[ListEntry], state: PageState, page_size: int = PAGE_SIZE
) -> tuple[ListEntry, ...]:
    pages = total_pages(len(entries), page_size)
    current = state.clamped(pages).page
    start = (current - 1) * page_size
    return tuple(entries[start : start + page_size])


def page_label(page: int, pages: int) -> Label:
    return label(f"Страница {page} из {pages}")


def paging_row(page: int, pages: int) -> tuple[Label, ...]:
    """The paging row, with only the directions that lead somewhere.

    "Следующая страница" on the last page was a promise the list could not keep:
    the press did nothing and said nothing, which by ear is indistinguishable from
    the game having frozen.
    """
    row: list[Label] = []
    if page > 1:
        row.append(PREVIOUS_PAGE)
    row.append(page_label(page, pages))
    if page < pages:
        row.append(NEXT_PAGE)
    return tuple(row)


SEARCH_PROMPT = (
    "Наберите сообщением, что искать в списке. Одно слово или его часть. "
    "Чтобы отменить поиск, нажмите «Сбросить фильтры»."
)


def filters_screen(
    *,
    screen_id: ScreenId,
    title: str,
    categories: Sequence[str],
    current: ListFilters,
) -> Screen:
    """Разделы списка, по одному на кнопку. Выбранный назван словами.

    Фильтр здесь ровно один - раздел, - и это нарочно: список из шестидесяти
    особенностей режется по разделам, а всё остальное быстрее найти поиском.
    """
    chosen = current.category or "не выбран"
    lines = [
        f"{title}. Разделов: {len(categories)}.",
        f"Сейчас показан раздел: {chosen}.",
        "Нажмите раздел, чтобы оставить в списке только его.",
    ]
    if current.query:
        lines.append(f"Поиск: «{current.query}».")
    rows = [(label(name),) for name in categories]
    rows.append((RESET_FILTERS,))
    return Screen(id=screen_id, lines=tuple(lines), rows=tuple(rows))


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
    categories: Sequence[str] = (),
) -> Screen:
    """Render a list page as a screen.

    The header always says what the list is, which filters are on, how many
    entries matched and which page this is - so a player who hears only this
    message knows exactly where they are.
    """
    described = state.filters.describe()
    header = f"{title}. {described}." if described else f"{title}."
    # Сколько записей влезет, решается до нарезки: длинный список умений режется
    # по три на страницу, короткий инвентарь - по восемь, как и раньше.
    overhead = (
        len(header)
        + COUNTS_LINE
        + sum(len(line) + 1 for line in lead_lines)
        + (len(empty_text) + 1 if not entries else 0)
    )
    page_size = entries_per_page(
        entries, overhead=overhead, page_size=page_size, limit=MESSAGE_LIMIT
    )

    pages = total_pages(len(entries), page_size)
    current = state.clamped(pages)
    visible = page_slice(entries, current, page_size)

    counts = (
        f"{found(len(entries))}, страница {current.page} из {pages}."
        if pages > 1
        else f"{found(len(entries))}."
    )

    lines: list[str] = [header, counts, *lead_lines]
    if not entries:
        lines.append(empty_text)
    lines.extend(line for entry in visible if (line := entry_line(entry)))

    rows: list[tuple[Label, ...]] = [(entry.as_label(),) for entry in visible]
    # Everything below the entries is machinery, and machinery nobody needs is not
    # shown: one page means nothing to page through, and a list that fits on one
    # page with no filter on has nothing to filter down (accessibility rule 7 -
    # the entries above keep their positions either way).
    if pages > 1:
        rows.append(paging_row(current.page, pages))
    if show_filters and (pages > 1 or current.filters.active):
        row = (FILTERS, SEARCH) if categories else (SEARCH,)
        rows.append((*row, RESET_FILTERS) if current.filters.active else row)
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
