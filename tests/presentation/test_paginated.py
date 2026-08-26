"""Общая часть списка со страницами."""

from __future__ import annotations

from mmorpg.presentation.telegram.keyboards.labels import (
    FILTERS,
    NEXT_PAGE,
    PREVIOUS_PAGE,
    RESET_FILTERS,
    SEARCH,
)
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.screens.format import MESSAGE_LIMIT
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


def render(count: int, page: int = 1, categories: tuple[str, ...] = (), **filters: object):
    return paginated_screen(
        screen_id=ScreenId.INVENTORY,
        title="Инвентарь",
        entries=entries(count),
        state=PageState(page=page, filters=ListFilters(**filters)),  # type: ignore[arg-type]
        categories=categories,
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
    """Игрок, услышавший одно лишь это сообщение, точно знает, где он."""
    screen = render(20, page=2, category="Боевые")
    header = screen.text()
    assert header.startswith("Инвентарь.")
    assert "категория «Боевые»" in header
    assert "сортировка «по названию»" in header
    assert "Найдено 20" in header
    assert "страница 2 из 3" in header


def test_a_single_page_list_is_only_its_entries() -> None:
    """Листать нечего и отбирать не из чего: механика не показывается.

    Записи держат свои места - они выше всего, что появляется и исчезает, - и сумка
    из трёх вещей открывается прямо на эти три вещи.
    """
    single = render(3)
    assert [row[0].text for row in single.all_rows()[:3]] == [
        "Запись 1",
        "Запись 2",
        "Запись 3",
    ]
    assert len(single.all_rows()) == 4  # три записи плюс служебный ряд
    assert all(PREVIOUS_PAGE not in row for row in single.all_rows())

    many = render(30, page=2)
    assert many.all_rows()[-3][0] is PREVIOUS_PAGE
    assert many.all_rows()[-3][2] is NEXT_PAGE


def test_page_button_reports_the_position() -> None:
    screen = render(30, page=3)
    page_button = screen.all_rows()[-3][1]
    assert page_button.text == "Страница 3 из 4"


def test_a_direction_that_leads_nowhere_is_not_offered() -> None:
    """«Следующая страница» на последней странице была нажатием, не делавшим ничего."""
    first = render(30, page=1).all_rows()[-3]
    assert PREVIOUS_PAGE not in first
    assert NEXT_PAGE in first

    last = render(30, page=4).all_rows()[-3]
    assert PREVIOUS_PAGE in last
    assert NEXT_PAGE not in last
    assert last[-1].text == "Страница 4 из 4"


def test_search_is_offered_on_every_long_list() -> None:
    screen = render(30)
    assert screen.all_rows()[-2] == (SEARCH,)


def test_sections_are_offered_only_where_the_list_has_them() -> None:
    plain = render(30)
    assert FILTERS not in plain.all_rows()[-2]

    sectioned = render(30, categories=("Боевые", "Ремесленные"))
    assert sectioned.all_rows()[-2] == (FILTERS, SEARCH)


def test_reset_is_offered_only_when_something_is_filtered() -> None:
    assert RESET_FILTERS not in render(30).all_rows()[-2]
    assert RESET_FILTERS in render(30, query="меч").all_rows()[-2]


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


def test_empty_list_says_so() -> None:
    screen = render(0)
    assert "Здесь пока пусто." in screen.text()
    assert "Найдено 0" in screen.text()
    # Пустой список тоже отвечает, а дорога наружу - служебный ряд.
    assert [item.text for item in screen.all_rows()[-1]] == ["Назад", "Главное меню"]


def test_a_filtered_short_list_keeps_the_filter_row() -> None:
    """Тот, кто поставил отбор, обязан иметь возможность снять его, не уходя с экрана."""
    screen = render(3, category="Боевые")
    assert screen.all_rows()[-2] == (SEARCH, RESET_FILTERS)


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


# --- страница влезает в сообщение ------------------------------------


def _wordy(count: int, length: int = 190) -> list[ListEntry]:
    return [
        ListEntry(key=f"e{index}", text=f"Запись {index}", detail="о" * length)
        for index in range(1, count + 1)
    ]


def test_a_long_list_shortens_the_page_instead_of_losing_its_tail() -> None:
    """Восемь записей — потолок, а не обещание.

    Экран умений собирал восемь кнопок и печатал пять описаний: отправлялась
    первая страница текста в девятьсот знаков, а хвост пропадал молча — ни
    строки о том, что он был.
    """
    screen = paginated_screen(
        screen_id=ScreenId.SKILLS,
        title="Умения",
        entries=_wordy(14),
        state=PageState(),
    )
    assert len(screen.text()) <= MESSAGE_LIMIT
    assert screen.pages() == (screen.text(),)
    described = [line for line in screen.lines if "оооо" in line]
    buttons = [row for row in screen.rows if len(row) == 1 and "Запись" in row[0].text]
    assert len(described) == len(buttons)
    assert int(screen.metadata["pages"]) > 2


def test_every_page_of_one_list_holds_the_same_number_of_entries() -> None:
    """«Четвёртое умение» обязано быть одним и тем же на любой странице."""
    sizes = {
        len([row for row in render_wordy(page).rows if "Запись" in row[0].text])
        for page in (1, 2, 3)
    }
    assert len(sizes) == 1


def render_wordy(page: int):
    return paginated_screen(
        screen_id=ScreenId.SKILLS,
        title="Умения",
        entries=_wordy(15),
        state=PageState(page=page),
    )


def test_a_short_list_still_holds_eight() -> None:
    screen = paginated_screen(
        screen_id=ScreenId.INVENTORY,
        title="Инвентарь",
        entries=entries(20),
        state=PageState(),
    )
    assert screen.metadata["pages"] == "3"
