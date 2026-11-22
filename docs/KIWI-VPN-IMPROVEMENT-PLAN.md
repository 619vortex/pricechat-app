# KiwiVPN improvement plan: safe, reliable, commercially ready

Date: 2026-11-12. Based on `KIWI-VPN-COMPETITIVE-ANALYSIS.md`. This is a plan only. Nothing here is deployed, no infrastructure changes and payments are not enabled without the owner's approval.

**Principle:** fix what can lose data, take the service down or create legal exposure first. Premium features (obfuscation, multihop, dedicated IP, more countries beyond wave 1) wait until items 1–8 are done.

**Costs** are rough, monthly USD unless one-off. **Effort** is engineering time: S = 1–3 days, M = 1–2 weeks, L = 3–6 weeks. **"Gate"** means it can't go live until that item is done.

---

## Summary

| # | Item | Status today | Cost | Effort | Blocked by | Impact |
|---|---|---|---|---|---|---|
| 1 | Verified backup + restore | S3 secret fixed 11-12; **no successful backup since 2026-10-21**; restore checker ready | $0 (bucket already paid) | S | Owner presses Run now (or approves deploying the retry fix) | **Unblocks every deploy**; removes the data-loss risk |
| 2 | Hosting outside Hetzner | 100% Hetzner, one account; one account block = total outage (11-05) | +$20–60/mo (2–3 nodes) | M | 1 (for backend registration), owner approval to buy | Survives a provider block; adds UK/NL/SG |
| 3 | Privacy audit of real logs | DB minimal by design; Railway, Vercel, node journald/proxy and NOWPayments retention **unverified** | $0 internal; $6–12k external pentest later | M | Read-only server access approval | An honest privacy policy; removes the "false no-logs" risk |
| 4 | Android production readiness | 0.1.0-rc1 public as "Testing"; one informal phone connect; device test plan not run; not on Play | $25 Play one-off (if no account) + 2–3 test phones (owner's) | M–L | Owner phone tests; Play account; policy pages (8) | The largest platform; store install instead of sideload |
| 5 | Windows automatic updates | Design only (`AUTO-UPDATE.md`); 2.1 built, live is 2.0 | $0 (existing signing cert + Vercel hosting) | M | Signing CI (exists); release gate 1 for the first update to ship | Fixes reach every user; required before scaling |
| 6 | Payments + subscriptions | USDT code built, **no real payment ever**; payments off; Stripe code off; manual renewal | Test payment ~$5–11; fees ~1% crypto, ~3–6% cards | S (crypto test) / M (cards, plans) | 1, 8 (terms/refunds), owner approval | First revenue |
| 7 | Device-limit fix + monitoring | Fix + bandwidth alerts + `proxy_host` **built** (release/kiwi-prod); owner alerts need a Telegram chat; no external uptime check | $0–10/mo (free uptime tier) | S | 1 (migration 0111) | Fewer "Too many devices" failures; outages noticed in minutes |
| 8 | Pricing + acceptable-use policy | $9.99/30 days only; no AUP; terms/refunds are drafts; launch-review legal gaps | $0 drafting; $500–2,000 optional legal review | S–M | Owner decisions (prices, entity, refund window) | Legal safety; protects the Hetzner account from abuse complaints |

---

## 1. Verified database backup and restore

**Current status**
- Backups failed from about 2026-10-22: the `api` S3 secret didn't match the bucket. It was fixed 2026-11-12 01:39 UTC.
- The job's next run is pushed out by daily backoff. It needs Admin › System › Background jobs › *Nightly database backup* › **Run now**, or a deploy of the backup-retry fix (`151d6b3`, no migrations), after which it runs within 15 minutes.
- The restore checker is ready: it downloads, gunzips, runs `PRAGMA integrity_check`, reads the alembic version (expect 0109) and counts key rows.
- The 12-hour watcher was stopped by the PC's low memory. Re-check by hand once the owner says it ran.

**Plan**
1. Owner presses Run now.
2. Download the newest `backups/db/*.db.gz` and run the restore check on a copy, never against production.
3. Record the timestamp, integrity, migration and row counts in the launch log.
4. Then the release deploy (items 5–7 depend on it).
5. Afterwards:
   - A **weekly automated restore test** (job: download the newest backup, check it, alert on failure). Effort S, built on a branch now.
   - An **alert when a backup fails** or is older than 26 h. Today the failure was silent for 3 weeks. Effort S.
   - A second copy outside Railway (e.g. a Backblaze B2 / Hetzner Storage Box bucket, ~$1–5/mo), so one platform account can't lose both. Needs owner approval for the new account.

| Cost | Effort | Dependencies | Impact |
|---|---|---|---|
| $0 now; +$1–5/mo for an off-platform copy | S | Owner: Run now | Critical: unblocks all deploys; ends 3 weeks without backups |

---

## 2. Hosting redundancy outside Hetzner

**Current status**
- Helsinki (shared with the owner's personal VPN), Germany (the owner's server) and Ashburn are all on one Hetzner account.
- The 2026-11-05 account block took all three down for about a day.
- The expansion plan (`docs/KIWI_VPN_EXPANSION_PLAN.md`) already lists UK and NL as second-provider locations.
- `install.sh` with AGENT_ALLOW / WITH_PROXY works on fresh Ubuntu nodes (ash1 proved it). nftables-only nodes are **untested**.

**Plan**
1. **Choose a provider** that is VPN-tolerant, has a clear abuse process, cheap bandwidth, and UK/NL/Singapore locations. Candidates to compare on price and abuse policy: OVH (unmetered), Vultr, DigitalOcean, Netcup, Datapacket. The owner chooses; a written comparison can be prepared now.
2. **First node:** one in Amsterdam or London, registered **testers-only**. Selftest, proxy tests, kill-switch e2e from CI, and the IPv6 self-reject check.
3. **Second Hetzner project or account** for customer nodes, so a block on the personal project doesn't hit customers (and the reverse).
4. **Move Helsinki customers to a dedicated customer node.** After that, wg1 on the personal server can be retired once all devices are migrated, using the existing "known servers" failover so clients move on their own.
5. Keep each node's DNS name per provider. The apps already fail over between servers.

| Cost | Effort | Dependencies | Impact |
|---|---|---|---|
| +$20–60/mo for 2–3 small nodes; more for Singapore bandwidth | M | Owner picks provider + approves each purchase; backend deploy of `proxy_host` (item 1 gate) for latency ranking | Critical: no single account can take the service down; adds 1–3 countries |

---

## 3. Privacy audit of actual server, API and platform logs

**Current status**
- **Database:** stores the account, each device's public key, tunnel IP, last handshake, monthly bytes, browser-proxy usernames (hashed secret) and payments. Anonymous payments are scrubbed.
- **Unverified:**
  - Railway HTTP/request logs: client IPs, paths with device ids, retention.
  - Vercel access logs.
  - Node `journald`: agent, proxy (stderr), `sshd`, `ufw` logs that may record customer IPs.
  - WireGuard itself keeps the last endpoint (customer IP) in memory while a peer is up.
  - NOWPayments' and Google sign-in's own data.
  - Support reports: redacted, but they include the app log tail.

**Plan**
1. **Code review (now, read-only):** list every log call in the backend VPN routes, agent, proxy and apps, and whether it can include an IP, a destination or an identity. Output: a table "source → fields → retention → needed?".
2. **Platform review (now, read-only):**
   - Railway log retention for the plan.
   - Vercel log retention.
   - What the request logger prints (e.g. uvicorn access logs include client IPs).
3. **Node review (needs approval for read-only SSH):**
   - `journalctl` sizes and retention, `ufw` logging level, and whether the proxy logs connections.
   - That `/etc/wireguard` and the agent hold no history.
4. **Fixes, each on a branch, each needing deploy approval:**
   - Turn off or anonymise access logs for `/api/members/vpn/*`.
   - Volatile journald (`Storage=volatile`, short `MaxRetentionSec`) on customer nodes.
   - `ufw logging off` on customer nodes.
   - The proxy never logs destinations.
   - A support-report retention period.
5. **Rewrite the privacy policy** to match exactly what is kept and for how long.
6. **Later:** a paid third-party pentest, then a no-logs audit, only after the above is true.

| Cost | Effort | Dependencies | Impact |
|---|---|---|---|
| $0 internal; $6–12k pentest later; audit more | M | Owner approval for read-only SSH and later for config changes on nodes | High: makes the privacy claim true and defensible |

---

## 4. Android production readiness

**Current status**
- `0.1.0-rc1` (signed with the Kiwi release key) is public at `/downloads/KiwiVPN-0.1.0-rc1.apk` with a "Testing" label.
- The owner reported one connect to Germany and Finland. No logs, and `DEVICE-TEST-PLAN.md` / `RC-TEST-CHECKLIST.md` have not been run.
- **Open risks:**
  - the `systemExempted` foreground service on Android 14+;
  - "Allow restricted settings" for notification access on sideloaded builds;
  - the `adblock` naming against Google's VPN ads rule;
  - Auto-Call may block Play approval;
  - Gradle dependency hash verification is not on.
- The release key needs an owner backup.

**Plan**
1. **Now (no deploy):**
   - Add Gradle dependency verification.
   - Remove or disable Auto-Call for the Play build.
   - Run the emulator tests on the `kiwi36` AVD.
   - Write a one-page phone test script covering: connect, kill switch via "Always-on + block", DNS and IPv6 leak, app-based routing, network change Wi-Fi↔mobile, reboot, and server switch.
2. **Owner:** run that script on 2–3 phones (ideally Android 12, 14, 16) and send the app's diagnostics.
3. **Play:**
   - Developer account and the Data safety form (needs the item 3 results).
   - VPN service declaration (Play requires VpnService apps to declare and justify it).
   - Privacy policy URL and an account deletion URL (Play requires one).
   - Then closed testing (Play needs 12+ testers for 14 days on new personal accounts), then production.
4. Add in-app account deletion or a deletion link. It's needed for Play now and iOS later.

| Cost | Effort | Dependencies | Impact |
|---|---|---|---|
| $25 Play one-off; test phones (owner's) | M–L (mostly waiting on the testing period) | Owner phones; Play account; privacy policy from item 3; AUP/terms from item 8 | High: the largest platform; store distribution |

---

## 5. Windows automatic updates

**Current status**
- **Design only** (`windows-client/docs/AUTO-UPDATE.md`).
- Live is 2.0.0 (no updater). 2.1.0 is signed and green in CI, but not published.
- Every user must re-download each release.

**Plan**
1. Build the signed updater on a branch:
   - Tauri updater with a minisign/ed25519 update key, kept separate from the code-signing cert.
   - The manifest is served from `pricechat.live/downloads/` (Vercel), and the app checks daily.
   - The installer must carry the expected Authenticode thumbprint (`0C2483…3559`). The updater refuses anything else.
   - The update runs the service upgrade the same way the 0.1.1 → 2.0 upgrade job does.
2. CI:
   - An `update-test` job: install the previous version, serve a test manifest, update, then check service, tunnel and kill-switch.
   - Negative tests: wrong signature, wrong thumbprint, downgrade, and a truncated file.
3. **Bootstrapping limit:** 2.0 has no updater, so existing users must install the first updater-capable version by hand. That should be 2.2. Ship it with an in-app "new version available" banner driven by `/api/members/vpn/public`.
4. Publishing needs owner approval. The update key is generated on the owner's machine, never pasted in chat.

| Cost | Effort | Dependencies | Impact |
|---|---|---|---|
| $0 | M | CI signing (exists); release gate (item 1) to ship 2.1 first | High: every later fix reaches users |

---

## 6. Payment testing and subscription management

**Current status**
- NOWPayments USDT (ERC20/TRC20/SOL) is built. The IPN signature is unproven, and **no real payment has ever been made**.
- Payments are off. Pricing is $9.99 = 30 days pro-rata, renewed by hand.
- The `vpn.reconcile` / `vpn.reminders` jobs exist.
- Stripe card subscription code exists but is disabled (`CARD_SUBSCRIPTIONS`).
- Admin has credit, refund, check and block actions.

**Plan**
1. **One real crypto payment in test mode**, after the release deploy and with owner approval: about $5 on Solana (TRC20's minimum is ~$10.95). Verify:
   - the IPN signature form;
   - credit and time added;
   - the reconcile job;
   - the receipt and reminder emails;
   - the anonymous-account scrub;
   - the refund path in Admin.
2. **Plans (code now, off):** 30 days $9.99, 6 months and 1 year (suggested ~$49–59). Make the amount fixed per plan, not pro-rata, as the owner may prefer. Add an expiry-reminder test.
3. **Cards:** decide between
   - **Stripe**: needs a supported entity/country for the operator, and the merchant handles VAT;
   - a **merchant-of-record** such as Paddle or Lemon Squeezy, which handles EU VAT and refunds and takes ~5% + $0.50, but **must confirm they accept VPNs**.

   Recurring billing only with cards, and only if the owner lifts the "manual renewal" rule.
4. **Subscription management page:** current plan, expiry, renew, payment history (exists for crypto), cancel auto-renew (cards), and a refund request.
5. **Do not enable public payments** until items 1, 3 (policy text) and 8 are done and the owner approves.

| Cost | Effort | Dependencies | Impact |
|---|---|---|---|
| ~$5–11 test; fees ~1% crypto, ~3–6% cards | S (test) / M (plans + cards) | Item 1 deploy; item 8 terms/refunds; owner approval; for cards, an entity decision | Critical for revenue |

---

## 7. Device-limit fix and monitoring

**Current status, all built and in `release/kiwi-prod`, not deployed:**
- **Device-limit fix** (`fix/vpn-device-limit`).
- **Bandwidth alerts at 80/100% of each provider allowance** (`feat/kiwi-monitoring`, migration 0111).
- **`live_load` + `proxy_host`** in the member server list.
- **Flap guard** (`fix/kiwi-stage0` `31c6148`): built but **not in the release branch**. Merge it before the release deploy, or ship it next.

**Remaining problems**
- **Limit counting:** the limit is 3 per account, and a device counts **per server**.
- **Shared rate limit:** the `vpn_device` rate-limit bucket (5, then 1 per 10 min) is shared with trial start and every browser-extension login.
- **Alerting:** owner outage alerts only reach logs unless a Telegram chat is set. There's no external uptime check or status page.

**Plan**
1. Deploy with the release (gate: item 1).
2. On a branch now:
   - Count a **physical device** (one install = one slot across servers). Set the default limit to 5.
   - Give browser logins their own rate-limit bucket.
   - Alert after N consecutive failed syncs and when a backup is stale (item 1).
3. Owner sets the alert Telegram chat in Admin.
4. External uptime monitor on the API, each proxy (TCP 443), each WireGuard port (UDP can't be checked externally, so use the agent-reported `last_seen`) and the website. Free tier of a hosted monitor, or a check from the second provider's node.
5. A simple public status page fed by `/api/members/vpn/stats` (once uptime is filled in).

| Cost | Effort | Dependencies | Impact |
|---|---|---|---|
| $0–10/mo | S | Item 1 for the release; owner sets the Telegram chat and approves the monitor account | High: fewer failed connects, outages seen in minutes |

---

## 8. Clear pricing and acceptable-use policies

**Current status**
- **Pricing and terms:** $9.99/30 days only. `/vpn/terms` and `/vpn/refunds` are drafts.
- **Legal gaps** (from `KIWI-LAUNCH-REVIEW.md`):
  - trader identity and contact email;
  - EU 14-day withdrawal consent;
  - governing law, liability and age;
  - GDPR basics.
- **No acceptable-use policy.** Nothing limits P2P, and abuse complaints go to the Hetzner account holder.

**Plan**
1. **Acceptable-use policy (draft now):**
   - no illegal content or abuse;
   - P2P allowed only where stated (or not at all at first);
   - no spam, scanning or attacks;
   - the account is suspended on a verified abuse report;
   - name what we can and cannot see (ties to item 3).
2. **Technical enforcement on Hetzner nodes** (needs approval): block common BitTorrent ports and outbound SMTP 25, and rate-limit new connections per peer. Send abuse mail to a monitored inbox.
3. **Pricing page:**
   - monthly / 6-month / 1-year, all in one place;
   - device limit, countries and payment methods;
   - the refund window (suggest 7 days for crypto, matching the EU withdrawal rules with an explicit "start now" consent);
   - **no** "unlimited", "no logs" or "unblocks streaming" claims until verified.
4. **Terms:** fill the four legal gaps. Optional lawyer review ($500–2,000). Decide on a legal entity, which also unblocks iOS and cards.

| Cost | Effort | Dependencies | Impact |
|---|---|---|---|
| $0 drafting; $500–2,000 optional review | S–M | Owner decisions: prices, refund window, entity, contact identity | Critical before paid launch; protects the hosting account |

---

## What can be built safely in parallel while the backup blocks deployment

**Safe now**: branch-only code, documents, CI and read-only checks. Nothing deployed, no infrastructure touched.

| Work | Item | Why it's safe |
|---|---|---|
| Weekly restore-test job + "backup stale" alert (code, tests) | 1 | Branch only; deploys with the next release |
| Provider comparison (price, VPN/abuse policy, bandwidth, locations) | 2 | Paper only; no purchase |
| Node install check on a clean distro in CI (Docker Ubuntu) for nftables-only providers | 2 | CI only |
| Log inventory from code and platform docs; draft privacy-policy rewrite | 3 | Read-only |
| Log-minimising changes (access-log filter, journald/ufw settings in `install.sh`) | 3 | Branch only; node changes need approval to apply |
| Android: Gradle verification, Auto-Call off for Play, emulator tests, phone test script, Data safety draft, account-deletion link | 4 | Branch + emulator; no publish |
| Windows updater + `update-test` CI job + negative tests | 5 | CI on the KIWIVPN branch; nothing published |
| Fixed-price plans, subscription page, reminder tests (payments stay off) | 6 | Branch only; behind the existing switch |
| Physical-device counting, separate browser rate limit, sync-failure alerts | 7 | Branch only |
| AUP, pricing page, terms/refunds fixes (drafts for owner review) | 8 | Text on a branch |

**Must wait for the backup gate:**
- The release deploy (Windows 2.1, support reports 0110, traffic allowance 0111, device-limit fix, `proxy_host`).
- Every later backend deploy and the real test payment.

**Needs owner action or approval regardless of the backup:**
- Backup Run now.
- The Helsinki DNS change.
- Read-only SSH to nodes.
- Buying a second provider or a second Hetzner project.
- Phone tests and the Play account.
- Price and refund decisions; the legal entity.
- The Telegram alert chat.
- Enabling payments.
- Ashburn access: it currently shows as public and "recommended" in `/stats`.

---

## Suggested order and timeline

| Week | Engineering (branches, CI) | Owner decisions and actions | Goes live (with approval) |
|---|---|---|---|
| 0 (now) | Restore job, log inventory, AUP/pricing drafts, device counting, Android hardening | Run now; DNS command; Telegram chat | — |
| 1 | Windows updater + CI; plans code; provider comparison | Choose provider; approve read-only SSH; prices + refund window | Release (after restore passes): 2.1, support, device-limit, monitoring |
| 2 | Log-minimising changes; first non-Hetzner node install scripts | Approve node purchase; phone tests | Non-Hetzner testers-only node; log fixes; AUP + pricing pages |
| 3–4 | Test payment support; status page; Play listing assets | Approve test payment; Play account | Real ~$5 payment; Android closed testing |
| 5–6 | Windows 2.2 with updater; customer node off the personal server | Approve the customer node | 2.2; customer migration; payments **only if** the owner approves |
| 7–8 | Fixes from Android testing; card provider integration if chosen | Entity / card decision | Android production; plans |

**Cash cost for items 1–8 combined:** about **$50–100/mo** extra (nodes, off-platform backup, monitor), plus **~$30–40 one-off** (Play account, test payment). Optional: legal review $500–2,000; a pentest $6–12k later.
