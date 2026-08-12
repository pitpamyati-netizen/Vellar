@echo off
rem ============================================================================
rem  Stop the Vellar stack.
rem
rem    stop.bat            stop the containers, keep the world
rem    stop.bat purge      stop AND delete every character, item and fight
rem
rem  Plain stop is safe and reversible: Start.bat brings everything back exactly
rem  as it was. Purge deletes the PostgreSQL and Redis volumes and cannot be
rem  undone, so it asks first.
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

echo [Vellar] Stopping the stack, keeping all saved data...
rem The containers get their stop_grace_period to finish updates already in
rem flight before they are killed.
docker compose down
if errorlevel 1 exit /b 1

echo [Vellar] Stopped. Start.bat brings it back with everything intact.
exit /b 0

:purge
echo.
echo [Vellar] ** This deletes the database and the Redis state: every character,
echo [Vellar] ** every item and every fight in progress. It cannot be undone.
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
