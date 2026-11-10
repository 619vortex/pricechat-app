# Kiwi VPN: infrastructure audit and global expansion plan

Written 2026-11-06. Nothing in this plan has been ordered, changed or deployed. Prices are list prices for new servers,
net of VAT, read on 2026-11-06 from hetzner.com/cloud and cloudtally.eu (which says it verified them on 2026-10-24). The
order page shows the final price before anything is bought.

## 1. Audit: what I could check, and what I couldn't

| Area | Finding |
|---|---|
| Hetzner Cloud API | **No working credentials on this PC.** The `HCLOUD_TOKEN` user variable (32 chars after trimming) is refused by the API ("the token you have provided is invalid"). Cloud tokens are 64 characters, so this one is probably old or something else. No hcloud CLI config. No Robot credentials. |
| SSH | Helsinki takes the `kiwi_vpn` key, but this session isn't allowed to run commands there, even read-only ones. Germany refuses the key and **accepts password logins**. |
| Helsinki <Helsinki IPv4> (`ubuntu-2gb-hel1-1`, shared with your personal wg0/client1) | 22, 443 and 8443 answer. The proxy gives 407 and the agent 401, both correct. wg1 serves Kiwi customers. The agent port 8443 is **open to the internet**: the allowlist change is prepared but not applied. **The proxy can reach the server's own IPv6** (fix ready, below). |
| Germany <Germany IPv4> (Falkenstein, yours) | Only 22 answers from outside. The backend reaches its agent. No browser proxy, so it isn't in Chrome. The runbook is ready. |
| DNS | pricechat.live is on Vercel DNS: `hel1.vpn` → Helsinki. No `fsn1.vpn` yet. |
| Backend | `vpn.sync` reaches both servers. One failed sync used to mark a server offline: fixed on `fix/kiwi-stage0`, not deployed. Alerts go only to logs, because no owner Telegram chat is set. |
| Earlier risks still open | Per my notes from 2026-11-04, an old root password and Hetzner token that were pasted into chat were still valid. Rotate both. |

**Why this matters for expansion:** both current servers sit in one Hetzner account. When Hetzner blocked that account on
2026-11-05, every Kiwi server went down at once. One more provider for some of the new locations removes that single point
of failure.

## 2. Where Hetzner can and can't go

Hetzner Cloud locations: **Falkenstein, Nuremberg (DE), Helsinki (FI), Ashburn VA, Hillsboro OR (US), Singapore.**
There's **no UK, Netherlands or France**. Those three need another provider (your approval).

| Option | Plan | Monthly | Traffic included | Extra traffic |
|---|---|---|---|---|
| Hetzner EU (DE/FI) | CX23, 2 vCPU / 4 GB ("limited" stock) | €5.49 + €0.50 IPv4 | 20 TB | €1/TB |
| Hetzner EU fallback | CPX02, 1 vCPU / 1 GB | €5.99 + €0.50 | 20 TB | €1/TB |
| Hetzner US (Ashburn / Hillsboro) | CPX11, 2 vCPU / 2 GB | €17.49 + €0.50 | **1 TB** | €1/TB |
| Hetzner Singapore | CPX02, 1 vCPU / 1 GB | €7.49 + €0.50 | **0.5 TB** | **€7.40/TB** |
| UK / NL / FR | another provider (e.g. Vultr, DigitalOcean, OVHcloud): about €5–7 for 1 vCPU / 1 GB, 1–2 TB, or unmetered on OVHcloud | to confirm on their order page | | |

Sources disagree on the UK, NL and FR prices, so treat that row as a range until a provider is chosen.

## 3. Recommended configuration (best value)

