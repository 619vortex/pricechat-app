# Kiwi VPN for Android

Native Kotlin and Compose, using the official WireGuard library and the existing Price Chat backend. Version `0.1.0-beta2`.

**Android is built on the main PC,** as the owner decided on 2026-11-05. The tools are under `%LOCALAPPDATA%\Android`: `Sdk`, `gradle-9.6.0` and `jdk-21*`. Android Studio is at `C:\Program Files\Android\Android Studio`. This applies to Android only: the rule against Rust toolchains on the main PC still stands.

## Build on the main PC
From `android-client`, with `local.properties` holding `sdk.dir` and `JAVA_HOME` set to the JDK above:
```powershell
& "$env:LOCALAPPDATA\Android\gradle-9.6.0\bin\gradle.bat" "-PkiwiCommit=$(git rev-parse --short HEAD)" :app:testDebugUnitTest :app:assembleDebug
```
The APK is at `app\build\outputs\apk\debug\app-debug.apk`. Or open this folder in Android Studio.

## Build in the VM (the earlier route, no longer the default)
1. **Main PC:** commit, then run:
   ```powershell
   powershell -ExecutionPolicy Bypass -File android-client\prepare-vm-build.ps1
   ```
2. **VM,** in PowerShell (no administrator needed), with the path the first step printed:
   ```powershell
   Expand-Archive \\tsclient\C\AI\pc-wt\kiwi-android-build-XXXXXXX.zip C:\kiwi-android -Force
   cd C:\kiwi-android
   powershell -ExecutionPolicy Bypass -File build-android-in-vm.ps1
   ```
   The first run downloads a JDK, Gradle and the Android SDK into `C:\kiwi-android-tools`. It needs about 6 GB and agrees to Google's SDK licences for that copy. Each direct download has its published hash checked. It runs the unit tests, and stops if they fail. Then it builds the debug APK, prints its SHA-256, and copies the result to `C:\AI\pc-wt\kiwi-release\android`.

Gradle and the JDK are pinned. The dependencies are pinned by version in `app/build.gradle.kts`, but they aren't verified by hash yet: add Gradle dependency verification before any release.

Android Studio can open the `android-client` folder.

## Tests
- **Unit tests** run in the build: `gradle :app:testDebugUnitTest`.
- **On-device tests** need a phone or emulator: `gradle :app:connectedDebugAndroidTest`.
- **`docs/DEVICE-TEST-PLAN.md`** is the real test. A passing unit test proves nothing about a real tunnel.

## Docs
`docs/API-CONTRACT.md`, `ARCHITECTURE.md`, `APP-ROUTING.md`, `AUTO-CALL-FEASIBILITY.md`, `DEVICE-TEST-PLAN.md`, `GOOGLE-PLAY.md`, `SIGNING.md`, `BACKEND-CHANGES-PROPOSED.md`.
