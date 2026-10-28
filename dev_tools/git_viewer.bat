@echo off
REM ---------------------------------------------------------------------------
REM  Launcher for dev_tools/git_viewer.py
REM  Double-click to start the local web UI (opens browser automatically).
REM  Close the console window to stop the server.
REM  Optional args are passed through, e.g.  git_viewer.bat --port 9000
REM
REM  NOTE: keep this file ASCII-only. cmd.exe reads .bat as GBK on zh-CN
REM  Windows, so UTF-8 Chinese comments corrupt the whole script.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0.."
title Git Commit Viewer

set "SCRIPT=%~dp0git_viewer.py"

if not exist "%SCRIPT%" (
    echo [viewer] cannot find %SCRIPT%
    pause
    exit /b 1
)

REM 1) project virtualenv
if exist "%~dp0..\.venv\Scripts\python.exe" (
    "%~dp0..\.venv\Scripts\python.exe" "%SCRIPT%" %*
    goto :done
)

REM 2) OAS bundled interpreter
if exist "%~dp0..\toolkit\python.exe" (
    "%~dp0..\toolkit\python.exe" "%SCRIPT%" %*
    goto :done
)

REM 3) Python launcher (py -3)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%SCRIPT%" %*
    goto :done
)

REM 4) python on PATH
where python >nul 2>nul
if not errorlevel 1 (
    python "%SCRIPT%" %*
    goto :done
)

REM 5) WorkBuddy bundled interpreter (first version found)
for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
    if exist "%%~D\python.exe" (
        "%%~D\python.exe" "%SCRIPT%" %*
        goto :done
    )
)

echo [viewer] no python interpreter found
pause
exit /b 1

:done
REM keep the window open on failure so the error stays readable
if errorlevel 1 pause
exit /b %errorlevel%
