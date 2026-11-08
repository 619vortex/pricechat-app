# Price Chat audit: test evidence

Audit of 2026-11-08. This appendix collects every command and test run listed in the reviewers' "Commands run" sections, together with the facts the coordinator verified. Unless stated otherwise, code was read and tests run at the audit worktree `C:\AI\pc-wt\audit` (branch `audit/full-2026-11` = main **e978cc3**).

---

## 1. Environments checked

| Environment | How it was touched | Rules |
|---|---|---|
| Audit worktree `C:\AI\pc-wt\audit` (= main e978cc3) | Code reading; targeted backend tests; frontend `npm ci`, tsc, vite build, vitest | New files only, as `backend/tests/audit_*.py` |
| Branch worktrees | `C:\AI\pc-wt\tg-trade` (feat/tg-trading, 8d14a7c); `C:\AI\pc-wt\kiwi-win2` (feat/kiwi-windows-2, 334f45d); `C:\AI\pc-wt\kiwi-android` (d0a051c); git show/diff on feat/kiwi-payments, feat/kiwi-monitoring, feat/kiwi-infra, fix/kiwi-stage0, feat/kiwi-ios-cloud | Read only; tests run in tg-trade |
| Production website https://pricechat.live | Public GETs only: no login, no form submits, no load tests | BRIEF hard rules |
| Production API https://api.pricechat.live | Public GETs and HEADs only | BRIEF hard rules |
| Railway production (service `api`) | `railway logs`, `railway deployment list` and `railway status` (all read only). No `railway variables`, ssh or up by reviewers | BRIEF hard rules |
| Hyperliquid | Read-only info calls on mainnet and testnet (TR). A real testnet probe with a throwaway unfunded key (coordinator, below) | No live trades |
| Telegram channel | `curl https://t.me/s/Pricechatupdates` (public page) | — |
| VPN | DNS lookups; downloads of the live binaries; public `/api/members/vpn/*` GETs. No server was touched | — |
| GitHub Actions, 619vortex/KIWIVPN | Run 42479850219 (coordinator-verified) | Reviewers did not use the GitHub token |

**Test shim.** Application Control on this PC blocks SQLAlchemy's compiled `.pyd`, so backend tests ran from `C:\AI\pc-wt\audit\backend` with:

```
PYTHONPATH="<scratchpad>/pyshim" C:/AI/price-chat/backend/.venv/Scripts/python.exe -m pytest <tests> -q -p no:cacheprovider
```

---

## 2. Full backend suite (coordinator-verified)

| Code | Result | Failures |
|---|---|---|
| main **e978cc3** | **1951 passed, 4 failed** | All 4 pre-existing (listed below) |
| feat/tg-trading | **1972 passed, the same 4 failed** | Same 4. `tests/test_tg_trading.py`: **22 passed** |

The 4 pre-existing failures on main are not regressions:
1. `test_community::test_agent_receives_labelled_relevant_community_context`
2. `test_lunch_bot::test_the_public_page_shows_paper_results_and_the_assumptions`
3. `test_trading_execution::test_referral_states_come_from_hyperliquids_record_and_no_longer_gate_bot_trading`. The trading reviewer found this is a **real policy mismatch, not a flaky test** (TR-9): `eligibility` no longer honours the owner's min rank and deposit settings.
4. `test_trends::test_trends_api_lists_and_makes_a_video_once`

**Frontend (UX):** `npx tsc -b` exit 0; `npx vitest run` gave **460 passed (50 files)** in 11.96 s; `npx vite build` OK in 3.3 s.

---

## 3. Targeted test runs by area

