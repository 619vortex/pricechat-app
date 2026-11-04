# VM checklist for the Windows build

Needs the backend deployed (`deploy-m3.ps1`) and your username added as a tester. Note each result; "looks fine" counts only if you actually saw it.

## Install
- [ ] One administrator prompt, no other tools needed
- [ ] Start menu shortcut, correct name and icon
- [ ] Opening it again brings up the same window, not a second one

## Sign in
- [ ] Sign in opens your browser at pricechat.live, and Allow returns you to the app
- [ ] Cancel works. Closing the browser instead gives a clear message after 5 minutes
- [ ] Close and reopen the app: still signed in
- [ ] Sign out: back to the sign-in screen, and the tunnel is closed
- [ ] On My Devices, "Sign out on every PC": the app asks you to sign in again soon after

## Connect
- [ ] No file picker anywhere on the main screen
- [ ] Germany: Connecting, then Connected, with the IP matching `https://api.ipify.org`
- [ ] Finland: same. Switching while connected works
- [ ] Device list on My Devices shows one device per country. Sign out and in again, connect: no duplicates pile up
- [ ] Delete the device on My Devices, then connect: it recovers by itself
- [ ] Click Connect and Disconnect quickly, many times: never stuck, never two tunnels (`Get-Service 'WireGuardTunnel*'` shows at most one)

## Problems
- [ ] Unplug the network while connected: it stops saying Connected within about 3 minutes, and recovers when plugged back in
- [ ] Sleep and wake
- [ ] Remove yourself as a tester: a clear message, no connection
- [ ] `Restart-Service KiwiVPN` as administrator while connected: the app recovers

## Restart
- [ ] Connected, restart Windows: the tunnel comes back without opening the app. Open it: Connected, country shown
- [ ] Disconnected, restart Windows: stays disconnected
- [ ] Settings: launch at startup, connect at startup, tray. Each survives a restart and does what it says

## Leaks
- [ ] `https://dnsleaktest.com` standard test while connected: note the servers
- [ ] `https://api64.ipify.org` while connected: never your real IPv6 address

## Security
- [ ] `ipc-and-key-test.ps1` and `fake-service-test.ps1`: ALL PASSED

## Upgrade and removal
- [ ] Install the same build over itself: settings and sign-in kept, service running
- [ ] Uninstall from Windows Settings: `C:\Program Files\KiwiVPN` gone, `Get-Service KiwiVPN` finds nothing, no `kiwi` adapter in `Get-NetAdapter`, no KiwiVPN entry in Startup Apps
