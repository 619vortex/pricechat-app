# OWNER-RUN. Physical-phone test kit for Kiwi VPN's App-Based VPN ("Only apps I choose"). Needs a phone on USB with USB debugging on, and nothing else attached.
#   powershell -ExecutionPolicy Bypass -File android-client\tools\phone-test.ps1 -Apk C:\AI\pc-wt\kiwi-release\android\0.2.0\KiwiVPN-0.2.0-<commit>.apk
# It installs the APK (unless -SkipInstall), records the phone, then walks you through: sign in, choose Telegram only (or -Selected <package>), connect. At each step it gathers evidence by itself and
# writes a report to android-client\test-evidence\phone-<date>-<model>\REPORT.md. It never signs in, never talks to Price Chat, and never changes the phone's settings except airplane mode and
# Wi-Fi during the reconnect step, which it turns back on. Every adb call has a time limit.
param(
  [string]$Apk,
  [switch]$SkipInstall,
  [string]$Package = "live.pricechat.kiwivpn",
  [string]$Selected = "org.telegram.messenger",
  [string]$Unselected = "com.android.chrome",
  [string]$OutDir,
  # Only for checking the script itself on an emulator: no questions asked, and the steps that need a person are skipped.
  [switch]$NoPrompts
)
$ErrorActionPreference = "Stop"
$Adb = "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"
if (-not (Test-Path $Adb)) { $Adb = (Get-Command adb.exe -ErrorAction SilentlyContinue).Source }
if (-not $Adb) { throw "adb not found. Install Android platform-tools." }

function Invoke-Adb([string]$ArgLine, [int]$Seconds = 60) {
  $o = [IO.Path]::GetTempFileName(); $e = [IO.Path]::GetTempFileName()
  $p = Start-Process -FilePath $Adb -ArgumentList $ArgLine -NoNewWindow -PassThru -RedirectStandardOutput $o -RedirectStandardError $e
  if (-not $p.WaitForExit($Seconds * 1000)) { try { $p.Kill() } catch { }; return "TIMEOUT after $Seconds s: adb $ArgLine" }
  $text = (Get-Content $o -Raw) + (Get-Content $e -Raw)
  Remove-Item $o, $e -ErrorAction SilentlyContinue
  if ($null -eq $text) { "" } else { $text.TrimEnd() }
}
function Sh([string]$Command, [int]$Seconds = 60) { Invoke-Adb "shell $Command" $Seconds }
function Ask([string]$Text) {
  if ($NoPrompts) { Write-Host "[no prompts] $Text"; return }
  Write-Host ""; Write-Host $Text -ForegroundColor Yellow; [void](Read-Host "Press Enter when done")
}

$devices = (Invoke-Adb "devices" 15) -split "`n" | Where-Object { $_ -match "\tdevice$" }
if ($devices.Count -ne 1) { throw "Connect exactly one phone with USB debugging allowed. adb sees: $($devices.Count)" }
$Serial = ($devices[0] -split "`t")[0]
$Model = (Sh "getprop ro.product.model").Trim()
$Stamp = Get-Date -Format "yyyy-MM-dd-HHmm"
if (-not $OutDir) { $OutDir = Join-Path (Split-Path $PSScriptRoot -Parent) ("test-evidence\phone-$Stamp-" + ($Model -replace '[^A-Za-z0-9]', '')) }
New-Item -ItemType Directory -Force $OutDir | Out-Null
$Report = Join-Path $OutDir "REPORT.md"
$lines = New-Object System.Collections.Generic.List[string]
function Note([string]$Text) { $lines.Add($Text); Write-Host $Text }
function Save([string]$Name, [string]$Text) { Set-Content (Join-Path $OutDir $Name) $Text -Encoding utf8 }
function Flush { Set-Content $Report ($lines -join "`r`n") -Encoding utf8 }

