@echo off
rem ============================================================================
rem  Vellar launcher.
rem
rem    Start.bat            the game: one process against PostgreSQL on this
rem                         machine, no Docker (same as "Start.bat solo")
rem    Start.bat local      one process, in-memory, no database at all
rem    Start.bat docker     the full stack in containers: PostgreSQL, Redis, bot
rem    Start.bat setup-db   create the vellar role and database, once
rem    Start.bat logs       follow the running container's log
rem    Start.bat status     what is running right now
rem
rem  Three ways to run, and they differ in what survives being stopped.
rem
rem  The solo path - the default - keeps the world: characters, gold, bags and
rem  contracts are in a PostgreSQL installed on this machine. It forgets the
rem  session: a restart puts everyone in the main menu and ends a fight in
rem  progress. No Docker, no Redis, one installer
rem  (docs/adr/0010-a-machine-without-containers.md).
rem  The local path is for trying a change quickly - it forgets everything on
rem  exit, so never leave players on it.
rem  The Docker path keeps both halves, restarts the bot if it dies or wedges,
rem  and is what a server runs. Started again on a stack that is already up, it
rem  rebuilds the bot and swaps it without taking PostgreSQL and Redis down.
rem
rem  What runs is always this working tree, stamped with the commit it came from
rem  and logged on startup, so "am I on my latest change" is answered rather than
rem  remembered. In the Docker path a build that fails stops here and never
rem  touches what is already serving.
rem ============================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Vellar

set "MODE=%~1"
rem Solo, because that is the one that needs nothing installed but PostgreSQL.
rem The stack is a deliberate choice and says so: "Start.bat docker".
if "%MODE%"=="" set "MODE=solo"

if /i "%MODE%"=="docker"   goto mode_docker
if /i "%MODE%"=="solo"     goto mode_solo
if /i "%MODE%"=="local"    goto mode_local
if /i "%MODE%"=="setup-db" goto mode_setup_db
if /i "%MODE%"=="logs"     goto mode_logs
if /i "%MODE%"=="status"   goto mode_status

echo Usage: Start.bat [solo^|local^|docker^|setup-db^|logs^|status]
echo        no argument is the same as "solo".
exit /b 2

rem ---------------------------------------------------------------------------
rem  Docker: the full stack.
rem ---------------------------------------------------------------------------
:mode_docker
call :ensure_env    || exit /b 1
call :ensure_hooks
call :ensure_docker || exit /b 1
call :stamp_build

echo [Vellar] Building the image from this working tree (%VELLAR_BUILD%)...
echo [Vellar] The first build downloads the base images and takes a few minutes.
echo.
rem Built before anything is stopped. A broken build then costs nothing: whatever
rem was serving keeps serving, and this script says so instead of leaving a half
rem started stack behind.
docker compose build bot
if errorlevel 1 (
    echo.
    echo [Vellar] The build failed, so nothing was started or replaced.
    echo [Vellar] Fix the error above and run this again.
    exit /b 1
)

echo.
echo [Vellar] Starting PostgreSQL, Redis and the bot...
echo.
rem --wait blocks until every service is healthy and the migration has finished.
rem The bot only reports healthy once its event loop is beating, so reaching this
rem point means it is genuinely serving - see src/mmorpg/health.py.
docker compose up -d --wait --wait-timeout 300
if errorlevel 1 (
    echo.
    echo [Vellar] The stack did not come up. Recent output:
    echo.
    docker compose logs --tail=60
    echo.
    echo [Vellar] A rejected BOT_TOKEN is the usual cause - check the value in .env.
    exit /b 1
)

echo.
echo [Vellar] Healthy.
docker compose ps
call :report_build
echo.
echo [Vellar] The bot is running and will keep running after this window closes.
echo [Vellar] Stop it with stop.bat.
echo.
echo [Vellar] Following the log. Ctrl+C stops watching, not the bot.
echo.
docker compose logs -f --tail=40 bot
exit /b 0

rem ---------------------------------------------------------------------------
rem  Solo: one process on this machine, against a PostgreSQL installed on it.
rem
rem  Same code and same schema as the Docker stack - the migrations are the ones
rem  in migrations\, run here before the bot rather than by a container. What is
rem  missing is Redis, so the screen a player is on, the fight they are in and the
rem  map of a location are held by this process and end with it. Everything a
rem  character is made of is in PostgreSQL and outlives any number of restarts.
rem ---------------------------------------------------------------------------
:mode_solo
call :ensure_env || exit /b 1
call :ensure_hooks
rem The same stamp as the Docker path, so the log line says which tree this is
rem even when no image is involved.
call :stamp_build
call :ensure_uv       || exit /b 1
call :ensure_postgres || exit /b 1

