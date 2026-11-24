# Terminal overhaul, 2026-11-24: report

Owner's brief (2026-11-24): separate liquidation pauses from market coverage; fix liquidation baselines and compare equivalent instruments;
one decision for News Desk and Trending (an unpublished story never blocks another lane); fix every classification error of the 55-headline
replay by understanding context; find why approved stories never reached a slot; replace broken feeds; prepare the $10.1B correction;
improve licensed images (link-preview fallback kept); verify the market events independently before using them; no daily limit on
genuinely major news; a seven-day replay of both lanes. No production change, no test channel, no manual post.

Everything is on PR #16 (`owner/fix/nd-major-moves`). Nothing is deployed.

## 1. Liquidation pauses are separate from market coverage

| Before | After |
|---|---|
| Production's `market_pause` was set automatically on 11-22 09:35 UTC by three PIEVERSE liquidation readings ("automatic: 3 implausible or contradicted figures") and held every market post since, including the 11-23 rally | A liquidation-data problem pauses liquidation posts only (`liq_pause`). A price-data problem pauses only its asset class (`class_pause`: crypto, index, stock, commodity); six or more across classes in an hour pause the classes involved (a broken feed, not a market). Only the owner's own switch holds every market post |
| — | One-time migration on the first desk pass, strict: only an *automatic* `market_pause` whose anomalies were **all liquidation readings** (or the verified 11-22 09:35 liquidation trip) moves to `liq_pause`, with its evidence recorded in `pause_migrated`. A price-triggered or unexplained automatic pause stays exactly as it is. The emergency stop (`kill_switch`), a timed pause (`paused_until`/`pause_reason`) and the owner's own pause are never touched. Settings: `class_pause_clear`; the overview shows `class_pause` and `pause_migrated` |

## 2. Liquidation baselines and equivalent instruments

- Liquidations are judged against the **same contract's** traded value that hour on a venue we record (OKX `{coin}-USDT-SWAP`, readable from
  Railway; it lists the stock and metal perpetuals Hyperliquid's main market doesn't). At most 25% of one venue's trading is believed; more
  than 3x is a unit error. 11-23: NVDA $3.5M was held as "170x its typical hour" while OKX's NVDA perpetual traded **$624M** that hour.
  Telegram 204's $10.1B is ~40x an ordinary BTC hour on OKX.
- Futures vs perpetual: the independent check found trade.xyz `xyz:COPPER` follows COMEX **March 2027** while Yahoo `HG=F` is **December
  2026** (1.5% apart all day). For `=F` symbols the level only has to be the same instrument (5% basis) and a day-sized move must show on
  trade.xyz too (same direction, at least a third of the size, over its 24 h). Unit errors and wrong instruments are still refused.

## 3. One decision for both lanes

Only what a lane **sent, or is sending now**, holds another lane: a sent/sending News Desk post, or a released event delivered on a platform
or released under 30 minutes ago. A candidate, a rejected/expired/missed event or a held post never does (`coordinate.event_out`, used by
`newsdesk_conflict` and `newsworthy.told`). A held trend draft is re-checked against what the other lane actually sent, every scan. Both
lanes call the same `importance.judge`. 11-23: the desk REJECTED event 1162 (Nvidia +27% on Huang's return) and Trending held its own,
higher-scoring telling as "covered by the News Desk (event 1162, candidate)" for hours.

## 4. Context, not keywords

`importance.structure` reads who did what: the main clause is what is reported; an "after/amid" clause is background (half weight beside a
main event or a stance, full when the main clause only states a consequence, never major on its own); "says/confirms" report, "claims" is a
party's claim (never major until confirmed), "warns/vows/praises" is a stance (no event; a major power's threat is at most notable). The
parties of the strike come from the reporting clause. Strikes inside an **established war** (from the desk's own record: three or more
attack stories between a pair in seven days, Russia-Ukraine always) are significant unless they escalate (another state drawn in, a NATO
state hit, 50+ killed, global energy supply, a nuclear site). Results of the replay set: `backend/tests/test_nd_context_20261124.py`.

## 5. Why approved stories never reached a slot

From production's own record (read-only backup 2026-11-24 10:46 UTC, `nd_events` + `nd_settings.skip_details`). Production skipped
**24 of the 42 slots** of 11-17 06:00 -> 11-24 06:15 with "nothing new and worthwhile". Every skip had a candidate; the causes:

