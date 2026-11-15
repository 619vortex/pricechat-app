# Trending Topics

Admin › X / Twitter › **Trending Topics**. Finds what is getting hot in crypto, stocks, markets, geopolitics, technology and
world events, scores it, writes a short tweet backed by a reliable source, and publishes it automatically (3 a day at most,
3 hours apart) only while the News Desk's X lane is live.

Code: `backend/app/trending/` (engine, sources, rank, writer, gates, coordinate, learning), models `backend/app/models/trending.py`,
routes `backend/app/api/routes/trending.py` (`/api/x/trending`), Admin `frontend/src/pages/x/TrendingTopics.tsx`,
tests `backend/tests/test_trending.py`, `frontend/src/pages/x/TrendingTopics.test.tsx`. Migration `0114_trending_topics`.

## Pipeline (one tick, job `newsdesk.trending`, every `JOB_TRENDING_SECONDS` = 300 s, 5 minutes since 2026-11-15)

1. **Read the sources that are due** (`sources.py`, official access only, nothing scraped):

   | Source | Access | Default | Interval | Reliable for facts |
   | --- | --- | --- | --- | --- |
   | News Desk feeds | the `nd_stories` the News Desk already reads (35+ RSS feeds); no extra request | on | every tick | yes |
   | Google Trends | official "Trending now" RSS, `https://trends.google.com/trending/rss?geo=US` (+GB) | on | 10 min | the trend no; its linked articles yes when the outlet is known |
   | Reddit | OAuth (`REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`, app-only) on `oauth.reddit.com`; public `.json` only if the owner ticks it | on, but needs setup | 15 min | no |
   | X trends | X API v2 `GET /2/trends/by/woeid/1` ($0.010 a request) | **off** (new cost) | 60 min | no |
   | X major accounts | X recent search `from:Reuters OR from:business ...` ($0.005 a post read) | **off** (cost) | 60 min | only news-org accounts |

   A 403 from X is shown as "not available on your X plan" and asked again only once a day. A missing setup is "unavailable",
   not an error. Each source's last scan, items, requests and cost today, error and failure streak are kept in `tt_settings.state.health/usage`.
   Round-ups, archives, listicles and explainers are dropped; sport and celebrity trends are dropped (not this audience).

