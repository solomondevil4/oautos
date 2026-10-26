@echo off
REM ---------------------------------------------------------------------------
REM  Launcher for dev_tools/backup_specs.py
REM  Used by Windows Task Scheduler, or double-click to back up right now.
REM  Optional args are passed through, e.g.  backup_specs.bat --list
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0.."

set "SCRIPT=%~dp0backup_specs.py"

if not exist "%SCRIPT%" (
    echo [backup] cannot find %SCRIPT%
    exit /b 1
)

REM 1) project virtualenv
if exist "%~dp0..\.venv\Scripts\python.exe" (
    "%~dp0..\.venv\Scripts\python.exe" "%SCRIPT%" %*
    exit /b %errorlevel%
)

REM 2) Python launcher (py -3)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%SCRIPT%" %*
    exit /b %errorlevel%
)

REM 3) python on PATH
where python >nul 2>nul
if not errorlevel 1 (
    python "%SCRIPT%" %*
    exit /b %errorlevel%
)

REM 4) WorkBuddy bundled interpreter (first version found)
for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
    if exist "%%~D\python.exe" (
        "%%~D\python.exe" "%SCRIPT%" %*
        exit /b %errorlevel%
    )
)

echo [backup] no python interpreter found
exit /b 1
