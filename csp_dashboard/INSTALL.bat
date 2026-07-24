@echo off
REM ============================================================
REM   CSP Platform - ONE-CLICK INSTALLER  (installs to C:\CSP_Platform)
REM
REM   The CSP double-clicks this ONE file. It:
REM     1. Copies the software into  C:\CSP_Platform  (a permanent home on the
REM        C: drive - NOT the Desktop, NOT run from VS Code).
REM     2. Installs everything needed - Python, Node.js, and the light app
REM        dependencies - with no manual downloads (uses winget). NO OCR engine
REM        is installed here: scanned documents are OCR'd on the Eko server.
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
    REM core\models (OnnxTR/custom OCR weights, ~87 MB) is EXCLUDED: OCR runs on
    REM the Eko server, so the CSP never needs the model files locally.
    robocopy "%SRC%" "%INSTALL_DIR%" /E /NFL /NDL /NJH /NJS /NP ^
        /XD ".git" ".venv" "node_modules" "__pycache__" ".pytest_cache" "admin_portal" "admin_dashboard" "tests" "scripts" "data" "update" ".wa_session" "%SRC%\core\models" ^
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

REM ---------- 2. (OCR engine) — nothing to install ----------
REM Scanned PDFs/images are OCR'd on the Eko central server, not on this PC, so
REM there is NO Tesseract / docTR / OnnxTR install here. CSV/Excel/typed-PDF are
REM parsed locally with no OCR at all. This is what keeps the install small/fast.
echo [OK] OCR runs on the Eko server - nothing to install locally.

REM ---------- 3. Node.js LTS (WhatsApp sending) ----------
where node >nul 2>&1 && goto node_done
call :ensure_node
:node_done
where node >nul 2>&1 && (echo [OK] Node.js ready.) || (echo [!] Node.js not set up - WhatsApp sending can be enabled later; the dashboard still runs without it.)

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
            REM OCR runs on the Eko server (no local OCR engine ships) - required
            REM or scanned uploads extract 0 rows.
            echo SERVER_OCR_ENABLED=1
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
REM The icon runs CSP_Platform.vbs via wscript (windowless) so a single click
REM opens the app with NO black cmd window on screen (see CSP_Platform.vbs).
REM IMPORTANT: this installer runs ELEVATED (admin), so the per-user "Desktop"
REM folder would be the ADMIN account's, not the logged-in CSP's -> the icon
REM would land on the wrong desktop and look "missing". So we create it on the
REM ALL-USERS (Public) Desktop + Start Menu (visible to every account, incl. the
REM CSP's), and also the current-user Desktop as a fallback. Errors are printed
REM (no 2>nul) so a failure is visible instead of silently producing no icon.
powershell -NoProfile -Command ^
  "$w=New-Object -ComObject WScript.Shell;" ^
  "$vbs='%INSTALL_DIR%\CSP_Platform.vbs';" ^
  "$paths=New-Object System.Collections.ArrayList;" ^
  "foreach($f in 'CommonDesktopDirectory','Desktop','CommonPrograms','Programs'){ try{ $d=[Environment]::GetFolderPath($f); if($d){ [void]$paths.Add($d+'\CSP Platform.lnk') } }catch{} };" ^
  "$made=0; foreach($p in $paths){ try{ $s=$w.CreateShortcut($p); $s.TargetPath='wscript.exe'; $s.Arguments=$vbs; $s.WorkingDirectory='%INSTALL_DIR%'; $s.IconLocation='%ICON%'; $s.Description='CSP Communication Platform'; $s.Save(); $made++; Write-Host ('  created: '+$p) } catch { Write-Host ('  skip: '+$p+' ('+$_.Exception.Message+')') } };" ^
  "if($made -eq 0){ Write-Host '[!] Could not create the icon anywhere.' } else { Write-Host ('[OK] App icon created ('+$made+' location(s)).') }"
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
start "" wscript.exe "%INSTALL_DIR%\CSP_Platform.vbs"
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
REM 2) Nothing usable — install Python 3.11. We DON'T use winget: its package
REM    servers (delivery-optimization CDN) are frequently blocked/slow on CSP
REM    networks and winget then HANGS with no download (seen live on a deploy PC).
REM    Instead we fetch the official python.org installer directly over plain
REM    HTTPS — the exact path that reliably pulled the app package — and install
REM    it silently. All-users (we're elevated) so the `py` launcher is global.
echo Downloading Python 3.11 from python.org ...
powershell -NoProfile -Command ^
  "try{ [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '%TEMP%\python311-setup.exe' -UseBasicParsing }catch{ Write-Host $_.Exception.Message; exit 1 }"
if exist "%TEMP%\python311-setup.exe" (
    echo Installing Python 3.11 ^(silent^) ...
    "%TEMP%\python311-setup.exe" /quiet InstallAllUsers=1 PrependPath=1 Include_launcher=1 Include_pip=1
    del /q "%TEMP%\python311-setup.exe" >nul 2>&1
)
call :detect_python
goto :eof

REM ------------------------------------------------------------
:detect_python
REM Resolve a real python.exe path (never "py -3.11"): py launcher first, then
REM the known all-users/per-user install dirs, then a bare `python` if it is 3.10-3.12.
for /f "delims=" %%P in ('py -3.11 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%P"
if not defined PY for %%D in ("%ProgramFiles%\Python311" "%LOCALAPPDATA%\Programs\Python\Python311" "%ProgramFiles%\Python312" "%LOCALAPPDATA%\Programs\Python\Python312") do if exist "%%~D\python.exe" set "PY=%%~D\python.exe"
if not defined PY ( python -c "import sys;raise SystemExit(0 if (3,10)<=sys.version_info[:2]<=(3,12) else 1)" >nul 2>&1 && set "PY=python" )
goto :eof

REM ------------------------------------------------------------
:ensure_node
REM Same story as Python: winget's servers hang on CSP networks, so pull the
REM official Node.js LTS MSI directly over HTTPS and install it silently.
echo Downloading Node.js LTS from nodejs.org ...
powershell -NoProfile -Command ^
  "try{ [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://nodejs.org/dist/v20.18.1/node-v20.18.1-x64.msi' -OutFile '%TEMP%\node-lts.msi' -UseBasicParsing }catch{ Write-Host $_.Exception.Message; exit 1 }"
if exist "%TEMP%\node-lts.msi" (
    echo Installing Node.js LTS ^(silent^) ...
    msiexec /i "%TEMP%\node-lts.msi" /quiet /norestart
    set "PATH=%PATH%;%ProgramFiles%\nodejs"
    del /q "%TEMP%\node-lts.msi" >nul 2>&1
)
goto :eof
