# Kiwi VPN infrastructure audit: Germany, Finland, United States

Date: 2026-11-10. Read-only. Sources: the Hetzner Cloud API (GET requests only, token never printed), the repo's node scripts
(`vpn-node/`), the backend code, and earlier notes. Nothing was changed, no server was logged into, and no IP address is
written here. Servers are named as in Admin: **Helsinki #1**, **Germany #2**, **Ashburn #3**.

## 1. What runs today

| | Helsinki #1 | Germany #2 | Ashburn #3 |
|---|---|---|---|
| Hetzner name / location | `ubuntu-2gb-hel1-1`, hel1 | `ubuntu-1gb-fsn1-1`, fsn1 (Falkenstein) | `kiwi-ash1`, ash (Ashburn, VA) |
| Type | CPX12: 1 vCPU (shared), 2 GB, 40 GB | CPX02: 1 vCPU (shared), 1 GB, 20 GB | CPX11: 2 vCPU (shared), 2 GB, 40 GB |
| Image | Ubuntu 24.04 | **Ubuntu 26.04** | Ubuntu 24.04 |
| Created | 2026-11-03 | 2026-11-04 | 2026-11-07 |
| How it was set up | `install-shared.sh`: customers on **wg1** beside the owner's personal **wg0** (never touch wg0/client1) | probably `install.sh` (nftables node), by hand | `kiwi-infra.mjs` + `install.sh` + `install-proxy.sh`, self-test 14/14 |
| Browser proxy (443) | yes (`hel1.vpn.pricechat.live`) | **no** (443 closed) | yes (`ash1.vpn.pricechat.live`) |
| List price, net / month | $10.99 + $0.60 IPv4 = **$11.59** | $7.49 + $0.60 = **$8.09** | $20.49 + $0.60 = **$21.09** |
| Traffic included / extra | 22 TB / $1.20 per TB | 22 TB / $1.20 per TB | **1.1 TB** / $1.20 per TB |
| Outgoing traffic this month (to 10 Nov) | ~9 GB | ~0 | ~5 GB |
| Delete + rebuild protection | on | on | on |
| Backups / snapshots | off / none | off / none | off / none |
| Hetzner firewall | `wg`: 22, 51820/udp (owner), 51821/udp (customers), 80, 443 open; **8443 only from Railway's 3 static IPs**; outbound limited to tcp/udp/icmp | `firewall-1`: 22, 51820/udp; 8443 from Railway only | `kiwi-ash1`: 22, 51820/udp, 80, 443, ICMP; 8443 from Railway only |

Account: one Hetzner project, 3 servers, 6 primary IPs (3 IPv4 + 3 IPv6, all attached), 1 SSH key, no volumes, no snapshots.
**Total: $40.77 a month at list price** (servers made before a price change may be billed less). The account bills in USD.

## 2. Capacity

- A shared 1-vCPU node carries a few hundred Mbit/s of WireGuard plus the proxy; today's traffic (a few GB a week) uses well
  under 1% of any node. CPU and memory aren't the limit for a long time; **traffic cost outside the EU is**: Ashburn includes
  only 1.1 TB.
- Admin capacity is a device count (default 250 per server). It isn't tied to bandwidth. With 1-3 devices per person and
  maybe 10-20% of devices active at once, 250 devices is about 25-50 concurrent tunnels: fine for 1 vCPU.
- Germany #2 has 1 GB RAM and no proxy: fine for WireGuard, tight if unbound + proxy + agent all grow.
- Device counts and live tunnels per server are in Admin; I can't read the production database from here.

## 3. Monitoring: what exists and what was missing

