<#
  Tayal ERP — install on the Windows PC that runs Tally, reachable from anywhere through Cloudflare Tunnel.

  Run once, in PowerShell opened with "Run as administrator", from the unzipped folder (e.g. C:\TayalERP):

      powershell -ExecutionPolicy Bypass -File .\deploy\windows\install.ps1 `
          -TunnelToken "<token from Cloudflare>" -DataFile .\data\tayal_erp.dump

  It is safe to run again: what is already done is kept.

  What it does
    1. Installs (winget): PostgreSQL 16, Python 3.12, Node.js LTS, cloudflared, NSSM.
    2. Creates the database and its user; restores the data brought from the Mac (-DataFile).
    3. Writes backend\.env (production settings + the secrets brought from the Mac's .env).
    4. Prepares the server (Python) and builds the web dashboard (Node).
    5. Installs two Windows services that start with the PC and restart if they stop:
         TayalERP-API  (127.0.0.1:8000)   TayalERP-Web  (127.0.0.1:3000)
    6. Installs the Cloudflare Tunnel service (-TunnelToken), so https://erp.tayaltraders.co.in reaches the web dashboard.
       Nothing is opened on the router or the Windows firewall; the database, the API and Tally stay private.
    7. Points the ERP at Tally on this PC (http://127.0.0.1:9000) and stops the PC from sleeping.
#>
#Requires -RunAsAdministrator
param(
    [string]$TunnelToken = "",
    [string]$DataFile = "",
    [string]$PgSuperPassword = "",
    [string]$BackupDir = "",
    [string]$TallyUrl = "http://127.0.0.1:9000",
    [switch]$ReplaceData
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Backend = Join-Path $Root "backend"
$Web = Join-Path $Root "web"
$Logs = Join-Path $Root "logs"
$Secrets = Join-Path $Root "secrets"
if (-not $BackupDir) { $BackupDir = Join-Path $Root "backups" }
New-Item -ItemType Directory -Force -Path $Logs, $Secrets, $BackupDir | Out-Null

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "    $text" -ForegroundColor Green }
function Note($text) { Write-Host "    $text" -ForegroundColor Yellow }
function Fail($text) { Write-Host ""; Write-Host "STOPPED: $text" -ForegroundColor Red; exit 1 }

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

function New-Secret([int]$Length = 32) {
    $chars = [char[]]"ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    $bytes = New-Object byte[] $Length
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}

function Lock-File($path) {
    # only Administrators and the services (SYSTEM) can read it
    & icacls $path /inheritance:r /grant:r "*S-1-5-32-544:F" "*S-1-5-18:F" | Out-Null
}

function Has-Python312 {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) { return $false }
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $v = & py -3.12 --version 2>&1; return ($LASTEXITCODE -eq 0 -and "$v" -match "3\.12") }
    catch { return $false } finally { $ErrorActionPreference = $old }
}

function Winget-Install($id, [string[]]$extra = @()) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "winget is not available. Install 'App Installer' from the Microsoft Store, then run this again."
    }
    Write-Host "    installing $id ..."
    $wargs = @("install", "-e", "--id", $id, "--silent", "--accept-package-agreements", "--accept-source-agreements") + $extra
    & winget @wargs | Out-Host
    Refresh-Path
}

# ------------------------------------------------------------------------------------------------ 1. tools
Step "1/7  Programs"
Refresh-Path

$pgRoot = Get-ChildItem "C:\Program Files\PostgreSQL" -Directory -ErrorAction SilentlyContinue |
    Sort-Object { [int]($_.Name -replace "\D", "") } -Descending | Select-Object -First 1
$pwFile = Join-Path $Secrets "postgres-superuser.txt"
if (-not $pgRoot) {
    if (-not $PgSuperPassword) {
        $PgSuperPassword = New-Secret 24
        Set-Content -Path $pwFile -Value $PgSuperPassword -NoNewline
        Lock-File $pwFile
        Note "PostgreSQL 'postgres' password saved in $pwFile (keep it safe)"
    }
    Winget-Install "PostgreSQL.PostgreSQL.16" @("--override",
        "--mode unattended --unattendedmodeui none --superpassword $PgSuperPassword --servicename postgresql-16 --serverport 5432")
    $pgRoot = Get-ChildItem "C:\Program Files\PostgreSQL" -Directory -ErrorAction SilentlyContinue |
        Sort-Object { [int]($_.Name -replace "\D", "") } -Descending | Select-Object -First 1
    if (-not $pgRoot) { Fail "PostgreSQL did not install. Install PostgreSQL 16 from postgresql.org, then run this again with -PgSuperPassword." }
} elseif (-not $PgSuperPassword) {
    if (Test-Path $pwFile) { $PgSuperPassword = (Get-Content $pwFile -Raw).Trim() }
    else { Fail "PostgreSQL is already installed. Run again with -PgSuperPassword '<the postgres password>'." }
}
$PgBin = Join-Path $pgRoot.FullName "bin"
$psql = Join-Path $PgBin "psql.exe"
Ok "PostgreSQL: $PgBin"

if (-not (Has-Python312)) { Winget-Install "Python.Python.3.12" @("--scope", "machine") }
if (-not (Has-Python312)) { Fail "Python 3.12 is not available (py -3.12). Install it from python.org, then run this again." }
Ok ("Python: " + (& py -3.12 --version))

if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Winget-Install "OpenJS.NodeJS.LTS" }
$node = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $node) { Fail "Node.js is not available. Install the LTS version from nodejs.org, then run this again." }
Ok ("Node: " + (& $node --version))

$cfExe = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
if (-not $cfExe) {
    Winget-Install "Cloudflare.cloudflared"
    $cfExe = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
    if (-not $cfExe) { $cfExe = Get-ChildItem "C:\Program Files*\cloudflared\cloudflared.exe" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName }
}
if ($cfExe) { Ok "cloudflared: $cfExe" } else { Note "cloudflared not found; the tunnel step will be skipped" }

$nssm = Join-Path $Root "tools\nssm.exe"
if (-not (Test-Path $nssm)) {
    $found = (Get-Command nssm -ErrorAction SilentlyContinue).Source
    if (-not $found) {
        Winget-Install "NSSM.NSSM"
        $found = (Get-Command nssm -ErrorAction SilentlyContinue).Source
    }
    if (-not $found) {
        $found = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages", "C:\Program Files\WinGet\Packages" -Recurse -Filter nssm.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "win64" } | Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $found) { Fail "NSSM was not found. Download nssm.exe (win64) from nssm.cc into $Root\tools\, then run this again." }
    New-Item -ItemType Directory -Force -Path (Join-Path $Root "tools") | Out-Null
    Copy-Item $found $nssm -Force
}
Ok "NSSM: $nssm"

# ------------------------------------------------------------------------------------------------ 2. settings
Step "2/7  Settings (backend\.env)"
$envFile = Join-Path $Backend ".env"
$managed = @("ERP_ENV", "ERP_DATABASE_URL", "ERP_REDIS_URL", "ERP_BACKUP_DIR", "ERP_PG_BIN_DIR", "ERP_SCHEDULER_ENABLED")
$values = [ordered]@{}
foreach ($src in @((Join-Path $Root ".env"), $envFile)) {   # the Mac's .env first, then what this PC already has
    if (Test-Path $src) {
        foreach ($line in Get-Content $src) {
            $t = $line.Trim().TrimStart([char]0xFEFF)
            if ($t -and -not $t.StartsWith("#") -and $t.Contains("=")) {
                $k, $v = $t.Split("=", 2)
                $values[$k.Trim()] = $v
            }
        }
    }
}
$dbPassword = $null
if ($values.Contains("ERP_DATABASE_URL") -and $values["ERP_DATABASE_URL"] -match "tayal_erp:([^@]+)@127\.0\.0\.1") { $dbPassword = $Matches[1] }
if (-not $dbPassword) { $dbPassword = New-Secret 28 }
if (-not $values.Contains("ERP_JWT_SECRET") -or $values["ERP_JWT_SECRET"].Length -lt 32) { $values["ERP_JWT_SECRET"] = New-Secret 48 }
$values["ERP_ENV"] = "production"
$values["ERP_DATABASE_URL"] = "postgresql+psycopg://tayal_erp:$dbPassword@127.0.0.1:5432/tayal_erp"
$values["ERP_BACKUP_DIR"] = $BackupDir
$values["ERP_PG_BIN_DIR"] = $PgBin
$values["ERP_SCHEDULER_ENABLED"] = "true"
$lines = @("# Tayal ERP on this PC. Written by deploy\windows\install.ps1 - keep private.")
foreach ($k in $values.Keys) { if ($k -ne "ERP_REDIS_URL") { $lines += "$k=$($values[$k])" } }
[IO.File]::WriteAllLines($envFile, [string[]]$lines, (New-Object Text.UTF8Encoding $false))
Lock-File $envFile
$keys = ($values.Keys | Where-Object { $managed -notcontains $_ }) -join ", "
Ok "written; kept from before: $keys"
if (-not $values.Contains("ERP_WHATSAPP_TOKEN")) { Note "No WhatsApp settings found: copy the Mac's .env into $Root before installing to bring them over" }

# ------------------------------------------------------------------------------------------------ 3. database
Step "3/7  Database"
$env:PGPASSWORD = $PgSuperPassword
function Sql($sql, $db = "postgres") { & $psql -h 127.0.0.1 -U postgres -d $db -v ON_ERROR_STOP=1 -tAc $sql }
Sql "select 1" | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "Cannot log in to PostgreSQL as 'postgres'. Check -PgSuperPassword." }
if ((Sql "select 1 from pg_roles where rolname='tayal_erp'") -ne "1") {
    Sql "create role tayal_erp login password '$dbPassword'" | Out-Null
} else {
    Sql "alter role tayal_erp with login password '$dbPassword'" | Out-Null
}
if ((Sql "select 1 from pg_database where datname='tayal_erp'") -ne "1") {
    Sql "create database tayal_erp owner tayal_erp encoding 'UTF8' template template0" | Out-Null
    Ok "database created"
}
$tables = Sql "select count(*) from information_schema.tables where table_schema='public'" "tayal_erp"
if ($DataFile) {
    $DataFile = (Resolve-Path $DataFile).Path
    if ([int]$tables -gt 0 -and -not $ReplaceData) {
        Note "The database already has data, so $DataFile was NOT restored (use -ReplaceData to overwrite everything)."
    } else {
        if ([int]$tables -gt 0) {
            Sql "drop database tayal_erp with (force)" | Out-Null
            Sql "create database tayal_erp owner tayal_erp encoding 'UTF8' template template0" | Out-Null
        }
        & (Join-Path $PgBin "pg_restore.exe") -h 127.0.0.1 -U postgres -d tayal_erp --no-owner --no-acl --role=tayal_erp $DataFile
        if ($LASTEXITCODE -gt 1) { Fail "Restoring $DataFile failed" }
        Ok "data from the Mac restored"
    }
} elseif ([int]$tables -eq 0) {
    Note "Empty database and no -DataFile: the ERP will start empty."
}
Remove-Item Env:PGPASSWORD

# ------------------------------------------------------------------------------------------------ 4. server
Step "4/7  Server (Python)"
Push-Location $Backend
try {
    if (-not (Test-Path ".venv\Scripts\python.exe")) { & py -3.12 -m venv .venv }
    $py = Join-Path $Backend ".venv\Scripts\python.exe"
    & $py -m pip install --quiet --upgrade pip
    & $py -m pip install --quiet -e .
    if ($LASTEXITCODE -ne 0) { Fail "Installing the server's Python packages failed" }
    & $py -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { Fail "Updating the database tables failed" }
    # Roles, units, the company and the product catalogue. Each only adds what is missing, so this is safe on restored data too.
    foreach ($c in "seed-base", "seed-company", "seed-catalog") {
        & $py -m app.cli $c | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail "app.cli $c failed" }
    }
    Ok "ready"
} finally { Pop-Location }

# Tally is on this PC now
$env:PGPASSWORD = $PgSuperPassword
Sql "update system_settings set value = jsonb_build_object('v', '$TallyUrl') where key = 'tally.url'" "tayal_erp" | Out-Null
Remove-Item Env:PGPASSWORD
Ok "Tally address set to $TallyUrl"

# ------------------------------------------------------------------------------------------------ 5. web
Step "5/7  Web dashboard (Node)"
Push-Location $Web
try {
    $env:API_URL = "http://127.0.0.1:8000"
    $env:NEXT_TELEMETRY_DISABLED = "1"
    & npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { Fail "npm ci failed" }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { Fail "Building the web dashboard failed" }
    Ok "built"
} finally { Pop-Location }

# ------------------------------------------------------------------------------------------------ 6. services
Step "6/7  Windows services"
function Set-ErpService($name, $display, $exe, $params, $dir, [string[]]$envVars) {
    $existing = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($existing) { & $nssm stop $name | Out-Null } else { & $nssm install $name $exe | Out-Null }
    & $nssm set $name Application $exe | Out-Null
    & $nssm set $name AppParameters $params | Out-Null
    & $nssm set $name AppDirectory $dir | Out-Null
    & $nssm set $name DisplayName $display | Out-Null
    & $nssm set $name Description "Tayal ERP - $display" | Out-Null
    & $nssm set $name Start SERVICE_AUTO_START | Out-Null
    & $nssm set $name AppExit Default Restart | Out-Null
    & $nssm set $name AppRestartDelay 5000 | Out-Null
    & $nssm set $name AppStdout (Join-Path $Logs "$name.log") | Out-Null
    & $nssm set $name AppStderr (Join-Path $Logs "$name.log") | Out-Null
    & $nssm set $name AppRotateFiles 1 | Out-Null
    & $nssm set $name AppRotateOnline 1 | Out-Null
    & $nssm set $name AppRotateBytes 10485760 | Out-Null
    if ($envVars.Count -gt 0) { & $nssm set $name AppEnvironmentExtra @envVars | Out-Null }
    & $nssm start $name | Out-Null
    Ok "$name running"
}
Set-ErpService "TayalERP-API" "Tayal ERP server" (Join-Path $Backend ".venv\Scripts\python.exe") `
    "-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers --workers 1" $Backend @("PYTHONUNBUFFERED=1")
