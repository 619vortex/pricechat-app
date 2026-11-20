# MAIN PC. Builds the signed release from a clean commit, checks it, and saves it with hashes, signing proof and R8 mappings. Publishes nothing, uploads nothing.
#   powershell -ExecutionPolicy Bypass -File android-client\tools\build-release.ps1
# Makes:
#   KiwiVPN-<version>-<commit>.apk        the "direct" flavor: the APK for pricechat.live (with Auto-Call VPN)
#   KiwiVPN-<version>-<commit>-play.aab   the "play" flavor: the App Bundle for Google Play (no Auto-Call VPN)
# into C:\AI\pc-wt\kiwi-release\android\<version>\. The gradle run has a hard time limit, and its output goes to a file.
param(
  [int]$TimeoutMinutes = 30,
  # The release key's certificate, as published with 0.1.0-rc1. An update installs over an installed copy only if this matches.
  [string]$ExpectedCertSha256 = "6564545155fbb8acee4b920cb46d1e13eb6616951e9001f04ebc49083a34ebb2"
)
$ErrorActionPreference = "Stop"
$Project = Split-Path $PSScriptRoot -Parent
$Repo = Split-Path $Project -Parent
$T = "$env:LOCALAPPDATA\Android"
$Dir = "$env:USERPROFILE\.kiwi-android-signing"
$Store = "$Dir\kiwi-release.keystore"
$PassFile = "$Dir\kiwi-release.password.dpapi"
$OutRoot = "C:\AI\pc-wt\kiwi-release\android"
if (-not (Test-Path $Store) -or -not (Test-Path $PassFile)) { throw "No release key. Run new-release-key.ps1 first." }
if (git -C $Repo status --short -- android-client) { throw "android-client has uncommitted changes. Commit first, so the build matches a commit." }

