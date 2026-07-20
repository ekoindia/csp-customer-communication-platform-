@echo off
REM ============================================================
REM   CSP Platform - UPDATE  (double-click, hands-off)
REM
REM   Just double-click this whenever Eko has released a new version.
REM   It fetches the latest app from Eko's GitHub and updates itself:
REM   replaces ONLY the program code - your settings, data, WhatsApp login
REM   and keys are kept - installs any new libraries, and restarts the app.
REM   Nothing to type. Then click the "CSP Platform" desktop icon to use it.
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo ============================================================
echo   CSP Platform - Update
echo   Fetching the latest version from the internet...
echo   Please keep this window open.
echo ============================================================
echo.

"%PY%" -m core.updater --from-github
if errorlevel 1 (
    echo.
    echo [X] Update could not be applied. Your existing installation is unchanged.
    echo     Check your internet connection and try again, or contact support.
    pause
    exit /b 1
)

REM Belt-and-braces: --from-github already checks the "CSP Platform" desktop /
REM Start-Menu icon and creates it only where it's MISSING. This second call
REM re-checks with the freshly-updated code (and, as the CSP's own user, on the
REM CSP's own desktop even if the original install put it on an admin account's).
REM It is a no-op when the icon is already there.
"%PY%" -m core.updater --make-icon

echo.
echo [OK] Update done. Restarting the app on the new version...
echo     If a dashboard/WhatsApp window from before is still open, close it.
start "" "%~dp0run.bat"
echo.
echo You can close this window.
timeout /t 5 /nobreak >nul
exit /b 0
