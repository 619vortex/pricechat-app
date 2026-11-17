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

# The shape of each secret, never its value: pasted spaces or line breaks, an email where SSL.com wants the username, a
# credential ID or 2FA secret in the wrong format. Surrounding whitespace is removed before use (here and in sign.ps1).
foreach ($v in "ES_USERNAME", "ES_PASSWORD", "ES_CREDENTIAL_ID", "ES_TOTP_SECRET") {
  $raw = [Environment]::GetEnvironmentVariable($v)
  if ($raw -and $raw -ne $raw.Trim()) { Write-Host "::add-mask::$($raw.Trim())"; Write-Host "NOTE $v had spaces or a line break around it (removed)"; [Environment]::SetEnvironmentVariable($v, $raw.Trim()) }
}
$u = $env:ES_USERNAME; $p = $env:ES_PASSWORD; $c = $env:ES_CREDENTIAL_ID; $t = $env:ES_TOTP_SECRET
Say ($u -notmatch "@") "ES_USERNAME is an SSL.com username, not an email address$(if ($u -match '@') { ': it contains @. eSigner signs in with the account USERNAME (SSL.com > Account > Username), not the email' })"
Say ($u -notmatch "\s") "ES_USERNAME has no spaces inside"
Say ($p -notmatch '["\r\n]') "ES_PASSWORD has no quote marks or line breaks (they can't be passed to CodeSignTool safely)"
Say ($c -match "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$") "ES_CREDENTIAL_ID looks like an eSigner credential ID (8-4-4-4-12 hex)"
Say ($t -match "^[A-Za-z0-9+/=]{16,}$") "ES_TOTP_SECRET looks like the eSigner 2FA secret code (no spaces, 16+ characters)"

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
if ($ids -match "authorization grant is invalid") {
  Say $false "SSL.com accepted the username and password (it refused them: wrong username, wrong password, or an email in ES_USERNAME)"
  throw "SSL.com refused the eSigner sign-in; nothing else can be checked until ES_USERNAME / ES_PASSWORD are right"
}
Say ($ids -match [regex]::Escape($env:ES_CREDENTIAL_ID)) "ES_CREDENTIAL_ID is one of the account's eSigner credentials"

$info = Cst @("credential_info", "-username=$env:ES_USERNAME", "-password=$env:ES_PASSWORD", "-credential_id=$env:ES_CREDENTIAL_ID")
$shown = ($info -split "`r?`n" | Where-Object { $_ -notmatch "^[A-Za-z0-9+/=]{60,}$" }) -join "`n"
Write-Host "Credential info:`n$shown"

# The certificate itself: CodeSignTool only prints a summary, so ask SSL.com's eSigner (CSC) API directly, the same sign-in
# CodeSignTool uses (its own client ID, from its settings file). credentials/info only reads; it signs nothing.
$props = @{}
Get-Content (Join-Path $Tool "conf\code_sign_tool.properties") | ForEach-Object { if ($_ -match "^\s*([A-Z0-9_]+)\s*=\s*(.+?)\s*$") { $props[$Matches[1]] = $Matches[2] } }
$tok = Invoke-RestMethod -Method Post -Uri $props["OAUTH2_ENDPOINT"] -ContentType "application/json" -Body (@{
    client_id = $props["CLIENT_ID"]; grant_type = "password"; username = $env:ES_USERNAME; password = $env:ES_PASSWORD } | ConvertTo-Json)
Write-Host "::add-mask::$($tok.access_token)"
$csc = Invoke-RestMethod -Method Post -Uri "$($props['CSC_API_ENDPOINT'])/csc/v0/credentials/info" -ContentType "application/json" `
  -Headers @{ Authorization = "Bearer $($tok.access_token)" } -Body (@{ credentialID = $env:ES_CREDENTIAL_ID; certificates = "chain" } | ConvertTo-Json)
$pems = @($csc.cert.certificates)
Say ($pems.Count -ge 1) "certificate downloaded from SSL.com's eSigner API ($($pems.Count) in the chain it sent)"
if ($pems.Count -lt 1) { exit 1 }
function ToCert($s) { New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 (, [Convert]::FromBase64String(($s -replace "-----[A-Z ]+-----", "" -replace "\s", ""))) }
$cert = ToCert $pems[0]
$extra = @($pems | Select-Object -Skip 1 | ForEach-Object { ToCert $_ })
$tsa = $props["TSA_URL"]
try { $t = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $tsa -ContentType "application/timestamp-query" -Body ([byte[]]@(0x30)) -TimeoutSec 20; $tsOk = $true }
catch { $tsOk = $null -ne $_.Exception.Response } # any HTTP answer to a junk request means the timestamp server is reachable
Say $tsOk "timestamp server $tsa reachable (RFC 3161; every signature gets a timestamp so it outlives the certificate)"
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
foreach ($x in $extra) { $chain.ChainPolicy.ExtraStore.Add($x) | Out-Null }
$built = $chain.Build($cert)
$path = ($chain.ChainElements | ForEach-Object { $_.Certificate.GetNameInfo("SimpleName", $false) }) -join " > "
$why = ($chain.ChainStatus | ForEach-Object { $_.StatusInformation.Trim() }) -join "; "
Say $built "chain to a root Windows trusts, not revoked: $path$(if ($why) { ' (' + $why + ')' })"
if ($env:KIWI_SIGNER_THUMBPRINT) { Say ($cert.Thumbprint -eq $env:KIWI_SIGNER_THUMBPRINT) "the expected certificate (KIWI_SIGNER_THUMBPRINT)" }
else { "`nTo pin this certificate, set the windows-signing variable KIWI_SIGNER_THUMBPRINT to ``$($cert.Thumbprint)``." | Out-File $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8 }

if ($fail) { throw "$fail certificate check(s) failed" }
Write-Host "Certificate ready for Kiwi's Authenticode signing. Nothing was signed."
