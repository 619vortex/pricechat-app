# News Desk: the wire style

Branch `feat/newsdesk-wire`, based on production `1374981` (the final-output claim gate). `feat/newsdesk-viral` is not included.
**Not deployed.**

The owner asked (2026-11-14) for "a cleaner, faster Watcher Guru news style": short factual headlines, earned tags, text-only posts allowed,
real charts for big moves, surprising stories first, quick breaking posts under a limit, and every accuracy gate kept. The first version
(0ff38ec, 1d0ddce) was reviewed and sent back with seven requirements. This document describes the revised version.

**Clean stays the default. Wire is opt-in.** Admin › X › News Desk › Price Chat News has a three-way switch (Wire / Clean / Classic).
Deploying this branch changes nothing in what Clean posts (a byte-for-byte test against 1374981's own output proves it). Wire goes live only
when the owner presses "Use wire" (the same as `PUT /x/newsdesk/network {"post_style": "wire"}`).

One change applies to every style: **sources are grouped into one story only on same-event evidence** (requirement 2). It fixes a
correctness bug (post 166). It is conservative: a source that isn't shown to be the same event is simply not attached.

Code: `backend/app/newsdesk/wire.py` (new on this branch) and `checks.py` (same-event and qualifier rules). Smaller changes in `network.py`,
`events.py`, `editorial.py`, `engine.py`, `telegram.py`, `visual.py`, `queue.py`, `api/routes/newsdesk.py` and
`frontend/src/pages/x/NetworkSwitches.tsx`.

## What changed in the revision (the seven requirements)

1. **Headline casing and qualifiers.**
   - `wire.headline_case` was rewritten. A Title Case outlet headline is put in sentence case. A word keeps its capital when:
     - it is written like a name (USDPT, SpaceX, U.S.);
     - it is a country, capital, known leader, asset name or a word that is always capitalised;
     - it belongs to a name the reporting writes the same way ("Deutsche Bank", "Anchorage Digital");
     - or the reporting writes it capitalised on its own mid-sentence ("Visa").

     An ordinary word never keeps its capital just because the reporting capitalises it inside some name. Post 164 now reads "..., bank
     says", where it read "Bank says". The reporting also includes the outlets' own sentence-case headlines (`wire.case_source`).
   - Qualifiers: `checks.restore_qualifiers` puts back a qualifier the sources attach to a figure. The qualifiers are "at least", "up to",
     "about", "around", "nearly", "more than", "almost", "an estimated", "as many as" and similar. Only the sources' own words are added, and
     only when the figure counts the same thing (it is followed by the same noun, or by nothing).
   - "reportedly" and "allegedly" are hedges on the whole claim, so they are never moved in front of a figure. Their loss is held instead.
   - `checks.qualifier_problems` is a new wire final-gate check. A figure stated as exact that the sources qualify is held. Example 4 now
     says "at least six".
2. **Same-event grouping** (`checks.same_event`). Two headlines are one event when they share:
   - two distinctive names;
   - one distinctive name plus words in common beyond that name;
   - one distinctive name plus the same amount ("$1.2B" and "$1.2 billion");
   - the same asset through the same price level in the same direction ("Bitcoin breaks below $69,000" / "tumbles below $69,000";
     $70K = $70,000);
   - or most of their words, with no conflicting figures.

   Countries, capitals, nationalities and Title Case ordinary words are never evidence. Different subjects, different places, different
   levels or different figures veto a match.

   It is applied where the desk groups sources:
   - `editorial.corroborating` (the importance path). Post 166 was merged here: "Israeli" and "Gaza" both counted as the country ISRAEL.
   - `engine.corroboration` (the chaos filter), plus a 48-hour window between tellings.
   - `events._same` (later tellings merged into an event).
   - `network.links_for` / `related` (which links a post shows).
3. **Clean is the default** (`network.DEFAULT_STYLE = "clean"`). Wire is opt-in from Admin. The Admin hint says "Default:" for Clean and
   "Opt-in:" for Wire. An unknown style shows as Clean.
4. **Engine-path tests.** These go through the real Telegram lane (`telegram.tick` → `compose_visual` → `network.package` → the final gate →
   `send_photo`/`send_message`):
   - JUST IN (the exact caption);
   - UPDATE as a reply (`reply_to == 170`, the parent's `tg_message_id`);
   - BREAKING for a measured move, with a chart verified by `visual.verify_series` on the fixture series (-6.2% over 24 h);
   - a news price story with real Yahoo response parsing: a chart when the candles break $70,000, text only when they don't;
   - text only;
   - BREAKING for news through the whole desk (the existing e2e test).

   Clean: byte-for-byte against 1374981 on five real stories (`tests/newsdesk_clean_golden_1374981.json`, written by 1374981's own code),
   and the lane posts Clean when no style is chosen.
5. **Selection** (`wire.filler`, `wire.surprise_bonus`).
   - Always filler: price predictions and price targets ("could reach $150,000", "set to explode"), previews and "what to expect",
     explainers, listicles ("5 altcoins to watch"), opinion, interviews and guides.
   - Routine earnings with no surprise (no beat or miss, no guidance, no big move) are filler.
   - Speculation ("could", "set to", "weighs", "analysts say", a partnership) is filler **only when it is in the main clause and the headline
     has no figure and no concrete decision of its own**. These now pass:
     - "Fed set to cut rates by 25 basis points after inflation cools"
     - "Binance could face $4 billion fine as DOJ files charges"
     - "Oil jumps 5% as traders say supply could tighten"
     - "Trump says he could fire Powell, markets slide"
   - The surprise bonus also counts probes, fines, jailings, missile strikes, tariffs, outages and big moves.
6. **Real pictures when they add something** (`wire.picture`), in this order:
   - the data card;
   - the measured move's verified chart;
   - the real chart of a price story. This now includes an index's own move ("S&P 500 falls 3.2%"). The candles must show what the headline
     says (`wire.chart_matches`: direction, at least half the stated %, the level actually broken, a record near the day's high). Otherwise
     there is no chart.
   - a licensed photo of the public figure who leads the headline. A social post, screenshot, poster or quote card is never used
     (`plain_photo`, added after the real Commons search returned a tweet graphic for "Donald Trump").
   - a licensed photo of **the named site** of a major incident or conflict (`site_subject`: "Nasser Hospital", "Ras Tanura terminal"). The
     photo must name that exact site, not a longer name ending in it: "Gamal Abdel Nasser Hospital" is in Egypt, and the real Commons search
     returned it. It must also name a place the story names. No people are shown on a story of violence.
   - text only.

   There are still no AI images and no skylines or stand-ins. ETF-flow charts are not drawn, because the desk has no verified flow series.
7. **Every gate kept.** Each of these is unchanged and still runs on what goes out:
   - ticker establishment;
   - `final_gate` (caption plus chart symbol);
   - `claim_problems` (figures, named people, accusations and legal actions, quotations);
   - `check_post`, `verify_copy` and `viral.draft_problems`;
   - the wording gate.

   The qualifier check is new on top. Tests show a punchier rewrite is still held when it adds a figure, drops a qualifier, or adds a name,
   a superlative or a ticker. A model draft that drops "at least" goes out with it restored, and passes the gate.

## The rules

### Words

- **One short factual headline.** This is the verified copy `network.write` picks: the local GPU caption, then the Anthropic model, then the
  desk's own outlet sentences.
  - It is set in sentence case (`headline_case`, above).
  - Over 120 characters, the trailing clause is dropped.
  - Qualifiers the sources attach to a figure go back in.
  - Nothing else is ever added.
- **At most one more sentence**, and only when it adds a figure the headline lacks, or when the headline is under eight words.
- **One cashtag per asset per post**, at its first mention, only where the sentence establishes the asset.
- **Footer:** one quiet line with the source links and `@thepricechat`. When the picture can't draw a photo credit, the credit goes on its
  own line above the footer.
- **X** gets the same tag and words, then one source link (or "Via ..." when X links are off). It never carries `@`.

### Tags (`wire.tag_for`)

| Tag | When |
|---|---|
| `BREAKING:` | Major and market-moving: news that `editorial.breaking_worthy` accepts, or an exceptional measured move or liquidation flush (`editorial.exceptional`). It also needs strong evidence (official, two outlets, our prices) and **at most 60 minutes** from first report to release. It applies only when the desk labelled the story BREAKING or released it through the breaking lane. |
| `JUST IN:` | Fresh but smaller: the desk earned a label (BREAKING, JUST IN or ALERT) and the story is **at most 90 minutes** old at release. |
| `UPDATE:` | A development on a story already posted. Telegram replies to the original message (`reply_to` = the parent's `tg_message_id`). |
| none | Everything else. Data posts and market checks never get a tag. |

BREAKING stays rare by design. A news story about a price move is not `breaking_worthy` (the topic "move" isn't a financial topic). A
measured move must clear its line: 8% in 24 hours for BTC. So the real 13 Nov bitcoin drop (two outlets in 27 minutes, -6.6% on the day)
is JUST IN (example 7). None of the real stories from 11 to 14 Nov earns BREAKING under these rules. BREAKING is proven by the tests: a
two-outlet missile strike through the whole desk, and a measured -6.2% move with an exceptional ratio through the lane.

### The gates on what goes out (all hard)

- Ticker establishment (ed36112).
- `final_gate` on the caption as sent plus the chart symbol (a7d2081).
- `claim_problems`: figures, named people, accusations and legal actions, quotations (1374981).
- `check_post`: figures, links, mentions, urgency, attribution, tickers.
- `verify_copy` and `viral.draft_problems`.
- Wire only, on top of the above:
  - `wording_problems`: hype, superlatives and loaded move words the sources don't support;
  - `qualifier_problems`: a figure stated as exact that the sources qualify.

  These run at write time, on the package and in `final_gate(..., wire=True)`. `final_gate` without `wire=True` is exactly 1374981's.

### Selection and speed (only when the style is wire)

- **Slot ranking:** the surprise bonus is up to +6 and affects ranking only. Filler costs -8 and counts against the bar.
- **Breaking lane:** the same as before (70+, two outlets, breaking-worthy, at most 90 minutes old), plus two more ways in:
  - an exceptional measured move on a priority asset (at least `market_bar`, at most 30 minutes old, with the asset cooldown);
  - a material update (strong evidence, at least `regular_bar`, at least 30 minutes after its original).
- **Rate limit:** at most 3 breaking posts a day and 2 in any 6 hours, at least 90 minutes from any other post, and none in the 45
  minutes before a slot.

## Settings and defaults

| Setting | Default | Where |
|---|---|---|
| `post_style` | **`clean`** (wire is opt-in) | `NdSettings.state.post_style`, Admin switch, `network.DEFAULT_STYLE` |
| breaking per day / per 6 h | 3 / 2 in wire (2 / none in clean and classic) | `events.WIRE_BREAKING_PER_DAY`, `WIRE_BREAKING_PER_6H` |
| BREAKING / JUST IN freshness | 60 / 90 min | `wire.BREAKING_MINUTES`, `wire.JUST_IN_MINUTES` |
| headline max | 120 chars | `wire.HEAD_MAX` |
| filler penalty / surprise max | 8 / 6 | `wire.FILLER_PENALTY`, `wire.SURPRISE_MAX` |
| corroboration window | 48 h | `engine.CORROBORATION_WINDOW` |

## Ten examples (real stories, revised)

How these were made:

- Harness: `wire_examples2.py` in the session scratchpad. Its output is `wire_examples2.json`; the picture files are under `real/`.
- **Before** is each post's exact text from `https://t.me/s/Pricechatupdates` (read 2026-11-14). Examples 9 and 10 were not posted live.
  Example 8's before is the original post it replies to.
- **Inputs** are the linked articles' og:title / og:description, and for the new stories the outlets' own RSS items (title, description,
  pubDate), fetched read-only on 2026-11-14. Bloomberg, FT and Seeking Alpha block this PC, so they are not used.
- **Words** come from the desk's own writer (outlet sentences). No model was used.
- **Prices** are real Yahoo Finance BTC-USD 15-minute candles. Charts are drawn from them at the release moment.
- **Photos** come from real Wikimedia Commons and Openverse searches through `photos.discover` (licence checks included).
- **Release moments** are the live post's time. For stories that weren't posted live, the harness assumes the release the lane would have
  made; each example says what was assumed. The labels the desk would have earned are set the same way. The breaking lane's score bar is
  not re-run.

| # | Story | Before (live) | After (wire, Telegram plain text) | Tag | Picture and why |
|---|---|---|---|---|---|
| 1 | Jailed for eight years over $2.7M | Crypto fraudster jailed for eight years over $2.7M theft plot directed from prison | Crypto fraudster jailed for eight years over $2.7M theft plot directed from prison. | none | text only: no real picture adds anything: not a price story whose candles agree, no public figure leading it, no named site of a major incident |
| 2 | Saudi output +26% | Saudi Crude Output Jumps 26% as Riyadh Reports Near Pre-War Production | Saudi crude output jumps 26% as Riyadh reports near pre-war production. | none | text only: no real picture adds anything: not a price story whose candles agree, no public figure leading it, no named site of a major incident |
| 3 | Deutsche Bank custody | Deutsche Bank’s Bitcoin and Ether Custody Is Open to Clients, Bank Says | Deutsche Bank's Bitcoin $BTC and Ether $ETH custody is open to clients, bank says. | none | text only: no real picture adds anything: not a price story whose candles agree, no public figure leading it, no named site of a major incident |
| 4 | Nasser Hospital strike | JUST IN: Israeli strike on Nasser Hospital kills at least six, Gaza civil defence agency says. | JUST IN: Israeli strike on Gaza hospital kills at least six Palestinians, health ministry says. | JUST IN: | text only: no real picture adds anything: not a price story whose candles agree, no public figure leading it, no named site of a major incident |
| 5 | IAEA / Iran | U.N. watchdog agency pushes for access to key Iran nuclear site. As Trump signals he may let the Iran war fester, the U.N. nuclear agency is "increasingly concerned" about access to a key uranium facility, according to a leaked report. | U.N. watchdog agency pushes for access to key Iran nuclear site. | none | text only: no real picture adds anything: not a price story whose candles agree, no public figure leading it, no named site of a major incident |
| 6 | China-US tariffs | JUST IN: China, US discuss tariffs, farm trade ahead of Xi-Trump meeting. Beijing has repeatedly urged Washington to implement the $60B tariff-reduction deal, calling it a prerequisite for increasing purchases of US farm goods. | JUST IN: China, US discuss tariffs, farm trade ahead of Xi-Trump meeting. Beijing has repeatedly urged Washington to implement the $60 billion tariff-reduction deal, calling it a prerequisite for increasing purchases of US farm goods. | JUST IN: | text only: no real picture adds anything: not a price story whose candles agree, no public figure leading it, no named site of a major incident |
| 7 | BTC below $69,000 | JUST IN: Bitcoin Breaks Below $70,000 as Liquidations Sweep Through Leveraged Longs ($BTC -5.4% in 24H). More than $1B in crypto positions were forced shut in 12 hours as bitcoin $BTC broke below $70,000. | JUST IN: Bitcoin $BTC breaks below $69,000 as selloff sets new lows. | JUST IN: | chart: $BTC · 15m · Yahoo Finance: 96 candles, -6.64% |
| 8 | US seizes $1.2B (UPDATE) | NORTH KOREAN HACKERS ARE CREATING MEMECOINS TO WASH LOOT FROM $6.3B BINANCE HACK  Source: CoinDesk | UPDATE: U.S. seizes $1.2B in cryptocurrency stolen from Binance. | UPDATE: | text only: no real picture adds anything: not a price story whose candles agree, no public figure leading it, no named site of a major incident |
| 9 | Trump-Putin-Xi summit | not posted live | JUST IN: Trump, Putin and Xi plan historic joint meeting in China next week, White House says. | JUST IN: | photo: licensed photo of Donald Trump: File:Donald Trump swearing in ceremony.jpg (Photo: The White House / Wikimedia Commons (public domain)) |
| 10 | BTC -5.0% in 24 h | not posted live | Bitcoin $BTC falls 5.0% in 24 hours. | none | chart: Bitcoin · 24h · Yahoo Finance: 97 points over 24.0 h, -5.02%; no second source |

### 1. Crypto fraudster jailed (post 162)

- Live post: https://t.me/Pricechatupdates/162
- Sources given to the desk: [The Block](https://www.theblock.co/news/regulation/2026-11-11-crypto-fraudster-jailed-for-eight-years-over-2-7-million-theft-plot-directed-from-prison-437858) "Crypto fraudster jailed for eight years over $2.7 million theft plot directed from prison"; [Decrypt](https://decrypt.co/382984/prisoner-jailed-for-8-years-over-2-7m-crypto-theft-plot-run-from-his-cell) "Prisoner Jailed for 8 Years Over $2.7M Crypto Theft Plot Run From His Cell"
- Linked after the same-event check: The Block · Decrypt
- Timing: first reported 11 Nov 10:20 UTC, released 12:48 UTC (148 min); evidence: two outlets

Before (exact, as shown in the channel):

```
Crypto fraudster jailed for eight years over $2.7M theft plot directed from prison

🔗 Sources: The Block · Decrypt

🥝 Price Chat News · Verified across 2 outlets
```

After, Telegram (exact HTML as sent):

```html
Crypto fraudster jailed for eight years over $2.7M theft plot directed from prison.

🔗 <a href="https://www.theblock.co/news/regulation/2026-11-11-crypto-fraudster-jailed-for-eight-years-over-2-7-million-theft-plot-directed-from-prison-437858">The Block</a> · <a href="https://decrypt.co/382984/prisoner-jailed-for-8-years-over-2-7m-crypto-theft-plot-run-from-his-cell">Decrypt</a> · <a href="https://t.me/thepricechat">@thepricechat</a>
```

After, X:

```
Crypto fraudster jailed for eight years over $2.7M theft plot directed from prison.

The Block: https://www.theblock.co/news/regulation/2026-11-11-crypto-fraudster-jailed-for-eight-years-over-2-7-million-theft-plot-directed-from-prison-437858
```

- Tag: **none**. Picture: **text**: no real picture adds anything: not a price story whose candles agree, no public figure leading it, no named site of a major incident.
- Package checks and final gate (tickers, chart, claims, wording, qualifiers): passed.

### 2. Saudi crude output (post 163)

- Live post: https://t.me/Pricechatupdates/163
- Sources given to the desk: [OilPrice](https://oilprice.com/Latest-Energy-News/World-News/Saudi-Crude-Output-Jumps-26-as-Riyadh-Reports-Near-Pre-War-Production.html) "Saudi Crude Output Jumps 26% as Riyadh Reports Near Pre-War Production"
- Linked after the same-event check: OilPrice
- Timing: publication time not on the outlets' pages: the desk's own label on the live post is carried over; evidence: attributed

Before (exact, as shown in the channel):

```
Saudi Crude Output Jumps 26% as Riyadh Reports Near Pre-War Production

Saudi Arabia told OPEC it raised crude production in October to 10.563 million barrels per day, back near pre-war levels and slightly above its OPEC quota of about 10.5 million bpd, Bloomberg columnist Javier Blas reported on Wednesday.

🔗 Sources: OilPrice · Seeking Alpha · Bloomberg

🥝 Price Chat News · Verified across 3 outlets
```

After, Telegram (exact HTML as sent):

```html
Saudi crude output jumps 26% as Riyadh reports near pre-war production.

🔗 <a href="https://oilprice.com/Latest-Energy-News/World-News/Saudi-Crude-Output-Jumps-26-as-Riyadh-Reports-Near-Pre-War-Production.html">OilPrice</a> · <a href="https://t.me/thepricechat">@thepricechat</a>
```

After, X:

```
Saudi crude output jumps 26% as Riyadh reports near pre-war production.

OilPrice: https://oilprice.com/Latest-Energy-News/World-News/Saudi-Crude-Output-Jumps-26-as-Riyadh-Reports-Near-Pre-War-Production.html
```

- Tag: **none**. Picture: **text**: no real picture adds anything: not a price story whose candles agree, no public figure leading it, no named site of a major incident.
- Package checks and final gate (tickers, chart, claims, wording, qualifiers): passed.

### 3. Deutsche Bank custody (post 164): the casing bug

- Live post: https://t.me/Pricechatupdates/164
- Sources given to the desk: [Unchained](https://unchainedcrypto.com/deutsche-banks-bitcoin-and-ether-custody-is-open-to-clients-bank-says/) "Deutsche Bank's Bitcoin and Ether Custody Is Open to Clients, Bank Says"; [The Block](https://www.theblock.co/news/business/2026-11-11-deutsche-bank-goes-live-with-digital-asset-custody-service-signs-asset-management-deal-437851) "Deutsche Bank goes live with digital asset custody service, signs asset management deal"; [Cointelegraph](https://cointelegraph.com/news/deutsche-bank-debuts-digital-asset-custody-services-eu) "Deutsche Bank Launches Institutional Crypto Custody"
- Linked after the same-event check: Unchained · The Block · Cointelegraph
- Timing: first reported 11 Nov 11:45 UTC, released 20:06 UTC (501 min); evidence: two outlets

Before (exact, as shown in the channel):

```
Deutsche Bank’s Bitcoin and Ether Custody Is Open to Clients, Bank Says

🔗 Sources: Unchained · The Block · Cointelegraph

🥝 Price Chat News · Verified across 5 outlets
```

After, Telegram (exact HTML as sent):

```html
Deutsche Bank's Bitcoin $BTC and Ether $ETH custody is open to clients, bank says.

🔗 <a href="https://unchainedcrypto.com/deutsche-banks-bitcoin-and-ether-custody-is-open-to-clients-bank-says/">Unchained</a> · <a href="https://www.theblock.co/news/business/2026-11-11-deutsche-bank-goes-live-with-digital-asset-custody-service-signs-asset-management-deal-437851">The Block</a> · <a href="https://cointelegraph.com/news/deutsche-bank-debuts-digital-asset-custody-services-eu">Cointelegraph</a> · <a href="https://t.me/thepricechat">@thepricechat</a>
```

After, X:

```
Deutsche Bank's Bitcoin $BTC and Ether $ETH custody is open to clients, bank says.

Unchained: https://unchainedcrypto.com/deutsche-banks-bitcoin-and-ether-custody-is-open-to-clients-bank-says/
```

- Tag: **none**. Picture: **text**: no real picture adds anything: not a price story whose candles agree, no public figure leading it, no named site of a major incident.
- Package checks and final gate (tickers, chart, claims, wording, qualifiers): passed.

### 4. Nasser Hospital strike (post 166): the qualifier and the unrelated source

- Live post: https://t.me/Pricechatupdates/166
- Sources given to the desk: [BBC](https://www.bbc.co.uk/news/articles/ckzd9p7e4eqeo) "Israeli strike on Gaza hospital kills six Palestinians, health ministry says"; [Al Jazeera](https://www.aljazeera.com/sports/2026/11/11/israeli-army-kills-palestinian-footballer-mahmoud-kamal-abdel-malek-in-gaza) "Israeli army kills Palestinian footballer Mahmoud Kamal Abdel-Malek in Gaza"
- Linked after the same-event check: BBC
- Timing: first report: the BBC feed item's time is not on the page; set 38 min before the live post, as the live post's JUST IN label implies; evidence: attributed

Before (exact, as shown in the channel):

```
JUST IN: Israeli strike on Nasser Hospital kills at least six, Gaza civil defence agency says.

🔗 BBC · Al Jazeera

📷 Photo: Shlomo Spitzer (שלמה שפיצר) / Wikimedia Commons (CC BY 2.5)

@thepricechat
```

After, Telegram (exact HTML as sent):

```html
<b>JUST IN:</b> Israeli strike on Gaza hospital kills at least six Palestinians, health ministry says.

🔗 <a href="https://www.bbc.co.uk/news/articles/ckzd9p7e4eqeo">BBC</a> · <a href="https://t.me/thepricechat">@thepricechat</a>
```

After, X:

```
JUST IN: Israeli strike on Gaza hospital kills at least six Palestinians, health ministry says.

BBC: https://www.bbc.co.uk/news/articles/ckzd9p7e4eqeo
```

- Tag: **JUST IN:**. Picture: **text**: no real picture adds anything: not a price story whose candles agree, no public figure leading it, no named site of a major incident.
- Package checks and final gate (tickers, chart, claims, wording, qualifiers): passed.

### 5. IAEA and Iran (post 174): the unrelated Bahrain story

- Live post: https://t.me/Pricechatupdates/174
- Sources given to the desk: [CBS News](https://www.cbsnews.com/live-updates/us-iran-war-trump-nuclear-material-no-deal/) "U.N. watchdog agency pushes for access to key Iran nuclear site"; [Al Jazeera](https://www.aljazeera.com/news/2026/11/13/iran-says-it-struck-us-radar-site-in-bahrain) "US denies Iran’s claim it hit two radar sites in Bahrain"; [BBC](https://www.bbc.co.uk/news/articles/cm75261e3pdko) "UN watchdog 'increasingly concerned' over uninspected Iran nuclear site"
- Linked after the same-event check: CBS News · BBC
- Timing: publication time not on the outlets' pages: the desk's own label on the live post is carried over; evidence: two outlets

Before (exact, as shown in the channel):

```
U.N. watchdog agency pushes for access to key Iran nuclear site. As Trump signals he may let the Iran war fester, the U.N. nuclear agency is "increasingly concerned" about access to a key uranium facility, according to a leaked report.

🔗 CBS News · Al Jazeera · BBC

@thepricechat
```

After, Telegram (exact HTML as sent):

```html
U.N. watchdog agency pushes for access to key Iran nuclear site.

🔗 <a href="https://www.cbsnews.com/live-updates/us-iran-war-trump-nuclear-material-no-deal/">CBS News</a> · <a href="https://www.bbc.co.uk/news/articles/cm75261e3pdko">BBC</a> · <a href="https://t.me/thepricechat">@thepricechat</a>
```

After, X:

```
U.N. watchdog agency pushes for access to key Iran nuclear site.

CBS News: https://www.cbsnews.com/live-updates/us-iran-war-trump-nuclear-material-no-deal/
```

- Tag: **none**. Picture: **text**: no real picture adds anything: not a price story whose candles agree, no public figure leading it, no named site of a major incident.
- Package checks and final gate (tickers, chart, claims, wording, qualifiers): passed.

### 6. China-US tariff talks (post 176)

- Live post: https://t.me/Pricechatupdates/176
- Sources given to the desk: [CNBC](https://www.cnbc.com/2026/11/14/china-us-tariffs-xi-trump-meeting.html) "China, US discuss tariffs, farm trade ahead of Xi-Trump meeting"
- Linked after the same-event check: CNBC
- Timing: first reported 14 Nov 08:16 UTC, released 08:22 UTC (6 min); evidence: attributed

Before (exact, as shown in the channel):

```
JUST IN: China, US discuss tariffs, farm trade ahead of Xi-Trump meeting. Beijing has repeatedly urged Washington to implement the $60B tariff-reduction deal, calling it a prerequisite for increasing purchases of US farm goods.

🔗 CNBC

@thepricechat
```

After, Telegram (exact HTML as sent):

```html
<b>JUST IN:</b> China, US discuss tariffs, farm trade ahead of Xi-Trump meeting. Beijing has repeatedly urged Washington to implement the $60 billion tariff-reduction deal, calling it a prerequisite for increasing purchases of US farm goods.

🔗 <a href="https://www.cnbc.com/2026/11/14/china-us-tariffs-xi-trump-meeting.html">CNBC</a> · <a href="https://t.me/thepricechat">@thepricechat</a>
```

After, X:

```
JUST IN: China, US discuss tariffs, farm trade ahead of Xi-Trump meeting. Beijing has repeatedly urged Washington to implement the $60 billion tariff-reduction deal, calling it a prerequisite for increasing purchases of US farm goods.

CNBC: https://www.cnbc.com/2026/11/14/china-us-tariffs-xi-trump-meeting.html
```

- Tag: **JUST IN:**. Picture: **text**: no real picture adds anything: not a price story whose candles agree, no public figure leading it, no named site of a major incident.
- Package checks and final gate (tickers, chart, claims, wording, qualifiers): passed.

### 7. Bitcoin below $69,000, two outlets in 27 minutes (the story behind post 173)

- Live post: https://t.me/Pricechatupdates/173
- Sources given to the desk: [The Defiant](https://thedefiant.io/news/markets/bitcoin-breaks-below-69-000-as-selloff-sets-new-lows) "Bitcoin Breaks Below $69,000 as Selloff Sets New Lows"; [The Block](https://www.theblock.co/news/markets/2026-11-13-bitcoin-tumbles-below-69000-439643) "Bitcoin tumbles below $69,000 as Nvidia turmoil adds to Fed-driven market jitters"
- Linked after the same-event check: The Defiant · The Block
- Timing: the same move the live desk posted at 12:08 UTC from Unchained alone (post 173). The Defiant (05:56 UTC) and The Block (06:23 UTC) both reported bitcoin below $69,000; the breaking lane is assumed to release it at 06:30 UTC, 34 min after the first report; evidence: two outlets

Before (exact, as shown in the channel):

```
JUST IN: Bitcoin Breaks Below $70,000 as Liquidations Sweep Through Leveraged Longs ($BTC -5.4% in 24H). More than $1B in crypto positions were forced shut in 12 hours as bitcoin $BTC broke below $70,000.

🔗 Unchained

@thepricechat
```

After, Telegram (exact HTML as sent):

```html
<b>JUST IN:</b> Bitcoin $BTC breaks below $69,000 as selloff sets new lows.

🔗 <a href="https://thedefiant.io/news/markets/bitcoin-breaks-below-69-000-as-selloff-sets-new-lows">The Defiant</a> · <a href="https://www.theblock.co/news/markets/2026-11-13-bitcoin-tumbles-below-69000-439643">The Block</a> · <a href="https://t.me/thepricechat">@thepricechat</a>
```

After, X:

```
JUST IN: Bitcoin $BTC breaks below $69,000 as selloff sets new lows.

The Defiant: https://thedefiant.io/news/markets/bitcoin-breaks-below-69-000-as-selloff-sets-new-lows
```

- Tag: **JUST IN:**. Picture: **chart**: $BTC · 15m · Yahoo Finance: 96 candles, -6.64%.
- Package checks and final gate (tickers, chart, claims, wording, qualifiers): passed.

### 8. UPDATE: US seizes $1.2B of the Binance hack loot (reply to post 171)

- Live post: https://t.me/Pricechatupdates/171
- Sources given to the desk: [CNBC](https://www.cnbc.com/2026/11/13/us-seizes-binance-hack-crypto.html) "U.S. seizes $1.2 billion in cryptocurrency stolen from Binance"; [Cointelegraph](https://cointelegraph.com/news/doj-12b-north-korean-crypto-seizure-400m-facestealer-scheme) "DOJ moves to seize $1.2B from Binance hack, disrupts FaceStealer scam"
- Linked after the same-event check: CNBC · Cointelegraph
- Timing: a development on post 171 (North Korean hackers laundering the $6.3B Binance loot). Not posted live; assumed released 12 min after CNBC's 12:03 UTC story, as a reply to message 171; evidence: two outlets
- Telegram: sent as a reply to message 171 (`reply_to`)

Before (exact, as shown in the channel):

```
NORTH KOREAN HACKERS ARE CREATING MEMECOINS TO WASH LOOT FROM $6.3B BINANCE HACK  Source: CoinDesk
```

After, Telegram (exact HTML as sent):

```html
<b>UPDATE:</b> U.S. seizes $1.2B in cryptocurrency stolen from Binance.

🔗 <a href="https://www.cnbc.com/2026/11/13/us-seizes-binance-hack-crypto.html">CNBC</a> · <a href="https://cointelegraph.com/news/doj-12b-north-korean-crypto-seizure-400m-facestealer-scheme">Cointelegraph</a> · <a href="https://t.me/thepricechat">@thepricechat</a>
```

After, X:

```
UPDATE: U.S. seizes $1.2B in cryptocurrency stolen from Binance.

CNBC: https://www.cnbc.com/2026/11/13/us-seizes-binance-hack-crypto.html
```

- Tag: **UPDATE:**. Picture: **text**: no real picture adds anything: not a price story whose candles agree, no public figure leading it, no named site of a major incident.
- Package checks and final gate (tickers, chart, claims, wording, qualifiers): passed.

### 9. Trump, Putin and Xi summit (a public-figure photo)

- Sources given to the desk: [BBC](https://www.bbc.co.uk/news/articles/ckz74jzpy18do) "Trump, Putin and Xi plan historic joint meeting in China next week, White House says"
- Linked after the same-event check: BBC
- Timing: not posted live (the live desk's last post is 176). Assumed released 18 min after the BBC's 05:52 UTC item; evidence: attributed

After, Telegram (exact HTML as sent):

```html
<b>JUST IN:</b> Trump, Putin and Xi plan historic joint meeting in China next week, White House says.

🔗 <a href="https://www.bbc.co.uk/news/articles/ckz74jzpy18do">BBC</a> · <a href="https://t.me/thepricechat">@thepricechat</a>
```

After, X:

```
JUST IN: Trump, Putin and Xi plan historic joint meeting in China next week, White House says.

BBC: https://www.bbc.co.uk/news/articles/ckz74jzpy18do
```

- Tag: **JUST IN:**. Picture: **photo**: licensed photo of Donald Trump: File:Donald Trump swearing in ceremony.jpg (Photo: The White House / Wikimedia Commons (public domain)).
- Package checks and final gate (tickers, chart, claims, wording, qualifiers): passed.

### 10. Bitcoin's measured 24-hour move (the desk's own chart post)

- Sources given to the desk: [Yahoo Finance](https://finance.yahoo.com/quote/BTC-USD/)
- Linked after the same-event check: Yahoo Finance
- Timing: measured -5.02% over 97 real closes; evidence: price data

After, Telegram (exact HTML as sent):

```html
Bitcoin $BTC falls 5.0% in 24 hours.

🔗 <a href="https://finance.yahoo.com/quote/BTC-USD/">Yahoo Finance</a> · <a href="https://t.me/thepricechat">@thepricechat</a>
```

After, X:

```
Bitcoin $BTC falls 5.0% in 24 hours.

Yahoo Finance: https://finance.yahoo.com/quote/BTC-USD/
```

- Tag: **none**. Picture: **chart**: Bitcoin · 24h · Yahoo Finance: 97 points over 24.0 h, -5.02%; no second source.
- Package checks and final gate (tickers, chart, claims, wording, qualifiers): passed.

Notes on the examples:

- **4 (post 166).** The live post linked an Al Jazeera story about a footballer's death because both said "Israeli" and "Gaza". It is no
  longer attached. The BBC page now says "six"; the BBC feed item the desk had said "at least six", so "at least" is restored.
  - The headline mentions no named site, but the reporting does ("Nasser hospital"), so a site photo was searched for. Commons returned
    only "Gamal Abdel Nasser Hospital", which is in Alexandria, Egypt, and two files that don't name the site. All three were rejected, so
    the post is text only.
- **5 (post 174).** The Al Jazeera "radar sites in Bahrain" story was a different event. It is no longer linked. The BBC story is the same
  event and is still linked.
- **7.** The same move the live desk posted at 12:08 UTC (post 173, Unchained alone, JUST IN). Two outlets had it by 06:23 UTC. It is
  grouped by the price-level rule and gets JUST IN, not BREAKING (see Tags). Its chart was checked: the real candles close under $69,000.
- **8.** UPDATE replies to message 171. CNBC and Cointelegraph are grouped by the same amount ($1.2B = $1.2 billion).
- **9.** The first real Commons hit for "Donald Trump" was a tweet laid over a rally photo. It is now rejected (`plain_photo`). The photo
  used is the White House's public-domain swearing-in photo. It is a file photo: credited, but not of the summit.
- **10.** Measured -5.02% from 97 real Yahoo closes. That is under BTC's 8% line, so there is no tag: a slot post.
- Picture files: `real/ex_btc-breaking.jpg`, `real/ex_btc-move.jpg` and `real/ex_summit-photo.jpg` in the scratchpad.

## Tests

New tests in `backend/tests/test_newsdesk_wire_revision.py` (94):

- Clean is the default, and is byte-for-byte equal to 1374981 on five real stories (`newsdesk_clean_golden_1374981.json`, written by
  1374981's own code from `newsdesk_style_cases.py`).
- With no style chosen, the lane posts Clean.
- Casing (9 cases).
- Qualifiers: restore, hedges, and the final gate.
- Post 166 through `network.package`: "at least six", and no Al Jazeera link.
- Same-event: 8 false-merge cases and 8 real same-event cases.
- `events._same`, `engine.corroboration` (including the 48-hour window) and `editorial.corroborating` (the 166 path, and NYSE/Avalanche
  still grouping).
- Through the whole desk: no country-only merge, and the two Ras Tanura tellings still grouping.
- Through the lane: JUST IN (exact caption), UPDATE with `reply_to`, BREAKING measured move with a verified chart, a news price chart from
  parsed Yahoo responses, no chart when the candles disagree, and text only.
- `chart_matches` (6 cases).
- Filler: 9 headlines that are filler and 9 that are news.
- Surprise ranking.
- Site photo, with the real wrong-hospital case, and plain-photo rejection.
- Index charts.
- Punchy rewrites (6), and a model draft dropping a qualifier.

One existing wire test changed: the model draft's bare "$1 billion" now gets the sources' "more than" back.

Results on this PC (2026-11-14):

- **News Desk:** `pytest tests/test_newsdesk*.py tests/test_x_follows_telegram.py` gives **667 passed**: the 573 that passed before
  this revision, plus 94 new.
- **Full backend:** **2735 passed, 5 failed** (9 min 41 s). The 5 failures also fail on clean main: `test_community`, `test_lunch_bot`,
  `test_tg_trading`, `test_trading_execution` and `test_trends`. `test_kalshi_links` passed in the full run and alone (8 passed).
- **Frontend:** `npx vitest run` gives 58 files and 505 tests passed. `npx tsc -b` is clean.

## Deploy (when the owner approves)

1. Merge `feat/newsdesk-wire` into main. It is based on 1374981, which production runs.
2. **Backend:** Railway, from a linked worktree's `backend/`. Run `railway link -p c2a48c3b… -e production -s api` and check
   `railway status` first. Never run it from C:/AI/price-chat or an unlinked directory.
3. **Frontend:** Vercel, for the three-way Admin switch. The old two-way switch can't select Wire.
4. There is **no migration** (`post_style` is a JSON state key) and **no worker restart** (worker code is unchanged).
5. After deploy, the live output is still Clean. Only the same-event grouping changes, which can mean fewer wrong links or fewer
   wrongly-corroborated stories.
6. To try Wire, use Admin › Use wire. Check the next posts with `railway logs <deployment> --filter "newsdesk OR tgnews"` and the Admin
   Decisions table.

## Rollback

- **Wire only:** Admin › Use clean (instant, no deploy).
- **Code:** redeploy 1374981. There is no migration to undo. Events posted in Wire keep `facts.visual.style = "wire"`; 1374981 reads that
  as classic if X still owes such a post.

## Risks

- **Grouping is stricter for every style.** A real second outlet that words the story very differently, with no shared distinctive name,
  amount or price level, is no longer counted. That story then stands on one outlet: it can lose "two outlets" evidence or wait for a
  second source. This is intended (a wrong source is worse than a missing one), but watch for fewer posts on big days.
- **Casing heuristics.** A Title Case word that the reporting never writes is judged by a list of ordinary words and English suffixes. A
  rare ordinary word can keep its capital, or a rare name ending in -s/-ed can lose it. It is cosmetic only; facts are unaffected.
- **Qualifier matching.** It is by figure plus the counted noun. A source qualifier on a different noun is ignored, which is
  conservative. An unqualified figure counting the same thing gets the qualifier back even when another source states it exactly. That
  is never false, only more cautious.
- **Photos.** Person photos are file photos, credited, and can be years old. A site photo is used only when the photo names that exact
  site and the story's place. In practice it is rare.
- **BREAKING is rare.** None of the 11-14 Nov real stories earns it. That is by the rules (price-move news is not breaking-worthy; BTC's
  24-hour line is 8%). If the owner wants a two-outlet $70K break to be BREAKING, that needs a rule change, which was not made here.
- **Not done:**
  - ETF-flow charts: there is no verified flow series.
  - `engine.same_story` (post dedupe across a day) still matches on any shared name. It suppresses posts and never attaches sources, so
    it was left as it is.
