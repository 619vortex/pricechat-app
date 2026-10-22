# Kiwi Brain: trading intelligence map and plan

Status as of 2026-10-16. "Real" means it runs in production on real Hyperliquid data; "placeholder" means it doesn't exist yet.

## The six engines, mapped to the code

| Engine | Where | Status |
|---|---|---|
| **Market Data** | `backend/app/tradingos/market.py` (Hyperliquid info API: `metaAndAssetCtxs`, `candleSnapshot`, `l2Book`, `allMids`, `clearinghouseState`), short TTL caches. `brain.market_snapshot()` adds the per-group **source + timestamp**, closed-candle indicators and a **stale** flag. | Real. Indicators: RSI, MACD, EMA 20/50/200, ATR, VWAP (rolling 24), Bollinger (20, 2), volume vs 20-avg, swing support/resistance, trend. No second data provider yet. |
| **Strategy** | `signals.py`: `trend_pullback_v1` (1h) and `breakout_4h_v1` (4h), deterministic, every NO TRADE says why. Research: `intraday.py`, `refine.py`, `backtest.py`, `history.py` (12 coins × 5000 candles + funding). | Code real for 2 strategies, both **switched off** 2026-10-16 (negative out of sample with live trade management, see below). Mean-reversion candidates tested and rejected; funding/positioning module: not built. |
| **User Signal** | `bot.py` (Telegram ENTER/REJECT/AUTO), website ENTER TRADE, AUTO (`execution.run_auto`). | Real. TradingView/JSON webhooks, natural-language rules, per-user strategy config: not built. |
| **Risk** | `risk.py` (`plan_trade`: stop-based sizing, leverage/position/daily-loss caps, liquidation-vs-stop, Hyperliquid $10 minimum, entry-zone check), `execution.check_live_permission` (API wallet still listed on Hyperliquid), pause switches (global + per member). | Real and deterministic; no AI in the path. Correlated exposure, portfolio drawdown cap, volatility circuit breakers, cooldowns: not built. |
| **Execution** | `execution.py` + `exchange.py` (Hyperliquid SDK via an approved API wallet; paper executor). Preview → confirm (single-use, 5 min), deterministic client order ids, exchange-side stop + TP1/TP2 (reduce-only), emergency close if the stop is refused, reconciliation every minute, P&L from exchange fills. | Real, including LIVE (verified on Hyperliquid 2026-10-16: PUMP short with stop + 2 TPs). Limit-order entries, explicit order lifecycle states beyond RESTING/FILLED/REJECTED: partial. |
| **Decision Logging** | `brain.py` + `kb_events` table (migration 0057). | **Real (Milestone 1).** See below. |

## Milestone 1: the durable decision timeline (done)

Every real event is a `kb_events` row: `kind` (market · strategy · signal · risk · order · exchange · position · system), `title`,
`data` (the evidence), `source` (e.g. `hyperliquid:info candleSnapshot 1h`, `risk-engine`, `hyperliquid:exchange live`) and
`source_at` (the data's own timestamp). Written by:

- the scanner job: a BTC/ETH market snapshot per run, every strategy evaluation (NO TRADE with its reasons, or the proposal
  with its indicators, entry, stop, target, R/R, score);
- signals: created, published, every status change (WAITING → ACTIVE → TARGET/STOP/EXPIRED/CANCELLED) with the price;
- the risk engine: every preview, **approved** (with the sized plan) or **rejected** (with the reason), for that member;
- execution: every order sent (kind, side, size, price, reduce-only, client order id) and Hyperliquid's answer (status,
  order id, filled size, average price, error); position opened / closed with net P&L.

Privacy: market/strategy/signal events are account-free; risk/order/exchange/position events carry the member id and are
returned only to that member (`GET /api/members/kiwi-brain/events`) or an admin (`GET /api/tradingos/admin/kiwi-brain/events`).
Keys whose names look like credentials are dropped before storage. Retention: market events 7 days, the rest 180 days.

