# Указать git на версионируемые хуки в .githooks/.
#
# Как запускать: pwsh -File scripts/install-hooks.ps1
#
# Start.bat делает это за вас; запустите руками после свежего клона, если Start.bat
# не пользуетесь.
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

git config core.hooksPath .githooks
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Git для Windows не требует бита исполнения, чтобы запустить хук, но бит хранится в
# дереве, и клонам на Linux и macOS он нужен.
git update-index --chmod=+x .githooks/pre-commit 2>$null | Out-Null

Write-Host "Hooks installed: .githooks/pre-commit runs on every commit." -ForegroundColor Green
Write-Host "It applies ruff format and ruff check --fix, then runs mypy and pytest."
