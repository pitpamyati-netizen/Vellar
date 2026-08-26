@echo off
rem ============================================================================
rem Остановить стек Vellar.
rem
rem stop.bat            сохранить всё, потом остановить; мир остаётся
rem stop.bat purge      остановить И удалить всех персонажей, вещи и бои
rem
rem Обычная остановка сохраняет три вещи, и в таком порядке:
rem
rem 1. временное состояние — где стоит каждый игрок, бой, который он ведёт,
rem    предложения, ждущие в группе. Redis выписывает это до того, как контейнер
rem    просят остановиться.
rem 2. постоянное состояние — персонажи, золото, сумки, задания, работа в ремёслах —
rem    выгружается в backups\ таким, каким оно на эту минуту, вместе с изменениями.
rem 3. сами контейнеры, остановленные со своей отсрочкой, чтобы обновление, уже
rem    бывшее в полёте, доработало, а не оборвалось.
rem
rem Здесь не удаляется ни один том, поэтому Start.bat возвращает мир ровно таким, каким
rem он был. Purge — единственное исключение, и он спрашивает.
rem
rem Без стека останавливать здесь нечего: игра, запущенная через «Start.bat solo»
rem или «Start.bat local», — это процесс в собственном окне, и останавливает его
rem Ctrl+C там. Этот скрипт всё равно отрабатывает и делает дамп: мир solo лежит в
rem PostgreSQL этой машины, и скопировать его оттуда можно ровно как контейнерный.
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
rem Контейнерам достаётся их stop_grace_period, чтобы доработать обновления, бывшие в
rem полёте, прежде чем их убьют.
docker compose down
if errorlevel 1 exit /b 1

echo.
echo [Vellar] Stopped. Start.bat brings it back with everything intact.
echo [Vellar] The dump above is a second copy: the database volume is untouched.
exit /b 0

rem ---------------------------------------------------------------------------
rem Ни один контейнер не работает. Либо не работает ничто, либо игра — это процесс в
rem собственном окне. Остановить тот процесс отсюда нельзя, да и не нужно: в режиме
rem solo мир пишется в PostgreSQL по ходу дела. Дамп всё равно снимается — это та
rem копия, которую можно унести.
rem ---------------------------------------------------------------------------
:no_stack
echo [Vellar] No stack is running in Docker.
echo [Vellar] A game started with "Start.bat solo" or "Start.bat local" is the
echo [Vellar] process in its own window: Ctrl+C there stops it, and a solo world
echo [Vellar] is already on disk the moment each action happens.
echo.
call scripts\vellar-tools.bat backup
rem Отсутствие базы здесь значит, что сохранять было нечего, а это не отказ остановки —
rem это ответ на вопрос «есть ли что сохранять».
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
