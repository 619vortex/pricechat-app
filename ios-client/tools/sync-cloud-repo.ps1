# WINDOWS. Copies the committed iOS source into the small private repository Codemagic builds, and pushes it. Compiles nothing.
# Only ios-client goes, with no history of the main repository, and without docs/ or the Windows helper scripts (internal, and the build doesn't need them).
#   powershell -ExecutionPolicy Bypass -File ios-client\tools\sync-cloud-repo.ps1 -RepoUrl git@github.com:OWNER/kiwi-vpn-ios.git [-NoPush]
param([Parameter(Mandatory)][string]$RepoUrl, [switch]$NoPush)
$ErrorActionPreference = "Stop"
$Source = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Cloud = "C:\AI\pc-wt\kiwi-ios-cloud-repo"
$Key = "$env:USERPROFILE\.kiwi-ios-cloud\push_key"
if (git -C $Source status --short -- ios-client) { throw "ios-client has uncommitted changes. Commit first, so the cloud build matches a commit." }

if (-not (Test-Path "$Cloud\.git")) {
    New-Item -ItemType Directory -Force $Cloud | Out-Null
    git -C $Cloud init -q -b main
    git -C $Cloud config core.autocrlf false
    git -C $Cloud config user.name "Kiwi VPN build"
    git -C $Cloud config user.email "kiwi-build@users.noreply.github.com"
    git -C $Cloud remote add origin $RepoUrl
}
git -C $Cloud remote set-url origin $RepoUrl
Get-ChildItem $Cloud -Force | Where-Object { $_.Name -ne ".git" } | Remove-Item -Recurse -Force

$Tar = Join-Path $env:TEMP "kiwi-ios-$PID.tar"
git -C "$Source\ios-client" archive --format=tar -o $Tar HEAD -- . ":(exclude)docs" ":(exclude)prepare-mac-package.ps1" ":(exclude)tools/sync-cloud-repo.ps1"
if ($LASTEXITCODE -ne 0) { throw "git archive failed" }
tar -xf $Tar -C $Cloud
if ($LASTEXITCODE -ne 0) { throw "unpacking failed" }
Remove-Item $Tar -Force
$Commit = git -C $Source rev-parse HEAD
[IO.File]::WriteAllText("$Cloud\BUILD-INFO.txt", "commit $Commit`n")

foreach ($p in "*.p8", "*.p12", "*.mobileprovision", "*.pem", "*.key", "*.jks", "*.keystore") {
    if (Get-ChildItem $Cloud -Recurse -Force -Filter $p | Where-Object { $_.FullName -notmatch "\\.git\\" }) { throw "A signing file ($p) is in the export. Nothing was pushed." }
}

git -C $Cloud add -A
if (git -C $Cloud status --short) { git -C $Cloud commit -q -m "Kiwi VPN iPhone source from $($Commit.Substring(0, 12))" }
git -C $Cloud log --oneline -1
if (-not $NoPush) {
    $env:GIT_SSH_COMMAND = "ssh -i `"$($Key -replace '\\','/')`" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
    git -C $Cloud push -q -u origin main
    if ($LASTEXITCODE -ne 0) { throw "git push failed" }
    Write-Host "Pushed."
}
