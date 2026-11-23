# News Desk diagnosis, 2026-11-23: where important stories die

Evidence: the 2026-11-23 10:45 UTC production backup (read-only temporary copy; `nd_events`, `nd_settings.state`, `nd_posts`,
`nd_telegram_posts`), production logs of deployments e772fda3 (11-22 20:24 -> 11-23 14:45 UTC) and 6d6c5296 (14:46 -> 15:28 UTC), and
Binance/Yahoo public market data. Replay dataset: `backend/tests/fixtures/nd_replay_7d.json` (666 items: 650 events 11-16 -> 11-23 10:45
plus 16 stories/moves from the logs after the backup). Replay: `ops/newsdesk/replay_7d.py` (current gates, stage by stage, pure functions).

## Output collapsed

| day (UTC) | events created | released | went out (TG or X) |
|---|---|---|---|
| 11-16 | 71 | 3 | 1 |
| 11-17 | 97 | 9 | 4 |
| 11-18 | 97 | 7 | 6 |
| 11-19 | 113 | 7 | 7 |
| 11-20 | 151 | 3 | 3 |
| 11-21 | 66 | 3 | 3 |
| 11-22 | 33 | 0 | 0 |
| 11-23 (to 15:30) | 22 + log items | 1 (#1131 breaking, 05:30) | 1 |

Slots 11-16 04:00 -> 11-23 12:00 UTC: 32; **25 skipped** (state.skipped_slots + the 12:00 log line). Fewer events on 11-21/22 is most likely
the weekend (not verified against feed health).

## Stage by stage

**1. Ingestion: not the problem.** Every rally story reached the system. Trending's scanner and the desk's feeds carried "US futures bounce
as AI rout rattles Asian stocks", "Stock futures rebound as battered AI, chip stocks rally", "Nvidia reinstates Jensen Huang as CEO. Stock
skyrockets 27%", "AI rally set to add $2 trillion to Nasdaq-100", "CrowdStrike to buy Zscaler in $44 billion deal". The desk made events
#1162/#1167 (Nvidia), #1173 (CrowdStrike). Price data: Yahoo quotes the desk reads (state.last_prices) were current.

**2. Classification: one known fault.** feed diversity reads the desk of each released post; #1131 "Market rout kills 50 basis points
December Fed rate hike bets" counted as geopolitics together with #1053, which held every Ukraine story at 08:00 and 12:00 UTC (see 5).
Not investigated further (desk_of on a signature without assets falls back to topic/category).

**3. Scoring: not the main problem.** The stories an editor would run mostly scored 56-85 at their peak (above regular_bar 56). The
breaking lane needs 70 *fresh* plus a MAJOR consequence within 90 min: 9 breaking releases in 7 days. #1173 ($44B deal) was "not breaking
(68 < 70)".

**4. Rejection: the newsworthy gate is the largest single killer of real news.** Replay of the 24 stories labelled `publish`:

| first failing stage (current code) | publish (24) | stay_out (13) | went_out (25, already published) |
|---|---|---|---|
| newsworthy | **15** | 13 | 9 |
| market_pause | **4** | 0 | 7 |
| pass all replayed gates | 5 | 0 | 8 |

All 13 junk items stay out (good), but so do 15 of 24 real stories, and 9 of the 25 posts that DID go out last week would be refused today.
The refusals that matter, with the gate's own words:

- "Nvidia reinstates Jensen Huang as CEO. Stock skyrockets 27%" (#1162): no concrete consequence. The mega-cap move rule only counts a % when
  the story's family is "market"; a CEO story with a 27% move is family "corporate".
- "CrowdStrike to buy Zscaler in $44 billion deal" (#1173): no concrete consequence. MONEY counts only for hacks and liquidations; a $44B
  takeover counts for nothing.
- "AI rally set to add $2 trillion to Nasdaq-100" (Trending): no concrete consequence ("set to" is treated as a forecast).
- "Ukraine sinks Russian warship off Libya" (#1145), "Zelenskyy praises... drone operation that sank Russian warship" (#1143): no
  consequence / a statement. A sunk warship is not in any hard-event list.
- "Two injured, one critically, in Iranian attack on industrial plant in Bahrain" (#1105): no consequence; also quality.weak_story calls it "a
  round-up of several stories" (the commas in "Two injured, one critically,").
- "Chainflip confirms $12M hack" (#1098): below the $25M hack line.
- "US moves to block China's remote access to AI chips" (#559), "France confirms its troops are in Ukraine" (#645, a statement), "Iran claims
  strike on US radar base" (#592), "Poland... two Russian missiles" (#969, "diplomacy talk (says)"), "Nvidia plunges as SEC probe deepens AI
  selloff" (#993) and "Nvidia shares sink to 52-week low" (#1027): no figure the rules know, or a "says" read as a statement.

The pattern: the consequence path is a list of keyword rules per story type; anything outside the list (M&A, CEO changes, sinkings, export
controls, troop deployments, a stock move inside a corporate story, Dow points, gold/silver %) scores "none", and "none" is a refusal.

**5. Scheduling: slots wait, stories go stale, and holds empty whole slots.**
- 121 news events last said "waiting for the next slot" and 126 "under the bar" (decayed score) before they expired; 4-hour slots plus
  freshness decay mean a story first seen just after a slot is stale by the next one unless it breaks.
- Feed diversity's "two in a row" had no time limit: 08:00 and 12:00 UTC on 11-23 were skipped with #1149 (refineries, PUBLISH strong)
  and #1152 waiting. (Fixed by ce9e312, 12-hour run window.)
- A slot is "filled" at release, but 8 released events (#536, #552, #575, #599, #649, #658, #662, #667) were then refused by the delivery gate
  on both platforms: the slot was used and nothing went out.

**6. Publishing: market posts paused since 11-21.** `events.release()` drops every `market_post()` while `state.market_pause` is on, so
today's moves never became posts: S&P 500 +5.4%, Nasdaq +7.6%, Dow +3.2%, Nvidia +36%, gold +4.2%, silver +5.8% (Yahoo, Mon vs Fri
close). A move is only logged when it is refused, so whether US-STOCKS became a candidate today can be confirmed only from the next backup.
Silver was separately refused by the second source at 14:31 UTC: "Hyperliquid trade.xyz SILVER 44.19 vs Yahoo 43.49: 1.63% apart"
(XYZ_TOLERANCE commodity 1.5%): a level comparison between a perpetual's oracle and a COMEX front month, on a 6% day.

## The market pause and the liquidation data

- The pause was tripped automatically at 2026-11-22 09:35 UTC: "3 implausible or contradicted figures within 60 minutes". All 3 (and the
  12 in state.anomalies) are ONE coin-hour, PIEVERSE on Binance + OKX, re-noted on every 5-minute pass ($1.1M at 09:25, $3.3M at 09:30,
  09:35...). `safety.settle` counts rows (`len(recent(state, now))`), not distinct (kind, subject, hour). One small token tripped the breaker.
- The figures were real. PIEVERSEUSDT 11-22 09:00 UTC: price range 19.9% (median hour 3.6%), $84M traded (median hour $2.8M, 30x);
  $3.3M liquidated is 4% of the hour's volume. Today's holds are the same: BTC 13:00 UTC range 4.6% (6x median), $2.1B traded (5x);
  $100M liquidated on three venues. NVDA perp 13:00 UTC +18% range (18x), $456M traded (11x); $6.6M liquidated.
- Root cause of the "bad data" alarm: `safety.liquidation_problem` compares an hour to 50x the coin's MEDIAN recorded hour of the week. On a
  violent hour, liquidations scale with volume x range (both 5-30x), so 60-360x the median is normal for a real squeeze. The rule cannot tell
  a squeeze from a unit error.
- The genuine data bug (Telegram 204, 11-21: "$10.1B BTCUSD_PERP", coin-margined contracts priced as coins) was fixed in liqtape
  (`coin_m_contract_usd`, $100/$10 contracts) and is not recurring: no BTCUSD_PERP figure since.

Proposed (not implemented, owner decision; precondition for unpausing):
1. Breaker: count distinct (kind, subject, UTC hour) in TRIP_WINDOW, not rows.
2. Liquidation plausibility against the SAME hour's market, not the week's median: refuse when the hour's liquidations exceed a share of the
   reporting venues' traded value for that symbol in that hour (e.g. 15%), or when the price range is not elevated (a liquidation spike
   needs a move). Keep the absolute ceilings. The 11-21 unit error ($10.1B vs $286M traded) fails this test; PIEVERSE (4%), BTC (~2-5%)
   and NVDA (~1.4%) pass.
3. Scope the pause: a liquidation anomaly pauses liquidation posts; index/stock/commodity/crypto PRICE moves keep their own checks (the
   numbers gate, the second source, plausible_move limits) and go on.
4. Second source on price moves: compare the two sources' CHANGE over the window (do they agree on the move?) rather than the level, or
   widen the level tolerance with the move's size; the basis between a perp oracle and a futures front month is not an error.

## Not verified

- Whether US-STOCKS / NVDA / GOLD move events were created today (moves are logged only when refused; the next backup will show).
- Telegram messages 207/208 recorded as delivered but "Post not found" publicly (outside this scope; noted by the coordinator).
- Labels are one editor's judgement; `label: null` items (589) are unlabelled, not judged.
