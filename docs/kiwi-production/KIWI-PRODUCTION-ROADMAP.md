# Kiwi VPN production roadmap

Date: 2026-11-10. Branch `feat/kiwi-production` (worktree `C:\AI\pc-wt\kiwi-prod`, from main `aa7583b`). Nothing here is
deployed or ordered. Companion documents: [REGIONS.md](REGIONS.md), [INFRA-AUDIT.md](INFRA-AUDIT.md),
[SECURITY-AUDIT.md](SECURITY-AUDIT.md). Earlier plans these replace or extend: `docs/KIWI_VPN_EXPANSION_PLAN.md`,
`docs/KIWI_VPN_UPGRADE_PLAN.md` (merged here from `docs/kiwi-upgrade-plan`).

## Where things stand

| Area | State |
|---|---|
| Windows 2.0.0 | **LIVE**, signed (SSL.com OV), pricechat.live/downloads/KiwiVPN-Setup.exe. Kill switch, DNS guard, IPv6 handling, Smart Connect, failover, CI e2e green. Don't touch. |
| Windows 2.1 | **In progress, another agent**, `feat/kiwi-windows-21`: failover < 60 s (tunnel probe), known servers first, Smart Connect by endpoint timing + load, signed auto-update **design** (`windows-client/docs/AUTO-UPDATE.md`). Device-limit backend fix on `fix/vpn-device-limit`. |
| Servers | Helsinki #1 (shared with owner's wg0), Germany #2 (no proxy), Ashburn #3 (dedicated). $40.77/month. One Hetzner account. |
| Backend | Live with payments off, trial on. Server monitoring and the flap guard were on undeployed branches; now merged here. |
| Chrome extension | 1.0.0 live in the store; 1.0.3 fixed and tested, not submitted; 1.0.4 Smart Connect on `feat/kiwi-ext-smart`. |
| Android | RC1 APK published for owner testing; not verified on a phone. iOS: CI set up, no build. |
| Payments | NOWPayments USDT by hand, $9.99/30 days; off; no real payment tested. Fixes on `feat/kiwi-payments(-v2)`. |

## What this branch adds (done, tested, undeployed)

