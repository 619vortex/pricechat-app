# Website & user experience (UX-n)

Reviewer: website / UX. Code read at audit worktree (= main e978cc3). Live checks: GETs only against https://pricechat.live, 2026-11-08.
Screenshots: `docs/audits/price-chat-full-audit/screenshots/ux-<page>-390.png` (true 390x844 mobile emulation via CDP) and `ux-<page>-1440.png` (1440x900).

## Summary

The public site is technically solid: type check clean, 460/460 unit tests pass, build is code-split per page, no horizontal overflow at 390px on the 8 key pages, focus-visible styles, reduced-motion handling, alt attributes on every `<img>`, and a good OG-preview server for social bots. The problems are product and discovery, not code quality.
1. **SEO:** every URL (27 pages, 465 market URLs, 25,000+ card URLs in the sitemap) serves the same 2,252-byte empty shell titled "Price Chat" to Googlebot and Bingbot. The backend already builds the right per-page title, description and canonical, but serves them only to social bots.
2. **The home page doesn't sell anything.** The hero is a slogan with no call to action. The first sign-up button is about 3,000px down on mobile. The strongest product (the Pokémon market, also the main SEO asset) has no link anywhere in the header, mobile menu or footer. The VPN tab is hidden from signed-out visitors.
3. **Claims the product doesn't keep:**
   - "$9.99 for 30 days" for the VPN: live payments are off.
   - "every rank unlocks a reward": 4 of 10 ranks have none or are "TBA".
   - Earn: the rewards ladder says Earn unlocks at Scout, but it is open to everyone.
   - The legal pages are publicly marked "Draft: not had final legal review", and the privacy page leaves out wallets, trading keys, Kalshi and the chat feed.
4. **Branding is split** across four visual systems:
   - green / Archivo on the main site
   - lime on the VPN pages
   - purple-teal on Earn
   - a light theme on the Pokémon market, under a dark header
5. **Biggest opportunities:**
   - Serve the server-built heads, plus a short text summary, to all crawlers (a small change, large SEO gain).
   - Give the home page a real value proposition with product tiles and a CTA.
   - Add product links to the nav and footer.
   - Make the website's claims match the live settings.

## Findings

### UX-1: Search engines get an empty shell for every page; the per-page heads exist but only social bots get them
Severity: High · Status: CONFIRMED · Effort: M
Evidence:
- `curl -A Googlebot https://pricechat.live/pokemon/cards/5` gives `<title>Price Chat</title>`, 2,252 bytes, no canonical and no content.
- The same URL with `-A Twitterbot/1.0` gives `<title>Blastoise Unlimited · 1999 Base Set 2/102 · PSA 10 Price · Price Chat</title>`, a card-specific description and `<link rel="canonical">`.
- All 20 key pages fetched with a browser user agent were byte-identical (2,252 B).
- `vercel.json` sends only TelegramBot, Twitterbot, facebookexternalhit, Slackbot, Discordbot and similar bots to `/api/website/seo/og/...`. The regex has no Googlebot, bingbot, Applebot, DuckDuckBot, GPTBot, PerplexityBot or ClaudeBot.
- The sitemap index lists `pages-1` (27 URLs), `market-1` (465), `profiles-1` and `cards-1..3` (25,000 in cards-1 alone).
- All content and titles come from client JS (`useHead.ts`), and canonical is added only after `/api/website/home` answers.

Impact: Google can render JavaScript, but rendering 50k+ JS-only URLs on a young domain is slow and budget-limited. Bing renders partly, and AI search crawlers don't render at all. The Pokémon market is the site's only large organic-traffic asset, and today its pages look identical before rendering.

Recommended fix: render the same heads for every request.
- Option (a): a Vercel Edge Middleware or Function fetches `/api/website/seo/og/<path>` (already built and tested against `seoHeads.golden.json`) and injects its `<title>`, meta, canonical and JSON-LD into `index.html`. It also adds a `<noscript>` or visually hidden text summary (card name, price, grade table).
- Option (b): prerender the 27 fixed pages at build time (for example `vite-plugin-prerender` or a small Puppeteer step).
- Longer term: server-rendered card and set pages. Comparable product: PriceCharting and Collectr serve full HTML card pages.
- Remove the `<meta http-equiv=refresh>` when serving these heads to search bots, because that tag is meant for social-preview bots.

