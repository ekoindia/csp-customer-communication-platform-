@echo off
REM ============================================================
REM   CSP Platform - ONE-CLICK INSTALLER  (installs to C:\CSP_Platform)
REM
REM   The CSP double-clicks this ONE file. It:
REM     1. Copies the software into  C:\CSP_Platform  (a permanent home on the
REM        C: drive - NOT the Desktop, NOT run from VS Code).
REM     2. Installs everything needed - Python, Node.js, Tesseract-OCR, and all
REM        app dependencies - with no manual downloads (uses winget).
REM     3. Puts a "CSP Platform" icon on the Desktop + Start Menu.
REM     4. Starts the app.
REM   From then on the CSP just double-clicks the Desktop icon.
REM   (A Windows security/UAC prompt may appear during installs - click Yes.)
REM ============================================================
setlocal EnableDelayedExpansion

set "INSTALL_DIR=C:\CSP_Platform"
set "SRC=%~dp0"
if "%SRC:~-1%"=="\" set "SRC=%SRC:~0,-1%"

REM ---------- 0. Relocate to C:\CSP_Platform (unless already there) ----------
if /I not "%SRC%"=="%INSTALL_DIR%" (
    echo ============================================================
    echo   Installing CSP Platform into  %INSTALL_DIR%
    echo ============================================================
    if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
    REM On a RE-RUN over an existing install, preserve the CSP's own settings,
    REM Eko connection, and PII key so a reinstall never resets the CSP name /
    REM login / admin link. (On a FRESH install these don't exist yet, so
    REM KEEPCFG is empty and config.py IS copied in — as it must be.)
    set "KEEPCFG="
    if exist "%INSTALL_DIR%\config.py" set "KEEPCFG=config.py .env pii.key"
    REM Copy the app tree, skipping dev/server-only + machine-specific + secret
    REM files so the CSP machine gets a clean customer-facing install.
    robocopy "%SRC%" "%INSTALL_DIR%" /E /NFL /NDL /NJH /NJS /NP ^
        /XD ".git" ".venv" "node_modules" "__pycache__" ".pytest_cache" "admin_portal" "admin_dashboard" "tests" "scripts" "data" "update" ".wa_session" ^
        /XF "secret.key" "*.db" "*.pyc" %KEEPCFG% >nul
    if not exist "%INSTALL_DIR%\uploads" mkdir "%INSTALL_DIR%\uploads"
    echo Copy complete. Continuing setup inside %INSTALL_DIR% ...
    echo.
    call "%INSTALL_DIR%\INSTALL.bat"
    exit /b %errorlevel%
)

REM ================= From here we ARE inside C:\CSP_Platform =================
cd /d "%INSTALL_DIR%"
echo ============================================================
echo   CSP Platform - installing dependencies (first run only)
echo   Location: %INSTALL_DIR%
echo   Please keep this window open. This can take a few minutes.
echo ============================================================
echo.

REM ---------- Hardware constraint (minimum spec) — checked UP FRONT ----------
REM The platform targets the 4 GB Dell Inspiron deploy PC as its FLOOR (see
REM config.py MIN_* constants). Check RAM + free disk before downloading Python/
REM Node/Tesseract, so a box below the HARD minimum fails fast with a clear
REM message, and a below-recommended box gets a heads-up but still installs.
echo Checking hardware ...
powershell -NoProfile -Command ^
  "$ram=[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB,1);" ^
  "$os=(Get-CimInstance Win32_OperatingSystem); $bits=$os.OSArchitecture;" ^
  "$drv=(Get-Item '%INSTALL_DIR%').PSDrive.Name; $free=[math]::Round((Get-PSDrive $drv).Free/1GB,1);" ^
  "Write-Host ('  OS: '+$os.Caption+' '+$bits+'   RAM: '+$ram+' GB   Free disk ('+$drv+':): '+$free+' GB');" ^
  "if($bits -notmatch '64'){ Write-Host '[X] A 64-bit version of Windows is required.'; exit 2 };" ^
  "if($ram -lt 3.0){ Write-Host '[X] Less than 3 GB RAM - too low to run the platform reliably.'; exit 2 };" ^
  "if($free -lt 3.0){ Write-Host '[X] Less than 3 GB free disk - free up some space and run again.'; exit 3 };" ^
  "if($ram -lt 3.5){ Write-Host '[!] Below 4 GB RAM - runs in light OCR mode; close other apps during a batch.' };" ^
  "exit 0"
