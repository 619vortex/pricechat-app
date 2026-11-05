# Builds Kiwi VPN for Android INSIDE THE VM, never on the main PC. Downloads pinned tools into C:\kiwi-android-tools, checks each one's published hash, runs the tests, and builds a debug APK.
#   powershell -ExecutionPolicy Bypass -File build-android-in-vm.ps1
# Options: -Release (also builds a release APK, signed if the KIWI_KEYSTORE variables are set: docs/SIGNING.md), -NoTests, -ReturnTo <folder>
param([switch]$Release, [switch]$NoTests, [string]$ReturnTo = "\\tsclient\C\AI\pc-wt\kiwi-release\android")
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Here = $PSScriptRoot
$Tools = "C:\kiwi-android-tools"
$Jdk = "microsoft-jdk-21.0.13.0.2-windows-x64"
$JdkSha = "c5706a9cf482ef1a0622a86eea5b13d557b07f04554572af061e2d02b58c0f99"
$Gradle = "gradle-9.6.0"
$GradleSha = "bbaeb2fef8710818cf0e261201dab964c572f92b942812df0c3620d62a529a01"
$Cmdline = "commandlinetools-win-16111833_latest.zip"
$CmdlineSha1 = "57d04f2d75eb8e8fffc5000a987e5de4b5a63e9d" # Google's repository lists SHA-1 for this package

function Fetch($Url, $File, $Hash, $Algo) {
  if (Test-Path $File) { if ((Get-FileHash $File -Algorithm $Algo).Hash -eq $Hash.ToUpper()) { return }; Remove-Item $File -Force }
  Write-Host "Downloading $(Split-Path $File -Leaf)"
  Invoke-WebRequest $Url -OutFile $File -UseBasicParsing
  $got = (Get-FileHash $File -Algorithm $Algo).Hash
  if ($got -ne $Hash.ToUpper()) { Remove-Item $File -Force; throw "$(Split-Path $File -Leaf): hash $got isn't the published $Hash. Nothing was run." }
}

New-Item -ItemType Directory -Force $Tools | Out-Null
$JdkHome = Get-ChildItem $Tools -Directory -Filter "jdk-21*" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $JdkHome) {
  Fetch "https://aka.ms/download-jdk/$Jdk.zip" "$Tools\jdk.zip" $JdkSha "SHA256"
  Expand-Archive "$Tools\jdk.zip" $Tools -Force
  $JdkHome = Get-ChildItem $Tools -Directory -Filter "jdk-21*" | Select-Object -First 1
}
if (-not (Test-Path "$Tools\$Gradle")) {
  Fetch "https://services.gradle.org/distributions/$Gradle-bin.zip" "$Tools\gradle.zip" $GradleSha "SHA256"
  Expand-Archive "$Tools\gradle.zip" $Tools -Force
}
$Sdk = "$Tools\sdk"
if (-not (Test-Path "$Sdk\cmdline-tools\latest\bin\sdkmanager.bat")) {
  Fetch "https://dl.google.com/android/repository/$Cmdline" "$Tools\cmdline.zip" $CmdlineSha1 "SHA1"
  New-Item -ItemType Directory -Force "$Sdk\cmdline-tools" | Out-Null
  Expand-Archive "$Tools\cmdline.zip" "$Sdk\cmdline-tools" -Force
  Rename-Item "$Sdk\cmdline-tools\cmdline-tools" "latest"
}

$env:JAVA_HOME = $JdkHome.FullName
$env:ANDROID_HOME = $Sdk
$env:ANDROID_SDK_ROOT = $Sdk
$env:Path = "$($JdkHome.FullName)\bin;$Tools\$Gradle\bin;$env:Path"

# Google's SDK licences have to be accepted to download its packages. This accepts them for this VM's copy of the SDK only.
$yes = (1..40 | ForEach-Object { "y" }) -join "`n"
$yes | & "$Sdk\cmdline-tools\latest\bin\sdkmanager.bat" --licenses | Out-Null
& "$Sdk\cmdline-tools\latest\bin\sdkmanager.bat" "platforms;android-37.0" "build-tools;36.0.0" "platform-tools" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "sdkmanager failed" }

$Commit = "unknown"
if (Test-Path "$Here\BUILD-INFO.txt") { $Commit = ((Get-Content "$Here\BUILD-INFO.txt" -TotalCount 1) -split "\s+")[1] }

Set-Location $Here
if (-not $NoTests) {
  Write-Host "=== Unit tests" -ForegroundColor Cyan
  & gradle.bat --no-daemon "-PkiwiCommit=$Commit" :app:testDebugUnitTest
  $TestExit = $LASTEXITCODE
  $Reports = Get-ChildItem "$Here\app\build\test-results\testDebugUnitTest\*.xml" -ErrorAction SilentlyContinue
  $Total = 0; $Bad = 0
  foreach ($r in $Reports) { $x = [xml](Get-Content $r.FullName); $Total += [int]$x.testsuite.tests; $Bad += [int]$x.testsuite.failures + [int]$x.testsuite.errors }
  Write-Host "Unit tests: $Total run, $Bad failed"
  if ($TestExit -ne 0) { throw "Tests failed or didn't compile. Send this output. No APK was built." }
}

Write-Host "=== Debug APK" -ForegroundColor Cyan
& gradle.bat --no-daemon "-PkiwiCommit=$Commit" :app:assembleDebug
if ($LASTEXITCODE -ne 0) { throw "The debug build failed. Send this output." }
$Out = @("$Here\app\build\outputs\apk\debug\app-debug.apk")

if ($Release) {
  Write-Host "=== Release APK" -ForegroundColor Cyan
  & gradle.bat --no-daemon "-PkiwiCommit=$Commit" :app:assembleRelease
  if ($LASTEXITCODE -ne 0) { throw "The release build failed. Send this output." }
  $Out += (Get-ChildItem "$Here\app\build\outputs\apk\release\*.apk" | Select-Object -First 1).FullName
}

$Dist = "$Here\dist-apk"
New-Item -ItemType Directory -Force $Dist | Out-Null
$Names = @()
foreach ($a in $Out) {
  $name = "KiwiVPN-" + (Split-Path $a -Leaf)
  Copy-Item $a "$Dist\$name" -Force
  $sha = (Get-FileHash "$Dist\$name" -Algorithm SHA256).Hash
  "$sha  $name" | Set-Content "$Dist\$name.sha256" -Encoding ascii
  Write-Host "$name  SHA-256 $sha" -ForegroundColor Green
  $Names += $name, "$name.sha256"
}
if (Test-Path "$Here\BUILD-INFO.txt") { Copy-Item "$Here\BUILD-INFO.txt" $Dist -Force; $Names += "BUILD-INFO.txt" }
if ($ReturnTo) {
  try {
    New-Item -ItemType Directory -Force $ReturnTo -ErrorAction Stop | Out-Null
    foreach ($n in $Names) { Copy-Item "$Dist\$n" "$ReturnTo\$n" -Force -ErrorAction Stop }
    Write-Host "Copied to the main PC: $ReturnTo" -ForegroundColor Green
  } catch { Write-Host "Couldn't copy to $ReturnTo ($_). Copy the files from $Dist by hand." -ForegroundColor Yellow }
}