### UX-2: Card link previews use a relative og:image, so shared card links show no picture
Severity: Medium · Status: CONFIRMED · Effort: S
Evidence: `curl -A Twitterbot/1.0 https://pricechat.live/pokemon/cards/5` returns `<meta property="og:image" content="/cards/u/b5292d9dcf0b2022.webp">`. `backend/app/api/routes/seo.py:149` uses `image = found.get("image") or f"{base}/og.jpg"`, and the card's `image_url` (`seo/heads.py:157`) is a site-relative path that is never made absolute.
Impact: X, Telegram, WhatsApp, Discord and Facebook need an absolute URL, so card shares, the most viral unit of the market, get no image. The `.webp` format is also not accepted by every unfurler (Facebook, LinkedIn).
Fix: in `link_preview`, prefix `base` when the image starts with `/`. Prefer a 1200x630 JPG/PNG card share image (card scan plus price on a branded background, like the Vercel OG image pattern). Add a test for an absolute og:image.

### UX-3: The home page has no value proposition or call to action above the fold, and sign-up is buried
Severity: High · Status: CONFIRMED · Effort: M
Evidence:
- `Website.tsx:362-389`: the Hero is a mascot, the H1 "Everything has a price." and a support line that repeats it (`websiteData.ts:29` `support: ["Markets. Money. Culture.", "Everything has a price."]`). There is no button.
- Section order: `Website.tsx:41-48` (Hero, Latest, Story, Community).
- The only "Join Price Chat" button is in `JoinCta`, rendered inside Community at the bottom (`Website.tsx:800-824`). The mobile home page is 4,629px tall (CDP measurement).
- The header offers "Login", not "Join" (`Website.tsx:336-342`).
- Screenshots: `ux-home-390.png`, `ux-home-1440.png`.

Impact: a first-time visitor can't tell what Price Chat gives them: a free trading bot, a VPN, a Pokémon price tracker, an XP passport. The funnel from visitor to Passport to Earn, VPN or market depends on people scrolling past three videos and an "Our story" essay.
Fix:
- Hero: one sentence saying what you get, a primary CTA ("Get your free Kiwi Passport") and a secondary one ("Explore the Pokémon market").
- Directly below, a row of four product tiles (Pokémon Market, Earn, Kiwi VPN, Giveaway), each with a live number (cards tracked, paper P&L, servers, packs opened).
- Rename the header "Login" to "Join / Sign in".
- References: Robinhood's and Linear's homepages put the value line and CTA in the first viewport. Duolingo puts "Get started" in the hero.

### UX-4: The Pokémon market, the biggest product and SEO asset, has no link from the main site's navigation or footer
Severity: High · Status: CONFIRMED · Effort: S
Evidence:
- The desktop nav (`Website.tsx:262-280`) shows Latest and About, plus category links only when content exists (live `categories: []`), plus Giveaway, Earn (flag on) and VPN (signed-in only).
- The mobile menu (`Website.tsx:296-316`) adds Leaderboard. Footer links (`websiteData.ts:82-90`) are About, Contact, Disclaimer, Terms and Privacy only.
- `grep 'href="/pokemon'` over `src/website` finds only `giveaway.tsx:425`.
- Live: the desktop nav on `ux-home-1440.png` reads LATEST · ABOUT · GIVEAWAY · EARN.

Impact: visitors never discover the market, and search engines see no internal links into the 25k card pages except through the sitemap.
Fix: add "Markets" (→ `/pokemon`) to the header and mobile menu, and a product column in the footer (Pokémon Market, Vault, Earn, Kiwi VPN, Giveaway, Leaderboard, Kiwi Passport). Show Leaderboard on desktop too.

