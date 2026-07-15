@echo off
REM ============================================================
REM   CSP Platform - UPDATE  (double-click, hands-off)
REM
REM   THE EASY WAY (default): just double-click this file. It fetches the
REM   latest app straight from Eko's GitHub and updates itself. Eko only has
REM   to `git push` - nothing to send you.
REM
REM   OFFLINE / no-internet: if Eko hands you an update file, put CSP_Update.zip
REM   in this folder (C:\CSP_Platform) OR drag it onto this UPDATE.bat, then
REM   double-click. It uses that file instead of the internet.
REM
REM   EITHER WAY it replaces ONLY the program code - your settings, data,
REM   WhatsApp login and keys are kept - installs any new libraries, and
REM   restarts the app. Nothing to type. Then click the "CSP Platform" desktop
REM   icon to use the updated dashboard.
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

REM ---- find an offline update file (dragged arg, or a .zip in this folder) ----
set "ZIP="
if not "%~1"=="" set "ZIP=%~1"
if not defined ZIP if exist "CSP_Update.zip" set "ZIP=%CD%\CSP_Update.zip"
if not defined ZIP (
    for /f "delims=" %%F in ('dir /b /a-d /o-d "*.zip" 2^>nul') do (
        if not defined ZIP if /I not "%%F"=="CSP_Platform.zip" set "ZIP=%CD%\%%F"
    )
)

echo ============================================================
echo   CSP Platform - Update
echo   Please keep this window open...
echo ============================================================
echo.

if defined ZIP (
    echo Using update file: %ZIP%
    "%PY%" -m core.updater --apply-zip "%ZIP%"
    set "RC=!errorlevel!"
    if !RC! EQU 0 del /q "%ZIP%" >nul 2>&1
) else (
    echo Fetching the latest version from the internet ^(GitHub^)...
    "%PY%" -m core.updater --from-github
    set "RC=!errorlevel!"
)

if not "!RC!"=="0" (
    echo.
    echo [X] Update could not be applied. Your existing installation is unchanged.
    echo     Check your internet connection and try again, or contact support.
    pause
    exit /b 1
)

echo.
echo [OK] Update done. Restarting the app on the new version...
echo     If a dashboard/WhatsApp window from before is still open, close it.
start "" "%~dp0run.bat"
echo.
echo You can close this window.
timeout /t 5 /nobreak >nul
exit /b 0
