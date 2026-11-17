# Starts TallyPrime if it is not already running. Run by the "Tayal ERP - Start Tally" scheduled task
# (at sign-in, then every 10 minutes, so Tally comes back if someone closes it).
# Which company opens is set inside Tally: F1 > Settings > Startup > Load companies on startup.
param([Parameter(Mandatory = $true)][string]$TallyExe)

if (Get-Process -Name "tally" -ErrorAction SilentlyContinue) { exit 0 }
if (-not (Test-Path $TallyExe)) { exit 1 }
Start-Process -FilePath $TallyExe -WorkingDirectory (Split-Path $TallyExe) -WindowStyle Maximized
