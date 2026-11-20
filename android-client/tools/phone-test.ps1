# OWNER-RUN. Physical-phone test kit for Kiwi VPN 0.2.0 against production. Needs a phone on USB with USB debugging on, and nothing else attached.
#   powershell -ExecutionPolicy Bypass -File android-client\tools\phone-test.ps1 -Apk C:\AI\pc-wt\kiwi-release\android\0.2.0\KiwiVPN-0.2.0-4c3e829.apk
# Walks you through: store-review sign-in, Helsinki 1, the public IP, "Only apps I choose" with WhatsApp / Telegram / Signal, DNS and IPv6 leak
# checks, reconnects, Block connections without VPN, and deleting the (review) account. At each step it gathers evidence by itself (Android's own
# VPN routing table, tunnel byte counters, screenshots) and writes android-client\test-evidence\phone-<date>-<model>\REPORT.md.
# It never signs in, never types a password, never talks to Price Chat, and changes no phone setting except airplane mode during the reconnect step,
# which it turns back off. Every adb call has a time limit.
param(
  [string]$Apk,
  [switch]$SkipInstall,
  [string]$Package = "live.pricechat.kiwivpn",
  [string[]]$Selected = @("com.whatsapp", "org.telegram.messenger", "org.thoughtcrime.securesms"),
  [string]$Unselected = "com.android.chrome",
  [string]$ExpectedExit = "65.109.255.206",  # Helsinki 1's customer exit (floating IP, hel1.vpn.pricechat.live)
  [switch]$OwnAccount,                        # test with your own account instead of the store-review account (then skip deletion)
  [switch]$SkipLockdown,
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
function Answer([string]$Question) {
  if ($NoPrompts) { return "" }
  Write-Host ""; return (Read-Host $Question).Trim()
}

$devices = (Invoke-Adb "devices" 15) -split "`n" | Where-Object { $_ -match "\tdevice$" }
if ($devices.Count -ne 1) { throw "Connect exactly one phone with USB debugging allowed. adb sees: $($devices.Count)" }
$Model = (Sh "getprop ro.product.model").Trim()
$Stamp = Get-Date -Format "yyyy-MM-dd-HHmm"
if (-not $OutDir) { $OutDir = Join-Path (Split-Path $PSScriptRoot -Parent) ("test-evidence\phone-$Stamp-" + ($Model -replace '[^A-Za-z0-9]', '')) }
New-Item -ItemType Directory -Force $OutDir | Out-Null
$Report = Join-Path $OutDir "REPORT.md"
$lines = New-Object System.Collections.Generic.List[string]
function Note([string]$Text) { $lines.Add($Text); Write-Host $Text }
function Save([string]$Name, [string]$Text) { Set-Content (Join-Path $OutDir $Name) $Text -Encoding utf8 }
function Flush { Set-Content $Report ($lines -join "`r`n") -Encoding utf8 }
function Utc { (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss 'UTC'") }
function Shot([string]$Label) {
  # A screenshot of whatever the phone shows now (saved beside the report; it can show your notifications, so look before sharing).
  [void](Sh "screencap -p /sdcard/kiwi-test-shot.png" 30)
  [void](Invoke-Adb "pull /sdcard/kiwi-test-shot.png `"$(Join-Path $OutDir "screen-$Label.png")`"" 60)
  [void](Sh "rm /sdcard/kiwi-test-shot.png" 15)
  Note "  - screenshot: screen-$Label.png"
}

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
  foreach ($p in (@($Package) + $Selected + @($Unselected, "com.android.shell"))) {
    $c = Test-Covered $ranges $p
    if ($null -ne $c) { Note "  - ``$p`` (uid $($UidMap[$p])): $(if ($c) { 'THROUGH THE VPN' } else { 'normal connection' })" }
  }
  $routes = [regex]::Match($line, 'Routes: \[([^\]]*)\]').Groups[1].Value.Trim()
  Note "  - Routes: ``$routes``"
  $v6 = if ($routes -match '::/0') { 'yes: all IPv6 of the selected apps goes into the tunnel (none leaks around it)' } else { '**NO ::/0 route: IPv6 of selected apps could bypass the tunnel**' }
  Note "  - IPv6 default route in the VPN: $v6"
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
function Same-Ip([string]$A, [string]$B) { return ($A -and $B -and $A.Trim() -eq $B.Trim()) }

# --- 1. phone and install ---------------------------------------------------------------------------------------------
Note "# Kiwi VPN phone test, $Stamp"
Note ""
Note "**Run by the owner on a physical phone, against production (pricechat.live).** Raw files are beside this report. Started $(Utc)."
Note ""
Note "## Phone"
foreach ($k in "ro.product.manufacturer", "ro.product.model", "ro.build.version.release", "ro.build.version.sdk", "ro.build.version.security_patch", "ro.build.fingerprint") { Note "- $k = ``$((Sh "getprop $k").Trim())``" }
Note "- Private DNS: ``$((Sh 'settings get global private_dns_mode').Trim())`` ``$((Sh 'settings get global private_dns_specifier').Trim())``"
Note "- Always-on VPN app: ``$((Sh 'settings get secure always_on_vpn_app').Trim())``, lockdown: ``$((Sh 'settings get secure always_on_vpn_lockdown').Trim())``"
$v6addr = @((Sh "ip -6 addr show scope global" 20) -split "`n" | Where-Object { $_ -match "inet6 " } | ForEach-Object { ($_ -replace '^\s*inet6\s+(\S+).*$', '$1') })
Save "ipv6-addresses-before.txt" ($v6addr -join "`r`n")
Note "- The phone's own network has global IPv6: $(if ($v6addr.Count) { "**yes** ($($v6addr.Count) address(es)): the IPv6 leak test below is meaningful" } else { 'no: an IPv6 leak can''t happen on this network; repeat the leak step on mobile data or Wi-Fi with IPv6 if you can' })"
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
$Selected = @($Selected | Where-Object { if ($UidMap.ContainsKey($_)) { $true } else { Note "- **Note:** ``$_`` isn't installed: it's left out of this test."; $false } })
if (-not $Selected.Count) { Flush; throw "None of WhatsApp, Telegram or Signal is installed. Install at least one, or pass -Selected <package>." }
if (-not $UidMap.ContainsKey($Unselected)) { Note "- **Note:** ``$Unselected`` isn't installed. Pass -Unselected <a browser package>." }
$names = @{ "com.whatsapp" = "WhatsApp"; "org.telegram.messenger" = "Telegram"; "org.thoughtcrime.securesms" = "Signal" }
$pretty = ($Selected | ForEach-Object { if ($names.ContainsKey($_)) { $names[$_] } else { $_ } }) -join ", "
Invoke-Adb "logcat -c" 15 | Out-Null
Flush

# --- 2. sign in -----------------------------------------------------------------------------------------------------
Note ""; Note "## Sign in"
if ($OwnAccount) {
  Ask "On the phone: open Kiwi VPN, press Sign in, sign in with YOUR OWN Price Chat account in the browser tab, and press Allow. Stop at the 'Choose what uses Kiwi VPN' screen."
} else {
  Ask "On the phone: open Kiwi VPN and press Sign in. In the browser tab, tap 'App Store or Google Play reviewer? Use the review account', enter the review email and password, Sign in. You're back on the sign-in page: press Allow. Stop at the 'Choose what uses Kiwi VPN' screen."
}
Note "- Signed in at about $(Utc)."
Shot "01-after-sign-in"
Flush

# --- 3. choose apps, Helsinki, connect ---------------------------------------------------------------------------------
Note ""; Note "## Only apps I choose: $pretty"
Ask "Pick 'Only apps I choose'. Switch ON only: $pretty. Everything else OFF (Chrome must stay OFF). Save. Then open the server list: it should show only 'Helsinki 1' for the review account. Leave the list open on screen."
Shot "02-server-list"
$only = Answer "Does the server list show ONLY Helsinki 1? (y/n, or what it shows)"
Note "- Server list shows only Helsinki 1: ``$only``"
Ask "Choose Helsinki 1 and press Connect. Accept Android's VPN request if it asks. Wait until Home shows 'Public IP: ... (verified: the server's)' (may take ~10 s). Leave Home on screen."
Note "- Connected at about $(Utc)."
Shot "03-home-connected"
$ipApp = Answer "What does Kiwi's Home say after 'Public IP:'? Type the address, and whether it says 'verified: the server's'"
Note "- Kiwi Home public IP: ``$ipApp`` (expected $ExpectedExit, verified)."
Load-Uids
$line = Snapshot "connected"
if ($line) {
  $r = @(Get-UidRanges $line)
  $allIn = @($Selected | Where-Object { -not (Test-Covered $r $_) }).Count -eq 0
  $ok = $allIn -and (Test-Covered $r $Package) -and -not (Test-Covered $r $Unselected)
  Note "  - **Result:** $(if ($ok) { "PASS: exactly the chosen apps ($pretty) and Kiwi are on the VPN; $Unselected is not" } else { 'CHECK: the routed list is not what was chosen' })"
}
Shell-Path "connected"
Flush

# --- 4. per-app routing: traffic through the tunnel ------------------------------------------------------------------
Note ""; Note "## Selective routing: traffic per app"
$b0 = Get-TunBytes
Ask "Open Chrome (NOT selected) and load https://ipleak.net . Wait for it to finish. Leave it on screen."
$b1 = Get-TunBytes
Shot "04-chrome-unselected-ipleak"
$ipChrome = Answer "What IPv4 address does ipleak.net show at the top? (should be YOUR OWN, not $ExpectedExit)"
$v6Chrome = Answer "Does ipleak.net show an IPv6 address? Type it, or 'none'"
Note "- Chrome (unselected): bytes through the tunnel meanwhile $(if ($null -ne $b0 -and $null -ne $b1) { $b1 - $b0 } else { 'n/a' }) (expect small: Kiwi's own keepalives only). ipleak.net IPv4 ``$ipChrome``, IPv6 ``$v6Chrome``."
Note "  - $(if ($ipChrome -and -not (Same-Ip $ipChrome $ExpectedExit)) { 'PASS: the unselected browser uses your own connection' } elseif (Same-Ip $ipChrome $ExpectedExit) { '**FAIL: the unselected browser went through the VPN**' } else { 'not answered' })"
foreach ($p in $Selected) {
  $n = if ($names.ContainsKey($p)) { $names[$p] } else { $p }
  $x0 = Get-TunBytes
  Ask "Open $n (selected). Send yourself or a contact a message WITH A PHOTO, and wait until it shows as sent/delivered (two ticks / checkmark). Then come back here."
  $x1 = Get-TunBytes
  $sent = Answer "Did the $n message with a photo send and deliver? (y/n)"
  Note "- ${n}: bytes through the tunnel while using it: $(if ($null -ne $x0 -and $null -ne $x1) { $x1 - $x0 } else { 'n/a' }) (expect clearly larger than Chrome's). Message delivered: ``$sent``."
  Flush
}
if ($null -eq $b0) { Note "- tun0 counters weren't readable on this phone (some makers hide them). The routing table above and Android's per-app data usage are the evidence then." }
Save "netstats-after-traffic.txt" (Sh "dumpsys netstats detail" 90)
Shot "05-after-messengers"
Flush

# --- 5. DNS and IPv6 leaks (through a selected browser) ---------------------------------------------------------------
Note ""; Note "## DNS and IPv6 leak checks"
Ask "Messengers have no leak test, so Chrome stands in for a selected app: in Kiwi VPN go to Settings > App-Based VPN, ALSO switch on Chrome, save (it reconnects). Wait for Home to show the verified public IP again."
$line = Snapshot "chrome-selected"
Ask "In Chrome open https://ipleak.net and wait until the 'DNS Addresses' section finishes. Leave it on screen."
Shot "06-ipleak-chrome-selected"
$leakV4 = Answer "ipleak.net (Chrome now selected): IPv4 at the top? (expected $ExpectedExit)"
$leakV6 = Answer "ipleak.net: IPv6 shown? Type it, or 'none' (expected none, or a Helsinki address; NEVER your own ISP's)"
$leakDns = Answer "ipleak.net 'DNS Addresses': which addresses / countries / ISPs? (expected only Finland / Hetzner, nothing from your ISP)"
Note "- ipleak.net with Chrome selected: IPv4 ``$leakV4``, IPv6 ``$leakV6``, DNS ``$leakDns``."
Note "  - IPv4: $(if (Same-Ip $leakV4 $ExpectedExit) { 'PASS (Helsinki exit)' } elseif ($leakV4) { '**CHECK: not the Helsinki exit**' } else { 'not answered' })"
Ask "Still in Chrome: open https://test-ipv6.com and wait for the summary. Leave it on screen."
Shot "07-test-ipv6"
$t6 = Answer "test-ipv6.com: what does the top summary say (e.g. 'You appear to be able to browse the IPv4 Internet only', or an IPv6 address)? Which ISP does it name?"
Note "- test-ipv6.com: ``$t6`` (pass = IPv4 only, or an IPv6 that belongs to Hetzner; fail = your own ISP's IPv6)."
Ask "Still in Chrome: open https://www.dnsleaktest.com , press 'Extended test', wait for the results table. Leave it on screen."
Shot "08-dnsleaktest-extended"
$dl = Answer "dnsleaktest.com extended: which ISPs / countries are listed? (expected: only Hetzner, Finland)"
Note "- dnsleaktest.com extended: ``$dl``."
Ask "Now switch Chrome OFF again in Settings > App-Based VPN and save (it reconnects with only $pretty)."
Snapshot "chrome-unselected-again" | Out-Null
Flush

# --- 6. reconnects --------------------------------------------------------------------------------------------------
Note ""; Note "## Reconnects"
Ask "In Kiwi VPN press Disconnect, then Connect again straight away. Wait for the verified public IP."
Snapshot "after-reconnect" | Out-Null
Note "- Airplane mode on for 30 s, then off (done by this script)."
Note "  - airplane on: ``$(Sh 'cmd connectivity airplane-mode enable' 20)``"
Start-Sleep 30
Note "  - airplane off: ``$(Sh 'cmd connectivity airplane-mode disable' 20)``"
$back = $null
for ($i = 0; $i -lt 24; $i++) { Start-Sleep 5; $back = Get-VpnLine; if ($back -and (Sh "ping -c 1 -W 3 1.1.1.1" 15) -match " 1 received") { break } }
Note "  - VPN network after airplane mode: $(if ($back) { "back after about $([int](($i + 1) * 5)) s" } else { 'NOT BACK within 2 minutes' })"
Snapshot "after-airplane" | Out-Null
Shot "09-after-airplane"
$re = Answer "What did Kiwi show after airplane mode (Reconnecting, then verified IP? anything else)?"
Note "- Kiwi after airplane mode: ``$re``."
Flush

# --- 7. Block connections without VPN ---------------------------------------------------------------------------
if (-not $SkipLockdown) {
  Note ""; Note "## Always-on and Block connections without VPN"
  Ask "Phone Settings > Network > VPN > gear beside Kiwi VPN: turn on Always-on VPN and Block connections without VPN. Keep Only apps I choose ($pretty). Come back here."
  Note "- always_on_vpn_app=``$((Sh 'settings get secure always_on_vpn_app').Trim())`` lockdown=``$((Sh 'settings get secure always_on_vpn_lockdown').Trim())``"
  Snapshot "lockdown-connected" | Out-Null
  Shell-Path "lockdown-connected"
  $lk = Answer "Open Chrome and load any page. Does it load? (expected: NO, unselected apps are cut off under Block connections without VPN)"
  Note "- Chrome under lockdown loads pages: ``$lk``."
  Ask "Now turn Block connections without VPN and Always-on VPN OFF again in phone Settings. Make sure Kiwi still shows connected (reconnect if not)."
  Note "- restored: always_on_vpn_app=``$((Sh 'settings get secure always_on_vpn_app').Trim())`` lockdown=``$((Sh 'settings get secure always_on_vpn_lockdown').Trim())``"
  Flush
}

# --- 8. account deletion ------------------------------------------------------------------------------------------
if (-not $OwnAccount) {
  Note ""; Note "## Account deletion (the review account)"
  Ask "In Kiwi VPN: Settings > Account > Delete account. Read the screen (what's deleted / kept) and leave it on screen without typing anything."
  Shot "10-delete-screen"
  Ask "Type the username WRONG (e.g. add an x) and check the Delete button stays disabled or is refused. Then type it exactly as shown and press Delete."
  $delAt = Utc
  Note "- Delete pressed at about $delAt."
  Start-Sleep 8
  Shot "11-after-delete"
  $after = Answer "What does the app show now? (expected: the sign-in screen, VPN disconnected)"
  Note "- After deletion the app shows: ``$after``."
  $vpnAfter = Get-VpnLine
  Note "- VPN network after deletion: $(if ($vpnAfter) { '**still up: CHECK**' } else { 'none (pass)' })"
  Save "dumpsys-connectivity-after-delete.txt" (Sh "dumpsys connectivity" 90)
  Ask "Optional, proves a fresh account: Sign in again with the review account (same steps). It should work and show no devices until you Connect. Then press Sign out (Settings > Account) to finish."
  Flush
} else {
  Note ""; Note "## Account deletion: skipped (your own account). Never delete your real account for a test."
}

# --- 9. log hygiene ------------------------------------------------------------------------------------------------
Note ""; Note "## Log check"
$log = Invoke-Adb "logcat -d -v time" 90
Save "logcat-kiwi.txt" (($log -split "`n" | Where-Object { $_ -match "KiwiVPN|WireGuard|GoBackend|kiwivpn" }) -join "`n")
$leaks = @($log -split "`n" | Where-Object { $_ -match "k[dr]\.[A-Za-z0-9_-]{10,}|PrivateKey|private_key|pc_session=" })
Note "- Lines that look like a session token or a private key in the device log: $($leaks.Count) $(if ($leaks.Count) { '**FAIL: see logcat-kiwi.txt**' } else { '(none: pass)' })"
Note ""
Note "Finished $(Utc). Send this whole folder back. Nothing here contains a password. The screenshots can show your notifications and the routed-packages lists show which apps are installed: look before sharing, and share only with the developer."
Flush
Write-Host ""; Write-Host "Report: $Report" -ForegroundColor Green
