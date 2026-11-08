# Price Chat: UX and design review

Audit of 2026-11-08. This review is organised page by page and platform by platform. It draws on these notes:

- `notes/website-ux.md` (UX-*): live GETs plus CDP screenshots.
- `notes/passport-gamification.md` (PP-*)
- `notes/telegram.md` (TG-*)
- `notes/newsdesk-content.md` (ND-*)
- `notes/kiwi-vpn.md` (VPN-*)
- `notes/architecture-admin.md` (AR-*)

Screenshots are in `screenshots/`. Mobile shots (`-390`) used true 390x844 emulation through CDP. Desktop shots (`-1440`) are 1440x900. Signed-in flows were not exercised, because the brief forbids logins. They were assessed from code.

**Overall:** the public site is technically sound:
- tsc is clean and vitest passes 460/460;
- the build is code-split per page;
- there is no horizontal overflow at 390 px on 8 key pages;
- focus-visible styles and reduced-motion handling are in place;
- every `<img>` has an alt attribute;
- muted text contrast is about 5.9:1 (AA).

The problems are discovery, honesty of claims, and brand consistency (UX Summary).

---

## 1. Cross-cutting issues

| Theme | Problem | Fix | IDs |
|---|---|---|---|
| **Claims vs live config** | "$9.99 for 30 days" while payments are off. "Every rank unlocks a reward" while 4 of 10 have none. Earn "unlocks at Scout" while it is free for all. "Reply and repost without leaving your Passport" while X write is off. Email called "the website's sign-in" while it is off | Drive price, trial, rank rewards, Earn gating and X features from the live API (`/members/vpn/public`, providers, rewards). Add a test that fails if "$9.99" is hard-coded while `payments=false` | UX-6, UX-7, UX-8, PP-14 |
| **Four visual systems** | Main site: green #00e58f with Archivo uppercase. VPN: lime #a3e635, title case. Earn: purple and teal gradients. Pokémon market: light OS theme under a dark header, gradient pills | One token file, one accent per product at most, shared button and pill components, dark market by default, a one-page brand sheet (Stripe and Revolut as references) | UX-10 |
| **Discovery** | No Markets link anywhere. VPN tab hidden when signed out. Leaderboard shown only on mobile. Footer links only to About, Contact and the legal pages | "Markets" in the header and mobile menu, a footer product column (Pokémon Market, Vault, Earn, Kiwi VPN, Giveaway, Leaderboard, Kiwi Passport), VPN for everyone | UX-4, UX-5 |
| **SEO** | Search crawlers get an identical 2,252-byte shell. Relative og:image. Duplicate OG tags. Thin and members-only sitemap URLs. Legacy `/website/*` duplicates | Edge-inject the server-built heads plus a text summary. Absolute 1200x630 share images. Update tags in place. noindex the members-only pages. 301 redirects | UX-1, UX-2, UX-17, UX-18, UX-19 |
| **Accessibility** | No skip link, and up to 10 tab stops before the main content. The modal focus trap was not exercised. The market's light theme contrast was not checked | Add a skip link, check modal focus, run axe or Lighthouse | UX-20, UX Untested |

---

## 2. Website, page by page

### Home `/` (screenshots: `ux-home-390.png`, `ux-home-1440.png`)
- **Hero has no offer or CTA (UX-3).**
  - The hero is a mascot, the H1 "Everything has a price." and a support line that repeats it (`Website.tsx:362-389`, `websiteData.ts:29`).
  - The only "Join Price Chat" button is at the bottom, inside Community (`Website.tsx:800-824`). The mobile page is 4,629 px tall, so that button is about 3,000 px down.
  - The header says "Login" rather than "Join".
  - Improvements:
    - One sentence saying what you get.
    - A primary CTA "Get your free Kiwi Passport" and a secondary "Explore the Pokémon market".
    - Below it, four live product tiles: Pokémon Market (cards tracked), Earn (paper P&L), Kiwi VPN (servers), Giveaway (packs opened).
    - Rename the header button "Join / Sign in".
    - Measure with the existing `/api/r/<placement>` click tracking.
    - References: Robinhood, Linear, Duolingo.
- **Chat bubble (UX-11).**
  - It shows a "9+" unread badge to every first-time visitor, because `seen` defaults to 0 (`chatBubble.tsx:62,83,155`). It appears on every screenshot.
  - It republishes raw Telegram text with links on the brand's homepage.
  - Improvements: no badge on a first visit (or a "live" dot only), strip links, and apply the bot's moderation.
