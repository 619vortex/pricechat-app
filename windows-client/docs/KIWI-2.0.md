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
| Kill switch | shown only when the service lists `kill_switch`; "Kill switch on", "blocking until the VPN reconnects", "Disconnect and unblock" | sends `protect` (switch, local network, split ranges) | `guard.rs` + `firewall.rs`: WFP filters in a dynamic session | rules: unit tests. Filters and installed service: CI job `kill-switch` (no account). Real tunnel: e2e (needs `KIWI_CI_SESSION`) |
| DNS leak guard | – | – | DNS can't leave outside the tunnel while one is wanted (replaces WireGuard's own, which split tunneling turned off) | as above |
| Auto-connect, tray, start with Windows | as 0.1.1 | as 0.1.1 | – | as 0.1.1 |

`app/preview.html` (dev only) renders the real interface in a browser with a stand-in for Rust: screenshots of home,
connected, settings and tools were made from it.

## Kill switch (built)

The service holds Windows Filtering Platform filters in its own sublayer, in a **dynamic** WFP session: Windows deletes
them the moment the service's process ends, however it ends. Nothing is persistent, so nothing can outlive Kiwi.

- **Always, while a tunnel is wanted:** DNS (port 53) can't leave outside the tunnel. Loopback and the tunnel's own
  addresses come first, so the VPN's DNS and a local resolver still work.
- **Kill switch on:** everything else outside the tunnel is blocked, IPv4 and IPv6, both directions, except Kiwi's two
  programs (`kiwivpn-service.exe`, which also runs the tunnel, and `KiwiVPN.exe`, so it can reconnect, switch servers
  and sign in), DHCP / DHCPv6, IPv6 neighbour discovery, the person's split-tunnel ranges, and the local network if
  "Allow my local network" is on (DNS to the router stays blocked).
- WireGuard's own firewall is kept off by sending each default route as two halves (same routing). It blocked the local
  network, vanished with the tunnel's process, and switched itself off whenever split tunneling was used.

Lifecycle and safeguards against being left offline:

| Situation | What happens |
|---|---|
| Connect / switch server | Filters go on before the old tunnel closes, replaced only once the new ones hold |
| Tunnel process crashes | Blocked meanwhile; the service starts it again every 15 s, up to 6 times, then releases and says so |
| Server stops answering (failover) | The app sends Pause: the tunnel closes, the kill switch stays, the next server's settings are fetched directly (DNS over HTTPS fallback for pricechat.live), then Connect. No Connect within 2 minutes: released |
| A new connection never answers | Given up after 60 s and released, as before |
| Windows restarts | The wanted tunnel and its kill switch come back together; if it hasn't reached its server in 10 minutes, released |
| The service crashes | Filters vanish with its process; Windows restarts it (3 s / 10 s / 30 s) and it restores them |
| Disconnect, switch off, uninstall | Released at once. `kiwivpn-service unblock` (administrator) does the same without the app |

An established tunnel that loses its server keeps blocking (that's the point of a kill switch); the window says so and
offers "Disconnect and unblock".

## How it can be built and tested without the VM

- **Compile and unit tests: GitHub Actions** (`ci/kiwi-windows.yml`, live in `619vortex/KIWIVPN`, pushed with `sync-ci-repo.ps1`). Microsoft's Windows runners have no
  Smart App Control; `cargo test` for the client and service, the Tauri build and the installer run there. Needs the
  code in a GitHub repository the owner controls, and no secrets.
- **Running it on this PC with Smart App Control on: sign it.** Smart App Control accepts RSA signatures from a CA in
  Microsoft's Trusted Root Program; an OV code-signing certificate is enough (no reputation needed). Microsoft's Artifact
  Signing is for organisations in the USA, Canada, the EU and the UK; elsewhere, an OV certificate from a CA with
  cloud signing (so the CI can sign) is the route. Cost and identity checks are the owner's decision.
- Real connection, failover and kill-switch tests then run on a signed build, here or on any spare Windows PC.
