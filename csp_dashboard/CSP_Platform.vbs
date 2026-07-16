' ============================================================
'  CSP Platform launcher — opens the dashboard with NO visible window.
'  The Desktop "CSP Platform" icon points here, so a single double-click
'  starts the app and NO black cmd window ever appears on screen (lighter +
'  cleaner on a 4 GB PC). The dashboard opens by itself in the browser.
'
'  The WhatsApp sender is NOT started here — it stays off during upload/OCR to
'  save memory. Start it later from the dashboard's Settings tab -> "Start
'  WhatsApp" (also windowless) only when your cases are ready and you want to
'  send.
' ============================================================
Option Explicit
Dim sh, fso, base, py

Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Run from the folder this script lives in (so the app's relative paths — DB,
' config, uploads — resolve correctly).
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base

' Prefer the app's own virtual-env Python; fall back to system python.
py = base & "\.venv\Scripts\python.exe"
If Not fso.FileExists(py) Then py = "python"

' 1) Apply any staged self-update BEFORE launching (hidden window, wait for it).
'    Safe here — the app's files aren't loaded/locked yet.
sh.Run """" & py & """ -m core.updater --apply-if-pending", 0, True

' 2) Start the dashboard with its console HIDDEN (window style 0). We use
'    python.exe (not pythonw) so stdout/stderr stay valid — just not shown —
'    which avoids Flask/werkzeug logging errors. No cmd window is visible.
sh.Run """" & py & """ app.py", 0, False

' 3) Open the dashboard in the browser once Flask is actually listening (a small
'    hidden PowerShell waiter — no window). Polls up to ~30s, then opens it.
sh.Run "powershell -NoProfile -WindowStyle Hidden -Command ""$u='http://127.0.0.1:5000'; for($i=0;$i -lt 60;$i++){ try{ Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 2 | Out-Null; Start-Process $u; break } catch { Start-Sleep -Milliseconds 500 } }""", 0, False
