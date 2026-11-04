# The real test: run INSIDE THE VM, as administrator, with a Kiwi VPN config file. Installs the service, connects, proves it, disconnects, proves that too, and removes everything.
#   powershell -ExecutionPolicy Bypass -File connect-test.ps1 -Config C:\path\germany.conf
# Never run this on the PC that runs client1. It stops itself there.
param([Parameter(Mandatory)][string]$Config, [int]$TimeoutSeconds = 45)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$dir = "C:\Program Files\KiwiVPN"
$exe = "$dir\kiwivpn-service.exe"
$engineDir = "C:\Program Files\WireGuard"
$fails = 0
function Check($name, $ok, $detail = "") { if ($ok) { Write-Host "PASS  $name  $detail" } else { Write-Host "FAIL  $name  $detail" -ForegroundColor Red; $script:fails++ } }
function PublicIp { try { (curl.exe -4 -s -m 12 https://api.ipify.org) } catch { "" } }
function Ipv6Ip { try { (curl.exe -6 -s -m 8 https://api64.ipify.org) } catch { "" } }
# tunnel.dll's own log, the only record of why a tunnel failed. Readable text only: it holds no private keys.
function TunnelLog { $log = "$engineDir\Data\log.bin"; if (Test-Path $log) { Write-Host "--- tunnel log"; [regex]::Matches([IO.File]::ReadAllText($log, [Text.Encoding]::ASCII), '[\x20-\x7e]{12,}') | Select-Object -Last 40 | ForEach-Object { Write-Host $_.Value }; Write-Host "---" } }

if (Get-Service 'WireGuardTunnel$*' -ErrorAction SilentlyContinue | Where-Object Name -ne 'WireGuardTunnel$kiwi') { throw "Another WireGuard tunnel is on this machine. This test belongs in the VM. Nothing was changed." }
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run this as administrator." }
if (-not (Test-Path $Config)) { throw "No config at $Config" }

# the pipe's own test, which Smart App Control won't let run on the build PC. Needs the pipe free, so it goes before the install.
& "$here\dist\pipe-test.exe" --test-threads=1 | Select-String "^test " | ForEach-Object { Write-Host $_ }
Check "the pipe answers and refuses what it should" ($LASTEXITCODE -eq 0)

$before = PublicIp
$routesBefore = (Get-NetRoute -AddressFamily IPv4 -DestinationPrefix 0.0.0.0/0 | Measure-Object).Count
$dnsBefore = (Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object ServerAddresses | ForEach-Object { $_.ServerAddresses }) -join ","
$endpointHost = (Select-String -Path $Config -Pattern '^\s*Endpoint\s*=\s*(.+):\d+\s*$').Matches.Groups[1].Value
# may be a host name, and has to be looked up before the tunnel owns DNS
$endpoint = try { ([Net.Dns]::GetHostAddresses($endpointHost) | Where-Object AddressFamily -eq InterNetwork | Select-Object -First 1).IPAddressToString } catch { $endpointHost }
Write-Host "Before: public IP $before, DNS $dnsBefore, server $endpointHost = $endpoint"
if ($before -eq $endpoint) { Write-Host "NOTE  This VM already exits from that server through the host, so the IP check proves nothing here. Use Germany." -ForegroundColor Yellow }
$engineWasThere = Test-Path $engineDir

try {
  New-Item -ItemType Directory -Force $dir | Out-Null
  Copy-Item "$here\dist\*" $dir -Exclude pipe-test.exe -Force
  & $exe install
  $until = (Get-Date).AddSeconds(10)
  while ((Get-Service KiwiVPN).Status -ne "Running" -and (Get-Date) -lt $until) { Start-Sleep -Milliseconds 300 }
  Check "the service installs and runs" ((Get-Service KiwiVPN).Status -eq "Running")

  $out = & $exe connect $Config | Out-String
  Write-Host $out
  $connected = $false
  $until = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $until) {
    $s = & $exe status | Out-String
    if ($s -match "state: Connected") { $connected = $true; break }
    if ($s -match "state: (Failed|Disconnected)") { Write-Host $s; break }
    Start-Sleep 2
  }
  Write-Host (& $exe status | Out-String)
  Check "it says Connected only after a real handshake" $connected
  if (-not $connected) { TunnelLog; throw "No connection, so the remaining checks would mean nothing." }

  $tunnelIp = PublicIp
  Check "web traffic works through the tunnel" ([bool]$tunnelIp) $tunnelIp
  if (-not $tunnelIp) { Write-Host "A handshake with no web traffic, on this VM, usually means packets too big for the host's client1 tunnel. Not necessarily a client bug." -ForegroundColor Yellow }
  Check "the public IP is the server's" ($tunnelIp -eq $endpoint) "$tunnelIp vs server $endpoint (before: $before)"
  $status = & $exe status | Out-String
  Check "real data is crossing the tunnel" ($status -match "received: [1-9]")
  $adapter = Get-NetAdapter -Name kiwi -ErrorAction SilentlyContinue
  Check "the tunnel adapter exists" ($null -ne $adapter -and $adapter.Status -eq "Up")
  $dns = (Get-DnsClientServerAddress -InterfaceAlias kiwi -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses -join ","
  Check "the tunnel has its own DNS set (this is not a leak test)" ([bool]$dns) $dns
  $v6 = Ipv6Ip
  Write-Host "IPv6 address the outside sees, if any: '$v6' (should be empty or the server's, never this network's)"
  Write-Host "For the DNS leak test, open https://dnsleaktest.com in a browser now, run the standard test, and note the servers it shows. Press Enter when done."
  [void](Read-Host)

  & $exe disconnect | Out-Null
  Start-Sleep 3
  $s = & $exe status | Out-String
  Check "disconnect reports disconnected" ($s -match "state: Disconnected") $s.Trim()
  Check "the tunnel adapter is gone" ($null -eq (Get-NetAdapter -Name kiwi -ErrorAction SilentlyContinue))
  $after = PublicIp
  Check "the normal connection works and the IP is back" ($after -eq $before) "$after vs $before"
  Check "default routes are back as they were" ((Get-NetRoute -AddressFamily IPv4 -DestinationPrefix 0.0.0.0/0 | Measure-Object).Count -eq $routesBefore)
  Check "the config with its private key is gone from the service" (-not (Test-Path "$dir\tunnels\kiwi.conf"))
}
finally {
  if (Test-Path $exe) { & $exe uninstall }
  Start-Sleep 2
  Check "uninstall removes the services" ($null -eq (Get-Service KiwiVPN -ErrorAction SilentlyContinue) -and $null -eq (Get-Service 'WireGuardTunnel$kiwi' -ErrorAction SilentlyContinue))
  Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
  # tunnel.dll keeps its log here whatever we do. Only removed if this test is what created it.
  if ((Test-Path $engineDir) -and -not $engineWasThere) { Write-Host "NOTE  tunnel.dll created $engineDir for its log. The real uninstaller must clean this up too."; Remove-Item -Recurse -Force $engineDir -ErrorAction SilentlyContinue }
}
if ($fails) { Write-Host "$fails FAILED" -ForegroundColor Red; exit 1 }
Write-Host "ALL PASSED" -ForegroundColor Green