| Area | Command (from the notes) | Result |
|---|---|---|
| Telegram (TG) | `pytest tests/test_bot_commands.py tests/test_fc_vs_signal.py tests/test_telegram.py tests/test_telegram_account.py tests/test_committee_telegram.py tests/test_price_alerts.py tests/test_vortex_assist.py tests/test_invites.py tests/test_invites_safety.py tests/test_secrets_telegram_setup.py -q` | **265 passed** |
| Telegram, branch (tg-trade worktree) | `pytest tests/test_tg_trading.py tests/test_bot_commands.py tests/test_telegram.py -q` | **73 passed** |
| Telegram audit | `pytest tests/audit_telegram_bot.py` | **5 passed** |
| Trading (TR) | `pytest tests/test_trading_bot.py tests/test_trading_execution.py tests/test_trading_execution_safety.py tests/test_trading_live_recovery.py tests/test_trading_os.py tests/test_trading_qa.py` | **118 passed, 1 failed** (the known `test_referral_states_…`, TR-9) |
| Trading, branch (tg-trade worktree) | `pytest tests/test_tg_trading.py tests/test_trading_execution.py tests/test_trading_os.py tests/test_trading_execution_safety.py tests/test_trading_live_recovery.py` | **100 passed, 1 failed** (same known test). All of `test_tg_trading.py` passes |
| Trading audit | `pytest tests/audit_trading_tr_checks.py -s` | **2 passed** |
| Trading audit, branch | `pytest -p tests.conftest <scratchpad>/tgrun/test_audit_live_gate.py`, run from tg-trade/backend (source kept at `audit/backend/tests/audit_tg_trading_live_gate.py`) | **1 passed** (LIVE manual open with `live_enabled_at=None` filled) |
| News Desk (ND) | `pytest tests/test_newsdesk.py tests/test_newsdesk_events.py tests/test_newsdesk_telegram.py tests/test_x_social.py tests/test_brand_publishing_safety.py -q` | **188 passed** (75 s) |
| News Desk audit | `pytest tests/audit_newsdesk_collect_wedge.py -q` | **1 passed** (bug reproduced on e978cc3) |
| News Desk fix (coordinator) | `tests/test_newsdesk_collect.py` + newsdesk tests on commit 5616ecd | **2 passed + 146 newsdesk tests passed** |
| Passport (PP) | `pytest tests/audit_passport_exploits.py -q -p no:cacheprovider -s` | **9 passed** (prints "parallel awards paid 30 XP against a daily cap of 10 (30 awards)") |
| Kiwi VPN backend | `pytest tests/test_vpn*.py -q` | **116 passed** |
| Kiwi VPN node | `vpn-node: pytest test_agent.py test_proxy.py -q` | **120 passed** |
| Kiwi VPN extension | `vpn-extension: node --test lib.test.js` | **10 passed** |
| Kiwi VPN audit | `pytest tests/audit_vpn_limits.py -q` | **3 passed** |
| Architecture (AR) | `tests/audit_jobs_server_disabled.py`, `tests/audit_admin_session_survives_password_change.py`, `tests/audit_scheduler_serial_starvation.py`, `tests/audit_newsdesk_collect_wedge.py` | All pass (current behaviour confirmed) |
| Security (SEC) | `backend/tests/audit_route_inventory.py` (diagnostic script) | TOTAL 778: public 300, marketpub 17, admin 461. 53 public routes without an auth marker were reviewed; all are public by design, signature- or key-authenticated, or sign-in steps |
| Security | `backend/tests/audit_guard_paths.py` | `//api/settings` → 404, `/api//settings` → 401, `/api/members/../settings` → 401, `%2e%2e` → 404, `HEAD` → 401 |

---

## 4. New audit test files and what each proves

