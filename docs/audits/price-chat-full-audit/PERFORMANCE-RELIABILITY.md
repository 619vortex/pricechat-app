# Price Chat: performance and reliability

Audit of 2026-11-08. This file covers performance, scalability, monitoring, alerting, backups, deploy and rollback, and incident response.

Production evidence comes from `railway logs` (read only) for the live deployment 20ebb668 (started 2026-11-08 05:14 UTC) and the earlier deployments df5d96bd, ec318fd9, c3b18744, ff871084 and 6a9cdd9e, plus public GETs. Full per-finding detail is in QA-FINDINGS.md. This file ranks the operational risks and gives the operations view.

**Bottom line (AR Summary):** "The code is better engineered than its operations."
- No backup has ever succeeded.
- Every alert goes nowhere.
- Migrations are one-way.
- Nobody can tell which commit is live.
- One global lock serialises 44 jobs behind the Telegram listener.
- The public market stalls every 15 minutes.

---

## 1. Operational risks, ranked

| Rank | Risk | IDs | Severity | Status | Effort | Deadline / trigger |
|---|---|---|---|---|---|---|
| 1 | **No working backup**: one 1.17 GB SQLite file on one volume in one region | AR-1, SEC-7 | Critical | CONFIRMED | S / M | Now |
| 2 | **Alerts reach no one**: backup failure, News Desk outage, VPN servers offline, unprotected trades all silent | AR-2, ND-2, TG-16, VPN-6, TR-1 | High | CONFIRMED | S | Now (open since 2026-10-23) |
| 3 | **No rollback**: migrations at boot, no down-path, nothing to restore from | AR-7 | High | CONFIRMED | M | Every deploy with a migration |
| 4 | **Unreproducible deploys**: laptop `railway up`, no CI, `version: null`, unpinned deps; the frontend was once deployed onto the api service | AR-8, SEC-9 | High | CONFIRMED | M | Every deploy |
| 5 | **VPN trial cliff**: payments off, so launch-cohort trials lapse with no way to pay | VPN-1 | High | CONFIRMED | M | About 2026-12-04 |
| 6 | **railway.json stops being honoured**: healthcheck, restart policy and single replica could be lost | AR-9 | Medium | CONFIRMED | S | 2026-12-01 |
| 7 | **VPN single provider and account**, customers on the owner's personal VPS, no node backups; a one-day full outage on 2026-11-05 | VPN-4 | High | CONFIRMED / UNVERIFIED | L | Any abuse report or billing issue |
| 8 | **Global job lock**: bot silent up to 185 s; 20-60 s jobs miss their contracts; sync work blocks the event loop | AR-3, TG-2, ND-7, AR-18 | High | CONFIRMED | M | Continuous |
| 9 | **Market stalls 10-39 s every 15 minutes**; 503 "market is busy" when visitors stack up | AR-6 | High | CONFIRMED | S | Continuous |
| 10 | **Shared Hyperliquid request budget**: one member's previews can cause 429s that break entries, closes and reconciliation for everyone | TR-2 | High | CONFIRMED (code) / SUSPECTED | M | Grows with the number of accounts |
| 11 | **Merging feat/kiwi-payments as-is creates two Alembic heads**, so boot fails | AR-16, VPN-11 | Medium | CONFIRMED | S | At merge time |
| 12 | **Telegram transient errors**: up to 30 min of backoff silence | TG-4 | Medium | CONFIRMED | S | Any Telegram incident |
| 13 | **Health checks lie**: "scheduler ok" while jobs fail; the Jobs page shows disabled jobs as enabled | AR-19, AR-5 | Low / Medium | CONFIRMED | S | Continuous |
| 14 | **SQLite as the ceiling**: no horizontal scaling, no worker, deploy = downtime | AR-12 | Medium | CONFIRMED / SUSPECTED | L | Growth |
| 15 | **AI provider possibly unreachable** (Ollama at localhost) | AR-15 | Medium | SUSPECTED | S | — |

---

## 2. Performance

### 2.1 Backend latency
- **Pokémon market (AR-6).** `railway logs --filter SLOW` (2,066 lines, 05:14-11:43) shows 112 requests of 10 s or more, clustered at :03/:06, :21, :36 and :51 every hour.

  | Route | Slow requests | Average | Max |
  |---|---|---|---|
  | `/pokemon/cards/{id}/chart` | 778 | 2.5 s | 33 s |
  | `/pokemon/market` | 566 | 3.0 s | 39 s |
  | `/pokemon/cards/{id}/quality` | 215 | 3.9 s | 35.8 s |

  - The first 30 s after a deploy are worse (`05:14:43 …/quality 200 26739ms`).
  - Cause: `BASE_TTL = 900` (`market.py:156`) outlives the 600 s `pokemon.warm`, which returns cached rows while they are fresh, so visitors rebuild about 50k rows inline.
  - Fix: refresh-ahead with stale-while-rebuild, persisted rows, and a precomputed `rank_of` map.
