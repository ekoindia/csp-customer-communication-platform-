@echo off
REM ============================================================
REM   CSP Platform - MANUAL UPDATE  (double-click, hands-off)
REM
REM   Use this ONLY when the automatic (admin-portal) update isn't
REM   available - e.g. Eko sends the CSP an update package file directly.
REM
REM   HOW THE CSP USES IT:
REM     1. Put the update file (CSP_Update.zip) into this same folder
REM        (C:\CSP_Platform), OR just drag the .zip onto this UPDATE.bat.
REM     2. Double-click UPDATE.bat.
REM   It then, on its own: verifies the package, replaces ONLY the program
REM   code (your settings, data, WhatsApp login and keys are kept), installs
REM   any new libraries, updates the version, and restarts the app.
REM   Nothing to type.
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM --- pick the app's Python (the isolated .venv if present) ---
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

REM --- locate the update package ---
REM   priority: 1) a file dragged onto this .bat (%1)
REM             2) CSP_Update.zip in this folder
REM             3) the single newest *.zip in this folder (excluding our own build output)
set "ZIP="
if not "%~1"=="" set "ZIP=%~1"
if not defined ZIP if exist "CSP_Update.zip" set "ZIP=%CD%\CSP_Update.zip"
if not defined ZIP (
    for /f "delims=" %%F in ('dir /b /a-d /o-d "*.zip" 2^>nul') do (
        if not defined ZIP if /I not "%%F"=="CSP_Platform.zip" set "ZIP=%CD%\%%F"
    )
)

if not defined ZIP (
    echo ============================================================
    echo   No update file found.
    echo   Put the update file ^(CSP_Update.zip^) in this folder:
    echo       %CD%
    echo   then double-click UPDATE.bat again.
    echo ============================================================
    pause
    exit /b 1
)

echo ============================================================
echo   CSP Platform - applying update
echo   Package: %ZIP%
echo   Please keep this window open...
echo ============================================================
echo.

REM --- apply: stage + verify + code swap (data/config preserved) + dep sync ---
"%PY%" -m core.updater --apply-zip "%ZIP%"
if errorlevel 1 (
    echo.
    echo [X] Update could not be applied. Your existing installation is unchanged.
    echo     Please contact support with the message shown above.
    pause
    exit /b 1
)

REM --- success: remove the consumed package and restart the app ---
del /q "%ZIP%" >nul 2>&1
echo.
echo [OK] Update applied. Restarting the app on the new version...
echo     If a dashboard/WhatsApp window from before is still open, close it.
start "" "%~dp0run.bat"
echo.
echo You can close this window.
timeout /t 5 /nobreak >nul
exit /b 0
