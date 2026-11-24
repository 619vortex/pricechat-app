# Market events 2026-11-16..24: independent verification (2026-11-24)

Before these events become News Desk replay / regression examples, each was checked against at least two sources other than
Yahoo Finance (production's own source). Machine-readable result: `backend/tests/fixtures/market_events_verified_20261124.json`
(35 entries: 18 confirmed, 12 partial, 5 wrong). Raw responses: `docs/newsdesk/raw-2026-11-24/` (`_index.json` maps every file to
its URL and retrieval time; all fetched 2026-11-24 06:35-06:50 UTC).

Sources used: FRED (SP500, NASDAQCOM, DJIA, NASDAQ100, DEXCAUS), Nasdaq's own API (COMP, NDX, NVDA, ANT, META, TSLA, MSTR, SLV, GLD),
Google Finance quote pages, Binance, Coinbase Exchange, Kraken, Hyperliquid (`candleSnapshot`, crypto and trade.xyz markets),
trade.xyz docs. Yahoo is listed beside them for comparison only.

Not reachable: Stooq (JavaScript bot challenge), Cboe CDN (403), CNBC quote API (403), S&P DJI site (error page). CME/COMEX
settlement pages were not read: individual COMEX/NYMEX contract months are read through Yahoo's per-contract symbols
(HGZ26.CMX, HGH27.CMX, BZF27.NYM ...) and labelled as such. FRED had not yet published 11-23 for SP500/NASDAQCOM (nor 11-20 for
NASDAQCOM/NASDAQ100), so those days rest on Nasdaq/Google Finance plus FRED's earlier closes.

## Confirmed

| Event | Claim | Independent values |
|---|---|---|
| Nasdaq Composite 11-23 | +6.51%, 24,089.56 | Nasdaq: 24,089.56 vs 22,617.73 = +6.51%; Google Finance 24,089.56 +6.51% |
| S&P 500 11-23 | +4.73%, 7,237.17 | Google Finance 7,237.17 +4.73% (+326.78); FRED prior close 6,910.39 matches; xyz:SP500 7,239.5 |
| Dow 11-23 | 49,595.31, +3.01% | FRED DJIA 49,595.31 / 48,147.33; Google Finance identical |
| NVDA 11-23 | +31.48%, 165.05 | Nasdaq 165.05 / 125.53 (high 171.50 = +36.6%); xyz:NVDA 165.25 |
| Nasdaq 11-20 | -5.28% | Nasdaq 22,617.73 / 23,879.20 (FRED confirms the 11-19 base) |
| S&P 500 11-20 | -3.70% | FRED 6,910.39 / 7,175.83 |
| NVDA 11-20 | -14.01% | Nasdaq; biggest drop since 2025-01-27 (-16.97%) |
| Nasdaq / S&P 11-19 | -2.25% / -2.02% | FRED |
| BTC below $60K, 11-20, first since July | | Binance/Coinbase/Kraken: first sub-60K hour 15:00 UTC, lows 57,655-57,778; last sub-60K day 2026-07-02 on Binance and Kraken |
| BTC two-year low, Sat 11-21 | | Lows 55,100 (Kraken), 55,106 (Coinbase), 55,130 (Binance): lowest since 2024-09-09 |
| BTC rebound ~$61K, 11-23 | | Closes 61,190-61,271 on three exchanges. **Yahoo's BTC-USD 11-23 daily bar is empty** |
| Kraken CAD "71% drop", 11-21 | | XBT/CAD low C$24,250 at 08:00 UTC vs fair C$83,069 (Binance x FRED 1.4323) = -70.8%. Kraken's own USD/CAD fell to 0.30001: a CAD-book dislocation, not a bitcoin crash |
| Silver 24h falls 11-18..20 | -5.0% to -6.7% | Yahoo and xyz:SILVER agree within 0.1 pt |
| BTC vs Nasdaq, 1 year (Telegram 184) | -31.1% vs +9.9% | Coinbase 94,184 -> 64,817; FRED 22,870.36 -> 25,136.27 (+9.91%) |
| Week to 11-20 | MSTR -30.3%, NVDA -22.9%, Nasdaq -10.0%, S&P -8.6% | Nasdaq / FRED, exact |
| Gold/silver at the 11-23 open | +4.2% / +5.8% | Yahoo and xyz agree (gold +4.3-4.4%, silver +6.0-6.6% at 14:35-14:40 UTC) |
| Copper instruments (see below) | | trade.xyz docs + per-contract prices |

## Partly right (the event happened; a figure, time, date or wording differs)

| Event | Claim | Finding |
|---|---|---|
| Silver "~31% one-day crash" | | Real but on **2026-01-30** (SI=F -31.35%, SLV -28.54%, xyz:SILVER -26.86%), **not in this window**. Largest silver fall in the window: 11-20, SI=F -7.41%, SLV -6.74%. Don't use it as a November event |
| 11-23 intraday log (log-us-stocks, log-nvda) | S&P +5.4%, Nasdaq +7.6%, Dow +3.2%, NVDA +36% at 14:45 | These are the day's **highs**; at 14:45 it was +4.3 / +6.1 / +2.5 / +31; closes +4.73 / +6.51 / +3.01 / +31.48 |
| Telegram 199 (11-19, 15 min) | Nasdaq -1.9, S&P -1.4, Dow -1.1 | 16:45-17:00 UTC: S&P -1.30% (Yahoo and xyz agree), Nasdaq -1.73% (Yahoo 5m), Dow -1.01% |
| Telegram 203 / X 144 (11-20 20:00) | Nasdaq -4.6, S&P -3.1 "in 24h" | S&P 24h -3.22% (Yahoo) / -3.21% (xyz); Nasdaq -4.47% vs previous close, -4.80% over 24h. Closes -5.28 / -3.70 |
| Telegram 202, BTC 4H | -3.3% | Binance/Coinbase: -3.5% (13:00->17:00) to -3.9% (13:04->17:04) |
| Telegram 200, Anthropic | $112.20 vs $83 IPO, +35% | Nasdaq: open 111.50, high 115.10, close 97.17 (+17.1%). $112.20 was real near the open but ~$94 when posted at 18:11 UTC. $83 from news reports only |
| X 145, NVDA | -12.0% so far today (20:47) | Right at 20:47 (xyz 127.90); posted at 00:11 UTC after the -14.01% close (stale) |
| ETH 24h (941, 1021) | -8.5%, -8.3% | -9.0% at 21:20 and -7.8% at 22:45 on Binance and Coinbase |
| Gold 24h (875, 885, 897) | -5.1 / -5.2 / -5.0% | 885 confirmed (-5.2 on both); 875 and 897 measure -4.3/-4.5 hourly; close -4.62% |
| Copper 24h (712, 756, 768) | -5.55 / -5.16 / -5.09% | 712, 756 confirmed on both; 768 about -4.7% |
| Brent 24h 11-17 (586) | -5.0% | -4.9% (Yahoo) / -4.6% (xyz) |
| S&P "biggest drop since November 2026" (1088, 1097) | -3.7% | Figure right; FRED shows -4.36% on 2026-11-05, so the record line is meaningless |

## Wrong

| Event | Claim | Finding | Published? |
|---|---|---|---|
| **Gold 11-18 (743)** | "biggest one-day drop since March 2026: -3.6%", "gold closed at $3,725" | -3.6% was the 20:00 UTC price; settlement was 3,741.9 (**-3.16%**). GLD (-4.15%) and xyz:GOLD (-4.11%) both had a bigger drop on **2026-06-10**; only Yahoo GC=F (-3.57%) supports "since March" | **Yes: Telegram 194, X** |
| **Silver 11-20 01:40 (880)** | "-6.7% so far today" vs previous close $45.00 | $45.00 is 11-18's close; 11-19's was 44.32, so the real figure was about **-5.1%** (price $42.0 confirmed on two sources) | **Yes: X** |
| BTC 11-21 04:03 (1046) | "-9.1% so far today" since the 00:00 open ($61,902) | $61,902 is the 11-20 open; since the 11-21 open: about **-4.3%** | No |
| Brent 11-16 (509) | "tumbles 4.3% in an hour" | Yahoo's BZ=F briefly switched from the Jan to the Feb contract; Jan contract flat, xyz:BRENTOIL -0.3% | No |
| Brent/WTI 11-18/19 (682, 714, 728, 730, 761) | Brent -5.0..-5.3% / WTI -5.1% in 24h; Brent -3.6% in 4h | Measured across Yahoo's front-month roll (~08:00-09:00 UTC 11-18). Single contract: Brent **about -2%**, WTI **about -4%**, the 4h move -0.6% | No |

Two published posts carry wrong figures (Telegram 194 / X for gold, X for silver 880). They are outside this task's scope (no
correction drafted); the owner should decide whether to correct them like 204.