| Change | Files | Tests |
|---|---|---|
| Merge `feat/kiwi-monitoring` (traffic per server, 80/100% bandwidth alerts, `live_load` + `proxy_host` for Smart Connect); its migration re-chained after main's 0107 as **0108** | `backend/app/vpn/service.py`, `alerts.py`, `migrations/versions/0108_*` | `test_vpn_monitoring.py` |
| Merge `fix/kiwi-stage0` (one failed sync no longer marks a server offline) | `service.py` | `test_vpn.py` |
| Merge `feat/kiwi-infra` (Hetzner manager: audit/plan/health read-only; create/dns/install need the owner's typed phrase) and `docs/kiwi-upgrade-plan` | `vpn-node/infra/*`, `docs/KIWI_VPN_*` | `lib.test.mjs` 10/10 |
| **Server health and capacity**: `vpn_server_checks` (migration **0109**, 14 days), uptime 24 h/7 d, agent p50/p95, live peak, traffic projection, status with reasons; fleet view; `GET /api/vpn/admin/health`; members' `health` ok/degraded; fleet alerts (< 2 healthy public servers while open, capacity >= 80%, traffic on pace past allowance) | `backend/app/vpn/health.py`, `models/vpn.py`, `alerts.py`, `routes/vpn.py` | `test_vpn_health.py` 14 |
| **Regions planner** (dry run; `--apply` refuses non-Hetzner and hands Hetzner to kiwi-infra's typed approval) + `regions.json` (9 countries, prices, sources, risk) | `vpn-node/infra/regions.mjs`, `regions-lib.mjs`, `regions.json` | `regions.test.mjs` |
| **New-region client check** (exit IP, country, DNS leak, IPv6 leak, connectivity) | `vpn-node/infra/region-verify.mjs` | `regions.test.mjs` |

How 2.1 uses it: 2.1's Smart Connect already reads `proxy_host` and `load` (from `feat/kiwi-monitoring`, now here) and may also
order by `health`. The fields are additive; 2.0 ignores them. No 2.1 file was edited.

## Phases

### Phase 1: make the current fleet trustworthy (no new cost)
1. Owner: set the alerts Telegram chat (O2); fix and prove backups (O1); rotate old credentials (O3).
2. Deploy this branch's backend (migrations 0108 + 0109; backup first; deploy from the worktree, never the main checkout).
   Then set traffic allowances in Admin: Helsinki 22000 GB, Germany 22000 GB, Ashburn 1100 GB.
3. Germany #2 browser proxy (`vpn-node/GERMANY-PROXY.md`): DNS `fsn1.vpn`, Hetzner firewall 80/443, installer.
4. Admin panel for `/api/vpn/admin/health` (frontend; small).
5. External uptime probe (outside Railway) for the API and each proxy's 407.

### Phase 2: Windows 2.1 (other agent) and backend device-limit fix
- Review/merge `feat/kiwi-windows-21` after its CI e2e; deploy `fix/vpn-device-limit` together with M1 (eligibility before
  charging the shared bucket). Release 2.1 as a normal signed download first (no auto-update yet).

### Phase 3: first new regions (needs approval and money)
- London, Amsterdam, Singapore on Vultr: **$15/month** at 1 TB each (fleet $55.77). Steps per region: `regions.mjs plan`.
- Each region: node self-test, `region-verify.mjs` through a client, CI e2e, Testers only → everyone.
- Before ordering: Vultr's written OK for a commercial VPN; `kiwi-infra.mjs install --host` for non-Hetzner servers (small change).

### Phase 4: security and privacy fixes (SECURITY-AUDIT.md)
- Before payments: M4 (trial abuse), M1 (shared buckets).
- Privacy promises: M6 (Helsinki DNS), M7 (proxy DNS), M5 (move customers off the personal box: a dedicated Finland node,
  ~$6-7/month on Hetzner CX23/CPX11 hel1).
- M2 proxy connection limits. Lows as files are touched.

### Phase 5: signed auto-update (2.2) and signed server list
- Build the design in `AUTO-UPDATE.md` (SYSTEM service verifies Ed25519 manifest + pinned Authenticode thumbprint; no
  Tauri updater). Needs: Ed25519 key generated offline by the owner, GitHub environment `kiwi-publish`, manifest hosting under
  `/downloads/kiwi/windows/`. Same key infrastructure (different prefix) signs the server list (closes I2).
- Kill-switch strict mode and the Low guard fixes (M3, L10-L13) in the same release.

### Phase 6: paid launch readiness
- One real NOWPayments payment in test mode; IPN signature proven; legal review of /vpn/terms and /vpn/refunds; trial rules
  (M4) live; abuse contact published; payment scrub for members (L2/L3).

### Phase 7: wave 2 and beyond
- Tokyo, Toronto, Sydney (+$15/month at 1 TB). Tel Aviv and Cyprus only on demand. UAE only after a UAE lawyer's opinion.
- Second node in the busiest country when the fleet alert says capacity >= 80% or a server's traffic is on pace past its allowance.

## Owner decisions and approvals needed

| # | Decision / approval | Cost | Notes |
|---|---|---|---|
| D1 | Set the owner alerts Telegram chat | - | Without it no alert reaches anyone |
| D2 | Fix the backup bucket and confirm one good backup | - | Precondition for migrations |
| D3 | Deploy this branch's backend (migrations 0108, 0109) | - | Backup first; members' API gains `health`, Admin gains `/health` |
| D4 | Second provider = **Vultr** (open account; ask support in writing that a commercial VPN is allowed) | - | Alternatives: Linode, Kamatera, OVH (REGIONS.md) |
| D5 | Order wave 1: London, Amsterdam, Singapore | **$15/month** at 1 TB each ($75 at 3 TB) | DNS names `lon1/ams1/sgp1.vpn.pricechat.live` too |
| D6 | Wave 2: Tokyo, Toronto, Sydney | +$15/month | Japan MIC notification question first |
| D7 | Israel / Cyprus: start or wait | +$5 / +$15.10 | Recommend wait |
| D8 | UAE: get a UAE lawyer's opinion, or drop it | - | Recommend avoid |
| D9 | Germany proxy (DNS, firewall 80/443, install) | - | Runbook ready |
| D10 | Move Helsinki customers to a dedicated node | ~$6-7/month | Ends personal-IP exposure |
| D11 | Device policy: per installation vs per server; browsers in the same budget? | - | SECURITY-AUDIT L6 |
| D12 | Trial rule before payments (one per verified identity / account age) | - | SECURITY-AUDIT M4 |
| D13 | Kill-switch strict mode as default or option | - | SECURITY-AUDIT M3 |
| D14 | Auto-update: generate the Ed25519 manifest key offline; create `kiwi-publish` environment | - | AUTO-UPDATE.md |
| D15 | Public uptime figure on /vpn once 7 days of checks exist | - | `public_stats.uptime` stays null until then |
| D16 | Free space on C: (it hit 0 MB during this work) | - | Breaks builds and git for every agent |

## Rules kept

No server created, changed or logged into; Hetzner API read with GET only; no deploys, no pushes, no CI runs, no Rust built;
Windows 2.0 files untouched; no 2.1 file edited; no IPs or secrets in these documents.
