"""Content is loaded once per test session - it is immutable, so sharing is safe."""

from __future__ import annotations

import pytest

from mmorpg.domain.entities import GameContent
from mmorpg.infrastructure.content import load_content
from tests.conftest import CONTENT_ROOT


@pytest.fixture(scope="session")
def content() -> GameContent:
    return load_content(CONTENT_ROOT)
