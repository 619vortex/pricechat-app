# Kiwi VPN for Windows: release checklist

Tick only what was really run. Nothing below has been run yet, except where marked.

## Build
- [ ] `tunnel.dll` built from wireguard-windows (needs approval to run its build script)
- [ ] Service compiles and all its tests pass in the VM (only the 5 config-safety tests have run so far)
- [ ] App compiles
- [ ] `KiwiVPN-Setup.exe` built and run in the VM from its clean checkpoint
- [ ] Licence notices for WireGuard's components shipped with it
- [ ] Code signing in place

## Sign-in
- [x] Backend and page tests for the one-time code flow (local tests only, not deployed)
- [x] Tester exception: backend tests (not deployed)
- [ ] Sign in through the real browser, sign out, expired session, invalid credentials

## Tunnel, on Windows 10 and Windows 11 (test machines, not the owner's PC running `client1`)
- [ ] Fresh install, no separate WireGuard app present
- [ ] Trial activation
- [x] Service alone, in the VM, run by the owner on 2026-11-04: handshake, Connected only after it, IP changed from 5.30.213.225 to 2.31.133.78, traffic, adapter, DNS set, disconnect restored networking, cleanup removed the services. Not yet through the app or an installer
- [ ] Finland connect: public IP becomes Helsinki's
- [ ] Germany connect: public IP becomes Falkenstein's
- [ ] Switching countries
- [ ] DNS leak test, IPv6 leak test
- [ ] Disconnect restores routes and DNS
- [ ] Network interruption, sleep and wake, restart
- [ ] Expired subscription, device limit, server unavailable
- [ ] Kill switch, real leak tests, before it's offered
- [ ] Uninstall removes services, adapter and configs
- [ ] Uninstall removes `C:\Program Files\WireGuard\Data`, which `tunnel.dll` creates for its log, unless the WireGuard app is installed there, in which case that folder is left alone
- [ ] Coexistence with an existing WireGuard install

## Release
- [ ] Security review
- [ ] `fake-service-test.ps1` passes against the release build
- [ ] `ipc-and-key-test.ps1` passes against the release build
- [ ] Signed server list (LOCAL-SECURITY.md, layer 5)
- [ ] Public build made with `--no-default-features`
- [ ] Pipe read timeout
- [ ] Every line in PRIVACY-REVIEW.md resolved, and the privacy page covers the app
- [ ] Backend changes explained and deployed with approval (migration 0102, with a backup first)
- [ ] `/vpn` Windows card switched to the real download, only after all of the above
