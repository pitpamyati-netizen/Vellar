@echo off
rem ============================================================================
rem  Запуск Vellar.
rem
rem Start.bat            игра: один процесс против PostgreSQL этой машины,
rem                      без Docker (то же, что «Start.bat solo»)
rem Start.bat local      один процесс, всё в памяти, база не нужна вовсе
rem Start.bat docker     полный стек в контейнерах: PostgreSQL, Redis, бот
rem Start.bat setup-db   завести роль и базу vellar, один раз
rem Start.bat logs       следить за журналом работающего контейнера
rem Start.bat status     что работает прямо сейчас
rem
rem Три способа запустить, и отличаются они тем, что переживает остановку.
rem
rem Путь solo — он же по умолчанию — сохраняет мир: персонажи, золото, сумки и
rem задания лежат в PostgreSQL, установленном на этой машине. Сессию он забывает:
rem перезапуск ставит всех в главное меню и обрывает начатый бой. Ни Docker, ни
rem Redis, один установщик (docs/adr/0010-a-machine-without-containers.md).
rem
rem Путь local — чтобы быстро попробовать правку: он забывает всё при выходе,
rem поэтому игроков на нём не оставляют.
rem
rem Путь Docker сохраняет обе половины, перезапускает бота, если тот умер или встал,
rem и именно так это работает на сервере. Запущенный на уже поднятом стеке, он
rem пересобирает бота и подменяет его, не роняя PostgreSQL и Redis.
rem
rem Работает всегда это самое рабочее дерево, проштампованное тем коммитом, из
rem которого оно вышло, и штамп пишется в журнал на старте, чтобы на «моя ли
rem последняя правка сейчас крутится» был ответ, а не воспоминание. На пути Docker
rem несобравшаяся сборка останавливается здесь и не трогает то, что уже обслуживает.
rem ============================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Vellar

set "MODE=%~1"
rem Solo, потому что именно ему не нужно ничего, кроме PostgreSQL. Стек — осознанный
rem выбор, и он назван прямо: «Start.bat docker».
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
rem  Docker: полный стек.
rem ---------------------------------------------------------------------------
:mode_docker
call :ensure_env    || exit /b 1
call :ensure_hooks
call :ensure_docker || exit /b 1
call :stamp_build

echo [Vellar] Building the image from this working tree (%VELLAR_BUILD%)...
echo [Vellar] The first build downloads the base images and takes a few minutes.
echo.
rem Собирается до того, как что-либо останавливают. Несобравшаяся сборка тогда не стоит
rem ничего: что обслуживало, то и продолжает обслуживать, а этот скрипт так и говорит
rem вместо того, чтобы оставить наполовину поднятый стек.
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
rem --wait ждёт, пока каждая служба не станет здоровой, а миграции не доработают. Бот
rem отчитывается здоровым, только когда бьётся его цикл событий, поэтому дойти сюда
rem значит, что он и правда обслуживает, — см. src/mmorpg/health.py.
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
rem Solo: один процесс на этой машине, против установленного на ней PostgreSQL.
rem
rem Тот же код и та же схема, что у стека Docker: миграции — те же, из migrations\,
rem и прогоняются здесь перед ботом, а не контейнером. Нет здесь Redis, поэтому экран,
rem на котором стоит игрок, бой, который он ведёт, и карта локации держатся этим
rem процессом и кончаются вместе с ним. Всё, из чего сделан персонаж, лежит в
rem PostgreSQL и переживает сколько угодно перезапусков.
rem ---------------------------------------------------------------------------
:mode_solo
call :ensure_env || exit /b 1
call :ensure_hooks
rem Тот же штамп, что и на пути Docker, чтобы строка журнала называла дерево даже там,
rem где никакого образа нет.
call :stamp_build
call :ensure_uv       || exit /b 1
call :ensure_postgres || exit /b 1

echo [Vellar] Syncing dependencies...
uv sync
if errorlevel 1 exit /b 1

rem Настоящие переменные окружения, поэтому они сильнее того, что говорит .env.
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
rem Завести роль и базу, которых ждёт режим solo. Запускается один раз, руками.
rem
rem Это единственное, чему нужен суперпользователь PostgreSQL, — поэтому оно и не
rem вшито в запуск: пусковой скрипт не должен спрашивать тот пароль каждый раз.
rem Идемпотентно: запустите дважды, и второй раз не сделает ничего
rem (scripts\setup-db.sql).
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
rem Local: один процесс с адаптерами в памяти.
rem ---------------------------------------------------------------------------
:mode_local
call :ensure_env || exit /b 1
call :ensure_hooks
rem Тот же штамп, что и на пути Docker, чтобы строка журнала называла дерево даже там,
rem где образа нет вовсе.
call :stamp_build

call :ensure_uv || exit /b 1

echo [Vellar] Syncing dependencies...
uv sync
if errorlevel 1 exit /b 1

echo.
echo [Vellar] Starting in local mode: no PostgreSQL, no Redis, nothing is saved.
echo [Vellar] Ctrl+C stops the bot.
echo.
rem Настоящая переменная окружения, поэтому она сильнее того, что говорит .env.
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
rem Пустой или всё ещё шаблонный токен значит, что Telegram отвергнет каждый вызов, и
rem куда добрее сказать об этом здесь, чем дать боту упасть на первом же обращении к
rem API.
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
rem Идемпотентно и безвредно вне рабочей копии git.
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
rem PostgreSQL на этой машине, который отвечает по POSTGRES_DSN и пускает нас внутрь.
rem Три разных отказа — три разных ответа: «не работает» полезной фразой не бывает
rem никогда.
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
rem Никогда не оставлять окно висеть на базе, которой нет.
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
