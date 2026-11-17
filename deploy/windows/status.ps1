<# Tayal ERP — is everything running?   powershell -File .\deploy\windows\status.ps1 #>
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Logs = Join-Path $Root "logs"
Write-Host ""
foreach ($n in @("postgresql-16", "TayalERP-API", "TayalERP-Web", "cloudflared")) {
    $s = Get-Service -Name $n -ErrorAction SilentlyContinue
    if (-not $s) { $s = Get-Service -Name "$n*" -ErrorAction SilentlyContinue | Select-Object -First 1 }
    $state = if ($s) { $s.Status.ToString() } else { "not installed" }
    $color = if ($state -eq "Running") { "Green" } else { "Red" }
    Write-Host ("  {0,-16} {1}" -f $n, $state) -ForegroundColor $color
}
foreach ($u in @("http://127.0.0.1:8000/health/live", "http://127.0.0.1:3000/login", "http://127.0.0.1:9000")) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 $u
        Write-Host ("  {0,-40} OK ({1})" -f $u, $r.StatusCode) -ForegroundColor Green
    } catch {
        $what = if ($u -like "*9000*") { "Tally not answering (is Tally open?)" } else { "not answering" }
        Write-Host ("  {0,-40} {1}" -f $u, $what) -ForegroundColor Red
    }
}
$b = Get-ChildItem (Join-Path $Root "backups") -Filter "tayal_erp_*.dump" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($b) { Write-Host ("  Last backup: {0}  ({1:N1} MB)" -f $b.LastWriteTime, ($b.Length / 1MB)) } else { Write-Host "  No backup yet" -ForegroundColor Yellow }
Write-Host ""
Write-Host "  Last lines of the server log:"
Get-Content (Join-Path $Logs "TayalERP-API.log") -Tail 5 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" }
