# =============================================================================
#  Резервная копия мира — и доказательство, что она разворачивается.
#
#    pwsh -File scripts/backup.ps1                снять копию, проверить, убрать старые
#    pwsh -File scripts/backup.ps1 -NoVerify      только снять копию
#    pwsh -File scripts/backup.ps1 -Keep 40       держать сорок копий вместо двадцати
#    pwsh -File scripts/backup.ps1 -Schedule 04:00   каждый день в это время
#    pwsh -File scripts/backup.ps1 -Unschedule    снять расписание
#
#  stop.bat снимает копию, когда игру останавливают руками. Этого мало: игру
#  можно не останавливать неделями, а потерять базу — за секунду. Поэтому копия
#  снимается по расписанию, а не по случаю.
#
#  Проверка восстановления — половина работы, и не меньшая. Файл, который никто
#  не разворачивал, — это не копия, а надежда: развернуть его пробуют здесь, в
#  отдельную базу, считают в ней персонажей и сверяют с живой. База после
#  проверки удаляется.
#
#  Работает в обе стороны: через контейнер, пока поднят стек, и через
#  PostgreSQL этой машины, когда стека нет (ADR 0010).
# =============================================================================
[CmdletBinding()]
param(
    [switch]$NoVerify,
    [int]$Keep = 20,
    [string]$Schedule = "",
    [switch]$Unschedule
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backups = Join-Path $root "backups"
$taskName = "Vellar backup"

function Say([string]$text) { Write-Host "[Vellar] $text" }

# --- .env, потому что где база — знает он, а не процесс игры -----------------
function Read-Env([string]$key, [string]$fallback) {
    $file = Join-Path $root ".env"
    if (Test-Path $file) {
        foreach ($line in Get-Content $file) {
            if ($line -match "^\s*$([regex]::Escape($key))\s*=\s*(.*)$") {
                $value = $Matches[1].Trim().Trim('"')
                if ($value) { return $value }
            }
        }
    }
    return $fallback
}

# Установщик под Windows не кладёт bin в PATH; ищется он ровно так же, как в
# scripts/vellar-tools.bat, и PATH меняется только для этого процесса.
function Add-PgTools {
    if (Get-Command psql -ErrorAction SilentlyContinue) { return $true }
    $home_dirs = Join-Path $env:ProgramFiles "PostgreSQL"
    if (Test-Path $home_dirs) {
        $newest = Get-ChildItem $home_dirs -Directory | Sort-Object Name -Descending
        foreach ($dir in $newest) {
            $bin = Join-Path $dir.FullName "bin"
            if (Test-Path (Join-Path $bin "psql.exe")) {
                $env:PATH = "$bin;$env:PATH"
                return $true
            }
        }
    }
    return $false
}

function Stack-Up {
    # Docker может быть не установлен вовсе — это обычный случай (ADR 0010),
    # а не ошибка, поэтому спрашивается сначала про сам docker.
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return $false }
    $ids = docker compose ps -q 2>$null
    return [bool]$ids
}

# --- расписание --------------------------------------------------------------
if ($Unschedule) {
    try { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop }
    catch { Say "Расписания и не было."; exit 0 }
    Say "Расписание снято: копии больше не снимаются сами."
    exit 0
}

if ($Schedule) {
    $pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue)?.Source
    if (-not $pwsh) { $pwsh = (Get-Command powershell).Source }
    $script = Join-Path $PSScriptRoot "backup.ps1"
    $action = New-ScheduledTaskAction -Execute $pwsh `
        -Argument "-NoProfile -File `"$script`" -Keep $Keep" -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Daily -At $Schedule
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Description "Копия мира Vellar и проверка, что она разворачивается." -Force | Out-Null
    Say "Копия будет сниматься каждый день в $Schedule, храниться будет $Keep штук."
    Say "Снять расписание: pwsh -File scripts/backup.ps1 -Unschedule"
    exit 0
}

# --- копия -------------------------------------------------------------------
if (-not (Test-Path $backups)) { New-Item -ItemType Directory $backups | Out-Null }
$stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$file = Join-Path $backups "vellar-$stamp.sql"
$dsn = Read-Env "POSTGRES_DSN" "postgresql://vellar:vellar@localhost:5432/vellar"
$database = ([uri]$dsn).AbsolutePath.Trim('/')
if (-not $database) { $database = "vellar" }
$inDocker = Stack-Up

