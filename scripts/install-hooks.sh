#!/usr/bin/env bash
# Point git at the versioned hooks in .githooks/.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

git config core.hooksPath .githooks
chmod +x .githooks/pre-commit

echo "Hooks installed: .githooks/pre-commit runs on every commit."
echo "It applies ruff format and ruff check --fix, then runs mypy and pytest."
