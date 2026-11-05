# Signing Android builds

**This isn't the Windows certificate.** SSL.com and similar certificates sign Windows programs. Android uses its own key, which costs nothing.

**If this key is lost, installed copies of the app can't be updated.** People would have to uninstall and reinstall. Google Play's App Signing protects against this only once the app is enrolled there.

## Where it is
| File | What it is |
|---|---|
| `%USERPROFILE%\.kiwi-android-signing\kiwi-release.keystore` | The private key. PKCS12, RSA 4096, alias `kiwi-release`, valid for 10,000 days |
| `%USERPROFILE%\.kiwi-android-signing\kiwi-release.password.dpapi` | Its random password, encrypted by Windows so that only this Windows user on this PC can read it |

The folder is restricted to this Windows user. Neither file is in the repository, and `.gitignore` also blocks `*.jks`, `*.keystore`, `*.p12` and `keystore.properties`.

## Back it up: do this now
**The password file only works on this PC, under this Windows user. If this PC dies, you need the password itself, as well as the keystore.**

1. Copy `kiwi-release.keystore` to two offline places, such as encrypted USB drives kept apart.
2. In your own PowerShell window, show the password once and store it in a password manager:
   ```powershell
   [Net.NetworkCredential]::new("", (Get-Content "$env:USERPROFILE\.kiwi-android-signing\kiwi-release.password.dpapi" | ConvertTo-SecureString)).Password
   ```
   Then clear the terminal. Don't paste it into a chat, email, ticket or repository. Claude doesn't need it, and never reads it.

Keep the keystore and the password in different places.

## Building
```powershell
powershell -ExecutionPolicy Bypass -File android-client\tools\build-release.ps1
```
It refuses to run with uncommitted changes, and refuses to keep a debuggable APK. It decrypts the password into its own process only, and saves the APK, its hash, the commit it came from, and the R8 mapping to `C:\AI\pc-wt\kiwi-release\android`.

**The mapping file is private.** It's needed to read crash reports from that exact build. Don't publish it.

`tools\new-release-key.ps1` made the key, and refuses to run if one exists.

## Showing the fingerprint
```powershell
& "$env:LOCALAPPDATA\Android\Sdk\build-tools\36.0.0\apksigner.bat" verify --print-certs <the apk>
```
This needs no password. Publish the fingerprint with the download, so people can check an update comes from the same key.

## Debug builds
Signed automatically with a key Gradle made on this PC, and installed under a separate package, `live.pricechat.kiwivpn.debug`. Never distribute them.

## Google Play, later
Enrol in Play App Signing. Google then holds the signing key, and this key becomes the upload key, which Google can replace if it's lost. App Links will need the fingerprint Play shows.
