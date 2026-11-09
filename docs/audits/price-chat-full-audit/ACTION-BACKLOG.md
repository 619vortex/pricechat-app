# Action backlog

*Ranked engineering tasks. P0 = this week (safety/revenue at risk), P1 = this month, P2 = this quarter. Each task cites the finding it closes; details and evidence are in QA-FINDINGS.md / SECURITY-REVIEW.md. "Owner" marks steps only the owner can do (accounts, secrets, payments, approvals).*

**Status 2026-11-09.**
- **Done on branches** (waiting for approval to deploy):
  - #4 and #5 (route): fix/audit-auth.
  - #8: fix/audit-passport.
  - #11 and #13: fix/audit-trading.
  - #14 (TR-4/6/11/13/14/16): feat/tg-trading.
  - #2, #15 (TG-4/5/6/10), #17 (build id) and #24 (AR-5/AR-10): fix/audit-ops and fix/audit-auth.
  - #19 and #20: fix/audit-web.
  - #23 (PP-6, PP-3 part): fix/audit-passport.
  - New: RV-1 to RV-15 on fix/rules-hotfix.
- **Deployed:** #9, the News Desk six-slot schedule, with feeds and staleness detection (eacfdb8c).
- **Owner-only and still open:** #1, #3, #5 (token), #6, #7 and #10.

