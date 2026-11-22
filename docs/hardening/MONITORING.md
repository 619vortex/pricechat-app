# Kiwi VPN hardening: monitoring, owner alerts, backup verification

Branch `feat/kiwi-hardening-monitoring`, based on main `a7d2081`. **Not deployed, not pushed.** Written 2026-11-14.

## What changed

### 1. Server health and capacity, ported from `feat/kiwi-production` ffb2de6 (migration 0115, first numbered 0112)
- Only that commit's backend work was brought over: `app/vpn/health.py`, the `VpnServerCheck` model, `GET /api/vpn/admin/health`
  (owner login), the members' `health` ok|degraded field, the fleet alerts in `app/alerts.py` (fewer than 2 healthy public
  servers while open, public capacity at 80% or more, a server's traffic on pace to pass its allowance), and `tests/test_vpn_health.py`.
  `vpn-node/infra/regions-lib.mjs` and the rest of that branch were left out. The capacity alert's reference to `regions.mjs` (not on main) was removed.
- Migration renumbered **0109 → `0112_vpn_server_checks`**, `down_revision = '0111_vpn_server_traffic_allowance'`. It only
  creates the `vpn_server_checks` table and one index. No existing data changes. The stale note in 0110 about rechaining onto 0109 was updated.
- 2026-11-14, on `release/kiwi-hardening`: renumbered again to **`0115_vpn_server_checks`** with `down_revision = '0114_trending_topics'`,
  because production already ran 0114 (feat/trending-topics, based on 0111). See STAGE-3.md.
- **Fixed during the port:** the fleet "redundancy" alert counted a server with one failed sync as unhealthy, so one blip
  on one of two public servers paged the owner. That went around main's flap guard. A server whose only problem is
  `0 < fail_streak < OFFLINE_AFTER` now still counts as healthy for that alert. New test:
  `test_one_blip_on_one_of_two_servers_is_not_a_redundancy_alert`.
- `test_vpn_monitoring.py::test_one_blip...` (single server) sets `VPN_MIN_HEALTHY = 0`, the same way ffb2de6 already did in test_vpn.py.

### 2. Backups
Before this branch, main already had:
- a `system.backup` failure alert at the first failure (`FAIL_AFTER_JOB`);
- the watchdog's "no successful backup for 26 h" silence monitor, based on the job's own record;
- `backup: ok|old` in `/api/health/public`.

None of these reached the owner from 10-22 to 11-12, because no owner chat was set. This branch adds checks that go to the bucket itself:
- **`system.backup_check`** (hourly, `app/services/db_backup.newest` plus `alerts.check_backup_freshness`): it lists
  `backups/db/` and reads the newest `*.db.gz` (age from the file name `price_chat-YYYYmmdd-HHMMSS`, otherwise LastModified).
  It sends one owner alert (`backup:stale`, re-sent every 6 h while it lasts) when:
  - the newest copy is older than 26 h,
  - there is no copy at all, or
  - the bucket can't be read. That is the 2026-10-22 wrong-secret case. The error is passed through `redact_secrets`.

  It sends one "recovered" message when a fresh copy appears. It never touches the database file.
- **`system.restore_test`** (weekly, retried 6 h after a failure, `db_backup.restore_test`):
  1. Downloads the newest copy into a `tempfile.TemporaryDirectory`, gunzips it and deletes the .gz.
  2. Opens the copy **read-only** (`file:...?mode=ro`).
  3. Runs a full `PRAGMA integrity_check`.
  4. Reads `alembic_version`, which must hold exactly one row, and reports whether it matches the code's head. A mismatch is reported but is not a failure, because a backup is up to a day older than a deploy.
  5. Checks that `members`, `vpn_servers`, `vpn_accounts`, `vpn_devices` and `vpn_payments` exist, and counts them. It fails if `members` is 0.

  A failure raises `RestoreTestFailed`. The scheduler records it and `alerts.job_outcome` messages the owner at the
  **first** failure ("Weekly backup restore test FAILED"), then "working again" after the next success.
  The live database is never opened. Only the downloaded temporary copy is.
- Both job names start with `system.`, which production's `SCHEDULER_ONLY_JOBS` already allows. No Railway variable change is needed.
- `system.backup` and the two new jobs share one `_backup_target` guard: SQLite only, and a bucket must be configured, otherwise the job is skipped.

