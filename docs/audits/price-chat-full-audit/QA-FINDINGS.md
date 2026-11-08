# Price Chat: QA findings

Audit of 2026-11-08. Code was read at main e978cc3 (audit worktree `C:\AI\pc-wt\audit`), plus the branches named in each finding. This file lists every confirmed bug and suspected issue from the eight reviewer notes in `notes/`, except the pure security items. Those are in **SECURITY-REVIEW.md** and are only cross-referenced here.

How to read it:
- **Status** labels are the reviewers' own: CONFIRMED, SUSPECTED or UNVERIFIED. **FIXED** is added only where the coordinator verified a fix during the audit.
- **Effort**: S = hours, M = days, L = weeks.
- Where two reviewers found the same problem, the entries are merged and both IDs are kept.
- "Audit test" means a new file `backend/tests/audit_*.py` in the audit worktree. All of them pass, and passing means the weakness is reproduced (see TEST-EVIDENCE.md).

Security items covered elsewhere (in SECURITY-REVIEW.md):

| Item | Topic |
|---|---|
| SEC-1 to SEC-16 | All security reviewer findings |
| TG-1 = PP-1 = SEC-4 | Telegram deeplink takeover |
| PP-2, PP-4, SEC-6 | Wallet sign-in abuse |
| PP-16 = SEC-14 | Widget replay |
| TR-4 | Telegram `/open` skips the LIVE checks |
| TR-10 | Hyperliquid keys after the breach |
| VPN-9 | Private keys through Telegram |
| VPN-14 | CI uses the owner's session |
| AR-10 | Admin sessions |
| AR-11 | Admin audit log |
| AR-13 = SEC-3 | Proxy secret |

---

## Summary table (sorted by severity)

