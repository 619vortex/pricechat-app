# Monitoring: owner setup checklist

This is for branch `feat/kiwi-hardening-monitoring`, after it is deployed. Do the steps in order and tick each box. Nothing here needs SSH, Hetzner, DNS or firewall changes.

## A. Admin: the owner-alert Telegram chat (required: until this is set, every alert goes only to the logs)

1. **Find your numeric Telegram user id.**
   - In Telegram, open a chat with **@userinfobot** and send it anything.
   - It replies with `Id: 123456789`. That number is your user id: digits only, no `@`. It is not your username or your phone number.
2. **Allow the Price Chat bot to message you.**
   - Open a private chat with the Price Chat bot, the bot whose token is on Railway. Its username is shown on Admin › Community › Setup.
   - Press **Start** once. A bot cannot message someone who never started it ("chat not found").
3. **Open Admin › Settings › Background jobs.**
   - URL: `https://pricechat.live/admin/settings?tab=jobs`, signed in as the owner.
   - The **Owner alerts** card shows a red **"Go nowhere"** badge until the chat is set.
4. **Enter the chat id.**
   - Field "Telegram user id, e.g. 123456789": type the number from step 1, then press **Save**.
   - Expected result: the note "Saved" appears and the red line disappears.
5. **Turn the alerts on.**
   - If the button reads **Turn on**, press it. It reads **Turn off** when alerts are already on.
   - Expected result: the badge reads **On**.
6. **Press Send test.**
   - Expected result: within seconds Telegram shows a message from the bot: `Price Chat: test alert. Owner alerts reach you here.` The card shows "Test alert sent: check Telegram".
   - If it says `Telegram didn't deliver it: … chat not found`, go back to step 2.
   - If it says `Set your Telegram user id (and turn alerts on) first.`, go back to steps 4 and 5.
7. **Check Admin › Kiwi VPN.**
   - URL: `https://pricechat.live/admin/vpn`.
   - The top of the page now shows "Server alerts go to your Telegram" and a **Send test alert** button, which sends the same message as step 6.
   - Before the chat was set, this banner read "Alerts go nowhere. … Set the alert chat".

No other Admin setting changes. Leave payments as they are.

## B. Railway environment variables
**None.** The new jobs `system.backup_check` and `system.restore_test` are covered by the `system.` entry already in production's `SCHEDULER_ONLY_JOBS`. They use the existing bucket variables, which `system.backup` already uses. Migrations 0115 and 0116 run by themselves at start-up.

## C. External uptime monitor on GitHub (outside Railway)

It now lives in this repository: `.github/workflows/uptime.yml` (pricechat-app). It starts running by itself once its PR is merged into
`main` (scheduled workflows run only from the default branch). Every 15 minutes it checks the website, `/api/health/live`,
`/api/health/public` (database, scheduler, jobs failing, backup age), the Kiwi VPN public endpoints and servers' online flags, each
browser proxy's 407, and the TLS certificates (21 days or more left).

On a failure it opens ONE GitHub issue labelled `uptime` that mentions you, so GitHub e-mails you even if Telegram is down. It comments
when the failures change and every 6 h while they last, and closes the issue with a "Recovered" comment when every check passes. It uses
only the built-in `GITHUB_TOKEN` with `issues: write`; no secret is needed for this.

| What | Where | Value |
|---|---|---|
| Secret `UPTIME_TELEGRAM_BOT_TOKEN` (optional) | pricechat-app › Settings › Secrets and variables › Actions › **Secrets** › New repository secret | the token of a **separate** bot made with @BotFather (`/newbot`), so a problem with the main bot can't silence this one. Press Start in a private chat with it once |
| Secret `UPTIME_TELEGRAM_CHAT_ID` (optional) | same place | your numeric user id from A.1 |
| Variable `UPTIME_PROXIES` (optional) | pricechat-app › Settings › Secrets and variables › Actions › **Variables** | a space-separated list of `host:port`. The default is `hel1.vpn.pricechat.live:443 ash1.vpn.pricechat.live:443`. Add a new region's proxy here |