### 3. Alert per server after N consecutive failed syncs: already on main, not duplicated
`vpn.sync`, then `alerts.check_vpn_servers`, sends `vpn:offline:<id>` once when `fail_streak >= OFFLINE_AFTER` (2). It sends "online
again" on recovery. One failed sync is tolerated: that is the flap guard from `9b77f77` / `test_vpn_monitoring.py`. Nothing new
was added here except aligning the fleet alert with that guard (see 1).

### 4. Alerts that go nowhere are now loud
- `alerts.delivery_problem(db, settings)` returns `"Owner alerts go nowhere: …"` when there is no chat, alerts are off, or there is no bot token.
- Startup self-check (production): `OWNER ALERTS GO NOWHERE: …` in the one warning line.
- `/api/health` (owner): new `owner_alerts` check, `fail` in production and `warn` elsewhere. Railway's health check is
  `/api/health/live`, which is unaffected.
- `GET/PUT /api/system/alerts` now also return `delivers` and `problem`.
- Admin › Settings › Background jobs › Owner alerts: red "Go nowhere" badge and message. The description now lists backup and VPN alerts.
  The **"Send test" button and `POST /api/system/alerts/test` already existed** (admin-only, under the owner login) and were not duplicated.
- Admin › Kiwi VPN: a banner at the top, "Alerts go nowhere. … Set the alert chat" (links to Settings › Background jobs).
  When alerts are delivered, it shows a small "Send test alert" button instead.

### 5. External uptime monitor (draft): `ops/uptime/kiwi-uptime.yml`
> 2026-11-22: activated as `.github/workflows/uptime.yml` in pricechat-app (every 15 min, adds `/api/health/live`, alerts through a GitHub issue first and Telegram only when its secrets are set). See MONITORING-OWNER-SETUP.md section C. The text below describes the original draft.

This is a GitHub Actions workflow. It is not placed in `.github/workflows`, not pushed, and not active anywhere. Every 30 min, plus a manual run, it checks:
- `https://pricechat.live/` returns 200.
- `/api/health/public`: `ok=true`, plus `jobs` not `failing` and `backup` not `old`.
- `/api/members/vpn/public` returns 200 and `/api/members/vpn/stats` returns 200, and every public server has `online: true`.
- The `hel1` and `ash1` proxies answer an unauthenticated CONNECT with **407**. The list can be overridden with the `UPTIME_PROXIES` repository variable.
- The TLS certificate of pricechat.live, api.pricechat.live, hel1 and ash1 has 21 days or more left. This also covers the proxies' certbot, which was registered without an email.
- **Agent last sync:** no public field exposes it, so it isn't checked directly. A stopped sync shows up as `scheduler=stale`, as `jobs=failing`,
  or as servers going `online:false`.

Alerts go to Telegram through the secrets `UPTIME_TELEGRAM_BOT_TOKEN` and `UPTIME_TELEGRAM_CHAT_ID`. It sends:
- a message when the set of failures changes (a problem starts, or "recovered");
- "STILL FAILING" every 6 h while a problem lasts.

State is kept with actions/cache. A failing run exits 1, so GitHub also e-mails the repo owner.
A local dry run against production passed every real check. One deliberately bad proxy was reported as `000`.

**Free hosted alternative (no account created):** UptimeRobot's free plan. Set up:
- HTTP keyword monitors on `https://api.pricechat.live/api/health/public` (keyword `"ok":true`) and on `https://pricechat.live`;
- a "port" monitor on hel1/ash1 :443;
- Telegram as the alert contact.

Free-plan limits, check them at sign-up: about 50 monitors at a 5-minute interval. A port monitor only proves the port is open, not the
407, and certificate-expiry alerts may need a paid plan, so keep the GitHub workflow for the 407 and TLS checks.
Better Stack's free tier is a similar option.

## Tests run (exact counts)
Backend (pyshim, venv Python):
- VPN, alerts and migrations after the port: `tests/test_vpn*.py tests/test_owner_alerts.py tests/test_migrations.py tests/test_audit_vpn_payments.py` gave
  229 passed and 1 failed. The failure was `test_vpn_monitoring::test_one_blip…`, the flap-guard conflict described in 1. It was fixed, and then
  `test_vpn_health.py test_vpn_monitoring.py test_vpn.py test_migrations.py` gave **48 passed**.
- New `tests/test_backup_monitoring.py`: **14 passed**. With `test_observability.py test_owner_alerts.py test_seo_edge_backup.py
  test_audit_vpn_payments.py test_api.py`: **70 passed**.
