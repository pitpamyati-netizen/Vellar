"""World table invariants: 15 cities, 5 locations each, levels 1-300 without gaps."""

from __future__ import annotations

import itertools

from mmorpg.domain.entities import GameContent

MAX_LEVEL = 300


def test_fifteen_cities(content: GameContent) -> None:
    assert len(content.cities) == 15
    assert sorted(city.order for city in content.cities) == list(range(1, 16))


def test_each_city_has_five_locations(content: GameContent) -> None:
    for city in content.cities:
        assert len(city.locations) == 5, city.id
        assert [location.slot for location in city.locations] == [1, 2, 3, 4, 5]


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
        # The next city starts inside the previous band: there is always both
        # somewhere to push forward and somewhere to go back and farm.
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
