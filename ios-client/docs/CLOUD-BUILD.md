# Cloud Mac builds, with Codemagic

Windows can't compile for iPhone, so Codemagic's Macs do. `codemagic.yaml` has four workflows. None starts by itself. **Only `kiwi-testflight` uploads anything to Apple, and only with the owner's approval.**

| Workflow | Needs | Does |
|---|---|---|
| `kiwi-compile` | No Apple account | Runs the logic tests, and compiles the app, the tunnel and WireGuard unsigned |
| `kiwi-adhoc-ipa` | Paid Apple Developer account | Makes the App IDs, registers one iPhone, signs an Ad Hoc IPA, and checks it with `tools/verify-ipa.sh` |
| `kiwi-appstore-ipa` | Organization account, the app record (`KIWI_APP_STORE_APPLE_ID` in `kiwi_signing`) | App IDs, Apple Distribution certificate and App Store profiles, the next build number, an App Store-signed IPA checked in App Store mode (no device list, `beta-reports-active`, privacy manifests). **Uploads nothing** |
| `kiwi-testflight` | The same, **and the owner's approval** | The same build, then uploaded to App Store Connect: TestFlight **internal** testers only. Never external testers, never App Review |

## What goes to the cloud
A separate private repository holding `ios-client` only, with no history of the main repository and no `docs/`. `tools/sync-cloud-repo.ps1` makes it, and refuses to push signing files. Signing secrets stay in Codemagic and never go in Git.

## Keys, and where they live
| Item | Where |
|---|---|
| Key that pushes to the private repository | `%USERPROFILE%\.kiwi-ios-cloud\push_key`, with its public half as a write deploy key |
| Key Codemagic reads the repository with | `%USERPROFILE%\.kiwi-ios-cloud\read_key`, with its public half as a read-only deploy key |
| App Store Connect key (`.p8`) | Codemagic's Developer Portal integration, named `kiwi` |
| `CERTIFICATE_PRIVATE_KEY`, `KIWI_IPHONE_UDID`, `KIWI_APP_STORE_APPLE_ID` | Codemagic's `kiwi_signing` group |

## WireGuardKit
`Vendor/wireguard-apple` is the official source at `2fec12a` (tag 1.0.16-27), holding only the three WireGuardKit folders, with two small changes listed in its `KIWI-PATCHES.md`. It's in this repository because the official revision can't be built as it stands.

Go is pinned to 1.22.12, with its checksum checked. WireGuard's patch to the Go runtime was dry-run against the Go source for 1.20 through 1.25 and applies to all of them, which doesn't prove the result compiles.

## What a green build proves
**`kiwi-compile`:** it compiles and the logic tests pass. **`kiwi-adhoc-ipa`:** it's signed for that iPhone, with the tunnel entitlement. **Neither proves the VPN works.** Only `IPHONE-TEST-PLAN.md`, on a real iPhone, can show that.
