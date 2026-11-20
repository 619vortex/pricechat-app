# MAIN PC. Builds the signed release-candidate APK from a clean commit, checks it, and saves it with its hash and R8 mapping. Publishes nothing.
#   powershell -ExecutionPolicy Bypass -File android-client\tools\build-release.ps1
$ErrorActionPreference = "Stop"
$Project = Split-Path $PSScriptRoot -Parent
$Repo = Split-Path $Project -Parent
$T = "$env:LOCALAPPDATA\Android"
$Dir = "$env:USERPROFILE\.kiwi-android-signing"
$Store = "$Dir\kiwi-release.keystore"
$PassFile = "$Dir\kiwi-release.password.dpapi"
$Out = "C:\AI\pc-wt\kiwi-release\android"
if (-not (Test-Path $Store) -or -not (Test-Path $PassFile)) { throw "No release key. Run new-release-key.ps1 first." }
if (git -C $Repo status --short -- android-client) { throw "android-client has uncommitted changes. Commit first, so the APK matches a commit." }

$env:JAVA_HOME = (Get-ChildItem $T -Directory -Filter "jdk-21*" | Select-Object -First 1).FullName
$env:ANDROID_HOME = "$T\Sdk"
$env:Path = "$env:JAVA_HOME\bin;$T\gradle-9.6.0\bin;$env:Path"
Set-Content "$Project\local.properties" ("sdk.dir=" + "$T\Sdk".Replace("\", "/").Replace(":", "\:")) -Encoding ascii
$Commit = git -C $Repo rev-parse --short HEAD

$secure = Get-Content $PassFile | ConvertTo-SecureString
$env:KIWI_KEYSTORE_PASSWORD = [Net.NetworkCredential]::new("", $secure).Password
$env:KIWI_KEY_PASSWORD = $env:KIWI_KEYSTORE_PASSWORD
$env:KIWI_KEYSTORE = $Store
$env:KIWI_KEY_ALIAS = "kiwi-release"
try {
  Set-Location $Project
  # --no-daemon, so no long-lived process keeps the password in its environment
  & cmd /c "gradle.bat --no-daemon --console=plain -PkiwiCommit=$Commit :app:clean :app:assembleRelease > `"$Project\app\build-release.log`" 2>&1"
  $code = $LASTEXITCODE
} finally {
  foreach ($n in "KIWI_KEYSTORE_PASSWORD", "KIWI_KEY_PASSWORD", "KIWI_KEYSTORE", "KIWI_KEY_ALIAS") { Remove-Item "Env:\$n" -ErrorAction SilentlyContinue }
}
if ($code -ne 0) {
  Get-Content "$Project\app\build-release.log" | Where-Object { $_ -match '^e: |ERROR|error:|Missing class|R8|FAILED|went wrong|Execution failed' } | Select-Object -First 40 | ForEach-Object { Write-Host $_ }
  Get-Content "$Project\app\build-release.log" -Tail 15 | ForEach-Object { Write-Host $_ }
  throw "The release build failed."
}
$Built = Get-ChildItem "$Project\app\build\outputs\apk\release\*.apk" | Select-Object -First 1
if (-not $Built -or $Built.Name -match "unsigned") { throw "The build made no signed APK." }

$Bt = "$T\Sdk\build-tools\36.0.0"
$Badging = & "$Bt\aapt2.exe" dump badging $Built.FullName 2>&1
if ($Badging -match "application-debuggable") { throw "This APK is debuggable. It must not be a release." }
$Version = ([regex]"versionName='([^']+)'").Match(($Badging | Select-String "^package:").ToString()).Groups[1].Value
New-Item -ItemType Directory -Force $Out | Out-Null
$Name = "KiwiVPN-$Version-$Commit.apk"
Copy-Item $Built.FullName "$Out\$Name" -Force
$Sha = (Get-FileHash "$Out\$Name").Hash
"$Sha  $Name" | Set-Content "$Out\$Name.sha256" -Encoding ascii
"commit $(git -C $Repo rev-parse HEAD)" | Set-Content "$Out\$Name.build-info.txt" -Encoding ascii
# For reading crash reports from this build. Private: never published.
$Mapping = "$Project\app\build\outputs\mapping\release\mapping.txt"
if (Test-Path $Mapping) { Copy-Item $Mapping "$Out\$Name.mapping.txt" -Force }
Write-Host "APK: $Out\$Name"
Write-Host "SHA-256: $Sha"
