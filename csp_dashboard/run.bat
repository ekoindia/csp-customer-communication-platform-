@echo off
REM ============================================================
REM  CSP Platform — full launcher.
REM  Opens the WhatsApp server and the Dashboard in two windows.
REM ============================================================

cd /d "%~dp0"

REM --- Apply any staged self-update BEFORE launching. Safe here because the
REM     app's own files aren't loaded/locked yet (no half-updated process).
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m core.updater --apply-if-pending
) else (
    python -m core.updater --apply-if-pending 2>nul
)

echo Launching WhatsApp server window...
start "CSP WhatsApp Server" cmd /k start_whatsapp.bat

echo Waiting for WhatsApp server to initialise...
timeout /t 8 /nobreak >nul

echo Launching Dashboard window...
start "CSP Dashboard" cmd /k start_dashboard.bat

echo.
echo Both services are starting in separate windows.
echo   - WhatsApp server : http://127.0.0.1:3000  (scan QR on first run)
echo   - Dashboard       : http://127.0.0.1:5000  (opens in your browser automatically)
echo.
echo You can close this window.
