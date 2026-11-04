# Builds the Kiwi VPN desktop window INSIDE THE VM, puts the already-proven service in place, and opens the window. Milestone 1: no installer yet.
#   powershell -ExecutionPolicy Bypass -File build-app-in-vm.ps1            (as administrator)
#   powershell -ExecutionPolicy Bypass -File build-app-in-vm.ps1 -Remove    removes the service and everything it put on the machine
# Not for the host: Smart App Control blocks these builds there, and no Kiwi service may ever run on the PC with client1. It stops itself there.
param([switch]$Remove, [switch]$NoLaunch)
$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Dir = "C:\Program Files\KiwiVPN"
$Svc = "$Dir\kiwivpn-service.exe"
$Engine = "C:\Program Files\WireGuard"

if (Get-Service 'WireGuardTunnel$*' -ErrorAction SilentlyContinue | Where-Object Name -ne 'WireGuardTunnel$kiwi') { throw "Another WireGuard tunnel is on this machine. This belongs in the VM. Nothing was changed." }
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run this as administrator." }

if ($Remove) {
  if (Test-Path $Svc) { & $Svc uninstall }
  Start-Sleep 2
  Remove-Item -Recurse -Force $Dir -ErrorAction SilentlyContinue
  # tunnel.dll keeps its log here. The real WireGuard app would have wireguard.exe beside it, and then the folder isn't ours.
  if ((Test-Path $Engine) -and -not (Test-Path "$Engine\wireguard.exe")) { Remove-Item -Recurse -Force $Engine -ErrorAction SilentlyContinue }
  Write-Host "Removed. Services left: $((Get-Service KiwiVPN, 'WireGuardTunnel$kiwi' -ErrorAction SilentlyContinue | Measure-Object).Count)"
  exit 0
}

$Work = "C:\kiwi-build"
New-Item -ItemType Directory -Force $Work | Out-Null
$env:RUSTUP_HOME = "$Work\rustup"; $env:CARGO_HOME = "$Work\cargo"; $env:CARGO_TARGET_DIR = "$Work\target-app"
$env:PATH = "$Work\cargo\bin;$env:PATH"
function Need($ok, $what) { if (-not $ok) { throw $what } }

Write-Host "[1/5] Node"
Need (Get-Command npm -ErrorAction SilentlyContinue) "npm isn't on the PATH. Open a new PowerShell window after installing Node."
node --version

Write-Host "[2/5] C++ build tools, which Rust's Windows linker needs (several GB, once)"
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$haveCpp = (Test-Path $vswhere) -and (& $vswhere -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath)
if (-not $haveCpp) {
  winget install --id Microsoft.VisualStudio.2022.BuildTools --silent --accept-package-agreements --accept-source-agreements --override "--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
  $haveCpp = (Test-Path $vswhere) -and (& $vswhere -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath)
  Need $haveCpp "The C++ build tools didn't install. Install 'Desktop development with C++' from Visual Studio Build Tools, then run this again."
}

Write-Host "[3/5] Rust"
if (-not (Test-Path "$Work\cargo\bin\cargo.exe")) {
  Invoke-WebRequest -UseBasicParsing https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe -OutFile "$Work\rustup-init.exe"
  & "$Work\rustup-init.exe" -y --no-modify-path --profile minimal --default-host x86_64-pc-windows-msvc --default-toolchain stable
  Need ($LASTEXITCODE -eq 0) "Rust didn't install."
}
cargo --version

Write-Host "[4/5] The app: tests, then the build"
Push-Location "$Here\app"
npm ci
Need ($LASTEXITCODE -eq 0) "npm ci failed."
npm test
Need ($LASTEXITCODE -eq 0) "The window's tests failed."
npm run tauri -- build --debug
Need ($LASTEXITCODE -eq 0) "The app didn't build."
Pop-Location
$Exe = "$Work\target-app\debug\KiwiVPN.exe"
Need (Test-Path $Exe) "No KiwiVPN.exe at $Exe"

Write-Host "[5/5] The service that passed the connection test, in its protected folder"
if (Test-Path $Svc) { & $Svc uninstall; Start-Sleep 2 }
New-Item -ItemType Directory -Force $Dir | Out-Null
Copy-Item "$Here\dist\kiwivpn-service.exe", "$Here\dist\tunnel.dll", "$Here\dist\wireguard.dll" $Dir -Force
& $Svc install
Need ($LASTEXITCODE -eq 0) "The service didn't install."
Copy-Item $Exe "$Dir\KiwiVPN.exe" -Force

Write-Host ""
Write-Host "Built: $Dir\KiwiVPN.exe" -ForegroundColor Green
Write-Host "Open it from a NORMAL File Explorer window, NOT this administrator PowerShell: the test has to prove it works without administrator rights."
if (-not $NoLaunch) { Start-Process explorer.exe $Dir }
