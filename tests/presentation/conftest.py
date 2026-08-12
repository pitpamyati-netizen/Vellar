"""Screen fixtures.

``all_screens`` builds every screen the game can show, so the accessibility tests
can inspect all of them at once. Every new screen must be added here - that is
deliberate: a screen nobody listed is a screen nobody checked.
"""

from __future__ import annotations

import pytest

from mmorpg.application.dto.creation import CharacterDraft
from mmorpg.domain.entities import Character, GameContent, StatBlock
from mmorpg.infrastructure.content import load_content
from mmorpg.presentation.telegram.handlers import creation as handlers_creation
from mmorpg.presentation.telegram.screens import creation
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
def complete_draft() -> CharacterDraft:
    return CharacterDraft(
        name="Аргус",
        race_id="dwarf",
        class_id="warrior",
        trait_ids=("berserker", "duelist"),
        allocated=StatBlock(STR=3, END=2),
    )


@pytest.fixture
def all_screens(
    content: GameContent, hero: Character, complete_draft: CharacterDraft
) -> list[Screen]:
    """Every screen in the game, rendered with sample data.

    New screens must be added here: a screen nobody listed is a screen nobody
    checked against the accessibility rules.
    """
    empty = CharacterDraft()
    screens: list[Screen] = [
        creation.name_screen(empty),
        creation.name_screen(complete_draft, notice="Имя Аргус уже занято."),
        creation.race_screen(content, empty, PageState()),
        creation.race_screen(content, complete_draft, PageState(page=2)),
        creation.race_details_screen(content, "dwarf"),
        creation.class_screen(content, complete_draft),
        creation.class_details_screen(content, "warrior"),
        creation.traits_screen(content, complete_draft, PageState()),
        creation.traits_screen(content, empty, PageState(page=3)),
        creation.points_screen(content, complete_draft),
        creation.confirm_screen(content, complete_draft),
        handlers_creation.welcome_screen(),
        handlers_creation.created_screen("Аргус", "Дальний Оплот"),
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
