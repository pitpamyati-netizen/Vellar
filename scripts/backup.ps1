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
#  уникальную отдельную базу, сверяют хеши всех таблиц самого снимка. База после
#  проверки удаляется.
#
#  Работает в обе стороны: через контейнер, пока поднят стек, и через
#  PostgreSQL этой машины, когда стека нет (ADR 0010).
# =============================================================================
[CmdletBinding()]
param(
    [switch]$NoVerify,
    [ValidateRange(1, 2147483647)][int]$Keep = 20,
    [string]$Schedule = "",
    [switch]$Unschedule
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backups = Join-Path $root "backups"
$taskName = "Vellar backup"

function Say([string]$text) { Write-Host "[Vellar] $text" }

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

# Проверка снимка и ротация выполняются одним процессом под SQL-блокировкой.
Add-PgTools | Out-Null
Set-Location $root
$argsList = @("run", "python", "-m", "scripts.backup", "--keep", "$Keep")
if ($NoVerify) { $argsList += "--no-verify" }
& uv @argsList
exit $LASTEXITCODE
