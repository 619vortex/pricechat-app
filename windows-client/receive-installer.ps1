# MAIN PC. Checks the installer the VM sent back. Reads and hashes it only: it never runs it, and nothing Kiwi runs on this PC.
#   powershell -ExecutionPolicy Bypass -File C:\AI\pc-wt\kiwi-windows\windows-client\receive-installer.ps1
$Repo = Split-Path $PSScriptRoot -Parent
$Dir = "C:\AI\pc-wt\kiwi-release"
$Exe = "$Dir\KiwiVPN-Setup.exe"
function Stop-Here($why) { Write-Host "STOPPED: $why" -ForegroundColor Red; exit 1 }
if (-not (Test-Path $Exe)) { Stop-Here "no installer at $Exe" }
if (-not (Test-Path "$Exe.sha256")) { Stop-Here "no KiwiVPN-Setup.exe.sha256 beside it" }
$Hash = (Get-FileHash $Exe -Algorithm SHA256).Hash
$Want = ((Get-Content "$Exe.sha256" -TotalCount 1) -split "\s+")[0]
if ($Hash -ne $Want) { Stop-Here "the installer's hash doesn't match the VM's: $Hash, expected $Want" }
$Info = (Get-Item $Exe).VersionInfo
$Commit = if (Test-Path "$Dir\BUILD-INFO.txt") { ((Get-Content "$Dir\BUILD-INFO.txt" -TotalCount 1) -split "\s+")[1] } else { $null }
$Head = git -C $Repo rev-parse HEAD
Write-Host "Installer matches the VM's hash" -ForegroundColor Green
Write-Host "SHA-256: $Hash"
Write-Host "Size: $([math]::Round((Get-Item $Exe).Length / 1MB, 1)) MB   Version: $($Info.ProductVersion)   Signature: $((Get-AuthenticodeSignature $Exe).Status)"
if (-not $Commit) { Write-Host "Source: not recorded" -ForegroundColor Yellow }
else {
  Write-Host "Source: $($Commit.Substring(0, 7))"
  $Client = git -C $Repo diff --name-only $Commit HEAD -- windows-client/app windows-client/client windows-client/service
  if ($Client) { Write-Host "App code has changed since this build, so it's out of date:`n$($Client -join "`n")" -ForegroundColor Yellow }
}