$env:JAVA_HOME = (Get-ChildItem $T -Directory -Filter "jdk-21*" | Select-Object -First 1).FullName
$env:ANDROID_HOME = "$T\Sdk"
$env:Path = "$env:JAVA_HOME\bin;$T\gradle-9.6.0\bin;$env:Path"
Set-Content "$Project\local.properties" ("sdk.dir=" + "$T\Sdk".Replace("\", "/").Replace(":", "\:")) -Encoding ascii
$Commit = git -C $Repo rev-parse --short HEAD
$Log = "$Project\app\build-release.log"

$secure = Get-Content $PassFile | ConvertTo-SecureString
$env:KIWI_KEYSTORE_PASSWORD = [Net.NetworkCredential]::new("", $secure).Password
$env:KIWI_KEY_PASSWORD = $env:KIWI_KEYSTORE_PASSWORD
$env:KIWI_KEYSTORE = $Store
$env:KIWI_KEY_ALIAS = "kiwi-release"
try {
  # --no-daemon, so no long-lived process keeps the password in its environment
  $gradleArgs = "/c `"gradle.bat --no-daemon --console=plain -PkiwiCommit=$Commit :app:clean :app:assembleDirectRelease :app:bundlePlayRelease > `"$Log`" 2>&1`""
  $proc = Start-Process -FilePath cmd.exe -ArgumentList $gradleArgs -WorkingDirectory $Project -NoNewWindow -PassThru
  if (-not $proc.WaitForExit($TimeoutMinutes * 60 * 1000)) {
    & taskkill /T /F /PID $proc.Id | Out-Null
    throw "The release build took longer than $TimeoutMinutes minutes and was stopped. See $Log"
  }
  $proc.WaitForExit()
  $code = $proc.ExitCode
} finally {
  foreach ($n in "KIWI_KEYSTORE_PASSWORD", "KIWI_KEY_PASSWORD", "KIWI_KEYSTORE", "KIWI_KEY_ALIAS") { Remove-Item "Env:\$n" -ErrorAction SilentlyContinue }
}
if ($code -ne 0) {
  Get-Content $Log | Where-Object { $_ -match '^e: |ERROR|error:|Missing class|R8|FAILED|went wrong|Execution failed' } | Select-Object -First 40 | ForEach-Object { Write-Host $_ }
  Get-Content $Log -Tail 15 | ForEach-Object { Write-Host $_ }
  throw "The release build failed."
}
$Apk = Get-ChildItem "$Project\app\build\outputs\apk\direct\release\*.apk" | Select-Object -First 1
$Aab = Get-ChildItem "$Project\app\build\outputs\bundle\playRelease\*.aab" | Select-Object -First 1
if (-not $Apk -or $Apk.Name -match "unsigned") { throw "The build made no signed APK." }
if (-not $Aab) { throw "The build made no App Bundle." }

$Bt = "$T\Sdk\build-tools\36.0.0"
# Native tools write warnings to stderr; Windows PowerShell 5.1 would turn those into terminating errors under "Stop".
function Invoke-Tool([string]$Exe, [string[]]$ToolArgs) {
  $saved = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try { return ((& $Exe @ToolArgs 2>&1) | ForEach-Object { "$_" }) -join "`n" } finally { $ErrorActionPreference = $saved }
}
$Badging = (Invoke-Tool "$Bt\aapt2.exe" @("dump", "badging", $Apk.FullName)) -split "`n"
if ($Badging -match "application-debuggable") { throw "This APK is debuggable. It must not be a release." }
$Version = ([regex]"versionName='([^']+)'").Match(($Badging | Select-String "^package:").ToString()).Groups[1].Value
$VersionCode = ([regex]"versionCode='([^']+)'").Match(($Badging | Select-String "^package:").ToString()).Groups[1].Value

# Signing proof, with no password needed: the APK's schemes and certificate, and the bundle's JAR signature.
$ApkVerify = Invoke-Tool "$Bt\apksigner.bat" @("verify", "--verbose", "--print-certs", $Apk.FullName)
if ($ApkVerify -notmatch "Verified using v2 scheme \(APK Signature Scheme v2\): true") { throw "The APK isn't v2-signed.`n$ApkVerify" }
if ($ApkVerify -notmatch "Verified using v3 scheme \(APK Signature Scheme v3\): true") { throw "The APK isn't v3-signed.`n$ApkVerify" }
$ApkCert = ([regex]"certificate SHA-256 digest: ([0-9a-f]{64})").Match($ApkVerify).Groups[1].Value
if ($ApkCert -ne $ExpectedCertSha256) { throw "The APK's certificate is $ApkCert, not the release key's $ExpectedCertSha256. It would not install over 0.1.0-rc1." }
$AabCertText = Invoke-Tool "$env:JAVA_HOME\bin\keytool.exe" @("-printcert", "-jarfile", $Aab.FullName)
$AabCert = ([regex]"SHA256: ([0-9A-F:]{95})").Match($AabCertText).Groups[1].Value.Replace(":", "").ToLower()
if ($AabCert -ne $ExpectedCertSha256) { throw "The bundle's certificate is '$AabCert', not $ExpectedCertSha256." }
$AabJar = Invoke-Tool "$env:JAVA_HOME\bin\jarsigner.exe" @("-verify", $Aab.FullName)
if ($AabJar -notmatch "jar verified") { throw "The bundle's signature didn't verify.`n$AabJar" }

$Out = "$OutRoot\$Version"
New-Item -ItemType Directory -Force $Out | Out-Null
$ApkName = "KiwiVPN-$Version-$Commit.apk"
$AabName = "KiwiVPN-$Version-$Commit-play.aab"
Copy-Item $Apk.FullName "$Out\$ApkName" -Force
Copy-Item $Aab.FullName "$Out\$AabName" -Force
$ApkSha = (Get-FileHash "$Out\$ApkName").Hash
$AabSha = (Get-FileHash "$Out\$AabName").Hash
"$ApkSha  $ApkName" | Set-Content "$Out\$ApkName.sha256" -Encoding ascii
"$AabSha  $AabName" | Set-Content "$Out\$AabName.sha256" -Encoding ascii
@(
  "commit $(git -C $Repo rev-parse HEAD)",
  "version $Version ($VersionCode)",
  "built $(Get-Date -Format o)",
  "",
  "APK  $ApkName",
  "SHA-256 $ApkSha",
  "certificate SHA-256 $ApkCert (expected $ExpectedCertSha256)",
  "",
  "AAB  $AabName",
  "SHA-256 $AabSha",
  "certificate SHA-256 $AabCert (expected $ExpectedCertSha256)",
  "",
  "--- apksigner verify --verbose --print-certs ---",
  $ApkVerify,
  "",
  "--- jarsigner -verify (AAB) ---",
  $AabJar
) | Set-Content "$Out\BUILD-INFO.txt" -Encoding utf8
# For reading crash reports from these builds. Private: never published.
$MapApk = "$Project\app\build\outputs\mapping\directRelease\mapping.txt"
$MapAab = "$Project\app\build\outputs\mapping\playRelease\mapping.txt"
if (Test-Path $MapApk) { Copy-Item $MapApk "$Out\$ApkName.mapping.txt" -Force }
if (Test-Path $MapAab) { Copy-Item $MapAab "$Out\$AabName.mapping.txt" -Force }
Write-Host "APK: $Out\$ApkName"
Write-Host "  SHA-256: $ApkSha"
Write-Host "AAB: $Out\$AabName"
Write-Host "  SHA-256: $AabSha"
Write-Host "Certificate SHA-256 (both): $ApkCert, matches the release key"
Write-Host "Details: $Out\BUILD-INFO.txt"
