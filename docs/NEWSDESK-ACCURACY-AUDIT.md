# News Desk accuracy audit (restored production data)

Prepared 2026-11-13 on `feat/newsdesk-viral` after the "stolen Binance $BNB crypto" post (event 275). Scope: every post in the restored
production database: 108 X-lane posts (`nd_posts`, dry runs and repeats included) and 24 delivered Telegram posts
(`nd_telegram_posts`), 2026-11-04 to 2026-11-12 09:28 UTC.

**Limit:** the restored copy ends 2026-11-12 12:02 UTC. The newest posts, including event 275 ("DOJ seizes $1.2B from Binance hack", the
$BNB caption of ~03:36 UTC 2026-11-13), are **not** in it and are not audited here; the main session diagnosed that one (mechanical
cashtags in `clean.cashtag`, fixed in `fix/newsdesk-cashtags`).

## Method

1. Automated: the new claim checker (`app/newsdesk/claims.py`: numbers, tickers via `clean.established()`, named people,
   accusation/legal-action words, causation, quotations, reliable-source rule) on each post's text against that event's stored reports
   (`facts.told` + the `nd_stories` rows it was built from). Script: `audit.py` in the session scratchpad; 109 texts checked, 14 flagged.
2. Manual: every flag and every released event read against its source excerpts and links (below), including misleading associations
   the deterministic checker can't see (a second "source" that is a different story).

## Findings that need action

| # | Post | What went out | Problem | Evidence (source) | Proposed correction |
|---|---|---|---|---|---|
| 1 | Telegram msg for event 46 (TG post 5), X post 55 | "BTCUSD_PERP LONGS LIQUIDATED: $17 MILLION ON BINANCE IN AN HOUR … **$BTCUSD_PERP**" | **Invalid ticker**: a futures contract symbol turned into a cashtag; also a routine $17M flush posted as JUST IN | Exchange liquidation feed (no outlet); `$BTCUSD_PERP` is not a tradable ticker | Delete or edit to drop the cashtag. Now blocked twice: `$BTCUSD_PERP` fails `checks.final_gate`/claims tickers, and flushes under $100M no longer qualify (production rule since 11-10). |
| 2 | Event 215 (TG post 23, X post 107) | "JUST IN: Israeli strike on Nasser Hospital kills at least six, Gaza civil defence agency says. Source: BBC · Al Jazeera" | **Misleading association**: the second source is a different story (an Al Jazeera report on a footballer killed in Gaza), so "BBC · Al Jazeera" overstates corroboration | BBC: https://www.bbc.co.uk/news/articles/ckzd9p7e4eqeo — Al Jazeera: https://www.aljazeera.com/sports/2026/11/11/israeli-army-kills-palestinian-footballer-mahmoud-kamal-abdel-malek-in-gaza | Edit the source line to "Source: BBC". The new editorial bar rejects this story anyway (harm to people, no market tie). In the examples run, gpt-oss merged the two reports ("strike kills footballer and hospital staff"); the new one-report rule (`hooks.merged`) rejects that. |
| 3 | Event 169 (TG post 18, X post 102) | "MIDDLE EAST LATEST: FOUR INDIAN CREW MEMBERS INJURED IN ATTACK ON LIBERIA-FLAGGED OIL TANKER IN GULF" | **Misleading corroboration**: "verified across 2 outlets", but OilPrice's article is about Middle East oil flows recovering, not the attack | The National (live blog): https://www.thenationalnews.com/news/mena/2026/11/11/iran-war-live-shipping-oil-tanker-attack-indians/ — OilPrice: https://oilprice.com/Energy/Crude-Oil/Middle-East-Oil-Flows-Far-from-Normal-Despite-Signs-of-Oversupply.html | No retraction needed (the fact is The National's); correct the attribution to "Source: The National". Grouping by topic words ("oil", "Middle East") should not count as a second outlet: see risks. |
| 4 | Event 105 (TG post 16, X post 100) | "HOT INFLATION, AI SELLOFF & ANTHROPIC IPO — Source: Bloomberg" | **Not a news claim at all**: the title of Bloomberg's "The Close" video episode, posted as if it were a headline; "Anthropic IPO" next to MarketWatch's story that Anthropic *retracted IPO terms sent by mistake* reads as an IPO happening | Bloomberg video: https://www.bloomberg.com/news/videos/2026-11-10/the-close-11-10-2026-video — MarketWatch: https://www.marketwatch.com/story/anthropic-reportedly-retracts-ipo-terms-sent-to-banks-by-mistake-exposing-jitters-over-ai-investments-6116578d | Delete. Now rejected: round-up title (−10) and editorial score 16 < 45. |
| 5 | Event 216 (TG post 24, X post 108) | "Russian diesel exports double after Trump lifts sanctions, but US gets none." | **Overstatement in the outlet's own headline**: the other source describes "President Donald Trump's temporary waiver of some sanctions", not lifted sanctions | FT: https://www.ft.com/content/525b53ee-4817-4eb6-8292-b6ee0ac45b47 — gCaptain: https://gcaptain.com/russian-diesel-faces-u-s-buyer-resistance-despite-trump-sanctions-waiver/ | Optional edit: "…after Trump's temporary sanctions waiver, but US gets none". The FT is the source of the wording, so this is attribution-accurate; flagged because the two sources disagree on the key fact. |
| 6 | Event 63 (TG post 8, X post 58) | "NORTH KOREAN HACKERS ARE CREATING MEMECOINS TO WASH LOOT FROM $6.3B BINANCE HACK — Source: CoinDesk" | **Attribution dropped**: the attribution to North Korea is Elliptic's analysis ("…from the $6.3 billion Binance hack, Elliptic says"), stated as fact in the headline | CoinDesk: https://www.coindesk.com/business/2026/11/09/north-korean-hackers-are-creating-memecoins-to-wash-loot-from-usd6-3-billion-binance-hack | No retraction (it is CoinDesk's headline); future posts: the claim checker requires attribution for accusations/legal actions, and the caption names the sources. Suggested wording: "Elliptic: North Korean hackers mint memecoins to wash Binance hack loot". |
| 7 | Event 1 (TG post 2, X post 52) | "🔴 ALERT: BINANCE'S $6.3B HACK LEAVES QUESTIONS OVER HOT WALLET SAFEGUARDS — Source: Cointelegraph" | **Single-outlet claim with an ALERT label**: evidence "attributed", one outlet, for a $6.3B hack claim | Cointelegraph: https://cointelegraph.com/news/binances-hack-questions-hot-wallet-risk-controls | No retraction: later corroborated (CoinDesk, Elliptic; events 63/69). Future: a single non-major outlet can't carry a crime claim alone (`claims.reliable_problems`). |

