# Per-app split tunneling, re-verified 2026-11-22 (code = main 71b337f, emulator only)

Owner asked to verify Android per-app split tunneling (all apps / all except selected / only selected) before phone tests.
Branch `owner/feat/android-split-tunnel` = main; no app code changed. Emulator `kiwi36`, Android 16. No real server, account or phone.

| Run | Result |
|---|---|
| `:app:testDirectDebugUnitTest` | 171 passed, 0 failed |
| `:app:testPlayDebugUnitTest` | 171 passed, 0 failed |
| `:app:connectedDirectDebugAndroidTest` | 21 passed, 0 failed, 2 skipped (the lockdown pair, which needs lockdown set) |
| `LockdownRoutingTest` with Always-on + "Block connections without VPN" | 2 passed |

On-device tests that passed include: a selected app's traffic and DNS go into the tunnel; an unselected app keeps its normal connection
and DNS; All Apps covers everything and All Except lets the excluded app out; changing the selection rebuilds the interface; ten
reconnects leave identical routing; IPv4 and IPv6 default routes go into the tunnel; the picker lists only launchable apps; onboarding
"Only apps I choose" with quick picks, search and save; "All apps" saves all apps.

Lockdown note: Android builds the lockdown filter for the always-on app at boot. Reinstalling the app after boot left lockdown
inactive (no "Lockdown filtering rules" in `dumpsys connectivity`) and both lockdown tests failed with traffic getting out; after a cold
boot with the app already installed, the rules were present and both passed (as on 2026-11-20). Settings restored afterwards
(`always_on_vpn_app` null, `always_on_vpn_lockdown` 0).

Not proven here: a physical phone, phone makers' changes, real-server traffic. See `docs/PHONE-TEST-SIMPLE.md`.
