# MAIN PC. Packs the committed Android source for the VM. Runs no build tool here.
#   powershell -ExecutionPolicy Bypass -File android-client\prepare-vm-build.ps1
$ErrorActionPreference = "Stop"
$Repo = Split-Path $PSScriptRoot -Parent
$Short = git -C $Repo rev-parse --short HEAD
$Full = git -C $Repo rev-parse HEAD
if (git -C $Repo status --short -- android-client) { throw "android-client has uncommitted changes. Commit first, so the ZIP matches a commit." }
$Zip = "C:\AI\pc-wt\kiwi-android-build-$Short.zip"
Remove-Item $Zip -Force -ErrorAction SilentlyContinue
git -C "$Repo\android-client" archive --format=zip -o $Zip HEAD
if ($LASTEXITCODE -ne 0) { throw "git archive failed" }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$z = [IO.Compression.ZipFile]::Open($Zip, "Update")
$e = $z.CreateEntry("BUILD-INFO.txt")
$w = New-Object IO.StreamWriter($e.Open())
$w.Write("commit $Full`n")
$w.Dispose()
$z.Dispose()
Write-Host "Package: $Zip"
Write-Host "SHA-256: $((Get-FileHash $Zip -Algorithm SHA256).Hash)"