2. **Group and score** (`rank.py`, owner 2026-11-14: one Nvidia cluster had pulled 26 loosely related items). Seed-based grouping:
   a signal joins a topic only when it tells the same event as the topic's *seed* (its first, most reliable item, never "any member",
   so a cluster can't drift), within 12 h of it: `checks.same_event` plus evidence beyond a shared big name (the same subject AND a
   shared action word or amount; in a Title Case headline one shared "name" needs two shared action words; without a distinctive
   subject, most of the words). A Google trend and the articles Google links to it are one unit (one unit per search term across
   countries); a one-word trend ("nvidia") never pulls in headlines by itself.
   Score 0-100: velocity 30, engagement 25 (Google traffic, Reddit score + 2×comments, X counts; outlet count alone at most 0.3 of it),
   freshness 15, audience relevance 20, discussion 10, times the learned weight (0.8-1.2). **Velocity comes only from measured growth**
   (`rank.growth`, stored in `parts.growth`): items and independent outlets added across our own scans (`tt_signals.first_seen`, last
   3 h), the Google Trends traffic tier and its rise between scans (each signal's `history`), Reddit/X engagement growth, and a burst
   of fresh publications (velocity only, not evidence). A topic with no measured growth (`evidence` empty: no `scans`, `google` or
   `engagement`) is capped at 60 (`NO_GROWTH_CAP`) and never queued automatically, whatever `hot_score` says. No migration: the
   history lives in the existing signal rows and topic JSON.

3. **Write** (`writer.py`) for topics scoring `suggest_score` (45)+, at most 5 per scan, one tweet per topic per `topic_cooldown_hours`.
   No hook lines, no canned questions, no emoji. Sentence one: the lead reliable headline credited to its outlet (varied: "(via X)",
   "X reports.", "X: ..."). Sentence two, when there is one: a concrete detail from another reliable outlet ("Reuters adds: ..."),
   the measured interest stated plainly ("Searches for Bybaz have topped 200K on Google in the US.", only with a Google traffic
   figure; "went from 20K+ to 200K+ in about 2 hours" when the tier rose between scans), or who else has it. The rules writer asks
   no question; the model writer (Anthropic, `NEWSDESK_MODEL`, when a key is set) gets the same constraints and may ask one only
   rarely (specific, neutral, tied to a stated fact). At most one cashtag. Its words go through the same gate and fall back to the
   rules writer if they fail.

4. **Gate** (`gates.py`, last, whatever wrote it): every News Desk check (`checks.check_post`: length, no link, no @, advice,
   rumour, attribution, figures, tickers) and `checks.final_gate(..., wire=True)` (claim gate: figures, named people,
   accusations, quotations; hype/superlatives; dropped qualifiers), plus: `supported` (at least one reliable item; Reddit/X never
   back a fact), `one_cashtag` (X answers 403 to more than one), `not_breaking`, `buzz` ("trending/viral" only with a trend source),
   `discussion` (Reddit mentions only with a Reddit thread), `controversy` ("backlash", "slams"... only if the sources say it),
   `bait` (the retired template lines and engagement bait), `question` (one at most, sharing two of the source's distinctive words),
   `sensitive` (below). A failing tweet is **held** with the reason and never sent.

5. **Publish** (one per tick): automatic mode queues tweets for topics at `hot_score` (75)+ *with measured growth* and no sensitive
   block; others wait for approval (the reason is the post's note).
   A queued tweet goes out only if nothing in *Coordination* stops it, through `SocialActionGuard.news_problem` /
   `issue_news_permit` (kill switch, outbound switch, X connection and capabilities) and the X client's local budget.

6. **Monitor**: owner alert (`alerts.notify_owner`, 6 h dedupe, one "recovered" message) when a source fails 3 reads in a row or X
   refuses a post; `/api/health` deep check `trending` (warn on failing sources, a stop, or no run for an hour); the scheduler's own
   job-failure alerts apply to `newsdesk.trending` like any job.

## Today's news first (owner, 2026-11-15)

- **Score (0-100)**: momentum 30 (measured growth: scans, Google Trends, engagement growth, and *posting velocity*: 3+ independent
  reliable outlets first publishing it within the last hour, at least half of its outlets, counted from the first scan), freshness 25
  (full for 0-3 h from the first report, fading to half at 6 h), market impact 15 (category + impact words + a 5%+ move), surprise 10,
  audience relevance 10, engagement 10. No measured growth: capped at 60. The automatic line (75), daily max, gaps and caps are unchanged.
- **Stale**: first reported more than 6 h ago and no genuine development (2+ new independent outlets or a rise in Google searches in the
  last 2 h) -> capped at 35, below the suggestion line: never tweeted. A queued tweet whose topic went stale while it waited expires.
- **One event, one post**: News Desk and Trending check each other's posts over 48 h (`SAME_STORY_WINDOW`), so yesterday's story is never
  re-posted because it still trends. The 24 h caps are unchanged.
- **Admin** shows per topic: event time (first report), first seen (our first scan), last update, source count, items per hour,
  outlets in the last hour, developing, and *why* it was selected; plus a speed card: event -> sent, first seen -> sent, written -> sent
  (median / worst), measured on every post (`tt_settings.state.latency`, `topic.parts.latency`, log line `trending latency post ...`).
- **News Desk**: breaking news (same bar: 70, verified by 2+ outlets, breaking-worthy, under 90 min old) no longer waits when a slot is
  under 45 min away: it goes now as that slot's post (`facts.slot_at`, `facts.early`), so the day's number of posts is unchanged. Slot
  ranking takes up to 10 points off stories older than 3 h (at 6 h); verified important stories are held 6 h, not 10. Release and X
  publish log lines carry `event N min, seen N min ago`.
- **Sources not used**: X's "Today's News" / Explore has no public API; X trends (`/2/trends/by/woeid`) and account reads are paid and
  stay off until the owner turns them on. Reddit needs `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`.

## Sensitive subjects (`sensitive.py`, owner 2026-11-14)

| Tier | Subjects | Automatic |
| --- | --- | --- |
| approval only | allegations / accusations / lawsuits / charges about a named person, crimes against people, terrorism, sexual misconduct, suicide and self-harm, children and minors | never (even in Automatic) |
| strict | war and armed conflict, deaths and casualties, disasters, health scares, legal action against an organisation (no person named) | only with 2+ independent reliable outlets in the topic (AP / AP News count once), an attributed sentence, no person named |

Writing for every sensitive topic: "Outlet reports: <headline>." plus "Also reported by ...", nothing else: no hook, attitude,
emoji, question, exclamation or interest statistics; the source's hedges ("alleged", "accused", "says") must stay (gate `sensitive`).
Detection is deliberately broad (regexes): a false alarm costs one approval click.

## Production evidence (logs)

No authenticated admin access is needed to see what it does: `app.trending.engine` logs at INFO, once per process
`trending startup: ... mode auto hot_threshold daily_max min_gap live_switch newsdesk_x x_live ...`, and per scan a few lines:
`trending scan <time>: sources news=ok/175 new 3; google=ok (not due); ... | topics N | suggested N`, `trending top1..5: <score>
[cat] "<headline>" v e f r d x items= growth=<evidence>(<notes>) sensitive=<kind/tier> | tweet <status> (<short reason>)`, and
`trending publish: <dry_run|published|waiting|held|blocked> post=<id> (<why>)`. Public headlines and our own decisions only;
anything secret-looking is redacted (`engine._safe`).

## Modes and switches (defaults, owner decision 2026-11-14)

- **Automatic**, confirmed at install: hot topics publish by themselves, `max_per_day` 3, `gap_minutes` 180. Switching to Approval
  clears the confirmation; turning Automatic on again needs a confirmation in Admin (`confirm_automatic: true`).
- **News Desk X mode gate**: a trend post reaches X only when *both* the News Desk's X lane is live (`nd_settings.mode == "live"`)
  *and* Trending's own `live` switch is on (default on: "follow the News Desk"). If the News Desk is in dry run, every trend post is
  recorded as `dry_run` (note: "the News Desk's X lane is in dry run") and X is never called. "Force dry run" in Admin keeps Trending in
  dry run even when the News Desk is live; turning it back needs a confirmation.
- **Stops**: Trending's own "Stop trend posts" (`kill_switch`), the News Desk's emergency stop, any News Desk pause (the network's
  emergency pause, X refusing the desk) all stop trend posts; scanning and suggestions go on so nothing is lost.
- Approval queue: Approve (re-checked, then sent if the spacing allows, else queued), Edit (same gate; a person can't add figures,
  names or quotes the sources don't have), Reject. Pending suggestions expire after 6 h, queued after 4 h.

## Coordination with the News Desk (`coordinate.py`)

- **Dedupe**: a topic the News Desk posted, queued or released (`nd_posts`, `nd_events`, last 24 h, `checks.same_event` or ≥50% word
  overlap) is held as "covered by the News Desk". Reverse: `newsdesk/engine.deliver_x` skips an event that Trending already posted
  (X status "skipped: Trending Topics already posted this story").
- **Spacing**: no trend post within `newsdesk_gap_minutes` (30) of a News Desk post, a released event or one of its scheduled slots
  (six Dubai slots), and `gap_minutes` (180) between trend posts.
- **Shared cap**: News Desk + Trending posts on X in 24 h ≤ `shared_daily_cap` (20), plus Trending's own `max_per_day` (3).
  In dry run, dry-run records count as sent (as the News Desk does).

## Learning (`learning.py`)

`newsdesk.trending_metrics` (daily) reads impressions, likes, reposts, replies for the last 3 days' trend posts (owned reads,
$0.001 each). Per category (`cat:crypto`) and per source type (`src:reddit`) weights: engagement per impression vs the average,
shrunk with 5 pseudo-posts, slope 0.5, clamped 0.8-1.2, and the product clamped again in `rank.score`. Nothing changes before 5
measured posts. Admin › Performance shows totals, per-category stats and the weights.

## Scheduler and production

Jobs `newsdesk.trending` (600 s) and `newsdesk.trending_metrics` (daily). Production's `SCHEDULER_ONLY_JOBS` includes the
`newsdesk.` prefix (added 2026-11-04 for the News Desk), so both run there **with no Railway env change**.

## Deploy

1. `0114_trending_topics` is already applied in production (Railway deploy 569ccbf5, 2026-11-14): never change its revision or
   `down_revision`. The Kiwi release chains 0115/0116 after 0114; after any merge `ScriptDirectory.get_heads()` must return one head.
2. Back up the DB, deploy the backend (migration creates `tt_settings`, `tt_signals`, `tt_topics`, `tt_posts`), deploy the frontend.
3. Optional: `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` (free Reddit "script"/web app, app-only OAuth). Without them Reddit stays
   "needs setup" (public JSON answered 403 from this PC on 2026-11-14).
4. Optional: turn on X trends ($0.010 a request, ≈ $0.24/day hourly) and X accounts ($0.005 per post read) in Settings.

Costs: a trend post without a link is $0.015 on X pay-per-use (3 a day ≈ $1.35/month). Metrics reads $0.001 each. The model writer, if an
Anthropic key is set, is one short Haiku call per suggestion (≤ 5 per scan).