- **Social proof (UX-16).** Counters for YouTube 43, X 56 and Telegram 506 with sparklines (`Website.tsx:640-680`). Show only counts above a threshold (for example 1,000), or lead with Telegram's +254% in 30 days.
- **VpnPerk** says "Then $9.99 for 30 days" (`Website.tsx:698`) while payments are off (UX-6). Change it to "Free during beta" while `payments=false`.
- **Layout shift (UX-12).** CLS 0.35 on the first load (0.122 on repeat), from the hero title and sections appearing late. Preload the Archivo woff2 with fallback-font metrics, and reserve the Latest section's height in the skeleton.
- **Footer.** "Disclaimer" appears twice. The legal links go to `/website/*` (UX-17, UX-20).

### Pokémon market `/pokemon`, cards and sets (screenshots: `ux-pokemon-390.png`, `ux-pokemon-1440.png`)
- **Discovery.** It is the largest product and the main SEO asset, but has no navigation link (UX-4).
- **Theme.** It renders white under the black site header because it follows the OS theme (`mx.tsx:105-128`), although `index.html` declares `color-scheme: dark`. Make it dark by default, with light only on an explicit toggle (UX-10).
- **Speed.**
  - FCP is 2,772 ms with CPU throttling. Split `lightweight-charts` (183 KB) off the market home if it isn't needed above the fold (UX-12).
  - Server side, the market stalls for 10-39 s every 15 minutes (AR-6). This is the single biggest UX problem on this page; the fix is refresh-ahead caching.
- **SEO and sharing.**
  - Card pages show crawlers an empty shell (UX-1).
  - Shared card links show no picture because og:image is relative and `.webp` (UX-2).
  - Build a 1200x630 share image (card scan, price, 30-day change, the Kiwi) and add a "Share" button on card pages and on pack results.
  - Use real `/pokemon/sets/<set>` and `/pokemon/years/<year>` paths instead of `?set=` (UX ideas).

### Earn `/earn`, `/earn/lunch`, `/earn/funding`, `/earn/brain`, Kalshi (screenshots: `ux-earn-390.png`, `ux-earn-1440.png`)
- **Contradictory gating (UX-8).** The hero says "FREE · EVERY KIWI PASSPORT MEMBER", while the rank ladder says Scout "UNLOCK EARN" (`rewards.py:36`, `tradingbot.tsx:349-350`). Pick one rule. If Earn is free, give Scout another reward.
- **Credibility (UX-14).**
  - The tab named "Earn" leads with the Lunch Bot: paper, 25,975 trades, 13.6% win rate, -421 SOL.
  - The flagship bot's public track record returns `configured:false`.
  - Improvements:
    - Rename the tab "Bots" or "Lab".
    - Lead with an audited paper record.
    - Put the risk box at the top.
    - Move negative-expectancy experiments behind "Research".
- **Speed (UX-13).** `/earn/lunch` waits about 5 s (`/api/members/arbitrage/lunch` took 5.15 s; funding 3.19 s). Add a 15-30 s server cache.
- **Members-only page in the sitemap.** `/earn/brain` returns 401 when signed out (UX-19).
- **Trading UX gaps** (TR ideas; the Earn terminal was not exercised in a browser):
  - A red banner on /earn for an unprotected position (TR-1).
  - API-wallet expiry reminders, 7 days and 1 day before.
  - An optional "move stop to breakeven after TP1" setting.
  - Clear TP-refusal messages on small positions (TR-8).
  - Honest wording that Price Chat never sends withdrawals, matching the website and not the branch's "can never withdraw" (TR-14).

### Kiwi VPN `/vpn` (screenshots: `ux-vpn-390.png`, `ux-vpn-1440.png`)
- **Promises a price nobody can pay (UX-6, VPN-1).**
  - "Start Free Trial — 30 Days" and "$9.99 for 30 days" appear on the page and in the SEO/OG description (`seo/heads.py:34`).
  - "USDT payments open soon." appears only inside billing (`vpn.tsx:867`).
  - Trials started at launch end around 2026-12-04 with "The $9.99 plan isn't available yet".
  - Show the trial end date (`plan.days_left` exists) and "Free during beta" until payments are live.
- **Hidden in navigation for signed-out visitors** (`Website.tsx:252-254`), although the page sells to them (UX-5).
- **Legal.** There are no VPN terms, AUP or refund policy (VPN-10). The page says USDT "can't be refunded" (`vpn.tsx:960`) with no refund terms behind it (UX-9).
- **Kill switch copy.** "There's no kill switch" is correct for 0.1.1 (`vpn.tsx:517, 748`). When 2.0 ships, the copy must say exactly when the block is released (VPN-8).
- **Good:** honest copy. No "no logs" claim, uptime shown as null, countries taken from live servers (VPN "What is good").

