# MAIN PC. Packs committed source into a ZIP for the VM and prints what to run there. It never compiles anything or runs cargo: see BUILDING.md.
#   powershell -ExecutionPolicy Bypass -File C:\AI\pc-wt\kiwi-windows\windows-client\prepare-vm-build.ps1
$Repo = Split-Path $PSScriptRoot -Parent
if (git -C $Repo status --short) { Write-Host "STOPPED: uncommitted changes in $Repo. The ZIP holds only what's committed." -ForegroundColor Red; exit 1 }
$Short = git -C $Repo rev-parse --short HEAD
$Zip = "C:\AI\pc-wt\kiwi-vm-build-$Short.zip"
python -I "$PSScriptRoot\build-zip.py" $Zip
if ($LASTEXITCODE -ne 0) { Write-Host "STOPPED: the ZIP wasn't made." -ForegroundColor Red; exit 1 }
New-Item -ItemType Directory -Force C:\AI\pc-wt\kiwi-release | Out-Null
Write-Host "ZIP: $Zip"
Write-Host "SHA-256: $((Get-FileHash $Zip -Algorithm SHA256).Hash)"
Write-Host ""
Write-Host "In the VM, in an administrator PowerShell:" -ForegroundColor Green
Write-Host "  Expand-Archive \\tsclient\C\AI\pc-wt\kiwi-vm-build-$Short.zip C:\kiwi-$Short -Force"
Write-Host "  cd C:\kiwi-$Short"
Write-Host "  powershell -ExecutionPolicy Bypass -File build-app-in-vm.ps1"
Write-Host ""
Write-Host "Back here afterwards:" -ForegroundColor Green
Write-Host "  powershell -ExecutionPolicy Bypass -File $PSScriptRoot\receive-installer.ps1"
