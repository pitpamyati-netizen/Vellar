"""Основа мира: 15 городов, по 5 локаций, уровни 1-150 без пробелов."""

from __future__ import annotations

import itertools

from mmorpg.domain.entities import GameContent

MAX_LEVEL = 150


def test_fifteen_cities(content: GameContent) -> None:
    assert len(content.cities) == 15
    assert sorted(city.order for city in content.cities) == list(range(1, 16))


def test_each_city_has_five_locations(content: GameContent) -> None:
    for city in content.cities:
        assert len(city.locations) == 5, city.id
        assert [location.slot for location in city.locations] == [1, 2, 3, 4, 5]


def test_each_city_has_one_deep_dungeon_at_its_ceiling(content: GameContent) -> None:
    """Ровно одно глубокое подземелье, названное, на верху полосы города (ADR 0041)."""
    for city in content.cities:
        deep = [one for one in city.dungeons if one.deep]
        assert len(deep) == 1, city.id
        assert deep[0].name and deep[0].flavour, city.id
        assert deep[0].level == city.level_max, city.id
        assert city.deep_dungeon is deep[0]


def test_each_city_has_at_least_four_regular_dungeons(content: GameContent) -> None:
    for city in content.cities:
        assert len(city.regular_dungeons) >= 4, city.id


def test_regular_dungeon_levels_span_the_city_band(content: GameContent) -> None:
    """Список подземелий покрывает полосу города, а не пару точек (ADR 0041)."""
    for city in content.cities:
        levels = sorted(one.level for one in city.regular_dungeons)
        band = city.level_max - city.level_min
        step = max(1, band // len(levels))
        assert levels[0] <= city.level_min + 2 * step, city.id
        assert levels[-1] >= city.level_max - 2 * step, city.id
        for lower, higher in itertools.pairwise(levels):
            assert higher - lower <= 2 * step, city.id


def test_every_dungeon_level_sits_inside_its_city_band(content: GameContent) -> None:
    for city in content.cities:
        for one in city.dungeons:
            assert city.level_min <= one.level <= city.level_max, one.id


def test_dungeon_ids_are_globally_unique(content: GameContent) -> None:
    ids = [one.id for city in content.cities for one in city.dungeons]
    assert len(ids) == len(set(ids))


def test_every_level_is_covered(content: GameContent) -> None:
    covered: set[int] = set()
    for city in content.cities:
        for location in city.locations:
            covered.update(range(location.level_min, location.level_max + 1))
    assert sorted(set(range(1, MAX_LEVEL + 1)) - covered) == []


def test_locations_have_no_gaps_and_increase(content: GameContent) -> None:
    for city in content.cities:
        for earlier, later in zip(city.locations, city.locations[1:], strict=False):
            assert later.level_min <= earlier.level_max, f"gap in {city.id}"
            assert later.level_min > earlier.level_min, f"{city.id} not monotonic"
            assert later.level_max > earlier.level_max, f"{city.id} not monotonic"


def test_city_bands_overlap_and_increase(content: GameContent) -> None:
    ordered = sorted(content.cities, key=lambda city: city.order)
    for earlier, later in itertools.pairwise(ordered):
        assert later.level_min > earlier.level_min
        assert later.level_max > earlier.level_max
        # Следующий город начинается внутри полосы предыдущего: всегда есть и куда
        # рваться вперёд, и куда вернуться и подкопить.
        assert later.level_min < earlier.level_max


def test_city_band_matches_its_locations(content: GameContent) -> None:
    for city in content.cities:
        assert city.locations[0].level_min == city.level_min
        assert city.locations[-1].level_max == city.level_max


def test_first_city_starts_at_level_one(content: GameContent) -> None:
    first = content.city_by_order(1)
    assert first.level_min == 1
    assert first.unlock_level == 1
    assert first.unlock_requires == ()


def test_last_city_reaches_max_level(content: GameContent) -> None:
    assert content.city_by_order(15).level_max == MAX_LEVEL


def test_unlock_requirements_point_at_known_cities(content: GameContent) -> None:
    known = {city.id for city in content.cities}
    for city in content.cities:
        for requirement in city.unlock_requires:
            assert requirement.removeprefix("city:") in known


def test_cities_available_at_level_one(content: GameContent) -> None:
    available = content.cities_available_at(1)
    assert [city.id for city in available] == ["farhold"]
