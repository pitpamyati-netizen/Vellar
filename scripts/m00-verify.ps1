# Приёмка M00 на отдельных PostgreSQL и Redis.
# Создаёт базы с фиксированными именами только внутри тестового PostgreSQL.
[CmdletBinding()]
param([string]$ReportPath = "", [string]$PgBin = "")

$ErrorActionPreference = "Stop"
$uvCommand = if ($env:VELLAR_UV) { $env:VELLAR_UV } else { "uv" }
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $ReportPath) {
    $ReportPath = Join-Path $root ("backups/m00-{0}.txt" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
}
$report = [System.IO.Path]::GetFullPath($ReportPath)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $report) | Out-Null

function Record([string]$message) {
    Write-Host $message
    Add-Content -LiteralPath $report -Value $message -Encoding utf8
}

function Require-Success([string]$step) {
    if ($LASTEXITCODE -ne 0) { throw "$step failed" }
}

function Pg-Tool([string]$name) {
    if ($PgBin) {
        $path = Join-Path $PgBin "$name.exe"
        if (Test-Path -LiteralPath $path) { return $path }
    }
    $found = Get-Command $name -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    throw "PostgreSQL tool $name is unavailable; provide -PgBin"
}

$psql = Pg-Tool "psql"
$pgDump = Pg-Tool "pg_dump"
$pgRestore = Pg-Tool "pg_restore"
$fresh = "vellar_m00_fresh_test"
$restored = "vellar_m00_restored_test"
$old = "vellar_m00_old_test"
$oldCopy = "vellar_m00_old_copy_test"
$scratchNames = @($fresh, $restored, $old, $oldCopy)

function Reset-TestDatabase([string]$database) {
    if ($database -notin $scratchNames) { throw "Database name is outside M00 scratch databases" }
    & $psql (Test-Dsn "postgres") -v ON_ERROR_STOP=1 `
        -c "DROP DATABASE IF EXISTS $database WITH (FORCE)" | Out-Null
    Require-Success "drop scratch database"
    & $psql (Test-Dsn "postgres") -v ON_ERROR_STOP=1 -c "CREATE DATABASE $database" | Out-Null
    Require-Success "create scratch database"
}

function Test-Dsn([string]$database) {
    return $env:VELLAR_TEST_POSTGRES_DSN -replace '/[^/]+$', "/$database"
}

function Upgrade-TestDatabase([string]$database, [string]$revision) {
    $previous = [Environment]::GetEnvironmentVariable("POSTGRES_DSN", "Process")
    try {
        $env:POSTGRES_DSN = Test-Dsn $database
        & $uvCommand run alembic upgrade $revision | Out-Null
        Require-Success "migration to $revision"
    } finally {
        [Environment]::SetEnvironmentVariable("POSTGRES_DSN", $previous, "Process")
    }
}

function Query-TestDatabase([string]$database, [string]$query) {
    $answer = & $psql (Test-Dsn $database) -v ON_ERROR_STOP=1 -tAc $query
    Require-Success "test database query"
    return ($answer | Select-Object -Last 1).Trim()
}

function Assert-Revision([string]$database, [string]$revision) {
    if ((Query-TestDatabase $database "SELECT version_num FROM alembic_version") -ne $revision) {
        throw "Unexpected migration revision in scratch database"
    }
}

function Assert-User([string]$database, [string]$account) {
    $count = Query-TestDatabase $database "SELECT count(*) FROM users WHERE telegram_id = $account"
    if ($count -ne "1") { throw "Test user did not survive backup or migration" }
}

try {
    Record "M00 check: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')"
    & $uvCommand run python -m scripts.test_stand | Out-Null
    Require-Success "storage address check"
    $pg = [uri]$env:VELLAR_TEST_POSTGRES_DSN
    if ($pg.AbsolutePath -ne "/vellar_test") {
        throw "M00 script requires the vellar_test database on the isolated server"
    }
    Record "M00.1: test addresses validated; checking isolated services"

    & pwsh -File scripts/ci.ps1 2>&1 | Tee-Object -FilePath $report -Append
    Require-Success "project checks"
    Record "M00.1: locked install, types, unit and integration tests passed"

    Reset-TestDatabase $fresh
    Upgrade-TestDatabase $fresh "head"
    Assert-Revision $fresh "0030"
    & $psql (Test-Dsn $fresh) -v ON_ERROR_STOP=1 `
        -c "INSERT INTO users (telegram_id, username) VALUES (-99990001, 'm00_fresh')" | Out-Null
    Require-Success "seed fresh database"
    $freshDump = Join-Path $root "backups/m00-fresh.dump"
    & $pgDump -d (Test-Dsn $fresh) -Fc -f $freshDump
    Require-Success "backup fresh database"
    if ((Get-Item -LiteralPath $freshDump).Length -eq 0) { throw "Fresh backup is empty" }
    Reset-TestDatabase $restored
    & $pgRestore -d (Test-Dsn $restored) --no-owner --exit-on-error $freshDump | Out-Null
    Require-Success "restore fresh backup"
    Assert-Revision $restored "0030"
    Assert-User $restored "-99990001"
    Record "M00.2: full migration and backup restore passed"

    Reset-TestDatabase $old
    Upgrade-TestDatabase $old "0029"
    Assert-Revision $old "0029"
    & $psql (Test-Dsn $old) -v ON_ERROR_STOP=1 `
        -c "INSERT INTO users (telegram_id, username) VALUES (-99990002, 'm00_old')" | Out-Null
    Require-Success "seed old schema"
    $oldDump = Join-Path $root "backups/m00-old.dump"
    & $pgDump -d (Test-Dsn $old) -Fc -f $oldDump
    Require-Success "backup old schema"
    if ((Get-Item -LiteralPath $oldDump).Length -eq 0) { throw "Old backup is empty" }
    Reset-TestDatabase $oldCopy
    & $pgRestore -d (Test-Dsn $oldCopy) --no-owner --exit-on-error $oldDump | Out-Null
    Require-Success "restore old schema copy"
    Upgrade-TestDatabase $oldCopy "head"
    Assert-Revision $oldCopy "0030"
    Assert-User $oldCopy "-99990002"
    Record "M00.2: old schema copy upgraded and test user preserved"

    Record "M00.3: controlled failure and concurrency tests passed in project checks"
    $previousEncoding = [Environment]::GetEnvironmentVariable("PYTHONIOENCODING", "Process")
    try {
        $env:PYTHONIOENCODING = "utf-8"
        & $uvCommand run python scripts/loadtest.py --test-stand --players 20 --actions 20 --pause 0.5 `
            2>&1 | Tee-Object -FilePath $report -Append
        $loadResult = $LASTEXITCODE
    } finally {
        [Environment]::SetEnvironmentVariable("PYTHONIOENCODING", $previousEncoding, "Process")
    }
    if ($loadResult -notin @(0, 3)) { throw "Baseline load test failed" }
    Record "M00.3: baseline finished with exit code $loadResult (3 means p95 exceeded budget)"
    Record "M00 checks finished. Scratch databases remain only on the isolated test server."
} catch {
    Record "M00 check failed: $($_.Exception.Message)"
    exit 1
}
