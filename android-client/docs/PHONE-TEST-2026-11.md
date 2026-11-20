# Phone test for 0.2.0: "Only apps I choose"

**For the owner, on a real phone.** Claude ran none of this on a phone: only the emulator results in `test-evidence/2026-11-20/` exist so far. About 30 minutes.

## You need
- An Android phone (Android 10 or later; 14, 15 or 16 matters most), with **Telegram** and **Chrome** installed. WhatsApp or Signal too, if you have them.
- A USB cable, and this PC.
- Your own Price Chat account. The test uses one device slot per country you connect to.

## 1. Prepare the phone (once)
1. Settings > About phone > tap **Build number** seven times. That turns on Developer options.
2. Settings > System > Developer options > turn on **USB debugging**.
3. Connect the phone by USB. On the phone, allow **USB debugging** for this computer.
4. If a debug build of Kiwi VPN (`live.pricechat.kiwivpn.debug`) is installed, open it, **Sign out** (that frees its devices), and uninstall it.
5. If 0.1.0-rc1 from the website is installed, leave it: 0.2.0 installs over it with the same key and keeps you signed in. That's test H1 of `RC-TEST-CHECKLIST.md`.

## 2. Run the kit
In PowerShell, from the repository folder:
```powershell
powershell -ExecutionPolicy Bypass -File android-client\tools\phone-test.ps1 -Apk C:\AI\pc-wt\kiwi-release\android\0.2.0\KiwiVPN-0.2.0-<commit>.apk
```
It installs the APK, records the phone, and then asks you to do each step on the phone and press Enter. It gathers the evidence itself after each step. If you use WhatsApp instead of Telegram: add `-Selected com.whatsapp`.

What it asks you to do, in order:
1. **Sign in**, then on **Choose what uses Kiwi VPN** pick **Only apps I choose**, keep **only Telegram** switched on, press the green button, and **Connect**.
2. **Traffic:** load ipleak.net in **Chrome** (not selected: it should show *your own* address), then use **Telegram** for 20 seconds (selected). The kit compares the bytes through the tunnel during each.
3. **Reconnect:** Disconnect and Connect straight away. Then the kit turns **airplane mode** on for 30 s and off, and times how long the VPN takes to come back.
4. **DNS:** select Chrome as well for a moment, run dnsleaktest.com's standard test in Chrome, note the resolvers, then unselect Chrome.
5. **Block connections without VPN:** turn on Always-on VPN and Block connections without VPN for Kiwi VPN in phone Settings, try Chrome (expected: no internet), Disconnect in Kiwi (expected: nothing has internet), then **turn both off again**.

## 3. What should happen
| Check | Pass |
|---|---|
| Install | "Success". If it says the signatures don't match, the installed copy came from another key: uninstall it first |
| Onboarding | Appears once after sign-in, shows Telegram (and WhatsApp/Signal if installed) as quick picks with their icons; search finds apps by name |
| Routed list | Report says Telegram and Kiwi VPN are **THROUGH THE VPN**, Chrome is **normal connection** |
| Chrome on ipleak.net | Your own address, not the server's |
| Tunnel bytes | Small while using Chrome, clearly larger while using Telegram |
| Kiwi's notification and Home | Say only selected apps are covered, not plain "Protected" |
| Airplane mode | VPN comes back by itself. Note the seconds |
| DNS test with Chrome selected | Only the VPN server's resolver |
| Block connections without VPN | Chrome (unselected) has no internet; after Disconnect nothing has internet. **Record exactly what you saw** |
| Settings > App-Based VPN | Shows the same choice; editing it there and saving reconnects and changes the routed list |
| Log check | "(none: pass)" |

Then also try: **Settings > Account > Delete account** only with a **throwaway test account**, never your real one. It must list what's deleted, refuse until the username is typed exactly, and return to the sign-in screen afterwards. Check that the account's devices are gone from `pricechat.live/vpn/devices` (sign in there with the test account first: after deletion it can't sign in).

## 4. Send back
The folder the kit printed (`android-client\test-evidence\phone-...`), plus anything odd you saw on screen. The `vpn-routed-packages-*.txt` files list your installed apps: share them only with the developer.

## What the kit can't prove
- That a call or message in Telegram itself went through the tunnel. Tunnel byte counts and Android's own routing table are strong evidence, not a packet capture.
- Other phone makers: Samsung, Xiaomi and others change networking and battery rules. One phone is one data point.
- IPv6 leaks, unless your Wi-Fi or mobile network has IPv6. Note whether ipleak.net showed an IPv6 address.