- **Earn endpoints (UX-13).** `/api/members/arbitrage/lunch` took 5.15 s and `/funding` 3.19 s (single sample). Other endpoints answer in about 0.45 s. Fix: a 15-30 s server cache.
- **Event-loop blocking (AR-3, AR-17).**
  - Only 16 `to_thread` uses across 60 jobs, so synchronous SQLAlchemy work runs on the event loop and stalls HTTP requests.
  - numpy/PIL rendering and whole-catalogue builds of 200-400 MB run in-process.
  - `/api/system/db-backup` copies 1.17 GB inside a request thread.
  - No OOM was seen in the logs.
- **Hyperliquid client (TR-2).**
  - Every executor costs 4 metadata calls (80 weight against 1,200/min per IP). Executors are rebuilt for every preview, confirm, reconcile, close and protection change.
  - About 14 accounts with open positions would use the whole budget on reconciliation alone.
  - Fix: one Info per network, a cached Exchange per account, per-member limits, and an in-process weight counter.
- **Telegram (TG-3, TG-12).**
  - Commands run serially. A chart renders in 34-51 ms locally (330 candles), and production answered an fc in about 1.4 s.
  - The heavy commands (`/signal` over 15 markets, `/scan`) have no per-user limit.
- **News Desk (ND-7).** 13 feeds and 20 quotes are fetched one after another, with 0.25 s sleeps, under a 240 s timeout, inside the global lock. Fix: fetch concurrently.
- **Retired work (AR-18).** `pokemon.exchange.auto` takes the lock every 15 s (5,760 times a day) for a retired feature.

### 2.2 Frontend
Measured by UX-12 through CDP at 390x844, dpr 2, CPU ×4, 150 ms latency, cold cache:

| Page | FCP | LCP | CLS |
|---|---|---|---|
| `/` | 1,084 ms | 1,528 ms | **0.35** (repeat: 0.122, LCP 3,156 ms) |
| `/pokemon` | **2,772 ms** | 3,676 ms | 0.088 |
| `/earn` | 1,068 ms | 1,296 ms | 0.088 |
| `/vpn` | 1,160 ms | 1,424 ms | 0.026 |

- The home route payload is about 491 KB raw (excluding fonts and images).
- Largest chunks: index 270.5 KB (86 KB gzipped), lightweight-charts 183 KB, AdminApp 120.8 KB (lazy).
- `dist` is 105 MB, of which cards are 76 MB. Binaries (15.5 MB APK, 3.6 MB EXE) are redeployed with every build (UX-20).
- Fixes:
  - Preload Archivo with fallback metrics.
  - Reserve the skeleton height.
  - Split lightweight-charts off the market home.
  - Stop loading `styles.css` (31 KB) on public pages.
  - Move the binaries to Releases or R2.

---

## 3. Scalability

- **One process does everything (AR-12).** The API, the Telegram bot, 44 jobs, the WebSocket tapes and the in-process caches share one uvicorn process with `numReplicas: 1`.
- **Database.** SQLite 1.17 GB with WAL, busy_timeout 15 s, pool 10+30, and a single writer lock shared by members, the bot and jobs. QueuePool exhaustion and 30 s responses happened on 2026-10-23 and 10-31 (owner notes). This is mitigated by the market concurrency gate (`MARKET_CONCURRENCY=10`).
- **Concurrency correctness on SQLite.**
  - `with_for_update()` is a no-op (TR-7).
  - XP caps are check-then-insert: 30 parallel awards paid 30 XP against a cap of 10 (PP-6).
  - Both need `BEGIN IMMEDIATE` or similar while on SQLite.
- **Scheduler.** One `asyncio.Lock` (`scheduler.py:88`) for every job. The audit test `audit_scheduler_serial_starvation.py` shows starvation, and that synchronous jobs overrun their timeouts.
- **Kiwi VPN capacity (VPN-17).**
  - Capacity is a device count (default 250) with no CPU, bandwidth or handshake signal. The Germany node has 1 GB RAM.
  - Ashburn includes 1 TB, then $1.20/TB, and `monthly_cap_gb=0` (VPN-5).
  - Throughput measured by the CI on a real server: 186 Mbps down through the tunnel. The app's Tools speed test showed 732 Mbps (coordinator; see TEST-EVIDENCE.md).
- **Staged path (AR-12 fix):**
  1. Fix AR-3 (lanes, listener as its own task) and AR-6 (refresh-ahead). These remove most of the contention.
  2. Move to Postgres (the code already handles it: `pool_pre_ping`, DataError handler).
  3. Run batch jobs in a separate worker service.
  4. Use managed point-in-time backups.

