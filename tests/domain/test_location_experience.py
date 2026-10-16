"""Опыт начальных мест: один фонд, затем доли и личная прибавка."""

from dataclasses import replace

import pytest

from mmorpg.domain.entities import GameContent
from mmorpg.domain.rules.adventure import location_experience
from mmorpg.infrastructure.content.loader import _parse_world, _validate_world


def test_only_starting_locations_have_ten_percent_bonus(content: GameContent) -> None:
    for city in content.cities:
        for location in city.locations:
            expected = 110 if city.order == 1 else 100
            assert (
                location_experience(content, 100, city_id=city.id, location_slot=location.slot)
                == expected
            )


@pytest.mark.parametrize("amount,expected", [(0, 0), (1, 1), (12, 13), (22, 24), (100, 110)])
def test_bonus_rounds_once_and_does_not_create_experience(
    content: GameContent, amount: int, expected: int
) -> None:
    assert location_experience(content, amount, city_id="farhold", location_slot=1) == expected


@pytest.mark.parametrize("city_id,slot", [("", 0), ("", 1), ("farhold", 0), ("farhold", 99)])
def test_without_a_location_experience_is_unchanged(
    content: GameContent, city_id: str, slot: int
) -> None:
    assert location_experience(content, 100, city_id=city_id, location_slot=slot) == 100


def test_bonus_comes_from_location_data(content: GameContent) -> None:
    city = content.city_by_order(2)
    location = replace(city.locations[0], experience_bonus_percent=20)
    city = replace(city, locations=(location, *city.locations[1:]))
    changed = replace(
        content,
        cities=tuple(city if one.id == city.id else one for one in content.cities),
        _cities_by_id={**content._cities_by_id, city.id: city},
    )
    assert location_experience(changed, 100, city_id=city.id, location_slot=1) == 120


def test_old_location_data_defaults_to_no_bonus() -> None:
    cities = _parse_world(
        {
            "city": [
                {
                    "id": "old",
                    "name": "Старый",
                    "level_min": 1,
                    "level_max": 2,
                    "location": [
                        {
                            "id": "old-place",
                            "slot": 1,
                            "name": "Место",
                            "level_min": 1,
                            "level_max": 2,
                        }
                    ],
                }
            ]
        },
        [],
    )
    assert cities[0].locations[0].experience_bonus_percent == 0


@pytest.mark.parametrize("bonus", [-1, 101])
def test_invalid_location_bonus_is_rejected(content: GameContent, bonus: int) -> None:
    first = content.cities[0]
    location = replace(first.locations[0], experience_bonus_percent=bonus)
    cities = (replace(first, locations=(location, *first.locations[1:])), *content.cities[1:])
    problems: list[str] = []
    _validate_world(cities, content.rules, problems)
    assert any("experience_bonus_percent must be 0..100" in problem for problem in problems)
