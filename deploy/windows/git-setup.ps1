<#
  Tayal ERP - connect this PC's copy to GitHub (once). After this, update.ps1 takes the latest version from GitHub.

  In PowerShell "Run as administrator", from the TayalERP folder:
     powershell -ExecutionPolicy Bypass -File .\deploy\windows\git-setup.ps1 -RepoUrl https://github.com/<you>/tayal_erp.git

  Your backend\.env, data, backups and logs are not in git and are left alone.
#>
#Requires -RunAsAdministrator
param([Parameter(Mandatory = $true)][string]$RepoUrl)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
function Ok($m) { Write-Host "  OK  $m" -ForegroundColor Green }
function Fail($m) { Write-Host "STOPPED: $m" -ForegroundColor Red; exit 1 }
function RefreshPath { $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") }

RefreshPath
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "Installing Git..."
    winget install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements
    RefreshPath
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "Git did not install. Install it from git-scm.com, then run this again." }
}
Ok (git --version)

# The folder was made by your Windows user but this runs as administrator: tell git that is fine.
git config --global --add safe.directory ($Root -replace '\\', '/')
git config --global credential.helper manager

Set-Location $Root
if (-not (Test-Path (Join-Path $Root ".git"))) {
    git init -q -b main
    git remote add origin $RepoUrl
} else {
    git remote set-url origin $RepoUrl
}
Write-Host "Fetching from GitHub (a browser window may ask you to sign in to GitHub the first time)..."
git fetch origin main
if ($LASTEXITCODE -ne 0) { Fail "Could not read $RepoUrl. Check the address and that you signed in with the GitHub account that owns it." }
# Make this folder exactly the GitHub version (files not in git, like backend\.env, stay as they are).
git reset -q --hard origin/main
git branch -q -u origin/main main
Ok "this folder now follows $RepoUrl ($(git log -1 --format='%h %s'))"

Write-Host ""
Write-Host "Now updating the ERP to this version..." -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "update.ps1")
