@echo off
rem ============================================================================
rem  Apply this working tree to a game that is already running.
rem
rem    Update.bat            back up, build, migrate, swap the bot
rem    Update.bat rollback   put the previous image back
rem
rem  What the players keep: everything. PostgreSQL and Redis are never stopped,
rem  so characters, bags and contracts stay where they are, and so does the
rem  temporary state - the screen each player is on, the fight they are in the
rem  middle of, the offers waiting in the group. Only the bot process is
rem  replaced, and it reads that state back on its first update.
rem
rem  The order is chosen so that a failure costs nothing:
rem
rem    1. dump the database        - a schema change has something to go back to
rem    2. build the new image      - a build error stops here, the old bot serves
rem    3. tag the old image        - "previous", so rollback is one command
rem    4. run the migrations       - alembic upgrade head, on its own, so a bad
rem                                  migration is reported as a migration
rem    5. swap the bot only        - --no-deps, and wait for it to report healthy
rem
rem  A player who presses a button during the swap gets no answer to that press;
rem  Telegram keeps the update and the new bot answers it. Nothing is lost, but
rem  the swap is not invisible either - it is a few seconds of silence.
rem ============================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Vellar

docker version >nul 2>&1
if errorlevel 1 (
    echo [Vellar] Docker is not responding. Start Docker Desktop and try again.
    exit /b 1
)

if /i "%~1"=="rollback" goto rollback
if not "%~1"=="" (
    echo Usage: Update.bat [rollback]
    exit /b 2
)

call scripts\vellar-tools.bat running
if errorlevel 1 (
    echo [Vellar] Nothing is running, so there is nothing to update.
    echo [Vellar] Start.bat builds this working tree and starts it.
    exit /b 1
)

call scripts\vellar-tools.bat stamp
echo [Vellar] This working tree: %VELLAR_BUILD%
set "BEFORE="
for /f "usebackq delims=" %%v in (`docker compose exec -T bot printenv VELLAR_BUILD 2^>nul`) do set "BEFORE=%%v"
if defined BEFORE echo [Vellar] Running now:      %BEFORE%
echo.

rem --- 1. the world as it stands, before anything changes --------------------
call scripts\vellar-tools.bat backup
if errorlevel 1 (
    echo [Vellar] No backup, no update. Nothing was changed.
    exit /b 1
)
echo.

rem --- 2. somewhere to go back to -------------------------------------------
rem Tagged from the container that is actually serving, so rollback returns to
rem what the players were just on - and tagged *before* the build, because the
rem moment "latest" moves the old image is untagged and can be collected.
set "OLD_IMAGE="
for /f "usebackq delims=" %%i in (`docker container inspect vellar-bot --format "{{.Image}}" 2^>nul`) do set "OLD_IMAGE=%%i"
if defined OLD_IMAGE docker tag %OLD_IMAGE% vellar-bot:previous >nul 2>&1

rem --- 3. the new image, while the old one keeps serving ---------------------
echo [Vellar] Building %VELLAR_BUILD%...
docker compose build bot
if errorlevel 1 (
    echo.
    echo [Vellar] The build failed. The bot that was running is still running,
    echo [Vellar] on the old code, and no player noticed anything.
    exit /b 1
)

rem --- 4. the schema ---------------------------------------------------------
echo.
echo [Vellar] Applying migrations...
docker compose run --rm migrate
if errorlevel 1 (
    echo.
    echo [Vellar] The migration failed, so the bot was left alone: it is still
    echo [Vellar] running the old code against the old schema, which match.
    echo [Vellar] The dump above is from before this attempt.
    exit /b 1
)

rem --- 5. the swap -----------------------------------------------------------
echo.
echo [Vellar] Swapping the bot. PostgreSQL and Redis stay up.
docker compose up -d --no-deps --wait --wait-timeout 180 bot
if errorlevel 1 (
    echo.
    echo [Vellar] The new bot did not report healthy. Recent output:
    echo.
    docker compose logs --tail=60 bot
    echo.
    echo [Vellar] Put the old one back with: Update.bat rollback
    exit /b 1
)

echo.
call scripts\vellar-tools.bat report
if errorlevel 1 exit /b 1
echo [Vellar] Updated. Characters, bags and fights in progress were not touched.
exit /b 0

rem ---------------------------------------------------------------------------
:rollback
docker image inspect vellar-bot:previous >nul 2>&1
if errorlevel 1 (
    echo [Vellar] There is no previous image to go back to. It is tagged only by
    echo [Vellar] an update that got as far as building, and only until the next one.
    exit /b 1
)

echo [Vellar] Putting the previous image back...
docker tag vellar-bot:previous vellar-bot:latest
if errorlevel 1 exit /b 1
docker compose up -d --no-deps --wait --wait-timeout 180 bot
if errorlevel 1 (
    echo [Vellar] The previous image did not come up either. Recent output:
    docker compose logs --tail=60 bot
    exit /b 1
)

echo.
call scripts\vellar-tools.bat report
echo [Vellar] Rolled back. A migration that already ran is still applied - the
echo [Vellar] schema only ever moves forward. Restore a dump from backups\ if the
echo [Vellar] old code cannot live with the new schema.
exit /b 0