| # | P | Task | Closes | Effort | Acceptance criteria |
|---|---|---|---|---|---|
| 1 | P0 | Set the owner alert Telegram chat (Admin) and verify | AR-2 | S · Owner | Startup self-check no longer warns; a forced test alert arrives in Telegram |
| 2 | P0 | Silence monitors: News Desk "no story ingested in 3 h" / "no post in 12 h", backup age > 26 h, Telegram listener lag > 60 s | ND-2, AR-1, TG-2 | S | Each monitor has a test; triggering each sends one owner alert (deduplicated) |
| 3 | P0 | Repair the backup: rotate bucket keys, fix the SignatureDoesNotMatch, run once, test a restore locally | AR-1, SEC-7 | S · Owner | A backup object exists with today's date; restore boots locally with row counts matching |
| 4 | P0 | Telegram deeplink sign-in/link requires an in-bot "Yes, sign in on <device/city>" confirmation; single-use code bound to the confirming user; expiry ≤ 5 min | SEC-4 / TG-1 / PP-1 | M | New test: attacker-created code + victim Start without Yes → no session; Yes → session for the victim's own browser only |
| 5 | P0 | Disable `POST /api/members/auth/telegram/widget` when the mode is deeplink; rotate the bot token | SEC-1 | S · Owner (token) | Route returns 404/403 in deeplink mode (test); the new token is in Railway; the old token is revoked in BotFather |
| 6 | P0 | Rotate APP_ENCRYPTION_KEY with _PREVIOUS and run the rekey; then remove _PREVIOUS | SEC-2, TR-10 | S · Owner | Log "Rekey: N re-sealed, failed=0"; old backups are treated as compromised (purge after re-key) |
| 7 | P0 | Set PROXY_SHARED_SECRET on Vercel then Railway | SEC-3 / AR-13 | S · Owner | Log "Website traffic carries the proxy secret"; a spoofed X-Forwarded-For no longer resets per-IP limits (test) |
| 8 | P0 | Wallet XP reward: a fixed amount (e.g. 100), once per member and once per wallet ever; wallet sign-in rate limit keyed by wallet + account, not User-Agent | PP-2, PP-4, SEC-6 | S | Audit tests audit_passport_exploits (wallet cases) now fail as expected; Knight + wallet ≠ Royal |
| 9 | P0 | News Desk: confirm a93c797a healthy; recalibrate release (fixed news bar ~52 or best-of-window; market candidates live for the window); add CoinDesk, Blockworks, DOJ/CFTC/Treasury feeds; staleness check (newest item older than 7 days = feed problem) | ND-1, ND-3, ND-4 | M | Replay of 7 normal days: 3-4 posts per weekday, ≥ 1 per weekend day, no filler; a stale feed flagged in Admin |
| 10 | P0 | VPN payments: rebase feat/kiwi-payments onto main (renumber migrations after 0105), one real $1 NOWPayments test, enable before 2026-12-01 | VPN-1, VPN-11, UX-6 | M · Owner | A real payment extends a test account; a trial-expired account sees a working Pay button; migration chain is linear |
| 11 | P0 | Trading: an owner alert plus a per-member DM when a position has no stop after the emergency close fails; reconcile retries the stop every minute | TR-1 | M | Fake exchange refusing stop and close → alert + DM + retry visible in the test |
| 12 | P1 | Trading: one cached Hyperliquid client per account (no meta re-fetch per action); preview rate limit per member | TR-2 | M | Test counts info calls per action ≤ 1 after warm-up |
| 13 | P1 | Trading: close/emergency close IOC limit at 2-5% from the mark (reduce-only) | TR-3 | S | Test: a close during a 1% gap fills on the fake exchange |
| 14 | P1 | feat/tg-trading: LIVE manual opens call check_live_permission + live_enabled (TR-4); testnet orders priced from testnet mids and testnet meta (TR-6); owner decides the ENTER-button gate (TR-5); deploy PAPER/TESTNET | TR-4, TR-5, TR-6 | M · Owner (decision) | audit_tg_trading_live_gate.py flips; a testnet entry uses testnet mid; a real testnet trade with SL/TP recorded |
| 15 | P1 | Telegram listener as its own always-on task (not under the job lock); per-user limits for /chart, /signal, /scan; Telegram 429 retry-after honoured; edits don't re-run commands; only allow-listed groups get answers | AR-3, TG-2 to TG-6 | M | audit_scheduler_serial_starvation and audit_telegram_bot tests flip; p95 reply < 3 s under a News Desk run |
| 16 | P1 | Pokémon market cache refresh-ahead (warm every 600 s, TTL 900 s, never rebuild on a request path) | AR-6 | S | No public request > 5 s in 48 h of logs |
| 17 | P1 | Build identifier in /api/health/public; deploy script that refuses dirty trees and records the SHA; GitHub Action running backend + frontend tests on push | AR-7, AR-8 | M | Health shows the commit; CI badge green |
| 18 | P1 | Railway config-as-code migration before 2026-12-01 | AR-9 | S | Healthcheck/restart policy verified after migrating |
| 19 | P1 | SEO: vercel.json serves the per-page meta to all crawlers; absolute OG image URLs | UX-1, UX-2 | S | `curl -A Googlebot` on a card page returns its own title and canonical |
| 20 | P1 | Home and navigation: above-the-fold CTA; market and VPN in the nav for everyone; remove the 9+ chat badge for first visits | UX-3, UX-4, UX-5, UX-11 | S | 390px screenshot shows the CTA without scrolling |
| 21 | P1 | Legal pack: final privacy policy (data map), terms, VPN acceptable-use policy, company and support contact; disclose the referral | UX-9, SEC-12, VPN-10 | M · Owner (legal) | No "Draft" label; every collected data type listed |
| 22 | P1 | VPN device model: one device per physical device with server change in place; separate rate-limit buckets (trial, device, extension login); extension login backoff | VPN-2, VPN-3 | M | audit_vpn_limits tests flip; one PC across 3 servers = 1 slot |
| 23 | P1 | XP integrity: packs/streak/self-reports inside the daily cap with a DB-level lock or atomic increment; referral self-check survives sign-out; same-browser flag persists; suspended members hidden from leaderboards | PP-3, PP-5, PP-6, PP-8, PP-13 | M | Parallel-request test pays ≤ cap |
| 24 | P1 | Admin: jobs off in production shown as "off on this server"; sessions invalidated on password change; audit log for login, DB download, API keys, VPN admin; confirm on the global emergency stop | AR-5, AR-10, AR-11 | M | Tests per item |
| 25 | P1 | Kiwi Windows 2.0: window failover test via a packet-layer block (works on existing flows); OV code signing in CI; update check | VPN-7, VPN-8 | M · Owner (certificate) | Signed build runs with Smart App Control on; failover test green |
| 26 | P1 | Branch hygiene: merge or close the 17 unmerged branches; delete stale worktrees | AR-16 | M | ≤ 3 unmerged branches, each with an owner and a date |
| 27 | P1 | Dependency hygiene: lock file (pip-tools/uv); upgrade Pillow and python-multipart; pytest out of production deps | SEC-8, SEC-9 | S | pip-audit shows no High; production image pinned |
| 28 | P2 | Retire or hide Lunch Bot (public), Paper Exchange writer, Kalshi/Realm/Content Studio jobs not earning | AR-18, UX-14 | S each | Job count in production down; Earn leads with a real record |
| 29 | P2 | Public verified signal record page | UX-14 | M | ≥ 30 signals with outcomes, append-only |
| 30 | P2 | "Trade this" button under charts and signals (testnet, then mainnet with approval) | FEATURE-IDEAS #1 | M | Weekly referred volume tracked in Admin |
| 31 | P2 | VPN infrastructure: customers off the personal Helsinki VPS; second provider; Ashburn data cap or traffic alert | VPN-4, VPN-5, VPN-6 | M · Owner (spend) | No customer on the personal VPS; a provider outage leaves ≥ 1 country up |
| 32 | P2 | Morning brief DM + alerts from any chart | FEATURE-IDEAS #3, #4 | M | D7 retention measured before/after |
