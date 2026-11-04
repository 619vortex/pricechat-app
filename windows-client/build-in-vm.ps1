# Builds Kiwi VPN's service and tunnel inside the VM. Not for the host: Smart App Control blocks these builds there (docs/VM-SETUP.md).
# Stops at the first problem. Every download is pinned: a different file stops the build. Not yet run: expect to correct it on first use.
$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Work = "C:\kiwi-build"
$WgCommit = "6ece77bc487c8aa697e3c092197621c4f3e5ccb8"
$WgNtSha256 = "DCEB30A9BC4BE48CCE0F74160FC88A585A2C2627366E8F846FC6658F9038DACE"
New-Item -ItemType Directory -Force $Work, "$Here\dist" | Out-Null

Write-Host "[1/4] WireGuard's tunnel.dll, from the verified commit"
if (-not (Test-Path "$Work\wireguard-windows")) { git clone https://git.zx2c4.com/wireguard-windows "$Work\wireguard-windows" }
git -C "$Work\wireguard-windows" checkout --quiet $WgCommit
if ((git -C "$Work\wireguard-windows" rev-parse HEAD) -ne $WgCommit) { throw "Not the verified WireGuard commit" }
cmd /c "$Work\wireguard-windows\embeddable-dll-service\build.bat"
if ($LASTEXITCODE -ne 0) { throw "tunnel.dll didn't build" }

Write-Host "[2/4] The signed driver DLL, unmodified"
$zip = "$Work\wireguard-nt-1.1.zip"
Invoke-WebRequest -UseBasicParsing https://download.wireguard.com/wireguard-nt/wireguard-nt-1.1.zip -OutFile $zip
if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne $WgNtSha256) { throw "wireguard-nt isn't the verified file" }
Expand-Archive -Force $zip "$Work\wgnt"
if ((Get-AuthenticodeSignature "$Work\wgnt\wireguard-nt\bin\amd64\wireguard.dll").Status -ne "Valid") { throw "wireguard.dll's signature isn't valid" }

Write-Host "[3/4] Rust, using the compiler WireGuard's build just downloaded"
$env:RUSTUP_HOME = "$Work\rustup"; $env:CARGO_HOME = "$Work\cargo"
$env:PATH = "$Work\cargo\bin;$Work\wireguard-windows\.deps\llvm-mingw\bin;$env:PATH"
if (-not (Test-Path "$Work\cargo\bin\cargo.exe")) {
  Invoke-WebRequest -UseBasicParsing https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe -OutFile "$Work\rustup-init.exe"
  & "$Work\rustup-init.exe" -y --no-modify-path --profile minimal --default-host x86_64-pc-windows-gnu --default-toolchain stable
}
Push-Location "$Here\service"
cargo test
if ($LASTEXITCODE -ne 0) { throw "Tests failed" }
cargo build --release
if ($LASTEXITCODE -ne 0) { throw "The service didn't build" }
Pop-Location

Write-Host "[4/4] Collecting"
Copy-Item "$Here\service\target\release\kiwivpn-service.exe", "$Work\wireguard-windows\embeddable-dll-service\amd64\tunnel.dll", "$Work\wgnt\wireguard-nt\bin\amd64\wireguard.dll" "$Here\dist" -Force
Get-ChildItem "$Here\dist" | ForEach-Object { "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.Substring(0, 16), $_.Name }
Write-Host "Done: $Here\dist"
