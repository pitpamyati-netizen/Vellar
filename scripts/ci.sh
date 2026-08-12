#!/usr/bin/env bash
# Full local quality gate: lint, format check, type check, tests with coverage.
set -euo pipefail

echo "==> ruff check"
uv run ruff check .

echo "==> ruff format --check"
uv run ruff format --check .

echo "==> mypy --strict"
uv run mypy

echo "==> pytest"
uv run pytest --cov --cov-report=term-missing

# The domain carries the game rules and is testable without any infrastructure,
# so it is held to a higher bar than the rest of the tree.
echo "==> domain coverage (>= 90%)"
uv run pytest --cov=src/mmorpg/domain --cov-report=term --cov-fail-under=90 -q

echo "All checks passed."
