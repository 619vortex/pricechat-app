# Pokémon Paper Exchange (PAPER TRADING ONLY)

Route: **/pokemon/exchange** ("📈 Trade Pokémon" on the Pokémon Market). Backend: `app/exchange/`, routes
`app/api/routes/exchange.py`, models `app/models/exchange.py`, migration `0040`. The physical-card marketplace is separate
and unchanged.

No real money anywhere: paper USDT can't be deposited, withdrawn, redeemed or settled; contracts are game instruments
and convey no ownership of any card. Everything is labelled PAPER TRADING.

## 1. Markets (Phase A, production data 2026-10-10)

Selected on pricing coverage and agreement between sources, not popularity alone. All English, graded **PSA 10**
(the only grade with verified completed sales in the database).

| Symbol | Card | Clean sales used | Sales vs Scrydex | Reference (card) |
|---|---|---|---|---|
| CHAR1ST | Charizard, Base Set 1st Edition #4/102 | 5 (2 rejected: $330, $4,950 misidentified) | 0.1% | $450,270 |
| CHARUNL | Charizard, Base Set Unlimited #4/102 | 5 | 0.4% | $34,933 |
| SHCHAR | Shining Charizard, Neo Destiny 1st Ed #107/105 | 5 (3 rejected) | 36.5% → sales index, low confidence, review | $55,596 |
| DKCHAR | Dark Charizard, Team Rocket 1st Ed #4/82 | 5 | 3.1% | $23,638 |
| BLASTUNL | Blastoise, Base Set Unlimited #2/102 | 5 | 13.8% | $9,890 |
| MEWTWOUNL | Mewtwo, Base Set Unlimited #10/102 | 5 | 14.9% | $5,097 |
| PIKA1ST | Pikachu, Base Set 1st Edition #58/102 | 5 | 9.6% | $7,230 |
| GENGAR1ST | Gengar, Fossil 1st Ed Holo #5/62 | 5 | 5.9% | $58,337 |
| ALAKA1ST | Alakazam, Base Set 1st Edition #1/102 | 4 | 1.1% | $42,994 |
| DRAGO1ST | Dragonite, Fossil 1st Ed Holo #4/62 | 5 | 3.5% | $20,150 |

Left out: Pikachu Illustrator (not in the catalogue), Lugia Neo Genesis 1st Ed (2 sales, sources 30% apart), Umbreon Gold
Star (2 sales, 50% apart), Rayquaza Gold Star (no sales), Blastoise / Venusaur / Mewtwo Base Set 1st Ed (≤ 3 sales).

## 2. Two prices

- **Reference** (real card): `app/exchange/oracle.py`. Sources: verified completed sales of the exact card + grade
  (`pokemon_sales`, Scrydex sold listings, verified, not excluded, last 365 days) and Scrydex's PSA 10 estimate
  (`pokemon_provider_valuations`). Outliers > 2x from the year's median are rejected and flagged for review. Sales index =
  median of the last 5 clean sales within 180 days. Combined with Scrydex (average) when within 35%; otherwise the sales
  index if 3+ clean sales (low confidence, review) else the market is insufficient. Confidence: high / medium / low.
  Staleness: no clean sale in 120 days and Scrydex older than 14 days → stale (maker stops quoting); nothing usable →
  insufficient (trading halted). Damping: one update moves the index at most 15% (flagged for review). Every evaluation is
  stored in `ex_oracle_snapshots` with provenance (sales used/rejected, Scrydex value + day). Refreshed every 15 minutes.
- **Paper price**: the last executed paper trade. Candles come only from executed trades. The premium/discount to the
  reference is shown.

## 3. Contract rules

1 contract = **1/1,000 of the card's reference value**, fixed (`multiplier_den = 1000`). Whole contracts, 1–1,000 per
order, tick 0.01 paper USDT. Fees: **0.10% taker, 0% maker**, rounded down to the cent, paid to the fees account. Limit
prices must be within ±50% of the reference. Market orders are IOC and fill at most 5% beyond the best opposite price at
arrival. No leverage, margin or short selling: you sell only contracts you own.

## 4. Wallets and ledger

Every member's account is created on first use with **10,000 paper USDT** from the treasury. Double-entry journal
(`ex_journals` + `ex_entries`), every journal sums to zero per asset; balances (`ex_balances`: available + held) always
equal the ledger (`ledger.reconcile`). Holds reserve collateral for open orders. All balance changes are guarded UPDATEs
that refuse to go below zero; journals are unique by reference (idempotent). Admin reset (`POST /api/exchange/admin/reset`)
cancels orders, returns everything to the treasury and refunds 10,000, in one journal, audited (`ex_audit`).

## 5. Matching engine (`app/exchange/engine.py`)

Price-time-priority central limit order book. Market and limit orders, partial fills, cancellation, fills at the resting
order's price, self-trade prevention (the resting order is cancelled), collateral reserved before matching. Concurrency:
a lock per market in-process plus a write lock as the first statement of each matching transaction (SQLite: database
write lock; PostgreSQL: the market row). Idempotency: `(account, client_id)` unique; retries return the first order.
Average-cost P&L per position with fees.

## 6. Simulated market maker (`app/exchange/maker.py`)