Live numbers: `GET /api/members/kiwi-brain/market?coin=BTC&timeframe=1h` (cached 15 s).

## Next milestones (not started)

2. Strategy modules (mean reversion, funding/positioning) with structured outputs + versioning; risk additions (correlated
   exposure, drawdown, circuit breakers, cooldowns); end-to-end paper tests.
3. **Done (2026-10-16):** the Kiwi Brain page. Entrance: a backward Kiwi (`frontend/src/website/kiwibutt.tsx`) under the
   trading terminal on /earn (connected traders only); clicking its butt shows "YOU FOUND THE KIWI'S BRAIN 🥝" and opens
   `/earn/brain` (bookmarkable; once found, a small "🧠 Kiwi Brain" link appears next to the Kiwi). The page
   (`kiwibrain.tsx`): status (mode Paper / Approval / Live, state, last data update, strategies, next evaluation), market
   (real Hyperliquid candles + signal markers, indicators with source and time), decision timeline (filters per kind,
   evidence per event), "Why this trade?" per signal, performance split LIVE / PAPER / HYPOTHETICAL backtest. APIs:
   `GET /api/members/kiwi-brain/status`, `GET /api/members/kiwi-brain/candles` (members only). Kiwi Replay: not built.
4. Teach Your Kiwi: per-user config, natural-language → structured rules (shown and confirmed, never code), webhooks.
5. Replay (from `kb_events` + stored snapshots), backtesting lab UI, performance split by paper / backtest / live.
6. Telegram trade announcements from confirmed fills of an approved public strategy account, deduplicated, admin-controlled.
7. Admin control room; unattended live trading stays off until every safety check passes.

## Strategy refinement (2026-10-16): both live strategies switched off

`backend/app/tradingos/lab.py` replays the strategies with the **live trade management** (stop for the whole size, half at
TP1 = the signal target, the rest at TP2 = 1.5x, no time exit), real hourly funding (now 2.3 years, extended from 210 days),
fees and slippage. The decision rule was written in the module before the run: data split 60% selection / 40% holdout; a
variant replaces the live rule only if it beats it on selection, is positive on the holdout with at least 30 trades, is no
worse than live on the holdout, and runs on the existing execution.

| Rule | Trades | Expectancy (90% CI) | Selection | Holdout |
|---|---|---|---|---|
| breakout_4h_v1 (live) | 288 | -0.09R [-0.23, +0.05] | -0.01R | -0.20R |
| best breakout variants (volume 1.5x, BTC agrees, 55-candle, stop 1.5 ATR) | 260–480 | +0.09 to +0.11R | +0.18 to +0.22R | -0.03 to -0.07R |
| trend_pullback_v1 (live, 1h) | 395 | -0.16R [-0.27, -0.04] | -0.17R | -0.16R |
| pullback on 4h | 401 | -0.00R | +0.09R | -0.14R |
| mean reversion (1h / 4h candidates) | 42 / 36 | -0.11R / -0.43R | negative | negative |

Nothing passed. The variants that looked good on the first 60% all lost on the last 11 months (the textbook sign of fitting
the past). The earlier 15m models (backtest.py A–E) were all negative too. So `trend_pullback_v1` and `breakout_4h_v1` are
off in the scanner settings: no new automatic signals, so no automatic real-money entries. Open signals, open positions and
their exchange-side stops/TPs are untouched. Manual (owner) signals still work and stay labelled "✍️ Manual setup".

To bring a strategy back: add it as a `Variant` in `lab.py`, run `python -m app.tradingos.lab`, and switch it on only if
`decisions` says `adopt`. Re-running on the same holdout over and over slowly turns it into selection data, so new ideas
should be judged on data that arrives after today.

## Kiwi Brain 2.0 (2026-10-16): the command center

Frontend `frontend/src/website/kiwibrain.tsx` + `brain/` (full-screen, tabs LIVE BRAIN · BATTLEFIELD · PERFORMANCE · TIME
MACHINE, tab in the URL hash). Backend `app/tradingos/brainx.py` + `app/api/routes/kiwibrain.py` (all member-session only).

