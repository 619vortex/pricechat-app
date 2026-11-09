# MAIN PC, read-only. Checks a signed Kiwi VPN installer before you run it, and the installed files after. Changes nothing,
# turns nothing off, installs nothing: you run the installer yourself (double-click), so Smart App Control judges it as it
# would for any user.
#   powershell -ExecutionPolicy Bypass -File windows-client\ci\main-pc-check.ps1 -Installer <path to *-signed-Setup.exe>
#   powershell -ExecutionPolicy Bypass -File windows-client\ci\main-pc-check.ps1 -Installed     (after installing)
param([string]$Installer = "", [switch]$Installed)
$ErrorActionPreference = "Stop"
$bad = 0
function Say($ok, $what) { if ($ok) { Write-Host "PASS $what" -ForegroundColor Green } else { Write-Host "FAIL $what" -ForegroundColor Red; $script:bad++ } }

function Check-File($path, [switch]$Quiet) {
  $s = Get-AuthenticodeSignature $path
  $c = $s.SignerCertificate
  $name = Split-Path $path -Leaf
  if (-not $c) { Say $false "$name has no signature"; return }
  $chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
  $chain.ChainPolicy.RevocationMode = "Online"
  $built = $chain.Build($c)
  $root = $chain.ChainElements[$chain.ChainElements.Count - 1].Certificate
  Say ($s.Status -eq "Valid") "$name signature: $($s.Status) ($($s.StatusMessage))"
  Say ([bool]$s.TimeStamperCertificate) "$name timestamped$(if ($s.TimeStamperCertificate) { ' by ' + $s.TimeStamperCertificate.Subject })"
  if (-not $Quiet) {
    Say $built ("certificate chain to a trusted root: {0}" -f (($chain.ChainElements | ForEach-Object { $_.Certificate.GetNameInfo("SimpleName", $false) }) -join " > "))
    Write-Host "     signer  $($c.Subject)"
    Write-Host "     key     $($c.PublicKey.Oid.FriendlyName), valid $($c.NotBefore.ToString('yyyy-MM-dd')) to $($c.NotAfter.ToString('yyyy-MM-dd')), thumbprint $($c.Thumbprint)"
    Write-Host "     root    $($root.Subject)"
    Say ($c.EnhancedKeyUsageList.ObjectId -contains "1.3.6.1.5.5.7.3.3") "certificate is for code signing"
    Say ($c.PublicKey.Oid.FriendlyName -eq "RSA") "RSA key (what Smart App Control is known to accept)"
  }
}

# Smart App Control: 0 off, 1 on (enforcing), 2 evaluation
$sac = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy" -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState
$sacText = switch ($sac) { 0 { "off" } 1 { "ON (enforcing)" } 2 { "evaluation" } default { "unknown" } }
Write-Host "Smart App Control: $sacText (this script never changes it)"

if ($Installer) {
  $exe = (Resolve-Path $Installer).Path
  $shaFile = "$exe.sha256"
  $got = (Get-FileHash $exe -Algorithm SHA256).Hash
  Write-Host "SHA-256 $got"
  if (Test-Path $shaFile) { Say ($got -eq ((Get-Content $shaFile -Raw).Trim() -split '\s+')[0]) "SHA-256 matches the build's" }
  Check-File $exe
}

if ($Installed) {
  $dir = "C:\Program Files\KiwiVPN"
  Say (Test-Path $dir) "installed in $dir"
  foreach ($f in Get-ChildItem $dir -Recurse -Include *.exe, *.dll) { Check-File $f.FullName -Quiet }
  $svc = Get-Service KiwiVPN -ErrorAction SilentlyContinue
  Say ($svc -and $svc.Status -eq "Running") "Kiwi VPN service running ($($svc.Status))"
  Write-Host "     KiwiVPN.exe version $((Get-Item "$dir\KiwiVPN.exe").VersionInfo.ProductVersion)"
  # Smart App Control / Code Integrity blocks in the last 2 hours that mention Kiwi
  $since = (Get-Date).AddHours(-2)
  $blocks = @(Get-WinEvent -FilterHashtable @{ LogName = "Microsoft-Windows-CodeIntegrity/Operational"; StartTime = $since } -ErrorAction SilentlyContinue | Where-Object { $_.Id -in 3033, 3034, 3076, 3077, 3089 -and $_.Message -match "(?i)kiwi|tunnel\.dll|wireguard" })
  Say ($blocks.Count -eq 0) "no Smart App Control / Code Integrity blocks of Kiwi files in the last 2 hours ($($blocks.Count) found)"
  $blocks | Select-Object -First 5 | ForEach-Object { Write-Host "     $($_.TimeCreated) event $($_.Id): $(($_.Message -split "`n")[0])" }
}
if (-not $Installer -and -not $Installed) { Write-Host "Give -Installer <file> and/or -Installed" }
Write-Host ""
if ($bad) { Write-Host "$bad check(s) failed" -ForegroundColor Red; exit 1 } else { Write-Host "All checks passed" -ForegroundColor Green }
