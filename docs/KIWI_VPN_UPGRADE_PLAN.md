# Kiwi VPN: next major upgrade plan

Smart Connect, a verified kill switch, DNS/IPv6/WebRTC leak protection, automatic failover, optional ad/tracker blocking and
better Chrome diagnostics. Written 2026-11-06 from a read-only audit of main and the client branches. Nothing here is built yet.

Rules for every stage: security and stability first; Helsinki connectivity, wg0/client1 and sign-in stay untouched unless a
step says otherwise; each stage ships alone with its tests; no deploy, server change or store publish without approval.

## Where things stand

| Feature | Backend / servers | Chrome | Windows | Android | iOS |
|---|---|---|---|---|---|
| Server choice | `recommended` = online, not full, fewest *provisioned devices* (`service.py` recommended_id). No location, latency or live load. Nodes report no latency/CPU/bandwidth | last used, then recommended (`lib.js` pickServer) | same order (`flow.rs`) | same | same |
| Kill switch | – | **Fails closed**: fixed proxy, no DIRECT fallback; re-applied first after a restart. `pricechat.live` bypasses by design (account API) | `tunnel.dll` with full routes probably blocks untunneled traffic while the tunnel runs, **unverified**; UI says "Not available yet" | relies on Android's "Always-on + Block connections without VPN" (app reads it, can't set it) | `includeAllNetworks` is a user setting; UI says no kill switch |
| DNS | install.sh nodes: unbound, no logs. **Shared nodes (Helsinki, Germany): no unbound, DNS 1.1.1.1** (through the tunnel) | names resolved on the server by the proxy (tested) | from config | config checked (`PeerRules`) | custom DNS allowed (skips the server) |
| IPv6 | install.sh: v6 rejected fast. **Shared nodes: no ip6tables at all**; clients send `::/0` into the tunnel where it's dropped silently (slow fallback, needs a test) | IPv6 sites go through the proxy (tested). **Proxy could reach the server's own IPv6** (fixed on `feat/kiwi-germany-proxy`, not applied) | not tested (`VERIFICATION.md`: "Not reported") | routes checked | not tested |
| WebRTC | – | limited while on; real candidate test passes | n/a (tunnel carries it) | n/a | n/a |
| Failover | **one failed sync marks a server offline** (no flap guard); each device is bound to one server's key and IP | retries the same server only | auto-connect once per launch; no failover | reconnects same server | – |
| Ad/tracker blocking | only install.sh nodes with `--adblock` (unbound + StevenBlack). Not on shared nodes, not for the browser proxy | none | doesn't read `adblock` | – | custom DNS bypasses |
| Diagnostics | – | one check URL; checks only after a proxy error; **no periodic check**; just "Connection problem" + Try again. **Bug found:** after a revoked login it can keep saying Connected (traffic is still blocked) | handshake age | handshake age | – |

## Platform limitations (they shape the design)

- **Chrome extension**: no UDP, no ICMP, no raw sockets. Latency can only be timed with HTTPS requests. It covers the browser only. It can't block other apps, and `pricechat.live` must bypass the proxy so sign-in works when the proxy is down.
- **Windows**: a true kill switch that survives the app or service crashing needs persistent Windows Filtering Platform rules. tunnel.dll's rules disappear with the tunnel service.
- **Android**: only the OS can block traffic outside the VPN ("Always-on VPN" + "Block connections without VPN"). An app can detect that setting and link to it, but can't turn it on.
- **iOS**: `includeAllNetworks` + On-Demand is the strongest option. Apple lets some of its own traffic bypass, so "no leaks" can't be promised.
- **Failover across servers** changes the exit IP and country. The servers have separate keys and IP pools, so each client needs a login or device on the second server first. On the apps that counts against the 3-device limit unless the backend changes.
- **Ad blocking** at DNS level is per resolver. Making it per user means two resolvers per node (filtered and unfiltered) and the client picking one. A custom DNS on iOS skips both.

## Stages