- Status bar: exchange connectivity, mode (Paper / Live·Approval / Live·Auto), market feed (a live allMids read), strategy
  engine (from the `tradingos.scan` job row: active / shadow / paused / off / failing / late, with the reason and next run),
  position monitoring (`tradingos.track` job age), protection (each position's stop as Hyperliquid's open orders show it),
  unrealized P&L, loss if every stop hits, Emergency Pause (= STOP NEW TRADES; protective orders stay).
- Strategy checks: `signals.analyze` / `analyze_breakout` record every rule (`Analysis.checks`: node, rule, ok, value).
  **Shadow mode**: with both strategies off, the scanner still evaluates and logs every market (labelled shadow, kept 7 days)
  but never creates a signal. `GET /members/kiwi-brain/evaluate?coin=` runs the real code live (incl. a dry-run risk plan).
- Battlefield: lightweight-charts (candles, volume, EMA20/50, VWAP, Bollinger, the member's entry/stop/TP/liq lines, signal
  markers; click a marker → Time Machine; timeline "Show on chart" → jumps to the candle with the logged values). Order-flow
  recorder `GET /members/kiwi-brain/flow` (l2Book + recentTrades sampled every 3 s while someone watches, bounded memory):
  heatmap, buy/sell volume, CVD, large trades, volume profile, OI/funding. Liquidations: not available from the public API, not
  estimated. History before a page was open is not reconstructed.
- Simulator: client-side, real contract specs (`/contract`), estimated liquidation labelled as an estimate; never orders.
- Performance: closed positions per member, live and paper apart, small samples flagged; backtests in a separate HYPOTHETICAL table.
- Time Machine: `/replay` fetches the candles for the signal's window; indicators are recomputed from candles up to the playhead.
- Ask Kiwi: `/ask` with evidence ids; citations not in the evidence are dropped; changes come back as proposals only.
  Production's model (Ollama) is OFFLINE, so the deterministic `direct_answer` mode answers the common questions from records.
- Teach Your Kiwi: `kb_configs` (migration 0058), versioned, rollback, impact preview on the last 50 signals; applied in
  `execution.run_auto` as filters only (logged as a risk event when they skip a signal).
- Telegram: `bot.announce` posts a reply to the signal card when a signal opens / hits target / hits stop / closes / expires;
  the website shows the same signal events as notifications (`/notifications`, on /earn and the Brain).

## Known risks

- Both live strategies are unprofitable or break-even in backtests: no profit claims.
- Partial closes and stop/TP edits have only been tested against a fake exchange, plus one live entry.
- Funding sign (positive = paid) is assumed, not verified against a live funding payment.
- Hyperliquid's docs don't state that API wallets can never withdraw; Price Chat never sends withdrawals or transfers.

## Kiwi Kitchen (2026-10-17): the 15m / 30m strategy, live

Backend `app/tradingos/kitchen.py` (job `tradingos.kitchen`, every 45 s), frontend `frontend/src/website/kitchen.tsx` (the chef
Kiwi next to the butt Kiwi on Earn). Strategy `intraday_15m_v1`: the `intraday.py` 15m entry models on CLOSED candles, confirmed
by the 30m candle that contains the setup candle, then liquidity, funding/crowding (open interest shown), costs in R (fees,
slippage, spread, funding over the max hold), quality and capacity (pause, open-signal limit, one open proposal per market,
30 min cooldown per market). States per market: SEARCHING · COOKING (only while the 30m candle is still forming) · READY ·
BURNT (the failing check is the reason). Every change is one `kb_events` row (source `kitchen`, public): the Earn page reads
`GET /api/members/trading-bot/kitchen` and then streams `GET /api/members/trading-bot/kitchen/stream` (SSE, members only); the
Kiwi Brain timeline lists the same rows. READY creates an ordinary signal (timeframe 15m, own max hold), so the existing
review, risk engine, ENTER / AUTO, exchange-side protection and Telegram cards apply unchanged.

