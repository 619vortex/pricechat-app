# Price Chat: one-click stop. Stops the backend and dashboard started by start-price-chat.ps1 (or the run-*.ps1
# scripts). Only Price Chat's own processes are touched: they are found by their command line, never by name alone.
#   -Quiet   don't wait before closing (for testing)
param([switch]$Quiet)
$root = Split-Path -Parent $PSScriptRoot
try { $Host.UI.RawUI.WindowTitle = "Stop Price Chat" } catch {}
$all = Get-CimInstance Win32_Process
$targets = @()
# the launchers (each one's tree holds uvicorn + its reloader, or npm + vite)
$targets += $all | Where-Object { $_.CommandLine -and $_.CommandLine -match "price-chat\\scripts\\run-(backend|frontend)\.ps1" }
# anything else of Price Chat's still serving the two ports (e.g. started by hand)
foreach ($port in 8000, 5173) {
    foreach ($c in Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        $p = $all | Where-Object { $_.ProcessId -eq $c.OwningProcess }
        if ($p -and $p.CommandLine -and $p.CommandLine.ToLower().Contains($root.ToLower())) { $targets += $p }
    }
}
Write-Host ""
if (-not $targets) {
    Write-Host "  Price Chat isn't running." -ForegroundColor DarkGray
} else {
    foreach ($t in ($targets | Sort-Object ProcessId -Unique)) {
        & taskkill.exe /PID $t.ProcessId /T /F 2>$null | Out-Null
    }
    Start-Sleep -Seconds 1
    $left = @(8000, 5173 | Where-Object { Get-NetTCPConnection -State Listen -LocalPort $_ -ErrorAction SilentlyContinue })
    if ($left) { Write-Host "  Still in use: port $($left -join ', ') (not a Price Chat process, left alone)." -ForegroundColor Yellow }
    else { Write-Host "  Price Chat stopped." -ForegroundColor Green }
}
if (-not $Quiet) { Start-Sleep -Seconds 2 }
