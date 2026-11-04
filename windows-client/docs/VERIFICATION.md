# What's implemented, and what's actually been verified

Only what's written under "Verified in the VM" has run in the Windows VM, as reported by the owner. "Host" means tests on the build PC, as a normal user, with no real service or tunnel. Update this file when a result comes back; don't tick anything from expectation.

| Feature | Implemented | Host tests | Verified in the VM |
|---|---|---|---|
| Tunnel: real handshake, IP change, traffic, adapter, DNS set, disconnect restores, cleanup | Yes | n/a | **Yes**, by `connect-test.ps1` (older service build) |
| App window launches, connects, shows live stats, disconnects, no administrator rights | Yes | Interface | **Yes**, Milestone 1 build |
| "Connected" only after a handshake | Yes | Yes | **Yes**, Milestone 1 build |
| Pipe rule: users can't add a pipe instance | Yes | Yes, under the real rule | **Not yet** (`fake-service-test.ps1`) |
| Client refuses a fake service before sending anything (owner + session 0) | Yes | Yes | **Not yet** |
| Anonymous pipe connection, so a fake can't act as the user | Yes | Not directly testable | **Not yet**, and only indirectly |
| Service allows Connect and Disconnect only from its own KiwiVPN.exe, or an administrator | Yes | Rule tests compile; the binary that runs them was blocked by Smart App Control on the host | **Not yet** (`ipc-and-key-test.ps1`) |
| Private key: unreadable to the user, gone after disconnect, not in logs or app data | Yes | No | **Not yet** (`ipc-and-key-test.ps1`) |
| Timer survives closing the window | Yes | Interface and rule | **Not yet** |
| Public IP from Price Chat's own endpoint, location from the server list | Yes | Interface and backend | **Not yet** |
| Content security policy lets the page reach nothing on the internet | Yes | Config only | **Not yet** |
| WebView2 launch settings cleared at start | Yes | No (Tauri can't compile on the host) | **Not yet**, only that it starts |
| Service refuses to install outside Program Files | Yes | Rule test blocked as above | **Not yet** |
| DNS leak test, IPv6 leak test | n/a | n/a | **Not reported** in any test so far |
| Installer: bundles service and WireGuard DLLs, one admin prompt | Yes | No (needs the Tauri build) | **Install and launch: yes, by the owner, on the build before sign-in**. Upgrade and uninstall: not reported |
| Plug-and-play: sign-in, choose server, local keys, automatic device | Yes | Backend, client crate, interface. Backend not deployed | **Not yet** |
| Reconnect after Windows restart | Yes | Compiles only | **Not yet** |
| Settings, tray, notifications, diagnostics | Yes | Interface and rules. Tray and notifications are Tauri-only | **Not yet** |