| ID | Area | Title | Severity | Status | Effort |
|---|---|---|---|---|---|
| ND-1 / AR-4 | News Desk | A recycled feed URL wedged every News Desk tick (UNIQUE nd_stories.link) | Critical | CONFIRMED, then **FIXED** in 5616ecd, deployed a93c797a; confirmed working in production 16:12 UTC | S |
| AR-1 | Ops / data | No database backup has ever succeeded (SignatureDoesNotMatch) | Critical | CONFIRMED | S / M |
| AR-2 (+ND-2, TG-16, VPN-6) | Ops | Owner alerts are wired but delivered to no one | High | CONFIRMED | S |
| AR-3 / TG-2 (+ND-7) | Scheduler / Telegram | One global job lock; the bot holds it for 90 s and waits behind long jobs | High | CONFIRMED | M |
| AR-6 | Pokémon market | Public market stalls for 10-39 s every 15 minutes (cache TTL outlives the warm-up) | High | CONFIRMED | S |
| AR-7 | Deploy | Migrations run at boot with no rollback path | High | CONFIRMED | M |
| AR-8 | Deploy | Unreproducible deploys: laptop `railway up`, no CI, no build id, unpinned deps | High | CONFIRMED | M |
| ND-2 | News Desk | No "desk went quiet" alert; a crashed tick never updates last_run | High | CONFIRMED | S |
| ND-3 | News Desk | Scoring calibration can't deliver 3-4 posts a day | High | CONFIRMED | M |
| ND-4 | News Desk | Dead MarketWatch feed, thin crypto coverage, staleness invisible | High | CONFIRMED | S |
| TR-1 | Trading | A live position left without a stop alerts no one | High | CONFIRMED | S |
| TR-2 | Trading | One member can use up the shared Hyperliquid request budget | High | CONFIRMED (code) / SUSPECTED (exhaustion) | M |
| VPN-1 | Kiwi VPN | Trial on, payments off: every trial hits a dead end around 2026-12-04 | High | CONFIRMED | M |
| VPN-2 | Kiwi VPN | One device per server plus a 3-device cap: one PC locks out the phone | High | CONFIRMED | M |
| VPN-4 | Kiwi VPN infra | Single provider and account, customers on the owner's personal VPS, no backups | High | CONFIRMED / UNVERIFIED | L |
| UX-1 | Website / SEO | Search crawlers get an empty shell for every URL | High | CONFIRMED | M |
| UX-3 | Website | Home page has no value proposition or CTA above the fold | High | CONFIRMED | M |
| UX-4 | Website | Pokémon market has no link in nav or footer | High | CONFIRMED | S |
| UX-6 | Website / VPN | Site promises "$9.99 for 30 days" while payments are off | High | CONFIRMED | S |
| UX-9 | Website / legal | Legal pages publicly marked "Draft" and incomplete | High | CONFIRMED | M |
| TR-3 | Trading | Emergency and manual closes use an IOC limit only 0.24% from mark | Medium-High | CONFIRMED | S |
| AR-5 | Admin | Jobs page shows server-disabled jobs as enabled; toggling does nothing | Medium | CONFIRMED | S |
| AR-9 | Deploy | railway.json (healthcheck, replicas, restart) stops working 2026-12-01 | Medium | CONFIRMED | S |
| AR-12 | Architecture | SQLite on one volume is the main scaling and reliability limit | Medium | CONFIRMED / SUSPECTED | L |
| AR-15 | AI | Production LLM provider is Ollama at its default localhost address | Medium | SUSPECTED | S |
| AR-16 / VPN-11 | Repo | Unmerged payments branch carries a conflicting migration; branch sprawl | Medium | CONFIRMED | S / M |
| TG-3 | Telegram | Commands handled one at a time, no per-user throttle | Medium | CONFIRMED | M |
| TG-4 | Telegram | Transient Telegram errors fail the job and trigger up to 30 min backoff | Medium | CONFIRMED | S |
| TG-5 | Telegram | Editing a message re-runs its command | Medium | CONFIRMED | S |
| TG-6 | Telegram | Bot answers in any group, channel posts and auto-forwards | Medium | CONFIRMED | S |
| TG-7 (+TR-15) | Telegram trading (branch) | closeall and sl/tp Confirm buttons have no token or expiry | Medium | CONFIRMED, then **FIXED** on branch (9e8e943, not deployed) | S |
| TG-8 | Telegram trading (branch) | Stop/target rounded to 6 significant digits; "/tp 64000 66000" read as position #64000 | Medium | CONFIRMED, then **FIXED** on branch (9e8e943, not deployed) | S |
| TR-5 | Trading (branch) | Banter referral gate changes the existing signal ENTER; website stays ungated | Medium | CONFIRMED | S |
| TR-6 | Trading | TESTNET trades priced and sized from mainnet data | Medium | CONFIRMED | M |
| TR-8 | Trading | Take-profit orders on small positions may be under the $10 minimum | Medium | SUSPECTED | S |
| TR-9 | Trading | Owner's min rank / deposit settings no longer gate LIVE (red test on main) | Medium | CONFIRMED | S |
| ND-5 | News Desk | Topic filter still rejects relevant crypto news | Medium | CONFIRMED | S |
| ND-7 | News Desk | X delivery depends on the whole tick; the tick holds up the bot | Medium | CONFIRMED | M |
| ND-8 | News Desk | Admin can't see or control desk decisions; Telegram lane has no review mode | Medium | CONFIRMED | M |
| ND-10 | News Desk | AI summary validator contradicts the prompt; fabrication guard checks only numbers | Medium | CONFIRMED / SUSPECTED | S |
| ND-14 | News Desk | Clustering misses the same story in different words | Medium | CONFIRMED | S |
| PP-3 | Passport | Pack XP eats the 60/day overall cap while packs themselves are uncapped | Medium | CONFIRMED | S |
| PP-5 | Passport | Royal (free VPN) reachable in about 2 weeks of click-only activity | Medium | CONFIRMED | M |
| PP-6 | Passport | Daily caps are check-then-insert; parallel requests overshoot (30 vs cap 10) | Medium | CONFIRMED | M |
| PP-7 | Passport | Weekly "Open 3 videos" paid from client-reported arbitrary targets | Medium | CONFIRMED | S |
| PP-8 | Passport | Referral and crew anti-abuse is weak | Medium | CONFIRMED | M |
| PP-11 | Passport / community | "Every message becomes XP" rewards chat spam | Medium | CONFIRMED | S |
| VPN-3 | Kiwi VPN | Trial, device creation and extension logins share one 5-per-10-min bucket | Medium | CONFIRMED | S |
| VPN-5 | Kiwi VPN | Ashburn is public and "recommended" to everyone, 1 TB included, no cap | Medium | CONFIRMED | S |
| VPN-6 | Kiwi VPN | One failed sync marks a server offline for 5 min | Medium | CONFIRMED / UNVERIFIED | S |
| VPN-7 | Kiwi VPN apps | No update mechanism anywhere; Windows builds unsigned | Medium | CONFIRMED | M |
| VPN-8 | Kiwi VPN Windows 2.0 | Kill switch is deliberately fail-open; copy must say so | Medium | CONFIRMED (design) | S / M |
| VPN-10 | Kiwi VPN / legal | No VPN terms, acceptable-use or refund policy live | Medium | CONFIRMED | S |
| VPN-12 | Kiwi VPN | Smart Connect has nothing to measure against production | Medium | CONFIRMED | S |
| VPN-13 | Kiwi VPN mobile | Android RC carries Play-sensitive permissions; iOS never run on a device | Medium | CONFIRMED / UNVERIFIED | M |
| UX-2 | Website / SEO | Card link previews use a relative og:image | Medium | CONFIRMED | S |
| UX-5 | Website | VPN tab hidden from signed-out visitors | Medium | CONFIRMED | S |
| UX-7 | Website | "Every rank unlocks a reward" is not true | Medium | CONFIRMED | S |
| UX-10 | Website | Four visual systems; market goes light under a dark header | Medium | CONFIRMED | M |
| UX-11 | Website | Chat bubble shows a fake "9+" badge and covers CTAs | Medium | CONFIRMED | S |
| UX-12 | Website perf | Home page layout shift (CLS 0.12-0.35) | Medium | CONFIRMED (value) / SUSPECTED (cause) | S-M |
| UX-14 | Website / Earn | "Earn" tab showcases a paper strategy down 421 SOL | Medium | CONFIRMED | S |
| TR-7 | Trading | Row lock for racing entries is a no-op on SQLite | Low-Medium | CONFIRMED (no-op) / SUSPECTED (race) | M |
| AR-14 | Admin | Long "Run now" holds an HTTP request behind the job queue | Low | SUSPECTED | S |
| AR-17 | Architecture | Heavy work (rendering, ML, catalogue builds, DB copy) runs in the API process | Low | SUSPECTED | M |
| AR-18 | Scheduler | Retired Paper Exchange still writes every 15 s | Low | CONFIRMED | S |
| AR-19 | Monitoring | Health says "scheduler ok" while jobs fail | Low | CONFIRMED | S |
| TG-9 | Telegram trading (branch) | Behaviour changes and messages to confirm | Low | CONFIRMED | S |
| TG-10 | Telegram | Client slices raw HTML at 4096/1024 characters | Low | CONFIRMED | S |
| TG-11 | Telegram | Commands acknowledged before handling; lost on crash or timeout | Low | CONFIRMED | S |
| TG-12 | Telegram | fc timeframe buttons pressable by anyone; "busy" guard does nothing | Low | CONFIRMED | S |
| TG-13 | Telegram | `/help` doubles as the trade check; `/risk` means two things | Low | CONFIRMED | S |
| TG-14 | Telegram | Other bots' commands logged and dropped from ingestion | Low | CONFIRMED | S |
| TG-15 | Telegram | Join-welcome flood and footer spam | Low | CONFIRMED | S |
| TG-16 | Telegram / ops | No owner alert chat; community messages stored but unused | Low | CONFIRMED | S |
| TR-11 | Trading (branch) | Manual trades skip several member limits | Low | CONFIRMED | S |
| TR-12 | Trading | P&L booking from fills can be incomplete (no paging) | Low | CONFIRMED | S |
| TR-13 | Trading (branch) | Manual signal rows can collide and are never cleaned up | Low | CONFIRMED / SUSPECTED | S |
| TR-14 | Trading (branch) | Telegram claims an API wallet "can never withdraw" | Low | CONFIRMED | S |
| TR-16 | Trading | Switching account via API key can orphan TESTNET positions | Low | CONFIRMED | S |
| ND-6 | News Desk | Category keywords match inside other words ("gold" in "Golden") | Low | CONFIRMED | S |
| ND-9 | News Desk | Dead Admin controls and dead code | Low | CONFIRMED | S |
| ND-11 | News Desk | Telegram "Paused" badge outlives the pause | Low | CONFIRMED | S |
| ND-12 | News Desk | Engagement feedback loop has no data | Low | CONFIRMED | M |
| ND-13 | News Desk | Post style probably hurts engagement | Low | SUSPECTED | S |
| ND-15 | News Desk / X | X dry run, link rule and cost look correct (informational) | Low | CONFIRMED / UNVERIFIED | S |
| PP-9 | Passport | A friend who signs up with Telegram or X never qualifies the invite | Low | CONFIRMED | S |
| PP-10 | Passport | Watch & Earn timer bypassed by the content-view endpoint | Low | CONFIRMED | S |
| PP-12 | Passport | Duplicate Passports cannot be merged | Low | CONFIRMED | M |
| PP-13 | Passport | Suspended members still appear on leaderboards | Low | CONFIRMED | S |
| PP-14 | Passport | Copy promises features that are off in production | Low | CONFIRMED | S |
| PP-15 | Passport | "Next steps" ignores the biggest real levers | Low | CONFIRMED | S |
| VPN-15 | Kiwi VPN | Testers-only servers keep working for ex-testers | Low | CONFIRMED | S |
| VPN-16 | Kiwi VPN | Trial abuse and silent trial start on install | Low | CONFIRMED / SUSPECTED | S |
| VPN-17 | Kiwi VPN | Capacity is a device count (250), not throughput | Low | SUSPECTED | S |
| UX-8 | Website / Earn | Earn gating copy contradicts itself | Low | CONFIRMED | S |
| UX-13 | Website perf | Earn sub-pages depend on slow uncached endpoints (5.15 s) | Low | CONFIRMED | S |
| UX-15 | Website | "Pack Giveaway" in top nav awards no prizes | Low | CONFIRMED | S |
| UX-16 | Website | Social-proof counters show very small audiences | Low | CONFIRMED | S |
| UX-17 | Website | Legacy /website/* URLs linked and duplicated, no 301 | Low | CONFIRMED | S |
| UX-18 | Website / SEO | useHead appends a second set of OG/Twitter tags | Low | CONFIRMED | S |
| UX-19 | Website / SEO | Thin, members-only and duplicate URLs in the sitemap | Low | CONFIRMED | S |
| UX-20 | Website | Housekeeping: dead assets, binaries in git, duplicate links | Low | CONFIRMED | S |

---

## A. Operations, architecture and admin (AR)

### ND-1 / AR-4: A recycled feed URL wedged every News Desk tick
- **Severity:** Critical
- **Status:** CONFIRMED, then **FIXED during the audit** with the owner's approval. Commit 5616ecd (main fast-forwarded), deployed as Railway deployment a93c797a at 2026-11-08 16:06 UTC. Tests: `tests/test_newsdesk_collect.py` 2 passed, plus 146 newsdesk tests passed. **Confirmation of the next production run is pending.**
- **Effort:** S
- **Reproduction:**
  1. Store a story row with link L, 4-7 days old.
  2. The feed returns L again with a fresh date.
  3. `collect()` builds `seen` only from the last 4 days (`engine.py:84`) but purges only after 7 days (`engine.py:103`). It re-inserts L, and the commit at `engine.py:104` fails, rolling back the whole batch.
- **Evidence:**
  - Production log: `10:54:00 / 11:00:33 / 11:11:58 / 11:33:18 Job newsdesk.tick failed: IntegrityError: UNIQUE constraint failed: nd_stories.link` (Cointelegraph's recycled "what-happened-in-crypto-today" URL).
  - Traceback: `engine.py line 535 tick -> line 104 collect`.
  - Reproduced by audit test `audit_newsdesk_collect_wedge.py` (passes).
- **Impact:**
  - No story from any feed is stored, X gets nothing, and the Telegram lane re-runs the full collect every 3 minutes on stale stories.
  - Without the fix it would recur about 3 days in every 7.
  - Nothing from the desk was posted to @Pricechatupdates after 2026-11-06 14:30 UTC.
- **Recommended fix (as listed by ND):**
  1. Check `seen` against every stored link.
  2. Use `INSERT … ON CONFLICT(link) DO NOTHING` or one savepoint per row.
  3. Commit the purge separately.
  4. Strip `utm_*` parameters.
  5. Apply `telegram.SPAM` at collect time.

  Which of these 5616ecd contains is described in its commit ("one stored link can no longer stop every feed").

### AR-1: No database backup has ever succeeded
- **Severity:** Critical
- **Status:** CONFIRMED
- **Effort:** S (credentials) / M (verified restore drill)
- **Reproduction:** read the nightly `system.backup` log line.
- **Evidence:**
  - `railway logs df5d96bd | grep backup` gives `2026-11-07 22:45:10 … Job system.backup failed: ClientError … (SignatureDoesNotMatch) … CreateMultipartUpload`, then "Owner alert (no owner Telegram chat set, logged only)".
  - Memory records 12 of 12 failures from 2026-10-23 to 11-03.
  - Code: `app/services/db_backup.py:64-80` streams a gzip of the 1.17 GB DB to bucket `portfolio-photos` through the portfolio S3 client (`brand_manager/store.py:33-42`).
- **Impact:** all data (members, XP, wallets, trading accounts, VPN billing, market) sits on one volume in one region. Losing the volume, or a bad migration, means total and unrecoverable loss.
- **Likely cause (SUSPECTED):** bucket credentials invalid after the 2026-10-21 incident, or botocore ≥1.36 default checksums on a non-seekable multipart stream.
- **Recommended fix:**
  1. Take a manual copy now through owner-only `GET /api/system/db-backup`.
  2. Fix `PORTFOLIO_S3_*`. Test with `head_bucket` plus a small `put_object`. If the credentials are fine, set `request_checksum_calculation="when_required"`.
  3. Enable Railway volume backups as a second copy.
  4. Run a restore drill and document the RTO.
  5. Make "last successful backup age" a health check.
- See also SEC-7 (backups readable with the leaked key).

### AR-2 (+ND-2, TG-16, VPN-6): Owner alerting delivers nothing
- **Severity:** High
- **Status:** CONFIRMED
- **Effort:** S
- **Reproduction:** read the startup self-check, or any "Owner alert" line, in `railway logs`.
- **Evidence:**
  - `05:14:13 WARNING pricechat.ops Startup self-check: 1 problem(s): owner alerts have no Telegram chat … alerts are only logged`.
  - "logged only" alerts for `job:newsdesk.tick` (11:11, 11:33) and `job:telegram.ingest` (09:19).
  - 2026-11-06 02:32-02:51: `vpn:offline:1/2` "Kiwi VPN server offline" was repeated and nobody was told.
  - Code: `main.py owner_alert` → `app/alerts.py:79-82`.
- **Impact:** the backup failure, the News Desk outage, offline VPN servers and 5xx bursts are all silent. The item has been on the owner's to-do list since 2026-10-23.
- **Recommended fix:**
  1. Set the chat in Admin › Settings › Background jobs › Owner alerts, then press "Send test" (`POST /api/system/alerts/test`).
  2. Add an external uptime monitor on `/api/health/public`.
  3. Show a red self-check banner on the admin Overview.

### AR-3 / TG-2 (+ND-7): One global lock serialises every job; the Telegram listener holds it about 90 s
- **Severity:** High
- **Status:** CONFIRMED
- **Effort:** M
- **Reproduction:** audit test `audit_scheduler_serial_starvation.py` (passes):
  - a 20 s-interval job waits for the full length of a long job;
  - a job doing synchronous work runs past its `asyncio.timeout` (timeout 0.1 s, ran 0.6 s, recorded "success").
- **Evidence:**
  - Code:
    - `scheduler.py:88` `self._serial = asyncio.Lock()`; `run_job` (175-194) takes it for every job.
    - telegram.ingest listens for 90 s (`settings.py:70`, `ingestion.py:124-137`, `jobs.py:432`).
    - Only 16 `to_thread` uses across 60 jobs.
  - Production logs:
    - `05:18:18 WARNING Telegram listener waited 148s for its turn (behind pokemon.exchange.auto)`.
    - ND-7 found "waited 185s / 85s / 134s … behind newsdesk.tick" on 11-06 and 11-07.
    - `telegram_ingested` lines arrive about every 95-100 s.
- **Impact:**
  - The bot is silent while any long job runs (pokemon.sync up to 600 s, catalog/top100 up to 900 s, backup up to 1800 s).
  - Jobs with 20-60 s contracts (`tradingos.vortex_expire`, `committee.llm_expire`, `tradingos.track`, `pokemon.exchange.auto`) can't keep them while the bot listens.
  - Sync DB work on the event loop stalls HTTP requests too.
- **Recommended fix:**
  - Run the Telegram listener as its own long-lived task.
  - Split the scheduler into lanes (realtime ≤60 s and heavy batch) with per-lane locks.
  - Push heavy sync jobs through `asyncio.to_thread`.
  - Later, move to a separate worker service (needs a server DB, AR-12).

### AR-5: Admin "Background jobs" shows server-disabled jobs as enabled
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S
- **Reproduction:** audit test `audit_jobs_server_disabled.py`. `x.ingest` is in `scheduler.disabled`, yet GET says enabled and PATCH enabled=true answers "enabled". Only "Run now" reveals "disabled".
- **Evidence:** `routes/jobs.py:12-39` returns `enabled=row.enabled` with no server state. `JobOut` has no field for it. Production turns 16 jobs off (SYSTEM-INVENTORY §5).
- **Impact:**
  - The owner believes community analysis, knowledge indexing, X ingest, approvals expiry and worker verification are running.
  - The pages fed by those jobs show stale data with no explanation (the earlier stale Read & Earn incident).
  - `tradingos.funding_alerts` is pinned off by an env var the checkbox can't override.
- **Recommended fix:** add `server_state` to JobOut, show a grey "Off on this server" badge, disable the toggle, and put a banner on the affected pages.

### AR-6: Public Pokémon market stalls 10-39 s every 15 minutes
- **Severity:** High
- **Status:** CONFIRMED
- **Effort:** S
- **Reproduction:**
  - `railway logs --filter SLOW` (2,066 lines, 05:14-11:43) has 112 requests of 10 s or more, clustered at :03/:06, :21, :36 and :51 of every hour.
  - Examples: `10:08:28 GET /pokemon/market 200 14637ms`, `10:08:52 GET /pokemon/cards/{card_id}/chart 200 17898ms`.
- **Evidence:**
  - `pokemon/market.py:156` `BASE_TTL = 900.0`. `jobs.py:1000` runs `pokemon.warm` every 600 s, which returns cached rows while they are fresh (`market.py:173`).
  - So the warm-up at T+600 does nothing, the cache expires at T+900, and the next visitor rebuilds about 50k rows inline.
  - Per route:

    | Route | Slow requests | Average | Max |
    |---|---|---|---|
    | `/pokemon/cards/{id}/chart` | 778 | 2.5 s | 33 s |
    | `/pokemon/market` | 566 | 3.0 s | 39 s |
    | `/pokemon/cards/{id}/quality` | 215 | 3.9 s | 35.8 s |

- **Impact:** the public site and admin pages hang. Each rebuild holds one of the 10 public market slots and about 200-400 MB of RAM, so stacked visitors get 503 "market is busy".
- **Recommended fix:**
  - Refresh-ahead: the warm-up job forces a rebuild before the TTL expires and serves stale rows meanwhile.
  - Persist the computed rows so a restart doesn't start cold.
  - Make `rank_of` (`market.py:548`) a lookup in a precomputed map.

### AR-7: Migrations run at boot with no rollback path
- **Severity:** High
- **Status:** CONFIRMED
- **Effort:** M
- **Reproduction:** roll the image back after a deploy that included a migration. The older code doesn't know the DB revision, so alembic fails at startup ("Can't locate revision").
- **Evidence:** `db/session.py init_db()` → `db/migrate.py:39-57` `command.upgrade(config, "head")` on every boot. 0054, 0066 and 0072 have empty downgrades, and downgrades are never tested.
- **Impact:** every deploy with a migration is one-way. Combined with AR-1, there is no recovery.
- **Recommended fix:**
  1. Fix backups first.
  2. Take an automatic pre-migration snapshot.
  3. Write expand/contract migrations.
  4. Write a rollback runbook (Railway Redeploy plus `alembic stamp` rules).

### AR-8: Deploys are unreproducible
- **Severity:** High
- **Status:** CONFIRMED
- **Effort:** M
- **Reproduction:** `curl https://api.pricechat.live/api/health/public` returns `"version":null`.
- **Evidence:**
  - No `.github/workflows`. Deploys use `railway up` from the local tree, uncommitted files included.
  - On 2026-11-03, deployment 35f36b4e on service `api` was the frontend (an outage).
  - `railway deployment list`: 20 deployments from 11-03 to 11-08, 3 FAILED.
  - `requirements.txt` uses only `>=`; there is no lock file; pytest ships to production.
- **Impact:**
  - Nobody can say which commit is live.
  - A new dependency release can change production on any deploy.
  - Every deploy restarts the bot and scheduler.
- **Recommended fix:**
  - Deploy from GitHub `main` (root dir `backend`), with a GitHub Action running the backend and frontend tests.
  - Set `BUILD_ID`.
  - Use a lock file and move pytest to dev requirements.
  - Until then, run `railway up backend --service api` and add a build guard.
- See SEC-9 for the supply-chain angle.

### AR-9: railway.json stops working on 2026-12-01
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** every `railway` CLI call warns: "Config as Code … deprecated … keep working until 2026-12-01". `railway.json` holds the healthcheck, `numReplicas: 1`, the restart policy and the start command.
- **Impact:** a broken build could go live with no health check. Two replicas would double-run every job and couldn't share the SQLite volume.
- **Recommended fix:** run `railway config migrate`, or set these values in the service settings before December, then verify on the next deploy.

### AR-12: SQLite on one volume is the main scaling limit
- **Severity:** Medium
- **Status:** CONFIRMED (contention) / SUSPECTED (future growth)
- **Effort:** L
- **Evidence:**
  - 1.17 GB DB with WAL, busy_timeout 15 s and pool 10+30 (`db/session.py:14-15, 44-55`).
  - One process runs the API, the bot, 44 jobs and the 200-400 MB caches.
  - History of QueuePool exhaustion and 30 s responses (owner notes 10-23, 10-31).
- **Impact:** no horizontal scaling, no worker, no replica. A deploy means downtime.
- **Recommended fix:** fix AR-3 and AR-6 first, then move to Postgres plus a separate worker.

### AR-14: Long "Run now" holds an HTTP request
- **Severity:** Low
- **Status:** SUSPECTED
- **Effort:** S
- **Evidence:** `routes/jobs.py:42-48` awaits `scheduler.run_job(..., trigger="manual")` inside the request.
- **Impact:** the proxy times out while the job keeps running. The owner clicks again and gets "Already queued".
- **Recommended fix:** enqueue the job, return 202 with the queue position, and poll for status.

### AR-15: Production LLM provider is Ollama at its default address
- **Severity:** Medium
- **Status:** SUSPECTED (env values not readable)
- **Effort:** S
- **Evidence:**
  - Log: `Price Chat API ready: provider=ollama model=llama3.1:8b embeddings=nomic-embed-text`.
  - Defaults: `settings.py:36,46` (`127.0.0.1:11434`).
  - The production validator (`settings.py:349-375`) doesn't check `OLLAMA_BASE_URL`.
- **Impact:** if Ollama is unreachable, AI Brain chat, agent replies and knowledge search fail while the dashboard shows the model as configured.
- **Recommended fix:** check Admin › AI Brain model status, and add `OLLAMA_BASE_URL` to the startup self-check.

### AR-16 / VPN-11: Conflicting migration on an unmerged branch; branch sprawl
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S / M
- **Reproduction:** `git show feat/kiwi-payments:backend/migrations/versions/0103_vpn_payment_recovery.py | grep revision` gives `down_revision '0102_desktop_codes'`, while main has 0103_newsdesk_telegram → 0104 → 0105.
- **Evidence:**
  - 17 of 39 local branches are unmerged, there are 29 worktrees, and 21 merged branches were never deleted.
  - There are 25 Kiwi branches.
  - feat/kiwi-monitoring's 0106 will collide with the payments fix.
  - Several branches are 17 commits behind main.
- **Impact:** merging feat/kiwi-payments as-is creates two heads, so boot-time `upgrade("head")` fails and the deploy won't start. Fixes such as the flap guard and payment recovery sit undeployed.
- **Recommended fix:**
  1. Rebase payments and renumber its migration (to 0107, after monitoring's 0106).
  2. Merge in the order stage0, monitoring, payments.
  3. Add a pre-merge `alembic heads` single-head check.
  4. Prune merged branches and worktrees.

### AR-17: Heavy work in the API process
- **Severity:** Low
- **Status:** SUSPECTED
- **Effort:** M
- **Evidence:**
  - numpy/PIL in `tradingos/chart.py`, `tradingos/ml.py`, `newsdesk/card.py` and `portfolio/photos.py`.
  - Whole-catalogue market builds.
  - `/api/system/db-backup` copies 1.17 GB inside a request thread.
  - No OOM was seen.
- **Recommended fix:** precompute charts and market rows in jobs, stream the DB download from a pre-made file, and watch memory metrics.

### AR-18: Retired Paper Exchange still writes every 15 s
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `settings.py:246` `exchange_enabled=False`, but `jobs.py:737-744` calls `ex_maker.prune` every 15 s under the global lock: 5,760 lock acquisitions a day. This job is what the bot waited behind at 05:18.
- **Recommended fix:** drop `pokemon.exchange` and `pokemon.exchange.auto` from `SCHEDULER_ONLY_JOBS`, listing explicit names instead of the `pokemon.` prefix.

### AR-19: Health says "scheduler ok" while jobs fail
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `/api/health/public` returned `{"ok":true,"db":"ok","scheduler":"ok","version":null}` at 11:47 UTC, while newsdesk.tick had failed 4 times and the last backup had failed. `health.py:68-80` checks loop liveness only.
- **Recommended fix:** add "backup age < 36 h" and "no enabled job with 3 or more consecutive failures" checks.

---

## B. Telegram bot and channel (TG)

TG-1 (deeplink takeover) is in SECURITY-REVIEW (SEC-4). TG-2 is merged with AR-3 above.

### TG-3: Commands handled one at a time; no per-user throttle
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** M
- **Reproduction:** spam `/signal` (15-market scan) or fc buttons in a group. Every other member's replies and button answers queue behind them.
- **Evidence:**
  - `ingestion.py:_trading_os` awaits `bot.handle_command` serially.
  - Only `committee` and `vpn_device` have rate-limit buckets (`bot.py:641`, `committeecmd.py:126/440`).
  - Callbacks are processed after all commands. The DM fallback in `_answer` exists for late callbacks.
  - `client.py:_call` raises `TelegramRateLimitError` without retrying.
- **Impact:** one user can stall the bot. 429s cause dropped replies ("Bot command not answered").
- **Recommended fix:**
  - Per-user and per-chat token buckets for heavy commands and fc.
  - Call `answerCallbackQuery` first.
  - A bounded worker pool with per-chat ordering.
  - A 429-aware send queue.

### TG-4: Transient Telegram errors trigger up to 30 min of silence
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:**
  - `ingestion.py:sync` calls `get_updates` with no try/except.
  - `scheduler.py:269` backs off as `min(interval*2**(failures-1), 1800)`.
  - Production: `09:18:22 TelegramRateLimitError: retry after 5s`, `09:18:30 502`, `09:19:09 … did not respond`, `09:20:59 recovered`. That is about 2.5 min of silence from a 5 s rate limit.
- **Recommended fix:**
  - Inside `sync`, catch 429 (sleep `retry_after`) and 5xx (jittered retry).
  - Give telegram.ingest a `max_backoff_seconds` of 30-60.
  - Don't count a 429 on getUpdates as a job failure.

### TG-5: Editing a message re-runs its command
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S
- **Reproduction:** audit test `audit_telegram_bot.py::test_TG1_an_edited_command_is_answered_again`. "/price btc" edited to "/price eth" is answered again, and an edited "/signal" in a DM is answered twice.
- **Evidence:** `parser.py` marks `edited=True`, but `ingestion.py:_handle` never checks it.
- **Impact:** duplicate replies, free re-runs of heavy commands, and on the branch an extra `/open` preview.
- **Recommended fix:** skip the command and fc paths when `parsed.edited`.

### TG-6: Bot answers in any group, including channel posts and auto-forwards
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S
- **Reproduction:** audit test `test_TG2_channel_posts_and_automatic_forwards_count_as_people`. "fc btc" in a channel and "/price btc" from 777000 are both queued.
- **Evidence:** `ingestion.py:_handle` queues commands before the allow-list check.
- **Impact:** anyone can add the bot to their own group and use its compute, which shares the single listener.
- **Recommended fix:** answer only in allow-listed chats and private chats, leave unknown groups, and treat `channel_post` and `is_automatic_forward` as non-person.

### TG-7 (+TR-15): closeall and sl/tp Confirm buttons have no token (feat/tg-trading)
- **Severity:** Medium
- **Status:** CONFIRMED, then **FIXED on the branch** in commit 9e8e943 (not deployed; the branch is not deployed)
- **Effort:** S
- **Reproduction:** press an old `/closeall` or `/sl` Confirm hours later. It closes whatever is open now, or re-applies the old level.
- **Evidence:**
  - `tgtrade.py:669` `callback_data "ca:all"`.
  - `tgtrade.py:687-690` `"pr:<pos>:s|t:<prices>"`.
  - TR-15: the website requires a matching `confirm_count` (`routes/trading.py:191-201`).
- **Impact:** a scrolled-back tap can close positions that didn't exist when the prompt was shown, or move a stop back to a stale level. The member's own positions only.
- **Recommended fix (applied per coordinator):** TrIntent-style token with a 5-min expiry, single use, values stored server-side, and buttons removed after use. Check that TR-15's count and timestamp requirement is covered when the branch is reviewed.

### TG-8: Stop/target prices rounded; "/tp 64000 66000" read as position #64000 (feat/tg-trading)
- **Severity:** Medium
- **Status:** CONFIRMED, then **FIXED on the branch** in commit 9e8e943 (not deployed)
- **Effort:** S
- **Reproduction:**
  - Audit test `audit_telegram_bot.py` (TG-8 `:g` rounding): `f"{104123.5:g}" == "104124"`.
  - Snippet: `_sl_tp_args(['64000','66000'], 5)` returns `(64000, [66000.0])`.
- **Evidence:** `tgtrade.py:687` uses `f"{v:g}"`.
- **Impact:** wrong stop or target levels, and confusing "no such position" errors.
- **Recommended fix:** exact values in a server-side intent, and position ids written as `#12`.

### TG-9: Behaviour changes on feat/tg-trading to confirm with the owner
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:**
  - Every signal ENTER now runs `gate_for_entry`, including PAPER, plus a Hyperliquid network call before the callback answer.
  - "Sent you a private preview." is posted even when the DM was a refusal.
  - `/market` vs `/markets` differ by one letter, and an unknown coin gives "Couldn't read the market data".
  - Non-Trade/Market exceptions leave the member with only "Working…".
- **Recommended fix:** decide on PAPER gating, make the group message honest, rename `/market`, and wrap the callback in a generic error reply. See TR-5.

### TG-10: Client slices raw HTML at 4096/1024 characters
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Reproduction:** audit tests `test_TG3` (cut inside `<a href="`) and `test_TG4` (`_whole_lines` returns an overlong line unchanged).
- **Evidence:** `client.py` uses `text[:4096]` and `caption[:1024]`. The News Desk `MAX_LEN = 900` is checked on plain text, but the HTML adds up to 3 links. A long caption is then refused and the card image lost (SUSPECTED).
- **Recommended fix:** one shared `fit_html` that measures UTF-16 on parsed text.

### TG-11: Commands acknowledged before handling
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `ingestion.py:sync` commits `last_update_id` before running `_trading_os` from memory. The job timeout is 150 s (`jobs.py:432`).
- **Impact:** a crash, deploy or timeout loses the rest of the batch silently. Safe for trades (no double execution) but invisible to users.
- **Recommended fix:** persist the pending queue for read-only commands, and decouple the timeout (TG-2).

### TG-12: fc buttons pressable by anyone; `_FC_BUSY` never triggers
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `bot.py:_fc_callback`. Rendering takes 35-50 ms locally; production answered an fc in about 1.4 s.
- **Recommended fix:** a per-user cooldown, reply in groups when the presser isn't the asker, and remove `_FC_BUSY` or make it useful.

### TG-13: `/help` doubles as the trade check; `/risk` means two things
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `/help <anything>` goes to `assist.start`. `/risk` alone shows account limits, while `/risk BTC` runs the committee.
- **Recommended fix:** add a `/check` command, keep `/help <cmd>` for help, and rename `/risk COIN`.

### TG-14: Other bots' commands dropped from ingestion
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** production `09:59:21 Bot command received: /ban …` and `10:00:17 … /mute`. `is_ours()` treats any "/x" without an @ as ours.
- **Recommended fix:** queue only known commands, and store the rest as messages.

### TG-15: Join-welcome flood and footer spam
- **Severity:** Low
- **Status:** CONFIRMED (the handle mislabel is SUSPECTED)
- **Effort:** S
- **Evidence:**
  - `ingestion._welcomes` sends one welcome per batch with no cooldown.
  - `_Footed` adds the footer to every reply, and sends a separate message when the caption is full.
  - `FC_BOT_LINK` labels @botpricechat inconsistently.
- **Recommended fix:** a welcome cooldown per chat, and no footer on error or usage replies or as a standalone message in groups.

### TG-16: No owner alert chat; community messages feed nothing in production
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** the startup self-check (AR-2). The community.* jobs are off in production, but telegram.ingest stores every allow-listed message.
- **Recommended fix:** set the chat, and decide on data minimisation (see also SEC-12).

---

## C. Trading / Earn (TR)

TR-4 and TR-10 are in SECURITY-REVIEW.

### TR-1: A live position left without a stop alerts no one
- **Severity:** High
- **Status:** CONFIRMED
- **Effort:** S
- **Reproduction (code path):** the stop is refused, then `execution.emergency()` sets `PROTECTION_FAILED` and pauses the account, then `_retry_emergency` retries up to 10 times, then the position is "left to the member".
- **Evidence:**
  - `execution.py:564-588` and `1024-1041`.
  - `bot.due_position_announcements` announces only OPEN and CLOSED.
  - `app/alerts.py` is never called from tradingos or jobs.
- **Impact:** an AUTO entry at 3am with a refused stop and an unfilled emergency close sits unprotected, and nobody is told.
- **Recommended fix:**
  - Telegram DM to the member and the owner on `protection_failed`, on every failed retry, and when retries are exhausted.
  - A health check counting LIVE PROTECTION_FAILED with size > 0.
  - A red banner on /earn.

### TR-2: One member can use up the shared Hyperliquid request budget
- **Severity:** High
- **Status:** CONFIRMED (code) / SUSPECTED (exhaustion)
- **Effort:** M
- **Evidence:**
  - `HyperliquidExecutor.__init__` (`exchange.py:132-143`) builds an Info and an Exchange. SDK 0.24.0 creates a second Info, and each fetches `spot_meta` and `meta`: 4 calls × weight 20 = 80 weight per executor, against an IP limit of 1200/min.
  - Executors are rebuilt on every preview, confirm, reconcile (each minute per account with open positions), close and protection change.
  - `/members/trading/preview` has no rate limit.
- **Impact:** about 14 accounts with open positions use the whole budget on reconciliation alone. Scripted previews cause 429s, and then entries, closes and reconciliation fail for everyone.
- **Recommended fix:**
  - One Info per network, with meta passed in, and a cached Exchange per account.
  - Per-member limits on preview, confirm and close.
  - Track HL weight in-process and alert when it nears the limit.

### TR-3: Emergency and manual closes use an IOC limit only 0.24% from mark
- **Severity:** Medium-High
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `execution.py:574, 744`: `px = mark*(1∓3*SLIPPAGE)` with `SLIPPAGE=0.0008`. The SDK default is 5%.
- **Impact:** in a fast move, closes often get no fill. The emergency path leaves the position unprotected for about 10 more minutes, and a manual close says "Close not filled".
- **Recommended fix:** a 2-5% bound for reduce-only closes, with an immediate wider retry on "could not immediately match".

### TR-5: Banter referral gate changes the existing ENTER button (feat/tg-trading)
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S (decision)
- **Evidence:**
  - The bot.py diff adds `gate_for_entry` before `_enter`, contradicting the tgtrade docstring.
  - Existing tests had to monkeypatch the gate away (autouse `_banter_member`).
  - Website preview is ungated.
- **Impact:** non-Banter members can't ENTER from Telegram but can on the website. Each press costs at least 60 HL weight.
- **Recommended fix:** owner decision. Make the website and Telegram consistent and fix the docstring.

### TR-6: TESTNET trades priced and sized from mainnet data
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** M
- **Reproduction:** read-only comparison (scratchpad `tr/cmp.py`):
  - mids differ by HYPE +5.97%, ETH +0.44%, SOL +0.31%;
  - `maxLeverage` differs on 77 coins (APT 10x vs 3x);
  - 20 coins are missing on testnet.
- **Evidence:** `_plan` and `open_position` use `market.data().market(coin)` (mainnet) for every mode. `tgtrade._sizing` does the same.
- **Impact:** TESTNET entries often don't fill, stops can sit on the wrong side, and leverage can be refused. The tg branch makes TESTNET the default non-paper mode.
- **Recommended fix:** a network-aware market source.

### TR-7: Row lock for racing entries is a no-op on SQLite
- **Severity:** Low-Medium
- **Status:** CONFIRMED (lock no-op) / SUSPECTED (race)
- **Effort:** M
- **Reproduction:** audit test `audit_trading_tr_checks.py::test_sqlite_ignores_with_for_update`.
- **Evidence:** `execution.py:445` `with_for_update()`. The checks at 446-454 are check-then-insert.
- **Impact:** two entries in the same coin on different signals (website vs AUTO, 1h vs 15m, tg vs web) can both pass, and the position cap can be exceeded by one.
- **Recommended fix:** `BEGIN IMMEDIATE` or a dummy UPDATE first, or a partial unique index on (account_id, coin).

### TR-8: Take-profit orders on small positions may be under $10
- **Severity:** Medium
- **Status:** SUSPECTED (exchange behaviour UNVERIFIED)
- **Effort:** S
- **Reproduction:** audit test `test_min_bumped_position_gets_take_profit_orders_under_10_usd`: a $12 position gives TP orders of $6.60 and $6.90.
- **Evidence:** `protect()` splits TP1 and TP2 (`execution.py:550-560`). A failed TP is only logged.
- **Recommended fix:** a single TP when half the position is under $10, and surface TP refusals. Verify with one testnet order (needs owner approval).

### TR-9: Owner's min rank / deposit settings no longer gate LIVE
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S (decision)
- **Evidence:** `execution.eligibility` (81-89) requires only a verified wallet. The pre-existing failing test `test_trading_execution::test_referral_states_come_from_hyperliquids_record_and_no_longer_gate_bot_trading` (line 692) asserts the minimum still applies.
- **Impact:** the Admin settings do nothing. The red test on main is a real policy mismatch.
- **Recommended fix:** honour the settings, or delete the assertion and the UI fields.

### TR-11: Manual trades skip several limits (feat/tg-trading)
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `manual_plan` and `_sizing` skip `allowed_coins`, `style_limits`, Engine v2 `enforce` and `intraday_allowed` (compare `_plan` 299-313).
- **Recommended fix:** reuse the shared checks.

### TR-12: P&L booking can be incomplete
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `fills_since` makes one `user_fills_by_time` call with no paging (Hyperliquid returns at most 2,000 fills). A TP1 taken on the exchange only shrinks `size` (1005-1006).
- **Recommended fix:** page fills, and book partial fills during reconciliation.

### TR-13: Manual signal rows collide and are never cleaned up (feat/tg-trading)
- **Severity:** Low
- **Status:** CONFIRMED (code) / SUSPECTED (race)
- **Effort:** S
- **Evidence:** `_anchor` uses `min(number)-1` on a unique column. An IntegrityError is not caught. Every preview inserts a TsSignal, and brainx learns from manual trades.
- **Recommended fix:** retry, create the anchor at confirm time, and filter manual trades out of brainx.

### TR-14: Telegram claims an API wallet "can never withdraw" (feat/tg-trading)
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `tgtrade.py:19,147-150` vs `accounts.py:340-346`.
- **Recommended fix:** use the website's wording.

### TR-16: Switching account through the API key can orphan TESTNET positions
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `connect_api_key` blocks a master change only for LIVE with open positions (`accounts.py:508`). TESTNET reconciliation then finds nothing and marks the positions CLOSED.
- **Recommended fix:** block on any open position on any network.

---

## D. News Desk and content (ND)

ND-1 is listed at the top with AR-4.

### ND-2: No alert when the desk goes quiet
- **Severity:** High
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:**
  - `11:11:58 Owner alert (no owner Telegram chat set, logged only) [job:newsdesk.tick]`.
  - No "no release / no stories in N h" check exists.
  - A crashed tick never updates `last_run` (`engine.py:519-548`).
- **Impact:** both the 11-06 to 11-08 outage and ND-1 went unnoticed.
- **Recommended fix:**
  - Set the chat (AR-2).
  - Watchdog rules: no release in 18 h on a weekday, no new story in 3 h, 2 or more consecutive tick failures, Telegram status uncertain.
  - Show the last error on the News Desk page.

### ND-3: Scoring can't deliver the 3-4 posts a day plan
- **Severity:** High
- **Status:** CONFIRMED (replay + arithmetic)
- **Effort:** M
- **Reproduction:** `scratchpad\ndaudit\audit_replay100.py replay100.db 96` gives 93 stories, 20 passing evidence, and 8 releases in 96 h (0 at the weekend, only 3 of them news). About 25 candidates scoring 52-58 expired.
- **Evidence:**
  - Single-outlet news tops out at about 59 (`virality.py`).
  - The bar starts at 64 and reaches 56 only after 4.5 h (`events.py:441`), while freshness decays from minute 45.
  - Market candidates live 45 min.
- **Impact:** 0-2 posts per weekday and silent weekends. The channel looks dead.
- **Recommended fix:**
  - Pick the best candidate of each window, and hold market candidates for the whole window.
  - Give news a bar of about 52.
  - Use names-based corroboration (ND-14).
  - Re-run the replay weekly.

### ND-4: Dead feed, thin coverage, staleness invisible
- **Severity:** High
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:**
  - `mw_marketpulse` has 30 items, the newest **11,831 h old**, with `problem=None`.
  - Crypto items in 36 h: Cointelegraph 8, Decrypt 3, The Block 3.
  - `sources.fetch_feed` flags only HTTP errors and empty feeds.
  - CoinDesk, Blockworks, Bitcoin Magazine, CFTC and `mw_topstories` are live. The Treasury press RSS returns 404.
- **Recommended fix:**
  - Replace the feed with `mw_topstories`.
  - Add CoinDesk, Blockworks and DL News, plus DOJ, CFTC and OFAC.
  - Add a staleness flag per feed, and per-feed counts in Admin.

### ND-5: Topic filter rejects relevant crypto news
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `chaos.evaluate` on live stories (`ndwhy.py`) rejects ETF outflows of $1.28B, a crypto theft of $2.5M and "Binance's finance chief … to leave". 47 of 93 stories were rejected.
- **Recommended fix:**
  - Add `flows` and `theft` to CHAOS.
  - Add leave, depart and exit to the leadership words.
  - Keep a labelled headline regression set.

### ND-6: Category keywords match inside other words
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `engine.py:66` uses `rf"\b{re.escape(k)}"` with no closing `\b`. "Golden Boot" is stored as commodities.
- **Recommended fix:** `\b…\b` with deliberate stems.

### ND-7: X delivery depends on the whole tick; the tick blocks the bot
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** M
- **Evidence:** `deliver_x` runs only at the end of the tick (`engine.py:537-548`). "Telegram listener waited 185s … behind newsdesk.tick". 13 feeds and 20 quotes are fetched serially under a 240 s timeout.
- **Recommended fix:** separate try blocks, concurrent fetches, and feed fetching outside the serial lane (AR-3).

### ND-8: Admin can't see or control desk decisions
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** M
- **Evidence:**
  - `engine.overview` returns `desk`, but `NewsDesk.tsx` never renders it.
  - There are no hold, kill or release endpoints for NdEvent.
  - `NdTelegramSettings` has no dry-run or approval mode.
  - The policy is editable only as raw JSON.
- **Recommended fix:** a Desk panel, Hold / Release now / Kill actions, an optional Telegram approval mode, and an editable policy.

### ND-9: Dead Admin controls and dead code
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:**
  - "Breaking sensitivity" (`NewsDesk.tsx:73`) is read only by the dead `detect_markets`.
  - `target_per_day` and related settings don't affect the shared plan (fixed at 4 + 3).
  - `overview.writer` says "model" for X.
- **Recommended fix:** remove them, or wire them to `events.Policy`.

### ND-10: AI summary validator contradicts the prompt
- **Severity:** Medium
- **Status:** CONFIRMED (validator) / SUSPECTED (fabrication risk)
- **Effort:** S
- **Evidence:**
  - The prompt asks for 1-2 sentences, but `_from_model` accepts only 2-3, so one-sentence answers are dropped silently (the 11-06 post shows `writer=feed`).
  - The model's headline is ignored.
  - `checks.check_post` doesn't check names or causal claims.
  - Whether the Anthropic key is set in production is UNVERIFIED.
- **Recommended fix:** align the prompt and validator, log drops, add an entity check, and drop the unused headline request.

### ND-11: "Paused" badge outlives the pause
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `problem` is cleared only by a successful send or Resume. `TelegramLane.tsx:29` shows "Paused" whenever `problem` is set.
- **Recommended fix:** clear `problem` once `paused_until` has passed.

### ND-12: The engagement loop has no data
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** M
- **Evidence:** `engagement.adjustments` reads only published X posts, and X is in dry run. Telegram views (45-72) are never read.
- **Recommended fix:** pull Telegram view counts.

### ND-13: Post style probably hurts engagement
- **Severity:** Low
- **Status:** SUSPECTED
- **Effort:** S
- **Evidence:** channel posts 144-145: ALL-CAPS headlines, a "🥝 PRICE ALERT" header even on news, no context on market posts, about 50 views each.
- **Recommended fix:** sentence case, the header only on price posts, and one measured context line.

### ND-14: Clustering misses the same story in different words
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `engine.corroboration` uses fingerprint Jaccard ≥0.34 (`engine.py:108-110`). The replay missed CFO "to leave" vs "steps down", and MEXC across outlets.
- **Recommended fix:** use the existing `events._same` names rule for evidence and spread.

### ND-15: X path is correct (informational)
- **Severity:** Low
- **Status:** CONFIRMED (code + logs) / UNVERIFIED (X prices)
- **Effort:** S
- **Evidence:**
  - `publish()` short-circuits when not live, and production logs show only "newsdesk DRY RUN".
  - `no_link` blocks URLs.
  - Going live requires the confirm dialog plus guard checks.
  - The cost is configured at $0.015 per post without a link; X's actual price is UNVERIFIED.
- **Recommended fix:** none required.

---

## E. Kiwi Passport and gamification (PP)

PP-1, PP-2, PP-4 and PP-16 are in SECURITY-REVIEW. All PP tests are in the audit test `audit_passport_exploits.py` (9 passed).

### PP-3: Pack XP eats the overall cap while packs are uncapped
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S
- **Reproduction:** `test_pp3_…`. After "Joined" (+20) and 8 packs (+40), the daily visit pays nothing and `website.x_post_read` is refused with `overall_cap`. 12 more packs still pay 5 each.
- **Evidence:** `packs.py:285-286` stores `source="website"`, while the cap logic in `xp.py:323-329` uses `rule.verification_source` ("packs").
- **Impact:** "today's XP limit is reached" for reading and watching, while click-only packs keep paying.
- **Recommended fix:** a "packs" source label, or a consistent cap policy.

### PP-5: Royal (free VPN) reachable in about 2 weeks of click-only activity
- **Severity:** Medium
- **Status:** CONFIRMED (code + live rules)
- **Effort:** M
- **Evidence:**
  - Live `/api/members/rules`: packs 100/day, streak up to 25/day, telegram.message 50/day. All are outside the 60/day cap (`xp.py:27-29`).
  - Earn self-reports pay without verification (`tradingbot/service.py:589-627, 680-694, 775-830`).
- **Impact:** about 125-175 XP/day without attention. Knight in 10-13 days, then the PP-2 wallet jump to Royal and free VPN.
- **Recommended fix:** one overall cap for all non-staff sources, or treat click XP as cosmetic. Tenure plus verified activity for Royal benefits. Earn self-reports pay 0.

### PP-6: Daily caps are check-then-insert
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** M
- **Reproduction:** `test_pp6_parallel_awards_overshoot_a_daily_cap`: 30 threads paid **30 XP against a daily cap of 10**, on every run.
- **Evidence:** `xp.py:279-330`. The same pattern exists in `referrals.py:163-167`, `crew.py:141-145` and `packs.py:271-273`. There is no `BEGIN IMMEDIATE`.
- **Recommended fix:** serialise awards per member (`BEGIN IMMEDIATE`, a per-member lock, or a conditional counter UPDATE).

### PP-7: Weekly "Open 3 videos" paid from arbitrary client targets
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S
- **Reproduction:** `test_pp2_weekly_content_mission_paid_for_fabricated_opens`: three POSTs with targets "nonsense-0..2" pay mission 5 XP plus streak 5 XP.
- **Evidence:** `routes/members.py:202-217` and `missions.py:322-332`. No rate limit, so the DB can grow without bound.
- **Recommended fix:** count only server-recorded opens of real content, and rate-limit `/members/me/actions`.

### PP-8: Referral and crew anti-abuse is weak
- **Severity:** Medium
- **Status:** CONFIRMED (code)
- **Effort:** M
- **Evidence:**
  - Self-referral is caught only if the referrer stays signed in (`referrals.py:106-128`).
  - `browser_hash` comes from a `pc_auth` cookie that is recreated after each sign-in (`routes/members.py:525,590`).
  - 5 throwaway Telegram accounts in the crew pay 1,000 XP, with no clawback.
- **Recommended fix:**
  - A server-side IP/UA signal.
  - Pay crew after 7 days, and require a meaningful post or a linked Passport.
  - Claw back when the friend leaves.
  - A weekly referral cap.

### PP-9: Telegram/X sign-up never qualifies the invite
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Reproduction:** `test_pp8_signing_up_with_telegram_does_not_qualify_the_referral`.
- **Evidence:** `service.py:126-131` calls `link_identity` before `attribute()`, and `referrals.py:193-204` requires the event to come after the referral.
- **Recommended fix:** call `attribute()` first.

### PP-10: Watch timer bypassed by content-view
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Reproduction:** `test_pp5_content_view_pays_without_any_play_time`.
- **Evidence:** `routes/members.py:297-307`.
- **Recommended fix:** pay video XP only through the watch path.

### PP-11: "Every message becomes XP" rewards spam
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `verified.py:137-161`, `catalog.py:94-96`, and the copy in `tgbar.tsx:76`.
- **Recommended fix:** count only `meaningful()` messages, lower the cap, and reword the copy.

### PP-12: Duplicate Passports cannot be merged
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** M
- **Evidence:** `service.py:76-83` raises an `identity_conflict` flag instead.
- **Recommended fix:** a proven-control merge flow, or a staff tool.

### PP-13: Suspended members on leaderboards
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `leaderboard.py:28-29`.
- **Recommended fix:** add `xp_suspended.is_(False)`.

### PP-14: Copy promises features that are off
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:**
  - `xfeed.tsx:129` "Reply and repost…" while `x.write=False`.
  - Email is called "the website's sign-in" while email is off.
  - The x_reply / x_repost cards stay "Open X" forever (`missions.py:444-449`).
- **Recommended fix:** drive the copy from the providers endpoint, and hide those cards.

### PP-15: "Next steps" ignores the real levers
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `insights.py:164-168` ranks open missions only.
- **Recommended fix:** a server-computed "Road to <next rank>" panel.

---

## F. Kiwi VPN (VPN)

VPN-9 and VPN-14 are in SECURITY-REVIEW.

### VPN-1: Trial on, payments off: dead end around 2026-12-04
- **Severity:** High
- **Status:** CONFIRMED
- **Effort:** M
- **Reproduction:** `curl https://api.pricechat.live/api/members/vpn/public` returns `{"enabled":true,"payments":false,"trial":true,…}`.
- **Evidence:**
  - `service.py:42-46` `PAID_UNAVAILABLE`, `TRIAL_DAYS = 30`, `no_access_reason()` (161-165).
  - The reconcile and reminders exist only on feat/kiwi-payments (AR-16).
  - Anonymous keys are refused while payments are off (`vpn.py:240`).
  - Memory: no real payment has ever gone through.
- **Impact:** from about 2026-12-04, `vpn.sync` switches off every launch-cohort trial user, and nobody can pay.
- **Recommended fix, before 2026-12-01:**
  1. Rebase payments.
  2. Make one real ~$10 USDT payment.
  3. Turn payments on.
  4. Ship the reminders and terms (VPN-10).

  Otherwise, deliberately choose free mode or a tester grant. Show the end date in the apps.

### VPN-2: One device per server plus a 3-device cap
- **Severity:** High
- **Status:** CONFIRMED
- **Effort:** M
- **Reproduction:** audit test `audit_vpn_limits.py::test_audit_one_device_per_server_fills_the_cap`. One "PC" on 3 servers gets 201 three times, then the phone gets 409.
- **Evidence:**
  - Windows 2.0 `flow.rs:28-34,80-92` names devices "<host> (<server id>)".
  - Android `Provisioner.kt:81-90`, `DeviceIdentity.kt:25`.
  - `max_devices` is 3. There are 3 public servers. Smart Connect failover burns slots.
- **Recommended fix:** count devices, not peers. Add `PATCH /members/vpn/devices/{id}` to move a key between servers.

### VPN-3: Trial, devices and extension logins share one bucket
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S
- **Reproduction:** audit tests `test_audit_deletes_do_not_spend_the_device_limit` (3 creates, 3 deletes, 2 more succeed, the 6th gets 429) and `test_audit_browser_logins_and_devices_share_one_bucket`.
- **Evidence:**
  - `rate_limit.py:69` (burst 5, then 1 per 600 s), used by `vpn.py:69, 89, 135, 252` and `bot.py:641`. DELETE (`vpn.py:117-123`) is unlimited.
  - The extension re-logs in on every server change and recheck with no backoff (`background.js:63-66,104-111`).
- **Correction (coordinator, verified):** the earlier belief, in commit 334f45d's message and in memory notes, that device deletions count against the `vpn_device` limit was wrong. Trial start, device creation and extension logins share the bucket.
- **Recommended fix:** separate buckets, no charge for an in-place replace, extension backoff, and correct the comment and notes.

### VPN-4: Single provider, personal VPS, no backups
- **Severity:** High
- **Status:** CONFIRMED (code/DNS) / UNVERIFIED (Hetzner state, from memory)
- **Effort:** L
- **Evidence:**
  - `hel1` resolves to 2.29.149.29, the owner's personal VPS, with customers on wg1.
  - One Hetzner project. A roughly one-day full outage on 2026-11-05 when the account was blocked (memory).
  - No backups.
  - `install-shared.sh:202` opens 8443 to anyone.
- **Impact:** one abuse complaint takes every country offline. Customer abuse is attributed to the owner's personal IP. Rebuilding a node means re-keying every customer.
- **Recommended fix:**
  - Dedicated customer nodes and a second provider.
  - Encrypted off-box backups of `/etc/wireguard` and `/etc/kiwi-vpn`.
  - Honour `AGENT_ALLOW`.
  - An abuse runbook.

### VPN-5: Ashburn recommended to everyone
- **Severity:** Medium
- **Status:** CONFIRMED (live)
- **Effort:** S
- **Evidence:** live `/stats` shows Ashburn `"recommended":true`. `recommended_id()` picks the lowest load (`service.py:349-352`). 1 TB included, `monthly_cap_gb` 0, and alerts only on the monitoring branch.
- **Recommended fix:** region- or RTT-aware recommendation, weighting by remaining traffic, merge monitoring, and a fair-use cap.

### VPN-6: One failed sync marks a server offline
- **Severity:** Medium
- **Status:** CONFIRMED (code) / UNVERIFIED (owner chat; AR-2 later confirmed it is unset)
- **Effort:** S
- **Evidence:** `service.py:1029-1033`. A 503 is returned until the next sync (472-473, 608-609). The fix is on fix/kiwi-stage0 31c6148, undeployed. No external probe; `uptime: None`.
- **Recommended fix:** merge the flap guard, set the chat and send a test alert, and add an external probe on `/v1/health`.

### VPN-7: No update mechanism; Windows unsigned
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** M plus cost
- **Evidence:**
  - `tauri.conf.json` has no updater.
  - The live setup.exe SHA-256 077ae0f6…0598 shows `NotSigned`.
  - CI produces `…-unsigned-Setup.exe`.
  - The APK can't self-update.
  - The in-place upgrade from 0.1.1 to 2.0 is untested.
- **Recommended fix:** a signed version check (JSON plus Ed25519) or the Tauri updater, a CI upgrade test, and an owner decision on code signing (the price is UNVERIFIED).

### VPN-8: The Windows 2.0 kill switch is fail-open
- **Severity:** Medium
- **Status:** CONFIRMED (design). Real-tunnel behaviour was UNVERIFIED at review time. Later, GitHub Actions run 42479850219 passed the kill switch on a real tunnel, including a tunnel-process crash with no leak and auto-recovery in 14 s (coordinator; see TEST-EVIDENCE.md).
- **Effort:** S (copy) / M (design)
- **Evidence:** `guard.rs:1-11` and `firewall.rs:1-2` use a dynamic WFP session. docs/KIWI-2.0.md:41-45 release the block after 6 restarts, after 10 min without a handshake, and after a 120 s Pause. The website says "no kill switch" (true for 0.1.1).
- **Recommended fix:** copy that states when the block is released, and an optional persistent "block until I disconnect" mode.

### VPN-10: No VPN terms, AUP or refund policy
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S (owner/legal)
- **Evidence:** no such pages on main. The drafts are only on feat/kiwi-payments.
- **Recommended fix:** the owner reviews and publishes them before payments go on.

### VPN-12: Smart Connect has nothing to measure
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S (deploy)
- **Evidence:** `service.overview()` (421-422) sends no `proxy_host` or `live_load`. The client expects them (`api.rs:37-41`, `smart.ts:22`). Germany has no proxy. The CI run confirmed "Smart Connect timings unavailable (production doesn't send proxy_host)".
- **Recommended fix:** merge monitoring, and give every node a probe endpoint.

### VPN-13: Mobile readiness
- **Severity:** Medium
- **Status:** CONFIRMED (code) / UNVERIFIED (device behaviour)
- **Effort:** M
- **Evidence:**
  - RC1 declares `FOREGROUND_SERVICE_SYSTEM_EXEMPTED` and `BIND_NOTIFICATION_LISTENER_SERVICE`.
  - No physical-phone team test.
  - iOS has never run on an iPhone and has no in-app account deletion. The App Store needs an organization account.
- **Recommended fix:** strip Auto-Call from the Play flavour, run the device test plan, decide on an organization entity, and add account deletion.

### VPN-15: Testers-only servers keep working for ex-testers
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `sees()` (`service.py:248-250`) is checked only at creation.
- **Recommended fix:** check it in sync and `device_peer`.

### VPN-16: Trial abuse and silent trial start
- **Severity:** Low
- **Status:** CONFIRMED (code) / SUSPECTED (abuse)
- **Effort:** S
- **Evidence:** one trial per Passport (`service.py:206-224`). Windows 2.0 starts the trial right after sign-in (`flow.rs:126-137`).
- **Recommended fix:** start the trial on first Connect, and optionally hash the OAuth identity.

### VPN-17: Capacity is a device count
- **Severity:** Low
- **Status:** SUSPECTED
- **Effort:** S
- **Evidence:** capacity defaults to 250 (`models/vpn.py:41`). Load = devices / capacity. The Germany node has 1 GB RAM.
- **Recommended fix:** benchmark-based capacity plus live handshake counts.

---

## G. Website and UX (UX)

Full page-by-page detail and screenshots are in UX-DESIGN-REVIEW.md.

### UX-1: Crawlers get an empty shell
- **Severity:** High
- **Status:** CONFIRMED
- **Effort:** M
- **Reproduction:**
  - `curl -A Googlebot https://pricechat.live/pokemon/cards/5` returns `<title>Price Chat</title>`, 2,252 bytes.
  - `curl -A Twitterbot/1.0 …` returns the card title and canonical.
- **Evidence:** the `vercel.json` bot regex lacks Googlebot, bingbot, Applebot, DuckDuckBot, GPTBot, PerplexityBot and ClaudeBot. Heads are set client-side (`useHead.ts`).
- **Impact:** 25k+ card URLs look identical before rendering.
- **Recommended fix:** edge-inject the existing server heads plus a text summary for all requests, or prerender the 27 fixed pages.

### UX-2: Relative og:image on card previews
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S
- **Reproduction:** Twitterbot on `/pokemon/cards/5` gets `og:image="/cards/u/b5292d9dcf0b2022.webp"`.
- **Evidence:** `seo.py:149`, `seo/heads.py:157`.
- **Recommended fix:** an absolute URL, a 1200x630 JPG/PNG, and a test.

### UX-3: Home has no value proposition or CTA
- **Severity:** High
- **Status:** CONFIRMED
- **Effort:** M
- **Evidence:** `Website.tsx:362-389`, `41-48` and `800-824`. The mobile home page is 4,629 px tall. The header says "Login". Screenshots `ux-home-390.png`, `ux-home-1440.png`.
- **Recommended fix:** a value line plus a "Get your free Kiwi Passport" CTA, four live product tiles, and "Join / Sign in" in the header.

### UX-4: Market has no nav link
- **Severity:** High
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `Website.tsx:262-280, 296-316`, `websiteData.ts:82-90`. The only `/pokemon` link is in `giveaway.tsx:425`.
- **Recommended fix:** "Markets" in the header and mobile menu, and a product column in the footer.

### UX-5: VPN tab hidden from signed-out visitors
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `Website.tsx:252-254`. `/vpn` sells to anonymous visitors (`ux-vpn-390.png`).
- **Recommended fix:** show the tab to everyone, or make `/vpn` honest that it is members-only.

### UX-6: "$9.99 for 30 days" while payments are off
- **Severity:** High
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `Website.tsx:698`, `seo.ts` PAGE_HEADS.vpn, `backend/app/seo/heads.py:34`, the /vpn pricing section, and "USDT payments open soon." only inside billing (`vpn.tsx:867`). Same root cause as VPN-1.
- **Recommended fix:** "Free during beta" while `payments=false`, and drive all price copy from `/members/vpn/public`.

### UX-7: "Every rank unlocks a reward" is false
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `websiteData.ts:62`, `community.tsx:82` (locked in by `community.test.tsx:80,144`), and `rewards.py:30-47`: 4 of 10 ranks are none or TBA.
- **Recommended fix:** change the copy, or define the rewards first.

### UX-8: Earn gating copy contradicts itself
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** live `min_rank_level: 1` and "FREE · EVERY KIWI PASSPORT MEMBER" vs `rewards.py:36` "UNLOCK EARN" at Scout, and `tradingbot.tsx:349-350`.
- **Recommended fix:** pick one rule.

### UX-9: Legal pages are unreviewed drafts and incomplete
- **Severity:** High
- **Status:** CONFIRMED
- **Effort:** M (legal input)
- **Evidence:**
  - `community.tsx:418-420` shows the "Draft" banner.
  - The privacy text omits wallets, HL keys, Kalshi, email, the chat republishing and localStorage.
  - Terms has no entity, governing law, liability limit or refund terms.
  - The Disclaimer vs the BANTER referral.
  - No contact email.
- **Recommended fix:** legal review, a named entity and email, the missing categories, and a referral disclosure. See SEC-12.

### UX-10: Four visual systems
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** M
- **Evidence:** `website.css:1-20`, `vpn.css:7-25`, Earn purple/teal, and the market's OS theme (`mx.tsx:105-128`). Screenshots `ux-earn-1440.png`, `ux-pokemon-1440.png`, `ux-pokemon-390.png`.
- **Recommended fix:** one token set, shared components, and dark market by default.

### UX-11: Fake "9+" chat badge covers CTAs
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `chatBubble.tsx:62,83,155`. It overlaps "Sign in with wallet" on `ux-join-390.png`. It republishes unmoderated Telegram text (1 of 40 messages had a link).
- **Recommended fix:** no badge on a first visit, hide it on /join and /passport, and strip links and moderate the text.

### UX-12: Home CLS 0.12-0.35
- **Severity:** Medium
- **Status:** CONFIRMED (value) / SUSPECTED (cause)
- **Effort:** S-M
- **Evidence:** CDP 390x844, CPU ×4: `/` CLS 0.35 (repeat 0.122), `/pokemon` FCP 2,772 ms.
- **Recommended fix:** preload Archivo with fallback metrics, reserve the skeleton height, and split out lightweight-charts.

### UX-13: Slow Earn endpoints
- **Severity:** Low
- **Status:** CONFIRMED (single sample)
- **Effort:** S
- **Evidence:** `/api/members/arbitrage/lunch` took 5.15 s and `/funding` 3.19 s.
- **Recommended fix:** a 15-30 s server cache, or precompute.

### UX-14: "Earn" showcases a losing strategy
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** live lunch stats `trades 25975, win_rate 13.6, pnl_sol -421.188`. The track record returns `{"configured":false}`.
- **Recommended fix:** rename the tab to "Bots" or "Lab", lead with an audited record, and put experiments behind a "Research" label.

### UX-15: "Giveaway" awards no prizes
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `Website.tsx:268`. The page itself says "you don't win real cards" (`ux-giveaway-390.png`). Official pack artwork is used (IP; legal).
- **Recommended fix:** rename it "Open Packs" and move it under Markets.

### UX-16: Small social-proof counters
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** YouTube 43, X 56, Telegram 506 (`Website.tsx:640-680`).
- **Recommended fix:** show a counter only above a threshold, and lead with Telegram.

### UX-17: Legacy /website/* URLs with no 301
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `Website.tsx:313, 338, 822, 826`, `websiteData.ts:82-90`, `main.tsx:67-77`. `/privacy` and `/terms` return 404.
- **Recommended fix:** move the legal pages, add 301s, and update the links and sitemap.

### UX-18: Duplicate OG/Twitter tags
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `useHead.ts:39-58` vs `index.html:15-29`.
- **Recommended fix:** update the static tags in place.

### UX-19: Thin and members-only URLs in the sitemap
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `/learn` (`ux-learn-390.png`), `/join` and `/earn/brain` (401) are in `pages-1.xml`.
- **Recommended fix:** add teaser content, or noindex and remove them.

### UX-20: Housekeeping
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:**
  - Unreferenced `adam.png` (2.1 MB) and `kiwi.png` (1.97 MB).
  - The APK (15.5 MB) and EXE (3.6 MB) are in git.
  - 3 TikTok meta tags and 6 verification files.
  - "Disclaimer" appears twice in the footer.
  - No skip link.
  - `styles.css` (31 KB) loads on every public page.
- **Recommended fix:** clean up each item. Serve the binaries from Releases or R2.
