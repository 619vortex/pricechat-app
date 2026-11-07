# Kiwi VPN for Windows 2.0: status

Branch `feat/kiwi-windows-2`, from the published 0.1.1 (`3be34ec`). The public 0.1.1 installer and website are untouched.
Rule on this PC: Smart App Control stays on, no firewall or security changes without the owner's approval, no Rust is
compiled here (it's blocked, and it raises Smart App Control prompts).

| Feature | Interface (TypeScript) | Rust (app) | Service | Tested |
|---|---|---|---|---|
| Smart Connect | "Fastest server" choice, timings and "busy" shown | `probe_servers`: 3 HTTPS requests to each proxy name, all servers at once | – | logic + window: yes (vitest). Rust: written, not compiled |
| Failover | waits 45 s, retries once, then the next best server, at most 2 switches, never after Disconnect | uses `vpn_connect` | – | yes (vitest, simulated time) |
| Split tunneling | ranges that skip the VPN, refused if too wide or covering the VPN's DNS | `tune::apply` rewrites AllowedIPs (0.0.0.0/0 minus the ranges; IPv6 stays `::/0`) | validates every config as before (max 64 ranges) | TS: yes. Rust tests written, not run |
| Malware/phishing blocking | switch, says Quad9 sees the names | DNS set to 9.9.9.9 / 149.112.112.112, kept inside the tunnel | – | TS: yes. Rust: not run |
| Speed test | Tools | `speed_test`: Cloudflare, 25 MB down, 5 MB up, 3 pings | – | TS: yes. Rust: not run |
| Leak check | Tools: IPv4 is the server's, no IPv6, DNS is the VPN's | `leak_check`: ipify (v4, v6-only host), ip-api EDNS (resolver) | – | verdict: yes. Rust: not run |
| Kill switch | shown only when the service lists `kill_switch` | – | **not built** (design below) | – |
| Auto-connect, tray, start with Windows | as 0.1.1 | as 0.1.1 | – | as 0.1.1 |

`app/preview.html` (dev only) renders the real interface in a browser with a stand-in for Rust: screenshots of home,
connected, settings and tools were made from it.

## Kill switch: design (not built yet)

A kill switch that survives the app crashing has to live in the service, as persistent Windows Filtering Platform rules:

1. On Connect with the kill switch on, the service adds a WFP sublayer with: block all outbound IPv4/IPv6; permit the
   tunnel interface's LUID; permit UDP to the server's endpoint only from the WireGuard service; permit loopback, DHCP,
   and (if "Allow my local network") RFC 1918 + link-local; permit the service itself to reach `pricechat.live` so
   sign-in and renewal still work.
2. The rules are dynamic=false (persistent) so they hold if the app or service crashes, and removed on Disconnect, on
   turning the switch off, and on uninstall.
3. **Rollback, before any test on a real PC:** the service writes the sublayer's GUID to `ProgramData\KiwiVPN\killswitch.json`;
   a separate `kiwivpn-unblock.exe` (and the uninstaller) deletes every filter in that sublayer; a scheduled task created
   before the first test runs it after 10 minutes unless the tester cancels. A System Restore point is made first.
4. Verification: on/off/reconnect/sleep/kill the app/kill the service/unplug the network, each with a leak check and
   `netsh wfp show filters`.

None of this can run on this PC as it is: the service must be built and run, which Smart App Control blocks for unsigned
code. It's also a change to Windows networking, which needs the owner's approval.

## How it can be built and tested without the VM

- **Compile and unit tests: GitHub Actions** (`ci/windows-installer.yml`, draft). Microsoft's Windows runners have no
  Smart App Control; `cargo test` for the client and service, the Tauri build and the installer run there. Needs the
  code in a GitHub repository the owner controls, and no secrets.
- **Running it on this PC with Smart App Control on: sign it.** Smart App Control accepts RSA signatures from a CA in
  Microsoft's Trusted Root Program; an OV code-signing certificate is enough (no reputation needed). Microsoft's Artifact
  Signing is for organisations in the USA, Canada, the EU and the UK; elsewhere, an OV certificate from a CA with
  cloud signing (so the CI can sign) is the route. Cost and identity checks are the owner's decision.
- Real connection, failover and kill-switch tests then run on a signed build, here or on any spare Windows PC.
