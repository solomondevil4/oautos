@echo off
REM Manage the boot auto-start of the Git viewer.
REM   autostart.bat          -> show current status (same as double clicking)
REM   autostart.bat on       -> start automatically after sign in
REM   autostart.bat off      -> stop starting automatically
REM ASCII only on purpose: cmd parses this file as GBK.
cd /d "%~dp0.."
set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=status"
toolkit\python.exe dev_tools\git_viewer.py --autostart %ACTION%
echo.
echo Press any key to close.
pause >nul