- Full suite (`pytest tests`, 11 min 33 s): **2612 passed, 5 failed**. Four are on the known-unrelated list (test_community,
  test_lunch_bot, test_trading_execution, test_trends). The fifth, `test_tg_trading::test_positions_orders_balance_and_settings`
  (`/pnl` answers "Something went wrong"), **fails identically on main a7d2081**, so this branch didn't cause it.

Frontend (`npm ci`, then): `npx vitest run` gave **58 files, 506 tests passed** (2 new in VpnAdmin.test.tsx). `npx tsc -b` was clean.

## Owner actions
1. **Set the owner-alert chat:** Admin › Settings › Background jobs › Owner alerts. Enter your numeric Telegram user id, press
   Start in a private chat with the bot, then press "Send test". Until then every alert above goes only to the logs, and now Admin says so.
2. Uptime monitor (optional, free):
   1. Copy `ops/uptime/kiwi-uptime.yml` to `.github/workflows/` in a repo you own. A public repo is free; a private repo uses about 1,440 of the 2,000 free minutes a month at */30.
   2. Add the secrets `UPTIME_TELEGRAM_BOT_TOKEN`. A separate bot from @BotFather is recommended.
   3. Add the secret `UPTIME_TELEGRAM_CHAT_ID`.
   4. Run it once by hand.
3. After the deploy, run `system.restore_test` once by hand (Admin › Background jobs) and read its summary line.

## Costs
- Backend: none. That is one bucket LIST per hour, plus one download of the newest backup per week (bucket egress is about the
  compressed DB size, once a week). The restore test needs temporary container disk of about the gz size plus the unpacked DB size, and the backup job
  already uses the same temp disk. A full integrity_check reads the whole copy (CPU for some seconds to minutes, timeout 30 min).
- GitHub Actions: $0 in a public repo, or within the free minutes in a private repo at */30.
- No new accounts and no paid services.

## Migration ordering note
Historical (before the release branch). The current chain is 0111 → 0114_trending_topics → 0115_vpn_server_checks → 0116_vpn_trial_guards (STAGE-3.md).

Another branch (trial abuse) adds **0113**, also based on 0111. **Whichever merges second must rechain:**
- if this branch merges first, set 0113's `down_revision = '0112_vpn_server_checks'`;
- if 0113 merges first, set this branch's 0112 `down_revision` to 0113's revision id. Renaming the file is optional; alembic follows the ids.

Then check that `alembic heads` shows exactly one head. `tests/test_migrations.py` catches drift.

## Deploy steps (when the owner says go)
1. **Back up first:** `GET /api/system/db-backup` (owner login) and keep the file, or confirm tonight's `system.backup` landed in the bucket.
2. Merge `feat/kiwi-hardening-monitoring` into main, after rechaining with 0113 if that has merged.
3. Deploy the backend to Railway from a clean worktree as usual. The start runs `alembic upgrade head`, which applies **0115** (was 0112)
   (`vpn_server_checks` table only). Confirm `/api/health` shows `migrations: ok`.
4. Deploy the frontend to Vercel (Admin banner and Settings card).
5. Check:
   - the startup log line (it will say OWNER ALERTS GO NOWHERE until the chat is set);
   - Admin › Background jobs lists `system.backup_check` and `system.restore_test`, not "off here";
   - `GET /api/vpn/admin/health` answers;
   - run `system.restore_test` by hand.

## Rollback
- Code: superseded by STAGE-3.md: old code is whatever production runs at deploy time (now feat/trending-topics `cf713cb`) **plus** the
  0115/0116 migration files, deployed with `railway up`; never Railway "Redeploy" (old code crashes on the unknown revision).
- Database: not required. The `vpn_server_checks` table is harmless to old code. To remove it:
  `alembic downgrade 0114_trending_topics` (drops the VPN tables/columns of 0115 and 0116; Trending Topics stays), run
  **before** redeploying the old code.
- The two new jobs' rows in `scheduled_jobs` are ignored by old code.

## Not tested
- The restore test and freshness check against the real Railway bucket. They were tested with a fake bucket and real SQLite and gzip files.
- A real Telegram delivery (tests use a fake Bot API).
- The workflow on GitHub's runners. Its check step was dry-run locally with Git Bash and a jq stand-in; the actions/cache state and the Telegram send were not exercised.


## Follow-up (pre-deploy checklist, 2026-11-14)

