# Kiwi VPN for Windows: release checklist

Tick only what was really run. Nothing below has been run yet, except where marked.

## Build
- [ ] `tunnel.dll` built from wireguard-windows (needs approval to run its build script)
- [ ] Service compiles and all its tests pass in the VM (only the 5 config-safety tests have run so far)
- [ ] App compiles
- [ ] `KiwiVPN-Setup.exe` built
- [ ] Code signing in place

## Sign-in
- [x] Backend and page tests for the one-time code flow (local tests only, not deployed)
- [x] Tester exception: backend tests (not deployed)
- [ ] Sign in through the real browser, sign out, expired session, invalid credentials

## Tunnel, on Windows 10 and Windows 11 (test machines, not the owner's PC running `client1`)
- [ ] Fresh install, no separate WireGuard app present
- [ ] Trial activation
- [ ] Finland connect: public IP becomes Helsinki's
- [ ] Germany connect: public IP becomes Falkenstein's
- [ ] Switching countries
- [ ] DNS leak test, IPv6 leak test
- [ ] Disconnect restores routes and DNS
- [ ] Network interruption, sleep and wake, restart
- [ ] Expired subscription, device limit, server unavailable
- [ ] Kill switch, real leak tests, before it's offered
- [ ] Uninstall removes services, adapter and configs
- [ ] Coexistence with an existing WireGuard install

## Release
- [ ] Security review
- [ ] Backend changes explained and deployed with approval (migration 0102, with a backup first)
- [ ] `/vpn` Windows card switched to the real download, only after all of the above
