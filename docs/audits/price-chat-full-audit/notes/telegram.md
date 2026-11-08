# Telegram ecosystem audit (TG)

Reviewer: Telegram area · 2026-11-08 · code at audit worktree (= main e978cc3) plus branch feat/tg-trading (8d14a7c, worktree C:\AI\pc-wt\tg-trade).

## Summary

The bot code is careful in most places. HTML escaping of user text is consistent. Account commands stay out of groups. Anonymous admins are handled. Signal-card ENTER always goes through a private, single-use, 5-minute Confirm, and position and intent ownership is checked on every trade callback. The 265 relevant existing tests pass (73 more on the tg-trade branch).

The main risks are in the architecture and the login flow, not in individual commands:

1. **Shared scheduler lane.** The bot runs inside one scheduler job (`telegram.ingest`) that shares a single global serial lock with every other background job. The bot goes silent while any other job runs (production today: "Telegram listener waited 148s … behind pokemon.exchange.auto"). Every other job also waits while the bot listens for 90 s.
2. **One slow command blocks everyone.** Commands and button presses are handled one at a time inside that job, with no per-user throttle on heavy commands.
3. **Transient errors cause long silences.** Any Telegram error (429, 502, timeout) fails the whole job, and the scheduler then backs off exponentially (up to 30 min).
4. **Login can be hijacked.** The `/start pc_<code>` website sign-in can be used for login fixation: one tap on a link logs an attacker's browser into the victim's account.

Biggest opportunities:
- Run the listener as its own always-on task outside the serial scheduler, with a small worker pool and per-user limits.
- Add an explicit "Sign in on this device?" confirmation in the bot.
- Cut the command surface. About 30 commands overlap: /price, /indicator, /scan and /market; /chart and fc; /committee, /debate, /research and /risk COIN; /help doubling as a trade check.

## Findings

### TG-1: Website Telegram sign-in (`/start pc_<code>`) lets someone take over an account with one tap
Severity: High · Status: CONFIRMED (code path) · Effort: M

**Evidence.**
- `app/telegram/ingestion.py:_member_sign_in` → `app/members/auth.py:279 telegram_seen_start` marks the request "verified" for whichever Telegram user presses Start.
- `app/api/routes/members.py:722-740 telegram_status` then gives a session cookie to the browser that *started* the request (`_set_session`, purpose "login").
- The browser binding (`pc_auth`) only proves the poller is the starter. It does not prove that the starter and the presser are the same person.

**Repro.**
1. The attacker starts "Sign in with Telegram" on pricechat.live and copies the `t.me/<bot>?start=pc_…` link (valid 10 min, `REQUEST_TTL`).
2. The attacker sends it to a victim ("verify to claim XP").
3. The victim taps Start. The bot replies "✅ Done! You're signed in to Price Chat".
4. The attacker's browser is now signed in as the victim.

With `purpose=link` the same trick links the victim's Telegram to the attacker's Passport (missions and XP for the victim's activity, and the victim's /close and ENTER act on the attacker's account).

**Impact.** Full web session on the victim's Kiwi Passport: Earn settings (mode, AUTO, limits), VPN devices, rewards. What a session can do with an already-approved API wallet should be confirmed by the auth/trading reviewers.

**Fix.**
- Don't finish on Start alone. Have the bot answer with "Sign in to Price Chat on <browser/OS, approx. location, time>? [Yes, it's me] [No]" and verify only on Yes.
- Alternatively, show a 2-digit code in the browser that the user must pick in the bot.
- Make the confirmation text name the purpose ("link to Passport X").

### TG-2: The bot shares one global serial lock with every background job
Severity: High · Status: CONFIRMED · Effort: M

**Evidence.**
- `app/scheduler/scheduler.py:88` `self._serial = asyncio.Lock()` ("One job at a time"). `run_job` acquires it for every job, `telegram.ingest` included (lines 183-194).
- `jobs.py:432`: telegram.ingest listens for `telegram_listen_seconds` = 90 (`config/settings.py:70`).
- Production log: `2026-11-08 05:18:18 WARNING Telegram listener waited 148s for its turn (behind pokemon.exchange.auto)`.
- `telegram_ingested` lines arrive about every 95-100 s. That is the listen window plus the gap where other jobs hold the lock.