## Copper: what each instrument is

- **Hyperliquid trade.xyz `xyz:COPPER`** is a perpetual whose oracle follows a designated **COMEX copper futures** month. trade.xyz's
  roll schedule (`raw-2026-11-24/tradexyz_roll_schedules.md`): November starts on Z (Dec 2026); the roll **Z6 -> H7** ran 2026-11-06..12
  in 20% steps. Since 11-12 it tracks **March 2027 (HGH27)**. Measured on 11-23: xyz:COPPER 5.7096 vs HGH27 5.7095 at 20:00 UTC, and
  within 0.04% all session.
- **Yahoo `HG=F`** is the continuous front month, on 11-23 the **December 2026 (HGZ26)** contract: 5.624 at 20:00, 1.52% below
  March all day (the calendar spread). Production's 5.74 vs 5.59 matches 14:00 UTC (5.714 vs 5.5895).
- **Like-for-like:** compare xyz:COPPER with HGH27.CMX (the same month), or compare percentage changes (they agree: 11-18 -5.5% on
  both). Comparing it with HG=F builds in a ~1.5% basis (larger in the minutes when one side moves first). The same applies to
  `xyz:CL` (WTI Jan 2027 after 11-12) and `xyz:BRENTOIL` (Brent Feb 2027), whereas Yahoo's CL=F/BZ=F rolled on 11-18. Gold and
  silver on trade.xyz reference spot, so a small spot-vs-COMEX-futures basis remains there too.

## Notes for the replay

- Use close-to-close values from the confirmed rows as truth; treat "so far today" and "24h" items as snapshots tied to a minute.
- Yahoo's continuous futures (BZ=F, CL=F, HG=F, SI=F, GC=F) jump at contract rolls. 24h/4h/1h moves measured across a roll are not
  market moves (509 and the 11-18 oil items). A per-contract or roll-aware series avoids this.
- Two "so far today" items (880, 1046) used a previous close or open one day too old. Both look like stale daily bars at the
  start of a new session (futures at 01:40 UTC, crypto at 04:03 UTC).
- The fixture's liquidation items for COIN-M contracts (`BTCUSD_PERP`, `ETHUSD_PERP`, `..._261225`) are inflated by the 11-21 unit
  bug (see `docs/incidents/2026-11-21-telegram-204/CORRECTION.md`) and should not be used as real figures.
