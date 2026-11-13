# News Desk media engine: publish-vs-reject review

Generated 2026-11-13 06:28 UTC on `feat/newsdesk-viral`. Input: **every one of the 108 posts** in `nd_posts` of the restored production database (2026-11-04 to 2026-11-12 09:28 UTC; the desk's record of everything it posted or would have posted on X, dry runs and repeats included; Telegram carried the same released events). The restored copy ends 2026-11-12 12:02 UTC, so later posts (including event 275, the $BNB caption) are not in it. The replay is deterministic (no model): the new editorial scorecard and bar (45), the factual-safety rule, the claim checker on the post text, the market rules already in production (exceptional move for its class, $100M liquidation flush, 24 h same-asset block) and story-level dedup, in time order. The 20 worked examples, with hooks, pictures and score breakdowns, are in [NEWSDESK-VIRAL-EXAMPLES.md](NEWSDESK-VIRAL-EXAMPLES.md); the accuracy audit is in [NEWSDESK-ACCURACY-AUDIT.md](NEWSDESK-ACCURACY-AUDIT.md).

## Summary

- **Would publish: 13. Would reject: 95.**
  - duplicate post of the same event: 34
  - editorial score under 45: 31
  - routine price move / price alert: 29
  - same story already selected (story-level dedup): 1
- **Duplicate clusters collapsed** (first post kept or rejected on its own merits, every later copy rejected):
  - event 74: 7 repeat(s) — "MERCADO BITCOIN BEGINS POST-HACK REOPENING WITHOUT A TIMELINE FOR WITHDRAWALS"
  - event 78: 7 repeat(s) — "OIL (WTI) SLIDES — DOWN 4.4% IN 24 HOURS"
  - event 82: 7 repeat(s) — "OIL (WTI) GETS HAMMERED — DOWN 6.0% IN 24 HOURS"
  - event 84: 7 repeat(s) — "OIL (WTI) GETS HAMMERED — DOWN 5.1% IN 24 HOURS"
  - event 90: 6 repeat(s) — "OIL (WTI) SLIDES — DOWN 3.3% IN 24 HOURS"
  - market OIL: 1 repeat(s) — "OIL (WTI) GETS HAMMERED — DOWN 5.1% IN 24 HOURS"
- Of the news stories that pass, the beats are: Exchanges 3, Scandal 2, Billionaires 1, Geopolitics (market tie) 1, Government decision 1.

## Every post

| Post | When (UTC) | Then | Original headline | New decision | Editorial | Main reason |
|---|---|---|---|---|---|---|
| 1 | 2026-11-04 10:53 | dry_run | SILVER Up 6.3% in 24 hours, now $52.05. | PUBLISH | — | SILVER 6.3% (commodity): exceptional for its class |
| 3 | 2026-11-04 10:53 | expired | Binance says fake job interviews used as part of $6.3bn crypto hack | REJECT | 43 | editorial score 43 under the minimum of 45 (weakest: surprise 0, controversy 0) |
| 4 | 2026-11-04 10:53 | expired | Bitcoin bounces from 7-week low under $76K as Democrats sweep Congress | REJECT | 16 | editorial score 16 under the minimum of 45 (weakest: surprise 0, controversy 0) |
| 5 | 2026-11-04 10:53 | expired | MARKETS, 24 HOURS Bitcoin: +1.9% | REJECT | — | a 24-hour market table: routine moves, not a story |
| 7 | 2026-11-04 11:44 | expired | MARKETS, 24 HOURS Bitcoin: +2.0% | REJECT | — | a 24-hour market table: routine moves, not a story |
| 9 | 2026-11-04 12:29 | expired | MARKETS, 24 HOURS Bitcoin: +1.1% | REJECT | — | a 24-hour market table: routine moves, not a story |
| 8 | 2026-11-04 12:42 | dry_run | BTCUSD_PERP longs liquidated: $3 million on Binance in an hour. | REJECT | — | liquidations $3M: an hour's flush needs $100M+ to be news |
| 10 | 2026-11-04 13:15 | expired | MARKETS, 24 HOURS Bitcoin: +1.2% | REJECT | — | a 24-hour market table: routine moves, not a story |
| 11 | 2026-11-04 14:05 | expired | MARKETS, 24 HOURS Bitcoin: +1.4% | REJECT | — | a 24-hour market table: routine moves, not a story |
| 2 | 2026-11-04 14:31 | dry_run | Treasury yields fall as investors crowd into bonds for safety amid election stock sell-off | REJECT | 14 | editorial score 14 under the minimum of 45 (weakest: surprise 0, controversy 0) |
| 13 | 2026-11-04 14:56 | expired | MARKETS, 24 HOURS Bitcoin: +2.5% | REJECT | — | a 24-hour market table: routine moves, not a story |
| 15 | 2026-11-04 15:08 | dry_run | Ethereum up 4.9% in an hour, now $2,428. | REJECT | — | ETH 4.9% (crypto_major): routine for its class (under its 24h alert line); a one-hour move, not a 24-hour one |
| 14 | 2026-11-04 15:08 | expired | BITCOIN Now back above $80,000, at $80,281. | REJECT | — | a level or 52-week mark with no exceptional move: a price alert, not news |
| 16 | 2026-11-04 15:46 | expired | MARKETS, 24 HOURS Bitcoin: +3.5% | REJECT | — | a 24-hour market table: routine moves, not a story |
| 6 | 2026-11-04 16:21 | dry_run | Justin Sun seeks $264K sanctions over World Liberty’s UAE disclosure | PUBLISH | 46 | Billionaires; surprise 0, impact 5, controversy 5, relevance 12, discussion 19 |
| 18 | 2026-11-04 16:37 | expired | MARKETS, 24 HOURS Bitcoin: +4.0% | REJECT | — | a 24-hour market table: routine moves, not a story |
| 19 | 2026-11-04 16:49 | dry_run | Oil (WTI) down 3.9% in an hour, now $90.19. | REJECT | — | OIL 3.9% (commodity): routine for its class (under its 24h alert line); a one-hour move, not a 24-hour one |
| 20 | 2026-11-04 17:15 | expired | APPLE A new 52-week high: $354.39. | REJECT | — | a level or 52-week mark with no exceptional move: a price alert, not news |
| 21 | 2026-11-04 17:22 | expired | MARKETS, 24 HOURS Bitcoin: +3.5% | REJECT | — | a 24-hour market table: routine moves, not a story |
| 23 | 2026-11-04 18:09 | expired | MARKETS, 24 HOURS Bitcoin: +3.9% | REJECT | — | a 24-hour market table: routine moves, not a story |
| 22 | 2026-11-04 18:15 | dry_run | APPLE A new 52-week high: $354.43. | REJECT | — | a level or 52-week mark with no exceptional move: a price alert, not news |
| 24 | 2026-11-04 18:57 | expired | MARKETS, 24 HOURS Bitcoin: +3.5% | REJECT | — | a 24-hour market table: routine moves, not a story |
| 26 | 2026-11-04 19:51 | expired | BITCOIN Now below $80,000, at $79,914. | REJECT | — | a level or 52-week mark with no exceptional move: a price alert, not news |
| 25 | 2026-11-04 20:03 | dry_run | MARKETS, 24 HOURS Bitcoin: +3.3% | REJECT | — | a 24-hour market table: routine moves, not a story |
| 27 | 2026-11-04 20:56 | expired | BITCOIN Now below $80,000, at $79,970. | REJECT | — | a level or 52-week mark with no exceptional move: a price alert, not news |
| 17 | 2026-11-04 21:52 | dry_run | Ice hockey federation reiterates opposition to reinstatement of Russia | REJECT | 0 | editorial score 0 under the minimum of 45 (weakest: surprise 0, impact 0) |
| 30 | 2026-11-04 22:59 | expired | Bitcoin rebounds past $80,000 as markets shrug off midterms and Binance hack | REJECT | 17 | editorial score 17 under the minimum of 45 (weakest: surprise 0, controversy 0) |
| 12 | 2026-11-04 23:45 | dry_run | Stocks making the biggest moves premarket: Trump Media, Tesla, CoreWeave, Novo Nordisk and more | REJECT | 5 | editorial score 5 under the minimum of 45 (weakest: controversy 0, discussion 0) |
| 32 | 2026-11-05 00:44 | expired | GOLD Now back above $4,100, at $4,100. | REJECT | — | a level or 52-week mark with no exceptional move: a price alert, not news |
| 29 | 2026-11-05 01:36 | dry_run | BitMart founder says security team ‘severely understaffed’ after new hack | REJECT | 0 | editorial score 0 under the minimum of 45 (weakest: surprise 0, impact 0) |
| 33 | 2026-11-05 03:25 | dry_run | Interest rates expected to rise for first time in over three years | REJECT | 23 | editorial score 23 under the minimum of 45 (weakest: controversy 0, discussion 0) |
| 31 | 2026-11-05 05:14 | dry_run | US midterm results a 'victory' for Canadians hopeful of tariff relief | REJECT | 23 | editorial score 23 under the minimum of 45 (weakest: surprise 0, controversy 0) |
| 28 | 2026-11-05 07:06 | dry_run | Coinbase Says 99% of Customer Crypto Is Offline and Out of Hackers’ Reach | PUBLISH | 46 | Exchanges; surprise 7, impact 11, controversy 0, relevance 20, discussion 4 |
| 34 | 2026-11-05 10:57 | dry_run | Isabel Schnabel: Navigating stormy waters: reflections on monetary policy | REJECT | 0 | editorial score 0 under the minimum of 45 (weakest: surprise 0, impact 0) |
| 38 | 2026-11-05 12:35 | expired | BITCOIN Now below $80,000, at $79,841. | REJECT | — | a level or 52-week mark with no exceptional move: a price alert, not news |
| 37 | 2026-11-05 12:49 | dry_run | Three ways the interest rate rise could affect you | REJECT | 10 | editorial score 10 under the minimum of 45 (weakest: surprise 0, controversy 0) |
| 39 | 2026-11-05 13:03 | expired | Stablecoins Go Mainstream as Samsung Wallet Adds USDC on Solana | REJECT | 26 | editorial score 26 under the minimum of 45 (weakest: surprise 0, controversy 0) |
| 36 | 2026-11-05 14:40 | dry_run | US sanctions Indian and Turkish firms accused of helping Iran | REJECT | 34 | editorial score 34 under the minimum of 45 (weakest: surprise 0, impact 5) |
| 42 | 2026-11-05 15:27 | dry_run | Broad US selloff: S&P 500 -2.7% · Nasdaq -5.0% in 24 hours. | PUBLISH | — | IXIC,SPX 5% (index): exceptional for its class |
| 43 | 2026-11-05 16:33 | dry_run | GOLD Now below $4,100, at $4,099. | REJECT | — | a level or 52-week mark with no exceptional move: a price alert, not news |
| 35 | 2026-11-05 18:21 | dry_run | Bitcoin, Ether ETFs draw $128M as crypto rebounds after midterms | REJECT | 29 | editorial score 29 under the minimum of 45 (weakest: surprise 0, controversy 0) |
| 44 | 2026-11-05 20:15 | dry_run | Paraguay President Pena resigns in wake of bank collapse | REJECT | 21 | editorial score 21 under the minimum of 45 (weakest: surprise 0, impact 0) |
| 40 | 2026-11-05 22:11 | dry_run | Four people, including a child, killed in Israeli attacks on Gaza | REJECT | 0 | editorial score 0 under the minimum of 45 (weakest: surprise 0, impact 0) |
| 41 | 2026-11-06 00:02 | dry_run | Barkin sees interest rate hikes ahead, but at a gradual pace | REJECT | 28 | editorial score 28 under the minimum of 45 (weakest: surprise 4, discussion 4) |
| 47 | 2026-11-06 00:07 | expired | Asian crypto exchanges bunker down as hacks surge: Asia Express | REJECT | 16 | editorial score 16 under the minimum of 45 (weakest: surprise 0, controversy 0) |
| 48 | 2026-11-06 01:55 | dry_run | Fears about an AI crackdown after midterms sparks a $2 trillion rout in tech stocks | REJECT | 7 | editorial score 7 under the minimum of 45 (weakest: surprise 0, impact 0) |
| 45 | 2026-11-06 03:49 | dry_run | Bitcoin erases post-election gains as Wall Street selloff triggers over $1 billion in crypto liquida | REJECT | 32 | editorial score 32 under the minimum of 45 (weakest: surprise 0, controversy 0) |
| 50 | 2026-11-06 03:56 | expired | Bitcoin Erases Election Rally as AI Stocks Plunge, $428 Million in Crypto Longs Get Wiped Out | REJECT | 29 | editorial score 29 under the minimum of 45 (weakest: surprise 0, controversy 0) |
| 46 | 2026-11-06 05:43 | dry_run | Melania Trump hosts Russia-Ukraine meeting after Oleshky ceasefire | REJECT | 0 | editorial score 0 under the minimum of 45 (weakest: surprise 0, impact 0) |
| 51 | 2026-11-06 07:34 | dry_run | First-time homebuyers remain scarce, but a record share of them pay all cash | REJECT | 19 | editorial score 19 under the minimum of 45 (weakest: impact 0, relevance 0) |
| 52 | 2026-11-06 10:53 | dry_run | BINANCE’S $6.3B HACK LEAVES QUESTIONS OVER HOT WALLET SAFEGUARDS | PUBLISH | 59 | Exchanges; surprise 11, impact 19, controversy 0, relevance 19.2, discussion 6 |
| 49 | 2026-11-06 12:52 | dry_run | Fed, OCC check US banks after Binance hack as experts flag security risks | REJECT | 29 | editorial score 29 under the minimum of 45 (weakest: surprise 0, controversy 0) |
| 53 | 2026-11-06 14:30 | dry_run | S&P 500 FALLS 3.6% IN 24 HOURS TO 7,373 | PUBLISH | — | SPX 3.6% (index): exceptional for its class |
| 54 | 2026-11-08 21:51 | dry_run | BTC LONGS LIQUIDATED: $8 MILLION ON BINANCE + BYBIT + OKX IN AN HOUR | REJECT | — | liquidations $8M: an hour's flush needs $100M+ to be news |
| 55 | 2026-11-09 05:09 | dry_run | BTCUSD_PERP LONGS LIQUIDATED: $17 MILLION ON BINANCE IN AN HOUR | REJECT | — | liquidations $17M: an hour's flush needs $100M+ to be news |
| 56 | 2026-11-09 08:00 | dry_run | Crypto slides, Ethereum down 6.5% in 24 hours. | REJECT | — | ETH 6.5% (crypto_major): routine for its class (under its 24h alert line) |
| 57 | 2026-11-09 12:04 | dry_run | Crypto slides, Ethereum down 5.6% in 24 hours. | REJECT | — | ETH 5.6% (crypto_major): routine for its class (under its 24h alert line) |
| 58 | 2026-11-09 16:04 | dry_run | NORTH KOREAN HACKERS ARE CREATING MEMECOINS TO WASH LOOT FROM $6.3B BINANCE HACK | PUBLISH | 68 | Exchanges; surprise 16, impact 19, controversy 0, relevance 19.2, discussion 10 |
| 59 | 2026-11-09 20:06 | uncertain | MERCADO BITCOIN BEGINS POST-HACK REOPENING WITHOUT A TIMELINE FOR WITHDRAWALS | REJECT | 27 | editorial score 27 under the minimum of 45 (weakest: surprise 0, controversy 0) |
| 60 | 2026-11-09 20:11 | uncertain | MERCADO BITCOIN BEGINS POST-HACK REOPENING WITHOUT A TIMELINE FOR WITHDRAWALS | REJECT | — | duplicate post of the same event #74 (first as post 59) |
| 61 | 2026-11-09 20:22 | uncertain | MERCADO BITCOIN BEGINS POST-HACK REOPENING WITHOUT A TIMELINE FOR WITHDRAWALS | REJECT | — | duplicate post of the same event #74 (first as post 59) |
| 62 | 2026-11-09 20:42 | uncertain | MERCADO BITCOIN BEGINS POST-HACK REOPENING WITHOUT A TIMELINE FOR WITHDRAWALS | REJECT | — | duplicate post of the same event #74 (first as post 59) |
| 63 | 2026-11-09 21:12 | uncertain | MERCADO BITCOIN BEGINS POST-HACK REOPENING WITHOUT A TIMELINE FOR WITHDRAWALS | REJECT | — | duplicate post of the same event #74 (first as post 59) |
| 64 | 2026-11-09 21:42 | uncertain | MERCADO BITCOIN BEGINS POST-HACK REOPENING WITHOUT A TIMELINE FOR WITHDRAWALS | REJECT | — | duplicate post of the same event #74 (first as post 59) |
| 65 | 2026-11-09 22:13 | uncertain | MERCADO BITCOIN BEGINS POST-HACK REOPENING WITHOUT A TIMELINE FOR WITHDRAWALS | REJECT | — | duplicate post of the same event #74 (first as post 59) |
| 66 | 2026-11-09 22:43 | uncertain | MERCADO BITCOIN BEGINS POST-HACK REOPENING WITHOUT A TIMELINE FOR WITHDRAWALS | REJECT | — | duplicate post of the same event #74 (first as post 59) |
| 67 | 2026-11-10 00:06 | uncertain | OIL (WTI) SLIDES — DOWN 4.4% IN 24 HOURS | REJECT | — | OIL 4.4% (commodity): routine for its class (under its 24h alert line) |
| 68 | 2026-11-10 00:11 | uncertain | OIL (WTI) SLIDES — DOWN 4.4% IN 24 HOURS | REJECT | — | duplicate post of the same event #78 (first as post 67) |
| 69 | 2026-11-10 00:21 | uncertain | OIL (WTI) SLIDES — DOWN 4.4% IN 24 HOURS | REJECT | — | duplicate post of the same event #78 (first as post 67) |
| 70 | 2026-11-10 00:41 | uncertain | OIL (WTI) SLIDES — DOWN 4.4% IN 24 HOURS | REJECT | — | duplicate post of the same event #78 (first as post 67) |
| 71 | 2026-11-10 01:12 | uncertain | OIL (WTI) SLIDES — DOWN 4.4% IN 24 HOURS | REJECT | — | duplicate post of the same event #78 (first as post 67) |
| 72 | 2026-11-10 01:42 | uncertain | OIL (WTI) SLIDES — DOWN 4.4% IN 24 HOURS | REJECT | — | duplicate post of the same event #78 (first as post 67) |
| 73 | 2026-11-10 02:12 | uncertain | OIL (WTI) SLIDES — DOWN 4.4% IN 24 HOURS | REJECT | — | duplicate post of the same event #78 (first as post 67) |
| 74 | 2026-11-10 02:42 | uncertain | OIL (WTI) SLIDES — DOWN 4.4% IN 24 HOURS | REJECT | — | duplicate post of the same event #78 (first as post 67) |
| 75 | 2026-11-10 04:09 | uncertain | OIL (WTI) GETS HAMMERED — DOWN 6.0% IN 24 HOURS | PUBLISH | — | OIL 6% (commodity): exceptional for its class |
| 76 | 2026-11-10 04:15 | uncertain | OIL (WTI) GETS HAMMERED — DOWN 6.0% IN 24 HOURS | REJECT | — | duplicate post of the same event #82 (first as post 75) |
| 77 | 2026-11-10 04:25 | uncertain | OIL (WTI) GETS HAMMERED — DOWN 6.0% IN 24 HOURS | REJECT | — | duplicate post of the same event #82 (first as post 75) |
| 78 | 2026-11-10 04:45 | uncertain | OIL (WTI) GETS HAMMERED — DOWN 6.0% IN 24 HOURS | REJECT | — | duplicate post of the same event #82 (first as post 75) |
| 79 | 2026-11-10 05:15 | uncertain | OIL (WTI) GETS HAMMERED — DOWN 6.0% IN 24 HOURS | REJECT | — | duplicate post of the same event #82 (first as post 75) |
| 80 | 2026-11-10 05:46 | uncertain | OIL (WTI) GETS HAMMERED — DOWN 6.0% IN 24 HOURS | REJECT | — | duplicate post of the same event #82 (first as post 75) |
| 81 | 2026-11-10 06:16 | uncertain | OIL (WTI) GETS HAMMERED — DOWN 6.0% IN 24 HOURS | REJECT | — | duplicate post of the same event #82 (first as post 75) |
| 82 | 2026-11-10 06:46 | uncertain | OIL (WTI) GETS HAMMERED — DOWN 6.0% IN 24 HOURS | REJECT | — | duplicate post of the same event #82 (first as post 75) |
| 83 | 2026-11-10 08:08 | uncertain | OIL (WTI) GETS HAMMERED — DOWN 5.1% IN 24 HOURS | REJECT | — | same asset (OIL) already posted within 24 h: hard block |
| 84 | 2026-11-10 08:13 | uncertain | OIL (WTI) GETS HAMMERED — DOWN 5.1% IN 24 HOURS | REJECT | — | duplicate post of the same event #84 (first as post 83) |
| 85 | 2026-11-10 08:23 | uncertain | OIL (WTI) GETS HAMMERED — DOWN 5.1% IN 24 HOURS | REJECT | — | duplicate post of the same event #84 (first as post 83) |
| 86 | 2026-11-10 08:44 | uncertain | OIL (WTI) GETS HAMMERED — DOWN 5.1% IN 24 HOURS | REJECT | — | duplicate post of the same event #84 (first as post 83) |
| 87 | 2026-11-10 09:14 | uncertain | OIL (WTI) GETS HAMMERED — DOWN 5.1% IN 24 HOURS | REJECT | — | duplicate post of the same event #84 (first as post 83) |
| 88 | 2026-11-10 09:44 | uncertain | OIL (WTI) GETS HAMMERED — DOWN 5.1% IN 24 HOURS | REJECT | — | duplicate post of the same event #84 (first as post 83) |
| 89 | 2026-11-10 10:14 | uncertain | OIL (WTI) GETS HAMMERED — DOWN 5.1% IN 24 HOURS | REJECT | — | duplicate post of the same event #84 (first as post 83) |
| 90 | 2026-11-10 10:44 | uncertain | OIL (WTI) GETS HAMMERED — DOWN 5.1% IN 24 HOURS | REJECT | — | duplicate post of the same event #84 (first as post 83) |
| 91 | 2026-11-10 12:07 | uncertain | OIL (WTI) SLIDES — DOWN 3.3% IN 24 HOURS | REJECT | — | OIL 3.3% (commodity): routine for its class (under its 24h alert line) |
| 92 | 2026-11-10 12:12 | uncertain | OIL (WTI) SLIDES — DOWN 3.3% IN 24 HOURS | REJECT | — | duplicate post of the same event #90 (first as post 91) |
| 93 | 2026-11-10 12:22 | uncertain | OIL (WTI) SLIDES — DOWN 3.3% IN 24 HOURS | REJECT | — | duplicate post of the same event #90 (first as post 91) |
| 94 | 2026-11-10 12:42 | uncertain | OIL (WTI) SLIDES — DOWN 3.3% IN 24 HOURS | REJECT | — | duplicate post of the same event #90 (first as post 91) |
| 95 | 2026-11-10 13:12 | uncertain | OIL (WTI) SLIDES — DOWN 3.3% IN 24 HOURS | REJECT | — | duplicate post of the same event #90 (first as post 91) |
| 96 | 2026-11-10 13:42 | uncertain | OIL (WTI) SLIDES — DOWN 3.3% IN 24 HOURS | REJECT | — | duplicate post of the same event #90 (first as post 91) |
| 97 | 2026-11-10 14:13 | uncertain | OIL (WTI) SLIDES — DOWN 3.3% IN 24 HOURS | REJECT | — | duplicate post of the same event #90 (first as post 91) |
| 98 | 2026-11-10 16:06 | published | UKRAINE PEACE TALKS FACE AN UPHILL BATTLE AS WINTER APPROACHES | REJECT | 0 | editorial score 0 under the minimum of 45 (weakest: surprise 0, impact 0) |
| 99 | 2026-11-10 20:06 | published | NVIDIA HIRES WACHTELL LIPTON TO INVESTIGATE C.E.O.’S AFFAIR WITH CHINESE EXECUTIVE | PUBLISH | 51 | Scandal; surprise 6, impact 4, controversy 18, relevance 8.4, discussion 10 |
| 100 | 2026-11-11 00:07 | published | HOT INFLATION, AI SELLOFF & ANTHROPIC IPO | REJECT | 16 | editorial score 16 under the minimum of 45 (weakest: surprise 0, controversy 0) |
| 101 | 2026-11-11 04:06 | published | IRAN OPENS TRAINING CAMP FOR WOMEN IN READINESS FOR WAR | REJECT | 0 | editorial score 0 under the minimum of 45 (weakest: surprise 0, impact 0) |
| 102 | 2026-11-11 07:04 | published | MIDDLE EAST LATEST: FOUR INDIAN CREW MEMBERS INJURED IN ATTACK ON LIBERIA-FLAGGED OIL TANKER IN GULF | REJECT | 37 | editorial score 37 under the minimum of 45 (weakest: controversy 0, discussion 4) |
| 103 | 2026-11-11 12:48 | published | Crypto fraudster jailed for eight years over $2.7M theft plot directed from prison | PUBLISH | 58 | Scandal; surprise 5, impact 5, controversy 15, relevance 18, discussion 10 |
| 104 | 2026-11-11 16:06 | published | Saudi Crude Output Jumps 26% as Riyadh Reports Near Pre-War Production | PUBLISH | 59 | Geopolitics (market tie); surprise 15, impact 17, controversy 0, relevance 14.4, discussion 11 |
| 105 | 2026-11-11 20:06 | published | Deutsche Bank’s Bitcoin and Ether Custody Is Open to Clients, Bank Says | REJECT | 28 | editorial score 28 under the minimum of 45 (weakest: surprise 0, controversy 0) |
| 106 | 2026-11-12 00:00 | published | Tesla vs Nvidia over the past 30 days: +3.4% against −23.2% | PUBLISH | — | data post (measured comparison, judged by its own bar): OTHER 23.2% (stock): exceptional for its class |
| 107 | 2026-11-12 04:27 | published | Israeli strike on Nasser Hospital kills at least six, Gaza civil defence agency says. | REJECT | 0 | editorial score 0 under the minimum of 45 (weakest: surprise 0, impact 0) |
| 108 | 2026-11-12 09:28 | published | Russian diesel exports double after Trump lifts sanctions, but US gets none. | PUBLISH | 61 | Government decision; surprise 13, impact 5, controversy 13, relevance 9.4, discussion 17 |
