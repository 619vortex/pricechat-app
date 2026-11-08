# Kiwi Passport & Gamification — audit notes (PP)

Reviewer area: sign-in / linking (Google, X, Telegram, email, wallet), XP ledger, missions, referrals / crew, ranks,
leaderboards, badges, streaks, rewards, anti-abuse, UX copy. Code read at audit/full-2026-11 (= main e978cc3).
Exploit tests: `backend/tests/audit_passport_exploits.py` (9 tests, all PASS = each weakness reproduced on the local test DB).
Test-name → finding map: pp1/pp1b → PP-1, pp7 → PP-2, pp3 → PP-3, pp4 → PP-4, pp6 → PP-6, pp2 → PP-7, pp8 → PP-9, pp5 → PP-10.

## Summary

The XP ledger itself is well built: one `award()` path, a DB unique key (member, event_type, source_ref) against
double-pays, one-time claim hashes per external account that survive deletion, savepoints, an audit log, and X
engagement rewards are policy-locked off. The weak spots are around it, and they matter because **XP buys real value:
Royal Kiwi (rank 7, 3,500 score) = free Kiwi VPN with no end date** (normally $9.99 / 30 days), and rank gates Earn, Vortex
Realm, the Knight reward claim and Royal VIP requests.
Biggest risks:
1. **Account takeover by phishing the Telegram start link** (live in prod: providers says `telegram.mode = "deeplink"`, email
   sign-in is off, so Telegram/Google/X are the only ways in). The hijacked session can also preview/confirm/close live
   trades for members who connected a Hyperliquid agent (trading routes accept the member cookie alone).
2. **The wallet reward pays "enough to reach the next rank"** — saved until rank 9 it is +6,000 XP for one free signature
   from an empty wallet; at Knight it is a straight jump to Royal (free VPN). The live board already has a Knight at 1,850
   and a member at 3,499.
3. **Click-only XP outside the overall cap** (packs 100/day, streak, Earn self-reports) makes Royal reachable in about 2
   weeks of scripted clicking + one wallet signature; **unlimited wallet-login sybils** (rate limit keys on IP + User-Agent).
4. Daily caps are check-then-insert: 30 parallel awards paid 30 XP against a cap of 10.
Biggest opportunities: make the wallet reward a flat amount, move packs/streak under a real overall cap, add a confirm
step to the Telegram link sign-in, gate Royal/VPN on time + verified activity instead of raw score, and surface the levers
that are real (crew invites, Telegram) in "next steps".

## Findings

### PP-1: Telegram start-link sign-in can be phished — one tap gives the attacker the victim's Passport (and linking steals their Telegram)
Severity: High · Status: CONFIRMED (test) · Effort: M
Evidence: `app/members/auth.py:266-303` — `telegram_start` returns `t.me/<bot>?start=pc_<code>`; `telegram_seen_start` records
WHOEVER presses Start; `telegram_claim` hands that identity to the browser that started the attempt.
`app/api/routes/members.py:723-742` signs that browser in as the identity's owner (`service.sign_in_with`, login) or links it
(link purpose). Prod: `GET https://api.pricechat.live/api/members/auth/providers` → `telegram: {available: True, mode: "deeplink"}`,
`email: {available: False}`. Tests `test_pp1_telegram_start_link_phishing_signs_attacker_into_victims_passport` and
`test_pp1b_telegram_link_phishing_attaches_victims_telegram_to_attacker` pass.
Impact: attacker starts a login, sends the link ("claim 100 free XP in the Price Chat bot"), victim taps Start in the real
official bot, attacker's browser polls `/auth/telegram/status/<id>` and gets a 30-day sliding session as the victim. The
bot even replies "Done! You're signed in". With purpose=link the victim's Telegram is attached to the attacker's Passport:
their Combot legacy rank points (follow the Telegram id, `reputation.py:28-42`), all future `telegram.message` /
participation XP, crew/channel missions. Escalation (CONFIRMED by reading, impact owned by the trading reviewer):
`/api/members/trading/preview|confirm|positions/*/close|close-all` (`app/api/routes/trading.py:125-200`) need only the member
cookie, so a hijacked session can trade a live-connected member's Hyperliquid account; VPN devices and Earn data are exposed too.
Recommended fix: after Start, the bot replies with a confirmation showing the requesting device/browser/approx. location and
an inline "Yes, sign me in" button, or show a short code in the browser that must match the one the bot shows; never
auto-confirm. Better: switch production to the Telegram Login Widget / Telegram OAuth (`members_telegram_deeplink=False`,
widget already implemented and HMAC-verified). Keep a "new sign-in to your Passport" notice in the bot.

