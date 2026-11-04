# The real test: run INSIDE THE VM, as administrator, with a Kiwi VPN config file. Installs the service, connects, proves it, disconnects, proves that too, and removes everything.
#   powershell -ExecutionPolicy Bypass -File connect-test.ps1 -Config C:\path\germany.conf
# Never run this on the PC that runs client1. It stops itself there.
param([Parameter(Mandatory)][string]$Config, [int]$TimeoutSeconds = 45)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$dir = "C:\Program Files\KiwiVPN"
$exe = "$dir\kiwivpn-service.exe"
$fails = 0
function Check($name, $ok, $detail = "") { if ($ok) { Write-Host "PASS  $name  $detail" } else { Write-Host "FAIL  $name  $detail" -ForegroundColor Red; $script:fails++ } }
function PublicIp { try { (curl.exe -4 -s -m 12 https://api.ipify.org) } catch { "" } }
function Ipv6Ip { try { (curl.exe -6 -s -m 8 https://api64.ipify.org) } catch { "" } }

if (Get-Service 'WireGuardTunnel$*' -ErrorAction SilentlyContinue | Where-Object Name -ne 'WireGuardTunnel$kiwi') { throw "Another WireGuard tunnel is on this machine. This test belongs in the VM. Nothing was changed." }
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run this as administrator." }
if (-not (Test-Path $Config)) { throw "No config at $Config" }

$before = PublicIp
$routesBefore = (Get-NetRoute -AddressFamily IPv4 -DestinationPrefix 0.0.0.0/0 | Measure-Object).Count
$dnsBefore = (Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object ServerAddresses | ForEach-Object { $_.ServerAddresses }) -join ","
Write-Host "Before: public IP $before, DNS $dnsBefore"

try {
  New-Item -ItemType Directory -Force $dir | Out-Null
  Copy-Item "$here\dist\*" $dir -Force
  & $exe install
  Check "the service installs and runs" ((Get-Service KiwiVPN).Status -eq "Running")

  $out = & $exe connect $Config | Out-String
  Write-Host $out
  $connected = $false
  $until = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $until) {
    $s = & $exe status | Out-String
    if ($s -match "state: Connected") { $connected = $true; break }
    if ($s -match "state: Failed") { Write-Host $s; break }
    Start-Sleep 2
  }
  Write-Host (& $exe status | Out-String)
  Check "it says Connected only after a real handshake" $connected
  $tunnelIp = PublicIp
  Check "the public IP is reachable through the tunnel" ([bool]$tunnelIp) $tunnelIp
  $endpoint = (Select-String -Path $Config -Pattern '^\s*Endpoint\s*=\s*([^:]+):' ).Matches.Groups[1].Value
  Check "the public IP is the server's" ($tunnelIp -eq $endpoint) "$tunnelIp vs server $endpoint (before: $before)"
  if ($before -eq $endpoint) { Write-Host "NOTE  This VM already exits from that server through the host, so the IP check proves nothing here. Use Germany." -ForegroundColor Yellow }
  $status = & $exe status | Out-String
  Check "real data is crossing the tunnel" ($status -match "received: [1-9]")
  $adapter = Get-NetAdapter -Name kiwi -ErrorAction SilentlyContinue
  Check "the tunnel adapter exists" ($null -ne $adapter -and $adapter.Status -eq "Up")
  $dns = (Get-DnsClientServerAddress -InterfaceAlias kiwi -AddressFamily IPv4).ServerAddresses -join ","
  Write-Host "Tunnel DNS: $dns"
  Check "DNS goes through the tunnel's server" ([bool]$dns) $dns
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
}
if ($fails) { Write-Host "$fails FAILED" -ForegroundColor Red; exit 1 }
Write-Host "ALL PASSED" -ForegroundColor Green
