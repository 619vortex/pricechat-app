# KiwiVPN competitive gap analysis

Date: 2026-11-12. This is research only: nothing in production was changed or deployed.

## How this was checked

**KiwiVPN (our side):**
- Production API, read-only GETs:
  - `/api/members/vpn/public` → `{"enabled":true,"test_mode":false,"payments":false,"trial":true,"free":false}`.
  - `/api/members/vpn/stats` → 3 servers online, `active_users: 1`, `uptime: null`.
  - `/api/members/vpn/support` → 404. The support route is not deployed.
- The live installer `https://pricechat.live/downloads/KiwiVPN-Setup.exe` was downloaded and hashed: SHA-256 `1F2BAAB6…FD7BA`, the signed **2.0.0** build. Windows 2.1 is not public.
- DNS: `hel1.vpn.pricechat.live` still resolves to `2.29.149.29`, the old address.
- Code: release branch `release/kiwi-prod` (`ab37d3c`), the feature branches, and the recorded CI and test results.
  - Windows CI runs are on 619vortex/KIWIVPN.
  - Android was checked on `feat/kiwi-android-rc`.
  - iOS was checked on `feat/kiwi-ios-cloud`.
- **Not checked:**
  - I can't sign in to Admin, so device counts, server settings such as `adblock` and `testers_only`, and real customer numbers beyond `/stats` were not read.
  - I ran no speed tests from outside. This PC itself exits through Helsinki, so a measurement from here would be meaningless.

**Competitors:** a web scan on 2026-11-12 of official pages and 2025–26 reviews. Full notes and sources: `competitors.md` in the session scratchpad. Facts tagged *(unconf.)* below were not re-confirmed for 2026. Prices are introductory and in USD unless marked.

### Status legend

| Status | Meaning |
|---|---|
| **LIVE** | Deployed and reachable, with real-world or real-server CI evidence |
| **LIVE-U** | Deployed, but no end-to-end proof with a real customer path |
| **BUILT** | Built and tested on a branch or in CI, NOT deployed |
| **PARTIAL** | Exists in a limited form |
| **MISSING** | Not built |
| **UNVERIFIED** | Code or a claim exists, but nothing proves it works |

Importance: **C**ritical / **H**igh / **M**edium / **L**ow. Difficulty: S (days) / M (weeks) / L (1–3 months) / XL (quarter+ or external dependency).

---

## 1. Side-by-side comparison

Competitor columns: **Nord** = NordVPN, **Expr** = ExpressVPN, **Proton** = Proton VPN, **Mull** = Mullvad, **Surf** = Surfshark, **Wind** = Windscribe.

### 1.1 Platforms

| Feature | KiwiVPN status and evidence | Nord | Expr | Proton | Mull | Surf | Wind | Imp | Diff | Action |
|---|---|---|---|---|---|---|---|---|---|---|
| Windows | **LIVE** 2.0.0, signed (SSL.com OV). CI on real servers: e2e 36/36, kill switch 30/30. Owner confirmed it works on a Smart App Control PC. 2.1 (failover in 26 s, support button) is **BUILT** | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | C | — | Ship 2.1 after the backup/restore gate |
| macOS | **MISSING**. A manual WireGuard config from the web dashboard *may* work in the official WireGuard app: **UNVERIFIED** | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | H | L | Native app (NetworkExtension). Until then, document and test the manual-config route |
| Linux | **MISSING** (manual WireGuard config only, **UNVERIFIED**) | GUI+CLI, open source | GUI (2025) | GUI + CLI beta | GUI | ✔ | GUI | M | M | Ship a tested `wg-quick` guide first, then a CLI |
| Android | **PARTIAL**: 0.1.0-rc1 signed APK, public download labelled "Testing". The owner said it connected to DE and FI on a physical phone; there are no logs and the device test plan was not run. Not on Google Play | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | C | M | Run DEVICE-TEST-PLAN on 2–3 phones, then Play internal testing, then production |
| iOS | **BUILT** (unsigned compile + 82 logic tests on Codemagic). Never run on an iPhone. The App Store needs an **organisation** developer account (guideline 5.4) | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | H | XL | Form a legal entity / D-U-N-S, then TestFlight |
| Chrome | **LIVE-U**: Chrome Web Store 1.0.0 (HTTPS proxy, browser traffic only). Passes e2e against a fake API. A real signed-in connect by the owner has not been reported. 1.0.4 (Smart Connect) is **BUILT**, not submitted | proxy ext | remote control for the app | proxy ext | none | proxy ext | proxy ext | M | S | Submit 1.0.4; do a real-account test |
| Edge | **PARTIAL**: the Chrome store extension installs in Edge and was e2e-tested in Edge. No Edge Add-ons listing | ✔ | ✔ | — | — | ✔ | ✔ | L | S | List on Edge Add-ons with the same package |
| Firefox | **MISSING** | ✔ | ✔ | ✔ | — | ✔ | ✔ | L | M | Port (MV3 proxy API differs) after the mobile apps |
| Routers / TV | **PARTIAL** (any WireGuard device can import a config, **UNVERIFIED**) | ✔ | ✔ incl. own router | ✔ | manual | ✔ | ✔ | L | S | Document it |