### PP-2: The wallet reward is "100 XP or enough to reach your next rank" — worth up to 6,000 XP, and a free jump to Royal (free VPN)
Severity: High · Status: CONFIRMED (test) · Effort: S
Evidence: `app/tradingbot/service.py:410-438` `free_level`: `need = max(100, next.min_xp - rank_score)`; called from wallet
verify and wallet sign-up. Ranks `catalog.py:15-36` (Knight 1,800 → Royal 3,500; Legend 10,000 → Mythic 16,000).
`test_pp7_wallet_reward_grows_with_rank...`: a member at 10,000 verifies a brand-new empty wallet → +6,000 XP → Mythic.
Royal = free VPN: `app/vpn/service.py:138,168-170` (`rank_level >= 7`), website copy "Free for Royal Kiwi and above".
Impact: the optimal play is to never connect a wallet until right before a big rank gap. Any Knight (1,800) gets +1,700 and
free VPN for one signature; generating a wallet costs nothing and no deposit is required. Live board (public GET): top
values 4195, 3499, 1850 — the 3,499 and 1,850 members are exactly one free signature away from Royal. It also devalues
"Rare, and earned" ranks.
Recommended fix: flat reward (e.g. 100 XP), or "next rank" capped at the low ranks only (e.g. only below Kiwi, level 3).
Consider requiring a deposit tier (already verified from Hyperliquid's records) for anything larger. Reassess existing
wallet awards above 100 XP with `reverse()` only if the owner wants to.

### PP-3: Pack Giveaway XP is labelled "website", so it eats the 60/day overall cap of every other reward — while packs themselves are not limited by it
Severity: Medium · Status: CONFIRMED (test) · Effort: S
Evidence: `packs.py:285-286` awards `pack.open` with `source="website"`; the cap check in `xp.py:323-329` uses
`rule.verification_source` ("packs", not in AUTOMATIC_SOURCES) to decide whether to apply the cap, but `_paid_today(...,
sources=AUTOMATIC_SOURCES)` sums by the stored `source` column ("website"). `test_pp3_...`: after "Joined" (+20) and 8 packs
(+40), the daily visit pays nothing and `website.x_post_read` is refused `overall_cap`; 12 more packs still pay 5 each.
Impact: a member who opens a few packs in the morning gets "No XP this time: today's XP limit is reached" for reading,
watching, the latest-video mission and Telegram-sourced missions — the activities the product actually wants — while the
click-only activity keeps paying 100/day. Confusing and inverts the incentive.
Recommended fix: give packs their own source label ("packs") or include them in the overall cap consistently; decide one
policy for click-only XP (see PP-5) and document it on the rules page.

### PP-4: Unlimited Passports via "Sign in with wallet"; the limiter keys on IP + User-Agent
Severity: Medium · Status: CONFIRMED (test) · Effort: M
Evidence: `app/tradingbot/service.py:441-482` `login_verify` creates a member for every new wallet (+20 joined, +100 wallet);
limiter `service.py:52-65` 10 per 10 min per `_visitor` = sha256(client_ip | User-Agent) (`routes/tradingbot.py:53-56`), and
the `_hits` dict is unbounded. `test_pp4_...`: 15 Passports from one IP by rotating the UA header, each with ≥120 XP.
Impact: scripted sybil farm with zero cost (keys are free), no CAPTCHA, no email. Each sybil also collects the click-only
Earn steps (PP-6), packs, streaks, and can hold Play USDT / binder cards; pollutes leaderboards (14 of 38 visible members
still have default `KiwiNNNNNN` names) and the Earn funnel analytics. Memory growth from unbounded limiter keys.
Recommended fix: key the limiter on the client IP (/64 for IPv6) only, plus a global creation budget; make new wallet-only
Passports earn nothing automatic until they link a platform account or pass a Turnstile/hCaptcha; bound `_hits`.

### PP-5: Royal (free VPN) is reachable in ~2 weeks of click-only activity
Severity: Medium · Status: CONFIRMED (code + live rules) · Effort: M
Evidence: live `GET /api/members/rules`: `pack.open` 5/100 per day (source packs), `streak.day` up to 25/day + milestones
(cap 500), `telegram.message` 1/50 per day (tgchat), `watch.combo` 60, `mission.complete` 30 — none of these are inside the
60/day overall cap (`xp.py:27-29`). Earn self-reports pay without verification: `joined_hyperliquid` +25 on a button press
(`tradingbot/service.py:589-601`), `signal_action` "skip" = +50 (`:680-694`), `check_group` +10 "on your word" when Telegram
isn't linked (`:775-830`), `confirm_bot` +25 "on your word" (`:604-627`). The streak's daily task can be a pack click
(`packs.py:300`).
Impact: packs (20 POSTs/day, 2 s cooldown) + streak ≈ 125 XP/day with no human attention; + one Telegram account chatting
distinct 3-character messages ≈ 175/day; + Earn self-reports ≈ +110 once. ~1,800 (Knight) in ~10-13 days, then PP-2's
wallet jump → Royal → free VPN. Ranks are meant to mean "trusted by the community".
Recommended fix: put every non-staff source under one overall daily cap (or count click-only XP as `premium`/cosmetic that
never counts towards rank); require a minimum account age + verified activity for Royal benefits (VPN); make Earn
self-reports pay 0 (or only after verification).

### PP-6: Daily caps, cooldowns and max-per-member are check-then-insert — parallel requests overshoot them
Severity: Medium · Status: CONFIRMED (test) · Effort: M
Evidence: `xp.py:279-330` reads `_paid_today` / counts, then inserts in a savepoint; the only DB guarantee is the unique
(member, event_type, source_ref). SQLite via pysqlite runs the SELECTs outside a write transaction (`db/session.py`: no
`BEGIN IMMEDIATE`). `test_pp6_parallel_awards_overshoot_a_daily_cap`: 30 threads → **30 XP paid against `website.x_post_read`
daily cap 10** (reproduced on every run). Same pattern: referral/crew `referral_daily_limit` (`referrals.py:163-167`,
`crew.py:141-145`), `_paid_discussions_today`, the packs cooldown read (`packs.py:271-273`, slot unique key saves packs).
Impact: Read & Earn (show N posts, wait 8 s, fire N reads at once), Watch, mission and referral limits can be exceeded by a
script; the overall 60 cap too.
Recommended fix: serialize awards per member: `BEGIN IMMEDIATE` for award transactions on SQLite (or a per-member
`threading.Lock`, since prod is one replica), or a per-(member, rule, day) counter row updated with a conditional UPDATE.

### PP-7: Weekly "Open 3 Price Chat videos" is paid from client-reported clicks with arbitrary targets
Severity: Medium · Status: CONFIRMED (test) · Effort: S
Evidence: `POST /api/members/me/actions` (`routes/members.py:202-217`) stores any `{kind, platform, target}` (target is free
text, never checked against content); `missions.sync_event_missions` counts distinct `MemberAction(kind="open",
platform="youtube")` targets for `weekly_content` (`missions.py:322-332`) and pays its 5 XP + the day's streak.
`test_pp2_weekly_content_mission_paid_for_fabricated_opens`: three POSTs with targets "nonsense-0..2" → mission.complete 5
XP + streak.day 5 XP. The card is labelled "Verified as an open only".
Impact: small XP, but it is a free daily-streak trigger once a week and the endpoint accepts unlimited distinct rows
(no rate limit → DB growth by any signed-in member).
Recommended fix: count only opens recorded by the server for real content (`content_view` / `watch.start` targets that
match a `ContentItem`), and rate-limit `/members/me/actions`.

### PP-8: Referral anti-abuse is weak: self-referral check needs the referrer to stay signed in, the "same browser" flag is a cookie the attacker controls
Severity: Medium · Status: CONFIRMED (code) · Effort: M
Evidence: `referrals.py:106-128`: self-referral = same member or `browser_member` (the session in that browser) is the
referrer — signing out bypasses it. `browser_hash` comes from the `pc_auth` cookie, which `_browser_binding` creates fresh
when absent and which `email_verify` / `_finish` delete after each sign-in (`routes/members.py:525,590`), so every new
sign-up has a new hash and `SAME_BROWSER_FLAG_AT` effectively never fires. No IP / device signal. Crew invites
(`crew.py`): 5 throwaway Telegram accounts that stay 24 h in @thepricechat pay 5×100 + 500 bonus = 1,000 XP; nothing is
clawed back when they leave after verification.
Impact: referral XP (25+10 per invite, 5/day) costs one fresh X or Telegram account; crew XP (1,000 for 5 accounts) is the
single biggest lever towards Royal.
Recommended fix: hash a server-side signal (IP /24 or /64 + UA family) alongside the cookie for pattern flags; delay crew
payment longer (7 days) and require the friend to post at least once (meaningful message) or link a Passport; claw back
(reverse) if the friend leaves within N days; put referral sources under a weekly cap.

### PP-9: A friend who signs up with Telegram or X never qualifies the invite through that link
Severity: Low · Status: CONFIRMED (test) · Effort: S
Evidence: `service.sign_in_with` (`service.py:126-131`) calls `link_identity` (awards `identity.telegram_linked`, which runs
`referrals.on_award` before any Referral row exists) and only then `attribute()`. `referrals.sweep` requires
`ReputationEvent.created_at >= Referral.created_at` (`referrals.py:193-204`), so that event never counts.
`test_pp8_signing_up_with_telegram_does_not_qualify_the_referral` passes. With email sign-in off in prod, Telegram/X are
the normal sign-up paths.
Impact: invites look "joined" forever unless the friend links the other platform or posts in Telegram; inviters see
fewer rewards than the copy promises ("connecting X or Telegram").
Recommended fix: call `attribute()` before `link_identity()` for new members (or let the sweep accept events in the same
transaction / same second).

### PP-10: The Watch & Earn play timer is bypassed by the plain content-view endpoint
Severity: Low · Status: CONFIRMED (test) · Effort: S
Evidence: `POST /api/members/me/content-view` (`routes/members.py:297-307` → `service.content_view`) pays the same
`website.content_view` ledger row (and the latest-video mission via `open_mission`) instantly, with no Watch press and no
seconds played; `watch.complete` then says "already". `test_pp5_content_view_pays_without_any_play_time`.
Impact: small XP (1/video, 3 for the latest mission), but the "played N seconds" claim on the Watch page is not what is
enforced; the combo bonus is the only thing that still needs the timer.
Recommended fix: pay video XP only through the watch path; make content-view a non-paying click record.

### PP-11: "Every message you send becomes XP" rewards chat spam
Severity: Medium (community health) · Status: CONFIRMED (code + copy) · Effort: S
Evidence: `telegram.message` 1 XP per distinct message ≥3 characters, 5 s apart, 50/day, outside both the Telegram cap and the
overall cap (`verified.py:137-161`, `catalog.py:94-96`); copy `frontend/src/website/tgbar.tsx:76` "every message you send in
Price Chat becomes XP". The only spam guard is "same text 5 times in a day voids the day".
Impact: invites low-value message floods in the main community chat (e.g. "ok1", "ok2"...), which degrades the
product's core asset and also feeds the AI/community features that read the chat.
Recommended fix: count only `meaningful()` messages (15+ chars, already implemented) for this rule, lower the cap (e.g. 10/day),
or pay per active day instead of per message; reword the copy.

### PP-12: Duplicate Passports cannot be merged; linking the other account just raises a flag
Severity: Low (UX/support) · Status: CONFIRMED (code) · Effort: M
Evidence: `service.link_identity` (`service.py:76-83`) refuses an identity held by another member and raises an
`identity_conflict` flag; there is no self-service merge. With email sign-in off, a member who used X on the phone and
Google on the laptop has two Passports, two XP ledgers, and the error "We've asked the Price Chat team to look into it".
Recommended fix: a merge flow that proves control of both accounts (sign in to B while signed in to A), moves identities
and ledger rows (respecting claim hashes), and deletes the empty one; or a staff tool in Members admin.

### PP-13: Suspended (xp_suspended) members still appear on leaderboards
Severity: Low · Status: CONFIRMED (code) · Effort: S
Evidence: `leaderboard.py:28-29` `_visible()` filters only `leaderboard_opt_out` and `status == "active"`; `xp_suspended`
members (under review for abuse) keep their place and score in public boards.
Recommended fix: add `Member.xp_suspended.is_(False)` to `_visible()`.

### PP-14: Copy promises features that are off in production
Severity: Low · Status: CONFIRMED (copy + live providers) · Effort: S
Evidence: `xfeed.tsx:129` "Reply and repost without leaving your Passport" is unconditional, but prod has
`x.write = False` (providers endpoint), so replies/reposts open X instead. `routes/members.py:1-2` and `emailLogin.tsx`
call email "the website's sign-in" while prod `email.available = False` (the form is correctly hidden). The X mission cards
complete with 0 XP only when sent from the app, which can't happen with write off — `x_reply` / `x_repost` cards stay
"Open X" forever (`missions.py:444-449`, `mark_x_action_done` never reached).
Recommended fix: drive the copy from `providers.x.write`; hide X reply/repost mission cards (or mark them "support only")
when write is off.

### PP-15: "Next steps" ignores the biggest real levers, so members don't learn what actually moves their rank
Severity: Low (retention) · Status: CONFIRMED (code) · Effort: S
Evidence: `insights.next_steps` (`insights.py:164-168`) ranks only open missions by XP; crew invites (+100 per friend, +500
bonus), deposit tiers, Telegram chat, the streak and packs are not missions, so a new member's "next steps" are typically
"Watch the latest video (+3)" and "Connect X (+20)". `today()` caps cards at 3 and X cards (0 XP) take a slot.
Recommended fix: a single "how to reach <next rank>" panel computed server-side from the member's state (what's left of
today's caps, streak at risk, invites pending verification, next rank's unlocks such as VPN at Royal).

