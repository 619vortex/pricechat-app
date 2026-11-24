# Terminal upgrade 2 (2026-11-24)

Stacked on PR #16 (production since 2026-11-24 13:37 UTC, Railway bf26f789, code 1772c9e). Not deployed: owner approval needed.

## What changed

| # | Owner's item | Change | Evidence |
|---|---|---|---|
| 1 | Strict stale market data rejection | A day's change is published only when Yahoo's daily bars are the price's own session and the one right before it: CME's trading day (18:00 New York roll), crypto's UTC day, weekends and NYSE holidays. The pre-send re-read waits instead of publishing. A provider a session behind is logged and never counted toward the safety stop. | Silver 880 (measured from 11-18's $45.00 close, not 11-19's $44.32) and bitcoin 1046 (a day-old 00:00 open) are refused; `tests/test_nd_stale_data_20261124.py` |
| 2 | Intelligent price milestones | A crossed level or a 52-week extreme is judged from the asset's own daily closes: round numbers (bitcoin $10,000s, gold $500s, S&P 1,000s), when the price last closed on the far side, and whether it is past every close in the record. Months away is major for a core market; recrossed within weeks is noise; a single stock's level is a slot story at most. The post prints the record line ("Bitcoin last closed below $60,000 on 3 March 2026"). | Bitcoin below $60,000 on 11-20 was stored as a 1% move and judged routine; `tests/test_nd_milestones_20261124.py` |
| 3 | Less routine war coverage | A strike, a claim of one, or a death toll under ten inside an established war, with no stated consequence for markets, supply or diplomacy, is now notable (a slot filler), not significant. Consequences (output halted, sanctions, ceasefire talks, a war spreading, a nuclear plant) and escalations (50+ killed, NATO territory, global shipping) are unchanged. "Zaporizhzhia" (the city) is no longer read as the nuclear plant. | Production's 11-24 16:00 slot posted a routine Baltic ship strike over BlackRock's record $6B bitcoin ETF month; `tests/test_nd_war_coverage_20261124.py` |
| 4 | Cross-asset context | A market move prints what related markets did over the same window, measured the same way ("In the same hour: Nasdaq −1.5%, Gold +0.6%, Ethereum −7.0%"). A market that is shut or stale is left out, and the re-read before sending measures them again. Figures only: never a cause. | `tests/test_nd_cross_asset_20261124.py` |
| 5 | Branded news-card fallback | When no licensed picture passes and there is no article preview (Telegram) or link card (X), the post carries the branded Price Chat News card drawn from its own words (headline, tickers, outlets, time), instead of being held. Switch: desk state `card_fallback: false` restores the hold. | 24.3% of the 7-day replay's stories were held for want of a picture; `tests/test_nd_newscard_20261124.py` |
| 6 | Commercial feed permissions | Every feed has its licence class (59 entries: 7 permitted, 13 licence needed, 35 non-commercial, 4 unclear). 25 live, robots-allowed primary-source feeds whose terms permit commercial reuse with credit were added for a `feeds_policy: "compliant"` switch. The default is unchanged: the owner decides. | `docs/newsdesk/feed-licences/`; `tests/test_nd_feed_licences_20261124.py` |
| 7 | Admin liquidation-resume control (review) | Review of PR #16's control found three issues, now fixed: (a) resuming liquidations reset the shared breaker count, wiping a half-way price problem; now each scope has its own reset. (b) Automatic per-class pauses were invisible in Admin; they are now shown with a confirmed per-class Resume. (c) There was no way to pause liquidation posts again; there is now. | `tests/test_nd_scoped_resume_20261124.py`, `MarketSafety.test.tsx` |

Tests: backend 3,940 passed, 0 failed; frontend 602 passed; `tsc -b` clean.

## Feeds: what the owner must decide

Only 7 of the 59 current feed entries clearly permit commercial use. If only compliant feeds are kept:
- crypto news is almost entirely lost (CoinDesk, Cointelegraph, Decrypt and The Block are non-commercial; CryptoSlate, The Defiant and Bitcoin Magazine need written permission);
- fast world and conflict news needs a wire licence (AP, Reuters or AFP);
- there is no compliant source for market wraps, or for oil and shipping news.

Routes, cheapest first:
1. Email CryptoSlate, The Defiant and Bitcoin Magazine for headline-plus-link permission.
2. Get a Benzinga social-display quote for markets.
3. Get an AP or Reuters quote for world news.

Conditions for the compliant feeds:
- UN: credit, and tell the UN.
- OGL and CC BY: an attribution statement in the channel bios.
- No government seals.

## Corrections (verified 2026-11-24; the owner publishes each by hand)

Re-checked against the raw Binance candles kept in `docs/incidents/2026-11-21-telegram-204/raw/` and Yahoo Finance daily and 5-minute data fetched today.

- **204:** $250.7M traded and $155.2M sold in 02:48–03:47 UTC. BTC traded between $56,418 and $58,566, so the inflation factor was 564–585 ("about 570") and the true value $17.2–18.0M.
- **148:** BTC traded between $74,472 and $74,972, so the true value was $22,000–23,500 ("about $23,000"); the market traded $19.7M ("about $20M").
- **Gold (Yahoo GC=F):**
  - 18 Nov settled at 3,741.9 against 3,864.2, which is −3.16%.
  - Four days since June fell more: 10 June −3.57%, 28 Sep −3.54%, 24 June −3.39%, 30 Oct −3.29%.
  - The draft's "about −4.1% on 10 June" was the GLD fund's figure, not the cited source's, so it is corrected.
- **Silver (Yahoo SI=F):**
  - At 04:05 UTC on 20 Nov, when X 142 went out, silver was $41.55–41.75, about −6.0 to −6.3% against 19 Nov's $44.32 close.
  - It closed the day −7.41%, its biggest one-day fall since 15 May (−9.13%).
  - The draft's "about 5.1%" was the 01:35 figure, so it is corrected.

## Deployment (2026-11-24 19:26 UTC, owner approved)

- Checks before deploying:
  - CI green on 058e148 (backend, frontend, gitleaks).
  - Local backend suite: 3,946 passed.
  - Backup restore test passed (backup taken 10:46 UTC, integrity ok, alembic 0120 = code head).
  - No migration and no dependency change, so rollback needs no database step.
- Review fixes made before deploying (5366166):
  - The branded card prints a measured move's own window over its figure ("IN 1 HOUR"), so a 1-hour move beside the 24-hour chart is never read as the day's.
  - Every writer's copy is refused if it names a related market in the headline, or links one to the move as a cause or an effect (`moves.cross_problems`).
- Backend: Railway f4642e55, from release/kiwi-hardening fast-forwarded to 058e148.
- Frontend: Vercel pricechat-r7yx5xut5.
- Liquidation posts stay paused: only the owner's Admin resume clears `liq_pause`.
- Rollback:
  - Backend: `railway up --service api` from `D:/pc-wt/rollback-1772c9e/backend` (linked). bf26f789 is now REMOVED and can't be redeployed.
  - Frontend: `vercel promote pricechat-cgz2t89xr-price-chat.vercel.app`.