echo [Vellar] Syncing dependencies...
uv sync
if errorlevel 1 exit /b 1

rem Real environment variables, so they win over whatever .env says.
set "APP_ENV=solo"
set "POSTGRES_DSN=%VELLAR_DSN%"

echo.
echo [Vellar] Bringing the schema up to date...
uv run alembic upgrade head
if errorlevel 1 (
    echo.
    echo [Vellar] The migration failed, so the bot was not started: it would have
    echo [Vellar] met a schema it does not know. Nothing in the database changed
    echo [Vellar] beyond the migrations that did apply.
    exit /b 1
)

echo.
echo [Vellar] Starting %VELLAR_BUILD% against PostgreSQL on this machine.
echo [Vellar] Characters, gold and bags are kept. The screen each player is on
echo [Vellar] and any fight in progress are not: a restart puts them in the main
echo [Vellar] menu, unhurt.
echo [Vellar] Ctrl+C stops the bot, and so does closing this window.
echo.
uv run python -m mmorpg.main
exit /b %ERRORLEVEL%

rem ---------------------------------------------------------------------------
rem  Create the role and the database solo mode expects. Run once, by hand.
rem
rem  This is the one thing that needs the PostgreSQL superuser, which is why it
rem  is not folded into the start: a launcher should not be asking for that
rem  password every time. Idempotent - run it twice and the second run does
rem  nothing (scripts\setup-db.sql).
rem ---------------------------------------------------------------------------
:mode_setup_db
call :ensure_env || exit /b 1
call scripts\vellar-tools.bat pgtools
if errorlevel 1 (
    call :no_postgres
    exit /b 1
)

set "SUPERUSER=%~2"
if "%SUPERUSER%"=="" set "SUPERUSER=postgres"
call scripts\vellar-tools.bat envvar POSTGRES_PASSWORD
if not defined ENV_VALUE set "ENV_VALUE=vellar"
set "VELLAR_PW=%ENV_VALUE%"

echo [Vellar] Creating the role "vellar" and the database "vellar", owned by it.
echo [Vellar] PostgreSQL will now ask for the password of its own superuser
echo [Vellar] "%SUPERUSER%" - the one set when you installed it, not the one in .env.
echo.
psql -U "%SUPERUSER%" -h localhost -d postgres -v ON_ERROR_STOP=1 -v role=vellar -v db=vellar -v pw="%VELLAR_PW%" -f "scripts\setup-db.sql"
if errorlevel 1 (
    echo.
    echo [Vellar] That did not go through. A wrong superuser password is the usual
    echo [Vellar] cause; a superuser called something other than "postgres" is the
    echo [Vellar] other, and takes: Start.bat setup-db yourname
    exit /b 1
)

echo.
echo [Vellar] Done. Start.bat solo runs the migrations and starts the game.
exit /b 0

rem ---------------------------------------------------------------------------
rem  Local: a single process with in-memory adapters.
rem ---------------------------------------------------------------------------
:mode_local
call :ensure_env || exit /b 1
call :ensure_hooks
rem Same stamp as the Docker path, so the log line says which tree this is even
rem when there is no image involved.
call :stamp_build

call :ensure_uv || exit /b 1

echo [Vellar] Syncing dependencies...
uv sync
if errorlevel 1 exit /b 1

echo.
echo [Vellar] Starting in local mode: no PostgreSQL, no Redis, nothing is saved.
echo [Vellar] Ctrl+C stops the bot.
echo.
rem A real environment variable, so it wins over whatever .env says.
set "APP_ENV=local"
uv run python -m mmorpg.main
exit /b %ERRORLEVEL%

rem ---------------------------------------------------------------------------
:mode_logs
call :ensure_docker || exit /b 1
docker compose logs -f --tail=100 bot
exit /b 0

rem ---------------------------------------------------------------------------
:mode_status
call :ensure_docker || exit /b 1
docker compose ps
call :stamp_build
echo.
echo [Vellar] This working tree: %VELLAR_BUILD%
call :report_build
exit /b 0

rem ---------------------------------------------------------------------------
rem  Helpers.
rem ---------------------------------------------------------------------------

