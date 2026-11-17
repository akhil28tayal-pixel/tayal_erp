# Makes TallyPrime open by itself when this PC starts and the Windows user signs in, and reopen if it is closed.
#
#   powershell -ExecutionPolicy Bypass -File .\deploy\windows\tally-autostart.ps1
#   powershell -ExecutionPolicy Bypass -File .\deploy\windows\tally-autostart.ps1 -TallyExe "D:\TallyPrime\tally.exe"
#   powershell -ExecutionPolicy Bypass -File .\deploy\windows\tally-autostart.ps1 -Remove
#
# Run it as the Windows user who uses Tally on this PC (Tally is a window, so it runs in that user's session).
# Opening Ishika Traders Daily is a Tally setting: F1 > Settings > Startup > Load companies on startup (see README).
param(
    [string]$TallyExe = "",
    [int]$EveryMinutes = 10,
    [switch]$Remove
)
$ErrorActionPreference = "Stop"
$TaskName = "Tayal ERP - Start Tally"
function Ok($m) { Write-Host "  OK  $m" -ForegroundColor Green }
function Fail($m) { Write-Host "  !!  $m" -ForegroundColor Red; exit 1 }

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Ok "removed the '$TaskName' task"
    exit 0
}

# ---- find tally.exe
if (-not $TallyExe) {
    $running = Get-Process -Name "tally" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($running -and $running.Path) { $TallyExe = $running.Path }
}
if (-not $TallyExe) {
    $guesses = @("C:\Program Files\TallyPrime\tally.exe", "C:\Program Files (x86)\TallyPrime\tally.exe",
                 "C:\TallyPrime\tally.exe", "D:\TallyPrime\tally.exe", "C:\Tally.ERP9\tally.exe", "D:\Tally.ERP9\tally.exe")
    $TallyExe = $guesses | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $TallyExe) {
    foreach ($root in @("C:\Program Files", "C:\Program Files (x86)", "C:\", "D:\")) {
        if (-not (Test-Path $root)) { continue }
        $hit = Get-ChildItem $root -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "Tally*" } |
               ForEach-Object { Join-Path $_.FullName "tally.exe" } | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($hit) { $TallyExe = $hit; break }
    }
}
if (-not $TallyExe -or -not (Test-Path $TallyExe)) {
    Fail "Could not find tally.exe. Open Tally, then run this again (it finds the open Tally), or add -TallyExe ""<path to tally.exe>""."
}
$TallyExe = (Resolve-Path $TallyExe).Path
Ok "Tally: $TallyExe"

# ---- the scheduled task: at sign-in (after 1 minute) and every few minutes, start Tally if it isn't running
$launcher = Join-Path $PSScriptRoot "start-tally.ps1"
if (-not (Test-Path $launcher)) { Fail "start-tally.ps1 is missing next to this script" }
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcher`" -TallyExe `"$TallyExe`""
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $user
$atLogon.Delay = "PT1M"
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($atLogon, $repeat) -Principal $principal `
    -Settings $settings -Description "Opens TallyPrime at sign-in and reopens it if closed, so the Tayal ERP can sync." -Force | Out-Null
Ok "task '$TaskName': at sign-in for $user, then every $EveryMinutes minutes if Tally is closed"

Write-Host ""
Write-Host "Two more things, done once by hand:" -ForegroundColor Cyan
Write-Host "  1. In Tally: F1 > Settings > Startup > Load companies on startup = Yes > Companies to load > Ishika Traders Daily."
Write-Host "  2. So this works after a restart with nobody at the PC, let Windows sign in by itself:"
Write-Host "     winget install Microsoft.Sysinternals.Autologon   then run  autologon  and enter this user's password."
