@echo off
REM ---------------------------------------------------------------------------
REM  Launcher for dev_tools/sync_snapshot.py
REM  Sync self-built changes to the oautos/Forme snapshot branch.
REM  Only files that really differ from the upstream are collected.
REM  Optional args are passed through, e.g.
REM      sync_snapshot.bat --status
REM      sync_snapshot.bat --dry-run
REM
REM  NOTE: keep this file ASCII-only. cmd.exe reads .bat as GBK on zh-CN
REM  Windows, so UTF-8 Chinese comments corrupt the whole script.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0.."
title OAS snapshot sync

set "SCRIPT=%~dp0sync_snapshot.py"

if not exist "%SCRIPT%" (
    echo [snapshot] cannot find %SCRIPT%
    pause
    exit /b 1
)

if exist "%~dp0..\toolkit\python.exe" (
    "%~dp0..\toolkit\python.exe" "%SCRIPT%" %*
) else (
    python "%SCRIPT%" %*
)

REM keep the window open on failure so the error stays readable
if errorlevel 1 pause
exit /b %errorlevel%
