"""The shared paginated list component."""

from __future__ import annotations

from mmorpg.presentation.telegram.keyboards.labels import (
    FILTERS,
    NEXT_PAGE,
    PREVIOUS_PAGE,
    RESET_FILTERS,
    SEARCH,
)
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.screens.paginated import (
    PAGE_SIZE,
    ListEntry,
    ListFilters,
    PageState,
    describe_selection,
    page_slice,
    paginated_screen,
    total_pages,
)


def entries(count: int) -> list[ListEntry]:
    return [ListEntry(key=f"e{index}", text=f"Запись {index}") for index in range(1, count + 1)]


def render(count: int, page: int = 1, **filters: object):
    return paginated_screen(
        screen_id=ScreenId.INVENTORY,
        title="Инвентарь",
        entries=entries(count),
        state=PageState(page=page, filters=ListFilters(**filters)),  # type: ignore[arg-type]
    )


def test_page_size_is_eight() -> None:
    assert PAGE_SIZE == 8
    assert total_pages(0) == 1
    assert total_pages(8) == 1
    assert total_pages(9) == 2
    assert total_pages(17) == 3


def test_one_entry_per_row() -> None:
    screen = render(20)
    entry_rows = screen.all_rows()[:PAGE_SIZE]
    assert all(len(row) == 1 for row in entry_rows)


def test_header_states_list_filters_count_and_page() -> None:
    """A player who hears only this message knows exactly where they are."""
    screen = render(20, page=2, category="Боевые")
    header = screen.text()
    assert header.startswith("Инвентарь.")
    assert "категория «Боевые»" in header
    assert "сортировка «по названию»" in header
    assert "Найдено 20" in header
    assert "страница 2 из 3" in header


def test_navigation_row_keeps_its_place_on_a_single_page() -> None:
    """Layout must not shift between a one-page list and a ten-page one."""
    single = render(3)
    many = render(30)
    assert single.all_rows()[-3][0] is PREVIOUS_PAGE
    assert many.all_rows()[-3][0] is PREVIOUS_PAGE
    assert single.all_rows()[-3][2] is NEXT_PAGE


def test_page_button_reports_the_position() -> None:
    screen = render(30, page=3)
    page_button = screen.all_rows()[-3][1]
    assert page_button.text == "Страница 3 из 4"


def test_filter_row_is_present() -> None:
    screen = render(30)
    filter_row = screen.all_rows()[-2]
    assert filter_row == (FILTERS, RESET_FILTERS, SEARCH)


def test_pages_slice_correctly() -> None:
    state = PageState(page=2)
    visible = page_slice(entries(20), state)
    assert [entry.text for entry in visible] == [f"Запись {index}" for index in range(9, 17)]


def test_page_numbers_are_clamped() -> None:
    assert PageState(page=99).clamped(3).page == 3
    assert PageState(page=0).clamped(3).page == 1
    assert PageState(page=1).moved(-1, 3).page == 1
    assert PageState(page=3).moved(1, 3).page == 3
    assert PageState(page=1).jumped(2, 3).page == 2


def test_empty_list_says_so_and_keeps_its_buttons() -> None:
    screen = render(0)
    assert "Здесь пока пусто." in screen.text()
    assert "Найдено 0" in screen.text()
    assert screen.all_rows()[-3][0] is PREVIOUS_PAGE


def test_filters_can_be_cleared_but_sorting_is_kept() -> None:
    filters = ListFilters(category="Боевые", query="меч", sort="по уровню")
    assert filters.active is True
    cleared = filters.cleared()
    assert cleared.active is False
    assert cleared.sort == "по уровню"


def test_selection_counter_is_spoken_not_drawn() -> None:
    assert describe_selection(1, 2) == "Выбрано: 1 из 2 позиций."
    assert "[" not in describe_selection(1, 2)


def test_metadata_exposes_the_page_for_the_handler() -> None:
    screen = render(30, page=2)
    assert screen.metadata == {"page": "2", "pages": "4", "count": "30"}
