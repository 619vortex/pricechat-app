# Architecture, admin dashboard, backend/database, performance & reliability (AR)

Reviewer scope: admin dashboard (frontend/src/AdminApp.tsx + pages/*, their backend routes), backend architecture, SQLite-on-volume,
scheduler, performance, deployment, backups/DR, monitoring. Code read at audit worktree = main e978cc3. Production evidence from
`railway logs` (read only) of the live deployment 20ebb668 (started 2026-11-08 05:14 UTC) and the previous one df5d96bd.

## Summary

The code is better engineered than its operations. Admin auth is careful (scrypt hash, server-side sessions, HttpOnly cookies,
deny-by-default `admin_guard`, Origin check on writes, rate-limited login), there is almost no dead code (only `tradingos/edge.py`
is reached by tests alone), and indexes are deliberately tracked in `models/perf_indexes.py`. The real risks are in how it runs:
**no backup has ever succeeded** (system.backup fails every night with SignatureDoesNotMatch, still failing 2026-11-07), and
**every alert goes nowhere** (no owner Telegram chat). Because migrations run automatically at boot and there is no down-path, a bad
deploy cannot be rolled back without a database restore, and there is nothing to restore from. Production has no build identifier
(`version: null`), no CI, and is deployed by `railway up` from a laptop working tree (one such deploy put the frontend on the api
service on 2026-11-03). Railway's Config-as-Code (railway.json: healthcheck, restart policy) stops being honoured on **2026-12-01**.
Performance: one global scheduler lock serialises all 44 production jobs behind the Telegram listener (90 s holds), the
public Pokémon market stalls 10-39 s **every 15 minutes** because the cache TTL (900 s) outlives the warm-up interval (600 s), and
News Desk has failed every run since 10:54 UTC today (UNIQUE constraint; also ND-1). Biggest opportunities: fix the bucket keys +
set the owner alert chat (an hour of work that makes everything else observable), add a build SHA + git-based deploys, give the
Telegram listener its own task outside the job lock, and switch the market cache to refresh-ahead.

## Findings

### AR-1: No database backup has ever succeeded; nightly job still fails (SignatureDoesNotMatch)
Severity: Critical · Status: CONFIRMED · Effort: S (credentials) / M (verified restore drill)
Evidence: `railway logs df5d96bd... | grep backup` →
`2026-11-07 22:45:10 | WARNING | app.scheduler.scheduler | Job system.backup failed: ClientError: An error occurred (SignatureDoesNotMatch) when calling the CreateMultipartUpload operation` followed by
`Owner alert (no owner Telegram chat set, logged only) [job:system.backup]: Price Chat alert: Nightly database backup FAILED`.
Owner memory note records 12/12 failures 2026-10-23..11-03; this extends it to 11-07. Code: app/services/db_backup.py:64-80 streams a
gzip of the 1.17 GB SQLite file (db_reclaim log: "volume price_chat.db: 1170.6 MB") via `upload_fileobj` to the Railway bucket
`portfolio-photos` using the Brand Manager/portfolio S3 client (app/brand_manager/store.py:33-42).
Impact: everything (members, XP, wallets, trading accounts, VPN billing, market) is one SQLite file on one volume in one region (iad).
Volume loss/corruption or a bad migration = total, unrecoverable data loss.
Likely cause (SUSPECTED, can't read env): bucket credentials rotated/invalid after the 2026-10-21 Railway incident, or botocore ≥1.36
default checksums on a non-seekable multipart stream with an S3-compatible endpoint (requirements pin only `boto3>=1.35`; local venv has botocore 1.40.76).
Fix: (1) take a manual copy now via `GET /api/system/db-backup` (owner-only route, system.py:23); (2) fix PORTFOLIO_S3_* (check with a
one-off `head_bucket` + small `put_object`); if credentials are fine, set `Config(request_checksum_calculation="when_required",
response_checksum_validation="when_required")`; (3) enable Railway volume backups as a second, independent copy; (4) run a restore drill
into a scratch environment and document the RTO. Make "last successful backup age" a health check.

### AR-2: Owner alerting is wired but delivers nothing; failures are invisible
Severity: High · Status: CONFIRMED · Effort: S
Evidence: live startup log `05:14:13 | WARNING | pricechat.ops | Startup self-check: 1 problem(s): owner alerts have no Telegram chat ... alerts are only logged`;
today `Owner alert (no owner Telegram chat set, logged only) [job:newsdesk.tick]` (11:11, 11:33), `[job:telegram.ingest]` (09:19); 2026-11-06 02:32-02:51
`[vpn:offline:1]` / `[vpn:offline:2]` "Kiwi VPN server offline" repeated, nobody told. Code: main.py owner_alert → app/alerts.py; observability.Watchdog.
Impact: backup failure (AR-1), News Desk outage (AR-4), VPN servers offline for paying customers, 5xx bursts — all silent. The same item
has been on the owner's to-do since 2026-10-23.
Fix: set the owner chat in Admin › Settings › Background jobs › Owner alerts and press "Send test" (POST /api/system/alerts/test). Add an
external uptime monitor on `/api/health/public` (it exists for that purpose) so a dead process (which can't alert about itself) is noticed.
Make the startup self-check problem visible as a red banner on the admin Overview.

### AR-3: One global lock serialises every background job; the Telegram listener holds it ~90 s per run
Severity: High · Status: CONFIRMED · Effort: M
Evidence: app/scheduler/scheduler.py:88 `self._serial = asyncio.Lock()`; run_job (175-194) acquires it for every job. telegram.ingest
runs every 20 s and long-polls for `telegram_listen_seconds` = 90 (settings.py:70) inside that lock (telegram/ingestion.py:124-137).
Log: `05:18:18 | WARNING | Telegram listener waited 148s for its turn (behind pokemon.exchange.auto)`; owner notes record 145 s behind
pokemon.sync/tradingos.scan. Reproduced: backend/tests/audit_scheduler_serial_starvation.py (passes) — a 20 s-interval job waits the
full length of a long job, and a job doing synchronous work blocks past its own `asyncio.timeout` (timeout 0.1 s, ran 0.6 s, recorded "success").
Impact: 44 jobs share one queue. Jobs with tight contracts — tradingos.vortex_expire (every 20 s, "answer within 60 s"),
committee.llm_expire (30 s), tradingos.track (60 s, live signal cards / auto-trading), pokemon.exchange.auto (15 s) — cannot keep
them while the bot listens; conversely any long job (pokemon.sync ≤600 s, pokemon.catalog/top100 ≤900 s, backup ≤1800 s) silences the
bot for its duration. Most jobs also run synchronous SQLAlchemy work directly on the event loop (only 16 `to_thread` uses in jobs.py
across 60 jobs), which stalls every HTTP request in the same process (admin_guard is async middleware on every request) and makes
job timeouts unenforceable.
Fix: run the Telegram listener as its own long-lived task (not a scheduled job); split the scheduler into lanes (realtime ≤60 s jobs,
heavy batch jobs) with a per-lane lock or a small semaphore, relying on SQLite's own write lock + short transactions; push heavy sync
jobs through `asyncio.to_thread`. Longer term, move batch jobs to a separate worker service (needs a server DB, see AR-12).

### AR-4: News Desk (X + Telegram news lane) has failed every run since 10:54 UTC today
Severity: High · Status: CONFIRMED · Effort: S
Evidence: logs `10:54:00 / 11:00:33 / 11:11:58 / 11:33:18 | Job newsdesk.tick failed: IntegrityError: UNIQUE constraint failed: nd_stories.link`
(Cointelegraph's recycled "what-happened-in-crypto-today" URL). app/newsdesk/engine.py:84 builds `seen` from rows created in the last
4 days, but rows are purged only after 7 days (line 103) → a link stored 4-7 days ago is re-inserted → the commit at line 104 fails →
`tick()` aborts before events.decide / deliver_x (engine.py:535). Reproduced by backend/tests/audit_newsdesk_collect_wedge.py (passes;
written by the News Desk reviewer, see ND-1). Alerts for it went nowhere (AR-2).
Impact: no new stories for up to ~3 days at a time, recurring; scheduler backs off exponentially meanwhile.
Fix: build `seen` from all stored links (or the 7-day window), and/or `INSERT ... ON CONFLICT(link) DO NOTHING`; isolate per-feed inserts.

### AR-5: Admin "Background jobs" shows server-disabled jobs as enabled; toggling them reports success but does nothing
Severity: Medium · Status: CONFIRMED · Effort: S
Evidence: app/api/routes/jobs.py:12-39 `enabled=row.enabled` with no server-level state; `JobOut` has no field for it; JobsCard.tsx never
mentions it. Production turns off 16 jobs via SCHEDULER_ONLY_JOBS/SCHEDULER_DISABLED_JOBS (log line 05:14:13 "Background jobs turned off on
this server: approvals.expire, combot.sync, community.*, content.opportunities, cross.signals, knowledge.reindex, opportunities.rank,
topic.merges, tradingos.funding_alerts, workers.verify, x.ingest, x.opportunities"). Test backend/tests/audit_jobs_server_disabled.py (passes):
x.ingest is in `scheduler.disabled`, GET says enabled, PATCH enabled=true answers "enabled", only "Run now" reveals "disabled".
Impact: owner believes community analysis, knowledge indexing, X reading, approvals expiry, worker verification are running in production;
the Community, Knowledge, X opportunities and Approvals admin pages show stale data with no explanation (this caused the earlier stale
Read & Earn incident, per memory). Also tradingos.funding_alerts is pinned off by an env var the Admin checkbox can't override.
Fix: add `server_state: "off_by_env" | "on"` to JobOut, show a grey "Off on this server (SCHEDULER_ONLY_JOBS)" badge, disable the toggle,
and put a banner on pages whose producing jobs are off.

### AR-6: Public Pokémon market stalls 10-39 s every 15 minutes (cache TTL outlives the warm-up)
Severity: High · Status: CONFIRMED · Effort: S
Evidence: pokemon/market.py:156 `BASE_TTL = 900.0`; jobs.py:1000 `pokemon.warm` every 600 s calls `market.base_rows`, which returns the cached
rows while fresh (market.py:173, history.cached) — so the warm at T+600 is a no-op, the cache expires at T+900 and the next visitor
rebuilds ~50k rows inline until the warm at T+1200. `railway logs --filter SLOW` (2066 lines, 05:14-11:43): 112 requests ≥10 s, clustered at
:03/:06, :21, :36, :51 of every hour (e.g. `10:08:28 GET /pokemon/market 200 14637ms`, `10:08:52 GET /pokemon/cards/{card_id}/chart 200 17898ms`);
per-route: /pokemon/cards/{id}/chart 778 slow (avg 2.5 s, max 33 s), /pokemon/market 566 (avg 3.0 s, max 39 s), /pokemon/cards/{id}/quality 215
(avg 3.9 s, max 35.8 s). After a deploy the first 30 s are worse (`05:14:43 .../quality 200 26739ms`).
Impact: public site and admin pages hang; each rebuild holds one of the 10 public market slots and ~200-400 MB RAM (comment in market.py:171),
so stacked visitors get 503 "market is busy".
Fix: refresh-ahead — let the warm job force a rebuild (swap-in on success) at an interval shorter than the TTL, and serve stale rows while
rebuilding; persist the computed rows (a table or file) so a restart doesn't start cold. Also make `rank_of` (market.py:548, called by every
chart request) a lookup in a precomputed {card_id: rank} map rather than `market(..., MAX_LIMIT=250)` + possible full `rows_for`.

### AR-7: Migrations run automatically at boot with no rollback path; rollback = restore, and restore is impossible today
Severity: High · Status: CONFIRMED · Effort: M
Evidence: app/db/session.py `init_db()` → app/db/migrate.py:39-57 `command.upgrade(config, "head")` in the app lifespan; railway.json start
command runs it on every boot. If the image is rolled back after a migration, the DB's revision is unknown to the older code and alembic
fails at startup ("Can't locate revision"), so the app won't boot. 3 migrations have empty downgrade (0054, 0066, 0072); downgrades are never
exercised in tests.
Impact: every deploy with a migration is one-way. Combined with AR-1 there is no recovery from a bad migration or data-destroying bug.
Fix: (a) restore working backups first; (b) take a pre-deploy snapshot automatically (run the backup step before `upgrade` when the script
head differs from the DB head); (c) write migrations expand/contract so the previous release still runs on the new schema; (d) document a
rollback runbook (Railway "Redeploy" of previous deployment + `alembic stamp` rules).

### AR-8: Deploys are unreproducible: laptop `railway up`, no CI, no build id, unpinned dependencies
Severity: High · Status: CONFIRMED · Effort: M
Evidence: no `.github/workflows` in C:\AI\price-chat or the audit worktree; memory note "deploy: `cd backend; npx @railway/cli up --service api --ci`"
(uploads the local working tree, including uncommitted files); 2026-11-03 19:03 outage: deployment 35f36b4e on service `api` was the FRONTEND
(memory note) — nothing prevents a repeat; `curl https://api.pricechat.live/api/health/public` → `"version":null` (health.py `_version()` reads
BUILD_ID/RAILWAY_GIT_COMMIT_SHA, unset with `railway up`). `railway deployment list`: 20 deployments 2026-11-03..11-08, 3 FAILED. requirements.txt
uses `>=` for every package, no lockfile; prod runs SQLAlchemy 2.1 (error link `sqlalche.me/e/21`) on Python 3.14; pytest is in the production
requirements. The 1951-test suite is run by hand.
Impact: nobody can say which commit is live; a broken deploy can't be traced or reproduced; a dependency release can change prod behaviour
on any deploy (AR-1's checksum suspicion is exactly this class); each deploy restarts the bot and scheduler (single replica + volume →
brief downtime, re-run of startup stampede).
Fix: connect the Railway service to the GitHub repo (deploy from `main` only, root dir `backend`), add a GitHub Action that runs the backend +
frontend tests and blocks the deploy; set BUILD_ID; generate a lock (`uv pip compile` / `pip-tools`) and move pytest to requirements-dev.
Until then, give `railway up` the path explicitly (`railway up backend --service api`) and add a guard in railpack/railway.json (e.g. fail the build if
`app/main.py` is missing).

### AR-9: Railway Config-as-Code (railway.json) stops working on 2026-12-01
Severity: Medium · Status: CONFIRMED · Effort: S
Evidence: every `railway` CLI call prints `warning: Config as Code (railway.json / railway.toml) is deprecated ... Existing files keep working until 2026-12-01.`
backend/railway.json holds the healthcheck path/timeout, `numReplicas: 1`, restart policy and the start command (railpack.json duplicates only the start command).
Impact: after 2026-12-01 a deploy may lose the health check (a broken build goes live), restart policy, and the single-replica guarantee (two
replicas would double-run every job and can't share the SQLite volume).
Fix: `railway config migrate` to `.railway/railway.ts` before December, or set these in the service settings, and verify on the next deploy.

### AR-10: Admin sessions survive a password change; single all-powerful role; no session list / revoke
Severity: Medium · Status: CONFIRMED · Effort: S
Evidence: app/security/admin_auth.py:209-221 `current()` checks only token hash + expiry; known-device cookies are keyed on the password hash
(they ARE revoked), sessions are not. Test backend/tests/audit_admin_session_survives_password_change.py (passes). There is exactly one admin
identity (ADMIN_USERNAME/ADMIN_PASSWORD_HASH), no roles, no 2FA; every non-public /api route has equal power (VPN servers, live-trading allowlist,
member deletion, DB download, secret store). `last_seen_at` is set once and never updated. CSRF: SameSite=Lax cookie + production Origin check on
writes (main.py admin_guard) — adequate; requests without an Origin header are allowed (fine for browsers).
Impact: after a suspected compromise (the Railway incident), rotating the password leaves a stolen session usable for up to ADMIN_SESSION_HOURS (default 12, max 720).
Fix: store a credential fingerprint in AdminSession and reject on mismatch (or delete all sessions at startup when the hash changes); add "sign out
everywhere" + an active-sessions list; consider TOTP for the owner; consider a read-only "viewer" role if anyone else gets access.

### AR-11: Sensitive admin actions are not audit-logged (and 9 separate audit tables exist)
Severity: Medium · Status: CONFIRMED · Effort: M
Evidence: no audit/activity entry for: admin login success/failure (admin.py:32-47), full database download (`GET /api/system/db-backup`, system.py:23 —
contains all member PII), `/system/qa-member-session` (mints a member session token), `/system/rekey`, API-key save/delete (api_keys.py:141-157),
every VPN admin action (vpn.py:294-440: block account/key, add/delete server, delete device — no audit/logger call in vpn/service.py for
set_blocked/block_key/delete_server/add_server). Meanwhile trading has TsAudit (`signals.audit`, e.g. trading.py:470,482), and there are ExAudit,
MarketAudit, MemberAuditLog, RewardAudit, TbAudit, WkAudit, the VPN ledger audit and ActivityLog — 9 separate stores with no unified view.
Impact: no forensic trail for exactly the actions an attacker with a stolen session would take.
Fix: one `admin_audit` table written by a small dependency/middleware for every non-GET admin request (route template, actor session id, IP key,
outcome) plus explicit entries for login and DB download; an Admin › Activity filter for it.

### AR-12: SQLite on one volume is now the main scaling and reliability limit
Severity: Medium · Status: CONFIRMED (contention) / SUSPECTED (future growth) · Effort: L
Evidence: 1.17 GB DB, WAL + busy_timeout 15 s (db/session.py:44-55), pool 10+30 (session.py:14-15); one process runs the API, the Telegram bot,
44 jobs and in-process caches of ~200-400 MB per grade; Railway status: single `api` service, volume 4.0/48.8 GB, region iad. History of
QueuePool exhaustion and 30 s responses (owner notes 2026-10-23, 10-31), mitigated by the market concurrency gate (main.py MARKET_CONCURRENCY=10).
Impact: no horizontal scaling, no separate worker, no read replica, deploy = downtime, a single writer lock shared by bot, jobs and members.
Fix (staged): keep SQLite but fix AR-3/AR-6 first (they remove most contention); then move to Postgres (code already handles it: pool_pre_ping,
DataError handler) so jobs can run in a separate worker service and backups become managed point-in-time.

### AR-13: PROXY_SHARED_SECRET still unset: all IP-based rate limits key on a forgeable header
Severity: Medium · Status: CONFIRMED · Effort: S
Evidence: live log `05:14:13 | WARNING | app.security.client_ip | PROXY_SHARED_SECRET is not set: rate limits still trust the left-most X-Forwarded-For entry, which direct callers can forge.`
vercel.json injects `x-pc-edge` from `$PROXY_SHARED_SECRET`. Open since 2026-10-23 (owner to-do item 4).
Impact: admin-login per-client limit, market crawler limit, VPN/pay limits can be bypassed by rotating X-Forwarded-For (the global login bucket
remains as a backstop; known-device cookie protects the owner). Overlaps the security reviewer's area.
Fix: generate one value, set in Vercel (Production) then Railway, redeploy frontend; confirm the "Website traffic carries the proxy secret" log line.

### AR-14: Long-running admin "Run now" holds an HTTP request behind the job queue
Severity: Low · Status: SUSPECTED · Effort: S
Evidence: jobs.py:42-48 `await scheduler.run_job(name, trigger="manual")` — waits for the global lock (AR-3) and the job itself (up to its
timeout, e.g. 900 s) inside the request, proxied through Vercel's external rewrite.
Impact: the button errors/time-outs at the proxy while the job keeps running; the owner may click again (gets "Already queued").
Fix: enqueue and return 202 with the queue position (`scheduler.queue()` already exists); poll status.

### AR-15: Production AI provider is Ollama at its default address (possibly unreachable)
Severity: Medium · Status: SUSPECTED (env values not readable) · Effort: S
Evidence: live log `Price Chat API ready: provider=ollama model=llama3.1:8b embeddings=nomic-embed-text`; settings.py:36,46 default
`llm_provider="ollama"`, `ollama_base_url="http://127.0.0.1:11434"`; the production validator (settings.py:349-375) checks localhost for
MEMBERS_PUBLIC_URL/DASHBOARD_URL/CORS only, not OLLAMA_BASE_URL. No Ollama runs in the Railway container.
Impact: if OLLAMA_BASE_URL isn't pointed at a reachable host, every feature using `runtime.provider` / embeddings (AI Brain chat, agent replies,
knowledge search) fails in production while the dashboard shows the model as configured.
Fix: check Admin › AI Brain model status in prod; add OLLAMA_BASE_URL to the production validator / startup self-check when LLM_PROVIDER=ollama.

### AR-16: Unmerged branches carry a conflicting migration; branch/worktree sprawl
Severity: Medium · Status: CONFIRMED · Effort: S
Evidence: `git branch --no-merged main` = 17 of 39 local branches; `git worktree list` = 29. feat/kiwi-payments adds
`0103_vpn_payment_recovery` with `down_revision='0102_desktop_codes'` while main already has 0103_newsdesk_telegram → 0104 → 0105; merging it as-is
creates two alembic heads, and boot-time `command.upgrade("head")` then fails ("Multiple head revisions") → the deploy won't start. (test_migrations
uses `get_current_head()`, which would catch it — only if someone runs the tests, see AR-8.) feat/kiwi-monitoring's 0106 (down 0105) is fine today but
will collide with the payments fix. Several branches are 17 commits behind main (feat/kiwi-android, -android-rc, -autocall, -downloads, -ios, -unified).
21 merged branches were never deleted.
Fix: rebase feat/kiwi-payments and renumber its migration onto 0105/0106; add a pre-merge check `alembic heads | wc -l == 1`; delete merged branches
and prune worktrees; set a 14-day stale-branch rule.

### AR-17: Heavy work runs in the API process (rendering, ML, catalogue builds)
Severity: Low · Status: SUSPECTED · Effort: M
Evidence: numpy/PIL in tradingos/chart.py, tradingos/ml.py, newsdesk/card.py, portfolio/photos.py; whole-catalogue market builds (~200-400 MB each,
`history._heavy` lock); `/api/system/db-backup` copies the 1.17 GB DB inside a request thread. Video generation is correctly off-box (worker/ on the
owner's DGX). No OOM/restart seen in today's logs (one "Started server process").
Impact: CPU spikes share the event loop/threadpool with members and the bot; memory headroom on a single container.
Fix: precompute chart images and market rows in jobs (AR-6), stream the DB download from a background-made file, watch Railway memory metrics.

### AR-18: Paper Exchange is retired but still writes every 15 s
Severity: Low · Status: CONFIRMED · Effort: S
Evidence: settings.py:246 `exchange_enabled: bool = False`; jobs.py:737-744 `pokemon_exchange_auto` always calls `ex_maker.prune` (sync DB write on the
event loop) every 15 s, under the global job lock; `pokemon.exchange` and `pokemon.exchange.auto` are in production via the `pokemon.` prefix.
Impact: 5,760 lock acquisitions/day for a retired feature; it was what the bot waited behind at 05:18.
Fix: once the backlog of dead quotes is pruned (db_reclaim reports 0), drop both jobs from SCHEDULER_ONLY_JOBS (use explicit names instead of `pokemon.`).

### AR-19: Health endpoint says "scheduler ok" while jobs are failing
Severity: Low · Status: CONFIRMED · Effort: S
Evidence: `curl https://api.pricechat.live/api/health/public` → `{"ok":true,"db":"ok","scheduler":"ok","version":null}` at 11:47 UTC while
newsdesk.tick had failed 4× and the last backup failed. health.py:68-80 checks loop liveness/staleness only.
Fix: add "backup age < 36 h" and "no enabled job with ≥3 consecutive failures" to the owner health (/api/health) and an ok/warn word to the public one.

## SYSTEM MAP

### Services and hosting
- **api** (Railway project `pricechat`, env production, service `api`, region iad): FastAPI + uvicorn single process, `numReplicas: 1`, volume
  `api-volume` at /data (4.0 / 48.8 GB; price_chat.db 1.17 GB, secrets.json, cache/). Start: `python -m app.db_reclaim; exec uvicorn app.main:app
  --proxy-headers --forwarded-allow-ips=* --no-access-log`; healthcheck `/api/health/live` (120 s); restart ON_FAILURE ×5. Builder RAILPACK, Python 3.14.
  Also hosts the in-process scheduler, Telegram bot long-poll, watchdog, WebSocket tapes (Hyperliquid, Bybit, OKX, PumpPortal).
- **Railway bucket** `portfolio-photos` (S3-compatible): portfolio photos, Brand Manager media, DB backups (backups/db/, keep 14 — none exist, AR-1).
- **Frontend** (Vercel project `pricechat`, https://pricechat.live): Vite/React SPA. `/admin/*` → AdminApp (main.tsx `isAdmin`), else public site.
  vercel.json: `/api/*` rewritten to https://api-production-b1be.up.railway.app with `x-pc-edge: $PROXY_SHARED_SECRET`; social-bot UA → OG pages;
  sitemaps → backend; security headers + CSP.
- **Off-platform**: worker/ (Brand Manager video worker on the owner's DGX Spark, outbound HTTPS with own token); Vortex (owner's home AI on a NUC,
  long-polls /api/vortex/*); Kalshi bot (C:\AI\kalshi-bot, posts to /api/kalshi/bot/*); Kiwi VPN nodes (vpn-node/, agent API polled by vpn.sync);
  vpn-extension/, windows-client/ (clients of /api/members/vpn/*); fundarb/ (library used by app/arbitrage/funding.py).
- **Databases**: one SQLite file (WAL, synchronous=NORMAL, busy_timeout 15 s, pool 10+30). No Redis, no Postgres. In-process caches (market rows).
- **CI/CD**: none. No GitHub workflows for Price Chat. Backend deployed by `railway up` from a local tree; frontend by `vercel --prod`.

### Route modules (all mounted under /api in main.py; private unless listed in admin_auth.PUBLIC_*)
| Module | Serves |
|---|---|
| health | /health (owner detail), /health/live (Railway), /health/public (uptime, public) |
| admin | owner login/logout/session (public) |
| home, overview | admin home "what's waiting", overview tiles |
| agent, brand, model, knowledge, content, connectors, activity, settings (/settings/safety), api_keys (/settings/api-keys) | AI Brain chat/config/prompt, brand goals, model routes/test, knowledge sources/search/reindex, content items, connectors list, activity log, safety/kill switch, provider API keys |
| telegram, telegram_account, community | bot connection/ingestion, Telethon account mode, community topics/questions/summaries/pilot |
| opportunities, social, growth, analytics | content opportunities, approvals/drafts/feedback/cross-signals/command center, growth pilot, growth analytics |
| x, newsdesk (/x/newsdesk) | X connection/monitoring/posts/opportunities; News Desk settings/posts/telegram |
| youtube, website, seo | YouTube sync/OAuth; public website content (home/latest/content/chat are public); sitemaps/OG (public prefix /api/website/seo/) |
| members (public prefix /api/members/), members_admin (/members-admin) | member sign-in & Passport; admin moderation/XP/ranks/badges |
| pokemon, portfolio, market, exchange, vault | Pokémon market (owner + sanitized public GETs via market_public), portfolios, card marketplace, Paper Exchange (retired), Vault (eBay sellers) |
| brand_manager, short_market | Brand Manager (TikTok automation; /worker/ and /media/ public w/ own auth), Short Market (/worker/ public) |
| invites, workers, rewards | Invite Bot, worker system, rank rewards + Vortex Realm |
| tradingbot, tradingos, trading, kiwibrain, arbitrage, committee, vortex, kalshi | VIP trading bot funnel (+ /api/r/ referral links), Trading OS admin + public charts, trade execution (member + admin execution/allowlist), Kiwi Brain, Earn Lunch/Funding (public read-only), AI Investment Committee (public prefix), Vortex worker API (bearer), Kalshi (bot reports public prefix w/ bearer) |
| vpn, desktop, burner | Kiwi VPN member/admin/payments/webhooks, VPN desktop sign-in, Passport burner wallet |
| system | DB download, email domain (Resend), QA member, owner alerts settings/test, rekey |
| webhooks | inbound signed webhooks (public prefix) |
| jobs | list / run / enable scheduled jobs |
Dead code: none routed-but-unused found; `app/tradingos/edge.py` is used only by tests; `frontend/src/pages/ConnectorPage.tsx` is imported nowhere.

### Scheduled jobs (60 defined in app/jobs.py; default intervals from settings — JOB_*_SECONDS env vars can override; "prod" from the live
"turned off" log line 2026-11-08 05:14)
| Job | Interval | Prod |
|---|---|---|
| telegram.ingest | 20 s (+90 s listen) | on |
| tradingos.vortex_expire | 20 s | on |
| pokemon.exchange.auto | 15 s | on |
| committee.llm_expire | 30 s | on |
| tradingos.kitchen | 45 s | on |
| tradingos.riskteam, tradingos.alerts, tradingos.track, brand.tick, tradingos.lunch_bot, tradingos.autodelete, pokemon.exchange | 60 s | on |
| tradingos.structure, pokemon.market_expire | 120 s | on |
| newsdesk.telegram, invites.poll | 180 s | on |
| tradingos.shadow, tradingos.brief, newsdesk.tick, vpn.sync, committee.paper | 300 s | on |
| members.verify, system.alerts, pokemon.warm | 600 s | on |
| tradingos.scan, tradingos.oi, tradingos.ideas, x.own, youtube.sync, committee.outcomes, pokemon.pulse | 900 s | on |
| brand.trends | 1800 s | on |
| tradingbot.affiliate, pokemon.catalog | 3600 s | on |
| vault.sync | 3 h | on |
| growth.snapshots, pokemon.top100, pokemon.portfolios, pokemon.sync | 6 h | on |
| newsdesk.metrics, system.backup (failing, AR-1), system.retention, pokemon.snapshot | 24 h | on |
| pokemon.sales | 7 d | on |
| workers.verify (300 s), tradingos.funding_alerts (600 s; pinned off by SCHEDULER_DISABLED_JOBS), community.process (60 s), community.topics (300 s), community.summary.recent (3 h), community.summary.daily (1 h), community.memories (30 min), content.opportunities (1 h), knowledge.reindex (30 min), combot.sync (6 h), x.ingest (15 min), x.opportunities (30 min), cross.signals (15 min), topic.merges (1 h), opportunities.rank (30 min), approvals.expire (10 min) | — | **OFF** |
44 on, 16 off. All 44 share one lock (AR-3).

### External APIs called (hostnames in app/)
Telegram Bot API + MTProto (Telethon); X API (api.x.com, OAuth2); YouTube Data API + Google OAuth; TikTok (open.tiktokapis.com); Anthropic, OpenAI
(committee/optional), Ollama (local default); Hyperliquid (api, testnet, WebSocket), Binance (fapi/api/data.binance.vision — blocked from iad), OKX, Bybit
(WS), Deribit, CoinGecko, GeckoTerminal, DexScreener, alternative.me (fear & greed), Yahoo Finance, Frankfurter + ECB (FX), mempool.space; Solana RPC,
pump.fun / PumpPortal; Scrydex, TCGdex, PokémonTCG.io, PriceCharting, PSA, GemRate, eBay (failing: invalid_client in today's log), Combot; Kalshi
(external-api / demo); Stripe, NOWPayments, Etherscan/Tronscan/Sepolia RPC (payments); Resend (email); RSS: CoinDesk, Cointelegraph, Decrypt, The Block,
CNBC, MarketWatch/Dow Jones, FT, BBC, NPR, Reuters, Al Jazeera, SEC, Federal Reserve, White House; S3-compatible bucket.

### Environment variable NAMES referenced (settings fields + os.environ; values not read)
ADMIN_USERNAME ADMIN_PASSWORD_HASH ADMIN_SESSION_HOURS APP_ENCRYPTION_KEY APP_ENCRYPTION_KEY_PREVIOUS APP_NAME ENVIRONMENT LOG_LEVEL TIMEZONE DATABASE_URL
SECRETS_FILE SEED_FILE CORS_ORIGINS DASHBOARD_URL MEMBERS_PUBLIC_URL PROXY_SHARED_SECRET TRUSTED_PROXY_HOPS BUILD_ID RAILWAY_GIT_COMMIT_SHA
SCHEDULER_ENABLED SCHEDULER_ONLY_JOBS SCHEDULER_DISABLED_JOBS JOB_{APPROVALS_EXPIRE,COMBOT_SYNC,COMMUNITY_PROCESS,COMMUNITY_TOPICS,CROSS_SIGNALS,
KNOWLEDGE_REINDEX,MEMBERS_VERIFY,MEMORY_EXTRACTION,NEWSDESK,NEWSDESK_TELEGRAM,OPPORTUNITIES,OPPORTUNITY_RANK,POKEMON_CATALOG,POKEMON_PULSE,POKEMON_SNAPSHOT,
POKEMON_SYNC,SUMMARY_DAILY,SUMMARY_RECENT,TELEGRAM_INGEST,TOPIC_MERGE,X_INGEST,X_OPPORTUNITIES,X_OWN,YOUTUBE_SYNC}_SECONDS
LLM_PROVIDER LLM_MODEL LLM_TEMPERATURE LLM_TIMEOUT_SECONDS LLM_STRONG_TIMEOUT_SECONDS MODEL_TIER_FAST MODEL_TIER_STRONG OLLAMA_BASE_URL OLLAMA_KEEP_ALIVE
EMBEDDING_PROVIDER EMBEDDING_MODEL ANTHROPIC_API_KEY OPENAI_API_KEY CONDITION_MODEL NEWSDESK_MODEL CONVERSATION_WINDOW MEMORY_PROMPT_CHAR_BUDGET
MEMORY_RECALL_LIMIT RETRIEVAL_MIN_SIMILARITY COMMUNITY_* (6) COMMITTEE_BACKEND COMMITTEE_CALL_TIMEOUT_SECONDS COMMITTEE_COSTS COMMITTEE_DAILY_CALLS
COMMITTEE_DAILY_LIMIT COMMITTEE_DAILY_LIMIT_RANKED COMMITTEE_DAILY_USD COMMITTEE_MAX_CONCURRENT COMMITTEE_MODEL_{FAST,DEEP,SYNTH} COMMITTEE_NEWS_FEEDS
TELEGRAM_BOT_TOKEN TELEGRAM_API_BASE TELEGRAM_LISTEN_SECONDS TELEGRAM_LONG_POLL_SECONDS MEMBERS_TELEGRAM_DEEPLINK
X_API_KEY X_API_SECRET X_ACCESS_TOKEN X_ACCESS_TOKEN_SECRET X_BEARER_TOKEN X_ACCOUNT_USERNAME X_API_BASE X_DAILY_BUDGET_USD X_MONTHLY_BUDGET_USD
X_WRITE_COST_ESTIMATE_USD X_MEMBER_CLIENT_ID X_MEMBER_CLIENT_SECRET X_MEMBER_WRITE X_OAUTH2_{AUTHORIZE,TOKEN,REVOKE}_URL MEMBER_X_REDIRECT_URI
YOUTUBE_API_BASE YOUTUBE_API_KEY YOUTUBE_CLIENT_ID YOUTUBE_CLIENT_SECRET YOUTUBE_REDIRECT_URI YOUTUBE_REFRESH_TOKEN
GOOGLE_AUTH_URL GOOGLE_TOKEN_URL GOOGLE_MEMBER_CLIENT_ID GOOGLE_MEMBER_CLIENT_SECRET MEMBER_GOOGLE_REDIRECT_URI MEMBER_SESSION_DAYS EMAIL_CODE_MINUTES
EMAIL_FROM RESEND_API_KEY TIKTOK_API_BASE TIKTOK_AUTH_URL TIKTOK_CLIENT_KEY TIKTOK_CLIENT_SECRET TIKTOK_SCOPES
BM_LEASE_SECONDS BM_MAX_VIDEO_MB BM_MEDIA_BASE_URL BM_TICK_SECONDS BM_WORKER_BOOTSTRAP_SHA256
PORTFOLIO_BUCKET PORTFOLIO_S3_ENDPOINT PORTFOLIO_S3_KEY_ID PORTFOLIO_S3_REGION PORTFOLIO_S3_SECRET PORTFOLIO_UPLOAD_DIR PORTFOLIO_CONDITIONS_PER_DAY
PORTFOLIO_CONDITIONS_TOTAL_PER_DAY PORTFOLIO_SCANS_PER_DAY PORTFOLIO_SCANS_TOTAL_PER_DAY
SCRYDEX_API_BASE SCRYDEX_API_KEY SCRYDEX_TEAM_ID SCRYDEX_CACHE_DIR SCRYDEX_CACHE_HOURS SCRYDEX_MAX_REQUESTS_PER_SYNC TCGDEX_API_BASE TCGDEX_CACHE_DIR
TCGDEX_CACHE_HOURS POKEMONTCG_API_BASE POKEMONTCG_API_KEY PRICECHARTING_API_BASE PRICECHARTING_API_KEY PSA_API_BASE PSA_API_TOKEN GEMRATE_API_BASE
GEMRATE_API_KEY EBAY_API_BASE EBAY_CLIENT_ID EBAY_CLIENT_SECRET EBAY_DEV_ID EBAY_MARKETPLACE EBAY_CARDS_PER_SYNC FX_API_BASE COINGECKO_API_KEY
POKEMON_CATALOG_REQUESTS_PER_RUN POKEMON_CATALOG_SETS_PER_RUN POKEMON_TOP100_BACKFILL_REQUESTS POKEMON_TOP100_MAX_AGE_DAYS POKEMON_TOP100_MIN_HISTORY
VAULT_EBAY_DAILY_CALLS VAULT_EBAY_DETAILS_PER_SYNC VAULT_EBAY_MAX_PAGES VAULT_EBAY_SELLERS VAULT_INCLUDE_GRADED VAULT_MAX_AGE_HOURS VAULT_REQUESTS_ENABLED VAULT_SYNC_SECONDS
MARKET_BLOCKED_COUNTRIES MARKET_BUYER_FEE_BPS MARKET_SELLER_FEE_BPS MARKET_HIGH_VALUE_CENTS MARKET_KYC_PROVIDER MARKET_LIVE_APPROVED MARKET_MODE
MARKET_OFFER_HOURS MARKET_ORDER_MINUTES MARKET_PAYMENT_PARTNER MARKET_TESTNET_RECEIVE MARKET_TESTNET_RPC MARKET_VAULT_PARTNERS EXCHANGE_ENABLED
LUNCH_BOT_ENABLED PUMPPORTAL_API_KEY SOLANA_RPC_URL KALSHI_FEE_COEFFICIENT VORTEX_ASSIST_TOKEN REALM_WS_URL COMBOT_API_BASE COMBOT_API_KEY
NOWPAYMENTS_API_KEY NOWPAYMENTS_IPN_SECRET STRIPE_SECRET_KEY STRIPE_WEBHOOK_SECRET VPN_EXTENSION_ORIGINS VPN_REVIEWER_EMAIL VPN_REVIEWER_PASSWORD_HASH
VPN_REVIEWER_UNTIL WEBSITE_CHAT_ENABLED. Vercel: PROXY_SHARED_SECRET.

### Deployment config
backend/railway.json (RAILPACK, start command, healthcheck /api/health/live 120 s, 1 replica, ON_FAILURE ×5 — deprecated 2026-12-01, AR-9);
backend/railpack.json (start command only); frontend/vercel.json (above). No Dockerfile for the API (worker/ has one for the DGX worker).
GitHub workflows: none for Price Chat (Kiwi Windows CI lives in a separate repo 619vortex/KIWIVPN per memory).

## Untested / couldn't verify (and why)
- Root cause of SignatureDoesNotMatch (credentials vs checksum behaviour) — env values and bucket are off-limits; whether portfolio photo /
  Brand Manager uploads also fail (no such errors in the two deployments' logs I read).
- Whether Railway volume backups/snapshots are enabled (not visible via read-only CLI without `railway variables`/dashboard).
- Actual production job intervals (JOB_*_SECONDS overrides) and the exact SCHEDULER_ONLY_JOBS string (derived from the "turned off" log instead).
- OLLAMA_BASE_URL value in production (AR-15).
- Memory/CPU metrics of the api container (not exposed by the logs CLI); no OOM restarts seen in today's deployment.
- Vercel external-rewrite timeout behaviour for long "Run now" calls (AR-14).
- Every admin button end-to-end (no login to production allowed); controls were checked by reading page → route → service for the risky ones
  (kill switches, trading allowlist/stop/publish, member delete/roles, VPN block/delete, News Desk go-live, jobs). Confirm dialogs exist for all
  destructive member/trading/VPN-delete/News-Desk-live actions; the global emergency stop toggles without a confirm (both directions).
- Logs only cover the current and previous deployments (Railway CLI); older history relied on owner memory notes.

## Improvement & feature ideas for this area (ranked)
1. **Backups that provably work + restore drill** (AR-1/AR-7): unblocks every other risky change; add backup age to health and the admin Overview.
2. **Owner alert chat + external uptime monitor** (AR-2): minutes of work, turns silent failures into messages.
3. **Git-based deploys with CI and a build SHA** (AR-8/AR-9): kills the wrong-service outage class, makes "what's live" answerable, enables one-click rollback.
4. **Scheduler lanes + listener as its own task** (AR-3): bot replies stay instant, 20-60 s jobs keep their promises, fewer event-loop stalls.
5. **Refresh-ahead market cache + persisted rows + rank map** (AR-6): removes the 15-minute public stalls and most SLOW lines.
6. **Truthful Jobs page** (AR-5/AR-19): server-off badges, failing-job banner, last success age per job.
7. **Unified admin audit log + session management** (AR-10/AR-11): one table, "sign out everywhere", login history; TOTP later.
8. **Pre-deploy migration guard**: single-head check + automatic pre-migration snapshot + expand/contract policy (AR-7/AR-16).
9. **Postgres + separate worker service** (AR-12) once the above are done; SQLite has carried the project well but is the ceiling now.
10. **Branch hygiene**: delete 21 merged branches, prune 29 worktrees, rebase/renumber feat/kiwi-payments.

## Commands run (for the evidence appendix)
- `cd C:\AI\price-chat\backend && npx @railway/cli logs --lines 5000 --filter "turned off"` → disabled-jobs line (05:14:13).
- `npx @railway/cli logs --lines 5000` (2816 lines, current deployment) and `--lines 3000 --filter "SLOW"` (2066 lines); awk per-route stats.
- `npx @railway/cli deployment list` (20 deployments 11-03..11-08, 3 FAILED); `npx @railway/cli logs <df5d96bd|ec318fd9|c3b18744> --lines 3000 | grep -iE "backup|SignatureDoesNotMatch|Owner alert"`.
- `npx @railway/cli status` (single api service, volume 4.0/48.8 GB, bucket portfolio-photos, region iad).
- `curl https://api.pricechat.live/api/health/public` → `{"ok":true,"db":"ok","scheduler":"ok","version":null,...}`; `/api/health/live` 200; `/docs` 404.
- `curl -o /dev/null -w %{http_code} https://pricechat.live/{kalshi,vpn,trading-os,admin}` → 404/200/200/200 (admin page lives at /admin/kalshi, fine).
- `git -C C:\AI\price-chat branch --no-merged main` (17/39), per-branch ahead/behind + migration diff; `git show feat/kiwi-payments:backend/migrations/versions/0103_vpn_payment_recovery.py`.
- Python: listed all JobSpecs via `build_jobs(MagicMock())` (scratchpad ar_jobs.py); dead-module scan (scratchpad ar_dead.py).
- Tests (shim, all pass = current behaviour confirmed): `tests/audit_jobs_server_disabled.py`, `tests/audit_admin_session_survives_password_change.py`,
  `tests/audit_scheduler_serial_starvation.py`, `tests/audit_newsdesk_collect_wedge.py` (from the News Desk reviewer).