| Cause in production | Example | In PR #16 |
|---|---|---|
| The market pause held every price post with **no reason recorded** | 11-23 20:00: #1167 "NVIDIA SKYROCKETS 32.1%" scored 100 and waited; 11-24 04:00: #1202 S&P 500 +4.7% | Pause scoped to liquidations; a held post always records why (`held_market`) |
| The old virality bar of 56 / "no concrete consequence" gate | 11-22 20:00: Chainflip $12M theft; 11-23 04:00 / 00:00: the Huang and Binance-recovery stories | One judgement (`importance.judge`): tiers, not a 56 bar |
| Feed diversity held a third geopolitics post in a row | 11-23 12:00: **ten** candidates held, best #1149 Ukrainian drones hit two Russian refineries (72) | Diversity only ranks; it can hold a *notable* story, never a significant one |
| The repeat check matched on shared countries | #1136 superseded as "same event" 136 h later | Event identity (`newsworthy.same_event`), one war = many events |

Replayed on the same week (section 10) with PR #16's code, every one of the 43 slots is filled.

## 6. Feeds

The Guardian's 403 was its CDN refusing our "Mozilla/5.0 (compatible; ...)" User-Agent; an honest agent gets 200 (reproduced locally;
Railway confirms only after a deploy). Six fast world feeds added (BBC, The Independent, ABC News, SCMP, The Washington Post, Sky News), all
already in `RELIABLE_OUTLETS`. GDELT is unusable (429 locally, ConnectTimeout from Railway): it now times out in 4 s and rests. Failing feeds
rest (10 min doubling to 1 h) and log once, not every scan; the owner alert fires after 6 h. Terms caveat: most publishers' RSS terms say
"personal, non-commercial"; the desk uses headlines to discover stories and links/credits the outlet (owner's call).

## 7. The $10.1B correction

`docs/incidents/2026-11-21-telegram-204/CORRECTION.md`: re-verified on Binance's public data (contract size $100; the whole market traded
$250.7M in that hour, $155.2M of it selling). Best estimate $17-18M (our own feed recalculated; Binance publishes no hourly total, and
`allForceOrders` is withdrawn). Telegram edit, reply and X texts drafted. **Telegram 148 (11-09) had the same error** ("$17 million"; real
about $23K). 204 never went to X. Not published: the owner publishes.

## 8. Images

Licensed coverage of the week's publish-worthy stories 44.8% -> 52.9% (567 stories, same set, no network). Matching bugs fixed (the
Trump-Putin photo on Trump-Xi summit stories, "Alice Huang" as Jensen Huang, palm oil as crude, BTCUSD_PERP as Binance's founder). Openverse
is no longer searched while vision is off (the searched photo could never pass) and rests after failures. Link-preview fallback and the hold
rule unchanged.