### UX-5: The Kiwi VPN tab is hidden from signed-out visitors, although /vpn is built to sell to them
Severity: Medium · Status: CONFIRMED · Effort: S
Evidence: `Website.tsx:252-254` has `const showVpn = !!member || onVpn; // Kiwi VPN is a member perk: signed-in members only`. Yet `/vpn` shows "Start Free Trial — 30 Days" to anonymous visitors (`ux-vpn-390.png`), the home page VpnPerk links to it for everyone (`Website.tsx:695-715`), and it is in the sitemap.
Impact: an acquisition page with no navigation entry point. It is also inconsistent: the product is marketed publicly but hidden in the nav.
Fix: show the VPN tab to everyone, or decide it is members-only and say so on `/vpn`.

### UX-6: The website promises "$9.99 for 30 days", but payments are switched off in production
Severity: High · Status: CONFIRMED · Effort: S
Evidence:
- `GET /api/members/vpn/public` returns `{"enabled":true,"payments":false,"trial":true,...}`.
- The claim appears in the home VpnPerk ("Then $9.99 for 30 days, renewed by hand", `Website.tsx:698`), in the SEO and OG description for `/vpn` (`seo.ts` PAGE_HEADS.vpn, `backend/app/seo/heads.py:34`) and on the /vpn pricing section.
- The backend message after the trial: `vpn/service.py:46` `PAID_UNAVAILABLE = "Your free trial has ended. The $9.99 plan isn't available yet."` The UI shows "USDT payments open soon." (`vpn.tsx:867`) only once you are inside the billing panel.

Impact: users start a 30-day trial expecting to be able to continue. When it ends they can't pay and lose service. This is a trust and consumer-protection problem.
Fix: while `payments=false`, render "Free during beta, paid plan coming" instead of the price. Drive the price copy from `/members/vpn/public` everywhere, including the SEO description.

### UX-7: "Every rank unlocks a reward" is not true
Severity: Medium · Status: CONFIRMED · Effort: S
Evidence: `websiteData.ts:62` (home joinNote) and `community.tsx:82` (join page "every rank has a reward"), both locked in by tests (`community.test.tsx:80,144`). The backend catalog `members/rewards.py:30-47` gives egg `kind="none"`, and golden, legend and mythic `kind="tba"` ("Exclusive reward — To Be Announced").
Impact: 4 of 10 ranks, including the top three that members grind toward, have no reward. This is a broken promise to the most engaged users.
Fix: change the copy to "rewards from Kiwi Chick up", or define the three top-tier rewards before advertising them.

### UX-8: Earn gating copy contradicts itself (Scout reward vs "free for every Passport member")
Severity: Low · Status: CONFIRMED · Effort: S
Evidence: the live `GET /api/members/trading-bot` returns `rules.min_rank_level: 1`. The `/earn` hero says "FREE · EVERY KIWI PASSPORT MEMBER" (`ux-earn-390.png`). The rank-rewards ladder still lists Scout as "UNLOCK EARN — Your Kiwi Trading Bot" (`backend/app/members/rewards.py:36`), and `tradingbot.tsx:349-350` keeps a "Reaching Scout Kiwi unlocks the trading bot" branch.
Impact: the Passport rewards screen tells members they must grind to Scout for something they already have, which devalues the rank ladder.
Fix: pick one rule. If Earn is free for all, give Scout a different reward.

### UX-9: The legal pages are publicly marked as unreviewed drafts and are incomplete for what the product does
Severity: High · Status: CONFIRMED · Effort: M (legal input needed)
Evidence: `community.tsx:418-420` renders "Draft: this page has not had final legal review yet." on Privacy, Terms, Disclaimer, Contact and VPN privacy. The privacy text (`community.tsx:302-331`) lists only Google, X and Telegram. Not covered:
- wallet sign-in addresses (`wallet.tsx`, "Sign in with wallet" on /join)
- Hyperliquid API-wallet keys for the trading bot
- the Kalshi account connection (`/members/kalshi/connect`)
- email sign-in
- the homepage chat bubble that republishes Telegram members' messages under their first names (`api/routes/website.py:320-360`)
- localStorage caching

