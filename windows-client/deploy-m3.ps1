# Deploys Kiwi VPN's sign-in to production: backend with migration 0102, then the website. Run it YOURSELF on this PC, from a normal PowerShell. It stops at the first problem.
#   powershell -ExecutionPolicy Bypass -File C:\AI\pc-wt\kiwi-windows\windows-client\deploy-m3.ps1
# It takes a fresh Railway volume backup first, and won't deploy without one. VPN access stays closed, and no download is published. Rollback: docs\M3-AUTH-REVIEW.md
$ErrorActionPreference = "Stop"
$Repo = "C:\AI\pc-wt\kiwi-windows"
$Backend = "$Repo\backend"
$Frontend = "$Repo\frontend"
$Live = "https://pricechat.live"
function Stop-Here($why) { Write-Host "STOPPED: $why" -ForegroundColor Red; exit 1 }

Write-Host "[1/6] The code"
$dirty = git -C $Repo status --short
if ($dirty) { Stop-Here "uncommitted changes in $Repo" }
$commit = git -C $Repo rev-parse --short HEAD
Write-Host "Deploying $commit"

Write-Host "[2/6] A fresh backup"
$py = "$PSScriptRoot\railway-backup.py"
python $py create
if ($LASTEXITCODE -ne 0) { Stop-Here "the backup wasn't confirmed" }

Write-Host "[3/6] main"
git -C C:\AI\price-chat merge --ff-only feat/kiwi-windows
if ($LASTEXITCODE -ne 0) { Stop-Here "main has moved on: it needs merging by hand" }

Write-Host "[4/6] The backend, with migration 0102"
Push-Location C:\AI\price-chat\backend
npx @railway/cli up "$Backend" --path-as-root --service api --ci
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { Stop-Here "the Railway deploy failed. Railway keeps the previous deployment running if the new one doesn't come up" }

Write-Host "[5/6] Checking the backend"
$ok = $false
for ($i = 0; $i -lt 40 -and -not $ok; $i++) {
  Start-Sleep 15
  try {
    $r = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$Live/api/members/desktop/token" -ContentType "application/json" -Body '{"code":"not-a-real-code-not-a-real-code","verifier":"not-a-real-verifier-not-a-real-verifier-xxxxx"}'
  } catch { $ok = $_.Exception.Response.StatusCode.value__ -eq 400 }
}
if (-not $ok) { Stop-Here "the new sign-in route isn't answering 400. Check Railway's logs. Do NOT press Redeploy on the old build: see the rollback plan" }
$ip = Invoke-RestMethod "$Live/api/members/vpn/ip"
$public = Invoke-RestMethod "$Live/api/members/vpn/public"
Write-Host "Your address, from Price Chat: $($ip.ip)"
Write-Host "VPN open to everyone: $($public.enabled) (expected False)"
if ($public.enabled) { Write-Host "NOTE: VPN access is open. This deploy didn't change that setting." -ForegroundColor Yellow }

Write-Host "[6/6] The website"
Push-Location $Frontend
if (-not (Test-Path .vercel)) { New-Item -ItemType Directory .vercel | Out-Null; Copy-Item C:\AI\price-chat\frontend\.vercel\project.json .vercel\ }
npx vercel deploy --prod --yes
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { Stop-Here "the website deploy failed. The backend is live and harmless without it" }
$page = Invoke-WebRequest -UseBasicParsing "$Live/vpn/desktop"
if ($page.StatusCode -ne 200) { Stop-Here "/vpn/desktop isn't answering" }
Write-Host ""
Write-Host "DONE: $commit is live." -ForegroundColor Green
Write-Host "Next: Admin > VPN > People, add your own username as a tester, so your account can connect while the VPN is closed."