if errorlevel 2 (
    echo.
    echo   ============================================================
    echo   This PC is below the MINIMUM hardware the CSP Platform needs:
    echo       Windows 10 64-bit  .  4 GB RAM  .  ~3 GB free disk
    echo   Please use a PC that meets this, then run the setup again.
    echo   ============================================================
    pause & exit /b 1
)
echo [OK] Hardware check passed.
echo.

REM --- winget available? (App Installer, present on current Windows 10/11) ---
where winget >nul 2>&1
if errorlevel 1 (
    echo [!] "winget" was not found on this PC. Please open Microsoft Store,
    echo     update "App Installer", then run this installer again. (Or install
    echo     Python 3.11, Node.js LTS and Tesseract-OCR manually once.)
    echo.
)

REM ---------- 1. Python 3.11 ----------
call :ensure_python
if not defined PY (
    echo [X] Python could not be set up automatically. Please install Python 3.11
    echo     from https://www.python.org/downloads/ ^(tick "Add to PATH"^) and re-run.
    pause & exit /b 1
)
echo [OK] Python: %PY%

REM ---------- 2. Tesseract-OCR (reads scanned documents) ----------
if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" goto tess_done
where tesseract >nul 2>&1 && goto tess_done
echo Installing Tesseract-OCR ...
where winget >nul 2>&1 && winget install -e --id UB-Mannheim.TesseractOCR --silent --accept-package-agreements --accept-source-agreements
:tess_done
echo [OK] Tesseract step done.

REM ---------- 3. Node.js LTS (WhatsApp sending) ----------
where node >nul 2>&1 && goto node_done
echo Installing Node.js LTS ...
where winget >nul 2>&1 && winget install -e --id OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements
set "PATH=!PATH!;%ProgramFiles%\nodejs"
:node_done
echo [OK] Node.js step done.

REM ---------- 4. App environment + Python dependencies ----------
if not exist ".venv\Scripts\python.exe" (
    echo Creating the app environment ...
    "%PY%" -m venv .venv
)
set "VPY=.venv\Scripts\python.exe"
echo Installing app dependencies ...
"%VPY%" -m pip install --upgrade pip
"%VPY%" -m pip install -r requirements-lite.txt
if errorlevel 1 (
    echo [X] Dependency install failed. Check the internet connection and re-run.
    pause & exit /b 1
)

REM ---------- 5. WhatsApp bridge dependencies ----------
where node >nul 2>&1 && (
    echo Installing WhatsApp bridge dependencies ...
    pushd whatsapp
    call npm install
    popd
)

REM ---------- 6. Readiness check ----------
echo.
echo ------------------------------------------------------------
"%VPY%" deploy_check.py
echo ------------------------------------------------------------

REM ---------- 7. Connect to Eko Admin Portal (optional, one-time) ----------
REM Skipped entirely if .env already exists (a re-run, or Eko pre-baked one
REM into this package) - never overwrites an existing connection. Only the
REM CSP ID + API Key are asked here; the admin server address itself is
REM already correct in config.py (same for every CSP - see config.py's
REM ADMIN_API_BASE comment), so it is never asked.
if not exist ".env" (
    echo.
    echo ============================================================
    echo   Optional: connect this install to the Eko Admin Portal
    echo   ^(Eko will have given you a CSP ID and an API Key^)
    echo   Press ENTER on both to skip - you can do this later from
    echo   the dashboard's Settings tab instead.
    echo ============================================================
    set "CSPID="
    set "APIKEY="
    set /p CSPID="  CSP ID: "
    set /p APIKEY="  API Key: "
    if not "!CSPID!"=="" if not "!APIKEY!"=="" (
        (
            echo ADMIN_CSP_ID=!CSPID!
            echo ADMIN_API_KEY=!APIKEY!
            echo ADMIN_REPORT_ENABLED=1
        ) > ".env"
        echo   Saved - this install will report to Eko's admin portal.
    ) else (
        echo   Skipped - you can connect later from Settings in the dashboard.
    )
)

