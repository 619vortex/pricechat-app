# Kiwi VPN for Android

Native Kotlin and Compose, using the official WireGuard library and the existing Price Chat backend. Version `0.1.0-beta1`.

**Nothing is built or run on the main PC.** It has no Android tools, and the project's rule keeps toolchains off it. The VM does all building.

## Build
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

Android Studio can open the `android-client` folder. Use it in the VM, not on the main PC.

## Tests
- **Unit tests** run in the build: `gradle :app:testDebugUnitTest`.
- **On-device tests** need a phone or emulator: `gradle :app:connectedDebugAndroidTest`.
- **`docs/DEVICE-TEST-PLAN.md`** is the real test. A passing unit test proves nothing about a real tunnel.

## Docs
`docs/API-CONTRACT.md`, `ARCHITECTURE.md`, `AUTO-CALL-FEASIBILITY.md`, `DEVICE-TEST-PLAN.md`, `GOOGLE-PLAY.md`, `SIGNING.md`, `BACKEND-CHANGES-PROPOSED.md`.
