@echo off
REM ============================================================
REM   CSP Platform - ONLINE SETUP  (the ONLY file sent to a CSP)
REM
REM   The CSP double-clicks THIS single file. It then automatically:
REM     1. Downloads the application package from the internet.
REM     2. Installs it into  C:\CSP_Platform  (a permanent C: drive home).
REM     3. Downloads + installs Python, Node.js + the light app deps (no OCR
REM        engine — scanned documents are OCR'd on the Eko server).
REM     4. Connects to the Eko Admin Portal - CSP_ID/API_KEY below, if set,
REM        are written straight into .env, so INSTALL.bat's own connect
REM        prompt is skipped entirely (nothing left for the CSP to type).
REM     5. Puts a "CSP Platform" icon on the Desktop + Start Menu.
REM     6. Starts the app.
REM   Nothing has to be copied by hand, no key to send separately - this one
REM   file is fully self-contained.
REM   (A Windows security / UAC prompt may appear - click Yes / Run anyway.)
REM
REM   >>> Normally you never edit this by hand - generate it from the admin
REM       portal's "New CSP Setup" page (or the "API Keys" page's per-CSP
REM       download link), which fills APP_URL / CSP_ID / API_KEY in for you.
REM   >>> Manual edit (only if not using the admin portal's generator):
REM       APP_URL = the PUBLIC GitHub repo zipball
REM                 (https://github.com/<ORG>/<REPO>/archive/refs/heads/main.zip).
REM       CSP_ID / API_KEY = issued from the admin portal's "API Keys" page.
REM       Leave CSP_ID/API_KEY as the REPLACE-* placeholders to skip
REM       pre-configuring - the CSP will be asked once instead (by
REM       INSTALL.bat, or the dashboard's first-login screen).
REM ============================================================
setlocal EnableDelayedExpansion

REM ---------- 0. Elevate ONCE up front (fewer clicks for the CSP) ----------
REM The dependency installs below (Node, Tesseract via winget machine-scope)
REM otherwise each raise their own UAC prompt. Elevating here means the CSP sees
REM ONE "allow changes?" prompt at the start and none afterwards. Safe fallback:
REM if elevation is declined, we simply continue un-elevated (each install then
REM prompts on its own) — the setup still works, it just asks a couple more times.
if "%~1"=="::elevated" goto :afterelevate
net session >nul 2>&1
if not errorlevel 1 goto :afterelevate
set "CSP_SETUP_SELF=%~f0"
powershell -NoProfile -Command "try { Start-Process -FilePath $env:CSP_SETUP_SELF -ArgumentList '::elevated' -Verb RunAs } catch { exit 1 }"
set "CSP_SETUP_SELF="
if not errorlevel 1 exit /b
echo (Continuing without administrator rights - you may see a security prompt for
echo  each component that installs.)
:afterelevate

REM GitHub-direct install source: the CSP dashboard lives in a PUBLIC GitHub
REM repo, and GitHub serves the whole repo as a zip automatically at
REM  https://github.com/<ORG>/<REPO>/archive/refs/heads/<branch>.zip
REM So there is NO hand-built CSP_Platform.zip to host/share — just push
REM csp_dashboard/ to the repo and put its zipball URL below (fill ORG/REPO;
REM branch is usually "main"). The download step handles the single top folder
REM GitHub wraps the files in.
set "APP_URL=https://github.com/ekoindia/csp-customer-communication-platform-/archive/refs/heads/main.zip"
set "CSP_ID=REPLACE-CSP-ID"
set "API_KEY=REPLACE-API-KEY"
REM Leave this as the placeholder — the real admin server URL is already baked
REM into config.py (same for every CSP) and stays masked from the CSP.
set "ADMIN_API_BASE=REPLACE-ADMIN-API-BASE"

set "INSTALL_DIR=C:\CSP_Platform"
set "TMPZIP=%TEMP%\CSP_Platform_download.zip"
set "TMPX=%TEMP%\CSP_Platform_extract"

echo ============================================================
echo   CSP Platform - Online Setup
echo   Downloading and installing everything automatically...
echo   Please keep this window open (this can take a few minutes).
echo ============================================================
echo.

if "%APP_URL%"=="https://github.com/REPLACE-ORG/REPLACE-REPO/archive/refs/heads/main.zip" (
    echo [X] APP_URL is not set. Eko must put the PUBLIC GitHub repo zipball URL
    echo     in APP_URL before sending this file.
    pause & exit /b 1
)

REM ---------- 1. Download the application package ----------
echo Downloading application package ...
powershell -NoProfile -Command ^
  "try{ [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%APP_URL%' -OutFile '%TMPZIP%' -UseBasicParsing } catch { Write-Host $_.Exception.Message; exit 1 }"
if errorlevel 1 (
    echo [X] Download failed. Check the internet connection and the APP_URL.
    pause & exit /b 1
)

REM ---------- 2. Extract it ----------
echo Extracting ...
if exist "%TMPX%" rmdir /s /q "%TMPX%"
powershell -NoProfile -Command "Expand-Archive -Path '%TMPZIP%' -DestinationPath '%TMPX%' -Force"
if errorlevel 1 ( echo [X] Extract failed. & pause & exit /b 1 )

REM Locate INSTALL.bat inside the extracted tree and install from that folder.
REM The GitHub zipball wraps the repo in a single top folder (<repo>-<branch>\),
REM and the CSP app lives in its  csp_dashboard\  subfolder — so INSTALL.bat is
REM one or two levels deep. Find it wherever it is (only csp_dashboard has one).
set "SRCDIR=%TMPX%"
for /f "delims=" %%F in ('dir /b /s "%TMPX%\INSTALL.bat" 2^>nul') do set "SRCDIR=%%~dpF"
if "%SRCDIR:~-1%"=="\" set "SRCDIR=%SRCDIR:~0,-1%"

REM ---------- 3. Copy into C:\CSP_Platform ----------
REM On a FRESH machine everything is copied. On a RE-RUN of an already-set-up
REM CSP, the existing settings + data + WhatsApp login are PRESERVED.
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
set "KEEP="
if exist "%INSTALL_DIR%\config.py" (
    echo Existing install detected - keeping current settings, data and WhatsApp login.
    set "KEEP=/XF config.py secret.key csp_platform.db /XD .wa_session"
)
echo Installing application files into %INSTALL_DIR% ...
REM Exclude core\models (OnnxTR/custom OCR weights, ~87 MB): OCR runs on the Eko
REM server, so the CSP never needs the model files locally. (The whole-repo zip
REM may still contain them; this stops them landing in the install.)
robocopy "%SRCDIR%" "%INSTALL_DIR%" /E /NFL /NDL /NJH /NJS /NP /XD "%SRCDIR%\core\models" %KEEP% >nul

REM ---------- 3b. Pre-configure the Eko Admin Portal connection ----------
REM If this file was generated for a specific CSP (CSP_ID/API_KEY are not the
REM placeholders), write .env now so INSTALL.bat's own connect prompt (which
REM only runs "if not exist .env") is skipped - the CSP never sees it, never
REM types anything, never needs a separate key sent to them.
if not "%CSP_ID%"=="REPLACE-CSP-ID" if not "%API_KEY%"=="REPLACE-API-KEY" (
    if not exist "%INSTALL_DIR%\.env" (
        (
            echo ADMIN_CSP_ID=%CSP_ID%
            echo ADMIN_API_KEY=%API_KEY%
            echo ADMIN_REPORT_ENABLED=1
            if not "%ADMIN_API_BASE%"=="REPLACE-ADMIN-API-BASE" echo ADMIN_API_BASE=%ADMIN_API_BASE%
        ) > "%INSTALL_DIR%\.env"
        echo Pre-configured for CSP %CSP_ID% - connects to Eko automatically.
    )
)

REM ---------- 4. Hand off to the dependency installer (in place) ----------
if not exist "%INSTALL_DIR%\INSTALL.bat" (
    echo [X] Package did not contain INSTALL.bat. Check the CSP_Platform.zip build.
    pause & exit /b 1
)
echo Running dependency setup ...
echo.
call "%INSTALL_DIR%\INSTALL.bat"

REM ---------- 5. Cleanup temp ----------
if exist "%TMPZIP%" del /q "%TMPZIP%" >nul 2>&1
if exist "%TMPX%" rmdir /s /q "%TMPX%" >nul 2>&1
exit /b 0
