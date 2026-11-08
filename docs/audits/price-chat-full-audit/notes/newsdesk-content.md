# News Desk & content automation (ND)

Reviewer scope: backend/app/newsdesk/*, X delivery path (engine.publish, social/guard.py), Telegram lane, admin (routes/newsdesk.py, frontend/src/pages/x/NewsDesk.tsx, TelegramLane.tsx). brand_manager/, youtube/, content/ only got a light pass (see Untested). Code read at e978cc3 (audit worktree). Production checked only through read-only `railway logs` and the public channel page.

## Summary

- **Production is down right now, for a new reason.** Since 2026-11-08 10:54 UTC every `newsdesk.tick` fails with `UNIQUE constraint failed: nd_stories.link` (ND-1). Cointelegraph's daily "Here's what happened in crypto today" item reuses one URL. `collect()` only remembers links from the last 4 days but keeps rows for 7, so it inserts the URL again and the whole batch rolls back. No new story reaches the desk and X delivers nothing. This heals by itself only when the old row turns 7 days old (about 2026-11-11 10:54 UTC), and it will come back every week. I reproduced it in a test.
- **The e978cc3 fix is live (20ebb668) but cannot help while ND-1 lasts.** Nothing from the desk has been posted to @Pricechatupdates since 2026-11-06 14:30 UTC. Post 146 (11-06 17:58) is a YouTube link someone posted by hand.
- **Nobody would have been told.** The owner-alert chat is not set in production, so "Background job failing" alerts only go to the log. There is no "nothing published / nothing ingested for N hours" check at all (ND-2).
- **Even when healthy, the plan of 3-4 posts a day is not reachable.** I replayed 96 hours of live feeds and prices through e978cc3. That was a very busy week (S&P -3.6%, a $6.3B Binance hack, Treasury cutting off MEXC), and it released 8 posts: about 2 a day on weekdays and 0 at the weekend. Only 3 of the 8 were news (ND-3). A story backed by one outlet tops out at roughly 50-60 points. The bar easing down over a window and the freshness decay work against each other. Market candidates expire (45 min) long before the bar comes down (4.5 h).
- **Feed coverage is thin and partly dead.** The MarketWatch feed's newest item is 1.35 years old, and the health check can't notice because it only flags HTTP errors or empty feeds. There are only 3 crypto outlets, so evidence from two outlets is rare. There is no CoinDesk or Blockworks (both checked live) and no Treasury, DOJ or CFTC feed (ND-4).
- **Classification still drops relevant crypto news:** ETF outflows, crypto theft, and the Binance CFO leaving (ND-5). The keyword regex is sloppy: "gold" matches "Golden" (ND-6).
- **Biggest opportunities:**
  1. Fix ND-1, add silence monitoring and set the owner chat (all small).
  2. Add feeds and a staleness check.
  3. Recalibrate: news gets a fixed bar of about 52 or a ranked best-of-window pick, and market candidates are held for the whole window.
  4. Use names-based clustering for corroboration.
  5. Show the desk's decisions in Admin and let the owner hold or kill an event.

Tests: newsdesk, newsdesk_events, newsdesk_telegram, x_social and brand_publishing_safety give 188 passed (75 s). The new audit test `tests/audit_newsdesk_collect_wedge.py` passes, which confirms the bug.

## Findings

### ND-1: A recycled feed URL wedges ingestion: every news-desk tick fails (live in production now)
Severity: Critical · Status: CONFIRMED · Effort: S

**Evidence**
- `backend/app/newsdesk/engine.py:82-105`: `collect()` builds `seen` from links with `created_at >= now - 4 days` (line 84), but rows are only deleted after 7 days (line 103), in the same transaction as the inserts. `NdStory.link` is `unique=True` (`app/models/newsdesk.py:39`). Nothing in the loop catches a conflict, and the single `db.commit()` (line 104) fails.
- Production log (deployment 20ebb668): `2026-11-08 10:54:00 WARNING Job newsdesk.tick failed: IntegrityError ... UNIQUE constraint failed: nd_stories.link`, with parameters `('https://cointelegraph.com/news/what-happened-in-crypto-today?utm_source=rss_feed...', 'Here’s what happened in crypto today', ..., published '2026-11-07 14:22')`. It repeats at 11:00, 11:11 and 11:33 (scheduler backoff, capped at 30 min). Traceback: `engine.py line 535 tick -> line 104 collect`.
- Reproduced by `C:\AI\pc-wt\audit\backend\tests\audit_newsdesk_collect_wedge.py`, which passes. A row stored 5 days ago plus the same URL with a fresh date makes every collect raise IntegrityError, and a genuine new story in the same feed ("exchange hacked for $200M") is never stored. Collect succeeds again only once the old row is more than 7 days old, because the purge DELETE then runs before the insert flush.

**Impact**
- While wedged, no new story enters `nd_stories` from any feed: the whole batch rolls back.
- `engine.tick` dies before `events.decide` and `deliver_x`, so X gets nothing and released events go "missed".
- The Telegram lane notices the desk's `last_run` is stale (`telegram.py:_decide_if_desk_is_not`). It calls collect itself every 3 minutes (re-fetching all 13 feeds), swallows the same IntegrityError, and runs decide on stale stories. Only live market moves can still go out, on Telegram only.
- The cycle repeats every week while Cointelegraph reuses the URL: inserted on day D, skipped D+1 to D+4, failing D+4 to D+7. That means no ingestion about 3 days in every 7.
- The next self-heal is about 2026-11-11 10:54 UTC.

**Fix**
1. Check `seen` against every link still stored (drop the 4-day bound, or make it at least the retention period).
2. Insert with SQLite `INSERT ... ON CONFLICT(link) DO NOTHING`, or one savepoint per row, so one bad item can't sink the batch.
3. Commit the purge separately.
4. Strip `utm_*` parameters from links.
5. Apply `telegram.SPAM` (it already lists "what happened in crypto today") at collect time.

Restoring production today needs a deploy or a one-off row delete. That is the owner's call: this audit is read-only.

### ND-2: No alert when the desk goes quiet, and job-failure alerts reach no one
Severity: High · Status: CONFIRMED · Effort: S

**Evidence**
- Production log: `11:11:58 WARNING app.alerts | Owner alert (no owner Telegram chat set, logged only) [job:newsdesk.tick]: Price Chat alert: Background job failing`. `app/alerts.py:79-82` logs only when `OwnerAlertSettings.telegram_chat` is empty or disabled.
- grep finds no check anywhere for "no release in N h", "no stories ingested in N h" or "Telegram lane delivered nothing in N h". `engine.overview` and `telegram.overview` show `last_run`, but a crashed tick never updates `last_run` (`engine.py:519-548`), so Admin shows a growing "Last run" time with no error.

**Impact**
- The 11-06 to 11-08 outage (filters) and today's wedge (ND-1) both went unnoticed.

**Fix**
1. Set the owner alert chat in Admin.
2. Add a watchdog rule that alerts on:
   - no desk release in 18 h on a weekday;
   - no new `nd_stories` row in 3 h;
   - `newsdesk.tick` with 2 or more consecutive failures;
   - Telegram `tg_status` uncertain or skipped.
3. Show the scheduler's last error on the News Desk page.

### ND-3: Scoring calibration can't deliver the 3-4 posts a day plan; news almost never clears the bar
Severity: High · Status: CONFIRMED (replay + arithmetic) · Effort: M

**Evidence: live replay**
- `scratchpad\ndaudit\audit_replay100.py` reads the live default feeds over 100 h and live Yahoo 5-minute bars, then runs `events.decide` every 5 minutes over 96 h with code e978cc3.
- It released 8 posts in 96 h:
  - Wed 14:48 Nasdaq +1.2%/15m
  - Wed 18:53 Oil -8.3%
  - Thu 02:48 Silver +8%
  - Thu 15:13 ETH -4.1%/1h
  - Thu 18:13 "Bitcoin erases election rally... $428M longs" (news, 67)
  - Fri 10:33 Binance $6.3B hack (news, 75)
  - Fri 14:43 Dow -3.6%
  - Fri 20:58 MEXC cut off (news, 60)
  - Nothing on Saturday or Sunday.
- About 25 candidates scoring 52-58 expired with "under the bar of 6x at this point in the window". Examples: TSLA -10.9%/24h (58), MSTR -11.2% (57), COIN -10.6% (56), NVDA -9.3% (56).

**Evidence: the score structure** (`virality.py`)
- A news story backed by one attributed outlet gets evidence 6 and spread 0, so its ceiling is severity 38 + reach 15 + 6 = 59 unless the headline states a dollar figure above $10M for a hack, scam or failure.
- A just-over-the-line market move: 20 + 10 + 12 + 12 = 54 (BTC).

**Evidence: the bar works against the decay** (`events.py:441`, `virality._freshness`)
- The bar starts at 64 and reaches 56 only 4.5 h into a window.
- Freshness decays from minute 45 (×0.925 at 1.5 h, ×0.85 at 2.25 h).
- Example: a 62-point story at the start of a window never posts. At 1.5 h it is worth 57.4 against a bar of 61.3.
- Market candidates live only 45 min (`market_ttl`), so they never see the eased bar.

**Impact**
- On weekdays the desk posts 0-2 times a day, mostly price moves. Weekends are silent. The channel looks dead.

**Fix**
1. Choose the target from data. Re-run the replay weekly; the script is in the scratchpad.
2. Pick the best candidate seen in each window (hold market candidates for the whole window, re-measured) instead of a bar that decays.
3. Give news its own bar of about 52, with evidence counted from names-based clustering (ND-14).
4. Let strong single-outlet specialist reports (The Block or CoinDesk "says/confirms") score evidence 8-9.
5. Optionally add a daily market wrap at a fixed time, built only from measured data (the X-only `snapshot()` already exists).

### ND-4: Feed list: dead MarketWatch feed, thin crypto coverage, staleness invisible
Severity: High · Status: CONFIRMED · Effort: S

**Evidence**
- Replay feed health, 2026-11-08 about 11:50 UTC (`replay_out.txt`):
  - MarketWatch `mw_marketpulse`: 30 items, newest **11,831 h old**, `problem=None`.
  - SEC: newest 91 h. Federal Reserve: 68 h. NPR: 37 h. These are plausible for a weekend but not monitored.
  - Crypto in 36 h: Cointelegraph 8, Decrypt 3, The Block 3.
  - In 100 h, the desk stored 93 stories, 48 of them crypto, and 20 passed the evidence rules.
- `sources.fetch_feed` reports a problem only for HTTP errors or an empty feed.
- Checked live today: CoinDesk RSS (200, 25 items), Blockworks (200, 50 items, updated today), Bitcoin Magazine and CFTC are fine, and MarketWatch `mw_topstories` is live. The Treasury press RSS URL I tried returns 404. Today's MEXC story was a Treasury action, so an alternative source is needed.

**Impact**
- With 3 crypto outlets, evidence from "two outlets" is rare and spread points are almost always 0. This is a direct cause of ND-3.

**Fix**
1. Replace `mw_marketpulse` with `mw_topstories`.
2. Add CoinDesk, Blockworks and DL News.
3. Add official sources: DOJ press (filter for crypto), CFTC, an OFAC recent-actions source.
4. Consider a hacks source: rekt.news is stale (newest April 2026); DefiLlama's hacks API is an option.
5. Flag a feed as stale when its newest item is older than X h for its type.
6. Show per-feed item counts in Admin.

### ND-5: The topic filter still rejects relevant crypto news after e978cc3
Severity: Medium · Status: CONFIRMED · Effort: S

**Evidence:** `chaos.evaluate` on the live stories (`ndwhy.py` on `replay.db`):
- "Bitcoin ETFs flip negative for 2026 after $1.28 billion outflow week" → "not market chaos". `etf` has severity 6 and is not in `chaos.CHAOS`.
- "Ex-FBI counterintelligence agent stole almost $2.5M in crypto" → "not market chaos".
- "Binance’s finance chief Kaiser Ng to leave company, report says" → "not market chaos". The Block's "Binance CFO steps down..." passed; "to leave" is not in the vocabulary.
- 47 of 93 stories in 100 h were rejected as "not market chaos".

**Fix**
1. Add `flows` (ETF or fund flows of $500M or more) and `theft` to CHAOS, with dollar-scaled magnitude.
2. Add `leave|leaving|depart|exit` to the leadership-change words in `brief.TOPICS` and `events.ESCALATE`.
3. Keep a labelled regression set of about 50 real headlines in tests.

### ND-6: Category keywords match inside other words
Severity: Low · Status: CONFIRMED · Effort: S

**Evidence**
- `engine.classify` uses `rf"\b{re.escape(k)}"`, with no closing `\b` (`engine.py:66`).
- So "gold" matches "Golden": "Messi becomes first player to win back-to-back MLS Golden Boot awards" was stored as category `commodities`.
- In the same way "dow" matches "down"/"download", "record" matches "recorded", "rates" matches "ratest..." and so on.

**Impact**
- Mostly noise, since the chaos filter rejects these later. But category switches in Admin gate on these categories, and they feed the "not about a market" exemption (`story.category != "crypto"`).

**Fix:** use `\b...\b`, with deliberate stems such as `liquidat\w*`.

### ND-7: X delivery depends on the whole tick, and the tick holds up the Telegram bot
Severity: Medium · Status: CONFIRMED · Effort: M

**Evidence**
- `deliver_x` runs only at the end of `engine.tick` (`engine.py:537-548`), so any exception earlier (ND-1) stops X entirely.
- Jobs run one at a time (`scheduler.py:180-186 _serial`). The production logs for ff871084 show "Telegram listener waited 185s / 85s / 134s for its turn (behind newsdesk.tick)" on 11-06 and 11-07.
- The tick fetches 13 feeds and 20 quotes one after another, with 0.25 s sleeps and Hyperliquid calls, under a 240 s timeout.
- When the desk is stale, the Telegram lane repeats the full collect every 3 minutes.

**Impact**
- The public Telegram bot can't answer commands for up to about 3 minutes.
- One feed problem stops every lane.

**Fix**
1. Wrap collect, quotes, decide and deliver in separate try blocks.
2. Fetch feeds concurrently with a short timeout.
3. Move feed fetching out of the serial scheduler lane, or give `telegram.ingest` priority.

### ND-8: Admin can't see or control the shared desk's decisions; the Telegram lane has no review or dry-run mode
Severity: Medium · Status: CONFIRMED · Effort: M

**Evidence**
- `engine.overview` returns `desk` (events with virality, why, status and per-platform delivery; `events.py:577-581`), but `frontend/src/pages/x/NewsDesk.tsx` never renders it: grep for `virality`, `.why` and `released_24h` finds nothing under `pages/x`.
- The only post controls (`routes/newsdesk.py:103-150`) act on X `NdPost` rows. There is no endpoint to hold, kill or force-release an `NdEvent`.
- `NdTelegramSettings` has only `enabled`, `max_per_day` and `gap_minutes`. Telegram posts live with no dry-run or approval step, while X stays in dry run.
- The desk policy (bars, windows) can only be changed by writing JSON into `NdSettings.state.policy`. `SettingsInput` has no field for it, and `events.overview` reports the `POLICY` defaults even when overridden.

**Impact**
- The owner couldn't see why nothing was posting (the root-cause work for the 11-06 outage needed replay scripts).
- The owner can't stop one bad story without switching the whole lane off.

**Fix**
1. Add a "Desk" panel: the last 48 h of events, their scores and why, and per-platform status.
2. Add Hold, Release now and Kill actions on an event.
3. Add an optional "Telegram needs approval" mode.
4. Make the policy editable, with validation.

### ND-9: Dead Admin controls and dead code
Severity: Low · Status: CONFIRMED · Effort: S

**Evidence**
- `NdSettings.sensitivity` is read only by `engine.detect_markets`. `detect_markets`, `detect_liquidations`, `plan_news`, `writer.rewrite` and `engine.KEYWORDS`-based `COOLDOWN` have no callers (grep over app/ and tests/ returns nothing).
- So Admin's "Breaking sensitivity" select (`NewsDesk.tsx:73`) does nothing.
- `target_per_day`, `max_breaking_per_day` and `breaking_gap_minutes` only affect the legacy `choose()` queue and X's cap (`target + breaking`, default 18). They do not affect the shared plan, which is fixed at 4 regular + 3 extra.
- `overview.writer` says "model" for X even though X text is never written by a model.

**Fix:** remove the dead paths and controls, or wire the Admin numbers to `events.Policy`.

### ND-10: AI summary: the validator contradicts the prompt; the fabrication guard only checks numbers
Severity: Medium · Status: CONFIRMED (validator) / SUSPECTED (fabrication risk) · Effort: S

**Evidence**
- `telegram.PROMPT` asks for "one or two complete sentences". `_from_model` then accepts only `2 <= len(sentences(summary)) <= 3` (`telegram.py:~118`). Every one-sentence answer, which the prompt invites, is silently thrown away with no log line, and the post falls back to the feed's own sentences.
- In production, the 11-06 news post shows `writer=feed`.
- The model's headline is requested and then ignored: compose always uses `ev.headline`.
- `checks.check_post` guards against figures no source gave, links, @mentions, advice words, rumour words and false BREAKING labels. It does not check names, places or causal claims. A model summary could say a hack was "attributed to Lazarus" without the source saying so, and still pass.
- The model is `newsdesk_model = "claude-haiku-5-5"` (`settings.py:282`). Whether the key is set in production is UNVERIFIED (variables are off-limits).

**Fix**
1. Align the prompt and the validator (accept 1-2 sentences).
2. Log why the model output was dropped.
3. Add an entity check: every capitalised name or ticker in the summary must appear in the source text. Alternatively, keep feed sentences only for hack and scam topics.
4. Drop the unused headline from the prompt.

### ND-11: Telegram "Paused" badge and problem text outlive the pause
Severity: Low · Status: CONFIRMED · Effort: S

**Evidence**
- `_pause` stores `paused_until` and `problem`. `_paused()` respects the expiry, but `problem` is cleared only by a successful delivery (`telegram.py send`) or Resume.
- `TelegramLane.tsx:29` shows "Paused" whenever `problem` is set.
- After a transient refusal, Admin says "Paused" for days on a quiet desk.

**Fix:** clear `problem` when `paused_until` has passed, or have the overview compute the paused state.

### ND-12: The engagement feedback loop has no data
Severity: Low · Status: CONFIRMED · Effort: M

**Evidence**
- `engagement.adjustments` reads only `NdPost` rows with status "published" and X metrics. X is in dry run (production logs show "newsdesk DRY RUN"), so the adjustment is always 0.
- Telegram views are never read. Recent channel posts have 45-72 views (`t.me/s/Pricechatupdates`).

**Fix:** pull Telegram view counts for the bot's own channel posts (the public page or `getMessages` via MTProto), and use views per post by topic as the engagement signal.

### ND-13: Post style probably hurts engagement
Severity: Low · Status: SUSPECTED · Effort: S

**Evidence:** channel posts 144-145:
- every headline is IN CAPITALS;
- every post starts with the same "🥝 PRICE ALERT" header, even for news;
- market posts carry no context ("S&P 500 FALLS 3.6% IN 24 HOURS TO 7,373" plus "Price from Yahoo Finance as of 14:30 UTC.");
- about 50 views each.

**Fix**
1. Use sentence-case headlines.
2. Reserve the "PRICE ALERT" header for price posts.
3. Add one measured context line to market posts (e.g. "biggest daily drop since ...", from the bars we hold).
4. Keep the card.

### ND-14: Clustering misses the same story in different words, so corroboration is undercounted
Severity: Medium · Status: CONFIRMED · Effort: S

**Evidence**
- `engine.corroboration` uses fingerprint Jaccard ≥ 0.34 (`engine.py:108-110`). The names-and-family logic in `events._same` is used only to merge events, not for evidence.
- In the replay, "Binance’s finance chief Kaiser Ng to leave company" was not matched with "Binance CFO steps down...".
- Decrypt's "Treasury Cuts Crypto Exchange MEXC Off From US Financial System" stayed "attributed" while two other outlets matched each other.

**Impact:** evidence and spread points are low, which feeds ND-3.

**Fix**
1. Count an outlet as corroborating when it shares 2 or more names, or 1 name plus the same topic family, within 12 h. This is the existing `_same` rule.
2. Count spread from that.

### ND-15: X path: dry run, link rule and cost look correct
Severity: Low (informational) · Status: CONFIRMED (code + logs) / UNVERIFIED (X prices) · Effort: S

**Evidence**
- `publish()` short-circuits when `mode != "live"` (`engine.py:~420`). Production logs show only "newsdesk DRY RUN" lines.
- The `no_link` check blocks any URL in X text (`checks.py`). `brief.x_text` adds a link only with `with_link=True`, and nobody calls it that way.
- Going live needs the Admin confirm dialog, and the guard's kill switch, outbound and platform checks (`guard.news_problem`).
- Credit or auth errors pause the desk for 6 h and keep the post queued. It then expires after 2 h, which is fine.
- The cost is configured at $0.015 per post without a link (`settings.x_write_cost_estimate_usd`). I can't verify X's current price list from here.
- Risk once live: the X daily cap is `target_per_day + max_breaking_per_day` = 18 by default. The shared plan caps at 7, so that's fine.

## Untested / couldn't verify (and why)

- **What is in the production DB** (the exact `created_at` of the poisoned row, current NdEvent candidates, whether the Anthropic key is set): no DB or SSH access and no `railway variables`, by the rules. Instead I inferred the row's age from the first failure time (10:54 UTC means it was created about 2026-11-04 10:54).
- **Production logs before about 09:50 UTC today:** Railway returns only recent lines per deployment. For older deployments (df5d96bd, ec318fd9) the filtered fetch came back empty.
- **Weekday news volume:** the replay window is Wed to Sun of one exceptional week. A quiet week would release fewer posts. I could not replay a quiet week because the feeds only hold about 1-4 days.
- **Liquidation events:** the replay ran with `liquidations=False`, because they need the exchange feeds Price Chat records in production.
- **Hyperliquid second opinion:** not exercised in the replay (`second_opinion=None`), so crypto moves score 2 points lower than they would in production.
- **brand_manager/ (TikTok pipeline), youtube/, content/:** I read only the pipeline header and the approval gates. Direct TikTok posting requires `approved_at` and the owner's privacy choice (`brand_manager/engine.py:256`). `test_brand_publishing_safety.py` passes. I can't see whether `brand.tick`, `brand.trends` or `youtube.sync` are in SCHEDULER_ONLY_JOBS (variable values are off-limits), and no successful runs appear in the logs.
- **X pricing ($0.015 vs $0.20):** not checkable from here.

## Improvement & feature ideas (ranked)

1. **Stop the bleeding.** ND-1 fix plus a regression test, the owner alert chat (ND-2) and silence alerts. Small effort, and it removes the two failures that caused both outages.
2. **Coverage.** Add CoinDesk, Blockworks and DL News, plus DOJ, CFTC and OFAC, and a staleness monitor per feed. More outlets give corroboration and spread for free and raise the quality of what clears the bar.
3. **Calibration by replay.** Make `audit_replay.py` a supported admin tool ("what would the desk have posted in the last 72 h?"). Tune bars to hit about 3 posts a day on median weekdays. Pick the best of each window rather than using a decaying bar.
4. **Names-based corroboration** (ND-14) and broader topics (ND-5: ETF flows, theft, leadership exits).
5. **An Admin "Desk" panel** with per-event why and Hold, Kill and Release controls. Also an optional approval mode for Telegram, so the owner can supervise while trust builds.
6. **Originality without fabrication.** Add one measured context line per post from our own data: the move against its 30-day range, the largest drop since a date, the liquidation total around a hack. The figures come from our prices, so the figures check passes and posts stop being copies of headlines.
7. **Speed.** Fetch feeds concurrently and poll the crypto feeds every 2 minutes. The "extraordinary" lane (80+) then reaches the channel within minutes of an outlet.
8. **Engagement loop from Telegram views** (ND-12). Also try sentence-case headlines and only occasional questions (ND-13).
9. **Remove dead code and controls** (ND-9), so Admin numbers mean what they say.

## Commands run (for the evidence appendix)

- `cd C:\AI\price-chat\backend && npx @railway/cli logs --lines 2000 --filter "newsdesk OR tgnews"` gave 23 lines, all IntegrityError nd_stories.link from 10:54 UTC.
- `npx @railway/cli logs --lines 5000` gave the full recent log (from 09:50 UTC), including the owner-alert "no owner Telegram chat set" and the "Background jobs turned off" lines.
- `npx @railway/cli deployment list`, then `logs <deployment> --lines 3000 --filter "newsdesk OR tgnews OR nd_stories"` for 20ebb668, df5d96bd, ec318fd9, ff871084 and 6a9cdd9e. These gave the 11-06 releases #1 and #7, "tgnews DELIVERED message_id=144/145", and "Telegram listener waited 185s ... behind newsdesk.tick".
- `curl -s https://t.me/s/Pricechatupdates`: the last desk post is 145 (2026-11-06 14:30 UTC). Post 146 (17:58) is a manual YouTube link. Views 45-72.
- From `C:\AI\pc-wt\audit\backend`, with the shim PYTHONPATH: `pytest tests/test_newsdesk.py tests/test_newsdesk_events.py tests/test_newsdesk_telegram.py tests/test_x_social.py tests/test_brand_publishing_safety.py -q` gave 188 passed in 75 s.
- `pytest tests/audit_newsdesk_collect_wedge.py -q` gave 1 passed (bug reproduced).
- `python scratchpad\ndaudit\audit_replay.py replay.db 72` (live feeds, 36 h ingest window): 3 releases in 72 h, all market moves.
- `python scratchpad\ndaudit\audit_replay100.py replay100.db 96` (ingest window widened to 100 h): 93 stories, 20 passed evidence, 8 releases in 96 h, 0 at the weekend.
- `python scratchpad\ndaudit\ndwhy.py replay.db`: per-story verdicts (ND-5, ND-6).
- `curl` of candidate feeds: CoinDesk, Blockworks, DL News, Bitcoin Magazine, CFTC and MarketWatch topstories all 200. Treasury press RSS 404. rekt.news stale.
- Greps: `grep -rn "rewrite(\|plan_news(\|detect_markets(\|detect_liquidations(" app tests` returns nothing. `grep -rn sensitivity app` matches only the model, route, overview and dead code.

All scratchpad files are under `C:\Users\USER\AppData\Local\Temp\claude\8bda9b0a0137d3c5\ffd3112d-5e3a-4168-8656-5c5285927885\scratchpad\ndaudit\`; outputs are in `replay_out.txt` and `replay100_out.txt` there.
