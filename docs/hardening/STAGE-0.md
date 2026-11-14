# Stage 0: checks and owner steps (no deploy)

Checked 2026-11-14 ~09:20 UTC, read-only. Production backend runs Railway deploy 5b48b707 (code 1374981).

## Backup and restore (done, read-only)

| Check | Result |
|---|---|
| Bucket readable with production credentials | yes (list only; credentials read into the process, never printed) |
| Newest backup | `backups/db/price_chat-20261113-120228.db.gz`, 21.2 h old, 4 copies kept, not stale. Next nightly run due ~12:00 UTC |
| Restore test (this branch's `db_backup.restore_test`, real bucket) | **PASSED in 21 s**: gunzip ok, `PRAGMA integrity_check` ok, alembic `0111_vpn_server_traffic_allowance` = production head, key tables present |
| Size | 326 MB compressed, 1,317 MB restored: the weekly job needs ~1.7 GB temporary disk at peak |
| Copies on this PC | in temporary directories, deleted when the check finished; no customer data printed |

## Monitoring prerequisites

| Prerequisite | State |
|---|---|
| `SCHEDULER_ONLY_JOBS` contains `system.` and `vpn.` (new jobs `system.backup_check`, `system.restore_test` will run) | yes |
| `PROXY_SHARED_SECRET`, `TRUSTED_PROXY_HOPS` on Railway | both set |
| Telegram bot token (owner alerts use the Community › Setup bot, not a Railway variable) | configured in Admin (the Telegram lane is live); a test alert proves it |
| **Owner alert chat** | **NOT set** (owner_alert_settings.telegram_chat empty in the 11-13 backup) |
| `GET https://api.pricechat.live/api/health/public` (what the uptime workflow reads) | live today: `{"ok":true,"db":"ok","scheduler":"ok","jobs":"ok","backup":"ok"}` |
| Railway temporary disk for the restore test (~1.7 GB) | not verifiable from here; the first "Run now" after the release shows it |

## A. Set the Telegram alert chat and send a test (works on today's production)

1. Find your numeric Telegram user id: in Telegram open `@userinfobot`, press Start; it replies with `Id: 123456789`.
2. Open a private chat with the Price Chat bot (its username is on **Admin › Community › Setup**) and press **Start** once. Without this, Telegram refuses with "chat not found".
3. Go to **https://pricechat.live/admin/settings?tab=jobs** (Admin › Settings › Background jobs). Card **Owner alerts**:
   - paste the id into "Telegram user id, e.g. 123456789", press **Save**;
   - if the badge says "Log only", press **Turn on**;
   - press **Send test**.
4. Expected: the page says "Test alert sent: check Telegram", and the bot sends **"Price Chat: test alert. Owner alerts reach you here."**
5. If it fails, the page shows Telegram's reason: "chat not found" means step 2 wasn't done; "Set your Telegram user id (and turn alerts on) first" means step 3 isn't saved or alerts are off.

## B. Uptime workflow (optional now; it works against today's production)

Per `MONITORING-OWNER-SETUP.md` section B: a GitHub repo you own (public = unlimited free minutes), file `.github/workflows/kiwi-uptime.yml` copied from `ops/uptime/`, secrets `UPTIME_TELEGRAM_BOT_TOKEN` (a separate bot from @BotFather) and `UPTIME_TELEGRAM_CHAT_ID` (the id from A.1), then Actions › Kiwi uptime › Run workflow once.

## C. Worker-token rotation (procedure; nothing done)

Facts (checked on this PC):
- One worker is registered: "This PC (until the DGX Spark)" (bm_workers id 1, last seen 11-13 12:02 in the backup).
- Three programs read `C:\AI\price-chat\worker\worker.env` at start: `start_worker.pyw` (AI worker, `worker\.venv`), scheduled task **Price Chat News Desk GPU** (`start_newsdesk_images.pyw`) and scheduled task **Price Chat World Monitor** (`start_worldmonitor.pyw`).
- Two copies of each are running now (6 `pythonw.exe`, two interpreters for the GPU/World Monitor scripts). Check that's intended; the rotation restarts all of them anyway.
- The token is valid and was committed in e8645ea. `worker/worker.env` is still tracked on main.

Steps (about 5 minutes; News Desk image jobs and World Monitor pause during it):
1. **https://pricechat.live/admin/brand-manager/settings** (Admin › Brand Manager › Settings, the Workers list under "AI engines", showing "This PC (until the DGX Spark)") › add a worker, e.g. "This PC (2026-11-14)". The new token is shown **once**. Don't paste it in chat.
2. Open `C:\AI\price-chat\worker\worker.env` in Notepad and replace the value after `PRICECHAT_WORKER_TOKEN=` with the new token. Save.
3. Restart the programs, in PowerShell:
   ```powershell
   Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -match 'start_(worker|newsdesk_images|worldmonitor)\.pyw' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
   Start-ScheduledTask -TaskName 'Price Chat News Desk GPU'
   Start-ScheduledTask -TaskName 'Price Chat World Monitor'
   Start-Process 'C:\AI\price-chat\worker\.venv\Scripts\pythonw.exe' 'C:\AI\price-chat\worker\start_worker.pyw'
   ```
4. Verify: in Admin › Brand Manager › Settings › Workers the new worker shows "seen" within a minute or two. The News Desk GPU log (`newsdesk_images.log`) shows no 401.
5. Delete the old worker "This PC (until the DGX Spark)" in the same list. The committed token stops working at once (the backend looks tokens up by hash).
6. Then (Claude can prepare it): a commit with `git rm --cached worker/worker.env`. The file stays on disk and is already in `.gitignore`. Merge it into main. A history rewrite isn't needed once the old token is deleted.

Rollback: until step 5, the old worker still works. Put the old line back in worker.env and restart.

## D. sin1 / hil1 are unused (checked)

| Check | kiwi-sin1 | kiwi-hil1 |
|---|---|---|
| Registered in the backend (vpn_servers in the 11-13 backup: only Helsinki 1, Germany, Ashburn) | no | no |
| Customer devices | 0 (impossible without registration) | 0 |
| DNS name under vpn.pricechat.live | none | none |
| Open ports from outside | 22 only (443, 8443, 80 closed) | 22 only |
| Traffic since creation 11-10 (Hetzner metrics) | ~264 MB in / ~21 MB out (internet scanning + system updates) | ~220 MB in / ~22 MB out |
| CPU | avg 0.57 %, max 1.8 % | avg 0.50 %, max 1.1 % |
| Peak outbound rate, last 24 h | 320 B/s | 365 B/s |
| Volumes, snapshots, backups | none | none |
| Cost | $9.59/mo | $21.09/mo |
