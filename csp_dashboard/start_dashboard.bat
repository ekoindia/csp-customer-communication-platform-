@echo off
REM ============================================================
REM  Starts the CSP Dashboard (Flask) on http://127.0.0.1:5000
REM ============================================================

cd /d "%~dp0"

REM Use a local virtual environment if present, else system Python.
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

REM Install Python dependencies if Flask is missing. Uses the LITE set (no
REM PyTorch/docTR) so a fresh low-end PC isn't forced to download ~2 GB; run
REM INSTALL.bat for the full one-time setup. Capable machines that want docTR OCR
REM can install requirements.txt manually.
%PY% -c "import flask" 2>nul
if errorlevel 1 (
    echo Installing Python dependencies ^(first run only^)...
    %PY% -m pip install -r requirements-lite.txt
)

REM Open the browser only AFTER Flask is actually listening — otherwise on a
REM slow 4 GB box the browser races ahead and shows "can't reach this page".
REM This waiter runs in the background (minimised) while the server starts in
REM this window; it polls up to ~30s, then opens the dashboard.
start "" /min powershell -NoProfile -Command "$u='http://127.0.0.1:5000'; for($i=0;$i -lt 60;$i++){ try{ Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 2 | Out-Null; Start-Process $u; break } catch { Start-Sleep -Milliseconds 500 } }"
echo Starting dashboard on http://127.0.0.1:5000 ...
%PY% app.py
pause
