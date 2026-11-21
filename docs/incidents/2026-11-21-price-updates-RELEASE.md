# fix/price-updates: release notes (2026-11-21, NOT deployed)

Branch `fix/price-updates`, worktree `D:/pc-wt/price-updates`. Based on the production branch `release/kiwi-hardening` 3073712
(Railway e789550e), plus a merge of `fix/market-urgency` 89d7bf7. Backend only: no migration, no frontend change, no new env var.

## What changes

### 1. Binance coin-margined liquidations (Telegram 204)
- `app/tradingos/liqtape.py`: a COIN-M symbol (`XXXUSD_PERP`, `XXXUSD_YYMMDD`) is valued at contracts x contract size (BTC $100, every
  other coin $10, checked against Binance exchangeInfo). `base_of` strips `_PERP` and `_YYMMDD`, so records are stored under the coin.
- `app/brand_manager/sources.py` `liquidation_topics` (feeds the News Desk event lane, the old engine lane and Brand Manager): refuses a
  record keyed by anything that isn't a coin symbol, and holds any coin-hour above $500M for a person (logged:
  "liquidations: ... held for review").
- Evidence and the correction draft: `2026-11-21-telegram-204/README.md`.

### 2. US index and stock posts (Telegram 199, 203)
- `sources.Quote` now carries Yahoo's own `previousClose`, the regular session's times and the previous session's close.
- `moves`: a US stock or index is measured as a 15-minute or 1-hour burst inside the session, or the **day against the official previous
  close** ("today"). It is never a rolling 4 or 24 hours. Without Yahoo's previous close there is no day figure at all. The price and its
  moment are Yahoo's `regularMarketPrice` / `regularMarketTime`. A window's reference price is matched on bar ENDS, so "15 minutes" really
  is 15 minutes.
- Combined posts carry each member's level and reference. The headline is "US STOCKS TUMBLE: NASDAQ -4.4% TO 22,824.39, S&P 500 -2.9% TO
  6,966.75 TODAY". Index levels are printed to the cent. A combined move without levels is never offered.
- Every market post ends with the data note: "Change from Thursday's close: Nasdaq 23,879.20, S&P 500 7,175.83. Market data: Yahoo
  Finance, 20:00 UTC, intraday (US market open)." At the close it says "at the close". The urgency line is kept. The note is printed in
  all three styles (clean, wire, classic) and on X. The old "Price from Yahoo Finance as of ..." footer is gone.

### 3. Pictures for Yahoo-measured stock and index posts
- `clean.picture`: a US stock/index move is checked against the same Yahoo quote the pre-send re-read used
  (`visual.verify_quote_move`). Every level, reference close, percent and the post's moment must match. Then the subject's library
  photograph follows (Wall Street for the indices, Nvidia/Apple/Tesla... for stocks). A mismatch stops the post (NumbersRefused). No quote
  means text only. Before this, every such post went out as text ("no verified series").
- `livedata._fetch` remembers what it fetched (90 s), so the check reads the identical answer.

### 4. Telegram/X freshness parity (merge of fix/market-urgency 89d7bf7)
- `livedata.refresh` before both Telegram's compose and X's deliver; `livedata.problem` in Telegram's final check and in X's publish
  (`engine._stale_market`). X 145 (Nvidia, 20:47 price, posted 00:11) can't happen again.
- **That merge brings the rest of that branch too.** The owner should know these behaviour changes before approving:
  - an urgency lane: up to 3 major/significant market moves a day on priority assets publish between slots;
  - the silver bullion library and the refusal of coin-collection pictures for metals;
  - owner-chat auto-discovery: the creator of the main chat / Bot Chat / news channel is DMed once and saved as the alert chat.
- Its test stubs were out of date (`photos.fetch(..., colour=False)`), which made 9 photography tests fail on that branch. The stubs are
  updated here. The production code was fine.

### 5. Phase 0 of the engine roadmap (owner approved 2026-11-21)
- **The emergency stop holds everything.** `engine.emergency_stop` (the News Desk stop from Admin or Terminal, or the network emergency
  pause) is checked in the Telegram tick, at the moment of each Telegram send, in X's `deliver_x`, and in `publish`, including the
  owner's test post. Before this, the stop held X only. Telegram kept posting and even took over the desk's decisions.
  X's automation switch alone still stops X only.
- **Market-data pause (new owner switch, off by default):** `PUT /newsdesk/settings {"market_pause": true}`. No desk-measured market
  post (moves, data posts, market checks, liquidations) is released or delivered. News goes on. There is no Admin button yet; it is API
  only.
- **Accuracy firewall:**
  - `checks.unit_problems` runs in `check_post` and `claim_problems`. A figure with a unit, scale or sign must match the source.
  - The "24 hours" words handed to every caption's source text are removed (telegram, queue, network).
  - News price tags: never a rolling window for US stocks or indices, and never under half the alert line.
  - A headline whose verb contradicts the measured sign is refused (`visual.direction_problem`), and "1.5%" no longer matches inside
    "11.5%".
- **Crypto 15m/1h/4h moves:** the chart check uses candles that cover exactly the move's window (1m/5m/15m) and compares the Yahoo
  headline with the Hyperliquid series at the two-source tolerance (0.5 points). These moves were all refused before.
- **Commodities** (gold, silver, oil, copper) get the Yahoo quote check and then their library photo, instead of unchecked text.
- **X's own text after a re-read** keeps the reference and "Market data:" line.
- **Owner alerts are never only logged.**
  - An alert with no linked chat, or one Telegram refused, is kept (`owner_alerts` rows `pending:<key>`). Kept alerts are delivered as
    one digest before the next alert, or by the watchdog.
  - The watchdog now also runs the desk's slot/delivery alerts, whatever state the Telegram lane is in, and alerts on news feeds that
    have been dead for 24 h.
- Telegram keeps the picture's `number_check` record (it was overwritten).

## Tests
- New: `backend/tests/test_price_updates_20261121.py` (22 tests, real Yahoo answers in `price_updates_yahoo_20261121.json`, Binance
  data in `2026-11-21-telegram-204/raw/`).
- Full suite: see the final report. The comparison is against the same branch before these fixes (64 failures there, all environment or
  clock dependent; none in the news desk).

## Deploy (owner approval needed)
1. Back up first (the usual pre-deploy backup).
2. `git -C D:/pc-wt/kiwi-hardening merge --ff-only fix/price-updates` (release/kiwi-hardening is the production branch).
3. `railway up` from `D:/pc-wt/kiwi-hardening/backend` (NOT the worktree root: that is linked to the stray "backend" project).
4. Watch the log for `visual editor: text only`, `liquidations: ... refused/held`, `livedata: event ... hold/faded`, `editorial gate`.
5. Rollback: check out 3073712 in a rollback worktree and `railway up` from its backend folder (never "Redeploy").

Old mis-keyed rows (`ts_liquidations.coin = 'BTCUSD_PERP'`, inflated `usd`) stay for 35 days. They can no longer publish (refused as
a non-coin), and no chart reads them. Optional cleanup on the service, once the evidence is kept:
`DELETE FROM ts_liquidations WHERE coin LIKE '%\_PERP' ESCAPE '\' OR coin GLOB '*USD_[0-9]*';`
