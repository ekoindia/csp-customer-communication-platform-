@echo off
REM ============================================================
REM   EKO SIDE - build an offline update file to SEND to a CSP.
REM
REM   Double-click this. It produces  CSP_Update.zip  right here (a clean copy
REM   of the app code only - no secrets, no database, no customer data). Send
REM   that ONE file to the CSP. They drop it into C:\CSP_Platform and
REM   double-click UPDATE.bat - it merges the new code and keeps their
REM   settings/data/WhatsApp login.
REM
REM   (Normally you don't need this at all: just `git push` and the CSP's
REM   UPDATE.bat pulls the latest from GitHub itself. Use this only for an
REM   offline / no-internet CSP.)
REM ============================================================
setlocal
cd /d "%~dp0"
set "OUT=%CD%\CSP_Update.zip"

echo Building CSP_Update.zip (code only, no secrets/data)...
powershell -NoProfile -Command ^
  "$root='%CD%';" ^
  "$stage=Join-Path $env:TEMP ('csp_upd_'+[guid]::NewGuid().ToString('N').Substring(0,8));" ^
  "New-Item -ItemType Directory -Path $stage | Out-Null;" ^
  "$xd=@(\"$root\.venv\",\"$root\.git\",\"$root\.pytest_cache\",\"$root\whatsapp\.wa_session\",\"$root\whatsapp\node_modules\",\"$root\tests\",\"$root\scripts\",\"$root\data\",\"$root\update\");" ^
  "$xf=@('CSP_Platform.zip','CSP_Update.zip','secret.key','pii.key','.env','*.db','*.db-shm','*.db-wal','*.db-journal','*.bak','*.pyc','*.onnx.bak','DxDiag.txt','*.log');" ^
  "& robocopy $root $stage /E /XD $xd '__pycache__' /XF $xf /NFL /NDL /NJH /NJS /NP | Out-Null;" ^
  "if(Test-Path '%OUT%'){Remove-Item '%OUT%' -Force};" ^
  "Compress-Archive -Path (Join-Path $stage '*') -DestinationPath '%OUT%' -Force;" ^
  "Remove-Item $stage -Recurse -Force;" ^
  "$mb=[math]::Round((Get-Item '%OUT%').Length/1MB,2);" ^
  "Write-Host ('Done -> CSP_Update.zip  ('+$mb+' MB)')"

if not exist "%OUT%" (
    echo [X] Build failed.
    pause & exit /b 1
)
echo.
echo ============================================================
echo   CSP_Update.zip is ready in this folder.
echo   Send it to the CSP. They drop it into C:\CSP_Platform and
echo   double-click UPDATE.bat.
echo ============================================================
pause
exit /b 0
