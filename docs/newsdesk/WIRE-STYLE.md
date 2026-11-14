# News Desk: the wire style

Branch `feat/newsdesk-wire`, based on production `1374981` (fix/newsdesk-cashtags, the final-output claim gate). `feat/newsdesk-viral` is
not included. **Not deployed.**

The owner asked (2026-11-14) for "a cleaner, faster Watcher Guru news style": short factual headlines, earned tags, text-only posts allowed,
real charts for big moves, surprising stories first, quick breaking posts under a limit, and every accuracy gate kept.

**Choice: a new style, `wire`, which becomes the default.** `clean` and `classic` stay as they are. Admin › X › News Desk › Price Chat News
now has a three-way switch (Wire / Clean / Classic). Changing it is one PUT (`/x/newsdesk/network {"post_style": "clean"}`). No deploy needed.

Code: `backend/app/newsdesk/wire.py` (new). Smaller changes in `checks.py`, `network.py`, `events.py`, `telegram.py`, `visual.py`,
`engine.py`, `queue.py`, `api/routes/newsdesk.py` and `frontend/src/pages/x/NetworkSwitches.tsx`.

## The rules

### Words

- **One short factual headline.** This is the verified copy `network.write` picks (in order: local GPU caption, Anthropic model, angle
  copy, the desk's own outlet sentences). It is put in sentence case. A word the clean style would lower-case takes the spelling the
  reporting itself uses ("U-turn", "pre-war", never "u-Turn"). Countries, capitals and known leaders keep their capital ("Riyadh"). Over
  120 characters, the trailing clause is dropped (`network.shorten`). Nothing is ever added.
- **At most one more sentence**, and only when it adds a figure the headline lacks, or when the headline is under eight words. A typical
  post is one or two sentences. There is no "why it matters", no question and no byline.
- **One cashtag per asset per post**, at its first mention, and only where the sentence establishes the asset (`clean.established`,
  unchanged). The old clean caption could tag `$BTC` twice; this one tags it once.
- **Footer:** one quiet last line with the source links and the community handle:
  `🔗 <a>CNBC</a> · <a>Reuters</a> · <a>@thepricechat</a>`. When the picture can't draw a photo credit, the credit goes on its own line
  above the footer.
- **X** gets the same tag and words, then one source link (or "Via …" when X links are off). It never carries `@`.

### Tags (`wire.tag_for`)

| Tag | When |
|---|---|
| `BREAKING:` | Major and market-moving: news `editorial.breaking_worthy` accepts, or an exceptional measured move or liquidation flush (`editorial.exceptional`). It also needs strong evidence (official, two outlets, or our own prices) and **at most 60 minutes** from first report to release. It applies only when the desk labelled the story BREAKING or released it through the breaking lane. |
| `JUST IN:` | Fresh but smaller: the desk earned a label (BREAKING, JUST IN or ALERT, as before) and the story is **at most 90 minutes** old at release. |
| `UPDATE:` | A development on a story already posted (an `update` event). Telegram already replies to the original message (`reply_to` = parent's `tg_message_id`). That code is unchanged. |
| none | Everything else. Data posts and market checks never get a tag. This style has no ALERT and no "MARKETS GONE MAD". |

A flag follows a BREAKING or JUST IN tag only when the first four words of the headline name exactly one country ("China, US discuss…"
gets no flag).

### Wording gate (new, wire only)

`checks.wording_problems` checks each sentence:

- An intensity word (massive, huge, explodes, skyrockets, bloodbath, meltdown…) needs **both** the sources' own word **and** a figure in
  the same sentence.
- A superlative (record, all-time, biggest, largest, first-ever, ever, unprecedented, historic…) needs the sources' own word.
- A loaded move word (crashes, plunges, soars, surges, tanks, collapses…) needs a figure in the sentence or the sources' own word.

It runs in three places:

1. At write time (`network.write(..., extra=…)`): a model or local draft that fails is refused, and the next writer's words are used. A
   draft is also refused here if a sentence would get a cashtag the sources don't establish.
2. On the package, for both platforms (check `wording`).
3. On the caption exactly as sent: `checks.final_gate(..., wire=True)`.

### Existing gates (unchanged, still hard)

- ticker establishment (ed36112)
- `final_gate` on the caption as sent plus the chart symbol (a7d2081)
- `claim_problems` for figures, people, accusations and quotations (1374981)
- `check_post`: figures, links, mentions, urgency, attribution, tickers
- `verify_copy` and `viral.draft_problems`

`final_gate` without `wire=True` gives exactly the same answers as before (there is a test for this).

### Pictures (`wire.picture`): real or none

| Picture | When |
|---|---|
| chart | A measured market move or liquidation flush (the desk's verified series; `visual._chart_for` still raises `NumbersRefused`). Or a news story whose first four words establish the asset **and** that reports its move (a % move, a level broken, a record, liquidations). For commodities, the price itself must move ("Oil prices jump 5%", never "Saudi crude output jumps 26%"). Or one the Viral Engine measured reacting. Macro stories (the S&P 500 stand-in) get a chart only on a measured reaction. Real Yahoo 15-minute candles. |
| data | A data post's comparison card (measured closes). |
| photo | A licensed photo of the public figure who leads the headline ("Powell says…"), under `clean.person_subject`'s rules (no private people, nothing violent, controversy only in a neutral photo). Never a company, a place or a stand-in scene. |
| text | Everything else. `b""` and `{"style":"wire","kind":"text","layout":"text"}` are stored on the event. Telegram sends a text message (link previews are off, as before). X posts with no media. The Admin preview shows a "TEXT ONLY" placeholder. |

This style never uses an AI illustration. The GPU render jobs still run, because they also carry the local caption draft, and Telegram still
waits for them as before (at most 6 minutes, or 2.5 for breaking). Their background image is simply not used.

### Selection and speed (`events.py`, only when the style is wire)

- **Slot ranking:** `wire.surprise_bonus` adds up to +6 for ranking only, never the bar. It counts +5 for hacks, halts, seizures, arrests,
  bankruptcies, resignations, bans, records, liquidations, sanctions, approvals and similar words, and +2 for a figure in the headline.
- **Filler:** `wire.filler` takes 8 points off, counted **against the bar** like routine controversy. It covers price predictions,
  analysis, outlooks, previews, "here's why", "analysts say", could/might/poised/set to, partnerships and how-tos. It applies to news only.
- **Breaking lane:** the existing `_release_breaking` also takes:
  - an exceptional measured move or liquidation flush on a priority asset (BTC, ETH, HYPE, majors, macro, commodities) that scores at least
    `market_bar` (70) and is at most 30 minutes old, unless that asset already went out within the 6-hour cooldown;
  - an update with a material development (`editorial.material_development`) on a story already out, with strong evidence, scoring at least
    `regular_bar` (56), at most 90 minutes old, and at least 30 minutes after its original.

  News still needs everything it needed before: 70+, two outlets, breaking-worthy, at most 90 minutes old. Every breaking post still goes
  through repeats and story dedup, diversity, the owner's block, preflight checks and the delivery gate.
- **Rate limit:** at most **3 breaking posts a day and 2 in any 6 hours** (new). Each must be at least 90 minutes from any other post, and
  none goes in the 45 minutes before a slot. All of this is as before except the counts. The Telegram lane's own daily cap picks up the
  plan's numbers automatically (`over_limit`). The six Dubai slots are unchanged.

## Settings and defaults

| Setting | Default | Where |
|---|---|---|
| `post_style` | `wire` (was `clean`) | `NdSettings.state.post_style`, Admin switch, `network.DEFAULT_STYLE` |
| breaking per day / per 6 h | 3 / 2 in wire (2 / no rolling cap in clean and classic) | `events.WIRE_BREAKING_PER_DAY`, `WIRE_BREAKING_PER_6H` |
| breaking market age | 30 min | `Policy.breaking_market_age` |
| BREAKING / JUST IN freshness | 60 / 90 min | `wire.BREAKING_MINUTES`, `wire.JUST_IN_MINUTES` |
| headline max | 120 chars | `wire.HEAD_MAX` |
| filler penalty / surprise max | 8 / 6 | `wire.FILLER_PENALTY`, `wire.SURPRISE_MAX` |

If the owner ever set `post_style` explicitly in Admin, that choice stays after deploy. If it is still `clean`, flip it to Wire in Admin.

## Ten before/after examples (real posts, real sources)

How these were made:

- **Before** is each post's exact text from the public channel preview `https://t.me/s/Pricechatupdates` (read 2026-11-14).
- **After** inputs are each post's own sources: the title and summary on the linked article page (og:title, og:description,
  article:published_time). Posts 163 and 174 also use the fuller outlet sentence that the before-post itself quoted from the feed.
  Seeking Alpha, Bloomberg and FT return 403 from this PC, so they are not used as input.
- The words come from the desk's own writer (outlet sentences, deterministic). No model was used and nothing is invented. Every figure in
  an "after" text appears in its source.
- The JUST IN tag is computed from each source's published time. Where a page gives no time, the desk's own label on the before-post is
  carried over.
- Post 173's live headline also carried the desk's measured "($BTC -5.4% in 24H)". That figure came from our own prices, which aren't
  public, so the harness used the outlet's title only.
- Pictures were decided with photo search and Yahoo stubbed, so these are decisions, not renders.
- Harness: session scratchpad `wire_examples.py`; the output is in `wire_examples.json`.

Post 174's source is a live blog. Its page title is the blog's ("Live Updates: …"), so the harness used the item headline the before-post
carried. Post 166's Al Jazeera link is a different story (a footballer). The existing `links_for` matched it on the shared name "Israeli".
This style doesn't change that, and it is a known weakness of link grouping.

### 1. Crypto fraudster jailed for eight years over $2.7 million theft plot directed from prison

- Post: https://t.me/Pricechatupdates/162 (2026-11-11 12:48:47 UTC)
- Sources: [The Block](https://www.theblock.co/news/regulation/2026-11-11-crypto-fraudster-jailed-for-eight-years-over-2-7-million-theft-plot-directed-from-prison-437858) "Crypto fraudster jailed for eight years over $2.7 million theft plot directed from prison"; [Decrypt](https://decrypt.co/382984/prisoner-jailed-for-8-years-over-2-7m-crypto-theft-plot-run-from-his-cell) "Prisoner Jailed for 8 Years Over $2.7M Crypto Theft Plot Run From His Cell"
- Freshness: first reported 10:20 UTC, released 12:48 UTC (148 min); evidence: two outlets

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

- Tag: **none**. Picture: **text-only**: no real picture fits: not a price story, no public figure as the subject.
- Writer: the desk's own (outlet sentences; no model). Gates passed: attribution, claims, figures, hashtags, length, no_link, no_mentions, not_exclusive, not_rumour, tickers, urgency, wording; final gate (tickers, chart, claims, wording): passed.

### 2. Saudi Crude Output Jumps 26% as Riyadh Reports Near Pre-War Production

- Post: https://t.me/Pricechatupdates/163 (2026-11-11 16:06:28 UTC)
- Sources: [OilPrice](https://oilprice.com/Latest-Energy-News/World-News/Saudi-Crude-Output-Jumps-26-as-Riyadh-Reports-Near-Pre-War-Production.html) "Saudi Crude Output Jumps 26% as Riyadh Reports Near Pre-War Production"
- Not readable from this PC (403), so not used as input: Seeking Alpha, Bloomberg
- Freshness: publication time not on the outlets' pages: the desk's own label on the before-post is carried over; evidence: two outlets

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

- Tag: **none**. Picture: **text-only**: no real picture fits: not a price story, no public figure as the subject.
- Writer: the desk's own (outlet sentences; no model). Gates passed: attribution, claims, figures, hashtags, length, no_link, no_mentions, not_exclusive, not_rumour, tickers, urgency, wording; final gate (tickers, chart, claims, wording): passed.

### 3. Deutsche Bank's Bitcoin and Ether Custody Is Open to Clients, Bank Says

- Post: https://t.me/Pricechatupdates/164 (2026-11-11 20:06:32 UTC)
- Sources: [Unchained](https://unchainedcrypto.com/deutsche-banks-bitcoin-and-ether-custody-is-open-to-clients-bank-says/) "Deutsche Bank's Bitcoin and Ether Custody Is Open to Clients, Bank Says"; [The Block](https://www.theblock.co/news/business/2026-11-11-deutsche-bank-goes-live-with-digital-asset-custody-service-signs-asset-management-deal-437851) "Deutsche Bank goes live with digital asset custody service, signs asset management deal"; [Cointelegraph](https://cointelegraph.com/news/deutsche-bank-debuts-digital-asset-custody-services-eu) "Deutsche Bank Launches Institutional Crypto Custody"
- Freshness: first reported 11:45 UTC, released 20:06 UTC (501 min); evidence: two outlets

Before (exact, as shown in the channel):

```
Deutsche Bank’s Bitcoin and Ether Custody Is Open to Clients, Bank Says

🔗 Sources: Unchained · The Block · Cointelegraph

🥝 Price Chat News · Verified across 5 outlets
```

After, Telegram (exact HTML as sent):

```html
Deutsche Bank's Bitcoin $BTC and Ether $ETH custody is open to clients, Bank says.

🔗 <a href="https://unchainedcrypto.com/deutsche-banks-bitcoin-and-ether-custody-is-open-to-clients-bank-says/">Unchained</a> · <a href="https://www.theblock.co/news/business/2026-11-11-deutsche-bank-goes-live-with-digital-asset-custody-service-signs-asset-management-deal-437851">The Block</a> · <a href="https://cointelegraph.com/news/deutsche-bank-debuts-digital-asset-custody-services-eu">Cointelegraph</a> · <a href="https://t.me/thepricechat">@thepricechat</a>
```

After, X:

```
Deutsche Bank's Bitcoin $BTC and Ether $ETH custody is open to clients, Bank says.

Unchained: https://unchainedcrypto.com/deutsche-banks-bitcoin-and-ether-custody-is-open-to-clients-bank-says/
```

- Tag: **none**. Picture: **text-only**: no real picture fits: not a price story, no public figure as the subject.
- Writer: the desk's own (outlet sentences; no model). Gates passed: attribution, claims, figures, hashtags, length, no_link, no_mentions, not_exclusive, not_rumour, tickers, urgency, wording; final gate (tickers, chart, claims, wording): passed.

### 4. Israeli strike on Gaza hospital kills six Palestinians, health ministry says

- Post: https://t.me/Pricechatupdates/166 (2026-11-12 04:27:54 UTC)
- Sources: [BBC](https://www.bbc.co.uk/news/articles/ckzd9p7e4eqeo) "Israeli strike on Gaza hospital kills six Palestinians, health ministry says"; [Al Jazeera](https://www.aljazeera.com/sports/2026/11/11/israeli-army-kills-palestinian-footballer-mahmoud-kamal-abdel-malek-in-gaza) "Israeli army kills Palestinian footballer Mahmoud Kamal Abdel-Malek in Gaza"
- Freshness: publication time not on the outlets' pages: the desk's own label on the before-post is carried over; evidence: two outlets

Before (exact, as shown in the channel):

```
JUST IN: Israeli strike on Nasser Hospital kills at least six, Gaza civil defence agency says.

🔗 BBC · Al Jazeera

📷 Photo: Shlomo Spitzer (שלמה שפיצר) / Wikimedia Commons (CC BY 2.5)

@thepricechat
```

After, Telegram (exact HTML as sent):

```html
<b>JUST IN:</b> Israeli strike on Gaza hospital kills six Palestinians, health ministry says.

🔗 <a href="https://www.bbc.co.uk/news/articles/ckzd9p7e4eqeo">BBC</a> · <a href="https://www.aljazeera.com/sports/2026/11/11/israeli-army-kills-palestinian-footballer-mahmoud-kamal-abdel-malek-in-gaza">Al Jazeera</a> · <a href="https://t.me/thepricechat">@thepricechat</a>
```

After, X:

```
JUST IN: Israeli strike on Gaza hospital kills six Palestinians, health ministry says.

BBC: https://www.bbc.co.uk/news/articles/ckzd9p7e4eqeo
```

- Tag: **JUST IN:**. Picture: **text-only**: no real picture fits: not a price story, no public figure as the subject.
- Writer: the desk's own (outlet sentences; no model). Gates passed: attribution, claims, figures, hashtags, length, no_link, no_mentions, not_exclusive, not_rumour, tickers, urgency, wording; final gate (tickers, chart, claims, wording): passed.

### 5. Russian Diesel Tanker Makes U-Turn After Heading to U.S.

- Post: https://t.me/Pricechatupdates/167 (2026-11-12 09:29:09 UTC)
- Sources: [gCaptain](https://gcaptain.com/russian-diesel-tanker-makes-u-turn-after-heading-to-u-s/) "Russian Diesel Tanker Makes U-Turn After Heading to U.S."
- Not readable from this PC (403), so not used as input: Financial Times, Seeking Alpha
- Freshness: first reported 11:52 UTC, released 09:29 UTC (817 min); evidence: two outlets

Before (exact, as shown in the channel):

```
Russian diesel exports double after Trump lifts sanctions, but US gets none.

🔗 Financial Times · gCaptain · Seeking Alpha

@thepricechat
```

After, Telegram (exact HTML as sent):

```html
Russian diesel tanker makes U-turn after heading to U.S.

🔗 <a href="https://gcaptain.com/russian-diesel-tanker-makes-u-turn-after-heading-to-u-s/">gCaptain</a> · <a href="https://t.me/thepricechat">@thepricechat</a>
```

After, X:

```
Russian diesel tanker makes U-turn after heading to U.S.

gCaptain: https://gcaptain.com/russian-diesel-tanker-makes-u-turn-after-heading-to-u-s/
```

- Tag: **none**. Picture: **text-only**: no real picture fits: not a price story, no public figure as the subject.
- Writer: the desk's own (outlet sentences; no model). Gates passed: attribution, claims, figures, hashtags, length, no_link, no_mentions, not_exclusive, not_rumour, tickers, urgency, wording; final gate (tickers, chart, claims, wording): passed.

### 6. Western Union's USDPT Stablecoin Becomes a Settlement Option on Visa, Anchorage Digital Says

- Post: https://t.me/Pricechatupdates/169 (2026-11-12 20:02:21 UTC)
- Sources: [Unchained](https://unchainedcrypto.com/western-unions-usdpt-stablecoin-becomes-a-settlement-option-on-visa-anchorage-digital-says/) "Western Union's USDPT Stablecoin Becomes a Settlement Option on Visa, Anchorage Digital Says"; [The Block](https://www.theblock.co/news/business/2026-11-12-visa-adds-western-unions-usdpt-stablecoin-to-its-settlement-network-in-anchorage-tie-up-438520) "Visa adds Western Union"; [Decrypt](https://decrypt.co/383098/anchorage-digital-stablecoins-settle-payments-visa-network) "Anchorage Digital’s Stablecoins Can Now Settle Payments on Visa’s Network"
- Freshness: first reported 16:00 UTC, released 20:02 UTC (242 min); evidence: two outlets

Before (exact, as shown in the channel):

```
Western Union’s USDPT Stablecoin Becomes a Settlement Option on Visa, Anchorage Digital Says.

🔗 Unchained · The Block · Decrypt

@thepricechat
```

After, Telegram (exact HTML as sent):

```html
Western Union's USDPT stablecoin becomes a settlement option on Visa, Anchorage Digital says.

🔗 <a href="https://unchainedcrypto.com/western-unions-usdpt-stablecoin-becomes-a-settlement-option-on-visa-anchorage-digital-says/">Unchained</a> · <a href="https://www.theblock.co/news/business/2026-11-12-visa-adds-western-unions-usdpt-stablecoin-to-its-settlement-network-in-anchorage-tie-up-438520">The Block</a> · <a href="https://decrypt.co/383098/anchorage-digital-stablecoins-settle-payments-visa-network">Decrypt</a> · <a href="https://t.me/thepricechat">@thepricechat</a>
```

After, X:

```
Western Union's USDPT stablecoin becomes a settlement option on Visa, Anchorage Digital says.

Unchained: https://unchainedcrypto.com/western-unions-usdpt-stablecoin-becomes-a-settlement-option-on-visa-anchorage-digital-says/
```

- Tag: **none**. Picture: **text-only**: no real picture fits: not a price story, no public figure as the subject.
- Writer: the desk's own (outlet sentences; no model). Gates passed: attribution, claims, figures, hashtags, length, no_link, no_mentions, not_exclusive, not_rumour, tickers, urgency, wording; final gate (tickers, chart, claims, wording): passed.

### 7. SpaceX-Tesla merger talks put 30,000 bitcoin back in focus

- Post: https://t.me/Pricechatupdates/172 (2026-11-13 08:02:35 UTC)
- Sources: [CoinDesk](https://www.coindesk.com/markets/2026/11/13/spacex-tesla-merger-talks-put-30-000-bitcoin-back-in-focus) "SpaceX-Tesla merger talks put 30,000 bitcoin back in focus"; [CNA](https://www.channelnewsasia.com/business/spacex-tesla-merger-talks-progress-bloomberg-news-reports-6547466) "SpaceX, Tesla merger talks progress, Bloomberg News reports"
- Not readable from this PC (403), so not used as input: Seeking Alpha
- Freshness: publication time not on the outlets' pages: the desk's own label on the before-post is carried over; evidence: two outlets

Before (exact, as shown in the channel):

```
SpaceX-Tesla merger talks put 30,000 bitcoin $BTC back in focus. A deal could be announced as soon as December, putting $2.1B of bitcoin $BTC under one roof, a sliver of what the combined group would be worth.

🔗 CoinDesk · Seeking Alpha · CNA

@thepricechat
```

After, Telegram (exact HTML as sent):

```html
SpaceX-Tesla merger talks put 30,000 bitcoin $BTC back in focus. A deal could be announced as soon as December, putting $2.1 billion of bitcoin under one roof, a sliver of what the combined group would be worth.

🔗 <a href="https://www.coindesk.com/markets/2026/11/13/spacex-tesla-merger-talks-put-30-000-bitcoin-back-in-focus">CoinDesk</a> · <a href="https://www.channelnewsasia.com/business/spacex-tesla-merger-talks-progress-bloomberg-news-reports-6547466">CNA</a> · <a href="https://t.me/thepricechat">@thepricechat</a>
```

After, X:

```
SpaceX-Tesla merger talks put 30,000 bitcoin $BTC back in focus. A deal could be announced as soon as December, putting $2.1 billion of bitcoin under one roof, a sliver of what the combined group would be worth.

CoinDesk: https://www.coindesk.com/markets/2026/11/13/spacex-tesla-merger-talks-put-30-000-bitcoin-back-in-focus
```

- Tag: **none**. Picture: **text-only**: no real picture fits: not a price story, no public figure as the subject.
- Writer: the desk's own (outlet sentences; no model). Gates passed: attribution, claims, figures, hashtags, length, no_link, no_mentions, not_exclusive, not_rumour, tickers, urgency, wording; final gate (tickers, chart, claims, wording): passed.

### 8. Bitcoin Breaks Below $70,000 as Liquidations Sweep Through Leveraged Longs

- Post: https://t.me/Pricechatupdates/173 (2026-11-13 12:08:07 UTC)
- Sources: [Unchained](https://unchainedcrypto.com/bitcoin-breaks-below-70000-as-liquidations-sweep-through-leveraged-longs/) "Bitcoin Breaks Below $70,000 as Liquidations Sweep Through Leveraged Longs"
- Freshness: first reported 11:30 UTC, released 12:08 UTC (37 min); evidence: attributed

Before (exact, as shown in the channel):

```
JUST IN: Bitcoin Breaks Below $70,000 as Liquidations Sweep Through Leveraged Longs ($BTC -5.4% in 24H). More than $1B in crypto positions were forced shut in 12 hours as bitcoin $BTC broke below $70,000.

🔗 Unchained

@thepricechat
```

After, Telegram (exact HTML as sent):

```html
<b>JUST IN:</b> Bitcoin $BTC breaks below $70,000 as liquidations sweep through leveraged longs. More than $1 billion in crypto positions were forced shut in 12 hours as bitcoin broke below $70,000.

🔗 <a href="https://unchainedcrypto.com/bitcoin-breaks-below-70000-as-liquidations-sweep-through-leveraged-longs/">Unchained</a> · <a href="https://t.me/thepricechat">@thepricechat</a>
```

After, X:

```
JUST IN: Bitcoin $BTC breaks below $70,000 as liquidations sweep through leveraged longs. More than $1 billion in crypto positions were forced shut in 12 hours as bitcoin broke below $70,000.

Unchained: https://unchainedcrypto.com/bitcoin-breaks-below-70000-as-liquidations-sweep-through-leveraged-longs/
```

- Tag: **JUST IN:**. Picture: **chart (real BTC-USD 15-minute candles from Yahoo Finance at send time)**: a story about BTC-USD's price (headline leads with the asset and reports a move): real Yahoo 15m candles.
- Writer: the desk's own (outlet sentences; no model). Gates passed: attribution, claims, figures, hashtags, length, no_link, no_mentions, not_exclusive, not_rumour, tickers, urgency, wording; final gate (tickers, chart, claims, wording): passed.

### 9. U.N. watchdog agency pushes for access to key Iran nuclear site

- Post: https://t.me/Pricechatupdates/174 (2026-11-13 16:02:05 UTC)
- Sources: [CBS News](https://www.cbsnews.com/live-updates/us-iran-war-trump-nuclear-material-no-deal/) "U.N. watchdog agency pushes for access to key Iran nuclear site"; [Al Jazeera](https://www.aljazeera.com/news/2026/11/13/iran-says-it-struck-us-radar-site-in-bahrain) "US denies Iran’s claim it hit two radar sites in Bahrain"; [BBC](https://www.bbc.co.uk/news/articles/cm75261e3pdko) "UN watchdog 'increasingly concerned' over uninspected Iran nuclear site"
- Freshness: publication time not on the outlets' pages: the desk's own label on the before-post is carried over; evidence: two outlets

Before (exact, as shown in the channel):

```
U.N. watchdog agency pushes for access to key Iran nuclear site. As Trump signals he may let the Iran war fester, the U.N. nuclear agency is "increasingly concerned" about access to a key uranium facility, according to a leaked report.

🔗 CBS News · Al Jazeera · BBC

@thepricechat
```

After, Telegram (exact HTML as sent):

```html
U.N. watchdog agency pushes for access to key Iran nuclear site.

🔗 <a href="https://www.cbsnews.com/live-updates/us-iran-war-trump-nuclear-material-no-deal/">CBS News</a> · <a href="https://www.aljazeera.com/news/2026/11/13/iran-says-it-struck-us-radar-site-in-bahrain">Al Jazeera</a> · <a href="https://www.bbc.co.uk/news/articles/cm75261e3pdko">BBC</a> · <a href="https://t.me/thepricechat">@thepricechat</a>
```

After, X:

```
U.N. watchdog agency pushes for access to key Iran nuclear site.

CBS News: https://www.cbsnews.com/live-updates/us-iran-war-trump-nuclear-material-no-deal/
```

- Tag: **none**. Picture: **text-only**: no real picture fits: not a price story, no public figure as the subject.
- Writer: the desk's own (outlet sentences; no model). Gates passed: attribution, claims, figures, hashtags, length, no_link, no_mentions, not_exclusive, not_rumour, tickers, urgency, wording; final gate (tickers, chart, claims, wording): passed.

### 10. China, US discuss tariffs, farm trade ahead of Xi-Trump meeting

- Post: https://t.me/Pricechatupdates/176 (2026-11-14 08:22:16 UTC)
- Sources: [CNBC](https://www.cnbc.com/2026/11/14/china-us-tariffs-xi-trump-meeting.html) "China, US discuss tariffs, farm trade ahead of Xi-Trump meeting"
- Freshness: first reported 08:16 UTC, released 08:22 UTC (6 min); evidence: attributed

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

- Tag: **JUST IN:**. Picture: **text-only**: no real picture fits: not a price story, no public figure as the subject.
- Writer: the desk's own (outlet sentences; no model). Gates passed: attribution, claims, figures, hashtags, length, no_link, no_mentions, not_exclusive, not_rumour, tickers, urgency, wording; final gate (tickers, chart, claims, wording): passed.


## Tests

New tests:

- `backend/tests/test_newsdesk_wire.py`: **45** tests covering tags, captions, the X post, sentence count, casing, one cashtag per asset,
  flags, the wording gate, the final gate with and without wire, punchy rewrites that still get blocked, model drafts refused, charts and
  photos, text-only posts, redraws, ranking and the policy switch.
- `backend/tests/test_newsdesk_wire_e2e.py`: **11** tests:
  - two outlets, a breaking release, a text-only Telegram message and an X post with no media;
  - switching back to clean;
  - exceptional moves break only in wire, with four refusal cases;
  - the rolling and daily caps, and the gap;
  - a material update breaking;
  - filler ranked below a hack story, and held under the bar.

These tests prove that **a punchy rewrite is still blocked**:

- `test_a_punchy_rewrite_with_an_unsupported_figure_ticker_superlative_or_name_is_still_blocked`: an invented 9% and $3B, an invented
  `$BNB`, "biggest drop ever", and a name nobody reported.
- `test_the_models_punchy_draft_is_refused_and_the_sources_words_go_out`: a model draft with an invented figure, a superlative,
  "Massive" or a Binance BNB ticker is refused, and the outlet's words go out.
- `test_a_chart_the_sources_dont_establish_is_still_blocked`: a BNB chart beside a Binance story.

Results on this PC:

- News Desk: `pytest tests/test_newsdesk*.py tests/test_x_follows_telegram.py` gives **573 passed**. That is the 517 existing tests (514 News Desk
  plus 3 in test_x_follows_telegram), all still passing, plus the 56 new ones.
- Full backend suite: **2639 passed, 6 failed** (9 min 26 s). Five are the known unrelated failures on this PC: test_community,
  test_lunch_bot, test_trading_execution, test_trends and test_tg_trading. The sixth,
  `test_kalshi_links.py::test_bot_activity_is_public_without_the_owners_account`, depends on test order: the file passes on its own (8 passed), and it touches no News Desk code. The full run came before the last small
  change (a photo only when the person leads the headline) and before line endings were normalised. The News Desk run above came after both.
- Frontend: `npx vitest run` gives 58 files and 504 tests passed (the NetworkSwitches test now covers wire, clean and classic). `npx tsc -b`
  is clean.
- Worker: unchanged, so no worker tests were needed.

## Deploy (when the owner approves)

1. Merge `feat/newsdesk-wire` into main. It is based on 1374981; main is 1374981's parent, so this is a fast-forward chain.
2. **Backend:** Railway, from a linked worktree's `backend/`. Run `railway link -p c2a48c3b… -e production -s api` and check
   `railway status` first. Never run it from C:/AI/price-chat or an unlinked directory.
3. **Frontend:** Vercel, for the Admin three-way switch. The backend works without it: the old two-way switch would show "Classic" for
   wire and can still set clean or classic.
4. There is **no migration** (the style is a JSON state key) and **no worker restart** (worker code is unchanged).
5. Check afterwards: `railway logs <deployment> --filter "newsdesk OR tgnews"`. The next slot's post should be one or two sentences with
   the footer line. `facts.visual.layout` is `text` or `chart` for most posts.

## Rollback

- **Instant, no deploy:** in Admin, Post style → Use clean (or classic). It is the same as
  `PUT /x/newsdesk/network {"post_style":"clean"}`. The breaking lane goes back to 2 a day with no market or update breaking, and the
  ranking goes back to the old one.
- **Code:** redeploy 1374981. There is no migration to undo. Events posted in wire keep `facts.visual.style = "wire"`. On 1374981,
  `_style_of` reads an unknown style as classic, so if X still owes a wire post it would draw a classic card for it. X only follows
  Telegram for a few hours after delivery, so this can only matter for posts made just before the rollback.

## Risks

- **Default flip.** Deploying makes wire live at once unless Admin has an explicit setting.
- **Fewer pictures.** Most news posts become text-only. That is the owner's intent ("don't force an image"), but engagement on X may
  change. X still gets the same words and link.
- **More breaking.** Up to 3 a day instead of 2, plus measured moves and updates. The caps are hard, but the owner should watch the first
  days.
- **Filler regex.** "could", "might" and "set to" also appear in real news ("Fed set to cut"). The penalty is 8 points, so a strong story
  still clears the bar. Watch the Admin Decisions table for "filler or speculation".
- **Mixed-case headlines.** Casing uses the reporting's own forms. When the reporting never repeats a capitalised common noun, it is
  lower-cased as before; only countries, capitals and leaders are protected. This is cosmetic and never changes a fact.
- **Wording gate on outlet headlines.** An outlet's own "Massive selloff…" with no figure is now held in wire, so that story won't post.
  This is intended.