REM ---------- 8. Desktop + Start-Menu shortcut (the app icon) ----------
echo Creating the "CSP Platform" app icon ...
set "ICON=%INSTALL_DIR%\installer\CSP_Platform.ico"
if not exist "%ICON%" set "ICON=%SystemRoot%\System32\shell32.dll,13"
powershell -NoProfile -Command ^
  "$w=New-Object -ComObject WScript.Shell;" ^
  "$t='%INSTALL_DIR%\run.bat';" ^
  "foreach($p in @([Environment]::GetFolderPath('Desktop')+'\CSP Platform.lnk', [Environment]::GetFolderPath('Programs')+'\CSP Platform.lnk')){" ^
  "  $s=$w.CreateShortcut($p); $s.TargetPath=$t; $s.WorkingDirectory='%INSTALL_DIR%'; $s.IconLocation='%ICON%'; $s.Description='CSP Communication Platform'; $s.Save() }" 2>nul
REM (No separate "Start WhatsApp" desktop icon: the WhatsApp sender is started
REM ON DEMAND from the dashboard's Settings tab -> "Start WhatsApp" button, which
REM launches it in the BACKGROUND with no window. Kept off during upload/OCR to
REM save RAM on the 4 GB PC — see run.bat.)

echo.
echo ============================================================
echo   Setup complete. The software now lives in:
echo       %INSTALL_DIR%
echo   Starting the app now...
echo.
echo   FIRST TIME: the app opens a one-time setup screen. There you
echo   choose your OWN login ID and password, and enter your branch
echo   details. Your login is also saved to CSP_Login.txt on the Desktop.
echo.
echo   From next time, just double-click the "CSP Platform" icon
echo   on the Desktop to open the software.
echo ============================================================
start "" "%INSTALL_DIR%\run.bat"
echo.
pause
exit /b 0

REM ------------------------------------------------------------
:ensure_python
REM Land on a Python 3.10-3.12 interpreter — those are the versions with matching
REM prebuilt wheels for this numpy 2.1 / opencv 4.10 / onnxruntime 1.19 stack. A
REM wrong version (e.g. 3.9 or 3.13) would force pip to build from source, which is
REM slow and can OOM the 4 GB box. This box (Dell Inspiron 3268, Win10) may ship a
REM NEWER python (e.g. 3.13) or none, so we DON'T trust bare `python` — we pin 3.11
REM via the Windows `py` launcher and resolve its real exe path (safe to quote).
REM 1) A supported version already present? Prefer the py launcher (most reliable),
REM    resolving the actual python.exe so `set PY=` is a real path, never "py -3.11".
for %%V in (3.11 3.12 3.10) do (
    for /f "delims=" %%P in ('py -%%V -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%P"
    if defined PY goto :eof
)
REM    (no launcher) accept a bare `python` only if it is itself 3.10-3.12.
python -c "import sys;raise SystemExit(0 if (3,10)<=sys.version_info[:2]<=(3,12) else 1)" >nul 2>&1 && ( set "PY=python" & goto :eof )
REM 2) Nothing usable — install 3.11 (user scope = no admin needed).
echo Installing Python 3.11 ...
where winget >nul 2>&1 && winget install -e --id Python.Python.3.11 --silent --scope user --accept-package-agreements --accept-source-agreements
REM 3) Re-detect after install: py launcher first, then the known install dirs.
for /f "delims=" %%P in ('py -3.11 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%P"
if not defined PY for %%D in ("%LOCALAPPDATA%\Programs\Python\Python311" "%ProgramFiles%\Python311" "%LOCALAPPDATA%\Programs\Python\Python312" "%ProgramFiles%\Python312") do if exist "%%~D\python.exe" set "PY=%%~D\python.exe"
if not defined PY ( python -c "import sys;raise SystemExit(0 if (3,10)<=sys.version_info[:2]<=(3,12) else 1)" >nul 2>&1 && set "PY=python" )
goto :eof