### Giveaway `/giveaway` (screenshots: `ux-giveaway-390.png`, `ux-giveaway-1440.png`)
- The nav label "Giveaway" is pink on every page (`Website.tsx:268`). The page opens with "⚠️ Just for fun: test feature… you don't win real cards or prizes" (UX-15).
- Rename it "Open Packs" or "Pack Opening" and move it under Markets.
- Official booster artwork is an IP exposure (for legal).
- The pack result is the most shareable moment the product has. Add a share card (UX ideas).
- Gamification side effect: pack XP uses up the daily overall cap, so members who open packs in the morning then see "today's XP limit is reached" for reading and watching (PP-3).

### Join `/join` (screenshots: `ux-join-390.png`, `ux-join-1440.png`)
- The fixed chat bubble overlaps the "Sign in with wallet" button on mobile (UX-11). Hide the bubble on /join, /passport and checkout-like screens.
- The copy "every rank has a reward" (`community.tsx:82`) is false (UX-7).
- Sign-in options in production: Telegram (deeplink), Google, X and wallet. Email is off (PP-14). The Telegram flow needs a confirm step in the bot for security (SEC-4); the UX should show "check Telegram and tap *Yes, it's me*".

### Leaderboard `/leaderboard` (screenshot: `ux-leaderboard-390.png`)
- Shown in the mobile menu only (UX-4).
- 14 of 38 visible members still have default `KiwiNNNNNN` names (PP-4).
- Suspended members still appear (PP-13).
- The all-time board is dominated by legacy points. A monthly seasonal board with a badge would give new members a reason to compete (PP ideas).

### Learn `/learn` (screenshot: `ux-learn-390.png`)
- Signed-out visitors see only "Sign in to your Kiwi Passport to see your rewards" (page height 851 px), but the page is in the sitemap (UX-19).
- Add teaser content (lesson titles), or noindex it.

### Kiwi Passport `/passport` (signed in; assessed from code)
- **"Next steps" ignores the real levers (PP-15).** It ranks open missions only, so new members see "Watch the latest video (+3)" and "Connect X (+20)", and X cards (0 XP) take one of the 3 slots.
  - Replace it with a server-computed "Road to <next rank>" panel: today's remaining caps, streak at risk, invites pending, and the next rank's unlocks (VPN at Royal).
- **X cards stuck on "Open X" forever** while X write is off (PP-14). Hide them, or mark them "support only".
- **Duplicate Passports** give the dead-end message "We've asked the Price Chat team to look into it" (PP-12). Add a self-service merge.
- **Confusing cap message.** "No XP this time: today's XP limit is reached" after opening packs (PP-3).
- **Onboarding.** Add a three-step first-run checklist: pick interests (markets, trading or VPN), claim the first XP, open the matching product (UX ideas; Duolingo and Notion as references).
- **Retention.** A "streak at risk" DM at 20:00 UTC through the bot (PP ideas).
- **Copy.** "Every message you send in Price Chat becomes XP" (`tgbar.tsx:76`) invites spam (PP-11).

### Legal pages `/website/privacy`, `/website/terms`, `/website/disclaimer`, `/website/contact`
- A public "Draft: this page has not had final legal review yet." banner (`community.tsx:418-420`) (UX-9).
- **Privacy** omits wallets, Hyperliquid keys, Kalshi, email sign-in, the homepage chat republishing, localStorage (UX-9) and Telegram for VPN keys (VPN-9). See SEC-12.
- **Terms** has no entity, governing law, liability limit or refund terms.
- **Disclaimer** promises to disclose affiliate links while Earn is built on the BANTER referral.
- **Contact** has no email.
- `/privacy` and `/terms` return 404. These pages exist only under `/website/*` (UX-17). Move them and add 301s.

### Housekeeping (UX-20)
- Unreferenced `adam.png` (2.1 MB) and `kiwi.png` (1.97 MB).
- APK and EXE binaries tracked in git and redeployed with every build.
- 3 TikTok meta tags and 6 verification files.
- The dashboard `styles.css` (31 KB) loads on every public page.

---

## 3. Telegram bot UX

Sources: telegram.md, plus ND for the channel.

