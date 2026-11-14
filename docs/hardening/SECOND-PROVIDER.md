# Kiwi VPN: second hosting provider plan

Date: 2026-11-14. Research and planning only. **Nothing was bought, no account was opened, and no server, firewall or DNS
record was changed.** The Hetzner figures come from the Hetzner Cloud API using GET requests only (`/v1/servers`,
`/v1/pricing`, `/v1/server_types`, `/v1/floating_ips`, `/v1/primary_ips`, `/v1/firewalls`). The token was never printed.
Servers are named, not addressed: this doc has no IP addresses.

Prices are monthly list prices without VAT. EUR prices are converted at 1.16 USD, the rate the earlier `REGIONS.md` used.
The provider's checkout page shows the final price. **UNVERIFIED** marks anything I could not confirm from a primary source today.

Builds on: `feat/kiwi-production:docs/kiwi-production/{REGIONS,INFRA-AUDIT,SECURITY-AUDIT}.md`,
`vpn-node/infra/regions.json`, `docs/KIWI_VPN_EXPANSION_PLAN.md`, and the roadmap
`docs/KIWI-VPN-IMPROVEMENT-PLAN.md` (item 2, "Hosting outside Hetzner").

## 1. Decision in one table

| | Choice | Why |
|---|---|---|
| **Primary second provider** | **EDIS Global** (Austria) | It is the only provider checked that says **in writing** that it allows commercial VPNs ("EDIS Global welcomes commercial VPN providers…"). It covers all six target areas, including **Helsinki**. It accepts **crypto (USDT)** as well as cards and PayPal. Its 1 TB plans cost €4.99 (€7.99 in Singapore). It has an Order API. |
| **Fallback** | **Vultr** | Same price in every region ($5 for 1 vCPU, 1 GB, 1 TB). Transfer is pooled across the account. It has an API and cloud firewall groups, and accepts crypto through BitPay. It covers London, Amsterdam, Frankfurt, Singapore, New Jersey and Stockholm. **Condition:** its AUP still couldn't be read in full (403), so get support's written OK for "an authenticated commercial WireGuard VPN and HTTPS proxy" before opening a paid account. |
| Third option (not now) | Akamai/Linode | Its AUP is permissive and its overage is the cheapest ($5/TB). But a card is mandatory and new accounts get a Trust & Safety ID review. |
| Rejected | DigitalOcean, Netcup, OVH, Contabo, DataPacket, Kamatera, UpCloud | See §3. DigitalOcean's AUP bans "cloak identity" and a Spamhaus listing. Netcup has no London or Nordics and reportedly blocks whole accounts after one complaint. OVH ties the service to IP reputation in its contract and has a history of anti-VPN/Tor wording. Contabo has an old VPN-proxy suspension clause. DataPacket sells dedicated servers only, at $207+. Kamatera's terms couldn't be read. UpCloud's Starter plans include 0.5 TB, and its sign-up has strict ID checks. |
| **sin1 / hil1** | **Delete both** (owner approval) | Saves **$30.68/mo**. Neither has been installed, has DNS, or is registered, and they've served about 0.01 GB each since 2026-11-10. Both are on the Hetzner account whose block is the risk being fixed here. Singapore moves to EDIS. US West isn't needed yet because ash1 covers the US. |
| **Finland (M5)** | **EDIS Helsinki**, new node `hel2` | Moves customers off the owner's personal server **and** off the Hetzner account in one step. Releasing the floating IP afterwards saves $3.50. |
| **Off-platform DB backup** | **Backblaze B2** with Object Lock (Cloudflare R2 as the alternative) | Under 10 GB stored is free on both. The existing boto3 code only needs a different endpoint and keys. Don't use Hetzner Storage Box: it's the same vendor and has no S3 API. |

## 2. Hetzner today (API, 2026-11-14)

All 5 servers have delete and rebuild protection on and Hetzner backups off. Each primary IP has `auto_delete=true`, so it
goes away with its server. The floating IP `kiwi-hel1-customers` has delete protection off.

| Server (Hetzner name) | Location | Type (vCPU / RAM / disk) | Server | Primary IPv4 | Extra | **Monthly** | Included / extra traffic | Outgoing this month |
|---|---|---|---|---|---|---|---|---|
| hel1 (`ubuntu-2gb-hel1-1`) | hel1 | cpx12 (1 / 2 GB / 40 GB) | $10.99 | $0.60 | floating IPv4 $3.50 | **$15.09** | 22 TB / $1.20 per TB | 250.6 GB (owner's wg0 plus customers' wg1) |
| fsn1 (`ubuntu-1gb-fsn1-1`) | fsn1 | cpx02 (1 / 1 GB / 20 GB) | $7.49 | $0.60 | | **$8.09** | 22 TB / $1.20 | 0.6 GB |
| ash1 (`kiwi-ash1`) | ash | cpx11 (2 / 2 GB / 40 GB) | $20.49 | $0.60 | | **$21.09** | 1.1 TB / $1.20 | 21.5 GB |
| sin1 (`kiwi-sin1`) | sin | cpx02 (1 / 1 GB / 20 GB) | $8.99 | $0.60 | | **$9.59** | 0.5 TB / $8.30 | 0.01 GB |
| hil1 (`kiwi-hil1`) | hil | cpx11 (2 / 2 GB / 40 GB) | $20.49 | $0.60 | | **$21.09** | 1.1 TB / $1.20 | 0.01 GB |
| **Fleet** | | | | | | **$74.95** | | |