## Checked and not wrong

- **Tickers**: apart from `$BTCUSD_PERP`, every cashtag in the restored posts is supported (`$BTC` on BTC liquidation posts; recap tickers
  `$HYPE`/`$SOL` are measured Hyperliquid prices of those tokens; the checker's flag there was a false positive because recap source text did
  not list the measured assets, which a7d2081 fixes in `recap.py`). No `$BNB`, `$COIN`, `$HYPE`-for-Hyperliquid or `$NVDA`-for-Nvidia errors
  exist in this window: the clean style with cashtags went live 2026-11-12 01:11 UTC, and only two posts in this copy came after it
  (Telegram 23 and 24), neither naming a listed company or exchange.
- **Event 133** (Nvidia/Wachtell, NYT "Affair With Chinese Executive"): an accusation-adjacent story about an identifiable person, but from the
  NYT with BBC, Euronews and others corroborating; the caption names the NYT. Acceptable; other outlets say "alleged Chinese spy", and the new
  hook rules keep "alleged" when a writer rewords it.
- **Event 177** "$2.7M" vs Decrypt's "£2 million": a currency conversion by The Block, both in the sources. Fine.
- **Event 182** Saudi output +26%: self-reported to OPEC, and the headline says "as Riyadh Reports". Fine.
- **Event 180** Deutsche Bank custody: "Bank Says" attributed; Unchained notes it rests on a regulator notification, not a new licence. Fine.
- **Numbers flagged on legacy market posts (Nov 4-5)**: measured Yahoo/Hyperliquid prices whose source data wasn't stored with the post: not
  checkable after the fact, not evidence of error. Numbers flagged in Telegram captions ("2.5", "5", "3") were the photo licence (CC BY 2.5)
  and "Verified across N outlets": not claims (`checks.story_of` now excludes those lines).
- **WTI posts of 2026-11-10** (events 78/82/84/90, each delivered once on Telegram but queued up to 8 times on X): figures correct, but
  four overlapping 24-hour windows told as four stories and "GETS HAMMERED" for a 5-6% move. Already stopped in production (11-10 hard block,
  recap fallback off); the review counts 34 duplicate X rows from these.

## What now stops each class of error

| Error class | Gate |
|---|---|
| Company/exchange name → ticker ($BNB for Binance, $COIN for a Coinbase outage) | `clean.established()` (fix/newsdesk-cashtags), `checks.final_gate`, `claims.ticker_problems` on hooks, questions, Admin picks and both final captions |
| Invented figure, person, accusation, legal action, quote | `checks.final_gate` claim_problems (1374981) + `claims.check` + `hooks.fact_check` |
| Cause not in the sources | `claims` / `hooks` causal-verb rule and the "as/after" clause rule |
| Two reports stitched into one claim | `hooks.merged` (one report must carry the hook, at most two words from elsewhere) |
| Accusation about a person on thin sourcing | `scorecard.safety` (official or two outlets) and `claims.reliable_problems` (court/regulator, two outlets or a major outlet, plus attribution) |
| Non-stories (episode titles, round-ups, live blogs, talk) | editorial scorecard: round-up −10, soft geopolitics −6, minimum 45 |

Any failure holds the post (no fallback caption), and the reason is stored in `facts["accuracy"]` and shown in Admin › News Desk ›
Editorial scores, whatever the story's virality or editorial score.