# --- what Android reports about the VPN -------------------------------------------------------------------------------
function Get-VpnLine { (Sh "dumpsys connectivity" 90) -split "`n" | Where-Object { $_.TrimStart().StartsWith("NetworkAgentInfo") -and $_ -match "Transports: [A-Z_|]*VPN" } | Select-Object -First 1 }
function Get-UidRanges([string]$Line) {
  $m = [regex]::Match($Line, "Uids: <\{([^}]*)\}>")
  if (-not $m.Success) { return @() }
  $m.Groups[1].Value -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ } | ForEach-Object { $r = ($_ -split "/")[0] -split "-"; ,@([int]$r[0], [int]$r[-1]) }
}
function Get-Dns([string]$Line) { [regex]::Match($Line, "DnsAddresses: \[([^\]]*)\]").Groups[1].Value.Trim() }
$UidMap = @{}
function Load-Uids {
  $script:UidMap = @{}
  foreach ($l in ((Sh "cmd package list packages -U" 60) -split "`n")) {
    $m = [regex]::Match($l, "package:(\S+) uid:(\d+)")
    if ($m.Success) { $script:UidMap[$m.Groups[1].Value] = [int]$m.Groups[2].Value }
  }
}
function Test-Covered([object[]]$Ranges, [string]$Pkg) {
  if (-not $UidMap.ContainsKey($Pkg)) { return $null }
  $u = $UidMap[$Pkg]; foreach ($r in $Ranges) { if ($u -ge $r[0] -and $u -le $r[1]) { return $true } }; return $false
}
function Get-TunBytes {
  $rx = Sh "cat /sys/class/net/tun0/statistics/rx_bytes" 10; $tx = Sh "cat /sys/class/net/tun0/statistics/tx_bytes" 10
  if ($rx -match "^\d+$" -and $tx -match "^\d+$") { return [long]$rx + [long]$tx }; return $null
}
function Snapshot([string]$Label) {
  $line = Get-VpnLine
  Save "dumpsys-connectivity-$Label.txt" (Sh "dumpsys connectivity" 90)
  if (-not $line) { Note "- **$Label**: no VPN network is up."; return $null }
  $ranges = @(Get-UidRanges $line)
  $routed = @($UidMap.Keys | Where-Object { Test-Covered $ranges $_ } | Sort-Object)
  Save "vpn-routed-packages-$Label.txt" ($routed -join "`r`n")
  Note "- **$Label**: VPN network up. DNS on it: ``$(Get-Dns $line)``. UID ranges: ``$(($ranges | ForEach-Object { "$($_[0])-$($_[1])" }) -join ', ')``."
  Note "  - $($routed.Count) installed packages are routed through the VPN (list in vpn-routed-packages-$Label.txt)."
  foreach ($p in @($Package, $Selected, "com.whatsapp", "com.whatsapp.w4b", "org.thoughtcrime.securesms", $Unselected, "com.android.shell")) {
    $c = Test-Covered $ranges $p
    if ($null -ne $c) { Note "  - ``$p`` (uid $($UidMap[$p])): $(if ($c) { 'THROUGH THE VPN' } else { 'normal connection' })" }
  }
  $routes = [regex]::Match($line, 'Routes: \[([^\]]*)\]').Groups[1].Value.Trim()
  Note "  - Routes: ``$routes``"
  return $line
}
function Shell-Path([string]$Label) {
  # The shell (com.android.shell) is never picked in the app, so under Only apps I choose it must use the normal connection.
  $ping = Sh "ping -c 2 -W 3 1.1.1.1" 20
  $dns = Sh "ping -c 1 -W 3 pricechat.live" 30
  Save "shell-ping-$Label.txt" "$ping`r`n----`r`n$dns"
  $reachedText = if ($ping -match ' [12] received') { 'reached' } else { 'NO REPLY' }
  $resolvedText = if ($dns -match 'PING \S+ [(]\d') { 'resolved' } else { 'FAILED' }
  Note "  - Shell (unselected) ping 1.1.1.1: $reachedText; name lookup: $resolvedText"
}

