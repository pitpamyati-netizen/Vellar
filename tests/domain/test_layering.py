"""The dependency rule is enforced mechanically, not by convention.

See docs/architecture.md, "Layers".
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.conftest import SOURCE_ROOT

DOMAIN_ROOT = SOURCE_ROOT / "domain"
APPLICATION_ROOT = SOURCE_ROOT / "application"

# Third-party packages the domain must never reach for.
FORBIDDEN_THIRD_PARTY = frozenset(
    {
        "aiogram",
        "asyncpg",
        "redis",
        "alembic",
        "pydantic",
        "pydantic_settings",
        "structlog",
        "aiohttp",
        "sqlalchemy",
    }
)

# Inner layers must not import outer ones.
FORBIDDEN_FOR_DOMAIN = frozenset(
    {"mmorpg.application", "mmorpg.infrastructure", "mmorpg.presentation"}
)
FORBIDDEN_FOR_APPLICATION = frozenset({"mmorpg.infrastructure", "mmorpg.presentation"})


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def _python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


@pytest.mark.parametrize("path", _python_files(DOMAIN_ROOT), ids=lambda p: p.name)
def test_domain_imports_stdlib_only(path: Path) -> None:
    for module in _imported_modules(path):
        root = module.split(".")[0]
        assert root not in FORBIDDEN_THIRD_PARTY, f"{path.name} imports third-party {module}"
        assert not any(module.startswith(bad) for bad in FORBIDDEN_FOR_DOMAIN), (
            f"{path.name} imports outer layer {module}"
        )


@pytest.mark.parametrize("path", _python_files(APPLICATION_ROOT), ids=lambda p: p.name)
def test_application_does_not_import_outer_layers(path: Path) -> None:
    for module in _imported_modules(path):
        assert not any(module.startswith(bad) for bad in FORBIDDEN_FOR_APPLICATION), (
            f"{path.name} imports outer layer {module}"
        )


def _rules_and_entities() -> list[Path]:
    """Domain modules that must be synchronous - everything except the ports.

    Ports describe the boundary to the outside world, which is asynchronous by
    nature; the logic behind them is not.
    """
    return [path for path in _python_files(DOMAIN_ROOT) if path.parent.name != "ports"]


@pytest.mark.parametrize("path", _rules_and_entities(), ids=lambda p: p.name)
def test_domain_has_no_coroutines(path: Path) -> None:
    """The domain is synchronous: no awaits, no async def, nothing to schedule."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = [node.name for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)]
    assert not offenders, f"{path.name} declares async functions: {offenders}"
