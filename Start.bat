@echo off
rem ============================================================================
rem  Vellar launcher.
rem
rem    Start.bat            the real stack in Docker: PostgreSQL, Redis, the bot
rem    Start.bat local      one process, in-memory, no Docker and no database
rem    Start.bat logs       follow the running bot's log
rem    Start.bat status     what is running right now
rem
rem  The Docker path is the one to use for actual players: state survives a
rem  restart and the bot is restarted automatically if it dies or wedges.
rem  The local path is for trying a change quickly - it forgets everything on
rem  exit, so never leave players on it.
rem ============================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Vellar

set "MODE=%~1"
if "%MODE%"=="" set "MODE=docker"

if /i "%MODE%"=="docker" goto mode_docker
if /i "%MODE%"=="local"  goto mode_local
if /i "%MODE%"=="logs"   goto mode_logs
if /i "%MODE%"=="status" goto mode_status

echo Usage: Start.bat [docker^|local^|logs^|status]
exit /b 2

rem ---------------------------------------------------------------------------
rem  Docker: the full stack.
rem ---------------------------------------------------------------------------
:mode_docker
call :ensure_env    || exit /b 1
call :ensure_hooks
call :ensure_docker || exit /b 1

echo [Vellar] Building the image and starting PostgreSQL, Redis and the bot...
echo [Vellar] The first build downloads the base images and takes a few minutes.
echo.
rem --wait blocks until every service is healthy and the migration has finished.
rem The bot only reports healthy once its event loop is beating, so reaching this
rem point means it is genuinely serving - see src/mmorpg/health.py.
docker compose up -d --build --wait --wait-timeout 300
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
echo.
echo [Vellar] The bot is running and will keep running after this window closes.
echo [Vellar] Stop it with stop.bat.
echo.
echo [Vellar] Following the log. Ctrl+C stops watching, not the bot.
echo.
docker compose logs -f --tail=40 bot
exit /b 0

rem ---------------------------------------------------------------------------
rem  Local: a single process with in-memory adapters.
rem ---------------------------------------------------------------------------
:mode_local
call :ensure_env || exit /b 1
call :ensure_hooks

where uv >nul 2>&1
if errorlevel 1 (
    echo [Vellar] uv is not on PATH. Install it from https://docs.astral.sh/uv/
    exit /b 1
)

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
    echo [Vellar] No Docker? Use "Start.bat local" instead.
    exit /b 1
)
exit /b 0
