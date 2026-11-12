# Kiwi VPN for Windows 2.1

Branch `feat/kiwi-windows-21` (from `feat/kiwi-windows-2` 936d6de). Rust is compiled and tested only on GitHub Actions.

## 1. Failover in under a minute

Before: a cut server was noticed only once WireGuard's handshake was 180 s old. Then came a 45 s grace and a retry, so the real
CI run took 159 s to notice and 262 s to move.

Now (app only, no service change):

- **Liveness** (`smart.ts` `observeRx` / `probeResult`). The window already reads the tunnel's received bytes every second.
  Anything received proves the server is alive. After 12 s with nothing received, the app sends a probe through the tunnel
  (`probe_tunnel`, `measure::tunnel_answers`): one DNS question for `pricechat.live` to the tunnel's own resolvers, **sent from
  the tunnel's own address**. Windows' strong host model only sends a packet from an address out of that address's own interface,
  so the probe can't leave outside the tunnel: if the tunnel is gone the bind fails, and nothing is sent. The resolvers are always
  inside the tunnel, because split tunneling refuses to exclude them. The server only counts as dead after 3 unanswered probes in
  a row, 4 s apart, with nothing received between them. Worst case that's about 25 s after the last byte. A quiet link answers the
  first probe, so idling never triggers a switch. If the app didn't start the tunnel (it was restarted while connected), the probe
  says "can't tell" and only the old handshake rule applies.
- **Switch** (`nextFailoverStep(..., dead)`). A confirmed dead server is switched away from at once: no grace period, no retry.
  `vpn_connect(server, pauseFirst)` pauses the tunnel even though the service still reads Connected. The kill switch keeps
  blocking (Request::Pause) while the next config is fetched directly. A server just switched to gets 15 s to answer
  (`switchGraceSeconds`), and isn't retried if it doesn't. If a candidate can't be had at all (device limit, gone), the next one
  is tried immediately. `maxSwitches` is 3; failover still gives up rather than looping, and never acts after Disconnect.

Expected total: about 25 s to notice, then pause, config and connect, so about 35-45 s. The CI window test now reports seconds
from the cut, plus the check `failover: back within 60 s of the cut`.

## 2. Known servers first

`flow::known_servers` lists online servers where this PC holds a saved key AND the device is still on the account.
`account_state` returns it as `known_servers`. `failoverOrder` tries those first, each group in Smart Connect order, so a switch
normally needs no new device and can't hit "Too many new devices". A new device is only made when no known server is left. The
CI core run now keeps one of its devices (key in the same Windows user's store) for the window's failover.

## 3. Smart Connect: latency + load

- The proxy name from the backend (`proxy_host`) is still timed by HTTPS when it's sent.
- New fallback: the server's **endpoint** from this PC's own config for it. `config_for` saves it with `Vault::save_endpoint`;
  it isn't secret, since every config carries it. It's timed by 3 TCP connections to port 443 (the proxy port; closed at once,
  nothing sent). Only completed connections count, because Windows retries a refused connection for about a second. Only public
  addresses are timed, never this PC's network. A server this PC has a device for but no endpoint yet gets it via a device-config
  read, which creates nothing.
- Servers that can't be timed are ordered by load (unknown load counts as half full), then recommended. Measured servers score
  `ms + load * 300`.
- Limits: Germany doesn't answer on 443, so it's ranked by load until the backend sends `proxy_host` (feat/kiwi-monitoring). A
  server this PC never had a config for has no endpoint to time.

## 4. Device limit

Backend change on its own branch (`fix/vpn-device-limit`); see its commit. Not deployed: needs the owner's approval.

## 5. Signed auto-update

Design only: `docs/AUTO-UPDATE.md`.

## 6. Support (Tools > Support)

A signed-in person sends a bug report or feedback to Price Chat from the Tools page: Bug / Feedback, a message (up to 4000
characters), and "Include diagnostics" (on by default for bugs). The page sends only those three things to the `send_support`
command; the diagnostics are built in Rust (`client/src/support.rs`: version, service and tunnel state, which options are on, the
number of split-tunnel ranges but not the ranges, server ids, the last 60 lines of the app log) and stripped of WireGuard key lines,
anything shaped like a WireGuard key, every IPv4/IPv6 address and the Windows user name in paths, then capped at 16 000 characters.
It goes to `POST /api/members/vpn/support` with the app's desktop session (allowed by `desktop_scope`: it's under
`/api/members/vpn/`). Signed out, the section says to sign in and opens https://pricechat.live/contact. The backend endpoint
(branch `feat/vpn-support`, migration 0110) must be live before this app ships; until then Send shows Price Chat's 404/405 message.

## Tests

- vitest: `smart21.test.ts` (liveness, dead-server switching, known-first order, load fallback) and `App21.test.tsx` (window:
  dead server left in under 30 s with pause; idle link not switched; busy link not probed; no probe info, no switch; known server
  preferred; refused server skipped at once; nothing after Disconnect), `support.test.tsx` (send, error, signed out, on the Tools page).
- Rust: `measure` (DNS probe format, fake resolver answers / silent / wrong id / no address, TCP timing, public-only),
  `tune` (tunnel address and resolvers), `flow` (known servers, endpoint saved, no device created for a known server), `store`
  (device ids without opening keys, endpoint validation), `support` (key lines, 44-character keys, addresses, user paths, report
  tail and cap, request body), `api` (support call shape against a local stand-in: path, cookie, JSON body, refusal message).