---

## 4. Monitoring and alerting

| Signal | Today | Gap | Fix | ID |
|---|---|---|---|---|
| Owner alerts | Wired (`app/alerts.py`, Watchdog), but `OwnerAlertSettings.telegram_chat` is empty, so "logged only". The self-check warns at every startup | Nothing reaches the owner | Set the chat; `POST /api/system/alerts/test` | AR-2 |
| Uptime | `/api/health/public` exists for this purpose; no external monitor uses it | A dead process can't alert about itself | UptimeRobot or Healthchecks on `/api/health/public`, plus VPN `/v1/health` | AR-2, VPN-6 |
| Health content | `{"ok":true,"db":"ok","scheduler":"ok","version":null}` while jobs fail | No backup age, no failing-job state, no build id | Add "backup age < 36 h", "no job with 3 or more consecutive failures", and `BUILD_ID` | AR-19, AR-8 |
| Job state in Admin | Shows `enabled=row.enabled` only | Server-disabled jobs look enabled | `server_state` badge | AR-5 |
| News Desk silence | `last_run` is not updated by a crashed tick | No "no release in 18 h" or "no stories in 3 h" rule | Watchdog rules, last error on the page | ND-2 |
| Unprotected live positions | `PROTECTION_FAILED` writes only an audit row | No DM, no owner alert, no incident check | DM to member and owner; health check | TR-1 |
| VPN servers | Alert after 2 failed syncs through `notify_owner` (no chat); `uptime: None` publicly | Silent outages (the 2026-11-06 02:32-02:51 offline alerts went unread) | Chat, external probe, public status page | VPN-6, AR-2 |
| VPN traffic | `monthly_cap_gb=0`; traffic alerts only on feat/kiwi-monitoring | Ashburn overage unnoticed | Merge monitoring | VPN-5 |
| Hyperliquid weight | Not tracked | 429s only discovered by failure | In-process counter with an alert near 1,200/min | TR-2 |
| Container metrics | Not exposed by the logs CLI | Memory headroom unknown | Watch Railway memory metrics | AR-17, AR Untested |
| Feed freshness | `fetch_feed` flags HTTP errors and empty feeds only | A feed 11,831 h stale shows `problem=None` | Staleness per feed type | ND-4 |

