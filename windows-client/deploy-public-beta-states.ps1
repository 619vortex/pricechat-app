# Deploys the public beta's account states: no running trial shown while the VPN is closed, and a clear unavailable message. Backend and website. No migration. It does NOT open the VPN: that switch stays yours.
#   powershell -ExecutionPolicy Bypass -File C:\AI\pc-wt\kiwi-windows\windows-client\deploy-public-beta-states.ps1
# To undo it: the newest backend needs no migration rollback, so Railway's Redeploy on the deployment before this one is safe (it has migration 0102). Website: npx vercel promote <the one it prints>
$ErrorActionPreference = "Continue"
$Repo = "C:\AI\pc-wt\kiwi-windows"
$Live = "https://pricechat.live"
function Stop-Here($why) { Write-Host "STOPPED: $why" -ForegroundColor Red; exit 1 }

Write-Host "[1/6] The code"
if (git -C $Repo status --short) { Stop-Here "uncommitted changes in $Repo" }
Write-Host "Deploying $(git -C $Repo log --oneline -1)"
$changed = git -C $Repo diff --name-only e3b763f HEAD -- backend/migrations
if ($changed) { Stop-Here "this deploy shouldn't include a migration: $changed" }

Write-Host "[2/6] A fresh backup"
python "$PSScriptRoot\railway-backup.py" create
if ($LASTEXITCODE -ne 0) { Stop-Here "the backup wasn't confirmed" }

Write-Host "[3/6] main"
git -C C:\AI\price-chat merge --ff-only feat/kiwi-windows
if ($LASTEXITCODE -ne 0) { Stop-Here "main has moved on: it needs merging by hand" }

Write-Host "[4/6] The backend"
Push-Location C:\AI\price-chat\backend
npx @railway/cli up "$Repo\backend" --path-as-root --service api --ci
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { Stop-Here "the Railway deploy failed. Railway keeps the previous deployment running if the new one doesn't come up" }

Write-Host "[5/6] Checking the backend"
$ok = $false
for ($i = 0; $i -lt 8 -and -not $ok; $i++) {
  try { Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$Live/api/members/vpn/card" | Out-Null }
  catch {
    $body = ""
    try { $body = (New-Object IO.StreamReader($_.Exception.Response.GetResponseStream())).ReadToEnd() } catch {}
    # the new wording while closed; signed out with the VPN open answers 401 or 403 from the payment checks instead
    if ($body -match "unavailable right now" -or $_.Exception.Response.StatusCode.value__ -eq 401) { $ok = $true }
  }
  if (-not $ok) { Start-Sleep 15 }
}
if (-not $ok) { Stop-Here "the new backend isn't answering yet. Check Railway's logs" }
try { Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$Live/api/members/desktop/token" -ContentType "application/json" -Body '{"code":"not-a-real-code-not-a-real-code","verifier":"not-a-real-verifier-not-a-real-verifier-xxxxx"}' | Out-Null; Stop-Here "a fake sign-in code was accepted" }
catch { if ($_.Exception.Response.StatusCode.value__ -ne 400) { Stop-Here "the sign-in route answered $($_.Exception.Response.StatusCode.value__), not 400" } }
Write-Host "Sign-in route refuses a fake code"
$public = Invoke-RestMethod "$Live/api/members/vpn/public"
Write-Host "VPN open to everyone: $($public.enabled) (this deploy doesn't change it)"

Write-Host "[6/6] The website. Live now, and the one to go back to:"
Push-Location C:\AI\price-chat\frontend
npx vercel ls --environment production 2>&1 | Select-String "https://" | Select-Object -First 1
Pop-Location
Push-Location "$Repo\frontend"
npx vercel deploy --prod --yes
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { Stop-Here "the website deploy failed. The backend is live and works with the previous website" }
Start-Sleep 5
foreach ($p in "/", "/vpn", "/vpn/desktop", "/passport") {
  try { $r = Invoke-WebRequest -UseBasicParsing "$Live$p" -ErrorAction Stop } catch { Stop-Here "$p isn't answering: $($_.Exception.Message)" }
  Write-Host "$p $($r.StatusCode)"
}
Write-Host ""
Write-Host "DONE. The VPN's public switch wasn't touched." -ForegroundColor Green