if (-not $inDocker -and -not (Add-PgTools)) {
    Say "Ни стека в Docker, ни PostgreSQL на этой машине: копировать нечего."
    exit 1
}

# Никогда не ждать базу, которой нет: это может идти по расписанию ночью.
$env:PGCONNECT_TIMEOUT = "10"

if ($inDocker) {
    docker compose exec -T postgres pg_dump -U vellar -d vellar --clean --if-exists --no-owner |
        Set-Content -Path $file -Encoding utf8
} else {
    pg_dump $dsn --clean --if-exists --no-owner | Set-Content -Path $file -Encoding utf8
}
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $file) -or (Get-Item $file).Length -eq 0) {
    Remove-Item $file -ErrorAction SilentlyContinue
    Say "PostgreSQL не ответил, копия не записана."
    exit 1
}

function Count-Characters([string]$target) {
    if ($inDocker) {
        $answer = docker compose exec -T postgres psql -U vellar -d $target -tAc "select count(*) from characters" 2>$null
    } else {
        $answer = psql ($dsn -replace "/$database$", "/$target") -tAc "select count(*) from characters" 2>$null
    }
    if ($LASTEXITCODE -ne 0) { return -1 }
    return [int]($answer | Select-Object -Last 1).Trim()
}

$living = Count-Characters $database
$size = [math]::Round((Get-Item $file).Length / 1MB, 2)
Say "Копия снята: $file, $size МБ, персонажей в базе: $living."

# --- старые копии ------------------------------------------------------------
$old = Get-ChildItem $backups -Filter "vellar-*.sql" | Sort-Object Name -Descending | Select-Object -Skip $Keep
foreach ($stale in $old) { Remove-Item $stale.FullName -ErrorAction SilentlyContinue }
if ($old) { Say "Старых копий убрано: $($old.Count). Осталось: $Keep." }

if ($NoVerify) { exit 0 }

# --- проверка восстановления -------------------------------------------------
# Отдельная база, живущая столько, сколько идёт проверка. Права на её создание
# у роли есть (scripts/setup-db.sql); если их нет, об этом говорится прямо, а не
# молчаливым «проверка не выполнялась».
$scratch = "${database}_restorecheck"

function Run-Sql([string]$target, [string]$sql) {
    if ($inDocker) {
        docker compose exec -T postgres psql -U vellar -d $target -v ON_ERROR_STOP=1 -c $sql | Out-Null
    } else {
        psql ($dsn -replace "/$database$", "/$target") -v ON_ERROR_STOP=1 -c $sql | Out-Null
    }
    return $LASTEXITCODE -eq 0
}

Run-Sql "postgres" "DROP DATABASE IF EXISTS $scratch" | Out-Null
if (-not (Run-Sql "postgres" "CREATE DATABASE $scratch")) {
    Say "Копия снята, но развернуть её некуда: роли нельзя заводить базы."
    Say "Дайте ей это право один раз, и проверка пойдёт сама:"
    Say "  psql -U postgres -c \"ALTER ROLE vellar CREATEDB;\""
    exit 1
}

try {
    if ($inDocker) {
        Get-Content $file | docker compose exec -T postgres psql -U vellar -d $scratch -v ON_ERROR_STOP=1 -q | Out-Null
    } else {
        psql ($dsn -replace "/$database$", "/$scratch") -v ON_ERROR_STOP=1 -q -f $file | Out-Null
    }
    $restored = if ($LASTEXITCODE -eq 0) { Count-Characters $scratch } else { -1 }
} finally {
    Run-Sql "postgres" "DROP DATABASE IF EXISTS $scratch" | Out-Null
}

if ($restored -lt 0) {
    Say "** Копия НЕ разворачивается. Файл: $file"
    Say "** Пока это не починено, копий у игры нет, сколько бы файлов ни лежало."
    exit 1
}
if ($restored -ne $living) {
    Say "** Развернулось персонажей: $restored, а в живой базе их $living."
    Say "** Копия снята посреди записи или не целиком: разберитесь до следующей."
    exit 1
}
Say "Проверено: копия разворачивается, персонажей в ней $restored из $living."
exit 0
