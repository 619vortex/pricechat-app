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
| — | One-time migration on the first desk pass: an *automatic* `market_pause` moves to the scope its anomalies justify (production's becomes `liq_pause`). The owner's own pause is never touched. Settings: `class_pause_clear`; the overview shows `class_pause` and `pause_migrated` |

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

(filled from the 11-24 production backup, section 10)

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
rule unchanged. Owner decisions: trademark logos, CEO portraits for Anthropic/OpenAI/Binance, one generic markets photo, maps or satellite
images for war stories.

## 9. Independently verified market events

`docs/newsdesk/2026-11-24-market-verification.md`, `backend/tests/fixtures/market_events_verified_20261124.json`: 35 events, 18 confirmed,
12 partly right, 5 wrong. Confirmed on 11-23: Nasdaq +6.51%, S&P 500 +4.73%, Dow +3.01%, NVDA +31.48%. Wrong and published: gold
(Telegram 194: "-3.6%, biggest since March" was a 20:00 price; settled -3.16% and June fell more), silver (X 880: a day-old reference).
Wrong and unpublished: Brent/WTI 24 h moves across Yahoo's contract roll, BTC "-9.1% so far today". The replay excludes the wrong ones.

## 10. Seven-day replay of both lanes

(filled from the final run)

## No daily limit on major news

`MAJOR_PER_DAY` (6), the market lane's daily count (3) and the wire lane's 2-a-day / 2-in-6-hours on major moves are gone; X delivery
exempts majors from its count cap. Kept: the minimum gap between posts, the asset cooldown, the repeat check, the X spend guards, and a
**burst guard** (four majors inside 60 minutes = probable malfunction; the next waits for a person and the owner is told).

## Decisions for the owner

(filled at the end)
