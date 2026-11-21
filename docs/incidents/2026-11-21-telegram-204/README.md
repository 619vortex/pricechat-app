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
| Second check, exact window 02:48-03:47 (`raw/dapi_klines_1m_0248_0348.json`, 60 one-minute candles, fetched 2026-11-21 ~11:10 UTC) | 2,506,511 contracts = **$250.7M** traded; sell-initiated (taker sell) **$155.2M** |

A liquidation is a trade, so $10.1B can't happen in an hour that traded $250.7M. A long liquidation is a forced SELL, so the true
figure is also at most the $155.2M that was sold aggressively in the window: the post overstated even that bound about 65 times.

## Root cause
`app/tradingos/liqtape.py` `on_binance` stored `usd = price x quantity` for every symbol. On Binance COIN-M (inverse) futures the
quantity is a number of USD contracts, not coins. Each BTCUSD_PERP liquidation was valued at contracts x the BTC price
(about 57,000) instead of contracts x 100, which overstates it about 570 times. `base_of("BTCUSD_PERP")` also returned "BTCUSD_PERP" instead of "BTC",
so the raw contract name reached the post.

## The real figure
Every row behind the post was a BTCUSD_PERP row (the post's coin key). The true dollar value is sum(contracts x 100) =
the stated figure x 100 / price. BTC traded between 56,418.0 and 58,565.9 in the exact window (1-minute candles), and $10.1B is
rounded (10.05-10.15B), so the real figure is **$17.2M to $18.0M** (best estimate about $17.5M), well inside both bounds above. Binance's stream shows at most one
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

## Correction (drafts, NOT published: owner approval needed for each)

The wording rule: the $17.2M-$18.0M range is **our corrected estimate from our own recorded feed**, recomputed with the right contract
size. It is not an independently verified exact total: Binance publishes no per-hour liquidation total, and its stream shows at
most one liquidation per market per second. What Binance's own data does verify is the bound: in that hour, all sell-side trading in
BTCUSD_PERP came to about $155M, so $10.1B was impossible.

Recommended: edit message 204 in place (forwards and screenshots then lead to the corrected text) and post the reply under it.
Telegram keeps an "edited" mark on the message.

**A. Edited text of message 204** (Telegram caption/HTML; the original line struck through):
> <s>JUST IN: BTCUSD_PERP longs liquidated: $10.1B on Binance in an hour.</s>
>
> CORRECTION: the $10.1B figure was wrong. Our liquidation feed misread Binance's coin-margined Bitcoin futures: their size is
> counted in $100 contracts, and we counted each contract as a whole bitcoin, overstating the total roughly 570 times. Recomputed
> correctly, our records show about $17-18 million of Bitcoin longs liquidated on that market in the hour to 03:48 UTC. That is an
> estimate from our own feed, not an exchange-published total. Binance's trading data confirms the original figure was impossible:
> all selling in that market in that hour came to about $155 million.
>
> @thepricechat

**B. Correction reply** (posted as a reply to 204):
> CORRECTION to our 04:04 UTC post: we reported $10.1 billion of Bitcoin long liquidations on Binance in an hour. That was wrong.
>
> What happened: Binance's coin-margined Bitcoin futures are counted in $100 contracts. Our feed treated each contract as one whole
> bitcoin, which inflated the total roughly 570 times.
>
> Corrected: our own records, recomputed with the right contract size, show about $17-18 million (an estimate from our feed, not an
> official exchange total). Binance's public trading data shows the original figure could not have been right: total selling in
> that market that hour was about $155 million.
>
> We have fixed the error and added checks that hold back implausible figures before they publish. We're sorry for the mistake.

The alternative is to delete 204 and post only B. That removes the wrong number, but the channel then shows nothing at the spot
where readers saw it.