A 1 vCPU node comfortably carries a few hundred Mbit/s of WireGuard plus the browser proxy, which is far more than today's
users need. Traffic is what costs money outside the EU. The design rule: **small nodes, one per country, rebuilt from scripts
rather than backed up** (a node holds no customer data the backend can't re-send).

| Wave | Location | Provider / plan | Est. monthly | Why |
|---|---|---|---|---|
| 0 (done) | Helsinki, Falkenstein | yours, existing | – | keep; add Germany's proxy |
| 1 | **Ashburn, US East** | Hetzner CPX11 | ~€18 (+€1/TB over 1 TB) | the most requested exit; East Coast reaches all of the US well |
| 1 | **Netherlands (Amsterdam)** | second provider | ~€5–7 | the main EU hub; the first location off the Hetzner account |
| 1 | **UK (London)** | second provider | ~€5–7 | UK sites and streaming |
| 2 | **Singapore** | Hetzner CPX02 | ~€8 (+€7.40/TB over 0.5 TB) | Asia. Its traffic is expensive, so cap it |
| 2 | France (Paris) | second provider | ~€5–7 | only once NL/UK are in use: it overlaps them |
| later | US West (Hillsboro) | Hetzner CPX11 | ~€18 | only if US East gets busy |

**Wave 1: about €30–35 a month. Waves 1 and 2: about €45–50 a month.** Traffic overage is extra: at 2 TB a month on
each non-EU node, that's about €1 (US) and €11 (Singapore). I'd turn off Hetzner's backups (+20%), because the nodes
are rebuilt from the repo.

Dedicated nodes are simpler and safer than the shared ones: they use the full `install.sh` (its own wg0, nftables, IPv6
rejected fast, the no-logs unbound resolver, optional ad blocking), and they have no personal peer to protect.

## 4. Security risks

1. **Account concentration**: one Hetzner block takes everything down. Mitigation: a second provider in wave 1.
2. **Abuse complaints**: a shared exit IP draws copyright and abuse reports, and enough of them get an account blocked.
   Mitigation: mail ports are already blocked; add per-login rate limits; publish an abuse contact; keep the terms page.
3. **Provisioning credentials**: a cloud token can create paid servers. It stays on your PC, DPAPI-encrypted (like the store key),
   is never on Railway, and has a separate read-only token for monitoring if the provider offers one.
4. **Agent ports**: new nodes accept 8443 only from Railway's static IPs, from day one.
5. **SSH**: keys only, no passwords, on every new node. Germany should change too.
6. **Unpatched nodes**: unattended security updates with a controlled reboot window (to add to `install.sh`).
7. **Customer/personal mixing on Helsinki**: your client1 shares the exit IP with customers (your earlier choice). New nodes don't mix.

## 5. What I build without asking (no cost, no production change)

Each stage is code plus tests, and lands on its own branch:

1. `install.sh` hardening for new nodes: unattended-upgrades, SSH keys only, 8443 allowlist, browser proxy included, health endpoint.
2. **Provisioning tool** (`vpn-node/provision.mjs`): makes a server from a plan with cloud-init. `--dry-run` is the default and
   shows the exact API calls and cost. It never creates anything without you typing an approval phrase at a terminal (the same
   design as the store release tool).
3. Monitoring: the backend already syncs every node; add latency and load reporting, Telegram alerts (needs your chat ID), and a status page entry.
4. Smart Connect and failover in the clients (upgrade plan, Stages 3–4), tested against fake nodes.

## 6. Exactly what needs your approval

| # | Change | Cost | Effect on today's users |
|---|---|---|---|
| A1 | Run `apply-proxy-ipv6-fix.sh --apply` on **Helsinki** (after you read its dry-run output) | – | proxy restarts for about 2 s; WireGuard untouched |
| A2 | Germany browser proxy: DNS `fsn1.vpn`, Hetzner firewall 80+443, SSH key, run the installer (includes the IPv6 fix) | – | agent restarts for a few seconds; WireGuard untouched |
| A3 | Deploy the backend flap guard (`fix/kiwi-stage0`) | – | none visible |
| A4 | Submit and go live with Chrome 1.0.3, and deploy the updated privacy page | – | store review |
| A5 | A new Hetzner Cloud API token (read/write), stored encrypted on this PC | – | – |
| A6 | Choose the second provider for NL/UK/FR, and open that account | – | – |
| A7 | Order the wave 1 servers (Ashburn, Amsterdam, London) | ~€30–35/mo | new countries appear only once healthy |
| A8 | DNS names and firewall rules for each new node | – | – |
| A9 | Your Telegram chat ID for owner alerts | – | – |
| A10 | Rotate the old root password and Hetzner token that were pasted into chat | – | – |
