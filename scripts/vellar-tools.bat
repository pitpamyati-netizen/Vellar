@echo off
rem ============================================================================
rem  Shared routines for Start.bat and stop.bat.
rem
rem    call scripts\vellar-tools.bat stamp    set VELLAR_BUILD from the git tree
rem    call scripts\vellar-tools.bat report   say which build is actually running
rem    call scripts\vellar-tools.bat flush    force Redis to write its state out
rem    call scripts\vellar-tools.bat backup   pg_dump into backups\, keep 20
rem    call scripts\vellar-tools.bat running  errorlevel 0 if the stack is up
rem    call scripts\vellar-tools.bat pgtools  put a local psql/pg_dump on PATH
rem    call scripts\vellar-tools.bat envvar X read X out of .env into ENV_VALUE
rem
rem  backup works both ways round: through the container while the Docker stack
rem  is up, and through a PostgreSQL installed on this machine when there is no
rem  stack at all (docs/adr/0010-a-machine-without-containers.md). The file it
rem  writes is the same either way, which is what lets one be restored into the
rem  other.
rem
rem  Deliberately no setlocal: the caller needs VELLAR_BUILD and BACKUP_FILE back.
rem  The caller is expected to have cd'd to the repository root already.
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
rem  Which working tree a build would come from. "-dirty" covers uncommitted and
rem  untracked files alike, because both end up inside the image.
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
rem  Read the stamp back out of the container that is serving right now, and
rem  prove it is the image that was just built rather than a leftover.
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
rem  Redis holds where every player is standing and every fight in progress. The
rem  append-only log is flushed every second and again on shutdown; SAVE writes a
rem  snapshot on top of it, so the last second cannot be the one that is lost.
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
rem  A dump of everything permanent, before anything is stopped or replaced.
rem ---------------------------------------------------------------------------
:backup
set "BACKUP_FILE="
if not exist "backups" mkdir "backups"
for /f "usebackq delims=" %%t in (`powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"`) do set "STAMP=%%t"
set "BACKUP_FILE=backups\vellar-%STAMP%.sql"

rem The container holds the database while the stack is up; a PostgreSQL on this
rem machine holds it when there is no stack. Which one is asked is decided here
rem rather than by the caller, so stop.bat reads the same either way.
call :running
if errorlevel 1 goto :backup_here

docker compose exec -T postgres pg_dump -U vellar -d vellar --clean --if-exists --no-owner > "%BACKUP_FILE%"
if errorlevel 1 (
    del /q "%BACKUP_FILE%" 2>nul
    set "BACKUP_FILE="
    echo [Vellar] PostgreSQL did not answer, so no backup was written.
    exit /b 1
)

set "CHARACTERS=?"
for /f "usebackq delims=" %%n in (`docker compose exec -T postgres psql -U vellar -d vellar -tAc "select count(*) from characters" 2^>nul`) do set "CHARACTERS=%%n"
echo [Vellar] Saved %CHARACTERS% character(s) to %BACKUP_FILE%.
goto :prune

rem ---------------------------------------------------------------------------
rem  The same dump, taken from a PostgreSQL installed on this machine instead.
rem ---------------------------------------------------------------------------
:backup_here
call :pgtools
if errorlevel 1 (
    del /q "%BACKUP_FILE%" 2>nul
    set "BACKUP_FILE="
    echo [Vellar] Nothing is running in Docker and there is no PostgreSQL on this
    echo [Vellar] machine either, so there is no database to dump.
    exit /b 1
)
call :envvar POSTGRES_DSN
if not defined ENV_VALUE set "ENV_VALUE=postgresql://vellar:vellar@localhost:5432/vellar"
set "VELLAR_DSN=%ENV_VALUE%"
rem Never sit waiting on a database that is not there: this runs inside a stop.
set "PGCONNECT_TIMEOUT=5"

pg_dump "%VELLAR_DSN%" --clean --if-exists --no-owner > "%BACKUP_FILE%" 2>nul
if errorlevel 1 (
    del /q "%BACKUP_FILE%" 2>nul
    set "BACKUP_FILE="
    echo [Vellar] PostgreSQL did not answer, so no backup was written.
    exit /b 1
)

set "CHARACTERS=?"
for /f "usebackq delims=" %%n in (`psql "%VELLAR_DSN%" -tAc "select count(*) from characters" 2^>nul`) do set "CHARACTERS=%%n"
echo [Vellar] Saved %CHARACTERS% character(s) to %BACKUP_FILE%.

:prune
rem Twenty is a month of daily stops and about as much disk as this deserves.
set /a KEPT=0
for /f "usebackq delims=" %%f in (`dir /b /a-d /o-d "backups\vellar-*.sql" 2^>nul`) do (
    set /a KEPT+=1
    call :prune_one "%%f"
)
exit /b 0

rem Called, not inlined: inside the loop above %KEPT% would expand once, at parse
rem time, and every file would be judged by the count before the first one.
:prune_one
if %KEPT% gtr 20 del /q "backups\%~1" 2>nul
exit /b 0

rem ---------------------------------------------------------------------------
rem  Whether the stack is up at all. Used to tell "nothing to do" apart from
rem  "something went wrong".
rem ---------------------------------------------------------------------------
:running
set "RUNNING_IDS="
for /f "usebackq delims=" %%c in (`docker compose ps -q 2^>nul`) do set "RUNNING_IDS=1"
if not defined RUNNING_IDS exit /b 1
exit /b 0

rem ---------------------------------------------------------------------------
rem  A PostgreSQL installed on this machine rather than pulled as an image. The
rem  Windows installer leaves its bin directory off PATH, so the newest one under
rem  Program Files is added for this process only - nothing on the machine is
rem  changed by running a batch file.
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
rem  One value out of .env, handed back in ENV_VALUE. The game itself never reads
rem  the environment except through Settings; this is for the scripts around it,
rem  which have to know where the database is before there is a process to ask.
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