Switches (Admin → Trading OS → Settings), both **off** by default:
* *Strategy: Kiwi Kitchen 15m / 30m* — off = SHADOW (evaluated and shown, nothing proposed).
* *15m signals may trade real money* — off = 15m signals trade PAPER only. On = TESTNET / LIVE too, but only for members who
  typed the opt-in phrase (Earn → Settings → Trading style). Existing accounts default to 1h / standard, so nothing changes
  for them.

Members' trading style (`tr_accounts`): timeframe 1h / 15m / both (which signals AUTO takes), frequency standard (≤ 6 new
trades a day, no cooldown) or active (their own cap ≤ 12 a day and a cooldown ≥ 15 min per coin). The risk engine enforces
the daily trade cap and the cooldown for every entry, AUTO or ENTER. Positions get a private Telegram "🥝 ORDER UP!" on open
and "🍽️ KIWI FINISHED COOKING!" on close (realized P&L after fees, PAPER / TESTNET / LIVE labelled), each sent once.

Backtest (`python -m app.tradingos.kitchen`, 12 coins, cached history ≈ 46 days of 15m, fees + slippage + real funding,
spread and capacity not replayable): 15m+30m **17 trades, +0.20R, 90% CI [-0.12, +0.53]**, 0.37 trades/day, holdout 5 trades;
15m without the 30m confirmation 23 trades, +0.14R [-0.16, +0.44]. Far too few trades to call it an edge: keep it in shadow
and paper until enough new (post-2026-10-17) data says otherwise.

## Kiwi ML (2026-10-18): a machine-learning filter on the kitchen's setups

`app/tradingos/ml.py`, model file `app/tradingos/ml_model.json` (shipped with the backend, ~47 KB). It can only REMOVE
setups the rules found; it never adds a trade, moves a stop or changes a size.

* **Data:** Hyperliquid only serves 52 days of 15m candles, which gave ~1,000 samples (too few: AUC 0.51). The training
  history is 2 years of Binance USDⓈ-M perps for the 12 research coins (`python -m app.tradingos.history binance`, public
  archive data.binance.vision, saved in `data/backtest/binance/`). Every closed 15m candle where a degen-mode entry
  model fired = one sample (28,783), replayed with `backtest.simulate` at Hyperliquid's costs. Label: net result > 0R.
* **Features (33):** side, model, regime, RSI, extension from EMA20 / VWAP, relative volume, ADX, ATR level and rank,
  stop distance, recent returns and range, room to the 12h high / low, candle body and wick, 1h slope / RSI / return,
  4h trend, hour of day, funding against the trade, costs in R, quality score, TP1 in R. Signed so + = in the trade's
  favour. The live kitchen calls the same `ml.features()`.
* **Model:** gradient-boosted trees in numpy (depth 3, 150 rounds), deterministic. Hyper-parameters picked on TRAIN's inner
  validation only.
* **Validation (rules fixed before the holdout was scored):** first 60% TRAIN, last 40% HOLDOUT; threshold chosen inside
  TRAIN. VALIDATED = on HOLDOUT, +0.03R or better per trade, better in ≥3 of 4 windows, ≥100 trades, AUC ≥ 0.53.
* **Result of the first training (2024-10-10 → 2026-09-30):** HOLDOUT without ML 763 trades at −0.157R, with ML 511 at
  −0.123R (better in 3 of 4 windows), AUC 0.518 → **not validated**. On Hyperliquid's own last 52 days: −0.114R → −0.099R.
  So it ships ADVISORY: every setup shows its estimate (Earn analysis, the "Kiwi ML" check, the ORDER UP card), nothing
  is blocked. Also learned: over 2 years the degen setups lose about 0.15R a trade after costs (the 46-day backtest said
  −0.07R); the filter reduces that loss, it does not create an edge.
* **Owner switch:** `strategies.intraday.ml` = `auto` (default: filters only when VALIDATED) | `filter` | `shadow` | `off`.
* **Retrain:** `python -m app.tradingos.history binance` then `python -m app.tradingos.ml`, run the tests, deploy. The
  test `test_the_shipped_model_matches_the_live_features_and_reports_its_holdout` fails if the file is stale.