### 1.2 Protocols, encryption, leak protection

| Feature | KiwiVPN status and evidence | Nord | Expr | Proton | Mull | Surf | Wind | Imp | Diff | Action |
|---|---|---|---|---|---|---|---|---|---|---|
| Protocol | **LIVE**: WireGuard only (Curve25519 / ChaCha20-Poly1305) on UDP 51821 (Helsinki) / 51820. The browser extension uses an HTTPS proxy on 443 | NordLynx, OpenVPN, NordWhisper | Lightway, WireGuard, OpenVPN | WireGuard, OpenVPN, IKEv2, Stealth | WireGuard only | WireGuard, OpenVPN, IKEv2, Dausos | WireGuard, OpenVPN, IKEv2, Stealth, WStunnel | — | — | Fine; WireGuard is the industry standard |
| Post-quantum key exchange | **MISSING** | ✔ | ✔ | roadmap | ✔ default | ✔ | (unconf.) | L | M | Add a WireGuard PSK from an ML-KEM exchange later (Mullvad's model) |
| Key handling | **PARTIAL**: Windows and Android generate keys on the device (**LIVE** on Windows). Configs made on the **website** are generated server-side, returned once and not stored, so the private key passes through the backend | client-side | client-side | client-side | client-side | client-side | client-side | H | S | Generate website configs in the browser (WebCrypto X25519) |
| DNS leak protection | Windows **LIVE**: a DNS guard in WFP is on whenever a tunnel is wanted; CI DNS leak test (bash.ws) passes. Android / manual configs **UNVERIFIED** | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | C | S | Add a DNS leak check to the Android device tests |
| IPv6 leak protection | Windows **LIVE** (CI `api6` test passes; configs route `::/0` into the tunnel). Shared nodes have no IPv6 pool, so customers get **no IPv6 at all** (blocked, not tunnelled). Android **UNVERIFIED** | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | H | M | Give nodes routed /64s so customers get real IPv6 |
| Kill switch | Windows **LIVE** (WFP, 30/30 CI checks, survives failover via Pause). Android: relies on the OS's "Always-on VPN + block connections without VPN", **UNVERIFIED**. Extension: n/a (proxy) | system + app | Network Lock | ✔ + advanced | Lockdown | ✔ | Firewall | C | S | Prove the Android always-on mode on a phone; consider kill switch on by default on Windows |
| WebRTC leak (browser) | Extension **BUILT** + e2e (real WebRTC candidate gathering checked in 1.0.3+) | ✔ | ✔ | ✔ | — | ✔ | ✔ | M | — | Ships with 1.0.4 |

### 1.3 Servers and network

| Feature | KiwiVPN status and evidence | Nord | Expr | Proton | Mull | Surf | Wind | Imp | Diff | Action |
|---|---|---|---|---|---|---|---|---|---|---|
| Countries / locations | **LIVE**: 3 countries, 3 servers (Helsinki FI, Falkenstein DE, Ashburn US). Germany has **no browser proxy**. All are Hetzner | 137 / 9,300+ servers | 113 / 214 locations | 140+ / 20,000+ | 50 / 570 | 100 / 4,500+ | 69 / 135 cities | C | M per site | Wave 1: UK, NL, Singapore, US-West via a second provider (plan in `KIWI_VPN_EXPANSION_PLAN.md`) |
| Server class | Hetzner cloud VPS: cpx12 (Helsinki, **shared with the owner's personal VPN**), cpx02 (Germany), cpx11 (Ashburn). 1–2 GB RAM, shared vCPU | 10 Gbps, RAM-only | RAM-only | 10 Gbps most | 10/20 Gbps, diskless | 10 Gbps, RAM-only | RAM-only | H | M | Dedicated nodes for customers; get off the personal box |
| Speed / latency | **UNVERIFIED**. A CI speed test exists, but no published or repeatable numbers. Windows Smart Connect times servers by TCP to 443; Germany can't be timed (443 closed) until `proxy_host` ships (**BUILT**) | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | H | S | Nightly speed and latency probe per node, stored in Admin |
| Reliability | **PARTIAL**. On 2026-11-05 Hetzner blocked the owner's account and **every server went down** (~1 day): one provider and one account is a single point of failure. Outage alerts only reach logs unless the owner's Telegram chat is set. `uptime: null` in `/stats` | high | high | high | high | high | high | C | M | Second provider; external uptime monitor; owner alerts |
| Bandwidth limits / monitoring | **PARTIAL**: a per-member monthly cap exists (`monthly_cap_gb`, 0 = unlimited). Provider-allowance alerts (80/100%) are **BUILT** (migration 0111). Ashburn includes only 1 TB/month | unlimited | unlimited | unlimited (paid) | unlimited | unlimited | unlimited (Pro) | H | S | Deploy 0111; set allowances; keep "unlimited" off the marketing until the cost model holds |
| Capacity | Default 250 devices per server, **UNVERIFIED** under load (one active user today) | — | — | — | — | — | — | H | S | Load-test one node (iperf3 + many peers) before selling |

### 1.4 Convenience features

| Feature | KiwiVPN status and evidence | Nord | Expr | Proton | Mull | Surf | Wind | Imp | Diff | Action |
|---|---|---|---|---|---|---|---|---|---|---|
| Split tunneling | Windows **LIVE** (IP ranges only; refuses ranges that would leak DNS; CI test via 1.0.0.1/1.1.1.1). Android per-app "App-Based VPN" in rc1, **UNVERIFIED** on phones. **No per-app split tunneling on Windows** | Win/Android/Linux | Win/Android/Mac | Win/Mac/Android (paid) | Win/Mac/Linux/Android | Win/Android/iOS | desktop + Android | H | M | Per-app on Windows (WFP app-ID filters) |
| Auto-connect / launch at login | Windows **LIVE-U** (`auto_connect` and `launch_at_startup` preferences in 2.0; no CI test of a cold boot). Android **UNVERIFIED** | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ incl. network rules | M | S | Add a reboot test to CI; consider auto-connect on untrusted Wi-Fi |
| Smart Connect / fastest server | Windows **LIVE** in 2.0 (latency + load; limited because `proxy_host` is undeployed). Extension 1.0.4 **BUILT** | Quick Connect | Smart Location | Quick Connect | — | Fastest | ✔ | M | — | Deploy feat/kiwi-monitoring (in the release) |
| Automatic failover | 2.0 **LIVE**: notices in 159 s, moves in 262 s (CI). 2.1 **BUILT**: about 26 s | (auto-reconnect) | ✔ | ✔ | ✔ | ✔ | ✔ | H | — | Ship 2.1 |
| Quick server switching | **LIVE** (Windows); limited by the device rate limit: a burst of 5, then **one new device per 10 min**. CI failovers hit "Too many new devices" | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | H | S | "Known servers first" (2.1) + the device-limit fix (**BUILT**); separate the browser-login bucket from devices |

### 1.5 Privacy, logging, audits, security architecture

| Feature | KiwiVPN status and evidence | Nord | Expr | Proton | Mull | Surf | Wind | Imp | Diff | Action |
|---|---|---|---|---|---|---|---|---|---|---|
| No-logs design | **PARTIAL**. The database stores only the account, each device's public key, tunnel IP, last handshake and monthly byte counts; there is no destination or traffic logging in the code. **Not verified:** what Railway/Vercel HTTP access logs, nginx/journald on the nodes and the proxy's stderr retain (the client IP is likely in platform logs) | audited | audited | audited ×5 | audited | audited | audited server stack | C | M | Write down and minimise every log source (Railway, Vercel, journald, proxy), set retention, then publish an accurate policy |
| Independent audit | **MISSING** | Deloitte | KPMG 2025 | Securitum 2026 | Assured | Deloitte 2025 | PacketLabs 2024 | H | XL ($) | After the policy is accurate; budget a 3rd-party pentest first |
| RAM-only / diskless servers | **MISSING** (ordinary Ubuntu VPS with disk) | ✔ | ✔ | (unconf.) | ✔ | ✔ | ✔ | M | L | Later, on dedicated nodes |
| Open-source clients | **MISSING** (the repo is private) | Linux only | Lightway only | ✔ all | ✔ all | — | ✔ | M | S | Optional; would help trust a lot for a small brand |
| Transparency report / warrant canary | **MISSING** | (unconf.) | ✔ | ✔ | — | ✔ | — | L | S | Publish once there is a legal entity |
| Jurisdiction / legal entity | **PARTIAL**: run by an individual in the UAE (the code-signing cert is an individual cert). Launch review found policy gaps: trader identity, EU 14-day withdrawal consent, governing law, GDPR basics. Terms and refunds are drafts | Panama | BVI | Switzerland | Sweden | Netherlands | Canada | C | M | Fix the policies before any paid launch; decide on an entity (also unblocks iOS) |
| Customer/personal separation | **PARTIAL (new)**: since 2026-11-12, customers on Helsinki exit from floating IP `65.109.255.206`, not the owner's IP. But customers still terminate on **the owner's personal server**, and the old DNS name still points at the personal IP | n/a | n/a | n/a | n/a | n/a | n/a | H | S→M | Owner runs the DNS change now; move customers to a dedicated node |
| Control-plane security | **LIVE**: node agents behind certificate pinning + tokens; Helsinki/Germany agent port 8443 limited to Railway's egress IPs; the extension is origin-gated; Windows app uses local keys, a VPN-only session scope, and is signed | — | — | — | — | — | — | — | — | Re-verify ash1's 8443 allowlist; rotate any secret ever pasted in chat |
| Anonymous accounts | **LIVE-U**: anonymous key accounts exist (`/members/vpn/anon*`), but new anonymous keys are refused while the service is free; payments are crypto only | email | email | email | ✔ number | email | optional email | M | — | Keep; a real differentiator next to Mullvad |
| Backups / data safety | **CRITICAL GAP**: the backup job failed from ~2026-10-22 (wrong S3 secret). Fixed 11-12, but **no successful backup has run since**; last good backup 2026-10-21 | — | — | — | — | — | — | C | S | Run the backup now, then test a restore (pending) |

### 1.6 Streaming, torrenting, gaming, censorship

| Feature | KiwiVPN status and evidence | Nord | Expr | Proton | Mull | Surf | Wind | Imp | Diff | Action |
|---|---|---|---|---|---|---|---|---|---|---|
| Streaming unblocking | **UNVERIFIED / not offered**. Hetzner datacenter IPs are widely flagged by Netflix and others | strong | very strong | paid | weak | strong | good | M | XL | Don't promise it. Residential/ISP IPs are expensive |
| Torrenting / P2P policy | **MISSING policy**. Nothing blocks it, but Hetzner forwards abuse/DMCA notices to the account holder, and **one Hetzner account block already took down all servers** | P2P servers | all servers | paid | ✔ | ✔ | ✔ | **C** | S | Publish an acceptable-use policy now; block or limit P2P ports on Hetzner nodes; put P2P only on an abuse-tolerant provider |
| Port forwarding | **MISSING** | — | — | ✔ | removed | — | ✔ | L | M | Skip |
| Gaming / latency | **PARTIAL**: Ashburn helps US-East users; no gaming features | Meshnet | — | — | — | — | — | L | — | Skip |
| Censorship resistance / obfuscation | **MISSING** for the apps (plain WireGuard UDP, easy to block). The browser proxy on TLS/443 is somewhat more resistant, **UNVERIFIED** | NordWhisper | automatic | Stealth | QUIC/MASQUE, Shadowsocks | Camouflage | Stealth, WStunnel | M (H if targeting MENA) | L | WireGuard-over-TLS/WebSocket (wstunnel/AmneziaWG) if the target market includes the Gulf/Iran |

### 1.7 Devices and accounts

| Feature | KiwiVPN status and evidence | Nord | Expr | Proton | Mull | Surf | Wind | Imp | Diff | Action |
|---|---|---|---|---|---|---|---|---|---|---|
| Device limit | **LIVE**: 3 per account (`max_devices`). A device is per server, so one phone on 2 servers uses 2 slots. The fix is **BUILT** (fix/vpn-device-limit) | 10 | 10–14 | 10 | 5 | unlimited | unlimited | H | S | Deploy the fix; count physical devices, not server peers; raise to 5–10 |
| Sign-in | **LIVE**: Price Chat Passport (Google) + desktop PKCE sign-in; anonymous keys | email | email | Proton account | number | email | optional | M | — | Add email sign-in without Google (Play/iOS reviewers need it) |
| Account dashboard | **LIVE** (`/vpn`: devices, configs, revoke, trial) | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | — | — | — |
| In-app account deletion | **MISSING** (required for the App Store) | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | M | S | Build before iOS submission |
| Family / team plans | **MISSING** | — | — | ✔ | — | ✔ | ✔ | L | M | Later |

### 1.8 User experience and stability

| Feature | KiwiVPN status and evidence | Competitors | Imp | Diff | Action |
|---|---|---|---|---|---|
| App design | Windows (Tauri/React) and Android (Compose) share the `/vpn` look; the web dashboard was redesigned and is live. Not yet checked signed in on a real iPhone/phone layout | Mature, polished apps with years of UX work | M | — | User test with 5 people |
| Connect time | **UNVERIFIED** (WireGuard is typically under 1–2 s) | under 2 s typical | M | S | Record connect time in the app's diagnostics |
| Stability | 0.1.0 had a reconnect bug (fixed in 0.1.1); 2.0 passes CI reconnect ×3. No field data: one active user | Very large field fleets | H | — | Opt-in crash/health telemetry (no traffic data) |
| Auto-update (Windows) | **MISSING** (design only, `AUTO-UPDATE.md`); users must re-download | ✔ | H | M | Build the signed updater; until then show "new version" in-app |
| Install friction | Signed installer clears Smart App Control (**LIVE**). Android needs sideloading (**PARTIAL**) | Store installs | H | — | Google Play |

### 1.9 Pricing and payments

| Feature | KiwiVPN status and evidence | Nord | Expr | Proton | Mull | Surf | Wind | Imp | Diff | Action |
|---|---|---|---|---|---|---|---|---|---|---|
| Price | Set at **$9.99 per 30 days**, manual renewal, no long-term plans. **Payments are OFF**: no revenue | $14.99/mo; $3.49/mo on 2-year | ~$12.95/mo; ~$2.49–3.49/mo on 2-year | $9.99/mo; $2.99/mo first year | €5 flat | $15.45/mo; ~$2/mo on 2-year | $9/mo; $69/yr; $1/location | C | S | Add 1-year (~$49–59) and 6-month plans; keep monthly $9.99 |
| Free tier / trial | **LIVE**: one 30-day trial per account; no free tier | none / 30-day refund | 30-day refund | free tier, unlimited data | none | 7-day mobile trial *(unconf.)* | 10 GB free | M | — | Shorten the trial to 7 days once paid launches (30 days at Hetzner prices is costly at scale) |
| Payment methods | **BUILT, never proven**: USDT via NOWPayments (ERC20/TRC20/SOL). The IPN signature is untested, **no real payment ever made**. Stripe card code exists, but it is off | card, PayPal, crypto | card, PayPal, BTC | card, PayPal, BTC, cash | cash, crypto, card | card, PayPal, crypto | card, PayPal, crypto, cash | **C** | S (crypto) / M (card) | One real test payment; then a card processor (most buyers won't use USDT) |
| Refunds | **PARTIAL**: draft `/vpn/refunds`, not legally reviewed; admin refund action exists | 30 days | 30 days | 30 days | 14 days | 30 days | (unconf.) | H | S | Adopt a clear 7–30-day policy matching the EU withdrawal rules |
| Recurring billing | **MISSING by design** (owner's rule: manual renewal) | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | H | M | Expiry reminders exist (`vpn.reminders`); add recurring only with cards |

### 1.10 Support, diagnostics, monitoring

| Feature | KiwiVPN status and evidence | Competitors | Imp | Diff | Action |
|---|---|---|---|---|---|
| Support channels | **PARTIAL**: website contact page; Telegram community. No live chat or helpdesk | Nord/Express/Surf 24/7 chat; Proton tickets; Mullvad email | H | S | Shared helpdesk inbox plus documented response times; an FAQ |
| In-app bug report + diagnostics | **BUILT** (Windows 2.1 Tools › Support; redacted diagnostics; backend migration 0110, Admin › Support). Production returns 404 | All have log upload | H | — | Ships with the release |
| Self-diagnostics | Windows 2.0 **LIVE**: leak check, speed test, server probe in Tools (CI-covered) | Partial at most | M | — | Advantage; keep it |
| Server monitoring | **PARTIAL**: `vpn.sync` every 5 min, `fail_streak`, flap guard **BUILT**; bandwidth alerts **BUILT**; owner alerts need a Telegram chat; no external uptime check or public status page | Status pages | H | S | External monitor (e.g. a free uptime service) + status page + owner Telegram alerts |
| Admin tooling | **LIVE**: Admin › VPN (servers, customers, payments, block/revoke, testers, test mode) | — | — | — | — |

### 1.11 Advanced features

| Feature | KiwiVPN status and evidence | Nord | Expr | Proton | Mull | Surf | Wind | Imp | Diff | Action |
|---|---|---|---|---|---|---|---|---|---|---|
| Ad / tracker blocking | **UNVERIFIED**: a per-server `adblock` resolver flag exists; whether any server has it on was not visible without Admin. Windows has a `block_malware` preference | Threat Protection | ✔ | NetShield | DNS blocking | CleanWeb | R.O.B.E.R.T. | M | S | Run a blocking resolver (e.g. Unbound + blocklists) on every node; offer a per-device toggle |
| Malware protection | **PARTIAL / UNVERIFIED** (DNS only, see above) | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | M | S | Same resolver with malware lists |
| Dedicated IP | **MISSING** | ~$4.19/mo | Pro / add-on | Business | — | ~$3.75/mo | static IPs | L | M | Later: a Hetzner floating IP costs $3.50/mo, so this is a ~$5–7 add-on with margin |
| Multihop | **MISSING** | Double VPN | — | Secure Core | ✔ | MultiHop | double hop | L | M | Later |
| Obfuscation | **MISSING** (see 1.6) | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | M | L | See 1.6 |
| Bundles (password manager, breach alerts) | **MISSING** | ✔ | ✔ | ✔ | — | ✔ | — | L | — | Skip |
| Unique: Price Chat integration | **LIVE**: Royal+ ranks get the VPN free; XP economy; Telegram community | none | none | Proton suite | none | none | none | — | — | This is the real differentiator: lead with it |

### 1.12 Infrastructure, scalability, profitability

| Item | KiwiVPN today | Competitor benchmark | Imp | Action |
|---|---|---|---|---|
| Node cost | About **$47/mo**: Helsinki + Germany ~$22.18 (both also the owner's), Ashburn $21.09, floating IP $3.50 | Mullvad: ~570 servers / 50 countries; Hetzner EU 20 TB included, ~$1.20/TB over | — | See section 5 |
| Revenue | **$0** (payments off, trial on, 1 active user) | — | C | Turn on payments after the test payment + legal |
| Scalability | Single Railway service; WireGuard peers synced every 5 min (a new device can wait up to the sync for routing, **UNVERIFIED**); 250-device default per node, untested | 10 Gbps fleets | H | Load test; push-based peer add |
| Provider concentration | 100% Hetzner, one account | Mullvad 14 providers | C | Second provider before paid launch |

---

## 2. The 20 biggest gaps, by priority

| # | Gap | Why it matters | Imp | Diff | Recommended action |
|---|---|---|---|---|---|
| 1 | **No verified backup since 2026-10-21** | One database failure loses every account, device and payment. The release with migrations is blocked on it | C | S | Press Run now; finish the restore test; then deploy the release |
| 2 | **No revenue path proven**: payments off, no real payment ever made, crypto only | The product can't earn | C | S/M | One real NOWPayments payment in test mode; then a card processor |
| 3 | **Single provider and account** (all nodes on one Hetzner account; the 11-05 account block took everything down) | One abuse complaint = total outage | C | M | Add a second provider (UK/NL/Singapore) and a second Hetzner project or account |
| 4 | **No acceptable-use/abuse policy or P2P control** | Torrent DMCA notices go to Hetzner, which already blocked the account once | C | S | Publish an AUP; block common P2P ports on Hetzner; route abuse mail to the owner |
| 5 | **Legal and policy gaps** (trader identity, EU withdrawal, GDPR, governing law; drafts not reviewed) | Selling to EU consumers without these is a liability | C | M | Fix the four items in `KIWI-LAUNCH-REVIEW.md` before payments |
| 6 | **Only 3 countries / 3 servers** | Leaders have 50–140 countries; buyers compare lists | C | M | Wave 1: UK, NL, SG, US-West (~$30–60/mo) |
| 7 | **Customers run on the owner's personal server** (Helsinki) | A customer-side compromise or abuse hits the owner's own VPN; the DNS still points to the personal IP | H | S | Owner runs the DNS command today; move Helsinki customers to a dedicated node |
| 8 | **Built-but-not-deployed pile** (Windows 2.1, support reports, device-limit fix, bandwidth alerts, extension 1.0.4, `proxy_host`) | Users get 262 s failovers and no support button | H | S | One release after the restore test |
| 9 | **No app-store presence on mobile** (Android sideload RC; iOS never run) | Most VPN buyers are on phones | H | M (Android) / XL (iOS) | Android: phone tests → Play. iOS: needs a company account |
| 10 | **No macOS app** | Second-largest desktop platform | H | L | Verify the manual WireGuard config path now; native app later |
| 11 | **Logging not verified end to end** (platform/proxy/journald logs) | A "no logs" claim that turns out false is worse than none | H | M | Log inventory + retention settings; then an honest policy |
| 12 | **Device limit 3, counted per server peer**, plus a 1-per-10-min device rate limit | Competitors give 5 to unlimited; switching servers burns slots | H | S | Deploy the fix; count physical devices; raise to 5+ |
| 13 | **Pricing uncompetitive** ($9.99/30 days only, manual renewal) | Long-plan competitors cost $2–4/mo | H | S | 1-year plan; position on community + anonymity, not price |
| 14 | **No Windows auto-update** | Fixes don't reach users | H | M | Build the signed updater (design exists) |
| 15 | **No external monitoring, status page or owner alerting** | The 11-05 outage was invisible except in logs | H | S | Uptime monitor + Telegram alert + public status |
| 16 | **No independent audit / open source** | Every competitor shows an audit | H | XL | After 11; start with a paid pentest |
| 17 | **Website configs generate private keys server-side** | Weaker than every competitor's client-side keys | H | S | WebCrypto X25519 in the browser |
| 18 | **No speed/latency data, no load test** | Can't promise performance or size capacity | H | S | iperf3 probes + one load test per node class |
| 19 | **No obfuscation / censorship resistance** | Matters for a UAE-based audience and MENA | M | L | WireGuard-over-TLS (wstunnel/AmneziaWG) on one node first |
| 20 | **Ad/malware blocking unverified; no IPv6 for customers** | Expected "free" extras; IPv6-only sites fail | M | S/M | Blocking resolver on all nodes; routed IPv6 pools |

Also behind, but lower: Linux, Firefox, Edge listing, post-quantum, RAM-only, multihop, dedicated IP, port forwarding, streaming, family plans, 24/7 chat.

---

## 3. Security and privacy risk assessment

| # | Risk | Likelihood | Impact | Current control | Recommended mitigation |
|---|---|---|---|---|---|
| R1 | Database loss (no good backup since 10-21) | Med | **Severe** | Secret fixed; job not re-run | Backup + restore test now; weekly restore drill |
| R2 | Provider account suspension (abuse/DMCA, billing) | **High** (already happened) | **Severe** (all servers) | None | AUP, P2P limits, second provider, abuse contact |
| R3 | Customer traffic shares the owner's personal server | Med | High | wg1 isolated from wg0; customer egress on a floating IP since 11-12; wg0 untouched | DNS change; dedicated customer node |
| R4 | Secrets once pasted in chat (old root password, earlier Hetzner token) | Med | High | Some rotated (new Hetzner token) | Confirm every pasted secret is rotated; record the rotation date |
| R5 | No-logs claim not matched by platform logs | Med | High (trust/legal) | DB stores no destinations | Log inventory, retention limits, accurate policy |
| R6 | Server-side key generation for web configs | Low | Med | Not stored, returned once | Browser-side generation |
| R7 | Control-plane compromise (Railway → agents) | Low | **Severe** (can add or remove peers on every node) | Pinned certs, tokens, 8443 allowlists (Helsinki/Germany) | Confirm the ash1 allowlist; agent tokens per node (already); Railway access with 2FA |
| R8 | Kill switch / leak failure on Android and manual configs | Med | High | Windows proven in CI only | Android device tests; label manual configs "no kill switch" |
| R9 | DNS resolver privacy (nodes default to 1.1.1.1; the Windows guard uses Quad9) | — | Low-Med | Third-party resolver sees queries from the node IP, not the user | Run a local resolver on each node (also enables ad blocking) |
| R10 | Payment webhook forgery / mis-credit | Low | Med | Signature check (both forms), reconcile job | Prove it with one real payment |
| R11 | Browser proxy reaching the server's own services | Low | Med | OWN_NETS refuses + IPv6 self-reject (Helsinki verified) | Run the same checks on ash1 and every new node |
| R12 | Supply chain (CI, signing) | Low | High | Pinned CodeSignTool hash, signer thumbprint pinned, signing gated to main | GitHub required reviewers need GitHub Pro; consider it |
| R13 | Abuse of the free trial (multiple accounts) | Med | Med (bandwidth cost, abuse) | One trial per account; device rate limits | Shorter trial; email or phone verification; per-IP trial limit |
| R14 | Legal exposure (an individual operator, draft terms) | Med | High | Drafts | Entity + reviewed terms before paid launch |

Overall: the **cryptography and client security are decent** for a WireGuard product: a signed Windows app, a WFP kill switch, local keys and pinned agents. The **operational and legal side is the weak point**: backups, provider concentration, abuse handling, logging verification and policies.

---

## 4. Roadmap to become competitive

**Phase 0: make it safe to sell (1–2 weeks)**
1. Run the backup, test the restore, then deploy the release: Windows 2.1, support reports, device-limit fix, bandwidth alerts, `proxy_host`.
2. Owner runs the Helsinki DNS change. Ashburn testers-only, if the owner still wants it (the public `/stats` currently lists Ashburn as public and "recommended").
3. Publish the AUP, block P2P ports on Hetzner nodes and set an abuse contact.
4. Make a log inventory and set retention; correct the privacy policy to match.
5. Fix the legal items (trader identity, withdrawal, GDPR, governing law).
6. Make one real test payment; then turn payments on (owner's decision).
7. External uptime monitor + owner Telegram alerts.

**Phase 1: credible paid product (weeks 3–8)**
1. Second provider. Wave 1 locations: UK, NL, SG, US-West. Move customers off the personal Helsinki box.
2. Android: phone test plan → Google Play (closed testing, then production).
3. Submit extension 1.0.4; Edge Add-ons listing.
4. Device limit 5+, counted per physical device; separate the browser-login rate limit.
5. 1-year plan; a card processor; a 7-day trial.
6. Blocking DNS resolver on every node (ads/malware), with a toggle.
7. Browser-side key generation for web configs.
8. Helpdesk inbox + FAQ; tested macOS/Linux manual-config guides.

**Phase 2: platform parity (months 3–5)**
1. Windows signed auto-update; per-app split tunneling on Windows.
2. iOS: legal entity → Apple organisation account → TestFlight → App Store (in-app account deletion first).
3. Native macOS app (shares the iOS NetworkExtension code).
4. Routed IPv6 for customers; speed/latency probes and a public status page.
5. Obfuscated transport (WireGuard over TLS/WebSocket) on 1–2 nodes.

**Phase 3: trust and differentiation (months 6–12)**
1. Third-party pentest, then a no-logs audit.
2. Open-source the clients (optional, cheap trust).
3. 10–15 countries; dedicated IP add-on; Firefox extension; Linux CLI.
4. Post-quantum PSK; RAM-only nodes.
5. Lean on the Price Chat community (free VPN for high ranks, XP) as the reason to choose Kiwi over bigger, cheaper brands.

---

## 5. Cost estimate for 100 / 1,000 / 10,000 paying customers

These are rough, monthly, USD. Assumptions:
- An average **100 GB per user per month** (heavy streamers do more).
- Peak concurrency is about 20% of users.
- About 150 concurrent users per 1 Gbps node.
- A blended price of **$7/month**: a mix of $9.99 monthly and ~$4.50/month annual.
- Hetzner list prices from 2026-11-07.
- Payment fees: about 1% (crypto) to about 3.5% (cards).
- Excluded: the owner's time, taxes, entity setup, refunds, and acquisition cost (CAC), which is shown separately.

| Cost line | 100 customers | 1,000 customers | 10,000 customers |
|---|---|---|---|
| Traffic | ~10 TB | ~100 TB | ~1 PB |
| Locations | 5–6 | 10–12 | 25–35 |
| VPN nodes | 6 small cloud VPS (EU nodes include 20 TB): **$90–150** | 12–15 nodes, mostly dedicated 1 Gbps (Hetzner AX-class EU ~$45–70; other providers in UK/US/Asia $60–150): **$1,000–1,600** | 50–70 nodes, many 10 Gbps in key cities ($300–1,000 each for premium PoPs): **$12,000–22,000** |
| Overage traffic (US/Asia allowances) | $5–20 | $100–300 | included in dedicated pricing, or $1,000–3,000 |
| IPs (floating/extra IPv4) | $5–20 | $50–100 | $500–1,000 |
| Backend (Railway share, DB, storage, bucket) | $20–50 | $50–150 | $300–800 (dedicated DB + replicas) |
| Monitoring, status page, email, helpdesk | $0–30 | $50–150 | $300–600 |
| Code signing, Apple ($99/yr), Play (one-off), domains | ~$25 | ~$25 | ~$50 |
| Payment fees | $10–25 | $100–250 | $1,000–2,500 |
| Support staff (part-time → team) | owner | $500–1,500 (part-time) | $8,000–15,000 (2–4 people) |
| Security/audit (amortised) | — | $500–1,000 (pentest ~$6–12k/yr) | $2,000–4,000 (pentest + audit) |
| **Total cost / month** | **~$160–320** | **~$2,400–5,100** | **~$25,000–49,000** |
| Revenue at $7 blended | ~$700 | ~$7,000 | ~$70,000 |
| Revenue at $9.99 (all monthly) | ~$1,000 | ~$10,000 | ~$100,000 |
| **Margin before acquisition cost** | ~55–75% | ~30–65% | ~30–65% |
| Acquisition cost (affiliates: industry says <$40 → ~$150 per paid user, very rough) | $4k–15k to acquire 100 | $40k–150k | $400k–1.5M |

What follows from this:
- **100 customers:** today's 3 nodes plus 2–3 more cover it. The margin is fine, but the absolute profit (~$500/mo) doesn't pay for support or legal work. This is a community add-on, not a business yet.
- **1,000 customers:** profitable on running costs if churn is controlled (monthly-plan churn of 5–15% per month is typical). It needs a support person and a second provider. Bandwidth heavy users are the main cost risk: consider a fair-use cap.
- **10,000 customers:** competing on network size means premium 10 Gbps nodes, where costs jump. Acquisition through paid channels at industry rates is not affordable without annual plans and low churn. The realistic growth path is the **existing Price Chat audience** (near-zero acquisition cost) rather than paid affiliates.
- **Break-even on fixed costs today:** about 7–10 paying users cover the current $47/mo of nodes plus tooling.

---

## Appendix: what was NOT verified

- Real customer counts, Admin settings (`adblock`, `testers_only`, device counts) and payment records: needs an Admin login.
- Speeds, latency and capacity under load: no measurements exist.
- Android kill switch, DNS and IPv6 on a real phone; iOS on a real iPhone; the extension with a real account in branded Chrome.
- Platform and node log contents and retention.
- Competitor facts marked *(unconf.)*, and some competitor prices, which vary by region and change often.
