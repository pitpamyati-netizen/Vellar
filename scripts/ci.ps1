# Местные проверки без команд Git: стиль, типы, тесты и покрытие.
#
# Как запускать: pwsh -File scripts/ci.ps1
$ErrorActionPreference = "Stop"
$uvCommand = if ($env:VELLAR_UV) { $env:VELLAR_UV } else { "uv" }
$testTemp = Join-Path (Resolve-Path .).Path "backups/m00-pytest-tmp"

Write-Host "==> locked dependencies" -ForegroundColor Cyan
& $uvCommand sync --frozen
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> ruff check" -ForegroundColor Cyan
& $uvCommand run ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> ruff format --check" -ForegroundColor Cyan
& $uvCommand run ruff format --check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> mypy --strict" -ForegroundColor Cyan
& $uvCommand run mypy
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> tests without services" -ForegroundColor Cyan
& $uvCommand run pytest -m "not integration" --ignore=tests/content/test_changelog.py --basetemp $testTemp -p no:cacheprovider --cov --cov-report=term-missing
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> isolated test storage" -ForegroundColor Cyan
& $uvCommand run python -m scripts.test_stand
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Только после проверки адресов передаём тестовую базу миграциям. Прежнее значение
# возвращается даже при ошибке; .env и адрес работающей игры не меняются.
$previousDsn = [Environment]::GetEnvironmentVariable("POSTGRES_DSN", "Process")
try {
    $env:POSTGRES_DSN = $env:VELLAR_TEST_POSTGRES_DSN
    Write-Host "==> test database migrations" -ForegroundColor Cyan
    & $uvCommand run alembic upgrade head
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    [Environment]::SetEnvironmentVariable("POSTGRES_DSN", $previousDsn, "Process")
}

Write-Host "==> PostgreSQL and Redis tests" -ForegroundColor Cyan
& $uvCommand run pytest -m integration -rs --basetemp $testTemp -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Домен несёт правила игры и проверяется без всякой инфраструктуры, поэтому спрос с него
# выше, чем с остального дерева.
Write-Host "==> domain coverage (>= 90%)" -ForegroundColor Cyan
& $uvCommand run pytest -m "not integration" --ignore=tests/content/test_changelog.py --basetemp $testTemp -p no:cacheprovider --cov=src/mmorpg/domain --cov-report=term --cov-fail-under=90 -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "All checks passed." -ForegroundColor Green
