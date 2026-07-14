@echo off
REM ============================================================
REM  Starts the WhatsApp server (Baileys — no browser/Chromium).
REM  First run shows a QR code in this window — scan it once
REM  from WhatsApp -> Linked Devices. The session is then saved.
REM ============================================================

cd /d "%~dp0whatsapp"

if not exist "node_modules" (
    echo Installing WhatsApp server dependencies ^(first run only^)...
    call npm install
)

echo Starting WhatsApp server on http://127.0.0.1:3000 ...
node wa_server.js
pause
