# Telegram 204: correction, re-verified 2026-11-24

Status: **drafts only, nothing sent.** Message 204 is still public and uncorrected (channel page read 2026-11-24 06:2x UTC). Each
text below needs the owner's approval and is published by the owner (steps at the end).

## 1. What was published

- Telegram @Pricechatupdates message 204, 2026-11-21 04:04:03 UTC:
  `JUST IN: BTCUSD_PERP longs liquidated: $10.1B on Binance in an hour.` / `Data: public exchange liquidation feeds.` / `@thepricechat`
- **X: not published.** The production log shows X refused event 1045: `newsdesk: X couldn't follow Telegram on event 1045: the X
  text failed the checks` (04:04:03) and the owner alert `... was not delivered to X: skipped` (04:07:06) (`raw/railway_api_logs_excerpt.txt`
  lines 13-14). The 7-day replay fixture also has no X post for event 1045 (`x_posts` keys). The owner should still check
  @pricechat's timeline for 2026-11-21 around 04:04 UTC. An X reply is drafted below in case a copy turns up.

## 2. Verified facts (Binance's public API, re-fetched 2026-11-24 06:2x UTC)

| Fact | Value | Source (raw file) |
|---|---|---|
| BTCUSD_PERP contract size | **100 USD per contract** (ETHUSD_PERP and the other COIN-M coins: 10) | `GET https://dapi.binance.com/dapi/v1/exchangeInfo` -> `raw/verify_20261124_dapi_exchangeInfo_excerpt.json` |
| All BTCUSD_PERP trading 02:48-03:47 UTC (60 one-minute candles, the post's hour) | 2,506,511 contracts = **$250.7M**; taker-sell (aggressive selling) 1,551,823 contracts = **$155.2M**; price range 56,418.0-58,565.9 | `GET https://dapi.binance.com/dapi/v1/klines?symbol=BTCUSD_PERP&interval=1m&startTime=1795229280000&endTime=1795232879999&limit=60` -> `raw/verify_20261124_dapi_klines_1m_0248_0348.json` |
| Hourly totals | 02:00-03:00 $35.1M; 03:00-04:00 $286.3M (sell $173.9M); 04:00-05:00 $289.1M | `.../klines?symbol=BTCUSD_PERP&interval=1h&startTime=1795226400000&limit=3` -> `raw/verify_20261124_dapi_klines_1h.json` |

These match the 2026-11-21 investigation exactly (`README.md`, `raw/dapi_*.json`).

**What is and isn't obtainable from Binance (checked 2026-11-24, `raw/verify_20261124_fetch_log.json`):**

- `GET /dapi/v1/allForceOrders` and `GET /fapi/v1/allForceOrders` (the old public market-wide liquidation history): **HTTP 404**,
  Binance's error page. The endpoint has been withdrawn.
- `GET /dapi/v1/forceOrders`: **HTTP 401** `{"code":-2014,"msg":"API-key format invalid."}`. It needs a signed API key and returns only
  that account's own liquidations, so it can never give a market total.
- Binance's bulk-data archive `data.binance.vision` has a `liquidationSnapshot` series for BTCUSD_PERP, but its last file is
  **2024-10-14** (472 daily files, 2023-06-25..2024-10-14; `raw/verify_20261124_binance_vision_liquidationSnapshot_listing.txt`). The 2026-11-21 file returns 404.
- Binance's live stream (`!forceOrder@arr`) shows at most one liquidation per market per second, so even a perfect recording is a lower bound.

So **the exact liquidation total cannot be independently verified from any public Binance source.** What can be verified:

1. **Upper bound (verified):** a liquidation is a trade, and a long liquidation is a forced sell. All selling in BTCUSD_PERP in the
   post's hour was **$155.2M**; all trading **$250.7M**. $10.1B was impossible (65x the sell-side total).
2. **Best estimate (our own data, recalculated, not independently verifiable):** the code priced each row as `price x contracts`
   instead of `100 x contracts`, so the true value is `$10.1B x 100 / price`. With the price between 56,418 and 58,566 and $10.1B
   rounded (10.05-10.15B), the real figure is **$17.2M-$18.0M (about $17.5M)**. The stored rows were not read (production database, see README).

## 3. Did the same bug affect other published posts?

Searched: the 34 Telegram posts and 24 X posts in `backend/tests/fixtures/nd_replay_7d.json` (2026-11-16..23), and every post still
visible on https://t.me/s/Pricechatupdates (156 posts, ids 19-209, `raw/verify_20261124_channel_scan.json`).

- **Yes: Telegram message 148 (2026-11-09 05:11:42 UTC)**, an older "PRICE ALERT" lane:
  `⚡ JUST IN: BTCUSD_PERP LONGS LIQUIDATED: $17 MILLION ON BINANCE IN AN HOUR` / `Data: public exchange liquidation feeds.` / `$BTCUSD_PERP`.
  Same symptom (contract name `BTCUSD_PERP`, Binance only), same code (the fix ee38975 came on 11-21). Binance's data
  (`raw/verify_20261124_post148_dapi_klines_1m_0411_0511.json`, `..._1h.json`): the whole market traded **$19.7M** in the hour to 05:11
  UTC and only **$12.0M** was selling (hour 04:00-05:00: $17.5M traded, $7.2M sold), so $17M of long liquidations was impossible.
  Recalculated: $17M x 100 / 74,472-74,972 = **about $22,000-$23,500**. That is far below the $1M an hour the desk now requires for a
  liquidation story (`LIQ_HOUR_USD`), so it should never have been posted. X was in dry run on 2026-11-09, so it most likely never went to X; the owner should check.
- Message 147 (2026-11-08, "BTC longs liquidated: $8 million on Binance + Bybit + OKX"): coin key `BTC`, i.e. USDⓈ-M/Bybit linear/OKX
  rows, whose quantities were priced correctly (OKX inverse swaps were already valued in USD). Not affected; not independently verifiable either.
- Messages 60, 173 and 179 cite other outlets' liquidation figures (not our feed). Not affected.
- In the replay fixture only event 1045 (= 204) went out. About 60 unpublished items there carry the same inflated COIN-M figures
  (e.g. #1060 "BTCUSD_PERP ... $1.0 billion", #863 "$596 million", #942 "ETHUSD_PERP ... $57 million"). Their true values are about
  `figure x 100 / BTC price` (BTC) or `figure x 10 / coin price` (others): a regression replay must not treat them as real.

## 4. Does production now guard it?

Yes, in production since 2026-11-21:

- `backend/app/tradingos/liqtape.py` (`coin_m_contract_usd`, `on_binance`): COIN-M symbols (`XXXUSD_PERP`, `XXXUSD_YYMMDD`) are valued
  at contracts x 100 (BTC) or x 10; `base_of` strips `_PERP`/`_YYMMDD`. Commit **ee38975**, an ancestor of every production deploy
  since 62e67c8 (Railway 419e6422, 2026-11-21), and of b3498ef (deployed 11-23). Tests: `backend/tests/test_price_updates_20261121.py`.
- `backend/app/brand_manager/sources.py` `liquidation_topics` (used by the News Desk event lane and the engine): refuses any non-coin key
  (`LIQ_COIN`) and holds implausible hours for a person (`_liq_implausible` -> `app/newsdesk/safety.py`): absolute ceiling $500M
  (BTC/ETH) / $150M (others) since ee38975; 50x the coin's median recorded hour since 4b644a0 (in production from d72649b, 11-22).
- **Not yet deployed:** the traded-volume check (`LIQ_VOLUME_SHARE`, commit 2dc1294, on this branch line), which refuses an hour whose
  liquidations exceed 2x the hour's traded value on Hyperliquid. That is the rule that would catch a figure like 148's ($17M), which
  passed both the ceiling and (for a quiet coin) possibly the history rule.
- Remaining limit: COIN-M contract sizes are hard-coded (BTC 100, others 10), checked against `exchangeInfo` on 2026-11-21 and again
  today. A new COIN-M listing with another size would be mispriced; reading `contractSize` from `exchangeInfo` would remove that.

## 5. Proposed texts (house style: plain statement, source line, `@thepricechat`)

### A. Edit of message 204 (replace the whole caption)

```
CORRECTION: This post said $10.1B of Bitcoin longs were liquidated on Binance's coin-margined BTCUSD_PERP market in an hour. That was wrong.

Our feed counted each $100 contract as a whole bitcoin, which inflated the figure about 570 times. Recalculated, our records show about $17-18M in the hour to 03:48 UTC on 21 Nov. Binance does not publish an hourly liquidation total, so this is our estimate, not an exchange figure.

Binance's own trading data shows the original figure was impossible: the whole market traded about $251M in that hour, and about $155M of that was selling.

🔗 Binance market data · @thepricechat
```

### B. Correction reply (posted as a reply to 204)

```
CORRECTION to our 21 Nov 04:04 UTC post: we reported $10.1B of Bitcoin long liquidations on Binance in an hour. The real figure was about $17-18M.

What went wrong: Binance's coin-margined Bitcoin futures (BTCUSD_PERP) are counted in $100 contracts. Our feed priced each contract as one whole bitcoin, about 570 times too much.

The $17-18M is our own feed, recalculated; Binance publishes no hourly liquidation total to check it against. Binance's trading data confirms the original was impossible: that market traded about $251M in the whole hour.

The same error affected our 9 Nov post ("$17 million" of BTCUSD_PERP longs): the real figure was about $23,000.

The calculation has been fixed, and figures this far out of line are now held for review before they publish.

🔗 Binance market data · @thepricechat
```

### C. Edit of message 148 (optional but recommended; same error)

```
CORRECTION: This alert said $17 million of BTCUSD_PERP longs were liquidated on Binance in an hour. That was wrong: our feed counted each $100 contract as a whole bitcoin. Recalculated, the figure was about $23,000 (our estimate; Binance publishes no hourly total). That whole market traded about $20M in that hour.

@thepricechat
```

### D. X correction reply (267 characters; only if a copy of 204 is found on @pricechat)

```
Correction: a Price Chat Telegram post on 21 Nov said $10.1B of Bitcoin longs were liquidated on Binance in an hour. That was wrong. Our feed counted each $100 contract as a whole bitcoin. Recalculated: about $17-18M (our estimate; Binance publishes no hourly total).
```

If it is posted as a reply under the X copy itself, a shorter form (245 characters):

```
Correction to our 21 Nov post: Binance BTCUSD_PERP long liquidations in that hour were about $17-18M, not $10.1B. Our feed counted each $100 contract as a whole bitcoin. The figure is our recalculated estimate; Binance publishes no hourly total.
```

## 6. What the owner must do

1. **Telegram 204:** in the Telegram app, as a @Pricechatupdates admin, open message 204 > Edit, replace the text with A, save.
   Telegram shows an "edited" label. (Price Chat's bot can't do this unattended: no automated sending was done or is proposed here.)
2. **Reply:** reply to message 204 with text B (long-press 204 > Reply). Optionally pin the reply for a day.
3. **Telegram 148:** edit it with text C (or, if preferred, delete 148: it was an alert below the desk's own threshold and B already mentions it).
4. **X:** open @pricechat and look for a post around 2026-11-21 04:04 UTC (and 2026-11-09 05:11 UTC). The logs say none went out.
   Only if one exists, reply to it with D (or delete it and post D standalone).
5. Optional, to replace the estimate with our recorded total: on the Railway service (`railway ssh`) run
   `SELECT SUM(qty)*100 AS usd, COUNT(*) FROM ts_liquidations WHERE coin='BTCUSD_PERP' AND side='long' AND t BETWEEN 1795229280000 AND 1795232880000;`
   (rows stored before the 11-21 fix hold contracts in `qty`). If it differs from $17-18M, use that figure in A and B, still labelled
   "our records".
6. Deploy 2dc1294 (volume check) with the next News Desk release.

Raw files added today: `raw/verify_20261124_*` (dapi klines and exchangeInfo excerpt, post-148 klines, endpoint probe log, binance.vision
listing, channel scan).

## 7. For approval (2026-11-24)

Two historical corrections, ready word for word. Nothing is sent by Price Chat: the owner publishes each by hand after approving it.

| # | Post | What | Text | Approve |
|---|---|---|---|---|
| 1 | Telegram 204 (2026-11-21 04:04 UTC), $10.1B | Edit the caption | A | ☐ |
| 1 | Telegram 204 | Reply under it (also names 148) | B | ☐ |
| 2 | Telegram 148 (2026-11-09 05:11 UTC), $17M | Edit the caption | C | ☐ |
| - | X | Only if a copy of 204 or 148 is found on @pricechat (204 never went to X per the logs; 148 was in X dry run) | D | ☐ |

Separately, the independent check found two other published figures wrong (docs/newsdesk/2026-11-24-market-verification.md): gold,
Telegram 194 + X ("-3.6%, biggest one-day drop since March": a 20:00 UTC price; gold settled -3.16% and fell more on 2026-06-10), and
silver, X post 880 ("-6.7% so far today" measured from a day-old close; about -5.1%). No correction is drafted for those: the owner decides.
