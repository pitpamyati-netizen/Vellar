# Point git at the versioned hooks in .githooks/.
# Usage: pwsh -File scripts/install-hooks.ps1
# Start.bat does this for you; run it by hand after a fresh clone if you never
# use Start.bat.
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

git config core.hooksPath .githooks
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Git for Windows does not need the executable bit to run a hook, but the bit is
# stored in the tree and Linux and macOS clones do need it.
git update-index --chmod=+x .githooks/pre-commit 2>$null | Out-Null

Write-Host "Hooks installed: .githooks/pre-commit runs on every commit." -ForegroundColor Green
Write-Host "It applies ruff format and ruff check --fix, then runs mypy and pytest."