### PP-16: Telegram widget data is replayable for 24 h and not bound to the browser (latent; widget not enabled in prod)
Severity: Low · Status: CONFIRMED (code), not active · Effort: S
Evidence: `auth.py:306-321` accepts any widget payload signed within `WIDGET_MAX_AGE = 86400`; `routes/members.py:756-771`
has no single-use or browser binding. Telegram recommends checking freshness; a leaked payload (logs, proxy) works for a day.
Recommended fix: reduce max age to ~5-10 minutes and store used hashes until expiry when the widget is switched on (PP-1 fix).

## Advertised missions / actions vs. server-side verification

| Action (as shown) | XP | How the server verifies | Verdict |
|---|---|---|---|
| Joined / Chose a username | 20 / 15 once | server events | OK |
| Daily visit | 2/day | signed-in `GET /members/session` | OK (trivial by design); often blocked by PP-3 |
| 7-day streak (website.streak) | 10/week | ledger | OK |
| Daily streak (streak.day) | 5–25/day + milestones | first "task" of the day — a pack click or PP-7 counts | Weak (PP-5) |
| Opened a Price Chat video | 1, 5/day | content-view POST, must be a real public item | "Open" only; bypasses Watch timer (PP-10) |
| Watch & Earn / Watch the latest video | 1 / 3 + combo | browser-reported seconds + server elapsed time since Watch | Client-trusted seconds; bypassable (PP-10) |
| Read & Earn (X posts) | 1, 10/day | server time between `shown` and `read` (8 s), X linked | Timer only; parallel reads overshoot cap (PP-6) |
| Connect Telegram / X / Google | 20 once per external account | OAuth+PKCE / bot Start | Verified, but Telegram Start is phishable (PP-1) |
| Telegram message | 1, 50/day | ingested messages from linked id in authorised chats | Verified, spam incentive (PP-11) |
| Active day / first participation / discussion | 2 / 3 / 1 (Telegram cap 10) | ingested meaningful messages | Verified |
| 3 active community days (weekly) | 10 | ledger | Verified |
| Open 3 Price Chat videos (weekly) | 5 | client-posted arbitrary targets | **Not verified (PP-7)** |
| Join Price Chat Updates | 10 once | getChatMember (bot admin) | Verified at that moment; leaving keeps XP |
| Join the discussion | 5 per announcement, 3/day | ingested comments, copy checks | Verified |
| Reply / repost / like on X | 0 | policy-locked | Correct (copy PP-14) |
| Pack Giveaway | 5, 100/day | none (button) | Click-only, outside cap (PP-3, PP-5) |
| Pokémon Portfolio first card / scan / public | 15 / 10 / 10 once | site actions | OK (once each) |
| Connect a wallet | 100 → up to 6,000 | EIP-191 signature, single-use nonce | Verified signature, absurd amount (PP-2) |
| Deposit tiers $10…$10k | 50/150/400/1000 | Hyperliquid deposit records | Verified (not deep-reviewed here) |
| Earn: Joined Hyperliquid | 25 | button press | **Self-reported** (PP-5) |
| Earn: first bot signal | 50 | join/skip/auto click | **Self-reported** (PP-5) |
| Earn: joined bot group | 50 / 10 on word | getChatMember; on word if Telegram unlinked | Partly self-reported |
| Earn: bot connected | 100 / 25 on word | Hyperliquid agents list; else on word / bot Start | Partly self-reported |
| Invited a friend / welcome | 25 / 10 | friend's first qualifying event | Sybil-able (PP-8), sign-up path bug (PP-9) |
| Friend joined Telegram / crew bonus | 100 / 500 at 5 | getChatMember after 24 h | Sybil-able, no clawback (PP-8) |
| Community event / helpful reply | 25 / 2 | moderator | Staff |

