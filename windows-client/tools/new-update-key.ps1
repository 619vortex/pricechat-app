# OWNER ONLY, on the main PC, by hand. Makes Kiwi VPN's two production update-manifest keys (Ed25519, RFC 8032): k1 signs every
# manifest, k2 is the spare that only ever comes out to replace k1 (docs/AUTO-UPDATE.md, "Manifest key").
# Agents and CI never run this. It never prints a private key.
#
#   powershell -ExecutionPolicy Bypass -File windows-client\tools\new-update-key.ps1 [-Out <folder outside the repository>]
#
# Uses OpenSSL from Git for Windows (signed, so Smart App Control lets it run). Afterwards:
#   1. Paste the two printed lines into PRODUCTION_KEYS in windows-client/service/src/update.rs and commit (public keys only).
#   2. GitHub > 619vortex/KIWIVPN > Settings > Environments > New environment "kiwi-publish" (deployment branches: main only).
#      Add secret KIWI_MANIFEST_KEY = the whole text of k1.pem. k2.pem goes nowhere online.
#   3. Back up k1.pem and k2.pem: the password manager, and an encrypted USB stick (e.g. a 7-Zip AES-256 archive whose
#      passphrase is kept apart). Then delete the folder this script made: the keys must not stay on the PC.
param([string]$Out = (Join-Path $env:USERPROFILE ("kiwi-update-keys-" + (Get-Date -Format "yyyyMMdd-HHmmss"))))
$ErrorActionPreference = "Stop"

$openssl = @("$env:ProgramFiles\Git\usr\bin\openssl.exe", "$env:ProgramFiles\Git\mingw64\bin\openssl.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $openssl) { throw "OpenSSL from Git for Windows wasn't found. Install Git for Windows, or pass its openssl.exe path by editing this script." }

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$full = [IO.Path]::GetFullPath($Out)
if ($full.StartsWith($repo, [StringComparison]::OrdinalIgnoreCase)) { throw "Choose a folder outside the repository ($repo): private keys must never be committed." }
if (Test-Path $full) { throw "$full already exists. Nothing was made." }
New-Item -ItemType Directory -Path $full | Out-Null
# only this user may read the folder
& icacls $full /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" | Out-Null

$lines = @()
foreach ($id in "k1", "k2") {
    $pem = Join-Path $full "$id.pem"
    & $openssl genpkey -algorithm ed25519 -out $pem
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $pem)) { throw "OpenSSL couldn't make $id" }
    $der = Join-Path $full "$id.pub.der"
    & $openssl pkey -in $pem -pubout -outform DER -out $der
    if ($LASTEXITCODE -ne 0) { throw "OpenSSL couldn't read $id's public key" }
    $bytes = [IO.File]::ReadAllBytes($der)
    if ($bytes.Length -ne 44) { throw "$id's public key isn't an Ed25519 key ($($bytes.Length) bytes)" }
    $hex = -join ($bytes[12..43] | ForEach-Object { $_.ToString("x2") })
    Remove-Item $der
    $lines += "    Key { id: `"$id`", public: hex32(`"$hex`") },"
}

Write-Host ""
Write-Host "Made k1 and k2 in $full (private keys: k1.pem, k2.pem)."
Write-Host ""
Write-Host "1. Paste into PRODUCTION_KEYS in windows-client/service/src/update.rs (public keys, safe to commit):"
Write-Host ""
Write-Host "pub const PRODUCTION_KEYS: &[Key] = &["
$lines | ForEach-Object { Write-Host $_ }
Write-Host "];"
Write-Host ""
Write-Host "2. GitHub environment kiwi-publish (main only): secret KIWI_MANIFEST_KEY = the text of k1.pem."
Write-Host "3. Back up k1.pem and k2.pem offline (password manager + encrypted USB), then delete $full."