Photo and link-preview coverage, measured separately on the same 567 stories (each article checked with production's own
`preview.check`, as Telegram's preview robot reads it):

| Outcome | Stories | Share |
|---|---|---|
| Licensed photo (library or template) | 311 | 54.9% (52.9% photo, 1.9% chart/template) |
| No photo, working link preview | 118 | 20.8% |
| No photo, preview refused (62 are publisher 403s) | 78 | 13.8% |
| No photo, no article link | 60 | 10.6% |

Covered by a photo or a preview: 75.7%. Held by the hold rule (neither): 24.3%. Owner decisions: trademark logos, CEO portraits for Anthropic/OpenAI/Binance, one generic markets photo, maps or satellite
images for war stories.

## 9. Independently verified market events

`docs/newsdesk/2026-11-24-market-verification.md`, `backend/tests/fixtures/market_events_verified_20261124.json`: 35 events, 18 confirmed,
12 partly right, 5 wrong. Confirmed on 11-23: Nasdaq +6.51%, S&P 500 +4.73%, Dow +3.01%, NVDA +31.48%. Wrong and published: gold
(Telegram 194: "-3.6%, biggest since March" was a 20:00 price; settled -3.16% and June fell more), silver (X 880: a day-old reference).
Wrong and unpublished: Brent/WTI 24 h moves across Yahoo's contract roll, BTC "-9.1% so far today". The replay excludes the wrong ones.

## 10. Seven-day replay of both lanes

`ops/newsdesk/replay_lanes_7d.py`, production backup 2026-11-24 10:46 UTC, window 11-17 06:00 -> 11-24 06:15 UTC: 1,416 stories
(342 News Desk only, 925 Trending only, 149 in both lanes), both lanes through one decision. Each headline is judged only on what had
arrived by that moment (its own first report, the outlets seen so far): an earlier run that let a cluster use later headlines was
corrected and its numbers are not used. Market events the independent check found wrong are excluded.

| | Production | PR #16 replay |
|---|---|---|
| Posts | 24 Telegram, 24 X (News Desk), 5 X (Trending) | 58: 16 major (breaking), 40 significant, 2 notable |
| Empty slots | 24 of 42 skipped | 0 of 43 |
| Breaking delay (from the deciding report) | median 187 min to first post (all posts) | median 2.9 min, p90 12 min |
| Slot posts' delay from first report | — | median 182 min (a slot story waits for its slot by design) |
| Duplicates | 0 true repeats (the 4 pairs the text check flags are templated wording across different assets or days) | 14 suppressed (silver x6 same asset in 24 h, S&P x3 after the index basket, Nvidia -12% after -14%) |

Verified major market events (section 9) and the replay: Nvidia -14.0% (11-20) broke at once (new: a 10% day in a mega-cap is major);
the US index drop of 11-20 at 14:55; Anthropic's +35% debut (11-19 18:15, a minute after the report); silver -6.5% (11-19); Nvidia +31.5%,
the index basket (+6.5% / +4.7% / +3.0%) and Tesla +11.5% on 11-23 at 14:35 / 14:55 / 15:15 (the 20-minute gap between posts orders a
burst; the burst guard was not reached); bitcoin's 17% rebound (11-23). Not posted: **bitcoin below $60,000 (11-20)**, a 1% move on the
day, judged routine; the round-level rule applies to news headlines, not to the measured price post (open item). Gold -3.6% (Telegram 194)
and silver "so far today" (X 880) are excluded as wrong.

Fixed after this run's first pass (tests in `test_nd_context_20261124.py`): "Bitcoin Enters Death Cross" read as people killed; "Convicted
FTX swindler ... misses out on $37B" read as an enforcement action; a hack's stolen funds moving or being recovered rated as a new hack
(major three times); a crypto move credited with another subject's % ("Bitcoin crashes to $59,000 as Anthropic falls 25%"); "46% in
28 days" read as a day's move; a person's fortune ("Jensen Huang's Wealth Rises $36 Billion") breaking after the move itself.

Remaining judgement calls the replay shows (not fixed; the owner's editorial line):
- War coverage dominates slots: 21 of the 43 slot posts are Russia-Ukraine or Iran/Gulf war stories (significant, never breaking unless they
  escalate). Six Iran/Gulf stories still broke as major (the US strike on Qeshm, tankers hit or mined in Hormuz, Bahrain/Saudi attacked).
- "The S&P 500's biggest one-day drop since November 2026: -3.7%" filled the 11-22 08:00 slot 12 hours late with its odd wording (a data
  post; a weekend slot with nothing better).
- Production posts the replay would not make: Jensen Huang agreeing to face Congress, World Bank stablecoin test, the $10.1B liquidation
  (TG 204), gold 194 (wrong), "Market rout kills 50 bp of Fed hike bets", "Putin will continue the war, intelligence says".

## No daily limit on major news

`MAJOR_PER_DAY` (6), the market lane's daily count (3) and the wire lane's 2-a-day / 2-in-6-hours on major moves are gone; X delivery
exempts majors from its count cap. Kept: the minimum gap between posts, the asset cooldown, the repeat check, the X spend guards, and a
**burst guard** (four majors inside 60 minutes = probable malfunction; the next waits for a person and the owner is told).

## Decisions for the owner

1. **Deploy PR #16** (backend only; no migration). On its first desk pass the 11-22 liquidation pause moves to `liq_pause`: price posts
   resume for every class. The emergency stop, any timed pause and your own pause stay as they are.
2. **Corrections**: approve texts A-D in `docs/incidents/2026-11-21-telegram-204/CORRECTION.md` (Telegram 204 edit or reply; Telegram
   148 the same error). Decide on gold (Telegram 194 + X) and silver (X 880), published wrong.
3. **Feeds**: the Guardian fix and six new feeds are confirmed only on Railway after the deploy; RSS terms are your call.
4. **Images**: logos, CEO portraits, one generic markets photo, maps for war stories.
5. **Editorial line**: strikes inside the Russia-Ukraine war (and, once established, Saudi-Houthi) go at slots, not as breaking; the
   share of war stories in slots; bitcoin's round-level breaks as a measured post.