All files are in `C:\AI\pc-wt\audit\backend\tests\`. For the reproduction tests, **"pass" means the weakness is reproduced** on the local test database (current behaviour confirmed). None of these files touch production.

| File | Author area | Tests | What it proves | Finding |
|---|---|---|---|---|
| `audit_newsdesk_collect_wedge.py` | ND | 1 | A row stored 5 days ago plus the same URL with a fresh date makes every `collect()` raise IntegrityError, and a genuinely new story in the same feed is never stored. Collect recovers only after the old row is more than 7 days old | ND-1 / AR-4 (fixed in 5616ecd) |
| `audit_scheduler_serial_starvation.py` | AR | — | A 20 s-interval job waits for the full length of a long job under the global lock. A job doing synchronous work overruns its `asyncio.timeout` (timeout 0.1 s, ran 0.6 s, recorded "success") | AR-3 / TG-2 |
| `audit_jobs_server_disabled.py` | AR | — | `x.ingest` is in `scheduler.disabled`, yet GET /jobs says enabled and PATCH enabled=true answers "enabled". Only "Run now" reveals "disabled" | AR-5 |
| `audit_admin_session_survives_password_change.py` | AR | — | An admin session stays valid after `ADMIN_PASSWORD_HASH` changes | AR-10 / SEC-11 |
| `audit_route_inventory.py` | SEC | diagnostic | Inventory of all 778 routes by access class (output in scratchpad `routes.tsv`) | Basis for SEC Summary |
| `audit_guard_paths.py` | SEC | — | `admin_guard` refuses odd path spellings (results in §3) | Verified OK |
| `audit_passport_exploits.py` | PP | 9 | `pp1`, `pp1b`: Telegram start-link phishing signs the attacker in, and the link variant attaches the victim's Telegram (PP-1 / SEC-4). `pp7`: wallet reward +6,000 XP at rank 10,000 (PP-2). `pp3`: packs eat the overall cap (PP-3). `pp4`: 15 Passports from one IP by rotating the User-Agent (PP-4). `pp6`: 30 parallel awards paid against a cap of 10 (PP-6). `pp2`: fabricated video opens pay the weekly mission (PP-7). `pp8`: Telegram sign-up doesn't qualify the referral (PP-9). `pp5`: content-view pays without play time (PP-10) | PP-1, 2, 3, 4, 6, 7, 9, 10 |
| `audit_telegram_bot.py` | TG | 5 | `test_TG1`: an edited command is answered again (TG-5). `test_TG2`: channel posts and automatic forwards count as people (TG-6). `test_TG3`: HTML cut inside `<a href="` (TG-10). `test_TG4`: `_whole_lines` returns an overlong line unchanged (TG-10). `test_TG5`: `:g` rounding, `f"{104123.5:g}" == "104124"` (TG-8) | TG-5, TG-6, TG-8, TG-10 |
| `audit_trading_tr_checks.py` | TR | 2 | `test_sqlite_ignores_with_for_update`: SQLite renders no FOR UPDATE (TR-7). `test_min_bumped_position_gets_take_profit_orders_under_10_usd`: a $12 position gets TPs of $6.60 and $6.90 (TR-8) | TR-7, TR-8 |
| `audit_tg_trading_live_gate.py` | TR | 1 | On feat/tg-trading, a manual LIVE open fills for an account with `mode=LIVE` and `live_enabled_at=None` (run from the tg-trade worktree) | TR-4 |
| `audit_vpn_limits.py` | VPN | 3 | `test_audit_deletes_do_not_spend_the_device_limit`: 3 creates, 3 deletes, 2 more creates OK, the 6th → 429. `test_audit_browser_logins_and_devices_share_one_bucket`: 4 extension logins plus 1 device, then the phone → 429. `test_audit_one_device_per_server_fills_the_cap`: one PC on 3 servers → 201 ×3, then the phone → 409 | VPN-3, VPN-2 |

---

## 5. Production and read-only evidence commands

### Architecture (AR)
- `cd C:\AI\price-chat\backend && npx @railway/cli logs --lines 5000 --filter "turned off"` gave the disabled-jobs line (05:14:13).
- `npx @railway/cli logs --lines 5000` (2,816 lines, current deployment) and `--lines 3000 --filter "SLOW"` (2,066 lines), with awk per-route statistics (AR-6).
- `npx @railway/cli deployment list` gave 20 deployments from 11-03 to 11-08, 3 FAILED.
- `npx @railway/cli logs <df5d96bd|ec318fd9|c3b18744> --lines 3000 | grep -iE "backup|SignatureDoesNotMatch|Owner alert"` (AR-1, AR-2).
- `npx @railway/cli status`: single api service, volume 4.0/48.8 GB, bucket portfolio-photos, region iad.
- `curl https://api.pricechat.live/api/health/public` returned `{"ok":true,"db":"ok","scheduler":"ok","version":null,…}`. `/api/health/live` returned 200 and `/docs` 404.
- `curl -o /dev/null -w %{http_code} https://pricechat.live/{kalshi,vpn,trading-os,admin}` returned 404/200/200/200.
- `git -C C:\AI\price-chat branch --no-merged main` (17 of 39), per-branch ahead/behind, migration diff, `git show feat/kiwi-payments:backend/migrations/versions/0103_vpn_payment_recovery.py`.
- Python scripts: all JobSpecs via `build_jobs(MagicMock())` (scratchpad `ar_jobs.py`); a dead-module scan (`ar_dead.py`).

