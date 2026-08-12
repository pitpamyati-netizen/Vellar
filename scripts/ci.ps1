# Full local quality gate: lint, format check, type check, tests with coverage.
# Usage: pwsh -File scripts/ci.ps1
$ErrorActionPreference = "Stop"

Write-Host "==> ruff check" -ForegroundColor Cyan
uv run ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> ruff format --check" -ForegroundColor Cyan
uv run ruff format --check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> mypy --strict" -ForegroundColor Cyan
uv run mypy
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> pytest" -ForegroundColor Cyan
uv run pytest --cov --cov-report=term-missing
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# The domain carries the game rules and is testable without any infrastructure,
# so it is held to a higher bar than the rest of the tree.
Write-Host "==> domain coverage (>= 90%)" -ForegroundColor Cyan
uv run pytest --cov=src/mmorpg/domain --cov-report=term --cov-fail-under=90 -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "All checks passed." -ForegroundColor Green
