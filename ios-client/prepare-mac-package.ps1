# WINDOWS. Packs the committed iOS source for a Mac, since this repository has no remote. Compiles nothing.
#   powershell -ExecutionPolicy Bypass -File ios-client\prepare-mac-package.ps1
$ErrorActionPreference = "Stop"
$Repo = Split-Path $PSScriptRoot -Parent
if (git -C $Repo status --short -- ios-client) { throw "ios-client has uncommitted changes. Commit first, so the ZIP matches a commit." }
$Short = git -C $Repo rev-parse --short HEAD
$Full = git -C $Repo rev-parse HEAD
$Zip = "C:\AI\pc-wt\kiwi-ios-source-$Short.zip"
Remove-Item $Zip -Force -ErrorAction SilentlyContinue
git -C "$Repo\ios-client" archive --format=zip -o $Zip HEAD
if ($LASTEXITCODE -ne 0) { throw "git archive failed" }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$z = [IO.Compression.ZipFile]::Open($Zip, "Update")
$w = New-Object IO.StreamWriter($z.CreateEntry("BUILD-INFO.txt").Open())
$w.Write("commit $Full`n")
$w.Dispose()
$z.Dispose()
Write-Host "Package: $Zip"
Write-Host "SHA-256: $((Get-FileHash $Zip -Algorithm SHA256).Hash)"
