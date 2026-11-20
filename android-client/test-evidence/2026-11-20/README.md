# Emulator evidence, 2026-11-20: App-Based VPN on Android 16

**Emulator only.** AVD `kiwi36`: `sdk_gphone64_x86_64`, Android 16 (SDK 36), build `BE2A.250530.026.F3`, WHPX on the main PC. No phone, no Kiwi server, no account and no production API were involved. These results say what Android itself does with Kiwi VPN's per-app lists; they say nothing about a real server, a phone maker's changes, or other Android versions.

## How traffic was made visible
The tests build a **real VPN interface** with the official WireGuard library (`GoTunnelDriver`, the same code the app uses), pointed at `192.0.2.1`, an address reserved for documentation that never answers. Anything routed into that tunnel goes nowhere. Android's shell (`com.android.shell`, UID 2000) stands in for "an app": the test puts it in or out of the list and has it send pings and DNS lookups. So:
- **in the VPN** means its ping gets no reply and names don't resolve (they go to the tunnel's DNS, 10.66.0.1, which is only reachable through the tunnel);
- **not in the VPN** means its ping and DNS work over the emulator's normal Wi-Fi.

The test's own process runs as Kiwi VPN's UID, so it also shows Kiwi itself staying inside the tunnel.

## Results
| Run | Where | Result |
|---|---|---|
| `connectedDirectDebugAndroidTest` | `run2-connected/junit-results.xml` | **19 passed, 0 failed, 2 skipped** (the lockdown pair, which skip unless lockdown is set) |
| `LockdownRoutingTest` with always-on + lockdown set | `run3-lockdown/am-instrument-output.txt` | **2 passed** |
| Release 0.2.0 installed over 0.1.0-rc1, then opened | `run4-release-update/` (build 186f47e, superseded) and `run7-release-665b0c9-update/` (the final build) | `install -r` Success (same key), versionCode 3 to 4, process alive after 8 s, crash buffer empty, sign-in screen shown. No sign-in |
| `ChooseAppsScreenTest` (the real onboarding screen, real apps and icons) | `run5-onboarding-ui/` | **2 passed**, 3 screenshots. The stand-in WhatsApp and Telegram here are test fixtures with those package names. Tapping an app row by injected touch failed once on the emulator right after the keyboard closed; the test clicks rows through their accessibility action |
| Final `connectedDirectDebugAndroidTest` at commit 87ea14a | `run6-final-connected/` | **21 passed, 0 failed, 2 skipped** (lockdown pair) |

### Per-app allowlisting (`run2-connected/kiwi-evidence-logcat.txt`)
- **Only Selected = [shell]**: VPN network `Uids: <{2000-2000, 10221-10221, 20221-20221}>` (shell, Kiwi, and Kiwi's SDK-sandbox UID), `DnsAddresses: [ /10.66.0.1 ]`. Shell ping 1.1.1.1: `1 packets transmitted, 0 received`. `ping dns.google`: no output (name didn't resolve). Kiwi's own TCP connect to 1.1.1.1:443: failed. **The selected app's traffic and DNS entered the tunnel.**
- **Only Selected = [Calendar]**, shell not selected: shell absent from the UID ranges; shell ping `1 received`; `PING dns.google (8.8.8.8)` resolved and answered. Kiwi's own connect still failed (Kiwi stays in the tunnel). **An unselected app kept its normal connection and normal DNS.**
- **All Apps**: shell in the tunnel (no reply). **All Except [shell]**: VPN ranges `…, 2001-99999` with 2000 left out; shell ping answered.
- **Selection change**: [shell] → [Calendar] → [shell, Calendar], each a rebuilt interface: no reply → reply → no reply, matching the UID ranges each time.
- **10 reconnects** with Only Selected = [shell]: same ranges every round; after the last disconnect no VPN network remained and the shell's normal connection was back.
- **Routes**: the VPN network carries `0.0.0.0/0` and `::/0`. A selected app's `ping6` got no reply, but the emulator's Wi-Fi has only site-local `fec0::` IPv6, so **IPv6 leak behaviour is not tested** here.
- **App list**: 21 launchable apps listed; Kiwi doesn't list itself; `com.android.shell` (no launcher icon) is not listed, so the in-app picker can't select such components.

### Foreground service, DEVICE-TEST-PLAN test 47 (`run2-connected/kiwi-evidence-logcat.txt`, "foreground_service")
Android's event log: `am_foreground_service_start: [0,live.pricechat.kiwivpn.debug/live.pricechat.kiwivpn.vpn.TunnelService,…,UNKNOWN,1024]` and `ActivityManager: Background started FGS: Allowed`. **1024 is `FOREGROUND_SERVICE_TYPE_SYSTEM_EXEMPTED`: Android 16 accepted the type** with no exception. The service then stopped itself within milliseconds, by design, because the app's connection state wasn't Connected (the test drives the tunnel directly). `ForegroundServiceTypeLoggerModule` printed "does not have any types": that logger only tracks API-gated types, and the start record above carries 1024. **Not shown:** the service staying in the foreground through a real, long connection; that needs a real server (RC-TEST-CHECKLIST B3).

### Always-on + Block connections without VPN (`run3-lockdown/`)
Set **on the emulator only** with `settings put secure always_on_vpn_app live.pricechat.kiwivpn.debug` and `always_on_vpn_lockdown 1`, then a reboot; restored afterwards (`restored-after-reboot.txt`: `null` and `0`).
- Before any tunnel: Android's `Lockdown filtering rules: UIDs: 1-10222, 10224-20222, 20224-99999`, i.e. every UID except Kiwi's (10223) and its sandbox UID. Shell ping: blocked (`before-tunnel-shell-ping.txt`).
- The library reported `alwaysOn/lockdown=(true, true)`.
- **Only Selected = [Calendar], lockdown on: the unselected shell got no reply.** Apps outside the list lose their connection under lockdown, as Google warns.
- **All Except = [shell], lockdown on: the excluded shell got no reply** either.
- So with "Block connections without VPN", "Only apps I choose" does not give other apps a normal connection: they get none. The app's copy says this.

## Not covered here
Traffic to a real Kiwi server, a real handshake, sign-in, Private DNS (the emulator's `private_dns_mode` was unset), real IPv6, work profiles, phone makers' battery and network changes, Android versions other than 16. Those are in `docs/PHONE-TEST-2026-11.md` for the owner.
