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

REM Install source: a SLIM (~2-3 MB) code-only package served by the Eko admin
REM server itself (the same host the CSP already reaches for OCR). We do NOT use
REM the GitHub whole-repo zip anymore: it was ~83 MB (mostly server-only OCR
REM models the CSP doesn't need) and frequently FAILED to download on CSP
REM networks. The admin route builds this zip on the fly from the server's own
REM checkout, minus the OCR models / dev / secret files. The download step below
REM handles the single top folder inside the zip.
set "APP_URL=http://122.176.147.78:8080/csp-admin/download/csp_app.zip"
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

REM ---------- 3. FRESH install into C:\CSP_Platform ----------
REM CLEAN refresh: every PROGRAM file is replaced with the new version and stale
REM Python bytecode is wiped, so NO old / error-prone code can linger or shadow
REM the new code. The CSP's DATA is kept: cases database, encryption keys, and
REM WhatsApp login. The Eko connection (.env) is ALWAYS rewritten below with the
REM fresh key, so a re-issued key takes effect and the old (revoked) key is gone.
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
REM Stop a running dashboard / WhatsApp bridge FIRST, so no file is locked while
REM we replace the program files (a locked .pyd is what leaves a half-updated,
REM broken install behind).
taskkill /F /IM pythonw.exe >nul 2>&1
taskkill /F /IM python.exe  >nul 2>&1
taskkill /F /IM node.exe    >nul 2>&1
echo Installing a FRESH copy (old program files replaced; your data + WhatsApp login kept) ...
REM Remove stale compiled bytecode so a renamed/removed module can't shadow new code.
for /d /r "%INSTALL_DIR%" %%d in (__pycache__) do if exist "%%d" rmdir /s /q "%%d" 2>nul
REM /XD core\models (server-only OCR weights) + .wa_session (WhatsApp login kept).
REM /XF keeps the cases DB + keys from being touched; all code + config.py refresh.
robocopy "%SRCDIR%" "%INSTALL_DIR%" /E /NFL /NDL /NJH /NJS /NP ^
    /XD "%SRCDIR%\core\models" ".wa_session" ^
    /XF "secret.key" "pii.key" "csp_platform.db" "*.db" >nul

REM ---------- 3b. (Re)write the Eko connection with the FRESH key ----------
REM ALWAYS overwrite .env (no "if not exist" guard) so a freshly re-issued key
REM replaces any old/revoked one immediately. This is what makes a re-run a true
REM "old setup out, new setup in" rather than silently keeping a dead key.
REM SERVER_OCR_ENABLED must be on: no local OCR engine ships with a CSP install.
REM NOTE: never put a REM (or anything with brackets) INSIDE the ( ... ) > file
REM block below, and never put an IF inside it either. A ")" in a comment closes
REM the block early and the remaining lines are mis-parsed — that is exactly how
REM the literal text "ADMIN_API_BASE=REPLACE-ADMIN-API-BASE" once ended up in a
REM CSP's .env and broke its OCR ("No scheme supplied"). Write the fixed lines in
REM one clean block, then append the optional line afterwards.
if not "%CSP_ID%"=="REPLACE-CSP-ID" if not "%API_KEY%"=="REPLACE-API-KEY" (
    (
        echo ADMIN_CSP_ID=%CSP_ID%
        echo ADMIN_API_KEY=%API_KEY%
        echo ADMIN_REPORT_ENABLED=1
        echo SERVER_OCR_ENABLED=1
    ) > "%INSTALL_DIR%\.env"
    call :write_api_base
    echo Connected CSP %CSP_ID% to Eko with the fresh key.
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

REM ------------------------------------------------------------
:write_api_base
REM Append ADMIN_API_BASE only when this file was generated with a REAL address.
REM Left as the placeholder (the normal case) we write NOTHING, so config.py's
REM built-in Eko address is used — writing the placeholder text would produce an
REM invalid URL and kill OCR on that CSP.
if "%ADMIN_API_BASE%"=="REPLACE-ADMIN-API-BASE" goto :eof
if "%ADMIN_API_BASE%"=="" goto :eof
>>"%INSTALL_DIR%\.env" echo ADMIN_API_BASE=%ADMIN_API_BASE%
goto :eof
