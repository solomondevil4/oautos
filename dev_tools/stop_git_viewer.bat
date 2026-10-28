@echo off
REM Stop the local Git viewer (the one started at boot, or by git_viewer.bat).
REM All comments are ASCII on purpose: cmd parses this file as GBK, so
REM non-ASCII text here would break the script on some systems.
setlocal enabledelayedexpansion
set "PORT=8765"
set "FOUND="

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do set "FOUND=%%a"

if not defined FOUND (
  echo [i] Nothing is listening on 127.0.0.1:%PORT% - the viewer is already stopped.
  goto :end
)

echo [i] Stopping viewer on port %PORT% ^(PID %FOUND%^) ...
taskkill /F /PID %FOUND% >nul 2>&1
if errorlevel 1 (
  echo [!] Could not stop PID %FOUND%. Try running this as administrator.
) else (
  echo [+] Stopped.
  echo [i] It will start again the next time you sign in to Windows.
  echo [i] To disable auto start, run: dev_tools\autostart.bat off
)

:end
echo.
echo Press any key to close.
pause >nul
endlocal
