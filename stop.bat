@echo off
rem ============================================================================
rem  Stop the Vellar stack.
rem
rem    stop.bat            save everything, then stop; the world is kept
rem    stop.bat purge      stop AND delete every character, item and fight
rem
rem  A plain stop keeps three things, in this order:
rem
rem    1. the temporary state - where every player is standing, the fight they
rem       are in the middle of, the offers waiting in the group. Redis writes it
rem       out before the container is asked to stop.
rem    2. the permanent state - characters, gold, bags, contracts, craft work -
rem       dumped to backups\ as it stands at this moment, changes included.
rem    3. the containers themselves, stopped with their grace period, so an
rem       update already in flight is finished rather than severed.
rem
rem  Nothing here deletes a volume, so Start.bat brings the world back exactly as
rem  it was. Purge is the one exception, and it asks first.
rem ============================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Vellar

docker version >nul 2>&1
if errorlevel 1 (
    echo [Vellar] Docker is not responding, so nothing of the stack can be running.
    echo [Vellar] If you started it with "Start.bat local", press Ctrl+C in that window.
    exit /b 1
)

if /i "%~1"=="purge" goto purge
if not "%~1"=="" (
    echo Usage: stop.bat [purge]
    exit /b 2
)

call scripts\vellar-tools.bat running
if errorlevel 1 (
    echo [Vellar] Nothing is running. There is nothing to stop or to save.
    exit /b 0
)

echo [Vellar] Saving before stopping.
echo.
call scripts\vellar-tools.bat flush
call scripts\vellar-tools.bat backup
if errorlevel 1 (
    echo.
    echo [Vellar] The database could not be dumped. Stopping now would still keep
    echo [Vellar] the volume - nothing is deleted by a stop - but the readable
    echo [Vellar] copy in backups\ would be missing. Nothing was stopped.
    echo [Vellar] Look at the error above, or force it with: docker compose down
    exit /b 1
)

echo.
echo [Vellar] Stopping the stack...
rem The containers get their stop_grace_period to finish updates already in
rem flight before they are killed.
docker compose down
if errorlevel 1 exit /b 1

echo.
echo [Vellar] Stopped. Start.bat brings it back with everything intact.
echo [Vellar] The dump above is a second copy: the database volume is untouched.
exit /b 0

:purge
echo.
echo [Vellar] ** This deletes the database and the Redis state: every character,
echo [Vellar] ** every item and every fight in progress. It cannot be undone.
echo [Vellar] ** Dumps already in backups\ are not touched and still hold the
echo [Vellar] ** characters as of the last stop.
echo.
set "CONFIRM="
set /p "CONFIRM=Type DELETE to confirm: "
if /i not "%CONFIRM%"=="DELETE" (
    echo [Vellar] Cancelled. Nothing was removed.
    exit /b 1
)

echo [Vellar] Stopping and deleting the volumes...
docker compose down --volumes
if errorlevel 1 exit /b 1

echo [Vellar] Done. The next Start.bat begins from an empty world.
exit /b 0