A labelled system account ("SIMULATED MARKET MAKER"), funded once from the treasury with 1,000,000 paper USDT and about
30,000 paper USDT of inventory per market at seeding. 3 bid + 3 ask levels: 1.00% spread around the reference, 0.60%
between levels, ~1,500 paper USDT per level (1–50 contracts). Inventory skew moves quotes toward its target; no bids
above 2x target inventory; asks only from inventory it owns. Re-quotes every minute and after trades; pulls all quotes
when the reference isn't "ok" or the market is paused; quotes one level when members bring depth within 2% of the
reference. Its orders are held like anyone's: it can't create money and can't lose more than its own balances. Its
levels and fills are tagged SIM.

## 7. Leaderboards

Equity values contracts at the **reference**, not the paper price (can't be pumped). Boards: highest equity, best 7-day
and 30-day return (daily equity snapshots), most profitable single trades. Eligible after 3 fills; one account per Kiwi
Passport; the maker is never ranked. Achievements: first trade, 10 / 100 fills, liquidity provider, 5 markets, whale
fill, in the green.

## 8. Operations

One job, `pokemon.exchange`, every minute (≤ 1 s of work: maker re-quotes; oracle every 15 min; equity snapshot daily),
inside the existing scheduler allow-list (`pokemon.`). No API calls, no media work. Rate limit: 12 orders / 10 s and 600 /
hour per member. Admin endpoints under `/api/exchange/admin/*` (owner only): overview + reconciliation, pause / resume,
oracle refresh, maker re-quote, reset.

## Perpetuals (paper, 1x–10x)

Route: `/pokemon/exchange?mode=perps` (the **Spot / Perps** switch at the top of the terminal). Same 10 markets, symbols `<SPOT>-PERP`. Paper USDT only.

| Rule | Value |
|---|---|
| Leverage | 1x–10x per order; isolated margin per position (max loss = that position's margin) |
| Index | the spot market's real-card reference (verified PSA 10 sales + Scrydex, see above) |
| Mark | perp mid (or last, else index) **clamped to ±5% of the index**: a thin book can't force liquidations |
| Funding | hourly, rate = (mark − index) / index / 24, capped ±0.05%/h; positive → longs pay shorts; paid from/to margin |
| Maintenance margin | 5% of position value at the mark |
| Liquidation | the insurance fund takes the whole position over at the mark (recorded trade, `kind=liquidation`), 1% fee to the fund; the fund absorbs any shortfall and unwinds 10 contracts/minute into the book |
| Insurance fund | system account seeded with 500,000 paper USDT from the treasury |
| Fees | 0.10% taker, 0% maker, rounded down |
| Halted index | `insufficient` evidence → closing / reduce-only orders only; the maker stops quoting on stale or insufficient |
| Simulated market maker | same labelled account as spot; 3 levels each side, 2x leverage, position limit ≈ 30,000 notional, inventory skew |

**Ledger.** Margin moves from the member to the `escrow` system account; realized P&L and funding settle through `clearing` (may go negative: it nets to zero across both sides). Every movement is a balanced journal. Invariants checked by `perps.check_invariants` (shown in the admin overview): escrow balance == Σ position margins, and Σ position sizes == 0 per market.

**Engine.** Same price-time CLOB pattern as spot (per-market lock + `seq` write lock, idempotent `client_id`, self-trade prevention). Both sides' funding is checked read-only (`_shortfall`) before any balance changes; a resting order that can no longer fund its margin is cancelled. Admin reset and member deletion hand open positions to the insurance fund at the mark (`force_close`).

**Job.** `pokemon.exchange` (every minute) runs `perps.tick`: funding when due, maker re-quote, liquidation sweep, insurance unwind.

**Tests.** `backend/tests/test_perps.py` (11): leverage + close P&L, shorts / reduce-only / limits, flipping, funding direction, mark clamp, liquidation → insurance + unwind, bankruptcy covered by the fund, halted index, admin reset, 12-thread concurrency with reconcile, API end-to-end. Frontend: `Exchange.test.tsx` covers the Perps tab.

## Simulated auto-trader (owner switch, admin only)

Dashboard > Website > **Exchange auto-trader** (`/admin/website`). Off by default; when on, job `pokemon.exchange.auto` runs every 15 s:
each card gets a simulated price path (mean-reverting random walk with momentum, never more than `swing_pct` from the REAL reference),
the simulated market maker centres its quotes on that path, and a separate labelled system account, **SIMULATED AUTO-TRADER**
(funded once from the treasury by a journal), takes those quotes with small market orders (`activity` trades/min per market, up to
`max_trade_value`) on spot and/or perps. Moods: Snorlax (1/min, ±1%), Pikachu (4/min, ±2%), Charizard (10/min, ±3.5%).

Transparency: its fills are ordinary recorded trades, shown as **SIM**; markets report `auto: true` and the site shows
"Simulated activity on". It is never ranked, never in member equity, never trades with itself, never backfills history, and stops
when the reference is stale/insufficient (the maker pulls its quotes). Switching it off re-centres the maker on the real reference.
Settings and path state live in `ex_settings` (migration 0042); changes are audited (`auto.config`). Tests: `tests/test_exchange_auto.py`.
