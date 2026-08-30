"""Семь домов: каждый держит два города и несёт технику, которую движок считает."""

from __future__ import annotations

from pathlib import Path

import pytest

from mmorpg.domain.entities import GameContent
from mmorpg.domain.rules.modifiers import EFFECTIVE_KEYS
from mmorpg.infrastructure.content.loader import ContentError, load_content


def test_seven_houses_hold_two_cities_each(content: GameContent) -> None:
    assert len(content.houses) == 7
    known = {city.id for city in content.cities}
    held: set[str] = set()
    for house in content.houses:
        assert len(house.seats) == 2, house.id
        for seat in house.seats:
            assert seat in known, seat
            assert seat not in held, f"{seat} held twice"
            held.add(seat)
    assert "obsidian_throne" not in held


def test_every_technique_promises_only_what_the_engine_counts(content: GameContent) -> None:
    for house in content.houses:
        assert house.technique.name, house.id
        assert house.technique.modifiers, house.id
        for key in house.technique.modifiers:
            assert key in EFFECTIVE_KEYS, f"{house.id}: {key}"


def _sandbox(tmp_path: Path) -> Path:
    for name in Path("content").glob("*.toml"):
        (tmp_path / name.name).write_text(name.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def test_a_technique_that_promises_nothing_real_is_refused(tmp_path: Path) -> None:
    root = _sandbox(tmp_path)
    broken = (root / "houses.toml").read_text(encoding="utf-8")
    (root / "houses.toml").write_text(
        broken.replace("initiative_percent = 8", "carrying_capacity = 8"), encoding="utf-8"
    )
    with pytest.raises(ContentError) as failure:
        load_content(root)
    assert "houses.toml" in str(failure.value)


def test_a_house_on_the_throne_city_is_refused(tmp_path: Path) -> None:
    root = _sandbox(tmp_path)
    broken = (root / "houses.toml").read_text(encoding="utf-8")
    (root / "houses.toml").write_text(
        broken.replace('"dusk_harbor"', '"obsidian_throne"'), encoding="utf-8"
    )
    with pytest.raises(ContentError) as failure:
        load_content(root)
    assert "houses.toml" in str(failure.value)
