# Signing Android builds

**This isn't the Windows certificate.** SSL.com and similar certificates sign Windows programs. Android uses its own key, which you make yourself and which costs nothing. **If the key is lost, an installed app can't be updated** unless Play App Signing holds the real signing key.

## Debug builds
Signed automatically with a key Gradle makes in that VM. Fine for testing, not for distribution.

## Making the release key
In the VM, or better on a machine you trust, using `keytool` from the JDK:
```powershell
& "C:\kiwi-android-tools\jdk-21*\bin\keytool.exe" -genkeypair -v -keystore C:\kiwi-secrets\kiwi-release.jks -alias kiwi -keyalg RSA -keysize 4096 -validity 10000
```
Use a strong password, and choose it yourself: don't send it to me.

**Never commit it.** `.gitignore` blocks `*.jks`, `*.keystore`, `*.p12` and `keystore.properties`. Keep two offline backups of the file and the password, apart from each other.

## Building a release APK
Set these only for the session that builds. The build reads them from the environment and stores them nowhere:
```powershell
$env:KIWI_KEYSTORE = "C:\kiwi-secrets\kiwi-release.jks"
$env:KIWI_KEYSTORE_PASSWORD = Read-Host "Store password"
$env:KIWI_KEY_ALIAS = "kiwi"
$env:KIWI_KEY_PASSWORD = Read-Host "Key password"
powershell -ExecutionPolicy Bypass -File build-android-in-vm.ps1 -Release
```
With no variables set, the release APK comes out unsigned and won't install.

Then record the fingerprint, which App Links will need:
```powershell
& "C:\kiwi-android-tools\jdk-21*\bin\keytool.exe" -list -v -keystore C:\kiwi-secrets\kiwi-release.jks -alias kiwi
```

## Google Play
Enrol in Play App Signing. The key above then becomes the upload key, which Google can replace if it's lost.