## Untested / couldn't verify (and why)
- Whether prod overrides any XP rule values or ReputationSettings (weights, Telegram cap, watch seconds) — only the public
  rules endpoint was read; settings need the admin API.
- How often the prod members.verify / crew / referral sweep jobs run (`SCHEDULER_ONLY_JOBS` not read: env access forbidden).
- Real-money trade execution through a hijacked session (PP-1 escalation) — read the routes only; the trading reviewer
  should confirm whether `execution.confirm` places live orders without any further factor.
- Vortex Realm / realm_online websocket tickets, Play Market economics and Worker roles were skimmed, not audited.
- Google `id_token` is not signature-checked (it comes straight from Google's token endpoint over TLS with the client
  secret, which OIDC allows) — acceptable; noted only.

## Improvement & feature ideas (ranked)
1. **Confirm-in-Telegram sign-in** (fixes PP-1) — biggest security win; one inline button.
2. **Flat wallet reward + verified-deposit bonus** (PP-2) — removes the single largest XP exploit with a one-line change.
3. **One overall daily cap for every non-staff source, or click-only XP as cosmetic "fun XP"** (PP-3, PP-5) — makes rank
   mean community trust again, protects the VPN benefit.
4. **Royal benefits need tenure**: e.g. Royal + 30 days of membership + 10 verified Telegram days before free VPN; keeps
   the aspiration, kills the 2-week farm.
