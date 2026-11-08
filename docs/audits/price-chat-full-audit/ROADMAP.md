# Roadmap: 7 days, 30 days, 90 days, 6 months

*Order of work: keep the business alive and safe first, then make it observable, then make it earn, then grow it. Finding IDs refer to QA-FINDINGS.md and SECURITY-REVIEW.md. Effort: S = under a day, M = 1-3 days, L = a week or more (one engineer working with AI).*

## Principles
1. **Stop the bleeding before building.** Backups, alerts, account takeover and leaked secrets come first.
2. **Finish before starting.** Unmerged branches (17), the undeployed Telegram trading module, the payments branch and the monitoring branch get finished or closed before new products start.
3. **One revenue line at a time.** Telegram trading inside the referral first, then VPN payments.

---

## Next 7 days: safety and visibility

| Day | Task | IDs | Effort | Depends on | Done when |
|---|---|---|---|---|---|
| 1 | Set the owner alert Telegram chat; add a "nothing published / ingested for 6 h" alert and a "backup older than 26 h" alert | AR-2, ND-2, SEC-7 | S | — | A test alert reaches the owner's Telegram |
| 1 | Fix the backup bucket credentials (rotate the bucket keys, which were exposed in the breach) and run one backup; download a manual copy via /api/system/db-backup first | AR-1, SEC-7 | S | owner (Railway variables) | A dated object in the bucket; restore tested on a laptop |
| 1-2 | Telegram sign-in: the bot asks "Sign in on this device? Yes / No"; turn off the widget route in deeplink mode | SEC-4 (TG-1/PP-1), SEC-1 | S-M | — | Phishing test (attacker link, victim taps Start) no longer signs anyone in |
| 2 | **Rotate the Telegram bot token** in BotFather and the encryption key (APP_ENCRYPTION_KEY + _PREVIOUS, rekey); set PROXY_SHARED_SECRET (Vercel first, then Railway) | SEC-1, SEC-2, SEC-3 | S-M | owner | Startup log shows "Rekey ... failed=0" and "Website traffic carries the proxy secret" |
| 2 | Cap the wallet XP reward at a fixed amount (no "enough for the next rank"); rate-limit wallet sign-in per wallet and per account | PP-2, PP-4, SEC-6 | S | — | Audit exploit tests flip to fail |
| 3 | Confirm the News Desk fix in production (deployed a93c797a), then recalibrate: fixed news bar of about 52 or best-of-window pick; hold market candidates for the window; add CoinDesk, Blockworks and regulator feeds; feed staleness check | ND-1 (fixed), ND-3, ND-4 | M | — | Replay of a normal week gives 3-4/day on weekdays; channel posts 2+ real items/day |
| 4 | Add a build SHA to /api/health/public; deploy only from a clean git commit (script or CI) | AR-7, AR-8 | S | — | Health shows the commit; a deploy from a dirty tree is refused |
| 5 | VPN: decide payments (NOWPayments test with $1, then on) and the trial-end message; rebase feat/kiwi-payments (migration renumbering) | VPN-1, VPN-11 | M | owner (payment account) | One real payment extends a test account; trials ending 12-04 see a working pay button |
| 6-7 | Trading safety: alert when a stop and its emergency close both fail; widen close slippage to 2-5%; cache the Hyperliquid client per account | TR-1, TR-3, TR-2 | M | — | Tests for each; a fake exchange refusing both raises an owner alert |

**7-day success metrics:** a working backup; alerts reach the owner; zero known account-takeover paths; channel posting again (2+ items a day); VPN payable.

---

## Next 30 days: finish what's half-built