| Exists (main) | Added on `feat/kiwi-production` (undeployed) | Still missing |
|---|---|---|
| `vpn.sync` every 5 min reads every agent; offline after 2 failed syncs in a row (flap guard, `fix/kiwi-stage0`, merged here) | `vpn_server_checks` history (14 days): ok, round-trip ms, peers, live tunnels per sync | **An outside probe.** Every check starts at Railway: if Railway is down, nothing alerts. A free external monitor on the proxy (407) and the API would cover it |
| Owner alerts: offline / recovered / nearly full (90%) | uptime 24 h / 7 d, p50/p95, live peak, traffic projection, status with reasons (`GET /api/vpn/admin/health`) | **An owner Telegram chat for alerts**: not set, so alerts go only to logs (Admin > Alerts) |
| `feat/kiwi-monitoring` (merged here): monthly traffic per server, 80/100% bandwidth alerts, `live_load` + `proxy_host` for Smart Connect | Fleet alerts: < 2 healthy public servers, public capacity >= 80%, traffic on pace past the allowance | Node metrics: CPU, bandwidth rate, conntrack, disk, unattended-upgrade reboots (the agent reports none) |
| `kiwi-infra.mjs health`: DNS, TLS, proxy 407, certificate days (run by hand) | Members' list carries `health` ok/degraded for failover ordering | Proxy certificate expiry alert (certbot registered without an email) |
| | | An Admin panel for the health report (backend only so far) |

## 4. Single points of failure

| # | Single point | Effect | Fix |
|---|---|---|---|
| S1 | **One Hetzner account** for every server | Hetzner blocked the account on 2026-11-05: every server went down at once | Second provider for new regions (REGIONS.md: Vultr) |
| S2 | **One backend** (Railway `api`, SQLite on a volume) | Backend down: no new devices or configs; existing tunnels keep working (peers stay on the nodes) | Nightly backups that actually succeed (they failed every night 23 Oct - 3 Nov per earlier logs; re-check), documented restore |
| S3 | **Agent port allowlist = Railway's 3 static IPs** | If Railway changes egress IPs, every sync fails and every server goes "offline" in Admin | Alert names this cause; keep the IP list in one place (`catalog.json`), update firewalls with `kiwi-infra.mjs agent-firewall` |
| S4 | One server per country (and Helsinki shared) | Any one failure leaves that country empty; Windows 2.1 fails over to another country | Second node in the busiest country once load justifies it |
| S5 | Helsinki shares the owner's personal VPS and exit IP | Abuse reports against customers hit the owner's personal IP; a compromised agent sits beside wg0 | Move customers to a dedicated Helsinki/Finland node (`install.sh`), keep the personal box personal |
| S6 | DNS on Vercel only; names under `vpn.pricechat.live` | DNS outage: proxies unreachable by name; WireGuard configs hold the endpoint name too | Acceptable now; low TTLs and a documented fallback |
| S7 | Alerts nowhere (no owner chat) | Outages found by customers | Owner sets the Telegram chat ID |
| S8 | No backups of nodes | None needed: nodes hold nothing the backend can't re-send; rebuilt from the repo | Keep it that way; don't turn on paid backups |

## 5. Findings to act on

1. **Germany #2 has no browser proxy and doesn't answer on 443**, so Chrome users can't use it and Smart Connect can't time it
   (2.1 ranks it by load only). Runbook ready: `vpn-node/GERMANY-PROXY.md` (DNS `fsn1.vpn`, firewall 80/443, installer). Needs approval.
2. **Germany #2 allows SSH passwords and refuses the Kiwi key** (earlier note): switch to keys only when the owner next has console access.
3. **Germany #2 runs Ubuntu 26.04** while the scripts are tested on 24.04: check `nft`/`unbound` versions before reinstalling.
4. **Helsinki's customers use Cloudflare DNS (1.1.1.1)**, not a no-logs resolver on the node: `install-shared.sh` sets it. The
   privacy text promises the node's own resolver (SECURITY-AUDIT.md M7).
5. **Ashburn's 1.1 TB allowance** is the first real cost risk: set `traffic_included_gb = 1100` in Admin once migration 0108 is deployed, so the 80%/100% and pace alerts work.
6. **Delete protection is on everywhere** (good); Hetzner requires delete and rebuild protection to match.
7. **The read/write Hetzner token** lives in the Windows user environment: fine for the owner's terminal, but monitoring should use a separate read-only token.

## 6. Cost summary (before any new region)

| Item | Monthly |
|---|---|
| Helsinki #1 | $11.59 |
| Germany #2 | $8.09 |
| Ashburn #3 | $21.09 (+$1.20 per TB over 1.1 TB) |
| **Total** | **$40.77** |

New regions and their costs: `REGIONS.md`.