5. **Per-member award serialization** (PP-6) — cheap on one replica (lock or `BEGIN IMMEDIATE`).
6. **"Road to <next rank>" panel** (PP-15) — shows exact remaining XP split by today's remaining caps; surfaces crew
   invites and the VPN unlock; strong retention driver.
7. **Account merge** (PP-12) — reduces support load and duplicate leaderboard entries.
8. **Streak freeze / reminder** via the bot (members already opted in by linking Telegram) — classic retention mechanic
   the streak lacks; send one "streak at risk" DM at 20:00 UTC when `at_risk`.
9. **Seasonal leaderboard** (monthly reset with a badge) — the all-time board is dominated by legacy points; a season
   gives new members a reason to compete.

## Commands run
- Read code under `backend/app/members/*`, `app/api/routes/members.py`, `rewards.py`, `tradingbot.py` (wallet + Earn),
  `trading.py` (routes), `app/tradingbot/service.py`, `app/vpn/service.py`, `app/security/rate_limit.py`, `app/security/admin_auth.py`,
  `app/db/session.py`, frontend `src/website/{xfeed,tgbar,emailLogin,wallet,vpn}.tsx` (copy).
- `curl -s https://api.pricechat.live/api/members/auth/providers` → telegram mode "deeplink", email available False, x write False.
- `curl -s https://api.pricechat.live/api/members/rules` → live XP rules table (used above).
- `curl -s "https://api.pricechat.live/api/members/leaderboard?metric=score&limit=100"` → 38 visible members; top values
  4195, 3499, 1850, …; ranks: 1 royal, 2 knight; 14 default-named. (No usernames recorded.)
- `curl -s -o /dev/null -w "%{http_code}" https://api.pricechat.live/api/rewards-admin/overview` → 401 (admin routes guarded).
- From `C:\AI\pc-wt\audit\backend`:
  `PYTHONPATH=<pyshim> C:/AI/price-chat/backend/.venv/Scripts/python.exe -m pytest tests/audit_passport_exploits.py -q -p no:cacheprovider -s`
  → `9 passed` (PP-6 prints "parallel awards paid 30 XP against a daily cap of 10 (30 awards)").