### Security (SEC)
- `curl -s -I` on https://pricechat.live, https://api.pricechat.live, `/docs`, `/openapi.json`, `/api/admin/overview` (401) and `/api/health/public`.
- `curl -s https://api.pricechat.live/api/members/auth/providers` showed telegram mode `deeplink`. `/api/website/chat` was checked for field names only.
- `npx @railway/cli logs --service api --lines 5000`, grepped for the PROXY_SHARED_SECRET warning, Rekey, backup, the self-check, and PII/token patterns (0 hits).
- `pip-audit -r freeze.txt --no-deps --disable-pip` (scratch venv; freeze taken from the project venv): pillow 11.3.0 (56 advisories), python-multipart 0.0.20 (6), pyasn1 0.6.4 (4), certifi (1), opentelemetry-api (1).
- `cd frontend && npm audit --omit=dev --json` gave 1 high (ws) and 1 moderate (ethers).
- Greps: `text(f` / `execute(f`, `dangerouslySetInnerHTML|innerHTML`, `html=True` vs `esc(`, logging of token/secret/key, `foreign_keys`, `ondelete`, TbNonce cleanup.
- A static IDOR sweep of all public id-taking routes (one sub-agent), recorded as SEC-15.

### Telegram (TG)
- `npx @railway/cli logs --lines 3000 --filter "telegram OR Bot OR listener"` gave 78 lines since the 05:14 restart.
- `npx @railway/cli logs --lines 5000`, grepped for waited, refused, failed and telegram.
- Snippets:
  - `_sl_tp_args(['64000','66000'], 5)` returned `(64000, [66000.0])`.
  - `chart.render_study` on 330 candles took 0.034 s (plain) and 0.051 s (ma+bb+3 panels).
  - The longest BIP-39 run in ordinary chat was 2-4 words (threshold 12).

### Trading (TR)
- Read-only Hyperliquid info calls: mainnet and testnet `metaAndAssetCtxs`, `meta` and `allMids` (scratchpad `tr/meta.py`, `tr/cmp.py`).
  - 0 invalid rounded prices across 179 mainnet and 159 testnet assets.
  - 77 coins differ in szDecimals or maxLeverage between networks.
  - Testnet mid gaps up to +5.97% (HYPE).
- Docs read: Hyperliquid "Nonces and API wallets", "Order types", "Rate limits and user limits".
- No orders placed, no writes to any API, no Railway commands.

### News Desk (ND)
- `npx @railway/cli logs --lines 2000 --filter "newsdesk OR tgnews"` gave 23 lines, all IntegrityError on nd_stories.link from 10:54 UTC.
- `npx @railway/cli logs --lines 5000` (from 09:50 UTC) and `logs <deployment> --lines 3000 --filter "newsdesk OR tgnews OR nd_stories"` for 20ebb668, df5d96bd, ec318fd9, ff871084 and 6a9cdd9e. These showed the 11-06 releases, "tgnews DELIVERED message_id=144/145", and "Telegram listener waited 185s … behind newsdesk.tick".
- `curl -s https://t.me/s/Pricechatupdates`: the last desk post is 145 (2026-11-06 14:30 UTC); 146 is a manual YouTube link; views 45-72.
- `python scratchpad\ndaudit\audit_replay.py replay.db 72` gave 3 releases in 72 h, all market moves.
- `python scratchpad\ndaudit\audit_replay100.py replay100.db 96` gave 93 stories, 20 passing evidence, 8 releases in 96 h, and 0 at the weekend (ND-3).
- `python scratchpad\ndaudit\ndwhy.py replay.db`: per-story verdicts (ND-5, ND-6).
- `curl` of candidate feeds: CoinDesk, Blockworks, DL News, Bitcoin Magazine, CFTC and MarketWatch topstories returned 200. Treasury press RSS returned 404. rekt.news is stale.
- Greps: `rewrite(|plan_news(|detect_markets(|detect_liquidations(` found nothing; `sensitivity` matched only the model, route, overview and dead code.
- Output files: `scratchpad\ndaudit\replay_out.txt`, `replay100_out.txt`.

