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
rem
rem  Without a stack there is nothing here to stop: a game started with
rem  "Start.bat solo" or "Start.bat local" is a process in its own window, and
rem  Ctrl+C there is what stops it. This still runs, though, and takes the dump -
rem  a solo world is in a PostgreSQL on this machine and can be copied out of it
rem  exactly like the container's one.
rem ============================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Vellar

if /i "%~1"=="purge" goto purge
if not "%~1"=="" (
    echo Usage: stop.bat [purge]
    exit /b 2
)

docker version >nul 2>&1
if errorlevel 1 goto no_stack

call scripts\vellar-tools.bat running
if errorlevel 1 goto no_stack

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

rem ---------------------------------------------------------------------------
rem  No containers are running. Either nothing is, or the game is the process in
rem  its own window. Nothing here can stop that process - and nothing needs to,
rem  because in solo mode the world is written to PostgreSQL as it happens. The
rem  dump is taken anyway: it is the copy that can be carried elsewhere.
rem ---------------------------------------------------------------------------
:no_stack
echo [Vellar] No stack is running in Docker.
echo [Vellar] A game started with "Start.bat solo" or "Start.bat local" is the
echo [Vellar] process in its own window: Ctrl+C there stops it, and a solo world
echo [Vellar] is already on disk the moment each action happens.
echo.
call scripts\vellar-tools.bat backup
rem A missing database here means nothing was set up to save, which is not a
rem failure of stopping - it is the answer to "is there anything to keep".
exit /b 0

:purge
docker version >nul 2>&1
if errorlevel 1 (
    echo [Vellar] Docker is not responding, and purge only deletes what Docker
    echo [Vellar] holds. A solo world lives in the PostgreSQL on this machine;
    echo [Vellar] to start that one over, drop and recreate its database:
    echo [Vellar]     psql -U postgres -c "DROP DATABASE vellar"
    echo [Vellar]     Start.bat setup-db
    echo [Vellar] Take a dump first if there is anything in it: stop.bat
    exit /b 1
)
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