:ensure_env
if not exist ".env" (
    if not exist ".env.example" (
        echo [Vellar] Neither .env nor .env.example exists; cannot continue.
        exit /b 1
    )
    copy /y ".env.example" ".env" >nul
    echo [Vellar] Created .env from .env.example.
)
rem An empty or still-templated token means Telegram will reject every call, and
rem it is far kinder to say so here than to let the bot fail on its first API call.
findstr /b /c:"BOT_TOKEN=" ".env" >nul 2>&1
if errorlevel 1 (
    echo [Vellar] BOT_TOKEN is missing from .env. Get a token from @BotFather and add it.
    exit /b 1
)
findstr /c:"replace-with-your-token" ".env" >nul 2>&1
if not errorlevel 1 (
    echo [Vellar] BOT_TOKEN in .env is still the placeholder.
    echo [Vellar] Get a token from @BotFather and put it in .env, then run this again.
    exit /b 1
)
exit /b 0

:ensure_hooks
rem Idempotent, and harmless outside a git checkout.
git rev-parse --git-dir >nul 2>&1
if errorlevel 1 exit /b 0
set "HOOKS="
for /f "usebackq delims=" %%h in (`git config core.hooksPath 2^>nul`) do set "HOOKS=%%h"
if /i "!HOOKS!"==".githooks" exit /b 0
git config core.hooksPath .githooks >nul 2>&1
echo [Vellar] Installed the pre-commit hook: commits now auto-apply ruff fixes,
echo [Vellar] then run mypy and the tests.
exit /b 0

:ensure_docker
docker version >nul 2>&1
if errorlevel 1 (
    echo [Vellar] Docker is not responding. Start Docker Desktop, wait for the
    echo [Vellar] whale icon to stop animating, then run this again.
    echo [Vellar] No Docker at all? "Start.bat" with no argument keeps the world
    echo [Vellar] in a PostgreSQL on this machine; "Start.bat local" keeps nothing.
    exit /b 1
)
exit /b 0

:ensure_uv
where uv >nul 2>&1
if errorlevel 1 (
    echo [Vellar] uv is not on PATH. Install it from https://docs.astral.sh/uv/
    exit /b 1
)
exit /b 0

rem ---------------------------------------------------------------------------
rem  A PostgreSQL on this machine that answers on POSTGRES_DSN and lets us in.
rem  Three different failures, three different answers - "it does not work" is
rem  never the useful sentence.
rem ---------------------------------------------------------------------------
:ensure_postgres
call scripts\vellar-tools.bat pgtools
if errorlevel 1 (
    call :no_postgres
    exit /b 1
)

call scripts\vellar-tools.bat envvar POSTGRES_DSN
if not defined ENV_VALUE set "ENV_VALUE=postgresql://vellar:vellar@localhost:5432/vellar"
set "VELLAR_DSN=%ENV_VALUE%"
rem Never leave the window hanging on a database that is not there.
set "PGCONNECT_TIMEOUT=5"

pg_isready -d "%VELLAR_DSN%" >nul 2>&1
if errorlevel 1 (
    echo [Vellar] PostgreSQL is installed but is not answering on %VELLAR_DSN%.
    echo [Vellar] Its service is probably stopped. From an administrator prompt:
    echo [Vellar]     net start postgresql-x64-17
    echo [Vellar] with the version number you installed. Or set it to start with
    echo [Vellar] Windows in services.msc, and it will be up before the game is.
    exit /b 1
)

psql "%VELLAR_DSN%" -tAc "select 1" >nul 2>&1
if errorlevel 1 (
    echo [Vellar] PostgreSQL is running but would not let this connection in.
    echo [Vellar] The role and the database are created once, with:
    echo [Vellar]     Start.bat setup-db
    echo [Vellar] If you changed POSTGRES_DSN in .env, that is what is being
    echo [Vellar] tried here - make it match the database you have.
    exit /b 1
)
exit /b 0

:no_postgres
echo [Vellar] No PostgreSQL was found on this machine, and solo mode keeps the
echo [Vellar] world in one. Install it from:
echo [Vellar]     https://www.postgresql.org/download/windows/
echo [Vellar] Take the defaults, remember the superuser password it asks for,
echo [Vellar] then run: Start.bat setup-db
echo [Vellar] Only trying a change out? "Start.bat local" needs nothing at all.
exit /b 0

:stamp_build
call scripts\vellar-tools.bat stamp
exit /b 0

:report_build
call scripts\vellar-tools.bat report
exit /b 0