### Passport (PP)
- `curl -s https://api.pricechat.live/api/members/auth/providers` showed telegram deeplink, email unavailable, x write False.
- `curl -s https://api.pricechat.live/api/members/rules` returned the live XP rules.
- `curl -s "https://api.pricechat.live/api/members/leaderboard?metric=score&limit=100"`: 38 visible members, top values 4195 / 3499 / 1850, 14 default-named. No usernames were recorded.
- `curl … /api/rewards-admin/overview` returned 401.

### Kiwi VPN (VPN)
- `curl https://api.pricechat.live/api/members/vpn/stats`: 3 servers, Ashburn recommended, active_users 5, uptime null.
- `curl https://api.pricechat.live/api/members/vpn/public`: enabled, trial on, payments off.
- Downloaded the live binaries and ran `sha256sum`: setup.exe 077ae0f6…0598 and rc1.apk cb52f939…06c7, both matching vpnConfig.ts. `Get-AuthenticodeSignature setup.exe` returned **NotSigned**.
- `nslookup`: hel1.vpn.pricechat.live is 2.29.149.29; ash1.vpn.pricechat.live is 178.156.252.184.
- `git grep -n vpn_device <branch>` over 6 branches; `git log -S'LIMITS["vpn_device"]'`; `git show 334f45d`; migration diffs per branch.
- `railway logs` was **not** run by this reviewer (the CLI wasn't on PATH in that shell).

### Website / UX
- `npm ci`, `npx tsc -b`, `npx vite build` and `npx vitest run` (§2).
- Live HTML GETs with a browser UA for 15+ pages, all identical 2,252-byte shells. `/terms`, `/privacy` and `/nonexistent-xyz` returned 404.
- `curl -A "Twitterbot/1.0"` gave per-page heads. On `/pokemon/cards/5` it showed a relative og:image. `curl -A Googlebot` gave `<title>Price Chat</title>`.
- Sitemaps: pages-1 27, market-1 465, profiles-1 2, cards-1 25,000 URLs.
- A crawl of 77 GETs at 0.3 s spacing: 77 × 200.
- Downloads: Range GETs on the setup.exe, apk and extension zip returned 206.
- API GETs: `/api/website/home`, `/api/members/vpn/public`, `/api/members/trading-bot`, `/track-record` (`configured:false`), `/arbitrage/lunch` (5.15 s), `/arbitrage/funding` (3.19 s), `/api/website/chat` (40 messages, 1 with a link).
- Web vitals: CDP at 390x844, dpr 2, CPU ×4, 150 ms latency, cold cache (PERFORMANCE-RELIABILITY.md §2.2).
- Overflow check: `scrollWidth == innerWidth == 390` on all 8 pages.

---

## 6. Coordinator-verified results

### 6.1 ND-1 fix (production)
- **Bug:** News Desk wedged on a duplicate link from 2026-11-08 10:54 UTC.
- **Fix:** made during the audit with the owner's approval. Commit **5616ecd** ("News Desk: one stored link can no longer stop every feed"); main was fast-forwarded.
- **Deploy:** Railway deployment **a93c797a** at **2026-11-08 16:06 UTC**.
- **Tests:** `tests/test_newsdesk_collect.py` 2 passed, plus 146 newsdesk tests passed.
- **Production confirmation: CONFIRMED.** Railway deployment a93c797a started 16:08:40 UTC; the scheduler logged `job_recovered: Background job 'News Desk' is working again` at 16:12:02 UTC, and no `Job newsdesk.tick failed` lines followed.

### 6.2 TG-7 and TG-8 fixes (branch only)
- **Fixed:** the feat/tg-trading Confirm buttons without expiry (TG-7) and the price rounding and `/tp` parsing (TG-8).
- **Commit:** **9e8e943** on feat/tg-trading. **Not deployed**, because the branch is not deployed.
- Open TR items on the same branch: TR-4, TR-5, TR-11, TR-13, TR-14 and TR-16. Whether the TG-7 fix covers TR-15 should be checked when the branch is reviewed.

### 6.3 VPN-3 correction
The earlier belief, in commit 334f45d's message and in memory notes, that device deletions count against the `vpn_device` rate limit **was wrong**. Trial start, device creation and extension logins share the bucket; deletes are not limited. This is backed by `audit_vpn_limits.py`.

### 6.4 Kiwi VPN Windows 2.0: GitHub Actions run 42479850219
Built from source **334f45d** in 619vortex/KIWIVPN.

| Job | Result |
|---|---|
| Build | **Success** |
| Kill-switch job | **Success**: 28 checks. The installed service against a never-answering test server, plus raw WFP filters |
| Real-server e2e | **36/36 core checks PASS** (details below) |
| Window tests | **7/10 PASS**, 3 FAIL (details below) |

Real-server e2e, 36/36 core checks passed:
- Connect to Germany, Helsinki and Ashburn.
- Public IP equals the server.
- DNS goes through the VPN (checked with bash.ws and whoami).
- No IPv6 leak.
- Switch country while connected.
- Reconnect 3/3.
- Speed 186 Mbps down.
- Split tunneling.
- Quad9.
- Kill switch on a real tunnel, including a tunnel-process crash with **no leak and auto-recovery in 14 s**.
- Disconnect releases the block.
- Pause limit 117 s.

Window tests, 7/10 passed:
- PASS: window opens; signed in; Smart Connect connects (Ashburn); Tools speed test 732 Mbps; leak check OK; disconnect.
- FAIL (3):
  1. The failover cut-off was not noticed. The test's Windows Firewall rule didn't stop the existing tunnel's UDP flow, so this is a suspected test-method flaw. **Product failover is UNVERIFIED.**
  2. Failover move.
  3. Reconnect after the failover.
- Smart Connect timings are unavailable, because production doesn't send `proxy_host` (VPN-12).

Defects found and fixed in earlier runs during the work:
- The WFP session closed instantly (a copy of Guard).
- A stop request was ignored during service start.
- A sublayer collision on reconnect (0x80320009).
- A dead DNS-leak service (edns.ip-api.com returned 404), replaced with bash.ws and whoami.akamai.net.
- A 13 s DNS delay after connect, fixed through the tunnel interface metric.

This partly supersedes VPN-8's and the VPN notes' "real-server e2e UNVERIFIED". The kill switch on a real tunnel is now evidenced. Failover is still UNVERIFIED. Signing, the update mechanism and the in-place upgrade from 0.1.1 are unchanged (VPN-7).

### 6.5 Hyperliquid testnet probe
Made with a throwaway, unfunded key:
- Info reads worked.
- Signing was recognised by the exchange.
- Leverage, order and stop refusals ("User or API Wallet … does not exist") came back as **ERROR results, never fills**.

**A full testnet trade was NOT done.** That needs a funded testnet account with an approved API wallet. TR-8 (TP orders under $10) and the trigger-price behaviour therefore remain unverified.

---

## 7. Screenshots

In `screenshots/`. Taken with headless Edge: 1440x900 through `--window-size`, and 390x844 through CDP `Emulation.setDeviceMetricsOverride(mobile:true)`. Plain `--window-size=390` gives 492 px, so those shots were discarded.

| File | Shows | Used for |
|---|---|---|
| `ux-home-390.png`, `ux-home-1440.png` | Home hero with no CTA; "9+" chat badge; nav LATEST · ABOUT · GIVEAWAY · EARN | UX-3, UX-4, UX-11 |
| `ux-earn-390.png`, `ux-earn-1440.png` | Earn hero "FREE · EVERY KIWI PASSPORT MEMBER"; purple/teal system | UX-8, UX-10 |
| `ux-vpn-390.png`, `ux-vpn-1440.png` | "Start Free Trial — 30 Days" for anonymous visitors; lime system | UX-5, UX-6, UX-10 |
| `ux-pokemon-390.png`, `ux-pokemon-1440.png` | Light market under a dark header | UX-10 |
| `ux-giveaway-390.png`, `ux-giveaway-1440.png` | "Just for fun: test feature… you don't win real cards" | UX-15 |
| `ux-join-390.png`, `ux-join-1440.png` | Chat bubble overlapping "Sign in with wallet" | UX-11 |
| `ux-leaderboard-390.png` | Mobile leaderboard | UX-4, PP-13 |
| `ux-learn-390.png` | Members-only gate in the sitemap (851 px) | UX-19 |

---

## 8. Limitations and unresolved verification

| Item | Why it is unresolved | IDs |
|---|---|---|
| Whether secrets were rotated after the 2026-10-21 breach (bot token, encryption key, admin password, bucket keys, member HL keys) | `railway variables` and the secret store are forbidden; inferred from logs only | SEC-1, SEC-2, TR-10 |
| Whether the breach included volume or DB access | Not knowable from here | SEC-2 |
| Root cause of the backup failure; whether volume backups are enabled | Env, bucket and dashboard off-limits | AR-1, SEC-7 |
| Production package versions | No lock file; no ssh | SEC-8, SEC-9 |
| `SCHEDULER_ONLY_JOBS` value, `JOB_*_SECONDS` overrides, `OLLAMA_BASE_URL`, Anthropic key for News Desk, whether `invites.poll`, `brand.*` and `youtube.sync` really run | Env values off-limits | AR-15, ND-10, TG Untested |
| Container memory and CPU | Not exposed by the logs CLI | AR-17 |
| Signed-in website flows, admin buttons end-to-end | No logins allowed; assessed from code (page → route → service) | UX, AR Untested |
| SEC-1 and SEC-4 against production | No logins; proven by code, local tests and the live providers config | SEC-1, SEC-4 |
| Account deletion with market RESTRICT rows | Inferred from the schema; not run | SEC-12 |
| Hyperliquid sub-$10 reduce-only triggers; `limit_px == triggerPx` behaviour | Needs a funded testnet write (owner approval) | TR-8 |
| Production Hyperliquid weight use and 429s | Logs not analysed for this | TR-2 |
| Real callback expiry, "Button answer refused" frequency, `/signal` wall time | Too little bot traffic in about 5 h of logs | TG Untested |
| Windows 2.0 failover | 3 window tests failed on a suspected test-method flaw | Coordinator §6.4 |
| Windows in-place upgrade from 0.1.1 to 2.0 | CI installs fresh | VPN-7 |
| Android on a physical phone; iOS on an iPhone; extension signed in on a real account | No devices available | VPN-13 |
| Live VPN server state (ufw, logging, ash1 delete protection, wg0/wg1 isolation) | Touching servers is forbidden | VPN-4 |
| A real NOWPayments payment and the IPN signature format | None has ever happened | VPN-1 |
| Google index coverage; Lighthouse and axe scores; Safari / real Android rendering; modal focus traps | No Search Console access; tools not installed; emulation only | UX Untested |
| Quiet-week News Desk volume; liquidation events; Hyperliquid second opinion | Feeds hold only 1-4 days; the replay ran without these inputs | ND Untested |
| X pricing ($0.015 vs $0.20 per post) | Not checkable from here | ND-15 |
| Production confirmation of the ND-1 fix | Next run pending at the time of writing | §6.1 |
| Logs older than the current and previous deployments | Railway CLI returns recent lines only | AR, ND Untested |
