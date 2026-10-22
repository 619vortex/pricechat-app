# Market structure: liquidity, positioning and order flow (real feeds only)

Price Chat now shows where liquidity rests, how derivatives are positioned and who is trading aggressively, not only
where the price has been. Every number comes from a public exchange API. Every chart, caption and fact says what kind
of number it is:

| Basis | Meaning | Examples |
|---|---|---|
| **Observed** | read directly from one exchange's feed | a book level, OKX's reported liquidations |
| **Aggregated** | observed numbers from several exchanges added up (venues listed) | open interest across 5 venues, CVD Binance + OKX |
| **Model estimate** | a model's output, never a confirmed position | estimated liquidation zones |
| **Inferred pattern** | a pattern in observed trades, never proof of who traded | TWAP-like slicing |

## Audit (before this change)

| What existed | Source | Gap |
|---|---|---|
| Candles, funding, current OI, l2 book (20 levels a side) | Hyperliquid info API (`tradingos/market.py`) | one venue only |
| OI history | Price Chat's own 15-min recorder (`tradingos/oi.py`) | Hyperliquid only; Hyperliquid publishes no history |
| `/chart` (Telegram): candles, RSI/BB/MACD/MA, volume, funding, OI, depth | `tradingos/chartcmd.py`, `chart.py` | no CVD, liquidations, cross-venue data |
| Kiwi Brain Battlefield (website, members) | Hyperliquid l2Book + recentTrades sampled while the page is open (`brainx.Flow`) | heatmap / CVD only while watched; one venue; "liquidations not available" |
| Content Studio trading stories | 48-hour Hyperliquid price line for every story | the same generic chart whatever the story |

Reachable public feeds (checked 2026-10-20 from the laptop): Binance USDⓈ-M, Bybit linear, OKX swaps, Deribit, Hyperliquid.

## What was added

`backend/app/tradingos/structure.py` (each venue optional; a venue that fails is named with the reason, never filled in):

- **Aggregated open interest**: current total across Binance, Bybit, OKX, Deribit (BTC/ETH) and Hyperliquid; history
  from the exchanges' own published series (Binance + Bybit + OKX), summed only at timestamps all of them reported.
- **CVD**: taker buys minus taker sells in USD, Binance klines (taker buy quote volume) + OKX taker volume (5m and up), perps.
- **Order book**: every venue's book on one price grid, the price range each venue's public book covers, depth
  imbalance inside the range every venue covers, and **walls** (single levels ≥10× the venue's median level).
- **Reported liquidations**: live liquidation streams from OKX (every swap), Binance USDⓈ-M (all markets) and Bybit
  linear (the 30 most traded markets), `tradingos/liqtape.py`, plus OKX's REST list; buffered in memory and written to
  `ts_liquidations` every 2 minutes (kept 35 days, de-duplicated). A venue counts only while its stream is connected or
  when it has records in the window; Binance's stream shows at most one liquidation per market per second (a lower bound).
- **Estimated liquidation zones** (model): Binance OI increases (OKX's when Binance can't be reached) treated as new positions at the interval close, split
  long/short by the taker buy share, leverage mix 5x 20% / 10x 30% / 25x 25% / 50x 15% / 100x 10%, maintenance margin
  0.5%; a zone is dropped once price trades through it, and zones shrink when OI falls. The assumptions are printed
  wherever the zones appear.
- **TWAP-like execution** (inferred): runs of ≥8 same-side prints of near-identical size at regular intervals (5 s–10 min,
  coefficient of variation ≤0.15). Two sources: Binance trades (size ±3%, top 10% of print size) and Price Chat's
  **Hyperliquid trade tape** (`tradingos/hltape.py`: Hyperliquid's public trades websocket for BTC, ETH, SOL, last hour in
  memory; each run must come from one on-chain address, size ±10%). Never attributed: an address is not an identity.
- **Liquidity heatmap**: binned books of Binance + OKX + Hyperliquid for BTC, ETH, SOL every 2 minutes (kept 2 days),
  job `tradingos.structure` (covered by the `tradingos.` prefix in `SCHEDULER_ONLY_JOBS`). Migration 0074.

Where it shows:

- **Telegram `/chart`**: new options `cvd`, `aggoi`, `liq`, `zones`, `heatmap`, `walls`, `twap`, and `structure`
  (= cvd aggoi liq walls). The caption and the image's footer list each layer's basis and venues.
- **Kiwi Brain Battlefield** (website): layer chips (Aggregated OI, CVD, Liquidations, Est. liq zones, Book walls,
  TWAP-like, Heatmap). Walls, zones (dotted, "est.") and TWAP-like markers draw on the main chart; a Market structure
  panel shows each layer with its basis badge, venues, UTC times and what's missing. API:
  `GET /api/members/kiwi-brain/structure?coin=BTC&timeframe=1h&layers=oi,cvd,liq,zones,book,twap,heat`.
- **Content Studio**: `brand_manager/marketviz.py` picks the visual from the story's words (liquidation → liquidations,
  aggressive buying/selling or a big move → CVD, leverage/open interest/funding → aggregated OI, a wall → order book,
  algorithmic/TWAP → slices), adds the data's facts to the brief (so the fact checker accepts those numbers and only
  those), and the editorial renderer draws it (`MarketChart` in `worker/motion/src/editorial/Editorial.tsx`) with the
  basis badge, venues, units and UTC window. New story type: an OKX-reported liquidation flush (≥ $1M in an hour).

## Limits

- Hyperliquid doesn't publish market-wide liquidations. Binance blocks only its REST API from production's region: its
  liquidation websocket connects and delivers there (checked 2026-10-20: "Binance connected, 4 received"). Binance's old
  `/ws/` stream route no longer delivers anything; the `/market/ws/` route does.
- The recorder logs the live feeds' status once an hour ("Market structure streams: OKX connected, … received").
- Public books are shallow (Hyperliquid 20 levels, Binance futures 1000, Bybit 500, OKX 5000): BTC's common range is
  about ±0.1%, so imbalance is measured there; walls further out come from the deeper books.
- Hyperliquid has no OI history and no taker volume in candles; it is in the current OI total only.
- **Production (Railway) is in a region Binance blocks** (seen 2026-10-20: "BTC book Binance unavailable: blocks requests
  from this server's region"). There, Binance drops out of OI, CVD and books with that reason shown, the zone model runs
  on OKX, and TWAP-like detection runs on the Hyperliquid tape. Bybit may be blocked too (it then shows as skipped).
  Moving the Railway service to a region Binance serves would bring it back; that is an infrastructure choice, not made here.
- The Hyperliquid tape lives in the API process's memory: after a restart it needs a few minutes before runs can show.
- TWAP detection sees fills, not orders: randomised or tiny slices are missed, and coincidences are possible.
