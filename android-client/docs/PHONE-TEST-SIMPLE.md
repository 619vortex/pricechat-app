# Kiwi VPN on your Android phone: the short test (about 20 minutes)

The full version, with what each step proves, is `PHONE-TEST-2026-11.md`. The kit does the measuring; you tap.

**You need:** your Android phone (Android 10+), with Telegram and Chrome; a USB cable; this PC; your own Price Chat account.

1. **Turn on USB debugging (once):** Settings > About phone > tap **Build number** 7 times. Then Settings > System > Developer
   options > **USB debugging** on. Plug in the phone and tap **Allow** on the phone.
2. **Run the kit** in PowerShell from `C:\AI\price-chat`:
   `powershell -ExecutionPolicy Bypass -File android-client\tools\phone-test.ps1 -Apk C:\AI\pc-wt\kiwi-release\android\0.2.0\KiwiVPN-0.2.0-4c3e829.apk`
3. **On the phone, when the kit asks** (press Enter on the PC after each):
   1. Sign in. On **Choose what uses Kiwi VPN** pick **Only apps I choose**, leave **only Telegram** on, save, **Connect**.
   2. Open **ipleak.net in Chrome**: it should show **your own** address (Chrome isn't selected). Then use **Telegram** for 20 seconds.
   3. **Disconnect and Connect** again. The kit then turns airplane mode on and off; the VPN should come back by itself.
   4. Switch **Chrome on** for a moment, run **dnsleaktest.com** (standard test), note the result, then switch Chrome off again.
   5. In phone Settings > Network > VPN > gear next to Kiwi VPN: turn on **Always-on VPN** and **Block connections without VPN**.
      Try Chrome: expected **no internet**. Disconnect in Kiwi: expected **nothing has internet**. Then turn both settings off again.
   6. Kiwi > Settings > **App-Based VPN**: switch to **All apps except selected**, exclude Chrome, save (it reconnects). Chrome on
      ipleak.net: your own address; another browser or app: the VPN's.
4. **Send back** the folder the kit prints (`android-client\test-evidence\phone-...`) and anything odd you saw. The
   `vpn-routed-packages-*.txt` files list your installed apps: share them only with the developer.

**Pass:** Chrome shows your own address while not selected; Telegram's traffic goes through the tunnel; the VPN comes back after
airplane mode; the DNS test with Chrome selected shows only the VPN server's resolver; with Block on, unselected apps have no internet.