Terms has no operator entity, governing law, liability limit or refund terms, although the site takes USDT payments ("can't be refunded", `vpn.tsx:960`). The Disclaimer says "If we ever feature partners or affiliate links, we'll say so clearly", while the Earn funnel is built on a Hyperliquid referral (code BANTER, `models/tradingbot.py:23-24`). Contact gives no email address, only Telegram and X.

Impact: regulatory and trust risk, especially for VPN billing, trading API keys and the Chrome Web Store / Edge listing, which require an accurate privacy policy.
Fix: get the documents reviewed. Name the operating entity and a contact email. Add the missing data categories. Remove the "Draft" banner only after review. Update the Disclaimer to state the referral relationship.

### UX-10: Four visual systems; the market switches to light theme under a dark header
Severity: Medium · Status: CONFIRMED · Effort: M
Evidence:
- Main site: `--site-green: #00e58f`, Archivo uppercase display (`website.css:1-20`).
- VPN: overrides the brand token, `--site-green: var(--vpn-lime)` = `#a3e635` (`vpn.css:7-25`), with title-case buttons.
- Earn: purple and teal gradient H1 and purple pills (`ux-earn-1440.png`).
- Pokémon market: follows the OS theme (`pages/pokemon/mx.tsx:105-128`) and renders a white page under the site's black header (`ux-pokemon-1440.png`, `ux-pokemon-390.png`), while `index.html` declares `color-scheme: dark`. It also uses different button language (gradient "Shop the Vault" pills).

Impact: the site reads as several stitched-together products, which weakens brand recall and trust. That matters for a brand asking people to connect trading keys and pay for a VPN.
Fix: one token set (brand green, one accent per product at most), shared button and pill components, and dark as the default market theme (light only on explicit toggle). Write a one-page brand sheet. Reference: Stripe and Revolut keep one system across very different products.

### UX-11: The chat bubble shows a "9+" unread badge to every first-time visitor and covers CTAs on mobile
Severity: Medium · Status: CONFIRMED · Effort: S
Evidence: `chatBubble.tsx:62,83` reads `seen` from localStorage (default 0), so every message counts as unread on a first visit, and the badge shows "9+" (`chatBubble.tsx:155`). It appears on every screenshot. On `ux-join-390.png` the fixed bubble overlaps the "Sign in with wallet" button. On mobile it sits over the bottom-right of all pages.
Impact: a fake-urgency badge, and it is mounted in the header on every page, so it competes with the primary CTA. It also republishes unmoderated Telegram text (raw text, up to 600 characters, links included; live sample: 1 of 40 messages contained a link) on the brand's homepage.
Fix: on a first visit, initialise `seen` to the newest message (no badge), or show only a "live" dot. Hide the bubble on /join, /passport and checkout-like screens, or move it above the safe area. Strip links and run the same moderation as the Telegram bot before publishing.

### UX-12: Home page layout shift (CLS 0.12-0.35)
Severity: Medium · Status: CONFIRMED (value) / SUSPECTED (cause) · Effort: S-M
Evidence: CDP run at 390x844 with 4x CPU throttling, latency 150ms, cache and localStorage cleared:
```
/        fcp 1084 lcp 1528 cls 0.35   (repeat: cls 0.122, lcp 3156)
/pokemon fcp 2772 lcp 3676 cls 0.088
/earn    fcp 1068 lcp 1296 cls 0.088
/vpn     fcp 1160 lcp 1424 cls 0.026
```
The layout-shift sources were `site-section`, `site-display site-hero-title` and `site-section site-story`. The likely causes are (a) the Archivo web font swapping in and reflowing the giant hero title, and (b) sections appearing once `/api/website/home` resolves (the Latest skeleton is one feature tile, but the real section adds rows).
Impact: Google's "Poor" threshold is CLS > 0.25. It hurts Core Web Vitals and mobile feel.
Fix:
- Preload the Archivo woff2 (latin) and use `size-adjust` / `ascent-override` on the fallback font.
- Reserve the Latest section's real height in the skeleton.
- Avoid inserting sections above content after data arrives.
- For /pokemon (FCP 2.8s throttled), split `lightweight-charts` (183 KB) off the market home if it is not needed above the fold.