| Task | IDs | Effort | Done when |
|---|---|---|---|
| Move the Telegram listener out of the global job lock; give heavy commands a per-user limit; retry after 429 instead of dropping | AR-3, TG-2, TG-3, TG-4 | M | Bot replies within 2 s while News Desk or the Pokémon sync runs |
| Pokémon market cache refresh-ahead (warm-up before TTL) | AR-6 | S | No 10 s+ requests at the 15-minute marks for 48 h |
| SEO: serve per-page meta to all crawlers (vercel.json); absolute OG image URLs; prune thin sitemap pages | UX-1, UX-2, UX-19 | S-M | Google's URL inspection shows the real title for a card page |
| Home page: one clear hero CTA, nav links to the market and the VPN for signed-out visitors | UX-3, UX-4, UX-5 | S | Join button above the fold on 390px |
| Legal: final privacy policy (wallets, trading keys, emails, photos, VPN, chat feed), terms, VPN acceptable-use, company name and support email | UX-9, SEC-12, VPN-10 | M | No "Draft" label; privacy page matches the data map |
| Telegram trading: fix TR-4 (LIVE gate on manual opens), TR-6 (testnet prices from testnet), owner decision on TR-5 (gate on the ENTER button); deploy on PAPER/TESTNET; run a real funded testnet trade | TR-4, TR-5, TR-6 | M | A testnet /open → SL/TP resting on Hyperliquid testnet → /close, recorded |
| VPN device model: one device per physical device, server change in place; separate rate-limit buckets per action; extension backoff | VPN-2, VPN-3 | M | A PC visiting all 3 servers uses 1 slot |
| Merge or close every unmerged branch (17); remove stale worktrees (29) | AR-16 | M | `git branch --no-merged main` is short and intentional |
| Migrate Railway config-as-code before 2026-12-01 | AR-9 | S | Healthcheck and restart policy still active after the cutoff |
| Admin: jobs disabled in production shown as such; admin sessions invalidated on password change; audit log for login, DB download and VPN admin actions; confirm on the emergency stop | AR-5, AR-10, AR-11 | M | Tests per item |
| Kiwi Windows 2.0: fix the window failover test method (packet-layer block or blackhole route) and get it green; buy an OV code-signing certificate (cloud HSM); signed CI build | VPN-7, Windows CI | M | Signed installer passes Smart App Control on the owner's PC; failover test green |
| XP: put packs, streaks and self-reported Earn steps under the daily cap with a lock; fix the referral self-check and the same-browser flag | PP-3, PP-5, PP-6, PP-8 | M | Exploit tests fail |

**30-day metrics:** referred trading volume per week (baseline it now); bot p95 reply time under 3 s; Google indexing real titles; VPN paying users ≥ 5; 0 Critical/High findings open.

---

## Next 90 days: earn and retain

| Theme | Work | Metric |
|---|---|---|
| Revenue: trading | "Trade this" button on charts and signals (FEATURE-IDEAS #1); mainnet with approval after a testnet soak; investigate Hyperliquid builder codes with disclosure | Referred volume +X%/month (set after the baseline) |
| Trust | Public verified signal record (#2); status page (#10) | Record live with ≥ 30 signals |
| Retention | Morning brief DM (#3), alerts from any chart (#4), P&L leaderboard (#8) replacing click-XP as the status signal | D7 / D30 retention of bot users |
| VPN | Annual plan, signed Windows 2.0 with an update check, second VPN provider, customers off the personal VPS | Paying users, churn, incidents |
| Market | Pokémon price alerts + affiliate links | Organic sessions, affiliate clicks |
| Platform | Postgres (or at least WAL tuning + job isolation) once the job count or traffic grows; CI with tests on every push | No lock waits > 5 s in logs |

## 6 months: grow on a stable base

- Decide the portfolio: keep crypto trading + Telegram as the core; run the Pokémon market as a separate brand if it earns; keep the VPN only if paid conversion beats its maintenance cost (4 client platforms is a lot for one owner).
- Paid tier (signals or pro tools) only after 3+ months of public record.
- Telegram Mini App for trading and positions.
- Partnerships: TCG creators, crypto newsletters, wallet privacy bundles.

## Dependencies at a glance
- Backups → before any migration (payments, monitoring branches).
- Secret rotation → before scaling trading.
- Sign-in confirm step → before promoting Telegram sign-in or trading.
- Track record → before paid signals or copy-trading.
- Signing → before shipping Windows 2.0.