| Issue | Detail | Improvement | ID |
|---|---|---|---|
| Bot goes silent | Up to 148-185 s behind other jobs; about 2.5 min after a 5 s rate limit, and up to 30 min after a longer incident | A dedicated always-on listener, 429-aware retries | TG-2, TG-4, ND-7 |
| One user stalls everyone | Commands run serially. `/signal` scans 15 markets. Button answers come after all commands | Per-user and per-chat buckets, answer callbacks first, a worker pool | TG-3, TG-12 |
| Edits re-run commands | Fixing a typo gives a duplicate reply and chart | Ignore edits | TG-5 |
| Replies in strangers' groups and channels | Commands are queued before the allow-list check | Allow-listed and private chats only | TG-6 |
| Command overload | About 30 commands; the branch menu has 28 entries. `/price`, `/indicator`, `/scan` and `/market` overlap; so do `/chart` and fc, and `/committee`, `/debate`, `/research` and `/risk COIN`. `/help <x>` is the trade check. `/risk` means two things. `/market` vs `/markets` | **Market:** `/price BTC [more]`, `/chart BTC 1h`, `/scan`. **Analysis:** `/signal`, `/committee COIN`, `/check <trade>`. **Account:** `/status`, `/positions`, `/pause`, `/resume`, `/close`. **Others:** `/alert`, `/vpn`, `/idea(s)`, `/record`. **New:** `/sessions`, `/unlink`, `/ref`, `/privacy`, `/help <cmd>` | TG-13, TG-9, TG ideas |
| Noise | One welcome per join batch with no cooldown. A footer on every reply (errors included), sometimes as a separate message. Inconsistent @botpricechat label | A welcome cooldown (1 per 10 min, names batched), no footer on errors or usage replies, no standalone footer in groups | TG-15 |
| Lost or mangled messages | HTML cut mid-tag at 4096/1024 characters. News Desk captions can lose the card image. Batches lost on timeout | A shared `fit_html` helper, persistent queue | TG-10, TG-11 |
| fc buttons | Anyone can switch the timeframe under the asker | Per-user cooldown; post a reply in groups | TG-12 |
| Misleading branch messages | "Sent you a private preview." even after a refusal. "Working…" left forever on errors. "Can never withdraw" claim | Honest group messages, a generic error reply, the website's wording | TG-9, TR-14 |
| Sign-in confirmation | "✅ Done! You're signed in" with no "is this you?" | Confirm button showing device, place and time | SEC-4 / TG-1 |
| Moderation commands swallowed | `/ban` and `/mute` meant for other bots are logged as ours and dropped from the record | Queue only known commands | TG-14 |

**@Pricechatupdates channel (ND-13, ND-3):**
- Every headline is in capitals.
- Every post starts with "🥝 PRICE ALERT", news included.
- Market posts have no context.
- About 50 views each, and weekends are silent.

Improvements: sentence-case headlines, the PRICE ALERT header only on price posts, and one measured context line ("biggest daily drop since …").

---

## 4. Kiwi VPN apps and extension

| Platform | UX problem | Improvement | IDs |
|---|---|---|---|
| All apps | One device per server with a 3-device cap. Trying each country once on a PC locks out the phone with a 409 error that sends the user to "My Devices on pricechat.live". Smart Connect failover burns slots silently | One device per installation; move servers in place (`PATCH /members/vpn/devices/{id}`) | VPN-2 |
| All apps and extension | One shared 5-per-10-min bucket for trial, devices and extension logins. A flaky browser connection can block the apps for 10 minutes | Separate buckets; extension backoff | VPN-3 |
| All apps | "Recommended" server is Ashburn for everyone, EU users included | Region- or RTT-aware recommendation | VPN-5 |
| All apps | Trial ends around 2026-12-04 with no way to pay and no end date shown | Show `days_left`; a payment or free-mode decision | VPN-1 |
| All apps | Transient node blips give 503 for 5 minutes | Flap guard (fix/kiwi-stage0) | VPN-6 |
| Windows 0.1.1 | No kill switch, no update check. Users never learn about 2.0 | A signed version check or the Tauri updater | VPN-7 |
| Windows 2.0 | Unsigned, so Smart App Control blocks it. The trial starts silently right after sign-in. Smart Connect "fastest server" has no data from production, so it looks broken. The kill switch is released after timers | Code signing (owner decision), start the trial on first Connect, merge monitoring, precise kill-switch copy, an optional persistent block mode | VPN-7, VPN-16, VPN-12, VPN-8 |
| Windows 2.0 CI (coordinator) | Window tests 7/10 PASS: opens, signed in, Smart Connect connects (Ashburn), Tools speed test 732 Mbps, leak check OK, disconnect. 3 FAIL around failover, suspected to be a test-method flaw; product failover UNVERIFIED | See TEST-EVIDENCE.md | — |
| Chrome extension | 1.0.0 live; 1.0.3 (all-site access fix) tested and not submitted. Re-logs in on every server change and recheck with no backoff. Germany has no proxy (`browser:false`) | Submit 1.0.3, add backoff | VPN-3, VPN-12 |
| Android RC1 | Play-sensitive permissions (systemExempted foreground service, notification listener for Auto-Call). No physical-phone team test | A Play flavour without Auto-Call; run the device test plan | VPN-13 |
| iOS | Never run on an iPhone. No in-app account deletion (App Store 5.1.1(v)) | Add a deletion link; an organization account (5.4) | VPN-13 |
| Telegram `/vpn add` | Sends a `.conf` with the private key through Telegram | Send a link to the website instead | VPN-9 |

