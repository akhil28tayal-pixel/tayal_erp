<#
  Tayal ERP — apply a new version on the Windows PC.

  In PowerShell "Run as administrator":   powershell -ExecutionPolicy Bypass -File .\deploy\windows\update.ps1

  If this folder is connected to GitHub (git-setup.ps1), it first takes the latest version from GitHub.
  Otherwise unzip the new TayalERP-for-windows.zip over this folder first.
  Your backend\.env, backups, logs and data always stay.

  It takes a backup first, then updates the server, the database tables and the web dashboard, and restarts.
#>
#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Backend = Join-Path $Root "backend"
$Web = Join-Path $Root "web"
$nssm = Join-Path $Root "tools\nssm.exe"
function Step($t) { Write-Host ""; Write-Host "==> $t" -ForegroundColor Cyan }
function Fail($t) { Write-Host "STOPPED: $t" -ForegroundColor Red; Write-Host "Starting the old version again."; & $nssm start TayalERP-API | Out-Null; & $nssm start TayalERP-Web | Out-Null; exit 1 }
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")

if (Test-Path (Join-Path $Root ".git")) {
    Step "Latest version from GitHub"
    Push-Location $Root
    $before = git rev-parse --short HEAD
    git fetch origin main
    if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Host "STOPPED: could not reach GitHub. Nothing changed." -ForegroundColor Red; exit 1 }
    git reset -q --hard origin/main
    $after = git rev-parse --short HEAD
    if ($before -eq $after) { Write-Host "Already the latest ($after); updating anyway." }
    else { git log --format="  %h %s" "$before..$after" }
    Pop-Location
}

Step "Backup before updating"
$py = Join-Path $Backend ".venv\Scripts\python.exe"
Push-Location $Backend
& $py -c "from app.modules.platform import jobs; j = jobs.run_backup(); print(j.status, j.file or j.detail)"
Pop-Location

Step "Stopping"
& $nssm stop TayalERP-Web | Out-Null
& $nssm stop TayalERP-API | Out-Null

Step "Server"
Push-Location $Backend
try {
    & $py -m pip install --quiet -e .
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }
    & $py -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { Fail "database update failed" }
    & $py -m app.cli seed-base | Out-Null  # new permissions or units, if any
    if ($LASTEXITCODE -ne 0) { Fail "seed-base failed" }
} finally { Pop-Location }

Step "Web dashboard"
Push-Location $Web
try {
    $env:API_URL = "http://127.0.0.1:8000"
    $env:NEXT_TELEMETRY_DISABLED = "1"
    & npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { Fail "npm ci failed" }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { Fail "web build failed" }
} finally { Pop-Location }

Step "Starting"
& $nssm start TayalERP-API | Out-Null
& $nssm start TayalERP-Web | Out-Null
Start-Sleep -Seconds 5
& (Join-Path $PSScriptRoot "status.ps1")
