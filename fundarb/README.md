# fundarb

Delta-neutral perp-to-perp funding-rate arbitrage across Hyperliquid, Lighter, Orderly and Backpack.

**Status: Phase 1 (venue adapters + data model + history). Read-only. No order can be placed.**

```
pip install -e .            # or run with any Python 3.11+ that has httpx, pydantic, pydantic-settings, websockets
python -m fundarb quotes BTC-PERP ETH-PERP [--venues hyperliquid,lighter] [--save] [--json]
python -m fundarb intervals
python -m pytest tests      # offline: recorded real responses served through httpx.MockTransport
```

## Configuration (environment, validated at startup)

| Variable | Needed for |
|---|---|
| `FUNDARB_HL_ACCOUNT_ADDRESS` | Hyperliquid fee tier, positions, balance, open orders (public address only) |
| `FUNDARB_LIGHTER_ACCOUNT_INDEX` | Lighter account reads (Phase 4) |
| `FUNDARB_ORDERLY_ACCOUNT_ID`, `FUNDARB_ORDERLY_KEY`, `FUNDARB_ORDERLY_SECRET` | Orderly order book, fees, account (signing not wired yet) |
| `FUNDARB_BACKPACK_API_KEY`, `FUNDARB_BACKPACK_API_SECRET` | Backpack fees, account (signing not wired yet) |
| `LIVE_TRADING`, `LIVE_CONFIRM_TOKEN` | Execution gate. Both absent by default; even when set, Phase 1 adapters refuse to place orders |
| `FUNDARB_SQLITE_PATH`, `FUNDARB_HISTORY_DAYS`, `FUNDARB_BOOK_DEPTH` | History storage, window, order book depth |

No secret has a default. Malformed values (address format, a key without its secret, `LIVE_TRADING` without a token) stop
the process at startup.

## Endpoints used (all verified against the live venues on 2026-10-21)

| Venue | Data | Endpoint | Auth |
|---|---|---|---|
| Hyperliquid | funding (current hour), mark, index (oracle) | `POST /info {"type":"metaAndAssetCtxs"}` | none |
| | funding interval | `POST /info {"type":"fundingHistory"}`, spacing of timestamps | none |
| | order book (20/side) | `POST /info {"type":"l2Book"}` | none |
| | fees | `POST /info {"type":"userFees","user"}` → `userAddRate` / `userCrossRate` | public address |
| | positions, balance, orders | `clearinghouseState`, `openOrders` | public address |
| | stream | `wss://api.hyperliquid.xyz/ws` `activeAssetCtx` | none |
| Lighter | markets, fees (percent) | `GET /api/v1/orderBooks` | none |
| | mark, index | `GET /api/v1/orderBookDetails?market_id=` | none |
| | funding + interval | `GET /api/v1/fundings?market_id=&resolution=1h&...` (rate in percent + direction) | none |
| | order book (≤250/side) | `GET /api/v1/orderBookOrders?market_id=&limit=` | none |
| Orderly | markets, **funding_period** | `GET /v1/public/info` | none |
| | funding | `GET /v1/public/funding_rate/{symbol}` (`est_funding_rate`) | none |
| | mark, index | `GET /v1/public/futures/{symbol}` | none |
| | order book, fees, account | `GET /v1/orderbook/{symbol}`, `/v1/client/info` … | Orderly key (ed25519), **not wired** |
| Backpack | markets, **fundingInterval (ms)** | `GET /api/v1/markets` | none |
| | funding, mark, index | `GET /api/v1/markPrices?symbol=` | none |
| | order book | `GET /api/v1/depth?symbol=` (bids arrive worst-first; re-sorted) | none |
| | fees, account | authenticated endpoints | ED25519 key, **not wired** |

## Funding intervals (fetched, never hard-coded)

| Venue | Interval on 2026-10-21 | Source |
|---|---|---|
| Hyperliquid | 1 h | spacing of `fundingHistory` timestamps |
| Lighter | **1 h** (brief said 8 h) | spacing of `/fundings` timestamps |
| Orderly | 8 h | `funding_period` field |
| Backpack | 1 h (all 103 perps) | `fundingInterval` field, logged at WARNING on startup |

## Assumptions

- `funding_rate_raw` sign: positive means longs pay shorts, on every venue.
- Lighter `rate` is a percent, signed by `direction` (`long`: longs pay). Lighter exposes no public predicted rate, so its
  quote uses the last settled rate (`funding_kind = "last_settled"`); the other three use the running interval's rate.