---

## 5. Admin dashboard

Sources: architecture-admin.md and newsdesk-content.md.

| Page / control | Problem | Improvement | ID |
|---|---|---|---|
| Overview | The startup self-check problem ("owner alerts have no Telegram chat") is visible only in logs | A red banner on Overview; backup age and failing-job count | AR-2, AR-19 |
| Settings › Owner alerts | Not set, so every alert is "logged only" | Set the chat and press "Send test" | AR-2 |
| Background jobs | 16 server-disabled jobs show as enabled, and toggling them reports success | A grey "Off on this server (SCHEDULER_ONLY_JOBS)" badge, a disabled toggle, banners on the Community, Knowledge, X opportunities and Approvals pages | AR-5 |
| Background jobs › Run now | Holds the HTTP request for up to the job's timeout; a second click gives "Already queued" | Return 202 with the queue position and poll | AR-14 |
| News Desk | Desk decisions (virality, why, per-platform status) are returned but never rendered. No Hold / Release / Kill. The policy is JSON-only. "Breaking sensitivity" does nothing. "Last run" grows with no error shown. The writer says "model" for X | A Desk panel (last 48 h), event actions, an editable policy, removal of dead controls, the scheduler's last error | ND-8, ND-9, ND-2 |
| News Desk › Telegram lane | "Paused" badge shown for days after the pause ended. No review or dry-run mode while X has one | Compute the pause state from `paused_until`; an optional approval mode | ND-11, ND-8 |
| Security | No session list or "sign out everywhere". No 2FA. No admin audit trail | An owner security panel: active sessions, key fingerprint and last re-key, proxy-secret status, backup age, rate-limit hits | AR-10, AR-11, SEC ideas |
| Global emergency stop | Toggles without a confirm in either direction (other destructive actions do have confirms) | Add a confirm | AR Untested section |
| Trading settings | `min_rank_level` / `min_deposit_usd` do nothing for bot trading | Honour or remove them | TR-9 |
| AI Brain | Shows the model as configured while the provider may be an unreachable localhost Ollama | Show live model status; add a self-check | AR-15 |

---

## 6. Ranked improvement list

Combined from the UX, PP, TG and VPN idea lists, in the reviewers' ranking order where they overlap.

1. **Edge-injected heads plus a text summary for all crawlers** (UX-1). The backend builders and golden tests already exist.
2. **Rebuild the home page's first viewport** with a value line, a CTA and four live product tiles, and add Markets to the navigation (UX-3, UX-4, UX-5).
3. **Copy driven by live config** for VPN price, rank rewards, Earn gating and X features, with a guard test (UX-6, UX-7, UX-8, PP-14).
4. **Telegram sign-in confirm button** (SEC-4). Both a UX element and a security fix.
5. **One device per installation, plus separate VPN rate-limit buckets** (VPN-2, VPN-3).
6. **"Road to next rank" panel and a first-run checklist** (PP-15, UX ideas).
7. **Share cards** for Pokémon cards and pack pulls (UX-2).
8. **One design system**, with the market dark by default (UX-10).
9. **Simplify the bot's command surface**, ignore edits and non-person senders, and add a welcome cooldown (TG-5, TG-6, TG-13, TG-15).
10. **Admin truthfulness**: job server-state badges, the News Desk panel, the alert banner (AR-5, ND-8, AR-2).
11. **Finish the legal pages**, add a support email and move them to clean URLs (UX-9, UX-17, VPN-10).
12. **Performance polish**: font preload, skeleton height, chart split, Earn caching, market refresh-ahead (UX-12, UX-13, AR-6).
