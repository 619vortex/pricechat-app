# Engine Phase 1: release notes (2026-11-21, NOT deployed)

Branch `feat/engine-phase1` (worktree `D:/pc-wt/phase1`). It is based on production `release/kiwi-hardening` 62e67c8 (Railway 419e6422)
and includes `feat/newsdesk-corrections` 816b03b. Backend AND frontend (Admin News Desk page, Terminal labels). No migration and no new
environment variable. Market-data posts stay paused (`market_pause` unset = paused) until the owner resumes them.

## 1. Plausibility limits and the automatic safety stop (`app/newsdesk/safety.py`)
- **LIMITS**, per asset group and window, set well above every real move in two years of Yahoo daily closes and 60 days of 5-minute bars
  (`tests/newsdesk_plausibility_calibration_20261121.json`) and above known historic crashes:
  - index: 8% in 15 minutes, 25% in a day;
  - stock: 40% / 80%;
  - crypto majors: 25% / 60%;
  - crypto alts: 40% / 80%;
  - commodities: 15% / 50%.
  A change beyond its limit is never measured as a move (moves, data posts, news price tags). It is held.
- **Liquidations:** an hour above the coin's ceiling (BTC/ETH $500M, others $150M) or above 50× the coin's median recorded hour of the
  last week is held. A 570× unit error (post 204) trips both rules; a 20× error trips the history rule.
- **Other held figures:** a disagreement with the independent second market, and a figure the picture check finds mismatched.
- **Every held figure** is logged, recorded on the desk (last 50) and reported to the owner. Nothing is published.
- **Safety stop:** 3 held figures within an hour switch `market_pause` on automatically, with the reason. The owner is told
  ("SAFETY STOP"). Resuming resets the count.

## 2. Independent price verification (`sources.second_opinion`, `livedata.second_market`)
- **Which second market:**
  - crypto: Hyperliquid (as before);
  - the S&P 500, NVDA, TSLA, AAPL, COIN, MSTR, META, AMZN, MSFT, GOOGL, gold, silver, WTI, Brent and copper: the oracle price of
    Hyperliquid's trade.xyz market (tolerance 1% for the index, 1.5% for stocks and commodities).
- **When:** when the move is measured, and again at the pre-send re-read on both platforms. A disagreement refuses the post (when
  measured) or holds it (at sending), and is a held figure.
- **No second market (Dow, Nasdaq Composite) or trade.xyz unreadable:** one source, said so ("Market data: Yahoo Finance"). A combined
  index move is checked on every member that has a second market (the S&P 500 always has one).
- The data line names the check: "Market data: Yahoo Finance, matched against Hyperliquid's trade.xyz market, 20:00 UTC".

## 3. One set of market-hours rules (`app/newsdesk/markethours.py`)
- **Freshness:** one limit, 20 minutes, on every path. Data posts allowed 90 minutes before; the picture check 90.
- **Session dates:** crypto dates are UTC; stocks, indices and futures are dated in New York. A finished session is named as it was:
  Friday's record drop read on Saturday says "yesterday" or "on Friday", never "today".
- **Recap:** US indices are measured against the official previous close (it used a rolling 24 h, Telegram 203's mistake).
- **Timestamps:** 52-week highs/lows and round levels are stamped with the price's own time, not the desk's pass.
- **News price tags** ("(BTC -5.4% IN 24H)") are dropped from the headline at sending once they are over an hour old.

## 4. Corrections and retractions (`app/newsdesk/corrections.py`, Admin News Desk > Corrections)
- **Actions:** edit a Telegram post (text or caption), reply under it, delete it; reply under an X post, delete it.
- **Two steps:** preview (sends nothing, issues a 10-minute single-use token), then confirm.
- **Audit:** every attempt is recorded (NdValidation kind "correction"), with the old text, new text, outcome, error, the emergency
  stop state and the X dry-run state.
- **During an emergency stop:** corrections still go out (they are the owner's repairs), recorded as such.
- **Formatting:** Telegram's own tags (`<b> <i> <s> <u> <code> <a href="https://...">`) are sent as formatting, so the 204 edit can strike
  the wrong line through. Anything else is plain text.
- **API:** `POST /api/x/newsdesk/events/{id}/corrections/preview`, `POST .../corrections`, `GET /api/x/newsdesk/corrections`.

## 5. Labels and messages
- The Terminal and classic pages now say the emergency stop halts all automatic Telegram and X posting (they said "ALL X posting").
  The News Desk button reads "Emergency stop (Telegram + X)".
- Owner alerts: "Owner alerts are not being delivered: ... they are kept and sent as one digest once ..." replaces "go nowhere ... only
  logged".
- Admin News Desk has a new "Market-data posts" card: the pause state, why, the held figures, and Resume/Pause buttons with a confirmation.

## Tests
- New: `tests/test_phase1_20261121.py` (21 tests, real data replay), `tests/test_newsdesk_corrections_20261121.py` (21),
  `frontend/src/pages/x/MarketSafety.test.tsx` (2).
- Changed for the new rules: `test_newsdesk_market_data_20261119.py` (freshness 20 min), `test_price_updates_20261121.py` (silver has a
  second market), the alert-wording tests, `TerminalPage.test.tsx`, `VpnAdmin.test.tsx`.
- Full results: see the final report.

## Deploy (owner approval needed)
1. Back up first. `git -C D:/pc-wt/kiwi-hardening merge --ff-only feat/engine-phase1`.
2. Backend: `railway up --service api` from `D:/pc-wt/kiwi-hardening/backend`. Frontend: the Vercel production deploy of `frontend/`,
   as before.
3. Rollback: backend `D:/pc-wt/rollback-3073712` (or a fresh worktree at 62e67c8, the current production commit); frontend: Vercel's
   previous deployment.
4. Market posts stay paused after the deploy. Resume from Admin > X > News Desk > Market-data posts only when the owner decides.
