# Kiwi VPN for Windows: app plan

Tauri 2 + React, on the Rust service that passed the VM connection test. The app runs as the signed-in person, and the service does everything privileged. Rust is built only in the VM: Smart App Control blocks Tauri's build on the host.

| # | Milestone | Done when | Needs approval |
|---|---|---|---|
| 1 (passed in the VM, 2026-11-04) | **A working window on the service.** Live status over the pipe, Connect and Disconnect from a test config file, real traffic numbers, pipe owner check. | In the VM, without administrator rights, it connects, shows Connected only after a handshake, and disconnects. | No |
| 2 | **Real numbers and a proven pipe.** Service reports when the tunnel came up, so time survives reopening the window. Public IP and location from an outside lookup. A program posing as the service is refused, and can't join the service's pipe. | Reopening keeps the time; the IP matches a check outside the app; `fake-service-test.ps1` passes. | No |
| 3 | **Sign-in.** Browser, one-time code and PKCE through `/vpn/desktop`, session stored with Windows DPAPI, sign out, expired sessions. | Signs in and out in the VM against production. | **Yes: backend deploy with migration 0102, backup first** |
| 4 | **Servers and real connecting.** Server list, location and entitlement from `/members/vpn` (the location then comes from Kiwi's own server list rather than a lookup), one device per country, configs encrypted with DPAPI, clear device-limit and expiry messages. Test config button removed from release builds. | Finland and Germany both connect from sign-in alone, and switching works. | No |
| 5 | **Settings and tray.** Auto-connect, start with Windows, stay in the tray. Kill switch shown as not available yet: it stays off until real leak tests pass. | Each setting survives a restart in the VM. | No |
| 6 | **Installer.** NSIS `KiwiVPN-Setup.exe`, with a release build of the service; installs and removes the service, tunnel, adapter and `Program Files\WireGuard\Data` if it's ours. | Install, upgrade and uninstall from the VM's clean checkpoint leave nothing behind. | No |
| 7 | **Release.** `RELEASE-CHECKLIST.md` on Windows 10 and 11, DNS and IPv6 leak tests, security review, code signing. | Every box really ticked. | **Yes: signing cost, `/vpn` download link** |
