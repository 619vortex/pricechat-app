# Deploys the fix for the app's sign-in stopping on /passport. Website only: no backend, no migration, no change to VPN access. Stops at the first problem.
#   powershell -ExecutionPolicy Bypass -File C:\AI\pc-wt\kiwi-windows\windows-client\deploy-signin-fix.ps1
# To undo it: npx vercel promote <the previous deployment it prints>, from C:\AI\price-chat\frontend
$ErrorActionPreference = "Continue"
$Repo = "C:\AI\pc-wt\kiwi-windows"
function Stop-Here($why) { Write-Host "STOPPED: $why" -ForegroundColor Red; exit 1 }

Write-Host "[1/5] The code"
if (git -C $Repo status --short) { Stop-Here "uncommitted changes in $Repo" }
Write-Host "Deploying $(git -C $Repo log --oneline -1)"

Write-Host "[2/5] The tests"
Push-Location "$Repo\frontend"
npx vitest run
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { Stop-Here "the website's tests failed" }

Write-Host "[3/5] main"
git -C C:\AI\price-chat merge --ff-only feat/kiwi-windows
if ($LASTEXITCODE -ne 0) { Stop-Here "main has moved on: it needs merging by hand" }

Write-Host "[4/5] The website. Live now, and the one to go back to:"
Push-Location C:\AI\price-chat\frontend
npx vercel ls --environment production 2>&1 | Select-String "https://" | Select-Object -First 1
Pop-Location
Push-Location "$Repo\frontend"
npx vercel deploy --prod --yes
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { Stop-Here "the deploy failed. The previous website is still live" }

Write-Host "[5/5] Checking the live site, signed out, changing nothing"
Start-Sleep 5
foreach ($p in "/", "/vpn", "/vpn/desktop", "/passport") {
  try { $r = Invoke-WebRequest -UseBasicParsing "https://pricechat.live$p" -ErrorAction Stop } catch { Stop-Here "$p isn't answering: $($_.Exception.Message)" }
  Write-Host "$p $($r.StatusCode)"
}
node "$PSScriptRoot\check-signin-live.mjs"
if ($LASTEXITCODE -ne 0) { Stop-Here "the live check failed. Roll back with the deployment shown in step 4" }
Write-Host ""
Write-Host "DONE. Now press Sign in in the Kiwi VPN app." -ForegroundColor Green
