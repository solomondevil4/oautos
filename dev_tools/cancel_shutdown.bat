@echo off
REM ---------------------------------------------------------------------------
REM  Emergency: cancel a pending Windows shutdown.
REM  Double-click this file. It does not need Python and works even in the
REM  last seconds before shutdown.
REM  Keep this file ASCII-only: cmd.exe parses .bat as GBK on Chinese Windows.
REM  Use absolute paths so Git's own timeout.exe / other tools cannot hijack us.
REM ---------------------------------------------------------------------------

"%SystemRoot%\System32\shutdown.exe" /a > "%TEMP%\oas_cancel_shutdown.log" 2>&1
if errorlevel 1 (
  echo.
  echo   [!] Cancel failed, or no shutdown was pending.
  echo.
  type "%TEMP%\oas_cancel_shutdown.log"
  echo.
  echo       If it says shutdown is already in progress, it was too late.
  echo.
) else (
  echo.
  echo   [+] Pending shutdown cancelled.
  echo.
)
REM  Also drop the plan file, otherwise the web page would keep showing a
REM  countdown for a shutdown that no longer exists.
del "%TEMP%\oas_git_viewer_shutdown.json" >nul 2>&1
del "%TEMP%\oas_cancel_shutdown.log" >nul 2>&1
"%SystemRoot%\System32\timeout.exe" /t 4 /nobreak >nul 2>&1