### UX-13: The Earn sub-pages depend on slow uncached endpoints
Severity: Low · Status: CONFIRMED (single sample) · Effort: S
Evidence: `GET /api/members/arbitrage/lunch` took 5.15s (34 KB) and `/api/members/arbitrage/funding` 3.19s (32 KB). Other endpoints answer in about 0.45s.
Impact: about 5s of spinner on /earn/lunch on any network.
Fix: cache the response server-side for 15-30s (the data is public and shared), or precompute it in the scanner job.

### UX-14: The "Earn" tab showcases a strategy losing money, with no framing
Severity: Medium · Status: CONFIRMED · Effort: S
Evidence: the live `GET /api/members/arbitrage/lunch` stats show `trades 25975, win_rate 13.6, pnl_sol -421.188` (paper). It is listed under the "Earn" nav tab as "Lunch Bot · pump.fun launches · paper" (`arbitrage.tsx:12`). The trading bot's public track record returns `{"configured":false}`.
Impact: a tab named "Earn" leads with a memecoin bot down 421 SOL on paper, and the flagship bot has no public track record. That undercuts the trading bot's credibility and invites regulatory attention, because "Earn" next to trading products implies returns.
Fix: rename the section "Bots" or "Lab". Lead with the trading bot's audited paper record once configured. Move experiments with negative expectancy behind a "Research" label, or hide them until they are positive after costs.

### UX-15: "Pack Giveaway" in the top nav awards no prizes, and is labelled a test feature
Severity: Low · Status: CONFIRMED · Effort: S
Evidence: the nav label "Giveaway" is highlighted in pink on every page (`Website.tsx:268`). The page opens with "⚠️ Just for fun: test feature… you don't win real cards or prizes" (`ux-giveaway-390.png`). It uses official Pokémon booster-pack artwork.
Impact: "Giveaway" sets a prize expectation the page immediately withdraws. A test feature gets a top-nav slot while the market gets none (UX-4). The pack art is an IP exposure (for the legal reviewer).
Fix: rename it ("Open Packs" or "Pack Opening") and move it under Markets, or keep it but drop "test feature" once it's stable.

### UX-16: Social-proof counters show very small audiences prominently
Severity: Low · Status: CONFIRMED · Effort: S
Evidence: live `/api/website/home` `growth`: YouTube 43 subscribers, X 56 followers, Telegram 506. These are rendered as animated counters with sparklines in the Community section (`Website.tsx:640-680`, `JoinStat`).
Impact: "43 subscribers" next to a "Join the chat" CTA is negative social proof. The Telegram growth (+254% in 30 days) is the only number worth showing.
Fix: show a counter only above a threshold (for example 1,000) or only when growth is strong, and lead with the Telegram number.

### UX-17: Legacy /website/* URLs are still linked internally and served as duplicates (no 301)
Severity: Low · Status: CONFIRMED · Effort: S
Evidence: internal links still point to `/website/leaderboard` (`Website.tsx:313, 826`), `/website/join` (`Website.tsx:338, 822`) and `/website/terms|privacy|disclaimer|contact` (footer, `websiteData.ts:82-90`). `main.tsx:67-77` renders the same pages at both paths. The legal pages exist only under `/website/*`: `/privacy` and `/terms` return 404 (live).
Fix: move the legal pages to `/privacy`, `/terms`, `/disclaimer` and `/contact`. Add Vercel `redirects` (301) from all `/website/*` and `/trading-bot` paths. Update the internal links and sitemap.

### UX-18: useHead appends a second set of OG and Twitter tags instead of replacing the static ones
Severity: Low · Status: CONFIRMED · Effort: S
Evidence: `useHead.ts:39-58` replaces only `meta[name=description]`. The static `og:title`, `og:description`, `og:image`, `og:url` and `twitter:*` from `index.html:15-29` stay first in the DOM, and per-page tags are appended after them. The rendered DOM of every page therefore carries two `og:title` and two `og:image` tags, and the generic home ones come first.
Impact: renderers and validators that read the first occurrence see the home card for every page. Only Google's rendered view is affected, because social bots get the backend version.
Fix: update the existing static tags in place (as done for the description), or remove them on mount.