**Fastest win (AR ideas #2, SEC ideas #1):** set the owner alert chat and add an external uptime monitor. It takes minutes and turns every failure above into a message.

---

## 5. Backups and disaster recovery

| Asset | Backup today | Evidence | Needed |
|---|---|---|---|
| SQLite DB (1.17 GB, members, XP, wallets, trading, VPN billing, market) | **None has ever succeeded.** `system.backup` fails nightly with `SignatureDoesNotMatch` (last seen 2026-11-07 22:45:10); 12/12 failures from 10-23 to 11-03 per memory | AR-1 | Manual copy now (owner-only `GET /api/system/db-backup`), fix the credentials or checksum config, enable Railway volume backups as a second copy, a restore drill with a documented RTO, backup age in health |
| `secrets.json` / encrypted secrets | Same volume | AR SYSTEM MAP | Covered by the volume backup. Old backups are decryptable with the leaked key, so purge them after re-keying (SEC-7) |
| Bucket `portfolio-photos` | Not assessed | — | — |
| VPN node keys (`/etc/wireguard`, `/etc/kiwi-vpn`) | **None** | VPN-4 (memory: "No backups") | Encrypted off-box backup. Without it, a node rebuild means re-keying every customer |
| VPN nodes (Hetzner) | Delete protection on hel1 and fsn1; ash1 unknown | VPN-4 | A second provider; customers off the personal VPS |

**Whether Railway volume backups or snapshots are enabled is UNVERIFIED.** It isn't visible through the read-only CLI (AR Untested).

**Root cause of the backup failure (SUSPECTED, AR-1):** bucket credentials invalid after the 2026-10-21 incident, or botocore ≥1.36 default checksums on a non-seekable multipart stream (`boto3>=1.35` is unpinned; the local venv has botocore 1.40.76).

---

## 6. Deploy and rollback

| Aspect | Today | Risk | Fix | ID |
|---|---|---|---|---|
| Backend deploy | `cd backend; npx @railway/cli up --service api --ci` from a laptop working tree, uncommitted files included | Wrong-service deploys (2026-11-03 19:03 outage: frontend on `api`); 3 of 20 deployments from 11-03 to 11-08 FAILED | Railway connected to GitHub `main` (root `backend`); until then `railway up backend --service api` plus a build guard | AR-8 |
| CI | None for Price Chat; the 1,951-test suite is run by hand (4 known failures on main, one a real policy mismatch, TR-9) | Regressions ship; a conflicting migration (AR-16) would only be caught if someone runs `test_migrations` | GitHub Action for backend and frontend tests that blocks deploys; a single-head check | AR-8, AR-16 |
| Build identity | `version: null` | Can't tell what's live | Set `BUILD_ID` / git SHA | AR-8 |
| Dependencies | `>=` floors, no lock file, pytest in production | Any deploy can pull a breaking or compromised release | Hashed lock file; dev requirements split | AR-8, SEC-9 |
| Migrations | `command.upgrade("head")` at every boot; 3 empty downgrades; downgrades never tested | A rollback after a migration won't boot ("Can't locate revision") | Pre-migration snapshot, expand/contract, rollback runbook (Redeploy plus `alembic stamp`) | AR-7 |
| Platform config | `railway.json` (healthcheck, 1 replica, restart policy) | Ignored from 2026-12-01 | `railway config migrate` or service settings | AR-9 |
| Restarts | Each deploy restarts the bot and scheduler (single replica + volume) | Brief downtime; in-flight Telegram batch lost | Accept for now; persist the bot queue | AR-8, TG-11 |
| Branches | 17 unmerged, 29 worktrees, 21 stale merged branches | Merge surgery; fixes sit undeployed (flap guard, payment recovery) | Rebase and renumber payments (0107), merge in the order stage0 → monitoring → payments, prune | AR-16, VPN-11 |
| VPN clients | No update mechanism on any platform; Windows unsigned | A security fix can't be pushed | Signed update channel; code signing | VPN-7 |
| Frontend | `vercel --prod`; binaries tracked in git | Large deploys | Binaries to Releases or R2 | UX-20 |

---

## 7. Incident response

### Incidents seen in the notes

| When (UTC) | Incident | Detected by | Duration | IDs |
|---|---|---|---|---|
| 2026-10-23 → ongoing | Nightly backup failing | Logs only | Every night | AR-1 |
| 2026-11-03 19:03 | Frontend deployed onto the `api` service (outage) | Owner (memory) | — | AR-8 |
| 2026-11-05 | Hetzner account blocked: the whole VPN down | Owner (memory) | About a day | VPN-4 |
| 2026-11-06 02:32-02:51 | `vpn:offline:1/2` "Kiwi VPN server offline" alerts | Logged only | About 20 min of repeats | AR-2 |
| 2026-11-06 → 11-08 | News Desk posted nothing (filters; the e978cc3 fix) | Owner, later, through replay scripts | About 2 days | ND Summary, ND-2 |
| 2026-11-08 09:18-09:20 | Telegram 429 / 502 / timeout, bot silent | Logged only ("logged only" alert at 09:19) | About 2.5 min | TG-4 |
| 2026-11-08 10:54 → 16:06 | News Desk wedged on a duplicate Cointelegraph link | Audit (ND-1); alerts at 11:11 and 11:33 "logged only" | About 5 h until the fix 5616ecd was deployed as a93c797a at 16:06; "working again" logged 16:12 UTC | ND-1, AR-4 |
| Every 15 minutes | Pokémon market stalls 10-39 s | SLOW log lines | Recurring | AR-6 |

### Gaps
- No paging path: AR-2.
- No runbooks for rollback (AR-7), restore (AR-1), VPN abuse (VPN-4) or breach rotation. The runbook items are pending per memory (SEC-2).
- No admin audit trail for forensics (AR-11).
- No build id to correlate an incident with a change (AR-8).

### Recommended minimum incident kit (from the AR, SEC, ND and VPN fixes)
1. Owner alert chat plus an external uptime probe.
2. Health shows backup age, failing jobs and the build SHA.
3. A verified restore drill with a documented RTO.
4. A rollback runbook (Railway Redeploy plus `alembic stamp` rules) and a pre-migration snapshot.
5. Silence watchdogs: News Desk, Telegram listener, VPN nodes.
6. An unprotected-position alarm (TR-1).
7. An abuse-handling runbook for VPN (VPN-4) and published AUP/terms (VPN-10).
8. The breach-rotation runbook as one tracked operation (SEC-1, SEC-2, SEC-3, SEC-7).

---

## 8. Not verified

From the AR, VPN and SEC Untested sections:
- The root cause of `SignatureDoesNotMatch`, and whether photo or Brand Manager uploads also fail.
- Whether Railway volume backups are enabled.
- Actual `JOB_*_SECONDS` overrides and the exact `SCHEDULER_ONLY_JOBS` string (derived from the "turned off" log line instead).
- Memory and CPU metrics of the api container.
- Vercel external-rewrite timeout behaviour for long "Run now" calls (AR-14).
- Production Hyperliquid weight use and any 429s (TR-2).
- `/signal` wall time in production (TG).
- Live VPN server state and Hetzner included-traffic figures (VPN).
- Logs older than the current and previous deployments. Older history relied on owner memory notes.
