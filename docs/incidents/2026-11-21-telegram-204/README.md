# Telegram 204: "$10.1B of BTCUSD_PERP longs liquidated on Binance in an hour" (2026-11-21)

Status: **published in error and still live**. No correction has been published. The correction below waits for the owner's approval.

## The post
- Telegram @Pricechatupdates message 204, delivered 2026-11-21 04:04:03 UTC (event #1045, "08:00 Dubai slot").
- Text: `JUST IN: BTCUSD_PERP longs liquidated: $10.1B on Binance in an hour.` / `Data: public exchange liquidation feeds.` / `@thepricechat`
- X refused the same event: "It doesn't name its source (public exchange feeds)", so nothing went out on X.
- Raw copies: `raw/telegram_204_embed.html`, `raw/railway_api_logs_excerpt.txt`.

## Why the figure is impossible
Binance's own public data (`raw/dapi_*.json`, fetched 2026-11-21 09:22 UTC from dapi.binance.com):

| | Value |
|---|---|
| BTCUSD_PERP contract size | **100 USD** per contract (`exchangeInfo`: 3 BTC contracts at 100, 27 other COIN-M contracts at 10) |
| All BTCUSD_PERP trading, 02:00-03:00 UTC | 351,370 contracts = $35.1M |
| All BTCUSD_PERP trading, 03:00-04:00 UTC | 2,863,142 contracts = $286.3M |
| The post's window (the hour to ~03:48 UTC) | at most **$321.5M** of trading in total |

A liquidation is a trade, so $10.1B of liquidations can't happen in an hour that traded at most $321.5M.

## Root cause
`app/tradingos/liqtape.py` `on_binance` stored `usd = price x quantity` for every symbol. On Binance COIN-M (inverse) futures the
quantity is a number of USD contracts, not coins. Each BTCUSD_PERP liquidation was valued at contracts x the BTC price
(about 57,000) instead of contracts x 100, which overstates it about 570 times. `base_of("BTCUSD_PERP")` also returned "BTCUSD_PERP" instead of "BTC",
so the raw contract name reached the post.

## The real figure
Every row behind the post was a BTCUSD_PERP row (the post's coin key). The true dollar value is sum(contracts x 100) =
the stated figure x 100 / price. BTC traded between 56,228.4 and 58,941.4 in the window, and $10.1B is rounded
(10.05-10.15B), so the real figure is **$17.1M to $18.1M** (best estimate about $17.5M). Binance's stream shows at most one
liquidation per market per second, so it is a lower bound for Binance COIN-M BTC.

Not verified: the stored rows themselves. The production database is on the Railway volume, and nothing in Price Chat's API reads
rows stored under "BTCUSD_PERP". To read them, the owner can run on the service (`railway ssh`):
`SELECT venue, coin, t, side, px, qty, usd FROM ts_liquidations WHERE coin='BTCUSD_PERP' AND t BETWEEN 1795229280000 AND 1795232880000;`
(02:48-03:48 UTC). The true value is `SUM(qty) * 100`, since qty holds contracts in those rows.

## The fix (branch fix/price-updates, not deployed)
- `liqtape.on_binance`: COIN-M symbols (`XXXUSD_PERP`, `XXXUSD_YYMMDD`) are valued at contracts x contract size (BTC 100, others 10).
  `base_of` strips `_PERP` and `_YYMMDD`.
- `brand_manager.sources.liquidation_topics` (used by the News Desk event lane, the old engine lane and Brand Manager) refuses
  any non-coin symbol. It holds any coin-hour above $500M for a person instead of publishing it.
- Tests: `backend/tests/test_price_updates_20261121.py`.
- Production is still exposed until that deploy. There is no targeted runtime switch: the only one is Admin > News Desk
  categories > crypto off, which stops every crypto story.

## Correction (draft, NOT published)

Recommended: edit message 204 in place, so forwards and screenshots lead to the corrected text, and post a short correction under it.

Edited text of 204:
> ~~JUST IN: BTCUSD_PERP longs liquidated: $10.1B on Binance in an hour.~~
> **Correction:** this post overstated the figure. About $17 million of Bitcoin long positions were liquidated on Binance's
> coin-margined Bitcoin futures in the hour to 03:48 UTC, not $10.1 billion. A unit error in our data feed counted $100
> contracts as whole bitcoins. It has been fixed.
>
> @thepricechat

Separate correction post (reply to 204):
> CORRECTION: Our 04:04 UTC post said $10.1 billion of Bitcoin longs were liquidated on Binance in an hour. That was wrong.
> The correct figure is about $17 million (Binance coin-margined BTC futures, 02:48-03:48 UTC). A unit error in our liquidation
> feed counted $100 contracts as whole bitcoins. We've fixed it and added checks so a figure like this can't publish
> automatically again.

The alternative is to delete 204 and post only the correction. That removes the wrong number, but the channel then shows nothing at the
spot where readers saw it.
