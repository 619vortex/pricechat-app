# Trending Topics

Admin › X / Twitter › **Trending Topics**. Finds what is getting hot in crypto, stocks, markets, geopolitics, technology and
world events, scores it, writes a short tweet backed by a reliable source, and publishes it automatically (3 a day at most,
3 hours apart) only while the News Desk's X lane is live.

Code: `backend/app/trending/` (engine, sources, rank, writer, gates, coordinate, learning), models `backend/app/models/trending.py`,
routes `backend/app/api/routes/trending.py` (`/api/x/trending`), Admin `frontend/src/pages/x/TrendingTopics.tsx`,
tests `backend/tests/test_trending.py`, `frontend/src/pages/x/TrendingTopics.test.tsx`. Migration `0114_trending_topics`.

## Pipeline (one tick, job `newsdesk.trending`, every `JOB_TRENDING_SECONDS` = 600 s)

1. **Read the sources that are due** (`sources.py`, official access only, nothing scraped):

   | Source | Access | Default | Interval | Reliable for facts |
   | --- | --- | --- | --- | --- |
   | News Desk feeds | the `nd_stories` the News Desk already reads (35+ RSS feeds); no extra request | on | every tick | yes |
   | Google Trends | official "Trending now" RSS, `https://trends.google.com/trending/rss?geo=US` (+GB) | on | 30 min | the trend no; its linked articles yes when the outlet is known |
   | Reddit | OAuth (`REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`, app-only) on `oauth.reddit.com`; public `.json` only if the owner ticks it | on, but needs setup | 30 min | no |
   | X trends | X API v2 `GET /2/trends/by/woeid/1` ($0.010 a request) | **off** (new cost) | 60 min | no |
   | X major accounts | X recent search `from:Reuters OR from:business ...` ($0.005 a post read) | **off** (cost) | 60 min | only news-org accounts |

   A 403 from X is shown as "not available on your X plan" and asked again only once a day. A missing setup is "unavailable",
   not an error. Each source's last scan, items, requests and cost today, error and failure streak are kept in `tt_settings.state.health/usage`.
   Round-ups, archives, listicles and explainers are dropped; sport and celebrity trends are dropped (not this audience).

2. **Group and score** (`rank.py`): single-link clustering (`checks.same_event`, or a search trend's words contained in a headline),
   then a 0-100 score: velocity 30 (items in the last 2 h vs the 6 h before, engagement growth from each signal's history),
   engagement 25 (Google traffic, Reddit score + 2×comments, X post counts, number of outlets), freshness 15 (by publication time),
   audience relevance 20 (crypto 1.0, stocks/markets 0.9, geopolitics 0.75, tech 0.7, world 0.5, keyword strength), discussion 10
   (source breadth, comments, outlets), times the learned weight (0.8-1.2).

3. **Write** (`writer.py`) for topics scoring `suggest_score` (45)+, at most 5 per scan, one tweet per topic per `topic_cooldown_hours`.
   Facts: the lead reliable headline credited "(via Outlet)", plus what our sources measured ("It's on Google's trending searches",
   "a top thread on r/...", "5 outlets are on it"). Personality only in a claim-free hook line and an optional question. Deaths and
   attacks: a plain "Worth knowing today" line, no question. At most one cashtag, inserted only for an asset the sources establish.
   The model writer (Anthropic, `NEWSDESK_MODEL`) is used when a key is set; its words go through the same gate and fall back to the
   rules writer if they fail.

4. **Gate** (`gates.py`, last, whatever wrote it): every News Desk check (`checks.check_post`: length, no link, no @, advice,
   rumour, attribution, figures, tickers) and `checks.final_gate(..., wire=True)` (claim gate: figures, named people,
   accusations, quotations; hype/superlatives; dropped qualifiers), plus: `supported` (at least one reliable item; Reddit/X never
   back a fact), `one_cashtag` (X answers 403 to more than one), `not_breaking`, `buzz` ("trending/viral" only with a trend source),
   `discussion` (Reddit mentions only with a Reddit thread), `controversy` ("backlash", "slams"... only if the sources say it).
   A failing tweet is **held** with the reason and never sent.

5. **Publish** (one per tick): automatic mode queues tweets for topics at `hot_score` (75)+; others wait for approval.
   A queued tweet goes out only if nothing in *Coordination* stops it, through `SocialActionGuard.news_problem` /
   `issue_news_permit` (kill switch, outbound switch, X connection and capabilities) and the X client's local budget.

6. **Monitor**: owner alert (`alerts.notify_owner`, 6 h dedupe, one "recovered" message) when a source fails 3 reads in a row or X
   refuses a post; `/api/health` deep check `trending` (warn on failing sources, a stop, or no run for an hour); the scheduler's own
   job-failure alerts apply to `newsdesk.trending` like any job.

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

1. Merge after release/kiwi-hardening: change `down_revision` in `0114_trending_topics.py` to that branch's 0113 revision so there is one
   alembic head (`ScriptDirectory.get_heads()` must return one).
2. Back up the DB, deploy the backend (migration creates `tt_settings`, `tt_signals`, `tt_topics`, `tt_posts`), deploy the frontend.
3. Optional: `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` (free Reddit "script"/web app, app-only OAuth). Without them Reddit stays
   "needs setup" (public JSON answered 403 from this PC on 2026-11-14).
4. Optional: turn on X trends ($0.010 a request, ≈ $0.24/day hourly) and X accounts ($0.005 per post read) in Settings.

Costs: a trend post without a link is $0.015 on X pay-per-use (3 a day ≈ $1.35/month). Metrics reads $0.001 each. The model writer, if an
Anthropic key is set, is one short Haiku call per suggestion (≤ 5 per scan).
