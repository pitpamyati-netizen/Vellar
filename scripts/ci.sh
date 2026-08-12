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

echo "All checks passed."