- Hyperliquid's index price is its oracle price (`oraclePx`).
- `depth_usd_1pct` is the thinner side (bid or ask) within ±1% of mid. When the fetched levels end inside the band,
  `depth_complete = False`: the venue may have more depth than counted, never less. Hyperliquid's `l2Book` returns 20 levels
  per side, which usually ends inside the band for majors.
- Fees that a venue only reveals to an authenticated account are reported as `None` with the reason in `fee_source`,
  never as 0.
- Canonical symbols: `BTC-PERP`. Thousand-unit contracts are `1000X-PERP` (Hyperliquid `kPEPE`, Lighter `1000PEPE`,
  Backpack `kPEPE_USDC_PERP`). Requests always use each venue's own market name.
- Orderly listings with a broker suffix (`PERP_X_USDC_mythos`) are skipped; only `PERP_X_USDC` counts.

## Uncertain / needed from the operator

1. **Lighter's interval.** Its settled-funding history is hourly, which contradicts the brief's "8 h". The query uses
   `resolution=1h`, so please confirm from Lighter's docs that funding really settles hourly, and confirm `rate` is a percent.
2. **Orderly order book.** It is authenticated (`orderly-account-id header is empty` without it), so Orderly quotes fail
   with that reason until an Orderly account id + key/secret are provided and the signing scheme is confirmed on it.
3. **Backpack reference implementation.** The brief asks to mirror a public Backpack↔Hyperliquid implementation's adapter
   structure and WebSocket handover. Its URL wasn't provided; the Backpack adapter is REST-only until it is.
4. **Fees for Orderly and Backpack** need the authenticated endpoints (same signing work as 2 and 3).
5. **Lighter account endpoints** (positions, balance, orders) and `lighter-sdk==1.1.6` (pinned in the `live` extra; 1.1.8
   is the latest on PyPI) are Phase 4 work.

## Phase 2: signal engine + `scan` (read-only)

```
python -m fundarb scan [--top 10] [--shortlist 25] [--leverage 2] [--buffer 0.25] [--hold 168] [--venues ...] [--json]
```

For every symbol on 2+ venues and every venue pair (backend/fundarb/signals.py):

- **Orientation:** the short leg is the venue with the higher hourly-normalized rate. This also works with negative funding: the long leg is then the one being paid.
- **7-day spread history:** each venue's own settled funding, expanded to hourly so 1h, 4h and 8h venues line up. From it: the mean, standard deviation, share of hours paid, and the current reading's z-score. Readings of ±2σ or more are flagged as extremes likely to revert.
- **Capital-adjusted APR:** `gross / (2 legs × 1/leverage × (1 + buffer))`. There's no cross-margining, so each leg posts its own margin and buffer.
- **Costs:** taker fees from each venue, plus slippage from walking the live order book at 5% of the thinner book's ±1% depth. Above 10% of that depth, the opportunity is rejected.
- **Break-even hours:** round-trip costs ÷ the current hourly spread. Anything above 72 hours is rejected.
- **Ranking:** capital-adjusted 7-day mean − 2σ (a lower bound on typical carry). A steady 15% ± 1.5% outranks a jumpy 25% ± 8%.
- **Flags:**
  - funding at a venue's cap (Orderly reports its cap; a crowded reading that can squeeze);
  - zero-fee legs (Lighter today), with the net shown again at a stress fee (`post_subsidy_taker_bps`, an assumption labelled as such);
  - too little shared history;
  - unknown fees or books. Net figures are left blank rather than shown as 0.

Hyperliquid fees: your tier when `FUNDARB_HL_ACCOUNT_ADDRESS` is set, otherwise the published base tier from the same `userFees` response.

Findings from the first live run (2026-10-21):
- **The interest component isn't unique to Hyperliquid.** Hyperliquid, Backpack and Orderly all pay the same 0.01%/8h (0.00125%/h) at zero premium. In a pair, that part cancels between the legs.
- **Backpack's large spreads are mostly noise.** For example, 1000PEPE showed +111% gross, but its 7-day history is +35% ± 35%. The variance ranking puts these below steadier pairs.
- **Orderly pairs can't be priced net** until Orderly authentication is wired: its books and fees need an account.
- **The persistence count is trivially satisfied.** Over the hourly-expanded history it currently passes for every spread. The z-score and paid share are the more useful filters.
