# GitHub Actions only, in the windows-signing environment. Asks SSL.com (CodeSignTool, pinned) about the eSigner certificate
# WITHOUT signing anything: which credential IDs the account has, and the certificate behind ES_CREDENTIAL_ID. Then checks
# it can do Windows Authenticode code signing as Kiwi needs: code signing purpose, RSA key, valid now, chained to a root this
# Windows runner trusts, and (if KIWI_SIGNER_THUMBPRINT is set) the expected certificate. Credentials are never printed.
$ErrorActionPreference = "Stop"
$fail = 0
function Say($ok, $what) { if ($ok) { Write-Host "PASS $what" } else { Write-Host "FAIL $what"; $script:fail++ } ; "- $(if ($ok) { 'PASS' } else { 'FAIL' }) $what" | Out-File $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8 }
"### eSigner certificate check (nothing signed)`n" | Out-File $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8

$missing = @("ES_USERNAME", "ES_PASSWORD", "ES_CREDENTIAL_ID", "ES_TOTP_SECRET") | Where-Object { -not [Environment]::GetEnvironmentVariable($_) }
Say ($missing.Count -eq 0) "secrets present in windows-signing$(if ($missing) { ': missing ' + ($missing -join ', ') })"
if ($missing.Count -eq 4) { throw "No eSigner secrets yet: add them to the windows-signing environment (windows-client/docs/SIGNING.md)" }

$Zip = "$env:RUNNER_TEMP\codesigntool.zip"
Invoke-WebRequest -UseBasicParsing -Uri $env:CODESIGNTOOL_URL -OutFile $Zip
if ((Get-FileHash $Zip -Algorithm SHA256).Hash -ne $env:CODESIGNTOOL_SHA256) { throw "CodeSignTool isn't the pinned file" }
$Tool = "$env:RUNNER_TEMP\codesigntool"
Expand-Archive $Zip $Tool -Force
$Java = Join-Path $Tool "jdk-11.0.2\bin\java.exe"
$Jar = (Get-ChildItem (Join-Path $Tool "jar") -Filter "code_sign_tool-*.jar" | Select-Object -First 1).FullName

function Cst([string[]]$a) {
  Push-Location $Tool
  try { $out = & $Java -Xmx512M -jar $Jar @a 2>&1 | Out-String } finally { Pop-Location }
  foreach ($v in $env:ES_USERNAME, $env:ES_PASSWORD, $env:ES_TOTP_SECRET) { if ($v) { $out = $out.Replace($v, "***") } }
  return $out
}

$ids = Cst @("get_credential_ids", "-username=$env:ES_USERNAME", "-password=$env:ES_PASSWORD")
Write-Host "Credential IDs on the account:`n$ids"
Say ($ids -match [regex]::Escape($env:ES_CREDENTIAL_ID)) "ES_CREDENTIAL_ID is one of the account's eSigner credentials"

$info = Cst @("credential_info", "-username=$env:ES_USERNAME", "-password=$env:ES_PASSWORD", "-credential_id=$env:ES_CREDENTIAL_ID")
$shown = ($info -split "`r?`n" | Where-Object { $_ -notmatch "^[A-Za-z0-9+/=]{60,}$" }) -join "`n"
Write-Host "Credential info:`n$shown"

# the certificate: a PEM block, or a long base64 DER line, in what SSL.com returned
$b64 = $null
if ($info -match "(?s)-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----") { $b64 = ($Matches[1] -replace "\s", "") }
elseif ($info -match "(MII[A-Za-z0-9+/=]{500,})") { $b64 = $Matches[1] }
if (-not $b64) { Say $false "certificate found in SSL.com's answer (see the output above)"; exit 1 }
$cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 (, [Convert]::FromBase64String($b64))
$alg = $cert.PublicKey.Oid.FriendlyName
$bits = if ($alg -eq "RSA") { $cert.PublicKey.Key.KeySize } else { "?" }
Write-Host "subject    $($cert.Subject)"
Write-Host "issuer     $($cert.Issuer)"
Write-Host "valid      $($cert.NotBefore.ToString('yyyy-MM-dd')) to $($cert.NotAfter.ToString('yyyy-MM-dd'))"
Write-Host "thumbprint $($cert.Thumbprint)"
"`nSubject ``$($cert.Subject)```nIssuer ``$($cert.Issuer)```nValid $($cert.NotBefore.ToString('yyyy-MM-dd')) to $($cert.NotAfter.ToString('yyyy-MM-dd')), thumbprint ``$($cert.Thumbprint)```n" | Out-File $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8

$eku = @($cert.EnhancedKeyUsageList | ForEach-Object { $_.ObjectId })
Say ($eku -contains "1.3.6.1.5.5.7.3.3") "for code signing (Authenticode EKU 1.3.6.1.5.5.7.3.3)"
Say ($alg -eq "RSA") "RSA key ($alg, $bits bits): what Smart App Control is known to accept"
$now = Get-Date
Say ($cert.NotBefore -le $now -and $cert.NotAfter -gt $now.AddDays(30)) "valid now and for more than 30 more days"
$chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
$chain.ChainPolicy.RevocationMode = "Online"
$chain.ChainPolicy.ApplicationPolicy.Add((New-Object System.Security.Cryptography.Oid "1.3.6.1.5.5.7.3.3")) | Out-Null
$built = $chain.Build($cert)
$path = ($chain.ChainElements | ForEach-Object { $_.Certificate.GetNameInfo("SimpleName", $false) }) -join " > "
$why = ($chain.ChainStatus | ForEach-Object { $_.StatusInformation.Trim() }) -join "; "
Say $built "chain to a root Windows trusts, not revoked: $path$(if ($why) { ' (' + $why + ')' })"
if ($env:KIWI_SIGNER_THUMBPRINT) { Say ($cert.Thumbprint -eq $env:KIWI_SIGNER_THUMBPRINT) "the expected certificate (KIWI_SIGNER_THUMBPRINT)" }
else { "`nTo pin this certificate, set the windows-signing variable KIWI_SIGNER_THUMBPRINT to ``$($cert.Thumbprint)``." | Out-File $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8 }

if ($fail) { throw "$fail certificate check(s) failed" }
Write-Host "Certificate ready for Kiwi's Authenticode signing. Nothing was signed."