### UX-19: Thin, members-only and duplicate URLs in the sitemap
Severity: Low · Status: CONFIRMED · Effort: S
Evidence: `/learn` is in `sitemaps/pages-1.xml`, but anonymous visitors (and crawlers) see only "Sign in to your Kiwi Passport to see your rewards" (`ux-learn-390.png`, page height 851px). `/join`, `/earn/brain` (its API returns 401 when signed out) and similar are also listed.
Fix: either give these pages public teaser content (lesson titles, a sample brain readout) or drop them from the sitemap and mark them noindex.

### UX-20: Housekeeping (dead weight and clutter)
Severity: Low · Status: CONFIRMED · Effort: S
- `public/website/team/adam.png` (2.1 MB) and `kiwi.png` (1.97 MB) are unreferenced. Only the `-640/-1200.webp` versions are used (grep across `src` and `backend/app`).
- `public/downloads/KiwiVPN-0.1.0-rc1.apk` (15.5 MB) and `KiwiVPN-Setup.exe` (3.6 MB) are tracked in git and redeployed with every frontend build (`dist` is 105 MB, of which `cards` is 76 MB). Serve binaries from GitHub Releases or R2.
- There are three TikTok verification meta tags in `index.html` plus six verification `.txt` files (root and `/website/`). Keep one.
- The footer shows "Disclaimer" twice (link row and copyright line, `Website.tsx:875-892`).
- There is no "skip to content" link. The header has up to 10 tab stops before main content.
- The global dashboard stylesheet `src/styles.css` (31 KB, `index-*.css`) is loaded by every public page because `main.tsx:18` imports it at the top level.

## Untested / couldn't verify (and why)
- Signed-in flows (Passport, Earn onboarding steps, VPN device creation, pack opening, wallet sign-in): the brief forbids logins and form submits, so I assessed them only from code and the signed-out views.
- Real Google index coverage and Search Console data: no access. Whether Google renders the JS card pages well is UNVERIFIED. UX-1 is about what crawlers get before rendering.
- Real-device Safari and Android checks. The 390px shots are Edge/Chromium with mobile emulation through CDP. Plain `--window-size=390` headless is clamped to 492px wide (measured `innerWidth=492`), so I discarded those shots and redid them through `Emulation.setDeviceMetricsOverride`.
- Lighthouse and axe scores (not installed). Contrast was spot-checked by hand: `--site-muted #858d88` on `#050706` is about 5.9:1 (passes AA). The VPN lime on dark passes. The market light theme was not checked in detail.
- Screen-reader behaviour of the modals (focus trap and return) was not exercised.
- Who ultimately receives the Hyperliquid referral fee for code BANTER: code says it is the owner's referrer wallet (`tradingbot/affiliate.py:30`). The naming overlap with the "From Banter to Price Chat" story is worth a business sanity check.

## Improvement & feature ideas (ranked)
1. **Edge-injected heads plus a text summary for all crawlers** (UX-1). This is the highest return for the effort: the backend builders and golden tests already exist, and 25k+ card pages become indexable on first fetch. Then add `/pokemon/sets/<set>` and `/pokemon/years/<year>` as real paths instead of `?set=`, because query-string landing pages are weaker for SEO.
2. **Rebuild the first viewport of the home page** (UX-3/4/5): a value line, a "Get your free Kiwi Passport" CTA and four live product tiles. Reference: Robinhood, Linear and Duolingo all put the promise and CTA in the first viewport. Measure it with the existing `/api/r/<placement>` click tracking.
3. **One onboarding path after sign-in.** A three-step checklist on the Passport (pick interests: markets, trading or VPN; claim the first XP; open the matching product), like Duolingo's or Notion's first-run checklist, instead of dropping new members on a dense passport.
4. **Make the copy follow the live config** (UX-6/7/8): price, trial, rank rewards and Earn gating come from the API, so the site can't promise what is switched off. Add a test that fails if hard-coded "$9.99" appears while `payments=false`.
5. **Share cards built for virality** (UX-2): a 1200x630 card image with price, 30-day change and the Kiwi. Add a "Share" button on card pages and the pack-opening result. A Pokémon pull showing what the card is worth is the most shareable moment the product has.
6. **Unify the design system** (UX-10): one token file, one button and pill set, dark market by default. Keep the per-product accent to a single highlight colour.
7. **Trust layer for Earn**: a public, audited paper-trading record for the Kiwi bot (the endpoint exists but returns `configured:false`), a clear risk box at the top rather than the bottom, and renaming "Earn" to avoid implying returns (UX-14).
8. **Finish the legal pages** (UX-9) and add a real support email. This is required before scaling paid VPN or the store extension.
9. Performance polish (UX-12): font preload with fallback metrics, a reserved skeleton height, chart lib split, and a cached Lunch / Funding API (UX-13).

