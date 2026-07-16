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

REM LOW-RAM DESIGN (4 GB deploy PC): only the Dashboard starts here. The WhatsApp
REM Node server (Baileys, ~130 MB) is NOT started at launch on purpose — it is not
REM needed for uploading, OCR or reviewing cases (the RAM-critical phase). Start it
REM ONLY when you are ready to SEND, via the "Start WhatsApp" desktop icon (or
REM start_whatsapp.bat). Sending is guarded: if WhatsApp isn't running the dashboard
REM tells you to start it. This keeps ~130 MB free during OCR on a small PC.
echo Launching Dashboard window (minimised) ...
start "CSP Dashboard" /min cmd /k start_dashboard.bat

echo.
echo Dashboard starting: http://127.0.0.1:5000  (opens in your browser automatically).
echo When your cases are ready and you want to SEND WhatsApp messages, open the
echo   dashboard's Settings tab and click "Start WhatsApp" (starts in the
echo   background, no window), then scan the QR once.
echo.
echo You can close this window.
