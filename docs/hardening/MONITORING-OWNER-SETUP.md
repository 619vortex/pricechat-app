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

## C. External uptime monitor on GitHub (optional, free, outside Railway)

| What | Where | Value |
|---|---|---|
| Workflow file | the repository's `.github/workflows/kiwi-uptime.yml` | copy `ops/uptime/kiwi-uptime.yml` from this branch unchanged |
| Repository | any GitHub repo you own. A **public** repo has unlimited free minutes. A private repo uses about 1,440 of the 2,000 free minutes a month at the default every 30 minutes | e.g. a new empty repo `kiwi-uptime`, or `619vortex/KIWIVPN` |
| Secret `UPTIME_TELEGRAM_BOT_TOKEN` | repo › Settings › Secrets and variables › Actions › **Secrets** › New repository secret | the token of a **separate** bot made with @BotFather (`/newbot`), so a problem with the main bot can't silence this one. Press Start in a private chat with it once |
| Secret `UPTIME_TELEGRAM_CHAT_ID` | same place | your numeric user id from A.1 |
| Variable `UPTIME_PROXIES` (optional) | repo › Settings › Secrets and variables › Actions › **Variables** | a space-separated list of `host:port`. The default is `hel1.vpn.pricechat.live:443 ash1.vpn.pricechat.live:443`. Add a new region's proxy here |

Token scopes:
- The workflow needs no personal token. It uses only the built-in `GITHUB_TOKEN` with `contents: read`; the cache needs no extra permission.
- If you push the file from a terminal with a fine-grained personal token, that token needs **Contents: Read and write** and **Workflows: Read and write** on that one repository, and nothing else.

To enable it:
1. Commit the file to the default branch. Scheduled workflows run only from the default branch.
2. Go to the repo's Actions tab, choose "Kiwi uptime", then "Run workflow", to run it once.
3. On a public repo, GitHub disables scheduled workflows after 60 days with no commits. Re-enable it from the Actions tab if that happens.

Expected first run:
- the job is green;
- the "Check" step log lists `ok:` lines for the website, api health, vpn public, the servers online, each proxy's 407 and each TLS host's days left;
- no Telegram message is sent while everything passes. A message goes out only when the set of failures changes, plus "STILL FAILING" every 6 h.

Local proof without GitHub:
- `python ops/uptime/selftest.py [--path DIR_WITH_JQ]` runs the workflow's own scripts 6 times against the live endpoints, with Telegram sent to a fake server on 127.0.0.1.
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
