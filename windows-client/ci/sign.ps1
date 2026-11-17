# Signs ONE file with the owner's SSL.com eSigner certificate (Authenticode, SHA-256, RFC 3161 timestamp from ts.ssl.com), on a
# GitHub Actions runner only. Called for each file: by the workflow for the service and tunnel.dll, and by Tauri (bundle >
# windows > signCommand) for KiwiVPN.exe, the NSIS plugins, the uninstaller and the installer itself.
#
# The credentials come from the GitHub environment "windows-signing" (main only) as environment variables and are never written to
# disk or printed (GitHub also masks them in the log):
#   ES_USERNAME, ES_PASSWORD      the SSL.com account that owns the certificate
#   ES_CREDENTIAL_ID              which eSigner certificate (CodeSignTool get_credential_ids lists them)
#   ES_TOTP_SECRET                the eSigner automation secret (the "secret code" behind the eSigner 2FA QR code), so no
#                                 one has to type a one-time code during the build
# CODESIGNTOOL_DIR is where the workflow unpacked SSL.com's CodeSignTool (pinned version, SHA-256 checked).
#
# After signing it checks the file itself: a valid signature, timestamped, by the expected certificate. Any doubt fails the build.
param([Parameter(Mandatory = $true)][string]$Path)
$ErrorActionPreference = "Stop"

foreach ($v in "ES_USERNAME", "ES_PASSWORD", "ES_CREDENTIAL_ID", "ES_TOTP_SECRET", "CODESIGNTOOL_DIR") {
  $raw = [Environment]::GetEnvironmentVariable($v)
  if (-not $raw) { throw "sign.ps1: $v isn't set (signing runs only in the windows-signing environment)" }
  if ($raw -ne $raw.Trim()) {
    # pasted spaces or line breaks. GitHub masks the secret as stored; mask the trimmed value too, so it can't show either
    if ($v -ne "CODESIGNTOOL_DIR" -and $env:GITHUB_ACTIONS) { Write-Host "::add-mask::$($raw.Trim())" }
    [Environment]::SetEnvironmentVariable($v, $raw.Trim())
  }
}
# Never trace this script: Set-PSDebug -Trace would print the CodeSignTool arguments below, credentials included
Set-PSDebug -Off
$File = (Resolve-Path $Path).Path
# makensis hands over the uninstaller as nstXXXX.tmp (!uninstfinalize), and CodeSignTool only signs known extensions: sign an .exe
# copy, then put it back in place.
$Original = $null
if ([IO.Path]::GetExtension($File) -notin ".exe", ".dll", ".sys", ".msi", ".cab", ".cat", ".ps1") {
  $Original = $File
  $File = Join-Path ([IO.Path]::GetTempPath()) ("kiwi-sign-" + [guid]::NewGuid().ToString("N") + ".exe")
  Copy-Item $Original $File
}
$Tool = $env:CODESIGNTOOL_DIR
$Java = Join-Path $Tool "jdk-11.0.2\bin\java.exe"
$Jar = Get-ChildItem (Join-Path $Tool "jar") -Filter "code_sign_tool-*.jar" | Select-Object -First 1
if (-not (Test-Path $Java) -or -not $Jar) { throw "sign.ps1: CodeSignTool isn't unpacked in $Tool" }

$Name = if ($Original) { "uninstaller ($(Split-Path $Original -Leaf))" } else { Split-Path $File -Leaf }
$CstArgs = @("-Xmx1024M", "-jar", $Jar.FullName, "sign",
  "-username=$env:ES_USERNAME", "-password=$env:ES_PASSWORD", "-credential_id=$env:ES_CREDENTIAL_ID", "-totp_secret=$env:ES_TOTP_SECRET",
  "-input_file_path=$File", "-override=true")
$ok = $false
foreach ($try in 1..3) {
  Push-Location $Tool # CodeSignTool reads conf\code_sign_tool.properties relative to where it runs
  try {
    $out = & $Java @CstArgs 2>&1 | Out-String
    $code = $LASTEXITCODE
  } finally { Pop-Location }
  # its output is about the file, never the credentials; trimmed to the lines that matter
  $said = ($out -split "`r?`n" | Where-Object { $_ -match "(?i)sign|error|fail|success|code" } | Select-Object -First 6) -join " | "
  # belt and braces on top of GitHub's masking: should CodeSignTool ever echo an argument, the credential never reaches the log
  foreach ($s in $env:ES_USERNAME, $env:ES_PASSWORD, $env:ES_CREDENTIAL_ID, $env:ES_TOTP_SECRET) { if ($s) { $said = $said.Replace($s, "***") } }
  $sig = Get-AuthenticodeSignature $File
  if ($code -eq 0 -and $sig.Status -eq "Valid") { $ok = $true; break }
  Write-Host "sign.ps1: $Name not signed on try $try (exit $code, signature $($sig.Status)): $said"
  if ($try -lt 3) { Start-Sleep 15 }
}
if (-not $ok) { throw "sign.ps1: couldn't sign $Name" }

$cert = $sig.SignerCertificate
if (-not $sig.TimeStamperCertificate) { throw "sign.ps1: $Name has no timestamp: it would stop being valid when the certificate expires" }
if ($env:KIWI_SIGNER_THUMBPRINT -and $cert.Thumbprint -ne $env:KIWI_SIGNER_THUMBPRINT) { throw "sign.ps1: $Name was signed by $($cert.Thumbprint), not the expected certificate" }
$alg = $cert.PublicKey.Oid.FriendlyName
Write-Host ("signed {0}: {1} | {2} key | expires {3:yyyy-MM-dd} | thumbprint {4} | timestamped by {5}" -f $Name, $cert.Subject, $alg, $cert.NotAfter, $cert.Thumbprint, $sig.TimeStamperCertificate.Subject)
if ($alg -ne "RSA") { Write-Host "::warning::$Name is signed with a $alg key. Smart App Control may not accept non-RSA signatures; check it on a PC with Smart App Control before release." }
if ($Original) {
  Copy-Item $File $Original -Force
  if ((Get-FileHash $Original).Hash -ne (Get-FileHash $File).Hash) { throw "sign.ps1: couldn't put the signed uninstaller back" }
  Remove-Item $File -Force
}
if ($env:GITHUB_STEP_SUMMARY) { "- signed $Name : $($cert.Subject), $alg key, thumbprint $($cert.Thumbprint)" | Out-File $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8 }
