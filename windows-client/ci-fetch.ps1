# MAIN PC. Shows the latest "Kiwi VPN Windows" build in 619vortex/KIWIVPN and downloads its installer, checking the
# SHA-256. Never runs the installer (unsigned: Smart App Control would block it, and no Kiwi service runs on this PC).
# Needs a read-only fine-grained token in the user env var KIWI_GH_TOKEN (repo KIWIVPN only: Actions read, Contents read).
# The token is read from the environment and never printed.
#   powershell -ExecutionPolicy Bypass -File windows-client\ci-fetch.ps1 [-Run <id>] [-NoDownload]
param([long]$Run = 0, [switch]$NoDownload, [string]$Repo = "619vortex/KIWIVPN", [string]$Out = "C:\AI\pc-wt\kiwi-release\ci")
$ErrorActionPreference = "Stop"
$Token = [Environment]::GetEnvironmentVariable("KIWI_GH_TOKEN", "User")
if (-not $Token) { $Token = $env:KIWI_GH_TOKEN }
if (-not $Token) { throw "Set the user env var KIWI_GH_TOKEN first (a fine-grained token for ${Repo}: Actions read, Contents read)." }
$H = @{ Authorization = "Bearer $Token"; Accept = "application/vnd.github+json"; "X-GitHub-Api-Version" = "2022-11-28"; "User-Agent" = "kiwi-ci-fetch" }
$Api = "https://api.github.com/repos/$Repo"
function Get-Api($path) { Invoke-RestMethod -Headers $H -Uri "$Api$path" }

if ($Run -eq 0) {
    $runs = Get-Api "/actions/workflows/kiwi-windows.yml/runs?per_page=5"
    if (-not $runs.workflow_runs) { Write-Host "No runs yet. Check that Actions is enabled for $Repo."; exit 0 }
    $r = $runs.workflow_runs[0]
} else { $r = Get-Api "/actions/runs/$Run" }
Write-Host ("Run {0} on {1}: {2} / {3}  {4}" -f $r.id, $r.head_sha.Substring(0, 7), $r.status, $r.conclusion, $r.html_url)
foreach ($j in (Get-Api "/actions/runs/$($r.id)/jobs").jobs) {
    Write-Host ("  job {0}: {1} / {2}" -f $j.name, $j.status, $j.conclusion)
    foreach ($s in $j.steps) { if ($s.conclusion -ne "success" -and $s.conclusion -ne "skipped") { Write-Host ("    step {0}: {1} / {2}" -f $s.name, $s.status, $s.conclusion) } }
}
if ($r.conclusion -ne "success" -or $NoDownload) { exit 0 }

$art = (Get-Api "/actions/runs/$($r.id)/artifacts").artifacts | Where-Object { $_.name -like "KiwiVPN-Windows-*" -and -not $_.expired } | Select-Object -First 1
if (-not $art) { throw "The run has no installer artifact (or it expired)." }
$Dir = Join-Path $Out $r.head_sha.Substring(0, 7)
New-Item -ItemType Directory -Force $Dir | Out-Null
$Zip = Join-Path $Dir "artifact.zip"
Invoke-WebRequest -UseBasicParsing -Headers $H -Uri $art.archive_download_url -OutFile $Zip
Expand-Archive $Zip $Dir -Force
Remove-Item $Zip
$Exe = Get-ChildItem $Dir -Filter *-Setup.exe | Select-Object -First 1
$Want = ((Get-Content "$($Exe.FullName).sha256" -Raw).Trim() -split '\s+')[0]
$Got = (Get-FileHash $Exe.FullName -Algorithm SHA256).Hash
if ($Want -ne $Got) { throw "The download's SHA-256 doesn't match the build's. Don't use it." }
Write-Host "Downloaded and checked: $($Exe.FullName)" -ForegroundColor Green
Write-Host "SHA-256 $Got"
Get-Content "$Dir\BUILD-INFO.txt"
Write-Host "Not run here. Test it on a machine without Smart App Control (or once it's signed)."