# --- 1. phone and install ---------------------------------------------------------------------------------------------
Note "# Kiwi VPN phone test, $Stamp"
Note ""
Note "**Run by the owner on a physical phone.** Raw files are beside this report."
Note ""
Note "## Phone"
foreach ($k in "ro.product.manufacturer", "ro.product.model", "ro.build.version.release", "ro.build.version.sdk", "ro.build.version.security_patch", "ro.build.fingerprint") { Note "- $k = ``$((Sh "getprop $k").Trim())``" }
Note "- Private DNS: ``$((Sh 'settings get global private_dns_mode').Trim())`` ``$((Sh 'settings get global private_dns_specifier').Trim())``"
Note "- Always-on VPN app: ``$((Sh 'settings get secure always_on_vpn_app').Trim())``, lockdown: ``$((Sh 'settings get secure always_on_vpn_lockdown').Trim())``"
if (-not $SkipInstall) {
  if (-not $Apk -or -not (Test-Path $Apk)) { throw "Give -Apk <path to the release APK>, or -SkipInstall." }
  Note "- APK: ``$(Split-Path $Apk -Leaf)`` SHA-256 ``$((Get-FileHash $Apk).Hash)``"
  $inst = Invoke-Adb "install -r `"$Apk`"" 240
  Note "- Install: ``$($inst -replace "`r?`n", ' ')``"
  if ($inst -notmatch "Success") { Flush; throw "Install failed: $inst. If it says the signatures don't match, the installed copy was signed with another key: see docs/PHONE-TEST-2026-11.md." }
}
$pkgInfo = Sh "dumpsys package $Package" 60
Note "- Installed: versionName ``$([regex]::Match($pkgInfo, 'versionName=(\S+)').Groups[1].Value)`` versionCode ``$([regex]::Match($pkgInfo, 'versionCode=(\d+)').Groups[1].Value)``"
Load-Uids
foreach ($p in $Selected, $Unselected) { if (-not $UidMap.ContainsKey($p)) { Note "- **Note:** ``$p`` isn't installed. Install it, or pass -Selected / -Unselected." } }
Invoke-Adb "logcat -c" 15 | Out-Null
Flush

# --- 2. sign in, choose, connect ------------------------------------------------------------------------------------
Note ""; Note "## Only apps I choose"
Ask "On the phone: open Kiwi VPN, sign in. On 'Choose what uses Kiwi VPN' pick 'Only apps I choose', leave ONLY $Selected switched on (switch off the other suggestions), save. Then connect to any country and wait for Protected."
Load-Uids
$line = Snapshot "connected"
if ($line) {
  $r = @(Get-UidRanges $line)
  $ok = (Test-Covered $r $Selected) -and (Test-Covered $r $Package) -and -not (Test-Covered $r $Unselected)
  Note "  - **Result:** $(if ($ok) { 'PASS: only the selected app and Kiwi are on the VPN' } else { 'CHECK: the routed list is not what was chosen' })"
}
Shell-Path "connected"
Flush

# --- 3. traffic per app through tun0 ------------------------------------------------------------------------------
Note ""; Note "## Traffic through the tunnel"
$b0 = Get-TunBytes
Ask "Open $Unselected (not selected) and load two or three web pages, e.g. ipleak.net. Note the IP it shows: it should be YOUR OWN address."
$b1 = Get-TunBytes
Ask "Now open $Selected (selected) and send a message with a photo, or scroll a busy channel for 20 seconds."
$b2 = Get-TunBytes
if ($null -ne $b0 -and $null -ne $b1 -and $null -ne $b2) {
  Note "- Tunnel bytes while using the UNSELECTED app: $($b1 - $b0) (expect small: only Kiwi's own checks and keepalives)"
  Note "- Tunnel bytes while using the SELECTED app: $($b2 - $b1) (expect clearly larger)"
} else { Note "- tun0 counters weren't readable on this phone (some makers hide them). Use the ipleak.net check below." }
if (-not $NoPrompts) {
  $ipOwn = Read-Host "What IP did ipleak.net show in $Unselected? (Enter to skip)"
  $ipVpnApp = Read-Host "In Kiwi VPN, what exit address does Home or Diagnostics show? (Enter to skip)"
  Note "- ipleak.net in the unselected app showed: ``$ipOwn``; Kiwi's exit address: ``$ipVpnApp``. They should differ."
}
Save "netstats-after-traffic.txt" (Sh "dumpsys netstats detail" 90)
Flush

# --- 4. reconnects --------------------------------------------------------------------------------------------------
Note ""; Note "## Reconnects"
Ask "In Kiwi VPN press Disconnect, then Connect again straight away. Wait for Protected."
Snapshot "after-reconnect" | Out-Null
Note "- Airplane mode on for 30 s, then off (done by this script)."
Note "  - airplane on: ``$(Sh 'cmd connectivity airplane-mode enable' 20)``"
Start-Sleep 30
Note "  - airplane off: ``$(Sh 'cmd connectivity airplane-mode disable' 20)``"
$back = $null
for ($i = 0; $i -lt 24; $i++) { Start-Sleep 5; $back = Get-VpnLine; if ($back -and (Sh "ping -c 1 -W 3 1.1.1.1" 15) -match " 1 received") { break } }
Note "  - VPN network after airplane mode: $(if ($back) { "back after about $([int](($i + 1) * 5)) s" } else { 'NOT BACK within 2 minutes' })"
Snapshot "after-airplane" | Out-Null
Ask "Check Kiwi VPN: does it say Protected again (or Reconnecting, then Protected)? Note what it showed."
Flush

# --- 5. DNS ---------------------------------------------------------------------------------------------------------
Note ""; Note "## DNS"
Ask "In $Selected there's no DNS test to run, so: temporarily also select Chrome in Settings > App-Based VPN, save (it reconnects), open dnsleaktest.com in Chrome and run the standard test. Note the resolvers it lists (they should be the VPN server's only). Then unselect Chrome again and save."
Snapshot "dns-check" | Out-Null
Flush

# --- 6. Block connections without VPN ---------------------------------------------------------------------------
Note ""; Note "## Always-on and Block connections without VPN"
Ask "Phone Settings > Network > VPN > gear beside Kiwi VPN: turn on Always-on VPN and Block connections without VPN. Keep Only apps I choose with $Selected. Come back here."
Note "- always_on_vpn_app=``$((Sh 'settings get secure always_on_vpn_app').Trim())`` lockdown=``$((Sh 'settings get secure always_on_vpn_lockdown').Trim())``"
Snapshot "lockdown-connected" | Out-Null
Shell-Path "lockdown-connected"
Note "  - Under lockdown, Google says apps outside the VPN lose their connection; on the emulator the unselected shell got no reply."
Ask "Open $Unselected and try a page. Does it load? (Expected: no, it's cut off.) Then in Kiwi VPN press Disconnect, and try $Selected and ${Unselected}: both should have no internet."
Shell-Path "lockdown-disconnected"
Ask "Now turn Block connections without VPN and Always-on VPN OFF again in phone Settings, and reconnect Kiwi VPN if you want."
Note "- restored: always_on_vpn_app=``$((Sh 'settings get secure always_on_vpn_app').Trim())`` lockdown=``$((Sh 'settings get secure always_on_vpn_lockdown').Trim())``"
Flush

# --- 7. log hygiene ------------------------------------------------------------------------------------------------
Note ""; Note "## Log check"
$log = Invoke-Adb "logcat -d -v time" 90
Save "logcat-kiwi.txt" (($log -split "`n" | Where-Object { $_ -match "KiwiVPN|WireGuard|GoBackend|kiwivpn" }) -join "`n")
$leaks = @($log -split "`n" | Where-Object { $_ -match "kd\.[A-Za-z0-9_-]{10,}|PrivateKey|private_key|pc_session=" })
Note "- Lines that look like a session token or a private key in the device log: $($leaks.Count) $(if ($leaks.Count) { '**FAIL: see logcat-kiwi.txt**' } else { '(none: pass)' })"
Note ""
Note "Send this folder back. Nothing here contains your password; the routed-packages lists show which apps are installed, so share them only with the developer."
Flush
Write-Host ""; Write-Host "Report: $Report" -ForegroundColor Green
