# Builds KiwiVPN-Setup.exe INSIDE THE VM: tests the service and the window, then packages the release builds with WireGuard's DLLs. It doesn't install anything: you run the installer yourself, like a customer.
#   powershell -ExecutionPolicy Bypass -File build-app-in-vm.ps1            (as administrator)
#   powershell -ExecutionPolicy Bypass -File build-app-in-vm.ps1 -Remove    removes Kiwi VPN and everything it put on the machine
# Not for the host: Smart App Control blocks these builds there, and no Kiwi service may ever run on the PC with client1. It stops itself there.
param([switch]$Remove, [switch]$NoLaunch, [string]$ReturnTo = "\\tsclient\C\AI\pc-wt\kiwi-release")
$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Dir = "C:\Program Files\KiwiVPN"
$Svc = "$Dir\kiwivpn-service.exe"
$Engine = "C:\Program Files\WireGuard"

if (Get-Service 'WireGuardTunnel$*' -ErrorAction SilentlyContinue | Where-Object Name -ne 'WireGuardTunnel$kiwi') { throw "Another WireGuard tunnel is on this machine. This belongs in the VM. Nothing was changed." }
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run this as administrator." }

function RemoveInstalled {
  if (Test-Path "$Dir\uninstall.exe") { Start-Process "$Dir\uninstall.exe" -ArgumentList "/S" -Wait; Start-Sleep 3 }
  if (Test-Path $Svc) { & $Svc uninstall; Start-Sleep 2 }
  Remove-Item -Recurse -Force $Dir -ErrorAction SilentlyContinue
}

if ($Remove) {
  RemoveInstalled
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

Write-Host "[4/6] The service's tests, which need the pipe's name free, then its build"
RemoveInstalled
Push-Location "$Here\service"
cargo test -- --test-threads=1
Need ($LASTEXITCODE -eq 0) "The service's tests failed."
Pop-Location
Push-Location "$Here\client"
cargo test
Need ($LASTEXITCODE -eq 0) "The sign-in and key tests failed."
Pop-Location
Push-Location "$Here\service"
cargo build --examples
Need ($LASTEXITCODE -eq 0) "The security test's tools didn't build."
cargo build --release --bins
Need ($LASTEXITCODE -eq 0) "The service didn't build."
Pop-Location

Write-Host "[5/6] The app: tests, then the installer"
$Bundle = "$Here\app\src-tauri\bundle-files"
New-Item -ItemType Directory -Force $Bundle | Out-Null
Copy-Item "$Work\target-app\release\kiwivpn-service.exe", "$Here\dist\tunnel.dll", "$Here\dist\wireguard.dll" $Bundle -Force
Need ((Get-AuthenticodeSignature "$Bundle\wireguard.dll").Status -eq "Valid") "wireguard.dll's signature isn't valid."
Push-Location "$Here\app"
npm ci
Need ($LASTEXITCODE -eq 0) "npm ci failed."
npm test
Need ($LASTEXITCODE -eq 0) "The window's tests failed."
# A public build: --no-default-features removes the "use a config file" testing option.
npm run tauri -- build -- --no-default-features
Need ($LASTEXITCODE -eq 0) "The installer didn't build."
Pop-Location
$Built = Get-ChildItem "$Work\target-app\release\bundle\nsis\*-setup.exe" | Sort-Object LastWriteTime | Select-Object -Last 1
Need $Built "No installer under $Work\target-app\release\bundle\nsis"
Copy-Item $Built.FullName "$Here\KiwiVPN-Setup.exe" -Force

Write-Host "[6/6] The fake service for the security test"
New-Item -ItemType Directory -Force "$Here\test-tools" | Out-Null
Copy-Item "$Work\target-app\debug\examples\fake_service.exe" "$Here\test-tools" -Force

Write-Host ""
$Sha = (Get-FileHash "$Here\KiwiVPN-Setup.exe" -Algorithm SHA256).Hash
"$Sha  KiwiVPN-Setup.exe" | Set-Content "$Here\KiwiVPN-Setup.exe.sha256" -Encoding ascii
Write-Host "Built: $Here\KiwiVPN-Setup.exe" -ForegroundColor Green
Write-Host ("SHA-256: " + $Sha + "   Size: " + [math]::Round((Get-Item "$Here\KiwiVPN-Setup.exe").Length / 1MB, 1) + " MB")

# Back to the main PC. It's only a copy: nothing runs on that side. Needs the VM's window in Enhanced Session with the C: drive shared; if not, it says so and the build still stands.
$Sent = $false
if ($ReturnTo) {
  try {
    New-Item -ItemType Directory -Force $ReturnTo -ErrorAction Stop | Out-Null
    $Names = @("KiwiVPN-Setup.exe", "KiwiVPN-Setup.exe.sha256") + @(if (Test-Path "$Here\BUILD-INFO.txt") { "BUILD-INFO.txt" })
    foreach ($n in $Names) { Copy-Item "$Here\$n" "$ReturnTo\$n" -Force -ErrorAction Stop }
    if ((Get-FileHash "$ReturnTo\KiwiVPN-Setup.exe" -Algorithm SHA256).Hash -ne $Sha) { throw "the copy's hash doesn't match" }
    $Sent = $true
    Write-Host "Copied to the main PC: $ReturnTo" -ForegroundColor Green
  } catch {
    Write-Host "Couldn't copy it to the main PC automatically: $($_.Exception.Message)" -ForegroundColor Yellow
  }
}
if (-not $Sent) { Write-Host "Copy these from $Here to C:\AI\pc-wt\kiwi-release on the main PC: KiwiVPN-Setup.exe, KiwiVPN-Setup.exe.sha256, BUILD-INFO.txt" }
Write-Host "To test it, double-click it in File Explorer, as a customer would."
if (-not $NoLaunch) { Start-Process explorer.exe $Here }
