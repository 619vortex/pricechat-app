# Price Chat: one-click start. Starts the backend (API, port 8000) and the dashboard (port 5173) if they aren't
# running yet, waits until both actually answer, then opens the dashboard in your browser.
# Both run hidden in the background; their output goes to data\logs\. Stop them with stop-price-chat.ps1.
#   -Quiet   no browser and no "press Enter" (for testing)
param([switch]$Quiet)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force $logs | Out-Null
try { $Host.UI.RawUI.WindowTitle = "Price Chat" } catch {}

function Test-Up([string]$url) {
    try { return (Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 3).StatusCode -lt 500 } catch { return $false }
}
function Test-Listening([int]$port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
}
function Start-Part([string]$name, [string]$script, [int]$port) {
    if (Test-Listening $port) {
        Write-Host "  $name is already running (port $port)" -ForegroundColor DarkGray
        return
    }
    Write-Host "  Starting $name..." -NoNewline
    # cmd writes the log, so Start-Process passes no handles to the servers (they never hold this window open)
    $run = Join-Path $root "scripts\$script"
    $log = Join-Path $logs "$name.log"
    Start-Process cmd.exe -WindowStyle Hidden -ArgumentList "/s /c `"`"powershell.exe`" -NoProfile -ExecutionPolicy Bypass -File `"$run`" > `"$log`" 2>&1`""
    Write-Host " ok"
}
function Wait-Up([string]$name, [string]$url, [int]$seconds) {
    Write-Host "  Waiting for $name..." -NoNewline
    for ($i = 0; $i -lt $seconds; $i++) {
        if (Test-Up $url) { Write-Host " ready" -ForegroundColor Green; return $true }
        Start-Sleep -Seconds 1
        if ($i % 5 -eq 4) { Write-Host "." -NoNewline }
    }
    Write-Host " no answer after $seconds seconds" -ForegroundColor Red
    return $false
}

Write-Host ""
Write-Host "  PRICE CHAT" -ForegroundColor Green
Write-Host ""
Start-Part "backend" "run-backend.ps1" 8000
Start-Part "dashboard" "run-frontend.ps1" 5173
$api = Wait-Up "backend" "http://127.0.0.1:8000/api/health/live" 120
$web = Wait-Up "dashboard" "http://127.0.0.1:5173/" 90

if ($api -and $web) {
    Write-Host ""
    Write-Host "  Price Chat is running:  http://127.0.0.1:5173/admin" -ForegroundColor Green
    if (-not $Quiet) {
        Start-Process "http://127.0.0.1:5173/admin"
        Start-Sleep -Seconds 3
    }
    exit 0
}

Write-Host ""
Write-Host "  Something didn't start. Last lines of the logs (data\logs):" -ForegroundColor Yellow
foreach ($f in "backend.log", "dashboard.log") {
    $p = Join-Path $logs $f
    if (Test-Path $p) {
        Write-Host "  --- $f" -ForegroundColor DarkGray
        Get-Content $p -Tail 12 | ForEach-Object { Write-Host "  $_" }
    }
}
Write-Host ""
Write-Host "  Tip: double-click 'Stop Price Chat', then 'Price Chat' again." -ForegroundColor Yellow
if (-not $Quiet) { Read-Host "  Press Enter to close" | Out-Null }
exit 1
