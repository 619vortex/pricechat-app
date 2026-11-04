# Kiwi VPN: morning report

**The Install → Sign in → Choose country → Connect flow is built, but hasn't run end to end.** Two things stand in the way, both yours: one production deploy, which the auto-mode safety check stopped me running, and one VM build. No production change was made overnight beyond the corrected Windows card text you approved.

## Do this, in order
**1. Deploy the backend and website.** Paste this at the Claude Code prompt, or run it in a normal PowerShell on this PC:
```
! powershell -ExecutionPolicy Bypass -File C:\AI\pc-wt\kiwi-windows\windows-client\deploy-m3.ps1
```
It takes a fresh Railway backup and confirms it first, deploys backend then website, and checks them. VPN access stays closed. **Never press Redeploy on the old Railway build afterwards:** rollback is in `docs/M3-AUTH-REVIEW.md`.

**2. Make yourself a tester.** Admin > VPN > People: add your Price Chat username. Without this, your account can't connect while the VPN is closed.

**3. Build the installer in the VM.** Transfer **`C:\AI\pc-wt\kiwi-app-m4.zip`**, unzip to `C:\kiwi-m4`, then in an **administrator** PowerShell:
```
cd C:\kiwi-m4
powershell -ExecutionPolicy Bypass -File build-app-in-vm.ps1
```
This is the first time the new Rust window code compiles. It may need a fix: send me any error.

**4. Run `C:\kiwi-m4\KiwiVPN-Setup.exe`** from File Explorer, then go through `docs/VM-CHECKLIST.md`. The essentials: sign in, choose Germany, Connect, check the IP, restart Windows.

**5. Run the security tests** in a **normal** PowerShell. I still have no results for these from any build:
```
cd C:\kiwi-m4
powershell -ExecutionPolicy Bypass -File ipc-and-key-test.ps1
powershell -ExecutionPolicy Bypass -File fake-service-test.ps1
```
Send me the output of steps 3 to 5.

## What's built
| Area | Built | Tested here | Verified in the VM |
|---|---|---|---|
| Browser sign-in: one-time code, PKCE, loopback callback with state check | Yes | Backend, client and the real listener | No |
| VPN-only session, recorded as desktop, revocable, weekly rotation | Yes | Backend and client | No |
| Session and keys encrypted with DPAPI | Yes | Real DPAPI, including a tampered file | No |
| Private key made on the PC, public half registered, device reused | Yes | Yes, with the service's own config parser | No |
| Germany and Finland from the backend, flags, switching | Yes | Interface only | No |
| Clear messages: expired plan, removed device, device limit, server offline | Yes | Client and interface | No |
| IP from your own endpoint; ipinfo and ipapi removed | Yes | Interface and backend | No |
| Config file only under Settings > Advanced | Yes | Interface | No |
| Settings: startup, connect at startup, tray, notifications, diagnostics | Yes | Interface and rules; real registry in a test key | No |
| Reconnect after Windows restart | Yes | **Compiles only** | No |
| Installer: previous build | Yes | n/a | **Yes, you confirmed install and launch** |
| Installer: this build, with upgrade and clean uninstall | Yes | No | No |
| Website: Download button, version, SHA-256, Beta | Yes, **switched off** | Yes | n/a |
| My Devices: sign the Windows app out on every PC | Yes | Yes | n/a |

## Test results here
- **Passed:**
    - Website: 431 tests, type check and build.
    - App interface: 29 tests, type check and build.
    - Client crate: 22 tests.
    - Backend VPN and sign-in: 51 tests.
    - Attack tests against a fake service: 3 tests.
    - Migration 0102 upgrade and downgrade, and the rollback test, on a throwaway database.
- **Failed:** 4 backend tests in the full suite (community, lunch bot, trading, trends). The same 4 fail on the code that's live today, so they aren't from this work. They do need looking at separately. The full suite ran before the last small session-refresh commit: 1,811 passed.
- **Couldn't run here:**
    - The Tauri build, because Smart App Control blocks it. I checked each new Tauri API name against its source instead, which is not the same as compiling.
    - The service's unit tests were blocked on the last run; the build script runs them in the VM.
    - Layout was checked in Edge at two window sizes and 150% scale, against a fake service.
- **Not reachable at all from here:** sleep and wake, network loss, restart, service restart, DNS and IPv6 behaviour. They're on the VM checklist.

## Security findings
**Fixed:**
- Users could add themselves to the service's pipe and impersonate it.
- Any program could control the tunnel.
- The app sent real customer IPs to outside lookups.
- A stolen app token had full account reach. It's now VPN-only.
- A random browser token could be mistaken for an app token.
- WebView2 launch settings could be hijacked.

**Still open:**
- **Unauthorized server changes aren't fully prevented.** Only `KiwiVPN.exe` can change the tunnel, but malware running as the same user could inject into it. The real fix is a signed server list: it needs a new signing key and backend work, and it's the top item before a public release. Details are in `docs/LOCAL-SECURITY.md`.
- **The private key sits in the service's admin-only folder while a connection is wanted,** including across a restart. That's what makes reconnect work. Administrators and SYSTEM can read it.
- **Unsigned installer:** SmartScreen will warn until you buy code signing.
- **A program holding 64 idle pipe connections** could block the app's status checks. It's a nuisance, not an escalation, and needs a read timeout.
- **Privacy:** the page doesn't yet describe the Windows app, and customers still share your personal Helsinki exit IP. See `docs/PRIVACY-REVIEW.md`.
- **The backup can't be restore-tested** without touching the live volume. The script confirms it exists at full size. The planned rollback doesn't depend on it.

**Deliberately left out:** a kill switch (shown as unavailable) and automatic retries after a drop. WireGuard keeps trying by itself, the window never says Connected without a fresh handshake, and auto-connect runs once per launch, so there's no reconnect loop.

## Production changes overnight
- **Website:** deployed the corrected Windows card, `pricechat-nh8052ygc`, which you approved. A Railway backup, `41ea803a`, was also taken.
- **Blocked:** the backend deploy. Migration 0102 has **not** run.
- `main` is still `56eb8df`. All new work is on `feat/kiwi-windows`.

## What needs you
1. Run the deploy above.
2. Build and test in the VM.
3. Choose the signed server list before public release (I recommend yes), and code signing.
4. **VPN access is still closed, so publishing the button alone wouldn't help anyone.** Opening it, and turning payments on or off, is your call.
5. To publish: send me the tested installer and I'll fill in its version and SHA-256, host it, and switch the button on.