Set-ErpService "TayalERP-Web" "Tayal ERP web dashboard" $node `
    "node_modules\next\dist\bin\next start -p 3000 -H 127.0.0.1" $Web @("API_URL=http://127.0.0.1:8000", "NODE_ENV=production", "NEXT_TELEMETRY_DISABLED=1")

$up = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $a = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:8000/health/live"
        $w = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://127.0.0.1:3000/login"
        if ($a.StatusCode -eq 200 -and $w.StatusCode -eq 200) { $up = $true; break }
    } catch { Start-Sleep -Seconds 2 }
}
if ($up) { Ok "server and dashboard answer on this PC" } else { Note "Not answering yet - see $Logs\TayalERP-API.log and TayalERP-Web.log" }

# ------------------------------------------------------------------------------------------------ 7. tunnel + power
Step "7/7  Cloudflare Tunnel and power"
if ($TunnelToken -and $cfExe) {
    if (Get-Service -Name cloudflared -ErrorAction SilentlyContinue) {
        & $cfExe service uninstall | Out-Null
        Start-Sleep -Seconds 2
    }
    & $cfExe service install $TunnelToken | Out-Null
    Start-Sleep -Seconds 3
    $svc = Get-Service -Name cloudflared -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq "Running") { Ok "tunnel running" } else { Note "The tunnel service did not start - check the token" }
} elseif (Get-Service -Name cloudflared -ErrorAction SilentlyContinue) {
    Ok "tunnel already installed"
} else {
    Note "No -TunnelToken given: the ERP works on this PC only. Run again with -TunnelToken to put it online."
}
& powercfg /change standby-timeout-ac 0 | Out-Null
& powercfg /change hibernate-timeout-ac 0 | Out-Null
Ok "the PC will not sleep on mains power"

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  Open:            https://erp.tayaltraders.co.in   (everywhere, including this PC; needs the tunnel's public hostname"
Write-Host "                   127.0.0.1:3000 set in Cloudflare)"
Write-Host "  Phone app:       server address https://erp.tayaltraders.co.in"
Write-Host "  Backups:         $BackupDir (every night at 23:30)"
Write-Host "  Logs:            $Logs"
Write-Host "  Check later:     powershell -File .\deploy\windows\status.ps1"