## Kiwi Risk Team (2026-10-18): desks that can veto, an AI that explains

`app/tradingos/riskteam.py`, kitchen check `risk_team` ("AI Risk Team"), job `tradingos.riskteam` (60 s).
Five rule-based desks review every kitchen setup; each can only VETO or WARN (never add a trade, move a stop, raise size):
correlation (no second open signal on the same side: crypto moves together), loss streak (4 losses → 6 h pause), daily
loss (−3R today → done for the UTC day), volatility (setup candle > 3 ATR) and edge (always states the evidence: no proven
edge). The AI Chief Risk Officer (task `kiwi.risk_memo`, fast tier) writes a 2–3 sentence memo per reviewed setup from
the desks' numbers; a memo containing a number that isn't in the evidence, or no model at all, falls back to the rules'
own summary. The memo is a kitchen event ("🛡️ …") and shows on Earn; the ORDER UP card carries the team's verdict.
Owner settings: `strategies.risk_desk` = {enabled, same_side, streak, pause_h, day_stop_r, shock_atr}.

**2-year research (Binance perps, 2024-10 → 2026-09, `kitchen.backtest(source="binance")`):** every kitchen mode loses
after costs: degen 8 coins −0.121R/trade at 3.25/day (−142% at 0.5% risk per trade), standard BTC/ETH/SOL −0.104R at
0.72/day. Before costs the setups average ~0.00R; costs are ~0.12R a trade. The desk rules don't improve the per-trade
result on both halves (so none is claimed as an edge); they reduce total loss and drawdown by trading less
(same-side guard: max drawdown 147% → 129%). Until a strategy shows a positive holdout, keep 15m signals on PAPER
(`intraday_live` off).

## Edge research (2026-10-18): four new ideas, all FAIL

`backend/app/tradingos/edge.py` (`python -m app.tradingos.edge`, result in `data/backtest/edge.json`). Ideas and rule were
written in the module before the first run: PASS = profitable after costs and funding with Sharpe > 0.5 on BOTH the first
60% and the last 40% of 2 years of Binance perps (maker idea: expectancy > 0 on both, ≥100 holdout trades), and not
negative on Hyperliquid's own history.

| Idea | First 60% | Last 40% | Hyperliquid | Verdict |
|---|---|---|---|---|
| maker entries on the degen 15m setups | −0.044R | −0.138R | −0.023R (74 trades) | FAIL |
| daily time-series momentum (30d, vol-sized) | +23.7%, Sharpe 0.60 | −8.4%, Sharpe −0.02 | −19.5% | FAIL |
| weekly cross-sectional momentum (14d, top/bottom 3) | +31.7%, Sharpe 1.02 | −16.5%, Sharpe −0.98 | +2.4% | FAIL |
| funding fade (3d funding > 2σ vs 90d) | +15.3%, Sharpe 0.49 | −13.7%, Sharpe −0.20 | +19.9% (funding only cached 210 days) | FAIL |

Same pattern as every earlier test: what works on 2024-10 → 2025-12 stops working in 2026. Maker entries are the one
useful finding: they cut the degen loss from −0.121R to −0.071R a trade (costs, not edge). Nothing is switched on.

## Telegram bot commands (2026-10-19, community feedback)

- `/chart COIN [tf] [options]` in any order (chartcmd.py → chart.render_study): rsi, bb, macd, ma (EMA 100/200),
  volume, funding (Hyperliquid funding history), oi, depth (Hyperliquid l2Book), wide. Timeframes 1m 5m 15m 30m 1h 4h 1d,
  also "15" / "60" / "240". Missing data is explained in the caption, never drawn.
- Open interest: Hyperliquid has no OI history, so job `tradingos.oi` (oi.py) records OI + funding of the top 30 markets
  every 15 minutes (`ts_oi_snapshots`, 35 days). OI panels and the weekly OI change appear once that record covers the window.
