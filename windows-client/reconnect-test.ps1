# The connection lifecycle test: run INSIDE THE VM, as administrator, with the new build installed. Real service, real tunnel, real adapter.
#   powershell -ExecutionPolicy Bypass -File reconnect-test.ps1 -Germany C:\path\germany.conf -Finland C:\path\finland.conf
# The configs come from Manual Setup at pricechat.live/vpn/devices, so the app's own devices aren't touched. Remove those two devices there afterwards. Never run this on the PC that runs client1: it stops itself.
param([Parameter(Mandatory)][string]$Germany, [Parameter(Mandatory)][string]$Finland, [int]$Rounds = 10, [int]$TimeoutSeconds = 45)
$ErrorActionPreference = "Stop"
$dir = "C:\Program Files\KiwiVPN"
$exe = "$dir\kiwivpn-service.exe"
$conf = "$dir\tunnels\kiwi.conf"
$tunnel = 'WireGuardTunnel$kiwi'
$fails = 0
function Check($name, $ok, $detail = "") { if ($ok) { Write-Host "PASS  $name  $detail" } else { Write-Host "FAIL  $name  $detail" -ForegroundColor Red; $script:fails++ } }
function State { (& $exe status | Out-String) }
function PublicIp { curl.exe -4 -s -m 12 https://api.ipify.org }
function NoAdapter { $null -eq (Get-NetAdapter -Name kiwi -ErrorAction SilentlyContinue) }
function DefaultRoutes { (Get-NetRoute -AddressFamily IPv4 -DestinationPrefix 0.0.0.0/0 -ErrorAction SilentlyContinue | Measure-Object).Count }
function ServerOf($file) { $h = (Select-String -Path $file -Pattern '^\s*Endpoint\s*=\s*(.+):\d+\s*$').Matches.Groups[1].Value; try { ([Net.Dns]::GetHostAddresses($h) | Where-Object AddressFamily -eq InterNetwork | Select-Object -First 1).IPAddressToString } catch { $h } }
function TunnelLog { $log = "C:\Program Files\WireGuard\Data\log.bin"; if (Test-Path $log) { Write-Host "--- tunnel log"; [regex]::Matches([IO.File]::ReadAllText($log, [Text.Encoding]::ASCII), '[\x20-\x7e]{12,}') | Select-Object -Last 40 | ForEach-Object { Write-Host $_.Value }; Write-Host "---" } }

if (Get-Service 'WireGuardTunnel$*' -ErrorAction SilentlyContinue | Where-Object Name -ne $tunnel) { throw "Another WireGuard tunnel is on this machine. This test belongs in the VM. Nothing was changed." }
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run this as administrator." }
foreach ($c in $Germany, $Finland) { if (-not (Test-Path $c)) { throw "No config at $c" } }
if (-not (Test-Path $exe)) { throw "Kiwi VPN isn't installed. Install the new build from File Explorer first." }
if (Get-Service WireGuardManager -ErrorAction SilentlyContinue) { Write-Host "NOTE  The separate WireGuard app is installed here (WireGuardManager). Customers won't have it, so repeat this once on a VM without it." -ForegroundColor Yellow }

function Connect-Once($label, $file) {
  $started = Get-Date
  $out = & $exe connect $file | Out-String
  $connected = $false
  while (((Get-Date) - $started).TotalSeconds -lt $TimeoutSeconds) {
    $s = State
    if ($s -match "state: Connected") { $connected = $true; break }
    if ($s -match "state: Failed") { Write-Host $s; break }
    Start-Sleep -Milliseconds 500
  }
  Check "$label reaches Connected with a real handshake" $connected ("{0:n1}s" -f ((Get-Date) - $started).TotalSeconds)
  if (-not $connected) { Write-Host $out; TunnelLog; return $false }
  Check "$label has its adapter, only one" ((Get-NetAdapter -Name kiwi* -ErrorAction SilentlyContinue | Measure-Object).Count -eq 1)
  $ip = PublicIp
  Check "$label exits from that server" ($ip -eq (ServerOf $file)) "$ip vs $(ServerOf $file)"
  return $true
}

function Disconnect-Once($label) {
  $started = Get-Date
  $s = & $exe disconnect | Out-String
  Check "$label reports Disconnected when Disconnect returns" ($s -match "state: Disconnected") ("{0:n1}s {1}" -f ((Get-Date) - $started).TotalSeconds, ($s -split "`n")[0].Trim())
  Check "$label removed the adapter" (NoAdapter)
  Check "$label removed the config holding the private key" (-not (Test-Path $conf))
  Check "$label left the tunnel stopped" ((Get-Service $tunnel -ErrorAction SilentlyContinue).Status -in @($null, "Stopped"))
  Check "$label restored the default routes" ((DefaultRoutes) -eq $routesBefore)
}

$before = PublicIp
$routesBefore = DefaultRoutes
Write-Host "Before: public IP $before, default routes $routesBefore"
if ($before -in @((ServerOf $Germany), (ServerOf $Finland))) { Write-Host "NOTE  This VM already exits from one of those servers, so that IP check proves nothing here." -ForegroundColor Yellow }
$since = Get-Date
$bad = $null
try {
  for ($round = 1; $round -le $Rounds; $round++) {
    if (-not (Connect-Once "cycle $round of $Rounds" $Germany)) { throw "Stopped at cycle $round." }
    Disconnect-Once "cycle $round of $Rounds"
    Check "cycle $round the normal address is back" ((PublicIp) -eq $before)
  }

  # Germany to Finland to Germany, with no Disconnect between: the old tunnel has to go first
  [void](Connect-Once "switch, Germany" $Germany)
  [void](Connect-Once "switch, Finland" $Finland)
  [void](Connect-Once "switch, Germany again" $Germany)
  Disconnect-Once "after switching"

  # Connecting straight after Disconnect, with no pause
  [void](Connect-Once "no pause, first" $Germany)
  & $exe disconnect | Out-Null
  [void](Connect-Once "no pause, second" $Germany)

  # What the connection looks like from outside. Reported, not judged: this is not a passed leak test.
  $dns = (Get-DnsClientServerAddress -InterfaceAlias kiwi -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses -join ","
  Check "the tunnel has its own DNS set (this is not a leak test)" ([bool]$dns) $dns
  $v6 = curl.exe -6 -s -m 8 https://api64.ipify.org
  Write-Host "IPv6 address the outside sees, if any: '$v6' (should be empty or the server's, never this network's)"

  # The Kiwi VPN service restarting while connected: the tunnel is its own process, and the service has to read its state back
  Restart-Service KiwiVPN
  Start-Sleep 3
  Check "after a service restart it still reads Connected" ((State) -match "state: Connected") ((State) -split "`n")[0]
  Disconnect-Once "after the service restart"
  Restart-Service KiwiVPN
  Start-Sleep 3
  Check "after a service restart while off it reads Disconnected" ((State) -match "state: Disconnected")
  [void](Connect-Once "after the service restart" $Germany)
  Disconnect-Once "after that"

  # Reconnecting after a failed connection: an address nothing answers on (reserved for documentation), then the real one
  $bad = Join-Path $env:TEMP "kiwi-no-answer.conf"
  (Get-Content $Germany) -replace '^(\s*Endpoint\s*=\s*).*$', '${1}192.0.2.1:51820' | Set-Content $bad -Encoding ascii
  & $exe connect $bad | Out-Null
  $started = Get-Date
  while (((Get-Date) - $started).TotalSeconds -lt 75 -and (State) -notmatch "state: Failed") { Start-Sleep 2 }
  $s = State
  Check "a server that never answers fails by itself, with a reason" ($s -match "state: Failed" -and $s -match "didn't answer") ("{0:n0}s" -f ((Get-Date) - $started).TotalSeconds)
  Check "that failure removed the adapter and the key" ((NoAdapter) -and -not (Test-Path $conf))
  [void](Connect-Once "after a failed connection" $Germany)
  Disconnect-Once "after that"

  # Something else holding the tunnel's service open, as the Services window or a PowerShell can
  [void](Connect-Once "before the held handle" $Germany)
  Add-Type -AssemblyName System.ServiceProcess
  $held = New-Object System.ServiceProcess.ServiceController $tunnel
  $null = $held.ServiceHandle
  try {
    & $exe disconnect | Out-Null
    [void](Connect-Once "with a handle held open" $Germany)
  } finally { $held.Dispose() }
  Disconnect-Once "after the held handle"
}
catch { Write-Host $_ -ForegroundColor Red; $fails++ }
finally {
  & $exe disconnect | Out-Null
  if ($bad) { Remove-Item $bad -Force -ErrorAction SilentlyContinue }  # holds a private key
  Write-Host "--- Windows' record of service failures during this test"
  Get-WinEvent -FilterHashtable @{ LogName = "System"; ProviderName = "Service Control Manager"; Id = 7023, 7024, 7031, 7034; StartTime = $since } -ErrorAction SilentlyContinue | Where-Object Message -match "Kiwi" | ForEach-Object { Write-Host $_.Message }
  Write-Host "--- the service's log"
  Get-Content "$dir\logs\service.log" -Tail 25 -ErrorAction SilentlyContinue
}
if ($fails) { TunnelLog; Write-Host "$fails FAILED" -ForegroundColor Red; exit 1 }
Write-Host "ALL PASSED" -ForegroundColor Green
