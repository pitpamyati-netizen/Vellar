"""Shared test fixtures and helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src" / "mmorpg"
CONTENT_ROOT = PROJECT_ROOT / "content"


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def source_root() -> Path:
    return SOURCE_ROOT


@pytest.fixture(scope="session")
def content_root() -> Path:
    return CONTENT_ROOT


def iter_source_files(root: Path = SOURCE_ROOT) -> list[Path]:
    """Every Python module shipped in the package."""
    return sorted(root.rglob("*.py"))