**Impact.**
- The bot doesn't answer for as long as the slowest other job runs (Pokémon sync, News Desk, committee and others).
- In the other direction, every other job (price alerts, signal tracker, position announcements, paper TP/SL monitors if scheduled) is delayed by up to 90 s whenever the bot is listening.

**Fix.**
- Run the Telegram long-poll loop as a dedicated always-on asyncio task (or a separate process/service) outside the serial lane.
- Keep short DB transactions (already done) and push heavy work to workers.
- Keep the scheduler lock for the jobs that really compete for SQLite writes.

### TG-3: Commands and button presses are handled one at a time with no per-user throttle; one user can stall the bot
Severity: Medium · Status: CONFIRMED · Effort: M

**Evidence.**
- `ingestion.py:_trading_os` awaits `bot.handle_command` for each queued command in turn, then the follow-ups, then the callbacks.
- Heavy commands run inline:
  - `/signal` with no coin analyses 15 markets (`bot.py SCAN_TOP=15`, semaphore 4).
  - `/scan` queries several exchanges.
  - `/chart` and fc build charts plus structure layers.
  - `/markets weekly` calls CoinGecko.
- Only `committee` and `vpn_device` have rate-limit buckets (`grep LIMITS[` → bot.py:641, committeecmd.py:126/440).
- Callbacks are processed after all commands in the same batch, so a button answer can arrive after Telegram's callback window. `_answer` has a DM fallback for exactly this, which shows it happens.

**Impact.**
- Spamming `/signal` or `fc` buttons in any group delays every other member's replies and button answers.
- Telegram's per-chat limit (about 20 msgs/min in groups) then triggers 429s. The client marks the chat flooded and drops the following replies without a retry (`client.py:_call` raises TelegramRateLimitError immediately; the reply is only logged as "Bot command not answered").

**Fix.**
- Add a per-user and per-chat token bucket for the heavy commands and for fc callbacks.
- Answer callbacks first (`answerCallbackQuery` before slow work).
- Process commands in a bounded worker pool (for example 4 concurrent, with a per-chat ordering key).
- Queue sends that hit a 429 and retry them after `retry_after` instead of dropping them.

### TG-4: Transient Telegram errors fail the whole ingest job and trigger exponential backoff (up to 30 min of silence)
Severity: Medium · Status: CONFIRMED · Effort: S

**Evidence.**
- `ingestion.py:sync` calls `self.client.get_updates(...)` with no try/except. Any `TelegramRateLimitError`, `TelegramUnavailableError` or 5xx propagates to the job.
- `scheduler.py:269` sets `backoff = min(interval * 2**(failures-1), max_backoff_seconds=1800)`.
- Production today:
  - `09:18:22 Job telegram.ingest failed: TelegramRateLimitError: retry after 5s`
  - `09:18:30 … 502: Bad Gateway`
  - `09:19:09 … did not respond to getUpdates in time`
  - `09:20:59 recovered`

  That was about 2.5 min of silence from a 5-second rate limit and a brief Telegram blip.

**Impact.** A longer Telegram incident leaves the bot silent for up to 30 min after Telegram recovers.

**Fix.**
- Inside `sync`, catch 429 (sleep `retry_after`) and Unavailable/5xx (short jittered retry), and keep listening.
- Give telegram.ingest a `max_backoff_seconds` of about 30-60 s.
- Don't record a 429 on getUpdates as a job failure.

### TG-5: Editing a message re-runs its command (in groups and DMs)
Severity: Medium · Status: CONFIRMED (audit test) · Effort: S

**Evidence.**
- `parser.py` treats `edited_message` as a ParsedMessage with `edited=True`.
- `ingestion.py:_handle` queues a command when `person and (fc or text.startswith("/"))` and never checks `parsed.edited`.
- `tests/audit_telegram_bot.py::test_TG1_an_edited_command_is_answered_again` passes: an edit of "/price btc" to "/price eth" is answered as a second command, and an edited "/signal" in a DM is answered twice.

