# Real-device test plan

**Status when written: nothing here has been run.** Don't mark a line passed unless it was done on a real phone. Record the phone model, Android version, and the app's build (Settings). Use more than one phone maker if you can, since Samsung, Xiaomi and Huawei manage background apps differently.

## Install
1. Copy `KiwiVPN-app-debug.apk` to the phone and open it. Allow installing from that source when asked.
2. Check the SHA-256 against the `.sha256` file if you copied it through anything you don't trust.
3. A debug build installs beside a release build, as `live.pricechat.kiwivpn.debug`. Its certificate belongs to the VM it was built in, so a build from another VM won't install over it.

## Core
| # | Test | Pass means |
|---|---|---|
| 1 | Sign in with Price Chat, by email and by Google | Returns to the app, shows your name. Try letting the browser sit for a minute first |
| 2 | Account with no trial: press Start free trial. Then an account that started on Windows | First starts it; second shows the same days left as the website and offers no new trial. Signing in alone starts nothing |
| 3 | Connect to Germany, accept Android's prompt | "Protected" only after a real connection |
| 4 | Check the exit address at an outside site | Germany's server, and matches the app |
| 5 | Disconnect | "Unprotected"; your normal address is back; no key icon |
| 6 | Reconnect straight away, with no pause | Connects. This is the bug Windows 0.1.0 had |
| 7 | Switch to Finland while connected | Finland's address, without pressing anything else |
| 8 | Diagnostics > Run 20 cycles (debug build) | `RESULT: 20 of 20 passed`. Send the text |
| 9 | Wi-Fi to mobile data and back, while connected | Recovers by itself. Note how long it took, and whether it showed Reconnecting |
| 10 | Airplane mode for 30 s, then 5 minutes | Recovers. Traffic didn't leave outside the tunnel |
| 11 | Lock the phone 10 minutes | Still connected; the notification is there |
| 12 | Close the app from Recents while connected, then reopen | Stays connected, or says it was lost. Never a false Protected |
| 13 | Force stop the app from Settings while connected | VPN stops, and the app says so when reopened |
| 14 | Reboot the phone | Comes back off, unless Always-on is set |
| 15 | Device limit: use up your devices elsewhere, then connect | A clear message; Remove under Account fixes it |
| 16 | Expired account: use a test account whose trial has ended | The website's status, no connection |
| 17 | Sign out, then sign in again | Devices removed from My Devices; connects again |

## Security
**These are the checks that decide what can be claimed. Nothing can be claimed from a passed connection test alone.**

| # | Test | Pass means |
|---|---|---|
| 18 | DNS: while connected, use a DNS leak test site | Only the server's resolver appears, never your network's or carrier's |
| 19 | Repeat 18 with Android Private DNS on Automatic, then on a named provider | Record what appears. Private DNS may use its own resolver |
| 20 | IPv6: on a network with IPv6, use an IPv6 test site while connected | No real IPv6 address of yours. Should fail, or show the server's |
| 21 | IPv6 with the tunnel on a server with no IPv6 pool | IPv6 traffic fails rather than going around |
| 22 | Always-on VPN on | After reboot it connects by itself |
| 23 | Always-on plus Block connections without VPN | With the tunnel off, or the server unreachable, nothing reaches the internet. Reconnects using saved details. **Only after this can anything be said about blocking** |
| 24 | Block on, then switch country | Switches, and nothing leaks during it |
| 25 | Capture traffic with another device on the Wi-Fi during 9 and 10, if you can | Only WireGuard to the server |
| 26 | Device log (`adb logcat -s KiwiVPN`) and Diagnostics | No key, token, config or address |

## Auto-Call VPN
Turn it on, give Notification access, and pick the apps. If Android calls it a restricted setting, use **Allow restricted settings**, and note it if that isn't offered. Have a second phone to call from. **After each row, copy Diagnostics > What Auto-Call VPN saw.** That's the evidence of what each app really sends.

Run each for **WhatsApp** and **Telegram**, voice and video, with the VPN starting **off**:

