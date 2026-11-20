# Phone test for Kiwi VPN 0.2.0 against production

**For the owner, on a real phone.** Nobody has run this on a physical phone yet. The only results so far are from the emulator, in
`test-evidence/2026-11-20/`. It takes about 40 minutes.

Production has had the mobile backend and the store-review seats since 2026-11-20 (Railway `e789550e`). The test uses the
**store-review account**, so the deletion test deletes only a throwaway review seat. Your own account is never touched.

## APK
- `C:\AI\pc-wt\kiwi-release\android\0.2.0\KiwiVPN-0.2.0-4c3e829.apk` (the website flavour: Auto-Call included, signed with the release key)
- SHA-256 `008AD5604FF94F7491AF099FB0E298E2E3BE8A620AEC9FFFD79CEFA4D8AD6397`
- versionName 0.2.0, versionCode 4. It talks to `https://pricechat.live`.
- Don't use the `-play.aab`. That's the Play bundle, and it can't be installed directly.

## You need
- An Android phone (Android 10 or later), with **WhatsApp, Telegram and Signal** installed and signed in, and **Chrome**.
- A USB cable and this PC.
- **The review account's email and password**, the ones `vpn-extension/store/reviewer_account.py` gave you. It's active until
  2026-12-04. If you no longer have the password, rerun the script in your own terminal. That sets a new password for 30 days, and
  Railway redeploys `api` with it.
- Something to message on each messenger: a second phone, a friend, or "Saved Messages" / "Note to Self".
- Ideally a network with **IPv6** (many mobile data plans have it). The kit tells you whether the phone has an IPv6 address.

## 1. Prepare the phone (once)
1. Settings > About phone > tap **Build number** seven times. That turns on Developer options.
2. Settings > System > Developer options > turn on **USB debugging**.
3. Connect the phone by USB. On the phone, allow **USB debugging** for this computer.
4. If a debug build of Kiwi VPN (`live.pricechat.kiwivpn.debug`) is installed, uninstall it. If 0.1.0-rc1 from the website is
   installed, you can leave it: 0.2.0 installs over it with the same key.
5. Turn off any other VPN app, and set **Private DNS** to Off or Automatic. A custom Private DNS host would sit in front of the
   VPN's DNS (the policy says so); the kit records the setting.

## 2. Run the kit
In PowerShell, from `D:\pc-wt\kiwi-mobile`:
```powershell
powershell -ExecutionPolicy Bypass -File android-client\tools\phone-test.ps1 -Apk C:\AI\pc-wt\kiwi-release\android\0.2.0\KiwiVPN-0.2.0-4c3e829.apk
```
It installs the APK and records the phone. Then it asks you to do each step on the phone and press Enter, or type a short answer.
It takes the screenshots and reads Android's routing table and tunnel counters itself. Options:
- `-SkipLockdown` skips the Block-connections test.
- `-Selected com.whatsapp,org.telegram.messenger` tests fewer messengers.
- `-OwnAccount` uses your own account and skips the deletion test.

The steps, in order:
1. **Sign in:**
   1. Press Sign in.
   2. In the browser tab, tap **"App Store or Google Play reviewer? Use the review account"**.
   3. Enter the review email and password.
   4. Press **Allow**.
2. **Choose apps:** pick **Only apps I choose**. Switch on only WhatsApp, Telegram and Signal; Chrome stays off. Then check the
   server list shows **only Helsinki 1**.
3. **Connect** to Helsinki 1. Home should show **"Public IP: 65.109.255.206 (verified: the server's)"**.
4. **Per-app routing:**
   1. Chrome (not selected) on ipleak.net should show **your own** IP.
   2. Then send a message with a photo in each messenger. The kit measures the bytes through the tunnel during each one.
5. **Leaks:**
   1. Select Chrome too, for a moment.
   2. Run ipleak.net (IPv4, IPv6 and the DNS section), test-ipv6.com, and dnsleaktest.com's Extended test.
   3. Unselect Chrome.
6. **Reconnects:** Disconnect and Connect. Then the kit turns airplane mode on for 30 seconds, and off again.
7. **Block connections without VPN:** turn it on in phone Settings, check that Chrome is cut off, then turn it off again.
8. **Delete the review account:**
   1. Settings > Account > Delete account.
   2. Check that a wrong username is refused, then type the right one.
   3. Check that it goes back to sign-in.
   4. Optionally, sign in again to see a fresh account.

## 3. What should happen
| Check | Pass |
|---|---|
| Install | "Success" (if the signatures don't match, uninstall the old copy first) |
| Review sign-in | The link appears in the sign-in tab; after the password you're back on the page with **Allow** |
| Server list | **Helsinki 1 only** |
| Kiwi Home | `Public IP: 65.109.255.206 (verified: the server's)` |
| Routed list (kit) | WhatsApp, Telegram, Signal and Kiwi VPN **THROUGH THE VPN**; Chrome **normal connection**; an IPv6 default route (`::/0`) in the VPN |
| Chrome unselected, ipleak.net | **Your own** IP, not 65.109.255.206 |
| Each messenger | Message with a photo delivered; tunnel bytes clearly larger than during Chrome |
| ipleak.net with Chrome selected | IPv4 65.109.255.206; IPv6 **none** (or a Hetzner one, never your ISP's); DNS only Finland / Hetzner |
| test-ipv6.com | "IPv4 only" (or a Hetzner IPv6); never your ISP's IPv6 |
| dnsleaktest.com Extended | Only Hetzner / Finland |
| Airplane mode | The VPN comes back by itself; note the seconds |
| Block connections without VPN | Chrome (unselected) has no internet |
| Delete account | Wrong username refused; afterwards the sign-in screen, VPN down; signing in again gives a fresh account |
| Log check | "(none: pass)" |

## 4. Send back
- **The whole folder the kit prints**: `android-client\test-evidence\phone-<date>-<model>\` (REPORT.md, the screenshots and the
  dumpsys files). Look at the screenshots first, because they can show notifications. The `vpn-routed-packages-*.txt` files list
  your installed apps, so share them only with the developer.
- **The times in REPORT.md** (sign-in, connect, delete, all in UTC), so the developer can match them in the production logs. Those
  show the device being made on Helsinki and removed after deletion.
- **Anything odd** you saw on screen, in your own words.

## What this can't prove
- That a call inside a messenger went through the tunnel. Android's routing table and the tunnel byte counts are strong evidence,
  not a packet capture. Voice and video calls are worth one try each; note whether they connected.
- Other phone makers. Samsung, Xiaomi and others change networking and battery rules, so one phone is one data point.
- IPv6 leaks, if your network has no IPv6. The kit says so; repeat steps 3 to 5 on mobile data if you can.