**Impact.**
- Duplicate replies and charts in the group when people fix typos.
- Each edit is a free re-run of heavy commands and of `/help <trade>` checks (the daily cap still applies).
- On the tg-trade branch, an edited `/open` produces another preview.

**Fix.** Skip the command and fc paths when `parsed.edited` (or only answer an edit if the original wasn't answered).

### TG-6: The bot answers commands in any group it is added to, including channel posts and linked-channel auto-forwards
Severity: Medium · Status: CONFIRMED (code + audit test) · Effort: S

**Evidence.**
- `ingestion.py:_handle` queues commands *before* the allow-list check (`source = sources.get(...)` comes after the command branch).
- A channel post has no `from` and gets `author_is_bot=False`. An automatic forward comes from 777000, which is not a bot. Both count as a "person".
- `test_TG2_channel_posts_and_automatic_forwards_count_as_people` passes: "fc btc" posted in a channel and "/price btc" from 777000 are both queued.

**Impact.**
- Anyone can add the bot to their own group and use its compute: /signal, /scan, charts, committee runs (within committee limits).
- That load shares the single sequential listener with the real community (TG-2, TG-3).
- Channel posts or auto-forwarded News Desk posts that start with "/" or "fc …" would get bot replies.

**Fix.**
- Answer public commands only in allow-listed chats plus private chats, and optionally leave unknown groups on `my_chat_member`.
- Treat `channel_post` and `is_automatic_forward` as non-person.

### TG-7: Telegram trading (feat/tg-trading): the closeall and sl/tp Confirm buttons have no token, so they can be pressed again later
Severity: Medium · Status: CONFIRMED (code) · Effort: S

**Evidence.**
- `tgtrade.py:669`: the `/closeall` button is `callback_data "ca:all"`. There is no token or expiry, and the count shown is from when the message was sent. `callback()` closes *all current* positions.
- `tgtrade.py:687-690`: the `/sl` and `/tp` Confirm button is `"pr:<pos>:s|t:<prices>"`, also stateless. Pressing an old message hours later re-applies an old stop or target.
- Ownership is checked (`execution._mine`), so only the member's own positions are affected.

**Impact.**
- A scrolled-back tap can close positions that didn't exist when the prompt was shown.
- A tap can also move a stop back to a stale level (for example widening risk after the price moved).

**Fix.**
- Use the existing TrIntent pattern: a token, a 5-minute expiry, single use, with the previewed values stored server-side.
- Edit the prompt message to remove the buttons after use.

### TG-8: Telegram trading: confirmed stop/target prices are rounded to 6 significant digits; "/tp 64000 66000" is read as position #64000
Severity: Medium · Status: CONFIRMED (audit test + snippet) · Effort: S

**Evidence.**
- `tgtrade.py:687` builds the button with `f"{v:g}"`. In `test_TG5`, `f"{104123.5:g}" == "104124"` and `f"{1.2345678:g}" == "1.23457"`. The price applied on Confirm differs from the dry-run preview text.
- `_sl_tp_args(["64000","66000"], only=5)` returns `(64000, [66000.0])`: a two-price `/tp` with the "only position" shortcut treats the first price as a position id. Same for `/sl 61000 62000`.

**Impact.** Wrong stop or target levels on BTC-scale and small-decimal coins. Confusing "no such position" errors.

**Fix.**
- Store the exact values in a server-side intent (see TG-7).
- Require position ids as `#12` (or an id that exists in the member's open positions) before treating a number as an id.

### TG-9: Telegram trading: behaviour changes and messages to confirm with the owner
Severity: Low · Status: CONFIRMED (code) · Effort: S

**Evidence.**
- `bot.py` (branch) `handle_callback`: every signal **ENTER** now runs `tgtrade.gate_for_entry` (Banter referral verified on Hyperliquid), including PAPER mode.
  - Members without a verified wallet can no longer paper-trade signals from the button. On main they could.
  - `gate_for_entry` calls `recheck_referral`, a Hyperliquid network call, before answering the callback. That slows the callback (TG-3).
- `_enter_from_group` always posts "Sent you a private preview." in the group, even when the private reply was a refusal ("There's no published signal", the gate text).
- `/market` (one perp) next to `/markets` (overview) differs by one letter. `/market bitcoin` doesn't use `coin_name`, and an unknown coin raises MarketError, which `handle_command` reports as "Couldn't read the market data right now".
- `tgtrade.callback` exceptions other than TradeError/MarketError leave the member with only "Working…".

**Fix.**
- Decide on purpose whether PAPER ENTER should be gated.
- Only say "Sent you a private preview" when a preview was sent.
- Rename `/market` (for example `/perp`) or merge it into `/price BTC more`.
- Wrap the callback in a generic error reply.

### TG-10: The client slices raw HTML at 4096/1024 characters, which can cut tags and lose the whole message
Severity: Low · Status: CONFIRMED (audit test) · Effort: S

**Evidence.**
- `client.py` `send_message: text[:4096]`, `send_photo/edit_caption/edit_photo: caption[:1024]`, `edit_message_text: text[:4096]`.
- The slice runs on raw HTML in Python characters, not UTF-16 units or parsed text.
- `test_TG3` shows a cut inside `<a href="`.
- `bot._whole_lines` returns a single overlong line unchanged (`test_TG4`), leaving the cut to the client.
- Real exposure: News Desk Telegram captions. `newsdesk/telegram.py MAX_LEN = 900` is checked on *plain* text, but the HTML adds up to 3 `<a href="…">` source links. A long caption is then refused, and `_deliver` silently falls back to text only, so the card image is lost (SUSPECTED; it depends on URL lengths).
- `committeecmd._with_footer` uses `len()` against TG_MAX, not UTF-16.

**Fix.** One shared `fit_html(text, limit)` that measures UTF-16 on the parsed text and trims at a line or tag boundary (`_whole_lines` with a hard fallback). Use it in the client.

### TG-11: Commands are acknowledged before they are handled; a crash, deploy or job timeout loses them
Severity: Low · Status: CONFIRMED (code) · Effort: S

**Evidence.**
- `ingestion.py:sync` commits `state.last_update_id` and *then* runs `_trading_os` from an in-memory list.
- The job timeout is `telegram_listen_seconds + 60` = 150 s (`jobs.py:432`). A batch that arrives late in the 90 s window with slow commands can hit the asyncio timeout mid-command, and the rest of that batch's commands are never answered.
- Railway redeploys also drop the in-flight batch.

**Impact.** Occasional unanswered commands or button presses with no trace beyond the log. This is at-most-once delivery; it is safe for trades (no double execution) but not visible to users.

**Fix.** Keep at-most-once for trade callbacks, but persist the pending command queue (or commit the offset per update after handling read-only commands). Decouple the job timeout from command duration (TG-2).

### TG-12: The fc timeframe buttons can be pressed by anyone, with no per-user limit; the "busy" guard does nothing
Severity: Low · Status: CONFIRMED (code) · Effort: S

**Evidence.**
- `bot.py:_fc_callback` lets any member redraw any fc chart in a group, so the shared message switches timeframe under the asker.
- Each press is a full `build_chart_response` (network plus render). Rendering itself is fast: about 35-50 ms in a local measurement, and production showed fc answered in about 1.4 s (11:28:10.49 → 11:28:11.86).
- `_FC_BUSY` only blocks concurrent redraws of the same message, but callbacks are processed one at a time anyway, so it never triggers.

**Fix.**
- Add a per-user cooldown on fc callbacks (for example 1 per 3 s).
- Optionally, in groups, post the new timeframe as a reply when the presser isn't the original asker.
- Remove `_FC_BUSY` or make it useful once there is concurrency.

### TG-13: `/help` doubles as the trade check; `/risk` means two different things
Severity: Low · Status: CONFIRMED (code) · Effort: S

**Evidence.**
- `bot.handle_command`: `/help <anything>` goes to `assist.start` when Vortex assist is enabled. `/help price` or `/help chart` returns the trade-check USAGE instead of the help.
- `/risk` alone shows the account limits (DM only). `/risk BTC` runs the AI committee (groups too).

**Fix.** Give the trade check its own command (`/check long BTC 81k 10x sl 78k`) and keep `/help <cmd>` for per-command help. Rename `/risk COIN` to `/committee COIN risk`, or drop it in favour of the committee's Risk button.

### TG-14: Commands meant for other bots are logged as received and silently dropped from community ingestion
Severity: Low · Status: CONFIRMED (logs + code) · Effort: S

**Evidence.**
- Production: `09:59:21 Bot command received: /ban in a group chat -1004420406122` and `10:00:17 … /mute`. These are moderation commands for Combot or another bot.
- `is_ours()` treats any "/x" without an @ as ours. The update is counted as a command, never stored, and `handle_command` stays silent (unknown command in a group).

**Impact.** Log noise. Moderation actions disappear from the community record and summaries.

**Fix.** Only queue commands that are in `PUBLIC | PRIVATE` (or fc). Store the rest as normal messages.

### TG-15: Join-welcome flood and footer spam
Severity: Low · Status: CONFIRMED (code) · Effort: S

**Evidence.**
- `ingestion._welcomes` sends one welcome per batch for every join in the signal group. A raid of joins over several seconds produces a series of welcomes, with no cooldown.
- `_Footed` adds the channel line plus the referral line under every reply, error and usage messages included. When a photo caption is full, it sends the footer as a *separate* message (`bot.py _Footed.send_photo`), which doubles messages in groups and adds to 429 pressure.
- `FC_BOT_LINK` labels `@botpricechat` as "Price Chat bot", while onboarding and `CHANNEL_FOOTER` call the same handle the signal group/channel. Which is right depends on the real handle (SUSPECTED mislabel).

**Fix.**
- Add a welcome cooldown per chat (for example 1 per 10 min, batching names).
- Leave the footer off error and usage replies, and don't send a standalone footer message in groups.

### TG-16: Ops: owner alerts have no Telegram chat; the ingested community messages feed nothing in production
Severity: Low · Status: CONFIRMED (logs) · Effort: S

**Evidence.**
- Startup self-check: "owner alerts have no Telegram chat … alerts are only logged". Today's telegram.ingest and newsdesk.tick failures were "logged only".
- The prod disabled-jobs list includes `community.process`, `community.topics`, `community.summary.*` and `community.memories`. `telegram.ingest` still stores every allow-listed message ("Received N new messages" every minute), and only members.verify/missions read them.

**Fix.**
- Set the owner alert chat.
- Decide whether full message storage is needed in prod. If not, keep only what missions need, plus a retention limit (data minimisation).

## Untested / couldn't verify (and why)

- **What a hijacked web session can do (TG-1):** whether it can turn on LIVE/AUTO with a previously approved API wallet. This is the trading/auth reviewer's area. Not exercised against production (read-only rule).
- **Real callback-expiry behaviour and "Button answer refused" frequency:** no such lines in today's logs (only about 5 h since the 05:14 restart, and very little bot traffic: 2 fc commands).
- **/signal (15-market scan) wall time in production:** no samples in today's logs.
- **Invite Bot (app/invites, Telethon user accounts):** logic read (consent rules, daily cap of 20, halting on flood). I couldn't see whether `invites.poll` runs in prod, because `railway variables` is forbidden and the job isn't in the disabled list. The "personal" mode collects usernames and activity of members of third-party groups (`InviteMember`) and suggests messaging them with prefilled text. That is a privacy and Telegram-ToS concern worth an owner decision. test_invites*.py pass.
- **Combot (members/combot.py):** `combot.sync` is disabled in prod. Not reviewed beyond the function list.
- **members/telegram_missions:** code read. Join-channel XP is never clawed back if the member leaves afterwards (join → verify → leave). This is a product decision.
- **Bot permissions in real groups** (privacy mode, delete rights for the branch's secret-deletion): need the live bot. `verify()` records `can_read_all_group_messages`.
- **feat/tg-trading secret deletion (`looks_secret`):** I checked false positives against ordinary English chat. The longest BIP-39 run was 2-4 words against the threshold of 12, so the risk is low.

## Improvement & feature ideas (ranked)

1. **Dedicated bot runtime** (TG-2/3/4/11). An always-on long-poll task, a small worker pool, per-user and per-chat buckets, 429-aware send queue, callbacks answered first. This fixes most user-visible slowness and silences.
2. **Confirm-on-device sign-in** (TG-1), plus `/sessions` and `/unlink` commands in DM to see and revoke web sessions and unlink Telegram.
3. **Simplify the command surface.** Today there are about 30 commands, and the menu on the branch has 28 entries.
   - Market: `/price BTC` (add `more` for funding, OI and max leverage, absorbing `/market` and `/indicator`), `/chart BTC 1h …` (fc = `/chart` with TF buttons, one renderer and one footer), `/scan`.
   - Analysis: `/signal [coin]`, `/committee COIN [tf]` (views by buttons; drop `/debate`, `/research` and `/risk COIN` as aliases), `/check <trade>` (out of `/help`).
   - Account: `/status` (merging /connect, /auto, /risk limits), `/positions` (+P&L, absorbing /pnl and /trades), `/pause`, `/resume`, `/close`.
   - Others: `/alert`, `/vpn`, `/idea(s)`, `/record`.
4. **Ignore edits and non-person senders** (TG-5/6/14). These are small changes with immediate noise reduction.
5. **Tokenised confirmations everywhere on feat/tg-trading before merge** (TG-7/8): one intent table for open, sl/tp and closeall.
6. **A `fit_html` helper** (TG-10) shared by bot, committee, assist and News Desk.
7. **Missing commands worth adding:** `/sessions`, `/unlink`, `/check`, `/ref` (the member's own referral or crew link), `/privacy` (what the bot stores), and `/help <cmd>`.

## Commands run (for the evidence appendix)

- Read code: `backend/app/telegram/{client,ingestion,parser,service}.py`, `backend/app/tradingos/{bot,chartcmd,alerts,committeecmd,assist,ideas}.py`, `members/{telegram_missions,auth}.py`, `api/routes/members.py:700-760`, `invites/{gateway,service}.py`, `newsdesk/telegram.py`, `scheduler/scheduler.py`, `jobs.py`, and on the branch `git -C C:\AI\pc-wt\tg-trade diff main...HEAD` plus `tradingos/tgtrade.py`.
- `cd C:\AI\price-chat\backend && npx @railway/cli logs --lines 3000 --filter "telegram OR Bot OR listener"` → 78 lines, since the 05:14 restart.
- `npx @railway/cli logs --lines 5000` → grepped locally for waited, refused, failed and telegram.
- Existing tests (audit worktree, shim): `pytest tests/test_bot_commands.py tests/test_fc_vs_signal.py tests/test_telegram.py tests/test_telegram_account.py tests/test_committee_telegram.py tests/test_price_alerts.py tests/test_vortex_assist.py tests/test_invites.py tests/test_invites_safety.py tests/test_secrets_telegram_setup.py -q` → **265 passed**.
- Branch tests (tg-trade worktree, shim): `pytest tests/test_tg_trading.py tests/test_bot_commands.py tests/test_telegram.py -q` → **73 passed**.
- New audit reproductions: `C:\AI\pc-wt\audit\backend\tests\audit_telegram_bot.py` → **5 passed** (TG-5 edits, TG-6 channel/auto-forward, TG-10 HTML slicing ×2, TG-8 `:g` rounding).
- Snippet (tg-trade): `_sl_tp_args(['64000','66000'], 5)` → `(64000, [66000.0])`.
- Snippet: `chart.render_study` timing on 330 synthetic candles → 0.034 s (plain), 0.051 s (ma+bb+3 panels).
- Snippet: BIP-39 run length on ordinary chat sentences → 2-4 (threshold 12).
