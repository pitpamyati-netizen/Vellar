#!/usr/bin/env bash
# Полный местный гейт качества: линтер, проверка форматирования, проверка типов, тесты с
# покрытием.
set -euo pipefail

echo "==> ruff check"
uv run ruff check .

echo "==> ruff format --check"
uv run ruff format --check .

echo "==> mypy --strict"
uv run mypy

echo "==> pytest"
uv run pytest --cov --cov-report=term-missing

# Домен несёт правила игры и проверяется без всякой инфраструктуры, поэтому спрос с него
# выше, чем с остального дерева.
echo "==> domain coverage (>= 90%)"
uv run pytest --cov=src/mmorpg/domain --cov-report=term --cov-fail-under=90 -q

echo "All checks passed."