Without the two Telegram secrets the Telegram step is skipped (the GitHub issue and e-mail still work). With them, the same events also
go to Telegram.

Cost: each run is one short job, billed as 1 minute. Every 15 minutes is about 2,900 Actions minutes a month on this private repository;
GitHub Pro includes 3,000 a month, shared with CI and the KIWIVPN builds (Windows minutes count double). If the minutes run short, either
change the cron line to `7,37 * * * *` (every 30 min, about 1,440 minutes) or move the file to a small **public** repository, where
Actions minutes are free (the checks only read public addresses, so nothing private is exposed).

To check it after the merge: Actions tab › "Uptime" › "Run workflow". Expected: the job is green; the "Check" step log lists `ok:` lines
for the website, api live, api health, vpn public, the servers online, each proxy's 407 and each TLS host's days left; no issue is opened
and no Telegram message is sent while everything passes.

Local proof without GitHub:
- `python ops/uptime/selftest.py [--path DIR_WITH_JQ]` runs the workflow's own scripts 8 times against the live endpoints, with a fake
  `gh` (issues kept in a JSON file, the workflow's jq expressions run through real jq) and Telegram sent to a fake server on 127.0.0.1.
- Expected output: `ALL PASS`.

## D. Verify after the deploy (expected outputs)

1. **Health.** `GET https://api.pricechat.live/api/health/public` returns 200 with `{"ok":true,"db":"ok","scheduler":"ok","jobs":"ok","backup":"ok",…}`.
2. **Owner health.** Admin, or `/api/health` with the owner login: `checks.migrations.status` is `"ok"`, and its code and database revisions are both `0116_vpn_trial_guards` (the release head). `checks.owner_alerts.status` is `"ok"` with detail `"delivered by Telegram"`. Before step A it is `"fail"` with "Owner alerts go nowhere: …".
3. **Start-up log.** Railway logs show `Startup self-check: configuration OK`, or a problems line. It must not contain `OWNER ALERTS GO NOWHERE` once A is done.
4. **The new jobs.** Admin › Settings › Background jobs: the jobs table lists **Backup freshness check** (`system.backup_check`, every 1 h) and **Weekly backup restore test** (`system.restore_test`, every 7 d). Neither may show "off here".
5. **Backup freshness check.** Press **Run now**. Expected result: `success`, and the result shows the newest key with `age_hours` under 26 and `"stale": false`. No Telegram message is sent.
6. **Restore test.** Press **Run now**. It takes seconds to a few minutes and needs temporary disk of about 2x the database size. Expected result: `success`, with the activity line `Restore test passed: price_chat-YYYYmmdd-HHMMSS.db.gz (N h old, M MB) integrity ok, alembic 0116_vpn_trial_guards, <members> members, <devices> VPN devices`. On a failure you get one Telegram message, `Price Chat alert: Weekly backup restore test FAILED …`, and then a reminder at most once a day.
7. **Server health.** `GET /api/vpn/admin/health` (owner) answers with `servers[]`, each with `status` healthy, degraded, offline or disabled, and a `fleet` block. `checks_24h` grows by 12 an hour per server.
8. **Uptime workflow**, if set up: the next scheduled run is green.

## What you will receive, and how often
Each problem sends one message when it starts and one "recovered" message when it ends. While a problem lasts you get these reminders:

| Problem | Reminder while it lasts |
|---|---|
| a background job failing (including the nightly backup) | every 6 h |
| stale or empty backups in the bucket | once a day. Silent if the backup job's own alert or the "no successful backup" alert already told you about it |
| restore test failing | once a day |
| a VPN server offline, nearly full, or at 80%/100% of its traffic allowance | none |
| fleet: too few healthy servers, capacity | none |
| uptime workflow | every 6 h |