### Backup edge cases: test and alert text
All of the tests below are in `backend/tests/test_backup_monitoring.py` (TBM) or `test_monitoring_delivery.py` (TMD).

| Case | Test | Owner message, or restore-test error |
|---|---|---|
| no backups at all | TMD `test_no_backups_at_all_alerts_once`; TBM `test_stale_backup_alerts_once_then_recovers`; TBM `test_restore_test_fails_loudly` ("No backup") | "database backups are STALE / There is no backup in the bucket (backups/db/ is empty)." |
| newest older than 26 h | TMD `test_hourly_backup_check_while_stale_sends_once_then_recovers_once`; TBM `test_newest_backup_and_its_age` | "...STALE / The newest backup is 40 h old (price_chat-....db.gz); one should land every 24 h." |
| truncated gzip | TBM `test_restore_test_fails_loudly[...]` | "Weekly backup restore test FAILED ... Error: <key> doesn't gunzip: EOFError ..." |
| gzip of non-SQLite bytes | same | "... is not a sound SQLite database: file is not a database" |
| fails integrity_check | same (`_corrupt_db_bytes`: the index no longer matches its table) | "... failed PRAGMA integrity_check: row 1 missing from index ix_members_name; ..." |
| missing alembic_version | same | "... has no alembic_version table (no such table: alembic_version)" |
| bucket auth error | TMD `test_unreadable_bucket_auth_error_or_timeout_alerts_once[ClientError]`, run through the real scheduler | "...STALE / The backup bucket can't be read: ClientError: An error occurred (InvalidAccessKeyId) when calling the ListObjectsV2 operation..." |
| network timeout | same `[ConnectTimeoutError]` | "... can't be read: ConnectTimeoutError: Connect timeout on endpoint URL: ..." |
| zero-byte object (**was a gap: counted as a fresh backup; fixed**) | TBM `test_a_zero_byte_object_is_not_a_backup`, `test_a_zero_byte_object_alerts`; a gzip of empty bytes fails with "unpacks to 0 bytes" | "...STALE / The newest backup object is empty (0 bytes): price_chat-....db.gz." The restore test fails with: "The newest backup object is empty (0 bytes): ..." |

### Deduplication: how each path behaves
The mechanism lives in `backend/app/alerts.py`:
- `notify_owner` (l.76) skips a key sent within `COOLDOWN` = 6 h (l.30), or within the `cooldown` passed in. A send that fails stamps nothing, so it is retried.
- An open problem gets one "recovered" message via `<key>:recovered`, with no cooldown.
- The watchdog (`observability.py` `_problem`/`_fixed`, l.360/371) sends once per incident. When it can't send, it retries after `RETRY_AFTER` = 15 min (l.51).

| Path | Before | Now |
|---|---|---|
| `system.backup_check` hourly while stale | 6 h reminders (4 a day). On top of that, the same incident also triggered `job:system.backup` (every 6 h) and the watchdog's `ops:silence:backup` (once): **up to 3 streams for one incident** | once, then a daily reminder (`DAILY`, l.32). Silent while `job:system.backup` or `ops:silence:backup` is open (`check_backup_freshness`, l.252). The watchdog skips its backup alert while `backup:stale` is open (observability l.430). One "recovered" |
| `system.restore_test` failing | the 6 h cooldown and a 6 h retry meant about 4 alerts a day | `REMIND_JOB` (l.33) gives a 24 h reminder, and `retry_failed_seconds` = 86400 (jobs.py l.1048). Then "working again" once |
| per-server offline (`check_vpn_servers`, l.304) | once per incident plus one "online again" | unchanged |
| fleet redundancy / capacity (`_check_vpn_fleet`, l.355) | once, then **closed silently** (no recovered message) | once, plus one "recovered" through `_recovered` (l.343). The problem is closed without a message only when there is no owner chat or the VPN is closed |
| traffic pace (l.395) | once per server per month | unchanged |
| owner_alerts self-check (`startup_self_check`, observability l.635) | log line only | unchanged. It never sends to Telegram (`test_owner_alerts_self_check_at_every_restart_sends_nothing`) |
| `system.backup` job failing | the first failure, then every 6 h while its hourly retries fail | unchanged: this is the existing designed behaviour |

