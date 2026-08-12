"""Screen fixtures.

``all_screens`` builds every screen the game can show, so the accessibility tests
can inspect all of them at once. Every new screen must be added here - that is
deliberate: a screen nobody listed is a screen nobody checked.
"""

from __future__ import annotations

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.infrastructure.content import load_content
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.paginated import (
    ListEntry,
    ListFilters,
    PageState,
    paginated_screen,
)
from tests.conftest import CONTENT_ROOT


@pytest.fixture(scope="session")
def content() -> GameContent:
    return load_content(CONTENT_ROOT)


@pytest.fixture(scope="session")
def hero() -> Character:
    return Character(id=1, user_id=42, name="Аргус", race_id="human", class_id="warrior")


@pytest.fixture
def all_screens(content: GameContent, hero: Character) -> list[Screen]:
    """Every screen in the game, rendered with sample data."""
    screens: list[Screen] = [
        paginated_screen(
            screen_id=ScreenId.INVENTORY,
            title="Инвентарь",
            entries=[
                ListEntry(key=item.id, text=item.name, detail=item.text[:40])
                for item in content.items[:20]
            ],
            state=PageState(page=1, filters=ListFilters(category="Оружие")),
        ),
        paginated_screen(
            screen_id=ScreenId.INVENTORY,
            title="Инвентарь",
            entries=[],
            state=PageState(),
        ),
    ]
    return screens
