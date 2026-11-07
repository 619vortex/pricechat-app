# MAIN PC. Copies the committed Windows client into the private build repo (619vortex/KIWIVPN) and pushes it, which
# starts the "Kiwi VPN Windows" GitHub Actions build. Compiles and runs nothing here.
# Only windows-client/ and .github/workflows/kiwi-windows.yml change in that repo; the iPhone project at its root,
# codemagic.yaml and anything else stay exactly as they are.
#   powershell -ExecutionPolicy Bypass -File windows-client\sync-ci-repo.ps1 [-NoPush]
# Exported: app, client, service and ci (what the build needs). Not exported: docs, VM and deploy scripts, test helpers.
param([switch]$NoPush, [string]$RepoUrl = "git@github.com:619vortex/KIWIVPN.git")
$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Repo = git -C $Here rev-parse --show-toplevel
$Cloud = "C:\AI\pc-wt\kiwi-ios-cloud-repo"
$Key = "$env:USERPROFILE\.kiwi-ios-cloud\push_key"
$env:GIT_SSH_COMMAND = "ssh -i `"$($Key -replace '\\','/')`" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

if (git -C $Repo status --short -- windows-client/app windows-client/client windows-client/service windows-client/ci) { throw "windows-client has uncommitted changes. Commit first, so the build matches a commit." }
if (-not (Test-Path "$Cloud\.git")) {
    git clone -q $RepoUrl $Cloud
    if ($LASTEXITCODE -ne 0) { throw "Couldn't clone $RepoUrl" }
    git -C $Cloud config core.autocrlf false
    git -C $Cloud config user.name "Kiwi VPN build"
    git -C $Cloud config user.email "kiwi-build@users.noreply.github.com"
}
if (git -C $Cloud status --short) { throw "$Cloud has uncommitted changes. Nothing was changed." }
git -C $Cloud fetch -q origin
if ($LASTEXITCODE -ne 0) { throw "Couldn't reach $RepoUrl" }
git -C $Cloud merge -q --ff-only origin/main
if ($LASTEXITCODE -ne 0) { throw "The local copy has diverged from GitHub. Nothing was changed." }

$Dest = Join-Path $Cloud "windows-client"
if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
New-Item -ItemType Directory -Force $Dest | Out-Null
$Tar = Join-Path $env:TEMP "kiwi-win-$PID.tar"
git -C "$Repo\windows-client" archive --format=tar -o $Tar HEAD -- app client service ci
if ($LASTEXITCODE -ne 0) { throw "git archive failed" }
& "$env:SystemRoot\System32\tar.exe" -xf $Tar -C $Dest  # Windows' own tar: Git Bash's reads "C:" as a host
if ($LASTEXITCODE -ne 0) { throw "unpacking failed" }
Remove-Item $Tar -Force
$Commit = git -C $Repo rev-parse HEAD
[IO.File]::WriteAllText("$Dest\BUILD-INFO.txt", "commit $Commit`n")
$Wf = Join-Path $Cloud ".github\workflows"
New-Item -ItemType Directory -Force $Wf | Out-Null
Copy-Item "$Dest\ci\kiwi-windows.yml" "$Wf\kiwi-windows.yml" -Force

# Nothing secret may go: signing files, env files, private keys, tokens.
foreach ($p in "*.pfx", "*.p12", "*.pem", "*.key", "*.p8", ".env", ".env.*", "*.jks", "*.keystore") {
    if (Get-ChildItem $Dest -Recurse -Force -Filter $p) { throw "A secret-looking file ($p) is in the export. Nothing was pushed." }
}
$Bad = Get-ChildItem $Dest -Recurse -File -Include *.rs, *.ts, *.tsx, *.json, *.toml, *.yml, *.ps1, *.nsh, *.html, *.md |
    Select-String -Pattern 'PRIVATE KEY-----', 'ghp_[A-Za-z0-9]{30}', 'github_pat_', 'sk_live_', 'AKIA[0-9A-Z]{16}', 'xox[bp]-' -List
if ($Bad) { throw "Something like a key is in: $($Bad.Path -join ', '). Nothing was pushed." }

git -C $Cloud add -A -- windows-client .github/workflows/kiwi-windows.yml
$Other = git -C $Cloud status --short | Where-Object { $_ -notmatch ' (windows-client/|\.github/workflows/kiwi-windows\.yml)' }
if ($Other) { throw "Something outside windows-client changed: $Other. Nothing was pushed." }
if (git -C $Cloud diff --cached --name-only) { git -C $Cloud commit -q -m "Kiwi VPN Windows source from $($Commit.Substring(0, 12))" } else { Write-Host "No change since the last sync." }
git -C $Cloud log --oneline -1
if (-not $NoPush) {
    git -C $Cloud push -q origin main
    if ($LASTEXITCODE -ne 0) { throw "git push failed" }
    Write-Host "Pushed. The build: https://github.com/619vortex/KIWIVPN/actions/workflows/kiwi-windows.yml"
}
