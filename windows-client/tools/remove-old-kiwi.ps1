# Removes an old, UNSIGNED Kiwi VPN (0.x) from a PC with Smart App Control on, without running any of its files.
# Smart App Control blocks 0.1.1's own uninstall.exe and kiwivpn-service.exe, so this does their job with Windows' own tools
# (sc.exe, Stop-Process, Remove-Item, the registry). Smart App Control stays on; nothing here changes Windows security.
#
# Run in PowerShell AS ADMINISTRATOR:
#   powershell -ExecutionPolicy Bypass -File C:\AI\pc-wt\kiwi-win2\windows-client\tools\remove-old-kiwi.ps1
#
# Removes: the KiwiVPN service and its WireGuardTunnel$kiwi tunnel service, C:\Program Files\KiwiVPN, Kiwi's uninstall entry,
# its shortcuts and its start-with-Windows entry.
# Keeps: your Kiwi sign-in and settings (%LOCALAPPDATA%\live.pricechat.kiwivpn), any other WireGuard tunnel or the WireGuard
# app, and your device on pricechat.live (remove it under My Devices if you like).
# Refuses to touch a signed 2.x install: that one uninstalls normally from Settings > Apps.
$ErrorActionPreference = "Stop"
$Dir = "C:\Program Files\KiwiVPN"
$UninstKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\KiwiVPN"

$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run this in PowerShell as administrator (right-click PowerShell > Run as administrator)." }

$entry = Get-ItemProperty $UninstKey -ErrorAction SilentlyContinue
$ver = if ($entry) { $entry.DisplayVersion } else { "(no uninstall entry)" }
Write-Host "Installed Kiwi VPN: $ver"
$uninst = Join-Path $Dir "uninstall.exe"
if ((Test-Path $uninst) -and (Get-AuthenticodeSignature $uninst).Status -eq "Valid") {
  throw "This Kiwi VPN is signed: uninstall it normally from Settings > Apps. This script is only for the old unsigned 0.x."
}
if ($entry -and $ver -notmatch "^0\.") { throw "Expected an old 0.x install, found $ver. Not touching it." }

Write-Host "1. Closing the Kiwi VPN app"
Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($Dir, [StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Stop-Process -Id $_.Id -Force }

Write-Host "2. Stopping and removing Kiwi's services (only KiwiVPN and WireGuardTunnel`$kiwi; other tunnels are left alone)"
foreach ($svc in 'WireGuardTunnel$kiwi', 'KiwiVPN') {
  if (Get-Service $svc -ErrorAction SilentlyContinue) {
    & sc.exe stop $svc | Out-Null
    $t = 0
    while ((Get-Service $svc -ErrorAction SilentlyContinue).Status -ne "Stopped" -and $t -lt 30) { Start-Sleep 1; $t++ }
    & sc.exe delete $svc | Out-Null
    Write-Host "   removed service $svc"
  }
}
# the service process may take a moment to exit and release its files
$t = 0
while ((Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($Dir, [StringComparison]::OrdinalIgnoreCase) }) -and $t -lt 20) { Start-Sleep 1; $t++ }
Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($Dir, [StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Stop-Process -Id $_.Id -Force }

Write-Host "3. Deleting $Dir"
if (Test-Path $Dir) { Remove-Item $Dir -Recurse -Force }

Write-Host "4. Removing the uninstall entry, shortcuts and start-with-Windows entry"
foreach ($k in $UninstKey, "HKLM:\SOFTWARE\Price Chat\KiwiVPN") { if (Test-Path $k) { Remove-Item $k -Recurse -Force } }
foreach ($l in "C:\ProgramData\Microsoft\Windows\Start Menu\Programs\KiwiVPN.lnk", "C:\Users\Public\Desktop\KiwiVPN.lnk") { if (Test-Path $l) { Remove-Item $l -Force } }
Remove-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "KiwiVPN" -ErrorAction SilentlyContinue

Write-Host ""
$left = @()
if (Test-Path $Dir) { $left += $Dir }
if (Test-Path $UninstKey) { $left += "uninstall entry" }
if (Get-Service KiwiVPN -ErrorAction SilentlyContinue) { $left += "KiwiVPN service" }
if ($left) { Write-Host "NOT finished, still there: $($left -join ', '). Restart the PC and run this again." -ForegroundColor Yellow }
else { Write-Host "Old Kiwi VPN removed. Your sign-in and settings were kept. You can now install the signed 2.0." -ForegroundColor Green }
