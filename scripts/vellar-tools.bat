@echo off
rem ============================================================================
rem Общие подпрограммы для Start.bat и stop.bat.
rem
rem call scripts\vellar-tools.bat stamp    поставить VELLAR_BUILD из дерева git
rem call scripts\vellar-tools.bat report   сказать, какая сборка работает сейчас
rem call scripts\vellar-tools.bat flush    заставить Redis выписать своё состояние
rem call scripts\vellar-tools.bat backup   проверенная копия в backups\, держать 20
rem call scripts\vellar-tools.bat running  errorlevel 0, если стек поднят
rem call scripts\vellar-tools.bat pgtools  добавить местные psql/pg_dump в PATH
rem call scripts\vellar-tools.bat envvar X прочитать X из .env в ENV_VALUE
rem
rem backup работает в обе стороны: через контейнер, пока поднят стек Docker, и через
rem PostgreSQL, установленный на этой машине, когда стека нет вовсе
rem (docs/adr/0010-a-machine-without-containers.md). Файл выходит один и тот же, и
rem именно это позволяет развернуть одно в другое.
rem
rem Нарочно без setlocal: вызывающему нужен обратно VELLAR_BUILD.
rem Предполагается, что вызывающий уже перешёл в корень репозитория.
rem ============================================================================
if "%~1"=="stamp"   goto :stamp
if "%~1"=="report"  goto :report
if "%~1"=="flush"   goto :flush
if "%~1"=="backup"  goto :backup
if "%~1"=="running" goto :running
if "%~1"=="pgtools" goto :pgtools
if "%~1"=="envvar"  goto :envvar_entry
echo [Vellar] vellar-tools: unknown routine "%~1".
exit /b 2

rem ---------------------------------------------------------------------------
rem Из какого рабочего дерева вышла бы сборка. «-dirty» покрывает и незакоммиченные,
rem и неотслеживаемые файлы: и те и другие оказываются внутри образа.
rem ---------------------------------------------------------------------------
:stamp
set "VELLAR_BUILD=unknown"
git rev-parse --git-dir >nul 2>&1
if errorlevel 1 exit /b 0
for /f "usebackq delims=" %%c in (`git rev-parse --short HEAD 2^>nul`) do set "VELLAR_BUILD=%%c"
set "VELLAR_DIRTY="
for /f "usebackq delims=" %%d in (`git status --porcelain 2^>nul`) do set "VELLAR_DIRTY=1"
if defined VELLAR_DIRTY set "VELLAR_BUILD=%VELLAR_BUILD%-dirty"
exit /b 0

rem ---------------------------------------------------------------------------
rem Прочитать штамп обратно из контейнера, который обслуживает прямо сейчас, и
rem убедиться, что это тот образ, который только что собрали, а не остаток.
rem ---------------------------------------------------------------------------
:report
set "RUNNING_BUILD="
for /f "usebackq delims=" %%v in (`docker compose exec -T bot printenv VELLAR_BUILD 2^>nul`) do set "RUNNING_BUILD=%%v"
if not defined RUNNING_BUILD (
    echo [Vellar] The bot is not answering, so its build cannot be read.
    exit /b 1
)
set "IMAGE_ID="
set "CONTAINER_IMAGE_ID="
for /f "usebackq delims=" %%i in (`docker image inspect vellar-bot:latest --format "{{.Id}}" 2^>nul`) do set "IMAGE_ID=%%i"
for /f "usebackq delims=" %%i in (`docker container inspect vellar-bot --format "{{.Image}}" 2^>nul`) do set "CONTAINER_IMAGE_ID=%%i"
echo [Vellar] Running build: %RUNNING_BUILD%
if not "%IMAGE_ID%"=="%CONTAINER_IMAGE_ID%" (
    echo [Vellar] ** The running container is NOT the image that was just built.
    echo [Vellar] ** Run "Start.bat docker" again to put the new one in its place.
    exit /b 1
)
exit /b 0

rem ---------------------------------------------------------------------------
rem Redis держит, где стоит каждый игрок и какой бой идёт. Журнал дописывания
rem сбрасывается раз в секунду и ещё раз при остановке; SAVE пишет снимок поверх
rem него, чтобы потерянной не оказалась именно последняя секунда.
rem ---------------------------------------------------------------------------
:flush
docker compose exec -T redis redis-cli SAVE >nul 2>&1
if errorlevel 1 (
    echo [Vellar] Redis did not answer; its own append-only log is still on disk.
    exit /b 1
)
echo [Vellar] Redis state written to disk: screens, fights and offers in flight.
exit /b 0

rem ---------------------------------------------------------------------------
rem Дамп всего постоянного, до того как что-либо остановлено или заменено.
rem ---------------------------------------------------------------------------
:backup
rem Один и тот же проверяемый путь для расписания и остановки. Ротация только
rem после восстановления; неудача сохраняет прежние годные копии.
pwsh -NoProfile -File scripts\backup.ps1
exit /b %errorlevel%

rem ---------------------------------------------------------------------------
rem Поднят ли стек вообще. Нужно, чтобы отличить «делать нечего» от «что-то пошло не
rem так».
rem ---------------------------------------------------------------------------
:running
set "RUNNING_IDS="
for /f "usebackq delims=" %%c in (`docker compose ps -q 2^>nul`) do set "RUNNING_IDS=1"
if not defined RUNNING_IDS exit /b 1
exit /b 0

rem ---------------------------------------------------------------------------
rem PostgreSQL, установленный на этой машине, а не притянутый образом. Установщик
rem Windows оставляет свой каталог bin вне PATH, поэтому самый свежий из тех, что под
rem Program Files, добавляется только для этого процесса: запуск .bat не меняет на
rem машине ничего.
rem ---------------------------------------------------------------------------
:pgtools
where psql >nul 2>&1
if not errorlevel 1 exit /b 0
for /f "delims=" %%d in ('dir /b /ad /o-n "%ProgramFiles%\PostgreSQL" 2^>nul') do (
    if exist "%ProgramFiles%\PostgreSQL\%%d\bin\psql.exe" (
        set "PATH=%ProgramFiles%\PostgreSQL\%%d\bin;%PATH%"
        exit /b 0
    )
)
exit /b 1

rem ---------------------------------------------------------------------------
rem Одно значение из .env, отданное обратно в ENV_VALUE. Сама игра читает окружение
rem только через Settings; это для скриптов вокруг неё, которым надо знать, где база,
rem раньше, чем появится процесс, у которого можно спросить.
rem ---------------------------------------------------------------------------
:envvar_entry
call :envvar "%~2"
exit /b %errorlevel%

:envvar
set "ENV_VALUE="
if not exist ".env" exit /b 1
for /f "usebackq tokens=1,* delims==" %%a in (`findstr /b /c:"%~1=" ".env"`) do set "ENV_VALUE=%%b"
if not defined ENV_VALUE exit /b 1
exit /b 0