## Commands run (for the evidence appendix)
```
# setup / quality gates (audit worktree frontend)
npm ci --no-audit --no-fund                     -> ok
npx tsc -b                                      -> exit 0 (no errors)
npx vite build                                  -> ok, 3.3s; largest chunks: index (react/router) 270.5 KB / 86 KB gz,
                                                   lightweight-charts 183 KB, index-RBf 150.6 KB, AdminApp 120.8 KB (lazy), passport 98 KB
npx vitest run                                  -> Test Files 50 passed (50), Tests 460 passed (460), 11.96s
# home route payload estimate from dist: entry js+css + Website chunk deps = ~491 KB raw (excl. fonts/images)

# live HTML (GET, browser UA) for / /earn /vpn /pokemon /giveaway /content /join /leaderboard /learn /passport /website/privacy
#   /earn/kalshi /trading-bot /pokemon/vault /u/test -> all 200, 2252 bytes, identical shell; /terms /privacy /nonexistent-xyz -> 404 (404.html)
curl -A "Twitterbot/1.0" https://pricechat.live/{earn,vpn,giveaway,passport,/}   -> per-page heads for social bots
curl -A Twitterbot   https://pricechat.live/pokemon/cards/5 -> card title, canonical, og:image="/cards/u/b5292d9dcf0b2022.webp" (relative)
curl -A Googlebot    https://pricechat.live/pokemon/cards/5 -> <title>Price Chat</title>
curl https://pricechat.live/sitemap.xml, /sitemaps/{pages-1,market-1,profiles-1,cards-1}.xml -> 27 / 465 / 2 / 25000 URLs
# crawl: 77 GETs (all 27 sitemap pages, 15 market, 10 profiles, 15 cards, 18 legacy/static/internal targets) at 0.3s spacing -> 77 x 200
# downloads: GET with Range 0-0 on /downloads/{KiwiVPN-Setup.exe,KiwiVPN-0.1.0-rc1.apk,kiwi-vpn-extension.zip} -> 206
GET /api/website/home        -> latest 3, trending/originals/price_board/categories empty, growth yt 43 / x 56 / tg 506, trading_bot true
GET /api/members/vpn/public  -> {"enabled":true,"test_mode":false,"payments":false,"trial":true,"free":false,"billing":null}
GET /api/members/trading-bot -> rules.min_rank_level 1, referral_code BANTER
GET /api/members/trading-bot/track-record -> {"configured":false}
GET /api/members/arbitrage/lunch  -> 5.15s; stats trades 25975, win_rate 13.6, pnl_sol -421.188
GET /api/members/arbitrage/funding -> 3.19s
GET /api/website/chat -> 40 messages, 1 containing a link (contents not printed)

# screenshots: Edge (installed 156/157) headless; 1440x900 via --window-size; 390x844 via CDP Emulation.setDeviceMetricsOverride(mobile:true)
#   scripts in session scratchpad (cdpshot.mjs, cdpperf.mjs); overflow check: scrollWidth==innerWidth==390 on all 8 pages
# web vitals: CDP, 390x844 dpr2, CPU x4, latency 150ms, cache+localStorage cleared, PerformanceObserver LCP/CLS
```
Screenshots: `screenshots/ux-{home,earn,vpn,pokemon,giveaway,join,leaderboard,learn}-390.png`, `screenshots/ux-{home,earn,vpn,pokemon,giveaway,join}-1440.png`.