### Stage 0: safety fixes (before any new feature)
1. **Chrome: revoked-login status bug.** Make the popup report "Connection problem" whenever the proxy refuses the login, reliably, then add a regression test that runs 20 times. *Extension only.*
2. **Proxy: refuse the server's own IPv6.** Already on `feat/kiwi-germany-proxy`. Apply to Helsinki (proxy and agent restart for a few seconds, WireGuard untouched). *Server change: approval.*
3. **Shared nodes: reject IPv6 from customers quickly.** On wg1 only, an ip6tables reject (`icmp6-adm-prohibited`) so apps fall back to IPv4 at once instead of timing out. wg0 untouched; the script stops if wg0 changes. *Server change: approval.*
4. **Backend: no single-failure offline flips.** Mark a server offline after 2 consecutive failed syncs (the alert already waits for 2). *Backend deploy: approval.*
5. **Windows: verify the kill switch.** VM test: kill the network path and the tunnel service while connected, then check for leaks. Only then change "Not available yet".

### Stage 1: leak verification on every platform (tests, little code)
A matrix of IPv4 exit, IPv6 exit, DNS resolvers and WebRTC; on/off/reconnect/sleep; Wi-Fi to mobile. Run per platform against Helsinki with a test account. Results go into each client's test docs. Chrome's automated suite already covers most of it. The rest needs a signed-in run on real servers (an owner account, or an approved test account).

### Stage 2: Chrome connection diagnostics
- A background check every minute while on (`chrome.alarms`, a permission with no install warning), so a dead server or revoked login shows up without browsing.
- A step-by-step result in the popup: account API, login issued, proxy reachable (TLS), proxy accepted the login, exit check, with the time of each in ms.
- "Copy diagnostics": versions, server, steps and error codes. Never the login or the password.

### Stage 3: Smart Connect (latency + load)
- **Backend**: live load from the agent's recent handshakes (active peers in the last 3 minutes) next to provisioned devices; keep `recommended` as the fallback.
- **Nodes**: a tiny unauthenticated `HEAD /health` on 443 (proxy) or a separate port, for timing only. It returns no data.
- **Clients**: time 3 HTTPS requests per server and take the median, then score latency + a load penalty and pick the best. "Smart" becomes the default; choosing a server by hand stays. Chrome can only time HTTPS (see limits).

### Stage 4: automatic failover (opt-in)
- Chrome first, as it's simplest: when the check fails and a retry on the same server fails, get a login on the next-best server and switch. **The proxy setting stays locked to the old server until the new one works**, so the kill switch holds. The popup says which country you're now on. Off by default: "Switch country if this one fails".
- Apps: needs a backend decision on spare devices (a failover device per account that doesn't count towards the 3, or a per-account key valid on every server). *Owner decision.*

### Stage 5: optional ad/tracker blocking
- Shared nodes: unbound bound to the wg1 address only, no query logs, two views (filtered and unfiltered) on two addresses. The app or extension picks one per device. wg0 and client1's DNS are untouched. *Server change: approval.*
- Browser proxy: resolve through the matching view based on the login's setting, and refuse blocked names with a clear 403 page.
- Public filtering resolvers (e.g. 1.1.1.2, Quad9) would be faster to add, but a third party then sees every query. Not recommended.

## Order and approvals

| Stage | Changes production? | Needs |
|---|---|---|
| 0.1 Chrome status bug | store update | your submit/go-live |
| 0.2 proxy IPv6 on Helsinki | server | approval |
| 0.3 shared-node IPv6 reject | server | approval |
| 0.4 offline flap guard | backend deploy | approval |
| 0.5 Windows kill-switch test | no | VM run |
| 1 leak matrix | no | a test account on real servers |
| 2 Chrome diagnostics | store update | approval |
| 3 Smart Connect | backend + nodes + clients | approval per part |
| 4 failover | Chrome first; apps need a backend decision | decision + approval |
| 5 ad blocking | servers + clients | approval |