The tests that run each path repeatedly in a failing state and assert exactly one message and then one recovery are all in TMD:
- `test_hourly_backup_check_while_stale_sends_once_then_recovers_once` (6 failing runs: 1 message; 4 fresh runs: 1 recovery)
- `test_unreadable_bucket_auth_error_or_timeout_alerts_once` (x2)
- `test_no_backups_at_all_alerts_once`
- `test_stale_backup_reminds_once_a_day_not_every_6_hours` (48 hourly checks: 2 messages)
- `test_backup_check_stays_quiet_while_the_backup_jobs_own_alert_is_open`
- `test_watchdog_backup_monitor_stays_quiet_while_backup_check_reported_it`
- `test_weekly_restore_test_failing_sends_once_then_working_again_once` (5 failing runs through the real scheduler: 1 message; 3 passing runs: 1 "working again")
- `test_restore_test_failures_remind_daily_and_retry_daily`
- `test_fleet_alerts_repeated_sync_failures_one_message_each_then_one_recovery` (6 failing syncs: offline + redundancy, 2 messages; 4 recovered syncs: 2 recoveries)
- `test_owner_alerts_self_check_at_every_restart_sends_nothing`

### Controlled delivery
- **The real send path against a fake Bot API.** The fake is a `ThreadingHTTPServer` on 127.0.0.1, with `telegram_api_base` pointed at it. Tests:
  - `test_owner_alert_reaches_a_local_bot_api_with_the_right_shape`: POST `/bot<token>/sendMessage` as JSON with `{"chat_id": 424242, "text": ..., "disable_web_page_preview": true}`. There is no parse_mode: alerts are plain text.
  - `test_telegram_429_or_500_is_logged_not_marked_sent_and_retried[429|500]`: `notify_owner` returns False and never raises. No OwnerAlert row is written, so no cooldown starts and the problem is not marked open. A warning names `TelegramRateLimitError` or `TelegramUnavailableError`, and never the token. The next attempt goes out.
  - `test_test_alert_endpoint_uses_the_same_path`: 200 `{"sent": true}` with the exact test text. When the fake returns 500, the endpoint returns 400 "Telegram didn't deliver it", with no token in it.
- **The uptime checker end to end.** `ops/uptime/selftest.py` takes the workflow's own Check and Alert scripts and runs them 6 times. The state folder is kept between runs, the checks hit the real public endpoints, and `TG_API` points at a 127.0.0.1 fake. Result: **ALL PASS**.

  | Runs | Situation | Messages sent |
  |---|---|---|
  | 1-2 | healthy | 0 |
  | 3 | `unreachable.invalid:443` added as a proxy | 1, "Kiwi uptime: PROBLEM / proxy unreachable.invalid:443: CONNECT answered '000', expected 407" |
  | 4 | same failure | 0 |
  | 5 | failure removed | 1, "Kiwi uptime: recovered, every check passes again" |
  | 6 | healthy | 0 |

  The workflow's Telegram URL is now `${TG_API}`, which defaults to `https://api.telegram.org`.
- **Workflow validation.** actionlint and shellcheck are not installed here, so the YAML was checked instead:
  - The YAML parses (PyYAML), and `bash -n` passes on all three run blocks.
  - Cron `*/30 * * * *` is valid; GitHub may delay a run by several minutes.
  - Permissions were reduced to `contents: read`, since the `actions: write` was unnecessary for actions/cache.
  - Concurrency group `kiwi-uptime` with `cancel-in-progress: false`, so runs never overlap.
  - `timeout-minutes` was raised from 5 to 10, because the worst case with everything down is about 6 min.
  - Save state runs `if: always()`. The last step exits 1 when a check failed, so GitHub e-mails the owner.

### Follow-up test counts
- TBM + TMD: **20 + 15 passed**.
- The VPN, alerts, observability, audit-ops, migrations and backup set (`test_vpn*.py test_owner_alerts.py test_migrations.py test_audit_vpn_payments.py test_audit_ops.py test_observability.py test_backup_monitoring.py test_monitoring_delivery.py test_seo_edge_backup.py`): **302 passed**.
- Full suite: **2633 passed, 5 failed**, the same 5 as before: the 4 known-unrelated ones plus `test_tg_trading`, which fails on main too (`/pnl` makes a real network call).

### Still unproven
- The real Railway bucket was not read, by design.
- No delivery to real Telegram.
- The workflow has not run on GitHub's runners: actions/cache persistence there, and `vars`/`secrets` resolution, are untested.
- The restore test's run time and disk use on the production-size database are not measured.
