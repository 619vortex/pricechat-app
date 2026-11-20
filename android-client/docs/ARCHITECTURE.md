# Kiwi VPN for Android: how it's built

**Stack:** Kotlin, Jetpack Compose, Material 3, coroutines and Flow. **Minimum Android 8.0 (API 26)**, target API 36, compiled against API 37. One Gradle module with packages rather than the suggested many modules: it's a small app and every extra module adds build configuration that can't be tried on the main PC. The logic is still split so it can be tested without a phone.

| Package | Job | Needs Android? |
|---|---|---|
| `model` | Backend answers; account status rules | No |
| `net` | `KiwiApi`, OkHttp, TLS only, no redirects, no logging | No |
| `auth` | PKCE, loopback listener | No |
| `security` | `Vault` (one encrypted file), `KeystoreSealer` | Sealer only |
| `vpn` | `PeerRules`, `Provisioner`, `ConnectionManager` | No |
| `vpn` | `GoTunnelDriver`, `TunnelService` | Yes |
| `autocall` | `CallClassifier`, `AutoCallEngine` | No |
| `autocall` | `CallListenerService`, `AutoCallController` | Yes |
| `ui` | Screens | Yes |

## WireGuard
`com.wireguard.android:tunnel:1.0.20260102` from Maven Central, the official library, Apache-2.0. Its POM and source were read before adding it. **Licence duty:** keep the Apache-2.0 notice with any distribution, and show it in the app before Google Play: not done yet. It bundles `wireguard-go` as native libraries for four processor types.

It starts a `VpnService` declared in its own manifest, and the app supplies only a `Tunnel` object and a config. All cryptography is in the library, including key generation with `SecureRandom`.

## Connection states
`Disconnected → Connecting → Connected → Disconnecting → Disconnected`, plus `Reconnecting` and `Failed`.
- **Connected needs a real handshake.** `GoBackend.setState(UP)` returns before any handshake, so the manager polls `latestHandshakeEpochMillis`. A new tunnel starts at 0, so it can't read an old one.
- **Limits:** 45 s for a handshake, 15 s for Android to start or stop the tunnel. Nothing waits forever.
- **One job at a time:** each command waits for the previous job to finish. A repeat of what's already happening does nothing.
- **Failure can't look idle:** a tunnel that goes down without the app asking becomes `Failed` with a reason.
- **Stale handshake:** older than 195 s becomes `Reconnecting`. The tunnel stays up, so routes still point into it.
- **HTTP sockets are dropped** each time the tunnel changes, so the next request doesn't hang on a dead one.

**A race found in the library:** its DOWN calls `stopSelf()` on its service. Starting a new tunnel straight away could land on the dying service, whose `onDestroy` would then take the new tunnel down. The driver waits 1.2 s after a stop. This is the Windows 0.1.0 class of bug, and only a real phone can show whether the wait is enough. If it isn't, the result reads as `Failed`, not as Protected.

## Changing country
The new server's details are fetched **before** the old tunnel goes. That differs from the order in the brief, for two reasons: with "Block connections without VPN" nothing could reach Price Chat once the tunnel is down, and a failed fetch leaves the person on the working connection. Then the old tunnel stops, the new one starts, and it isn't Connected until a handshake.

## What happens when the VPN stops unexpectedly
| Cause | What the app shows | Where traffic goes |
|---|---|---|
| Network lost, tunnel still up | Reconnecting | Into the tunnel, and waits or fails. Should be fail-closed: **not yet verified on a phone** |
| Another VPN starts, or the person stops it in Android | Connection failed | **Outside the VPN**, unless Block connections without VPN is on |
| App process killed | Connection failed, on next open | **Outside the VPN**, unless Block connections without VPN is on |
| Handshake never completes | Connection failed after 45 s | Outside the VPN once the tunnel is removed |

The app itself has **no kill switch**. Android's Always-on VPN with "Block connections without VPN" is the only thing that can provide one, and it's Android's feature. The app supports it (`GoBackend.setAlwaysOnCallback`, and saved details to reconnect with when nothing else can reach the internet) and hasn't been tested with it on a phone.

## Secrets
- Stored in one AES-256-GCM file with a Keystore key, in `noBackupFilesDir`, and excluded from backups and device transfers.
- The private key leaves the file only to build the config, in memory. Java strings can't be reliably wiped, so a memory dump of the running app could find it.
- `Diag` is the only log. It takes named events and short values, and removes anything shaped like a key, token, config or IP address.

## Known limits
See the delivery report and `DEVICE-TEST-PLAN.md`. Android Private DNS can send DNS to its own resolver. Diagnostics shows whether it's on, but no code here decides where that traffic goes.