- `/markets weekly` (weekly.py): 7-day BTC/ETH, movers, CoinGecko market cap / volume / dominance (sanity-checked), funding
  this week vs last, OI change, breadth. Cached 10 minutes. No news headlines (no verified feed).
- `/signal COIN` is market analysis, answered for anyone, in groups too (no account data). It never publishes or trades.
- `/idea COIN long|short [tp X] [inv Y] reason` and `/ideas [COIN]` (ideas.py, `ts_ideas`): members' opinions, 3 per day,
  no links, kept apart from signals and never traded.
- Unknown commands: a hint in private chats, silence in groups (they're usually another bot's).
- Admin → Trading signals → Settings: "/chart asked in the main chat" (`chart_routing`): answer there (default) or post
  the chart in the signal group (Bot Chat) with a one-line pointer. Open/close notices still go to `main_chat`.
- Admin → Trading signals → Performance (performance.py, `GET /api/tradingos/admin/performance`): per strategy and
  timeframe, outcomes, win rate, R, profit factor, expiry, hold time, unrealized, fees and the slippage the signal records
  leave out, data-quality checks and rule-based findings. Read-only: it changes no strategy.

## Kiwi Trading Engine v2, stage 1 (2026-10-19): structured decisions, shadow trading, portfolio risk checks

Audit first: most of the v2 spec already existed (market data + staleness, 15m/30m kitchen with 1h/4h context, measurable
regime classifier `intraday.regime`, deterministic risk engine `risk.plan_trade`, execution with idempotent client ids and
fill-confirmed states, reconciliation, kb_events audit trail, Kitchen states, ORDER UP / FINISHED COOKING announcements,
ML + risk team, honest backtests with selection/holdout). The gaps closed here:

* **`ts_decisions` (migration 0069, `app/tradingos/decisions.py`)**: every kitchen candidate (a 15m model fired on a closed
  candle) is one structured row: plan (ref, stop, TP1/TP2, R, max hold), estimated fees / slippage / cost in R, regime +
  `regime_confidence` (0-1, how far the logged features sit inside the regime's thresholds; not a probability), score,
  ML estimate, every check, and the decision READY (TAKE) · REJECTED (REJECT + the failing check) · WAITING_CONFIRMATION.
  Written in its own savepoint from `kitchen.tick_coin`: a failure never stops the kitchen. Kept 180 days (kb_events keep
  shadow evaluations only 7).
* **Shadow outcomes** (job `tradingos.shadow`, 5 min): after the max hold, every decision (taken, rejected or shadow) is
  replayed on the real 15m candles that followed with `backtest.simulate` (next open + slippage, stop first, TP1 half /
  TP2, time exit, taker fees, real funding) plus MAE / MFE. Always labelled HYPOTHETICAL; never mixed into real results.
* **Report** `GET /api/tradingos/admin/decisions` (Admin → Trading signals → Decisions & shadow): approved vs rejected, each
  filter's rejected candidates ("may be too restrictive" / "doing its job" / "not enough data" under 30), per regime and
  strategy version, and score / ML calibration ("validated" only with ≥30 per bucket, rising results and separation).
* **Risk engine portfolio checks** (`risk.portfolio_checks`, called from `execution._preview` after `plan_trade`): spread,
  slippage walked through the real l2 book, account exposure (x equity), same-side correlated positions, free margin,
  fees + slippage + funding in R, 7-day realized loss, minimum stop distance. **Advisory by default** (shown as ⚠️ on the
  preview and stored in the plan, trades exactly as before); the owner's switch `strategies.risk_v2.mode = "enforce"`
  (Settings → Fine-tuning) makes a failed check refuse the entry. No AI in this path.

Not changed: strategies, filters, member limits, live switches. Next stages (not started): calibration-based sizing (only
once a score is validated), Kitchen WAITING / SERVED / FINISHED states from real fills, the research pipeline
(proposal → backtest → out-of-sample → walk-forward → paper → approval), TradingView webhooks as untrusted inputs.
