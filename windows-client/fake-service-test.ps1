# Proves a program posing as the Kiwi VPN service can't catch a connect request. Run it INSIDE THE VM, in a NORMAL PowerShell, NOT as administrator: an attacker wouldn't be.
#   powershell -ExecutionPolicy Bypass -File fake-service-test.ps1
# It asks for administrator approval twice, to stop and restart the service. The "key" it uses is made up.
$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Dir = "C:\Program Files\KiwiVPN"
$Svc = "$Dir\kiwivpn-service.exe"
$Fake = "$Here\test-tools\fake_service.exe"
$Out = Join-Path $env:TEMP "kiwi-fake-service.txt"
$Conf = Join-Path $env:TEMP "kiwi-canary.conf"
$fails = 0
function Check($name, $ok, $detail = "") { if ($ok) { Write-Host "PASS  $name  $detail" -ForegroundColor Green } else { Write-Host "FAIL  $name  $detail" -ForegroundColor Red; $script:fails++ } }
function Elevated($command) { Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "-NoProfile -Command $command" }
# Runs a program and returns its output and exit code, without PowerShell turning its error text into a failure of this script.
function Run($exe, $arguments) {
  $o = [IO.Path]::GetTempFileName(); $e = [IO.Path]::GetTempFileName()
  $p = Start-Process $exe -ArgumentList $arguments -NoNewWindow -Wait -PassThru -RedirectStandardOutput $o -RedirectStandardError $e
  $text = (Get-Content $o -Raw) + (Get-Content $e -Raw)
  Remove-Item $o, $e -Force
  [pscustomobject]@{ Code = $p.ExitCode; Text = "$text".Trim() }
}

if (([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run this in a normal PowerShell, not as administrator." }
if (-not (Test-Path $Fake)) { throw "No $Fake. Run build-app-in-vm.ps1 first." }
if (-not (Test-Path $Svc)) { throw "The service isn't installed. Run build-app-in-vm.ps1 first." }
if ((Get-Service KiwiVPN).Status -ne "Running") { throw "The KiwiVPN service isn't running." }

Write-Host "--- with the real service running"
$s = Run $Svc "status"
Check "the app's check accepts the real service, without administrator rights" ($s.Code -eq 0 -and $s.Text -match "state:") $s.Text.Split("`n")[0]
if ($s.Text -notmatch "state: Disconnected") { throw "Press Disconnect in the app first, then run this again." }
$r = Run $Fake "instance"
Check "Windows refuses a fake instance on the service's own pipe" ($r.Code -eq 0 -and $r.Text -match "REFUSED 5") $r.Text

Write-Host "--- with the service stopped, so the name is free. Approve the prompt."
Elevated "Stop-Service KiwiVPN -Force"
$listener = $null
try {
  if ((Get-Service KiwiVPN).Status -ne "Stopped") { throw "The service didn't stop." }
  $key = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("KIWI-CANARY-NOT-A-REAL-KEY-32-B!"))
  Set-Content -Path $Conf -Encoding ascii -Value "[Interface]`nPrivateKey = $key`nAddress = 10.66.0.9/32`nDNS = 1.1.1.1`n`n[Peer]`nPublicKey = $key`nEndpoint = 192.0.2.1:51821`nAllowedIPs = 0.0.0.0/0`n"
  $listener = Start-Process $Fake -ArgumentList "serve 600" -PassThru -NoNewWindow -RedirectStandardOutput $Out
  $until = (Get-Date).AddSeconds(10)
  while (-not ((Test-Path $Out) -and (Select-String -Path $Out -Pattern "LISTENING" -Quiet)) -and (Get-Date) -lt $until) { Start-Sleep -Milliseconds 200 }
  Check "the fake service holds the pipe's name" ((Test-Path $Out) -and (Select-String -Path $Out -Pattern "LISTENING" -Quiet))

  $c = Run $Svc "connect `"$Conf`""
  Check "a connect request is refused before it's sent" ($c.Code -ne 0 -and $c.Text -match "Something other than") $c.Text

  Write-Host ""
  Write-Host "Now the real app, against the fake:" -ForegroundColor Yellow
  Write-Host "  1. Open $Dir\KiwiVPN.exe"
  Write-Host "  2. It should say SERVICE OFFLINE and 'Something other than the Kiwi VPN service is answering. Nothing was sent to it.', with Connect greyed out."
  Write-Host "  3. Leave it open for 10 seconds, then close it."
  $seen = Read-Host "Did it say that? (y/n)"
  Check "the app refuses the fake service" ($seen -match "^y")

  $k = Run $Fake "control"
  Start-Sleep -Milliseconds 500
  $log = (Get-Content $Out) -join "`n"
  Check "the control: the fake really does catch what's sent to it, so these passes mean something" ($k.Code -eq 0 -and $log -match "client sent \d+ bytes: control")
  Check "the fake caught nothing else: no key, no request of any kind" (($log -notmatch "CAUGHT") -and ($log -notmatch "KIWI") -and ($log -notmatch "client sent [1-9]\d* bytes: nothing"))
  Write-Host "Connections the fake saw: $(([regex]::Matches($log, 'client sent')).Count), of which carried a request: $(([regex]::Matches($log, 'CAUGHT')).Count)"
}
finally {
  if ($listener -and -not $listener.HasExited) { Stop-Process -Id $listener.Id -Force }
  Remove-Item $Conf -Force -ErrorAction SilentlyContinue
  Write-Host "--- restarting the real service. Approve the prompt."
  Elevated "Start-Service KiwiVPN"
}
Start-Sleep 1
$s = Run $Svc "status"
Check "the real service is back, and the app's check accepts it again" ($s.Code -eq 0 -and $s.Text -match "state: Disconnected") $s.Text.Split("`n")[0]
if ($fails) { Write-Host "$fails FAILED" -ForegroundColor Red; exit 1 }
Write-Host "ALL PASSED" -ForegroundColor Green
