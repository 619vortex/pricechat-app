# OWNER-RUN. Captures Google Play phone screenshots of Kiwi VPN from the one device on USB (a phone, or the kiwi36 emulator), with Android's
# demo-mode status bar (12:00, full battery and signal, no notifications), so no personal notifications reach the store.
#   powershell -ExecutionPolicy Bypass -File android-client\tools\store-screenshots.ps1
# You navigate; it captures each screen into android-client\store-assets\screenshots\ and turns demo mode off at the end.
# Use the store-review account (only Helsinki 1), and a phone with real WhatsApp, Telegram and Signal installed: the onboarding
# screen shows their real names and icons. Nothing is uploaded. Look at each picture before using it.
param([string]$OutDir)
$ErrorActionPreference = "Stop"
$Adb = "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"
if (-not (Test-Path $Adb)) { $Adb = (Get-Command adb.exe).Source }
if (-not $OutDir) { $OutDir = Join-Path (Split-Path $PSScriptRoot -Parent) "store-assets\screenshots" }
New-Item -ItemType Directory -Force $OutDir | Out-Null
$devices = @((& $Adb devices) -split "`n" | Where-Object { $_ -match "\tdevice\s*$" })
if ($devices.Count -ne 1) { throw "Connect exactly one device (adb sees $($devices.Count))." }

function Demo([string]$Command, [string]$Extras = "") { & $Adb shell "am broadcast -a com.android.systemui.demo -e command $Command $Extras" | Out-Null }
& $Adb shell settings put global sysui_demo_allowed 1 | Out-Null
Demo "enter"
Demo "clock" "-e hhmm 1200"
Demo "battery" "-e level 100 -e plugged false"
Demo "network" "-e wifi show -e level 4"
Demo "network" "-e mobile hide"
Demo "notifications" "-e visible false"

$shots = [ordered]@{
  "1-sign-in"        = "Kiwi VPN's first screen (signed out): the kiwi, 'Sign in'."
  "2-choose-apps"    = "After signing in: 'Choose what uses Kiwi VPN' with 'Only apps I choose' picked and WhatsApp, Telegram, Signal switched on."
  "3-home-connected" = "Home, connected to Helsinki 1, showing the verified public IP."
  "4-app-based-vpn"  = "Settings > App-Based VPN, showing the chosen apps."
  "5-locations"      = "The server list (Helsinki 1)."
  "6-settings"       = "Settings (scrolled to show Privacy policy, Open-source licences, Delete account)."
  "7-delete-account" = "Settings > Account > Delete account, before typing anything."
}
try {
  foreach ($k in $shots.Keys) {
    Write-Host ""; Write-Host "Show: $($shots[$k])" -ForegroundColor Yellow
    $a = Read-Host "Press Enter to capture, or type s to skip"
    if ($a -eq "s") { continue }
    & $Adb shell screencap -p /sdcard/kiwi-store.png | Out-Null
    & $Adb pull /sdcard/kiwi-store.png (Join-Path $OutDir "$k.png") | Out-Null
    & $Adb shell rm /sdcard/kiwi-store.png | Out-Null
    Write-Host "  saved $k.png"
  }
} finally {
  Demo "exit"
  & $Adb shell settings put global sysui_demo_allowed 0 | Out-Null
}
Write-Host ""; Write-Host "Screenshots in $OutDir. Play needs 2 to 8 phone screenshots (PNG or JPEG, 320-3840 px, 9:16 here)." -ForegroundColor Green
