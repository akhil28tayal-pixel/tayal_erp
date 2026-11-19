<#
  Tayal ERP — apply a new version on the Windows PC.

  In PowerShell "Run as administrator":   powershell -ExecutionPolicy Bypass -File .\deploy\windows\update.ps1

  If this folder is connected to GitHub (git-setup.ps1), it first takes the latest version from GitHub.
  Otherwise unzip the new TayalERP-for-windows.zip over this folder first.
  Your backend\.env, backups, logs and data always stay.

  It takes a backup first, then updates the server, the database tables and the web dashboard, and restarts.
#>
#Requires -RunAsAdministrator
param([switch]$NoPull)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Backend = Join-Path $Root "backend"
$Web = Join-Path $Root "web"
$nssm = Join-Path $Root "tools\nssm.exe"
function Step($t) { Write-Host ""; Write-Host "==> $t" -ForegroundColor Cyan }
function Fail($t) { Write-Host "STOPPED: $t" -ForegroundColor Red; Write-Host "Starting the old version again."; cmd /c "`"$nssm`" start TayalERP-API >nul 2>&1"; cmd /c "`"$nssm`" start TayalERP-Web >nul 2>&1"; exit 1 }
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")

if ((Test-Path (Join-Path $Root ".git")) -and -not $NoPull) {
    Step "Latest version from GitHub"
    $me = (Get-FileHash $PSCommandPath).Hash
    Push-Location $Root
    $before = git rev-parse --short HEAD
    git fetch origin main
    if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Host "STOPPED: could not reach GitHub. Nothing changed." -ForegroundColor Red; exit 1 }
    git reset -q --hard origin/main
    $after = git rev-parse --short HEAD
    if ($before -eq $after) { Write-Host "Already the latest ($after); updating anyway." }
    else { git log --format="  %h %s" "$before..$after" }
    Pop-Location
    if ((Get-FileHash $PSCommandPath).Hash -ne $me) {  # this script itself changed: carry on with the new one
        & $PSCommandPath -NoPull
        exit $LASTEXITCODE
    }
}

Step "Backup before updating"
$py = Join-Path $Backend ".venv\Scripts\python.exe"
Push-Location $Backend
& $py -c "from app.modules.platform import jobs; j = jobs.run_backup(); print(j.status, j.file or j.detail)"
Pop-Location

# Stop a service and make sure it really stopped (a service that keeps crashing and restarting can refuse a normal
# stop with "cannot accept control messages"); if it won't, end its processes.
function Stop-Erp($name) {
    $s = Get-Service $name -ErrorAction SilentlyContinue
    if (-not $s -or $s.Status -eq "Stopped") { Write-Host "  $name already stopped"; return }
    cmd /c "sc.exe stop $name >nul 2>&1"   # asks and returns at once (nssm stop can wait a long time)
    for ($i = 0; $i -lt 20; $i++) {
        $s = Get-Service $name -ErrorAction SilentlyContinue
        if (-not $s -or $s.Status -eq "Stopped") { Write-Host "  $name stopped"; return }
        Start-Sleep -Seconds 1
    }
    $svcPid = (Get-CimInstance Win32_Service -Filter "Name='$name'").ProcessId
    if ($svcPid) { cmd /c "taskkill /PID $svcPid /T /F >nul 2>&1" }
    Start-Sleep -Seconds 2
    Write-Host "  $name stopped (forced)"
}

Step "Stopping"
Stop-Erp "TayalERP-Web"
Stop-Erp "TayalERP-API"

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
cmd /c "`"$nssm`" start TayalERP-API >nul 2>&1"
cmd /c "`"$nssm`" start TayalERP-Web >nul 2>&1"
Start-Sleep -Seconds 5
& (Join-Path $PSScriptRoot "status.ps1")
