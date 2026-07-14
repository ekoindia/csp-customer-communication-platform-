# ============================================================
#  Build a clean distributable ZIP of the CSP Platform to send
#  to a CSP. Excludes dev-only / machine-specific files so the
#  target gets a fresh, small package. Run from the project root:
#      Right-click -> Run with PowerShell
#  (or:  powershell -ExecutionPolicy Bypass -File MAKE_ZIP.ps1 )
#  Output:  CSP_Platform.zip  (in this folder)
#  The CSP then: extract the ZIP -> double-click INSTALL.bat.
# ============================================================
$ErrorActionPreference = "Stop"
$root  = Split-Path -Parent $MyInvocation.MyCommand.Path
$stage = Join-Path $env:TEMP ("csp_stage_" + [System.Guid]::NewGuid().ToString("N").Substring(0,8))
$zip   = Join-Path $root "CSP_Platform.zip"

Write-Host "Staging a clean copy..." -ForegroundColor Cyan
New-Item -ItemType Directory -Path $stage | Out-Null

# Copy everything except dev-only / machine-specific items.
#   /XD = exclude directories,  /XF = exclude files
$excludeDirs = @(
    (Join-Path $root ".venv"),
    (Join-Path $root ".git"),
    (Join-Path $root ".pytest_cache"),
    (Join-Path $root "whatsapp\.wa_session"),
    (Join-Path $root "whatsapp\node_modules"),
    # (the Eko admin portal now lives in the separate code/admin_dashboard/ tree,
    #  a sibling of this csp_dashboard/ package, so it is never staged here)
    (Join-Path $root "tests"),
    (Join-Path $root "scripts"),
    # data/ holds the REAL scanned bank PDF used for OCR development — it contains
    # live customer PII and must NEVER be shipped to a CSP or published. Exclude it.
    (Join-Path $root "data"),
    (Join-Path $root "update")          # updater staging - machine-specific
)
# __pycache__ appears in many folders -> exclude by name (no path)
# Also exclude INTERNAL Eko documents (emails, recommendations, context) - these
# must NOT go to the CSP; the CSP only needs the app + installer.
robocopy $root $stage /E /XD $excludeDirs "__pycache__" `
    /XF "CSP_Platform.zip" "secret.key" "pii.key" ".env" "csp_platform.db" "*.pyc" "DxDiag.txt" `
        "EMAIL_*" "WhatsApp_*.docx" "WhatsApp_*.pdf" "WhatsApp_*.txt" `
        "CSP_WhatsApp_*.docx" "CONTEXT_FOR_CLAUDE.md" "PENDING.md" `
        "PROJECT_REPORT.md" "instructions.md" "*.tiff" `
        "ADMIN_PORTAL_ARCHITECTURE.md" "ADMIN_PORTAL_DESIGN.md" `
        "WHATSAPP_TEMPLATES_FOR_META.md" "EXTERNAL_DATA_REGISTER.md" `
        "PRODUCTIZATION_ROADMAP.md" "CSP_Setup.bat" "MAKE_ZIP.ps1" `
        "_icon_preview.png" `
    /NFL /NDL /NJH /NJS /NP | Out-Null

# robocopy exit codes 0-7 are success; 8+ are errors.
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with code $LASTEXITCODE" }

# Make sure required folders exist but are empty in the package.
foreach ($d in @("uploads", "uploads\drafts", "database")) {
    $p = Join-Path $stage $d
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p | Out-Null }
}

Write-Host "Compressing to CSP_Platform.zip..." -ForegroundColor Cyan
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zip -Force

Remove-Item $stage -Recurse -Force
$sizeMB = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host ""
Write-Host "Done -> $zip  ($sizeMB MB)" -ForegroundColor Green
Write-Host "Send this ZIP to the CSP. They extract it and double-click INSTALL.bat." -ForegroundColor Green
