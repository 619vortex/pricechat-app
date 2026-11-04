# Proves, on a real tunnel, that an unauthorised program can't control it and that the private key stays where it should. Run INSIDE THE VM, in a NORMAL PowerShell.
#   powershell -ExecutionPolicy Bypass -File ipc-and-key-test.ps1 -Config C:\path\germany.conf
# The config is read only to search for its key. The key is never printed or passed on a command line.
param([Parameter(Mandatory)][string]$Config)
$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Dir = "C:\Program Files\KiwiVPN"
$Svc = "$Dir\kiwivpn-service.exe"
$Fake = "$Here\test-tools\fake_service.exe"
$Conf = "$Dir\tunnels\kiwi.conf"
$fails = 0
function Check($name, $ok, $detail = "") { if ($ok) { Write-Host "PASS  $name  $detail" -ForegroundColor Green } else { Write-Host "FAIL  $name  $detail" -ForegroundColor Red; $script:fails++ } }
function Run($exe, $arguments) {
  $o = [IO.Path]::GetTempFileName(); $e = [IO.Path]::GetTempFileName()
  $p = Start-Process $exe -ArgumentList $arguments -NoNewWindow -Wait -PassThru -RedirectStandardOutput $o -RedirectStandardError $e
  $text = (Get-Content $o -Raw) + (Get-Content $e -Raw)
  Remove-Item $o, $e -Force
  [pscustomobject]@{ Code = $p.ExitCode; Text = "$text".Trim() }
}
function Connected { (Run $Svc "status").Text -match "state: Connected" }

if (([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run this in a normal PowerShell, not as administrator." }
foreach ($need in $Fake, $Svc, $Config) { if (-not (Test-Path $need)) { throw "Missing $need. Run build-app-in-vm.ps1 first." } }
$key = (Select-String -Path $Config -Pattern '^\s*PrivateKey\s*=\s*(\S+)\s*$').Matches.Groups[1].Value
if (-not $key) { throw "No private key found in $Config" }

Write-Host "Open KiwiVPN.exe, press Connect and choose this same config. Waiting up to 2 minutes for Connected..." -ForegroundColor Yellow
$until = (Get-Date).AddMinutes(2)
while (-not (Connected) -and (Get-Date) -lt $until) { Start-Sleep 2 }
if (-not (Connected)) { throw "It never reported Connected." }

Write-Host "--- the tunnel is up. Unauthorised programs try to control it."
$r = Run $Svc "status"
Check "anyone signed in may read the status" ($r.Code -eq 0)
$r = Run $Fake "disconnect"
Check "another program can't disconnect it" ($r.Code -eq 0 -and $r.Text -match "REFUSED") $r.Text
$r = Run $Fake "connect"
Check "another program can't point it at another server" ($r.Code -eq 0 -and $r.Text -match "REFUSED") $r.Text
$r = Run $Svc "disconnect"
Check "the service's own command-line tool can't either, without administrator rights" ($r.Code -ne 0 -and $r.Text -match "Only the Kiwi VPN app") $r.Text

$spoofDir = Join-Path $env:TEMP "kiwi-spoof"
New-Item -ItemType Directory -Force $spoofDir | Out-Null
$spoof = Join-Path $spoofDir "KiwiVPN.exe"
Copy-Item $Fake $spoof -Force
$r = Run $spoof "disconnect"
Check "a program merely named KiwiVPN.exe can't either" ($r.Code -eq 0 -and $r.Text -match "REFUSED") $r.Text
Remove-Item -Recurse -Force $spoofDir -ErrorAction SilentlyContinue
Check "after all that, it's still Connected" (Connected)

Write-Host "--- the private key, while connected"
$readable = $true
try { [void][IO.File]::ReadAllBytes($Conf) } catch { $readable = $false }
Check "this user can't read the service's copy of the config" (-not $readable)
$canAdd = $true
try { [IO.File]::WriteAllText("$Dir\kiwi-write-test.tmp", "x"); Remove-Item "$Dir\kiwi-write-test.tmp" -Force } catch { $canAdd = $false }
Check "this user can't write to the install folder" (-not $canAdd)

Write-Host ""
Write-Host "Now press Disconnect in the app, and close the app." -ForegroundColor Yellow
[void](Read-Host "Press Enter when that's done")
Start-Sleep 2
Check "it's disconnected" ((Run $Svc "status").Text -match "state: Disconnected")

Write-Host "--- searching for the key. Approve the prompt: the tunnel's folder and log are for administrators."
$out = Join-Path $env:TEMP "kiwi-key-scan.txt"
Remove-Item $out -Force -ErrorAction SilentlyContinue
$data = Join-Path $env:LOCALAPPDATA "live.pricechat.kiwivpn"
Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$Here\scan-for-key.ps1`" -Config `"$Config`" -Out `"$out`" -AppData `"$data`""
$scan = if (Test-Path $out) { Get-Content $out } else { @() }
Remove-Item $out -Force -ErrorAction SilentlyContinue
Check "the scan ran" ($scan -contains "done") ($scan -join " | ")
Check "the config with the key is gone from the service" ($scan -contains "conf: gone")
Check "the key isn't anywhere in the install folder" ($scan -contains "install folder: clean")
Check "the key isn't in the tunnel's log" (($scan -contains "log: clean") -or ($scan -contains "log: none"))
Check "the key isn't in the app's own data, which holds the web page's storage" (($scan -contains "app data: clean") -or ($scan -contains "app data: none"))

if ($fails) { Write-Host "$fails FAILED" -ForegroundColor Red; exit 1 }
Write-Host "ALL PASSED" -ForegroundColor Green
