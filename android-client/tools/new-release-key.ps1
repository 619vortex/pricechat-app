# Makes the Android release signing key, once. Refuses to run if one exists: replacing it would stop every installed copy of the app from updating.
#   powershell -ExecutionPolicy Bypass -File android-client\tools\new-release-key.ps1
# The key and its password stay outside the repository, readable only by this Windows user. The password is random, is never shown, and is stored encrypted with Windows DPAPI.
$ErrorActionPreference = "Stop"
$Dir = "$env:USERPROFILE\.kiwi-android-signing"
$Store = "$Dir\kiwi-release.keystore"
$PassFile = "$Dir\kiwi-release.password.dpapi"
$Alias = "kiwi-release"
if ((Test-Path $Store) -or (Test-Path $PassFile)) { throw "A release key already exists at $Dir. Nothing was changed." }

$Jdk = (Get-ChildItem "$env:LOCALAPPDATA\Android" -Directory -Filter "jdk-21*" | Select-Object -First 1).FullName
if (-not $Jdk) { throw "No JDK under $env:LOCALAPPDATA\Android" }

New-Item -ItemType Directory -Force $Dir | Out-Null
& icacls $Dir /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Couldn't restrict $Dir" }

$bytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
$env:KIWI_NEW_STOREPASS = [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_").TrimEnd("=")
try {
  # PKCS12 uses one password for the store and the key. The password goes through the environment, so it's never on a command line.
  # keytool reports progress on stderr, which PowerShell's Stop mode would treat as a failure, so its own exit code decides.
  $ErrorActionPreference = "Continue"
  & "$Jdk\bin\keytool.exe" -genkeypair -keystore $Store -storetype PKCS12 -alias $Alias -keyalg RSA -keysize 4096 -sigalg SHA256withRSA -validity 10000 `
    -dname "CN=Kiwi VPN, O=Price Chat" -storepass:env KIWI_NEW_STOREPASS -keypass:env KIWI_NEW_STOREPASS 2>$null
  $made = $LASTEXITCODE
  $ErrorActionPreference = "Stop"
  if ($made -ne 0 -or -not (Test-Path $Store)) { throw "keytool couldn't make the key" }
  ConvertTo-SecureString $env:KIWI_NEW_STOREPASS -AsPlainText -Force | ConvertFrom-SecureString | Set-Content $PassFile -Encoding ascii
} finally {
  Remove-Item Env:\KIWI_NEW_STOREPASS -ErrorAction SilentlyContinue
}
Write-Host "Release key made: $Store"
Write-Host "BACK IT UP: see android-client\docs\SIGNING.md. If it's lost, installed copies of the app can't be updated."