| # | Scenario | Expected |
|---|---|---|
| 27 | Incoming, answered, then ended by you | Call detected, Connecting VPN, VPN connected. Off 15 s after the end |
| 28 | Incoming, ended by the other person | Same |
| 29 | Incoming, missed | Off 15 s after ringing stops. The missed-call notification doesn't reconnect it |
| 30 | Incoming, rejected | Off 15 s after |
| 31 | Outgoing, answered | Connects, off 15 s after |
| 32 | Outgoing, not answered | Off 15 s after you hang up |
| 33 | **Does the call survive the VPN coming on?** | Record: no effect, a blip, or dropped. Note the seconds from answer to Protected |
| 34 | Second call during the first (other app if possible) | Off only 15 s after the last ends |
| 35 | New call within 15 s of the last | Stays on |
| 36 | VPN already on by hand, then a call | Stays on afterwards |
| 37 | Press Disconnect during a call | Stays off for that call |
| 38 | Swipe away the call notification during a call | "Couldn't confirm". Stays on until you disconnect |
| 39 | Force stop Kiwi VPN during a call | Reopen: says uncertain or lost, never falsely Protected |
| 40 | Force stop WhatsApp during a call | Record what happens. May read as a confirmed end |
| 41 | Phone locked for the whole call | All the same statuses |
| 42 | Battery saver on, and the phone idle for an hour before the call | Still detects. Record any delay |
| 43 | Withdraw Notification access, then call | Nothing happens; Settings says access is needed |
| 44 | Only one app selected, and a call on the other | Nothing happens |
| 45 | VPN permission withdrawn, then a call | Says to open the app, and doesn't loop |
| 46 | Airplane mode just after the call starts | Doesn't loop or drain the battery |

**Results decide what's said about this feature.** If 27 to 33 don't hold on a given phone and app, the setting should say so, or be removed.

## App-Based VPN
Use an outside site in Chrome, such as ipleak.net, to see each app's address. Check WhatsApp or Telegram with a second app you can see an address in if you have one. **"Uses the VPN" means the address is the server's. "Normal" means it's your own.**

| # | Test | Pass means |
|---|---|---|
| 47 | (Emulator, Android 16, 2026-11-20: Android logged the start with type 1024 and allowed it. Still run it on a phone.) **First, on Android 14 or later:** connect with any mode, and watch for a crash | Stays running with the notification showing. If it crashes, send the Android version: see the open risk in `APP-ROUTING.md` |
| 48 | All Apps: Chrome | Server's address |
| 49 | Only Selected, Chrome selected | Server's address |
| 50 | Only Selected, Chrome not selected, another app selected | Your own address |
| 51 | All Except, Chrome excluded | Your own address |
| 52 | All Except, Chrome not excluded | Server's address |
| 53 | Only Selected, nothing selected | Save is disabled, with the reason shown |
| 54 | Only Selected, then uninstall the only selected app, then Connect | Refuses and says why. It must **not** connect every app |
| 55 | Change mode while connected, for each pair of modes | The prompt appears, it reconnects, "Active now" changes only after it reconnects, and test 48 to 52 still hold |
| 56 | Change mode while connected with the server unreachable | An error shows. It doesn't claim the new mode |
| 57 | Change mode, restart the app | Choice kept |
| 58 | Change mode, reboot the phone | Choice kept |
| 59 | Switch mode back and forth | Each mode's app list is as you left it |
| 60 | Germany to Finland in each mode | Mode stays, address changes for routed apps only |
| 61 | Search, Select all, Bypass all, Clear selection | The counts and each switch match. Select all acts only on what the search shows |
| 62 | Always-on VPN with Only Selected or All Except | Reconnects with the same mode after reboot |
| 63 | Block connections without VPN with Only Selected or All Except | **Record what happens to apps outside the VPN.** Google warns they can lose their connection |
| 64 | DNS test site in an included app, then in an excluded app | Included: the server's resolver only. Excluded: your normal resolver, as expected |
| 65 | IPv6 test site in an included app, then in an excluded app | Included: no real IPv6 address of yours. Excluded: your normal IPv6 |
| 66 | Auto-Call with Only Selected and WhatsApp not selected | The warning shows. During a call, the VPN comes up and the call doesn't use it |
| 67 | Auto-Call with WhatsApp selected, then a call | VPN comes up in that mode, and your selections aren't changed |
| 68 | Is a work-profile app listed? Does it follow the rules? | Record it. Android keeps work profiles separate |

## Results to send back
Phone and Android version, the app's build, the output of test 8, any failure's on-screen message, Diagnostics > Recent events, and the Auto-Call lines.
