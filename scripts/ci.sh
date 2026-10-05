#!/usr/bin/env bash
# Местные проверки без команд Git: стиль, типы, тесты и покрытие.
set -euo pipefail

echo "==> locked dependencies"
uv sync --frozen

echo "==> ruff check"
uv run ruff check .

echo "==> ruff format --check"
uv run ruff format --check .

echo "==> mypy --strict"
uv run mypy

echo "==> tests without services"
uv run pytest -m "not integration" --ignore=tests/content/test_changelog.py --basetemp backups/m00-pytest-tmp -p no:cacheprovider --cov --cov-report=term-missing

echo "==> isolated test storage"
uv run python -m scripts.test_stand

echo "==> test database migrations"
POSTGRES_DSN="$VELLAR_TEST_POSTGRES_DSN" uv run alembic upgrade head

echo "==> PostgreSQL and Redis tests"
uv run pytest -m integration -rs --basetemp backups/m00-pytest-tmp -p no:cacheprovider

# Домен несёт правила игры и проверяется без всякой инфраструктуры, поэтому спрос с него
# выше, чем с остального дерева.
echo "==> domain coverage (>= 90%)"
uv run pytest -m "not integration" --ignore=tests/content/test_changelog.py --basetemp backups/m00-pytest-tmp -p no:cacheprovider --cov=src/mmorpg/domain --cov-report=term --cov-fail-under=90 -q

echo "All checks passed."
