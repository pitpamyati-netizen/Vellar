# Полный местный гейт качества: линтер, проверка форматирования, проверка типов,
# тесты с покрытием.
#
# Как запускать: pwsh -File scripts/ci.ps1
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

# Домен несёт правила игры и проверяется без всякой инфраструктуры, поэтому спрос с него
# выше, чем с остального дерева.
Write-Host "==> domain coverage (>= 90%)" -ForegroundColor Cyan
uv run pytest --cov=src/mmorpg/domain --cov-report=term --cov-fail-under=90 -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "All checks passed." -ForegroundColor Green
