"""Domain fixtures: real content, hand-built characters."""

from __future__ import annotations

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.infrastructure.content import load_content
from tests.conftest import CONTENT_ROOT


@pytest.fixture(scope="session")
def content() -> GameContent:
    return load_content(CONTENT_ROOT)


@pytest.fixture
def warrior() -> Character:
    return Character(id=1, user_id=100, name="Тест", race_id="human", class_id="warrior")
