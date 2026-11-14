# Kiwi VPN hardening: monitoring, owner alerts, backup verification

Branch `feat/kiwi-hardening-monitoring`, based on main `a7d2081`. **Not deployed, not pushed.** Written 2026-11-14.

## What changed

### 1. Server health and capacity, ported from `feat/kiwi-production` ffb2de6 (migration 0112)
- Only that commit's backend work was brought over: `app/vpn/health.py`, the `VpnServerCheck` model, `GET /api/vpn/admin/health`
  (owner login), the members' `health` ok|degraded field, the fleet alerts in `app/alerts.py` (fewer than 2 healthy public
  servers while open, public capacity at 80% or more, a server's traffic on pace to pass its allowance), and `tests/test_vpn_health.py`.
  `vpn-node/infra/regions-lib.mjs` and the rest of that branch were left out. The capacity alert's reference to `regions.mjs` (not on main) was removed.
- Migration renumbered **0109 → `0112_vpn_server_checks`**, `down_revision = '0111_vpn_server_traffic_allowance'`. It only
  creates the `vpn_server_checks` table and one index. No existing data changes. The stale note in 0110 about rechaining onto 0109 was updated.
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
Another branch (trial abuse) adds **0113**, also based on 0111. **Whichever merges second must rechain:**
- if this branch merges first, set 0113's `down_revision = '0112_vpn_server_checks'`;
- if 0113 merges first, set this branch's 0112 `down_revision` to 0113's revision id. Renaming the file is optional; alembic follows the ids.

Then check that `alembic heads` shows exactly one head. `tests/test_migrations.py` catches drift.

## Deploy steps (when the owner says go)
1. **Back up first:** `GET /api/system/db-backup` (owner login) and keep the file, or confirm tonight's `system.backup` landed in the bucket.
2. Merge `feat/kiwi-hardening-monitoring` into main, after rechaining with 0113 if that has merged.
3. Deploy the backend to Railway from a clean worktree as usual. The start runs `alembic upgrade head`, which applies **0112**
   (`vpn_server_checks` table only). Confirm `/api/health` shows `migrations: ok`.
4. Deploy the frontend to Vercel (Admin banner and Settings card).
5. Check:
   - the startup log line (it will say OWNER ALERTS GO NOWHERE until the chat is set);
   - Admin › Background jobs lists `system.backup_check` and `system.restore_test`, not "off here";
   - `GET /api/vpn/admin/health` answers;
   - run `system.restore_test` by hand.

## Rollback
- Code: redeploy the previous main build (`a7d2081`) on Railway, and the previous Vercel deployment.
- Database: not required. The `vpn_server_checks` table is harmless to old code. To remove it:
  `alembic downgrade 0111_vpn_server_traffic_allowance` (drops the table and its index; no other data is touched), run
  **before** redeploying the old code.
- The two new jobs' rows in `scheduled_jobs` are ignored by old code.

## Not tested
- The restore test and freshness check against the real Railway bucket. They were tested with a fake bucket and real SQLite and gzip files.
- A real Telegram delivery (tests use a fake Bot API).
- The workflow on GitHub's runners. Its check step was dry-run locally with Git Bash and a jq stand-in; the actions/cache state and the Telegram send were not exercised.
