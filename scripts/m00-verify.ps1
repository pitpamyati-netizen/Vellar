# Приёмка M00 на отдельных PostgreSQL и Redis.
# Создаёт базы с фиксированными именами только внутри тестового PostgreSQL.
[CmdletBinding()]
param([string]$ReportPath = "", [string]$PgBin = "")

$ErrorActionPreference = "Stop"
$uvCommand = if ($env:VELLAR_UV) { $env:VELLAR_UV } else { "uv" }
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if ($PgBin) { $env:PATH = "$PgBin;$env:PATH" }

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

function Seed-TestCharacter([string]$database, [string]$account) {
    $seed = @"
INSERT INTO users (telegram_id, username) VALUES ($account, 'migration_control');
INSERT INTO characters (user_id, name, race_id, class_id, level, gold, bank_gold, equipment, experience, stat_str)
VALUES ($account, 'Control', 'human', 'warrior', 12, 731, 219, '{"weapon":"sword@1#common"}', 345, 8);
INSERT INTO inventory (character_id, item_id, quantity)
SELECT id, 'sword@1#common', 3 FROM characters WHERE user_id = $account;
"@
    & $psql (Test-Dsn $database) -v ON_ERROR_STOP=1 -c $seed | Out-Null
    Require-Success "seed control character"
}

function Assert-Character([string]$database, [string]$account) {
    Assert-User $database $account
    $saved = Query-TestDatabase $database "SELECT level || ':' || gold || ':' || bank_gold || ':' || experience || ':' || stat_str || ':' || split_part(equipment->>'weapon', '!', 1) FROM characters WHERE user_id = $account"
    if ($saved -ne "12:731:219:345:8:sword@1#common") { throw "Control character changed during backup or migration" }
    $items = Query-TestDatabase $database "SELECT sum(quantity) FROM inventory JOIN characters ON characters.id = inventory.character_id WHERE user_id = $account AND split_part(item_id, '!', 1) = 'sword@1#common'"
    if ($items -ne "3") { throw "Control inventory changed during backup or migration" }
}

try {
    Record "M00 check: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')"
    & $uvCommand run python -m scripts.test_stand | Out-Null
    Require-Success "storage address check"
    $schemaHead = & $uvCommand run python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; print(ScriptDirectory.from_config(Config('alembic.ini')).get_current_head())"
    Require-Success "migration head check"
    $schemaHead = $schemaHead.Trim()
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
    Assert-Revision $fresh $schemaHead
    Seed-TestCharacter $fresh "-99990001"
    $entryCount = Query-TestDatabase $fresh "SELECT count(*) FROM economic_entries"
    if ($entryCount -ne "4") { throw "Control economic journal is incomplete" }
    Query-TestDatabase $fresh "INSERT INTO durable_effects(key, value) VALUES('digest:control:5', '1'); INSERT INTO message_delivery(key, bot_id, chat_id, payload, priority) VALUES('control-reply', 1, '1', jsonb_build_object('chat_id', 1, 'text', 'control'), 0); SELECT 1" | Out-Null
    & $uvCommand run python -m scripts.m03_gameplay_probe --database $fresh --seed
    Require-Success "seed persistent battle"
    & $uvCommand run python -m scripts.m04_journey_probe --database $fresh --seed
    Require-Success "seed journey and full reading"
    $freshDump = Join-Path $root "backups/m00-fresh.dump"
    & $pgDump -d (Test-Dsn $fresh) -Fc -f $freshDump
    Require-Success "backup fresh database"
    if ((Get-Item -LiteralPath $freshDump).Length -eq 0) { throw "Fresh backup is empty" }
    Reset-TestDatabase $restored
    & $pgRestore -d (Test-Dsn $restored) --no-owner --exit-on-error $freshDump | Out-Null
    Require-Success "restore fresh backup"
    Assert-Revision $restored $schemaHead
    Assert-Character $restored "-99990001"
    if ((Query-TestDatabase $restored "SELECT count(*) FROM economic_entries") -ne $entryCount) {
        throw "Economic journal did not survive backup restore"
    }
    if ((Query-TestDatabase $restored "SELECT value FROM durable_effects WHERE key='digest:control:5'") -ne "1") {
        throw "Reward mark did not survive backup restore"
    }
    if ((Query-TestDatabase $restored "SELECT status FROM message_delivery WHERE key='control-reply'") -ne "queued") {
        throw "Pending reply did not survive backup restore"
    }
    Record "M00.2: full migration and backup restore passed"
    & $uvCommand run python -m scripts.m03_gameplay_probe --database $restored
    Require-Success "restore persistent battle"
    Record "M03: persistent battle, rules, occupation and screen survived backup restore"
    & $uvCommand run python -m scripts.m04_journey_probe --database $restored
    Require-Success "restore journey and full reading"
    Record "M04: journey, history and full reading survived backup restore"

    Reset-TestDatabase $old
    Upgrade-TestDatabase $old "0030"
    Assert-Revision $old "0030"
    Seed-TestCharacter $old "-99990002"
    $oldDump = Join-Path $root "backups/m00-old.dump"
    & $pgDump -d (Test-Dsn $old) -Fc -f $oldDump
    Require-Success "backup old schema"
    if ((Get-Item -LiteralPath $oldDump).Length -eq 0) { throw "Old backup is empty" }
    Reset-TestDatabase $oldCopy
    & $pgRestore -d (Test-Dsn $oldCopy) --no-owner --exit-on-error $oldDump | Out-Null
    Require-Success "restore old schema copy"
    Upgrade-TestDatabase $oldCopy "head"
    Assert-Revision $oldCopy $schemaHead
    Assert-Character $oldCopy "-99990002"
    Record "M00.2/M02: schema 0030 copy upgraded to $schemaHead; hero, wallet, bank, equipment and bag preserved"

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