Primary IPv6 addresses have no price line, so they're free. The account bills in USD, with a VAT rate of 0 in `/v1/pricing`.
Servers created before a price change may be billed less than list.

**sin1 and hil1:** together **$30.68/mo** ($368/yr) for no customers. Since 2026-11-10 they have cost about $4. Hetzner bills
hourly up to the monthly cap, so billing stops when they are deleted.

| Option | Monthly | Comment |
|---|---|---|
| **Delete both (recommended)** | **−$30.68** | Singapore moves to EDIS ($9.27). Recreate later with `kiwi-infra.mjs create` if US West demand appears. |
| Install and register both as testers only | $0 saving | Adds 2 more servers on the account that was blocked. Only worth it if you want Hetzner Singapore as a hot spare for EDIS `sgp1`. |
| Keep sin1 as testers only, delete hil1 | −$21.09 | A middle way if the owner wants a Singapore node this week, before the EDIS account exists. |

To delete, the owner approves, then in the Hetzner console (or `kiwi-infra.mjs` with the owner's typed phrase) turns
**delete and rebuild protection off** on each server (Hetzner needs the two to match), then deletes. The firewalls
`kiwi-sin1` and `kiwi-hil1` can then be deleted for free. No DNS records or Admin registrations exist to clean up.

## 3. Provider comparison (verified 2026-11-14)

### 3.1 VPN and proxy terms, abuse handling, risk

| Provider | Commercial VPN in the terms (quote) | Abuse handling | Account-block risk for a VPN |
|---|---|---|---|
| **EDIS Global** | **Allowed:** "EDIS Global welcomes commercial VPN providers, including paid retail services whose customers' traffic exits to the internet through EDIS Global VPS IP addresses." Also: "A special VPN plan or separate reseller application is not required." Banned: rotating/random IPv6 proxies, Tor exits, I2P outproxies. [FAQ][edis-vpn], [AUP][edis-aup] | Reseller guidance "requires action on abuse messages within 24 hours"; "not a guaranteed grace period"; "serious incidents may require immediate restrictions, suspension or termination". "You remain responsible for abuse originating from your customers." Recommends blocking outbound TCP 25. | **Low.** Markets itself to VPN providers; no suspension reports found. Account verification "may apply" (what KYC involves: UNVERIFIED). Its IP ranges may be blocked in Russia. |
| **Vultr** | **UNVERIFIED in full** (policy page returns 403). Search snippet: bans "operating network services like open proxies, open mail relays, or open recursive domain name servers". VPN not mentioned in the snippet. [AUP][vultr-aup] | Staff (forum, possibly old): "Our abuse department generally requests a response within 12 hours". Older cases: the whole account was terminated after repeated DMCA notices. [LET][vultr-let] | **Medium.** Strict fraud checks on sign-up (suspensions within 24 h of funding, document requests). In Nov 2025 it closed GreatFire's account after a Tencent complaint. [GreatFire][greatfire] |
| **Akamai / Linode** | Not mentioned. Nearest: may not use "or facilitate or allow others to use/access, the Services in any unlawful manner", which covers "any users which Customer directly or indirectly provides access to". The old rule "abuse-listed IP = violation" is no longer in the current AUP (UNVERIFIED). [AUP PDF][akamai-aup] | "May result in Akamai taking immediate action… terminating or suspending"; it weighs "the policies and processes Customer has in place". Forum reports of a 4–24 h response window; DMCA notices are forwarded. [forum][linode-abuse] | **Medium.** Trust & Safety review of new sign-ups (photo ID, card digits). Once an account is active, the AUP is permissive. |
| **DigitalOcean** | Not mentioned. Bans "Operating open proxies… Tor exit nodes", "Any activity intended to withhold or cloak identity", and any action that "results in any of our IP addresses being listed" (Spamhaus). [AUP][do-aup] | Networking switched off automatically ("Networking Disabled"), suspension "even if a violation is committed unintentionally", forum reports of 13–48 h offline. [docs][do-conn] | **Medium-high.** The "cloak identity" wording, plus a 2019 wrongful automated account lock. [blog][do-2019] |
| **Netcup** | Not mentioned. AGB §5(5) bans only bulk mail, mining, very high CPU load and extremist material. The binding German AGB was not checked (UNVERIFIED). [AGB][netcup-agb] | Abuse team "response time of 2 hours"; "products may be temporarily suspended". Users report **all servers on an account switched off** after one report. [help][netcup-abuse], [Trustpilot][netcup-tp] | **Medium-high.** Same failure mode as Hetzner; 12–24 month terms lock money in; prepayment or ID check at sign-up. |
| **OVHcloud** | Not named. VPS terms: must not use it in a way that "harms the reputation of the Host Server's IP address". In 2022 OVH told a Tor operator "Anonymization services, such as TOR, are not permitted". [VPS terms][ovh-vps], [tor-relays][ovh-tor] | Suspension "immediately and without notice in the event of an emergency", including "a notification from a third party". In practice, a 48 h reply window. [GTC][ovh-gtc] | **Medium.** The IP-reputation clause and the anti-anonymization history. |
| **UpCloud** | Not mentioned (ToS effective 01.04.2024). Publishes its own proxy tutorials. [ToS][upcloud-tos] | ToS §13: temporary suspension; termination "if… material or… recurring". | Medium-high at sign-up (card photo and ID, some bans minutes after trial start), low-medium after. |
| **Kamatera** | **UNVERIFIED** (terms pages returned 404). The marketing page says you can "run the VPN of their choice". [VPN page][kamatera-vpn] | Not documented; fraud check asks for an ID-plus-card video. [FAQ][kamatera-faq] | Medium. |
| **DataPacket** | Not mentioned; NordVPN and Mullvad are customers. [AUP][dp-aup], [NordVPN case][dp-nord] | "Reasonable efforts to notify"; immediate suspension only for DDoS. | Low, but dedicated servers only. |
| Contabo (extra) | Current terms block Tor. An older clause allowed suspending "a proxy service such as VPN or Tor" on abuse (still in force: UNVERIFIED). [terms][contabo-tos] | Suspended if a fix isn't confirmed within 24 h. [help][contabo-abuse] | Medium-high. |

### 3.2 Locations, price, traffic, IPv6, payment

Columns: London (LON), Amsterdam (AMS), Frankfurt (FRA), Singapore (SIN), US East (USE), Nordics (NOR).
**Yes** marks a location the provider has. Prices are for the cheapest plan with about 1 GB; a 2 GB plan is shown where one exists.

| Provider | LON | AMS | FRA | SIN | USE | NOR | 1 GB plan | 2 GB plan | Included | Over the allowance | IPv6 | Payment |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **EDIS** | Yes | Yes | Yes | Yes | Yes (NYC, Miami) | **Helsinki**, Stockholm | KVM Smart 1 vCPU / 1 GB / 15 GB: **€4.99 ($5.79)**; Singapore €7.99 ($9.27) | UNVERIFIED (12 plans per location, not itemized; REGIONS.md priced the 2 GB Starter at €12.99 in Cyprus) | 1 TB (in + out) | **Throttled to about 1 Mbps** after the allowance plus ~5%; no automatic charge; buy a traffic pool (~€6/TB per REGIONS.md, UNVERIFIED today) | /64-equivalent, ≤20 addresses at once | Card (Stripe), PayPal, Apple/Google Pay, Alipay, **crypto incl. USDT** |
| **Vultr** | Yes | Yes | Yes | Yes | Yes (New Jersey) | Stockholm | vc2-1c-1gb **$5** | vc2-1c-2gb $10; vc2-2c-2gb $15 | 1 / 2 / 3 TB, pooled | $0.01/GB ($10/TB) | Optional subnet (/64 UNVERIFIED) | Card, PayPal (needs a backup card), **crypto (BitPay)**, Alipay, wire |
| **Akamai/Linode** | Yes | Yes | Yes | Yes | Yes (Newark, DC) | Stockholm | Nanode **$5** | Linode 2 GB $12 | 1 / 2 TB, pooled | **$0.005/GB ($5/TB)** | 1 address plus routed /64 | Card required; PayPal and Google Pay as recurring methods |
| DigitalOcean | Yes | Yes | Yes | Yes | Yes (NYC) | No | $6 | $12 (1 vCPU) / $18 (2 vCPU) | 1 / 2 / 3 TB, pooled | $0.01/GiB | 16 addresses (/124) | Card (no prepaid or virtual cards), PayPal prepay only |
| Netcup | No | Yes | No (Nuremberg) | Yes (+€7.66) | Yes (Manassas, +€1.04) | No | None | VPS 500 G12.5 (2 vCPU / 4 GB) €8.26 on a 12-month term | Flat | Throttled to 200 Mbps if the 24 h average is over 2 TB | /64 | Bank transfer, PayPal, card, SEPA |
| OVHcloud | Yes (UK) | Local Zone | Limburg (UNVERIFIED) | Yes | Vint Hill (OVH US, UNVERIFIED) | UNVERIFIED | None | VPS-1 2 vCPU / 4 GB from $4.54 (likely 12-month term) | Unlimited at 500 Mbps; Singapore 500 GB–1 TB, then 10 Mbps | Throttled | 1 address (/128) | Card, PayPal, SEPA, bank transfer |
| UpCloud | Yes | Yes | Yes | Yes | Yes (NYC) | **Helsinki**, Stockholm | Starter €3 | Starter 1 vCPU €6 / 2 vCPU €8 | 0.5 / 0.5 / 1 TB | Throttled to 100 Mbps, or opt in to $0.01/GB | 1 address | Prepaid balance (min €10): card, PayPal, Apple/Google Pay |
| Kamatera | Yes | Yes | Yes | Yes | Yes (NYC) | Stockholm | $4 (+$1 per IPv6) | Type A 1 vCPU / 2 GB ~$6–9 (UNVERIFIED) | 5 TB | $0.01/GB | $1 per address | Card, PayPal prepay |
| DataPacket | many | many | many | many | many | UNVERIFIED | No VPS | Cheapest dedicated server $207–275 | Unmetered | – | UNVERIFIED | UNVERIFIED |

Sources for this table: [EDIS KVM][edis-kvm], [EDIS locations][edis-loc], [EDIS payment][edis-pay],
[Vultr plans API][vultr-plans], [Vultr regions API][vultr-regions], [Vultr overage][vultr-over],
[Vultr payment][vultr-pay], [Akamai pricing][akamai-price], [Linode regions API][linode-regions],
[Akamai transfer][akamai-xfer], [Akamai IPv6][akamai-v6], [DO pricing][do-price], [DO bandwidth][do-bw],
[DO IPv6][do-v6], [Netcup VPS][netcup-vps], [Netcup payment][netcup-pay], [OVH VPS][ovh-price],
[OVH Singapore][ovh-sg], [OVH IPv6][ovh-v6], [UpCloud pricing][upcloud-price], [UpCloud fair transfer][upcloud-ft],
[UpCloud data centres][upcloud-dc], [Kamatera pricing][kamatera-price], [DataPacket servers][dp-servers].

**Why EDIS rather than Vultr as primary.** Hetzner blocked the account once already, so the deciding factor is written
permission for the business model, not $0.79 a month. EDIS is the only provider that gives it. It also has Helsinki, which
no fallback candidate has (Vultr and Linode only have Stockholm). It takes USDT, the currency Kiwi already collects. The
costs:

1. Traffic over 1 TB is **throttled, not billed**. Admin must alert at 80% (see §6), and a traffic pool is bought ahead of time.
2. A cloud firewall and server-create API are UNVERIFIED (the Order API needs a verified account). Plan for **host
   nftables only**, which is the `install.sh AGENT_ALLOW` path and still untested (see §5).
3. It's a small provider. That's why Vultr is the documented fallback, so no single small host carries everything.

## 4. Recommendation

### 4.1 Wave 1 (EDIS)

| Key | City | Plan | Monthly | Replaces / adds | Notes |
|---|---|---|---|---|---|
| `hel2` | Helsinki | KVM Smart 1 vCPU / 1 GB / 1 TB (AMD EPYC, NVMe) | €4.99 = **$5.79** | Takes customers off hel1's wg1 (M5) | hel1's customers used 9 GB in early November, so 1 TB is plenty. Pick the 2 GB plan if the order page shows one under ~€8 (unbound, proxy and agent fit in 1 GB on fsn1 today, with little headroom). |
| `lon1` | London (Docklands, 10 Gbps) | KVM Smart | €4.99 = **$5.79** | New country | UK legal notes in REGIONS.md (IPA 2016). |
| `ams1` | Amsterdam | KVM Smart | €4.99 = **$5.79** | New country | Expect copyright notices: answer within 24 h. |
| `sgp1` | Singapore | KVM Smart | €7.99 = **$9.27** | Replaces Hetzner sin1 ($9.59, 0.5 TB, $8.30/TB) | Cheaper and off the Hetzner account. |
| **Wave 1 total** | | | **$26.64** | | Plus VAT if charged. The owner is outside the EU, so likely none (UNVERIFIED). |

Fallback, only if EDIS refuses or blocks the account: the same four on Vultr (Stockholm instead of Helsinki), 4 × $5 = $20.
Akamai is the alternative at the same price, with $5/TB overage.

Wave 2 (later, by demand): a second US East node on EDIS New York (€4.99), so the US no longer depends only on Hetzner
ash1. Also Frankfurt on EDIS, if fsn1 (the owner's server) should stop serving customers.

### 4.2 Helsinki move (security audit M5)

1. Install `hel2` (EDIS Helsinki) using the rollout in §5, and register it in Admin as **Helsinki #2, testers only**.
2. After the e2e passes, make `hel2` public and set Helsinki #1 to **not accepting new devices**.
3. Existing devices on Helsinki #1 move automatically when their app next fetches a config, or the owner sends a "reconnect"
   notice. Windows 2.1 fails over by itself.
4. When Helsinki #1 has had 0 live peers for 7 days, unregister it, then remove wg1 and the agent from the owner's server.
   The owner's wg0/client1 is never touched.
5. Release the floating IPv4 `kiwi-hel1-customers` (**−$3.50/mo**) and point `hel1.vpn.pricechat.live` at `hel2` or remove it.

hel1's Hetzner server (cpx12, $11.59) stays as the owner's personal VPN. From then on it isn't a Kiwi cost.

### 4.3 Off-platform backup copy

| Option | Price at ~1 GB (30 nights × ~30 MB) | S3 API | Immutability | Verdict |
|---|---|---|---|---|
| **Backblaze B2** | **$0** (first 10 GB free; then $6.95/TB-month) | Yes (v4 signatures; use an application key, not the master key) | **Object Lock** (governance/compliance, default retention) | **Recommended** |
| Cloudflare R2 | $0 (10 GB-month free; then $0.015/GB), free egress | Yes | R2 "Bucket Locks" (not S3 object-lock headers) | Good alternative; Vercel/Cloudflare-style account |
| Hetzner Object Storage | €6.49 ($7.53) base | Yes | Yes | No: same vendor as the blocked account |
| Hetzner Storage Box BX11 | €3.20 ($3.71) | **No** (SFTP/rsync/WebDAV/SMB) | Snapshots only | No: same vendor, and the job would need rewriting |

Sources: [B2 pricing][b2-price], [B2 Object Lock][b2-lock], [B2 S3 API][b2-s3], [R2 pricing][r2-price],
[R2 bucket locks][r2-lock], [Storage Box][hz-sb], [Hetzner Object Storage][hz-os].

**How the job writes there** (`backend/app/services/db_backup.py`; today it streams one gzip to the bucket
`PORTFOLIO_BUCKET` through `MediaStore._s3()` and keeps the newest 14):

1. New settings: `BACKUP2_S3_ENDPOINT` (for example `https://s3.<region>.backblazeb2.com`), `BACKUP2_S3_REGION`,
   `BACKUP2_BUCKET`, `BACKUP2_S3_KEY_ID`, `BACKUP2_S3_SECRET`. These are Railway variables. Use a B2 key limited to that
   one bucket, with write and list rights and **no delete right**.
2. Write the gzip to a temp file once (it's tens of MB), upload it to the primary bucket as today, then
   `upload_file` the same file to `BACKUP2_BUCKET` under the same `backups/db/…db.gz` key. Check it with `head_object`.
3. Retention on B2 is handled by the bucket, not the job: Object Lock default retention of 30 days plus a lifecycle rule
   that hides and deletes after 31 days. The job never deletes from the second copy, so a leaked Railway secret can't wipe both.
4. If the second upload fails, the job result is **warning** ("primary OK, off-platform copy failed"), not a failure. It
   raises the same stale-backup alert as the primary after 26 h.
5. The weekly restore test (roadmap item 1) alternates between the primary and the B2 copy.
6. Tests: extend `FakeS3` in `backend/tests/test_seo_edge_backup.py` with a second client. Assert both keys are written and
   no delete is ever called on the second client.

Cost: **$0/mo** while under 10 GB. A card is needed to open the B2 account.

## 5. Per-node rollout (EDIS or Vultr)

| # | Step | Who | Check |
|---|---|---|---|
| 0 | **Before the first non-Hetzner node:** run `install.sh --print-config` with `AGENT_ALLOW=<Railway's 3 egress IPs from catalog.json>` and `WITH_PROXY=1`, then a real install in a clean Ubuntu 24.04 container or VM in CI. Confirm the nftables `ip saddr { … } tcp dport 8443 accept` rule and the default drop. The nftables-only path has never run on a live node. | Engineer (branch/CI) | `nft list ruleset` shows 8443 only from the 3 Railway IPs |
| 1 | Order the plan from §4.1, Ubuntu 24.04, with the Kiwi SSH key. Bought by hand, owner approved; any traffic pool is bought at the same time. | Owner | Server is up |
| 2 | **Provider firewall** (Vultr firewall group; EDIS: host nftables only, since its panel firewall is UNVERIFIED): allow 22/tcp, 51820/udp, 80/443/tcp, ICMP; **8443/tcp only from Railway's 3 static egress IPs**; everything else denied. | Engineer | From a non-Railway host: TCP 8443 times out |
| 3 | DNS: an A record `<key>.vpn.pricechat.live` in Vercel DNS (zone `pricechat.live`), TTL 300. Add an AAAA record only if the proxy is meant to answer on IPv6. Use `vercel dns add pricechat.live <key>.vpn A <ip>` or the dashboard. | Owner approves, engineer runs | `dig +short <key>.vpn.pricechat.live` matches the node |
| 4 | `AGENT_ALLOW=<3 IPs> WITH_PROXY=1 bash install.sh`, then `install-proxy.sh` (Let's Encrypt for `<key>.vpn.pricechat.live`; certbot **with an email address** so expiry warnings arrive). Optionally `--adblock`. | Engineer | `kiwi-vpn-doctor` all OK |
| 5 | On the node: `vpn-node/infra/node-selftest.sh` (handshake, IPv4 exit, the node's own resolver, IPv6 refused fast, no metadata or private ranges, port 25 blocked, no client-to-client). Also block 465 and 587 (SECURITY-AUDIT L19). | Engineer | 14/14 pass |
| 6 | Register in Admin as **Testers only**, with `provider = edis` (or `vultr`), `traffic_included_gb = 1000` and capacity 150. | Engineer | The next `vpn.sync` (every 5 min) shows it online |
| 7 | Through a client: `node vpn-node/infra/region-verify.mjs --host <key>.vpn.pricechat.live --country <CC>`. Then the Windows **CI e2e** job in the client CI repo (exit IP, bash.ws DNS leak, IPv6, reconnect, switch) when the shared test account has a free device slot. | Engineer | 0 FAILs. If geolocation warns, ask the provider to update its RFC 8805 geofeed before advertising the country |
| 8 | 48 h testers only, no sync failures, external probe green (see §6). | Engineer | Uptime ≥ 99% |
| 9 | The owner switches the node to **everyone**. | Owner | Visible in the apps' server list |

### 8443 firewall per provider

| Provider | Where the 8443 rule lives | How |
|---|---|---|
| Hetzner (existing) | Cloud Firewall (already in place: `wg`, `firewall-1`, `kiwi-ash1`, …) | `kiwi-infra.mjs agent-firewall` updates the source list |
| EDIS | **Host nftables** (`install.sh AGENT_ALLOW`). A panel or API firewall is UNVERIFIED; if one exists, mirror the rule there as a second layer | Re-run `install.sh` with a new `AGENT_ALLOW` when Railway's IPs change. The rest of the config is kept |
| Vultr | Firewall group (API `/v2/firewalls`) **plus** host nftables | One group shared by all Vultr nodes, so one edit covers them all |
| Akamai | Cloud Firewall **plus** host nftables | Same pattern |

The Railway egress IPs live in one place (`vpn-node/infra/catalog.json` → `agentAllow`). If Railway changes them, every
provider's rule must be updated the same day (INFRA-AUDIT S3). Keep a checklist with one row per provider.

## 6. Monitoring hook

| Layer | What | Cost |
|---|---|---|
| Existing | `vpn.sync` every 5 min from Railway; offline after 2 failed syncs; `vpn_server_checks`; bandwidth alerts at 80/100% of `traffic_included_gb` (`feat/kiwi-monitoring`) | $0 |
| **Add: provider grouping** | A `provider` field on each server, plus an alert **"all servers at provider X offline at once → possible account block"**. That is the 2026-11-05 signature. It names the provider and links its status page and abuse inbox | $0 (code) |
| **Add: EDIS throttle guard** | `traffic_included_gb = 1000` on every EDIS node, so the 80% alert fires before the 1 Mbps throttle. The owner buys pool traffic when the pace alert fires | $0 |
| **Add: outside probe** | A hosted free-tier monitor (UptimeRobot / Better Stack; free-tier limits UNVERIFIED today) checks each `<key>.vpn.pricechat.live:443` (TCP, or HTTPS expecting 407), the API health URL and the website, every 5 min, alerting by Telegram or email. It covers "Railway itself is down", which today alerts nobody (INFRA-AUDIT §3) | $0 |
| Owner alert chat | Set the owner Telegram chat ID so alerts leave the logs (INFRA-AUDIT S7) | $0 |

## 7. Monthly cost before and after

| Line | Today | After the plan |
|---|---|---|
| hel1 (Hetzner, cpx12 + IPv4) | $11.59 | $11.59 (now the owner's personal VPN, not Kiwi) |
| hel1 floating IPv4 (customers) | $3.50 | **$0** (released after the move) |
| fsn1 (Hetzner) | $8.09 | $8.09 |
| ash1 (Hetzner) | $21.09 | $21.09 |
| sin1 (Hetzner, idle) | $9.59 | **$0** (deleted) |
| hil1 (Hetzner, idle) | $21.09 | **$0** (deleted) |
| EDIS hel2 / lon1 / ams1 / sgp1 | – | $5.79 + $5.79 + $5.79 + $9.27 = **$26.64** |
| Off-platform backup (B2) | – | $0 (under 10 GB) |
| Outside monitor | – | $0 (free tier) |
| **Total** | **$74.95** | **$67.41** |
| Of which on Hetzner | $74.95 (100%) | $40.77 (60%; the Kiwi customer exits on Hetzner drop to fsn1 and ash1) |
| Countries served | FI, DE, US | FI, DE, US, **UK, NL, SG** |

Excluding hel1, which becomes the owner's personal server, Kiwi's own bill goes from $74.95 to **$55.82**.
Possible extras: EDIS traffic pool (~€6/TB) or Vultr overage ($10/TB) only if a node goes past 1 TB. ash1's overage stays $1.20/TB.

## 8. Owner approvals, in order

| # | Approval | Exact cost | Unblocks |
|---|---|---|---|
| 1 | **Delete Hetzner sin1 and hil1**: turn delete and rebuild protection off, then delete; delete the firewalls `kiwi-sin1` and `kiwi-hil1` | **−$30.68/mo** (saving) | Stops paying for idle servers on the at-risk account |
| 2 | **Open a Backblaze B2 account** (card), create one bucket with Object Lock (30 days) and a bucket-scoped key without delete rights; deploy the second-copy backup change | **$0/mo** under 10 GB (then $6.95/TB-month) | An off-platform DB copy |
| 3 | **Open an EDIS Global account** (crypto or card; verification may apply). Optionally email support to confirm the authenticated HTTPS proxy is fine too | $0 until the first order | Wave 1 |
| 4 | **Order EDIS Helsinki `hel2`** + DNS `hel2.vpn.pricechat.live` | **+€4.99 ≈ $5.79/mo** | M5: customers off the owner's server |
| 5 | **Order EDIS Singapore `sgp1`** + DNS | **+€7.99 ≈ $9.27/mo** | Singapore, replacing sin1 |
| 6 | **Order EDIS London `lon1` and Amsterdam `ams1`** + DNS | **+€9.98 ≈ $11.58/mo** | UK and NL |
| 7 | Each node's switch from **testers only to everyone**, after §5 step 8 | $0 | Public launch per node |
| 8 | **Retire Helsinki #1 for customers**: unregister it, remove wg1 and the agent (wg0 untouched), release the floating IP | **−$3.50/mo** | M5 closed |
| 9 | (When needed) Write to Vultr support for its OK, then open a Vultr account as the warm fallback (no servers) | $0 until used; $5 per node | Fallback ready if EDIS blocks the account |
| 10 | (Optional) A free outside monitor account; set the owner's Telegram alert chat | $0 | Alerts when Railway is down |

Net effect of approvals 1–8: **$74.95 → $67.41/mo (−$7.54)**, with three new countries and 40% of the bill off Hetzner.

## Sources (all accessed 2026-11-14)

[edis-vpn]: https://docs.edisglobal.com/faq/commercial-vpn-hosting
[edis-aup]: https://docs.edisglobal.com/getting-started/acceptable-use-policy.md
[edis-kvm]: https://www.edisglobal.com/vps-hosting/kvm-vps
[edis-loc]: https://www.edisglobal.com/vpn-hosting
[edis-pay]: https://docs.edisglobal.com/accepted-payment-methods
[vultr-aup]: https://www.vultr.com/legal/use-policy/
[vultr-let]: https://lowendtalk.com/discussion/comment/710523/
[greatfire]: https://en.greatfire.org/blog/2025/dec/tencent-enlisting-american-cloud-hosting-providers-enforce-censorship
[vultr-plans]: https://api.vultr.com/v2/plans?type=vc2
[vultr-regions]: https://api.vultr.com/v2/regions
[vultr-over]: https://docs.vultr.com/support/platform/billing/what-is-the-bandwidth-overage-rate
[vultr-pay]: https://docs.vultr.com/support/platform/billing/what-payment-methods-do-you-accept
[akamai-aup]: https://www.akamai.com/site/en/documents/corporate/acceptable-use-policy.pdf
[linode-abuse]: https://www.linode.com/community/questions/8802/abuse-ticket
[akamai-price]: https://www.akamai.com/cloud/pricing/cpu
[linode-regions]: https://api.linode.com/v4/regions
[akamai-xfer]: https://techdocs.akamai.com/cloud-computing/docs/network-transfer-usage-and-costs
[akamai-v6]: https://techdocs.akamai.com/cloud-computing/docs/an-overview-of-ipv6-on-linode
[do-aup]: https://www.digitalocean.com/legal/acceptable-use-policy
[do-conn]: https://docs.digitalocean.com/support/connectivity-troubleshooting
[do-2019]: https://www.digitalocean.com/blog/an-update-on-last-weeks-customer-shutdown-incident/
[do-price]: https://www.digitalocean.com/pricing/droplets
[do-bw]: https://docs.digitalocean.com/platform/billing/bandwidth/
[do-v6]: https://docs.digitalocean.com/products/networking/ipv6/
[netcup-agb]: https://www.netcup.com/en/agb
[netcup-abuse]: https://netcup.com/en/helpcenter/documentation/security/abuse-notices
[netcup-tp]: https://ca.trustpilot.com/review/netcup.com?page=7
[netcup-vps]: https://www.netcup.com/en/server/vps
[netcup-pay]: https://www.netcup.com/en/about-netcup/payment-methods
[ovh-vps]: https://contract.eu.ovhapis.com/1.0/pdf/Conditions_Particulieres_VPS-gb.pdf
[ovh-gtc]: https://contract.eu.ovhapis.com/1.0/pdf/contrat_genServices-gb.pdf
[ovh-tor]: https://archive.torproject.org/websites/lists.torproject.org/pipermail/tor-relays/2022-February/020286.html
[ovh-price]: https://www.ovhcloud.com/en/vps/
[ovh-sg]: https://www.ovhcloud.com/en-sg/vps/vps-singapore/
[ovh-v6]: https://docs.ovhcloud.com/en/guides/bare-metal-cloud/virtual-private-servers/configure-ipv6.md
[upcloud-tos]: https://upcloud.com/terms-of-service/
[upcloud-price]: https://upcloud.com/pricing/
[upcloud-ft]: https://upcloud.com/fair-transfer-policy
[upcloud-dc]: https://upcloud.com/data-centres/
[kamatera-vpn]: https://www.kamatera.com/?p=18214
[kamatera-faq]: https://try.kamatera.com/faq
[kamatera-price]: https://www.kamatera.com/pricing/
[dp-aup]: https://datapacket.com/acceptable-use-policy
[dp-nord]: https://www.datapacket.com/case-study/nordvpn
[dp-servers]: https://www.datapacket.com/dedicated-servers
[contabo-tos]: https://contabo.com/en-us/legal/terms-and-conditions/
[contabo-abuse]: https://help.contabo.com/support/solutions/articles/103000271238
[b2-price]: https://www.backblaze.com/cloud-storage/pricing
[b2-lock]: https://www.backblaze.com/docs/en/cloud-storage-object-lock
[b2-s3]: https://www.backblaze.com/docs/en/cloud-storage-call-the-s3-compatible-api
[r2-price]: https://developers.cloudflare.com/r2/pricing/
[r2-lock]: https://developers.cloudflare.com/r2/buckets/bucket-locks/
[hz-sb]: https://www.hetzner.com/storage/storage-box/
[hz-os]: https://www.hetzner.com/storage/object-storage/

Plain list of the same URLs, for readers that don't render reference links:

- EDIS: https://docs.edisglobal.com/faq/commercial-vpn-hosting · https://docs.edisglobal.com/getting-started/acceptable-use-policy.md · https://www.edisglobal.com/vps-hosting/kvm-vps · https://www.edisglobal.com/vpn-hosting · https://docs.edisglobal.com/accepted-payment-methods
- Vultr: https://www.vultr.com/legal/use-policy/ (403 to automated readers) · https://api.vultr.com/v2/plans?type=vc2 · https://api.vultr.com/v2/regions · https://docs.vultr.com/support/platform/billing/what-is-the-bandwidth-overage-rate · https://docs.vultr.com/support/platform/billing/what-payment-methods-do-you-accept · https://lowendtalk.com/discussion/comment/710523/
- Akamai/Linode: https://www.akamai.com/site/en/documents/corporate/acceptable-use-policy.pdf · https://www.akamai.com/cloud/pricing/cpu · https://api.linode.com/v4/regions · https://techdocs.akamai.com/cloud-computing/docs/network-transfer-usage-and-costs
- DigitalOcean: https://www.digitalocean.com/legal/acceptable-use-policy · https://www.digitalocean.com/pricing/droplets · https://docs.digitalocean.com/platform/billing/bandwidth/
- Netcup: https://www.netcup.com/en/agb · https://netcup.com/en/helpcenter/documentation/security/abuse-notices · https://www.netcup.com/en/server/vps
- OVHcloud: https://contract.eu.ovhapis.com/1.0/pdf/Conditions_Particulieres_VPS-gb.pdf · https://contract.eu.ovhapis.com/1.0/pdf/contrat_genServices-gb.pdf · https://www.ovhcloud.com/en/vps/
- UpCloud: https://upcloud.com/terms-of-service/ · https://upcloud.com/pricing/ · https://upcloud.com/fair-transfer-policy
- Kamatera: https://www.kamatera.com/pricing/ · https://try.kamatera.com/faq
- DataPacket: https://datapacket.com/acceptable-use-policy · https://www.datapacket.com/dedicated-servers
- Backups: https://www.backblaze.com/cloud-storage/pricing · https://developers.cloudflare.com/r2/pricing/ · https://www.hetzner.com/storage/storage-box/ · https://www.hetzner.com/storage/object-storage/
- Hetzner: Cloud API `/v1/pricing`, `/v1/servers`, `/v1/server_types`, `/v1/floating_ips`, `/v1/primary_ips`, `/v1/firewalls` (GET, read 2026-11-14)
