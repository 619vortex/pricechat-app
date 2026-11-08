# Price Chat: security review

Audit of 2026-11-08. The code reviewed is main e978cc3 (audit worktree), plus feat/tg-trading where stated. Production was touched only through public unauthenticated GETs/HEADs and `railway logs` (read only). This file merges `notes/security.md` (SEC-*) with the security-relevant findings from the other reviewers, removing duplicates. Every item keeps its original IDs and status labels (CONFIRMED / SUSPECTED / UNVERIFIED).

Non-security bugs are in QA-FINDINGS.md. Backups and alerting are also covered operationally in PERFORMANCE-RELIABILITY.md.

---

## 1. Summary

The basic web-app hygiene is good (security.md "Verified OK"):
- Every `/api` route is private by default (`admin_guard`, allow-list). Of 778 routes, 461 are admin-only, 300 public-prefix and 17 public market reads.
- Sessions are opaque server-side hashes in HttpOnly, Secure, SameSite=Lax cookies.
- OAuth uses PKCE plus state bound to the browser. Wallet sign-in is single-use and domain-bound.
- Webhooks are signed. Uploads are re-encoded.
- No SQL injection or `dangerouslySetInnerHTML` was found.

The serious risks come from three places:

1. **The 2026-10-21 Railway breach is still not cleaned up.**
   - No re-key has run (SEC-2).
   - The bot token is not proven rotated, while the Telegram widget login is still routable (SEC-1).
   - `PROXY_SHARED_SECRET` is unset (SEC-3).
   - Backups are unproven (SEC-7).
2. **Account takeover paths into accounts that can trade real money:**
   - the one-tap Telegram deeplink phishing (SEC-4 = TG-1 = PP-1);
   - no step-up for live trading (SEC-5);
   - a latent LIVE bypass on the Telegram trading branch (TR-4).
3. **Abuse economics.** Free wallet Passports with a free rank-up are worth up to 6,000 XP. Royal rank means free VPN (PP-2, PP-4, SEC-6).

---

## 2. Threat model (short)

From security.md.

**Assets**
- Members' Hyperliquid API-wallet keys (can trade, cannot withdraw).
- Members' X tokens.
- The Telegram bot token (controls the bot and is the HMAC key for widget logins).
- Owner admin access (everything, including the secret store).
- The SQLite DB (about 1.17 GB) and `secrets.json` on the volume.
- Bucket backups.
- Personal data: emails, Telegram ids and messages, wallets, photos, VPN accounts.
- VPN private keys created on the server (VPN-9).
- The owner's KIWIVPN CI session (VPN-14).

**Actors**
1. The holder of the Railway env leaked on 2026-10-21.
2. Telegram phishers.
3. Automated abusers (Sybil XP or VPN accounts, sign-in floods).
4. A malicious member probing for IDOR.
5. Supply chain (unpinned PyPI packages).
6. Anyone with push access to the KIWIVPN repo (VPN-14).

**Trust boundaries**
- Vercel edge → Railway. It is unauthenticated while `PROXY_SHARED_SECRET` is unset, and the API is reachable directly at api.pricechat.live.
- Telegram bot polling: the identity source for deeplink and widget logins.
- Hyperliquid, Google and X OAuth.
- The private bucket.
- The VPN node agent (certificate-pinned).

**Highest-leverage attack paths**
- (a) Leaked bot token → forged widget login → any member's session → real-money trades (SEC-1 + SEC-5).
- (b) Leaked `APP_ENCRYPTION_KEY` plus any DB copy or backup → every stored secret in plaintext (SEC-2, TR-10).
- (c) Phished deeplink tap → the victim's session, or the victim's Telegram linked to the attacker (SEC-4).

---

## 3. Findings, ranked by risk

| Rank | IDs | Title | Severity | Status | Effort |
|---|---|---|---|---|---|
| 1 | SEC-1 (+SEC-14 / PP-16) | Leaked bot token can forge sign-ins: widget route live even in deeplink mode; widget payloads replayable for 24 h | Critical | CONFIRMED (code + live config); token rotation UNVERIFIED | S |
| 2 | SEC-2 + TR-10 | Breach rotation not done: no re-key at the latest startup; the old key decrypts members' Hyperliquid keys and all platform secrets | Critical | SUSPECTED (SEC-2, from logs) / UNVERIFIED (TR-10) | M |
| 3 | SEC-4 = TG-1 = PP-1 | Telegram deeplink sign-in phishable with one tap (login and account-link hijack) | High | CONFIRMED (code + audit tests) | S–M |
| 4 | SEC-3 = AR-13 | `PROXY_SHARED_SECRET` unset: every per-client rate limit bypassable through X-Forwarded-For | High | CONFIRMED | S |
| 5 | SEC-7 (+AR-1) | Backups unproven (AR-1: never succeeded); any old backup readable with the leaked key | High | UNVERIFIED (SEC-7) / CONFIRMED failing (AR-1) | S |
| 6 | SEC-5 | Live-trading actions need only a 30-day session cookie, no step-up | Medium | CONFIRMED | M |
| 7 | PP-2 + PP-4 + SEC-6 | Wallet sign-in: unlimited free Passports, a reward worth up to 6,000 XP (a free jump to Royal = free VPN), limiter keyed on IP + User-Agent, nonce table never purged | High (PP-2) / Medium (PP-4, SEC-6) | CONFIRMED (code + audit tests) | S–M |
| 8 | SEC-11 = AR-10 | Admin: no second factor; sessions survive a password change; no session list or revoke | Medium | CONFIRMED (audit test) | S–M |
| 9 | AR-11 | Sensitive admin actions not audit-logged (login, DB download, QA session mint, rekey, API keys, VPN admin) | Medium | CONFIRMED | M |
| 10 | TR-4 | feat/tg-trading: manual `/open` on LIVE skips the LIVE-enabled and API-wallet checks | Medium (latent) | CONFIRMED (audit test) | S |
| 11 | SEC-8 + SEC-9 | Known CVEs (Pillow 11.3.0: 56 advisories) in the local venv; no lock file; test tools in production | Medium | CONFIRMED (local) / UNVERIFIED (prod versions) | S |
| 12 | SEC-12 (+UX-9, TG-16) | Privacy page doesn't match what is collected; account deletion leaves data behind | Medium | CONFIRMED / SUSPECTED (deletion failure) | M |
| 13 | VPN-9 | Server-generated VPN private keys travel through Telegram, Railway and Vercel; privacy page doesn't mention Telegram | Medium | CONFIRMED (code) / SUSPECTED (bot live in prod) | S |
| 14 | VPN-14 | Windows CI e2e runs on the owner's real 90-day desktop session secret | Low | CONFIRMED | S |
| 15 | SEC-15 | Small authorization gaps from the IDOR sweep | Low | CONFIRMED | S |
| 16 | SEC-13 | Partial CSP on the site; no security headers on the API | Low | CONFIRMED | S |
| 17 | SEC-10 | CSRF rests on SameSite=Lax plus an Origin check only when Origin is present | Low | CONFIRMED | S |
| 18 | SEC-16 | Public market sanitiser is a deny-list | Low | CONFIRMED | S |

Related items in QA-FINDINGS.md (abuse or integrity, not access control):
- PP-5 (Royal reachable by clicking), PP-6 (cap races), PP-7, PP-8 (referral Sybils).
- TG-6 (bot compute usable from any group).
- VPN-4 (customer abuse attributed to the owner's personal IP).
- VPN-16 (trial abuse).
- TG "Untested": the Invite Bot "personal" mode collects third-party group members' usernames and activity, which is a privacy and Telegram ToS concern that needs an owner decision.

---

## 4. Finding details

### Rank 1: SEC-1 (+SEC-14 / PP-16): The widget login route forges sessions for anyone holding the bot token
- **Severity:** Critical while the token is not proven rotated
- **Status:** CONFIRMED in code and live config; rotation UNVERIFIED
- **Effort:** S
- **Evidence:**
  - `routes/members.py:756-770` registers `POST /api/members/auth/telegram/widget` unconditionally (not gated by `members_telegram_deeplink`).
  - `members/auth.py:306-321`: the only check is an HMAC keyed with `sha256(bot_token)`.
  - Live `GET /api/members/auth/providers` shows `telegram: {available: True, bot: 'Cryptopricechatbot', mode: 'deeplink'}`, so the site doesn't need the widget route.
  - Memory `price-chat-railway-todo.md` lists "Revoke at source: Telegram bot token" as pending.
  - SEC-14 / PP-16: `WIDGET_MAX_AGE = 86400` and hashes are never recorded as used (`auth.py:41,317-318`).
- **Impact:**
  - With the token, an attacker computes a payload for any Telegram id (ids of group members are visible) and gets a 30-day `pc_session`.
  - For members with Hyperliquid connected, the attacker can then:
    - enable live trading by typing a phrase (`trading.py:256`);
    - preview and confirm trades (`:125-150`);
    - change limits and AUTO (`:65`);
    - close positions, read PnL, and delete the account.
- **Repro (local only, not production):** build `{id, first_name, auth_date=now}`, HMAC it with `sha256(token)`, and POST it. A session cookie comes back.
- **Fix:**
  1. Rotate the bot token in BotFather now.
  2. Return 404 from the widget route in deeplink mode.
  3. Set the max age to about 300 s and make hashes single-use.
  4. Add the bot token to the rotation runbook.

### Rank 2: SEC-2 + TR-10: The breach rotation hasn't been done
- **Severity:** Critical (SEC-2) / High (TR-10)
- **Status:** SUSPECTED (SEC-2: strong indication from logs) / UNVERIFIED (TR-10: can't read variables or the DB)
- **Effort:** M
- **Evidence:**
  - Today's 05:14:13 startup has no `Rekey` line. `main.py:150-156` starts `rekey.run_at_startup` whenever `APP_ENCRYPTION_KEY_PREVIOUS` is set, and `rekey.py:136,146` always logs. So PREVIOUS is not set and no rotation is in progress.
  - Stored secrets are Fernet under `APP_ENCRYPTION_KEY` (`security/secrets.py:59-88`). That covers:
    - Hyperliquid agent keys (`tradingos/exchange.py:65-70`, `models/trading.py:24,27`; rekey covers `tr_accounts.*_agent_key`, `rekey.py:70-71`);
    - X tokens (`x_actions.py:55,75`);
    - the dashboard secret store (bot token, Telethon session, X, YouTube, Resend and Anthropic keys).
  - The admin device-cookie key derives from `ADMIN_PASSWORD_HASH` + `APP_ENCRYPTION_KEY` (`admin_auth.py:117-122`).
- **Impact:**
  - Anyone with the leaked env plus any DB copy can decrypt every member's trading key and every platform credential.
  - Trades alone can drain an account through bad fills (TR-10).
  - The leaked scrypt hash (N=2^14) can be attacked offline.
  - Leaked-env holders can mint "known device" cookies that skip the global login lockout.
  - Re-keying does not protect keys that were already exposed; only revoking them on Hyperliquid does.
- **Fix (the runbook, in order):**
  1. New Fernet key, with PREVIOUS set to the old one. Confirm `failed=0`, then remove PREVIOUS.
  2. New admin password.
  3. Rotate the bucket keys.
  4. Rotate the Google, X, TikTok and PSA secrets.
  5. Revoke at source: the bot token, the Telethon session, the other keys.
  6. Pause live trading globally and have members create new API wallets (`connect_api_key`), marking the old agents unusable.
  7. Add a key fingerprint and last re-key date to the startup self-check.

### Rank 3: SEC-4 = TG-1 = PP-1: Telegram deeplink sign-in can be phished with one tap
- **Severity:** High
- **Status:** CONFIRMED (code path; audit tests `test_pp1_telegram_start_link_phishing_signs_attacker_into_victims_passport` and `test_pp1b_telegram_link_phishing_attaches_victims_telegram_to_attacker` pass)
- **Effort:** S–M
- **Evidence:**
  - `members/auth.py:266-303`: `telegram_start` returns `t.me/Cryptopricechatbot?start=pc_<code>` (10-minute TTL). `telegram_seen_start` records whoever presses Start, and `telegram_claim` gives that identity to the browser that started the attempt.
  - `telegram/ingestion.py:267-290` (`_member_sign_in`); `routes/members.py:722-742` (status → `_set_session`).
  - The `pc_auth` binding proves only that the poller is the starter. The bot replies "✅ Done! You're signed in" with no confirmation step.
  - Live mode is deeplink, and email sign-in is off.
- **Repro:**
  1. The attacker starts "Sign in with Telegram" and sends the link ("claim 100 free XP").
  2. The victim taps Start in the real official bot.
  3. The attacker's browser polling `/auth/telegram/status/<id>` receives a 30-day session as the victim.
  4. With `purpose=link`, the victim's Telegram is attached to the attacker's Passport instead. The attacker gains the victim's Combot reputation (`reputation.py:28-42`), future Telegram XP and VIP gating, and the victim's /close and ENTER act on the attacker's account.
- **Impact:** a full web session. Trading routes (`trading.py:125-200`) need only the member cookie, which is the same escalation as SEC-1. VPN devices and Earn data are exposed too.
- **Fix:**
  - The bot replies with "Sign in to Price Chat on <browser/OS, approx. location, time>? [Yes, it's me] [No]" and verifies only on that callback from the same `from.id`.
  - And/or the user must type or pick a code shown in the browser.
  - The confirmation text names the purpose ("link to Passport X").
  - TTL of 3 minutes.
  - A "new sign-in" notice.
  - `/sessions` and `/unlink` commands (TG ideas).

  PP-1 also suggests the Login Widget instead, but that needs SEC-1's hardening first.

### Rank 4: SEC-3 = AR-13: The proxy secret is unset, so rate limits trust a forgeable header
- **Severity:** High
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:**
  - Production log `2026-11-08 05:14:13 WARNING app.security.client_ip PROXY_SHARED_SECRET is not set: rate limits still trust the left-most X-Forwarded-For entry…`.
  - `security/client_ip.py:111-114`.
  - The API is reachable directly. Open since 2026-10-23.
- **Impact:** every per-IP bucket becomes per-request: admin login (5/15 min), email codes, signin, `vpn_new`, `vpn_pay`, committee, market, chart and the Vortex limiter. Only the shared caps remain:
  - admin global 30/15 min (an owner lockout from new browsers);
  - email "all" 300/h (an attacker can burn the site's email budget and Resend reputation);
  - market 1200.
- **Fix:**
  1. Set one 64-hex value in Vercel (Production).
  2. Redeploy the frontend.
  3. Set the same value in Railway.
  4. Confirm the log line "Website traffic carries the proxy secret".
  5. Optionally reject direct-to-Railway traffic without it, except webhooks and workers.

### Rank 5: SEC-7 (+AR-1): Backups are unproven and readable with the leaked key
- **Severity:** High (availability / privacy)
- **Status:** SEC-7 UNVERIFIED for today; AR-1 CONFIRMED that the nightly job failed with `SignatureDoesNotMatch` on 2026-11-07 22:45, with 12 of 12 failures 10-23..11-03 per memory
- **Effort:** S
- **Evidence:** `jobs.py:916-926`, plus AR-1's log line.
- **Impact:** a 1.17 GB DB with possibly no off-site copy. Any backup that did succeed before the breach is decryptable with the old key.
- **Fix:**
  - Fix the bucket credentials as part of rotation.
  - Set the owner alert chat.
  - Add `backup_age_hours` to health.
  - Purge backups sealed under the old key after re-keying.
  - See PERFORMANCE-RELIABILITY.md for the restore drill.

### Rank 6: SEC-5: No step-up re-authentication for live trading
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** M
- **Evidence:**
  - `trading.py:256` (live/enable takes a typed phrase), `:65` (settings), `:142` (confirm), `:367` (paste API key).
  - Sessions slide for 30 days (`members/service.py:162-174`).
- **Impact:** multiplies SEC-1, SEC-4 and any cookie theft into weeks of real-money trading.
- **Fix:**
  - Require a fresh SIWE signature or a Telegram confirm for live/enable, raising limits, AUTO, and connecting or replacing keys.
  - Send a Telegram notice for every new session and every live-mode change.
  - Add an active-sessions list with "sign out everywhere".

### Rank 7: PP-2 + PP-4 + SEC-6: Wallet sign-in abuse
- **Severity:** High (PP-2) / Medium (PP-4, SEC-6)
- **Status:** CONFIRMED (code; audit tests `test_pp7_wallet_reward_grows_with_rank…` and `test_pp4_…` pass); real-world abuse not tested
- **Effort:** S (reward) / M (limiter)
- **Evidence:**
  - `tradingbot/service.py:410-438` `free_level`: `need = max(100, next.min_xp - rank_score)`. A member at 10,000 XP verifying an empty new wallet gets +6,000 and reaches Mythic.
  - Royal (level 7, 3,500) = free VPN (`vpn/service.py:138,168-170`).
  - The live leaderboard has members at 3,499 and 1,850, each one free signature from Royal.
  - Limiter: `_visitor` = sha256(client_ip | User-Agent) (`routes/tradingbot.py:53-56`), 10 per 10 min. 15 Passports were created from one IP by rotating the UA, each with at least 120 XP.
  - `login_nonce` inserts a `TbNonce` row that is never deleted. The `_hits` dict is unbounded (`service.py:56, 440-484`).
- **Impact:**
  - Sybil ranked Passports: VPN slots, leaderboards, gated content, polluted funnel analytics.
  - Unauthenticated growth of the DB and memory.
  - Devalues the ranks.
- **Fix:**
  - A flat wallet reward (about 100 XP), with larger amounts only for verified deposit tiers.
  - Key the limiter on client IP only (/64 for IPv6), plus a global creation cap.
  - New wallet-only Passports earn nothing automatic until they link a platform or pass a CAPTCHA.
  - Purge `TbNonce` in `retention.py` and bound `_hits`.

### Rank 8: SEC-11 = AR-10: Admin hardening gaps
- **Severity:** Medium
- **Status:** CONFIRMED (audit test `audit_admin_session_survives_password_change.py`)
- **Effort:** S–M
- **Evidence:**
  - `admin_auth.py:170-221`: `current()` checks only the token hash and expiry. Known-device cookies are revoked on a password change; sessions are not.
  - One identity, no roles, no 2FA.
  - `ADMIN_SESSION_HOURS` defaults to 12 (max 720).
  - `last_seen_at` is never updated.
- **Impact:** after the breach (the hash was leaked), changing the password leaves stolen sessions valid.
- **Fix:**
  - Store a credential fingerprint on each AdminSession.
  - Add "sign out everywhere" and an active-sessions list.
  - Add TOTP or a Telegram confirm-to-login.
  - Add a read-only viewer role if anyone else gets access.

### Rank 9: AR-11: Sensitive admin actions are not audit-logged
- **Severity:** Medium
- **Status:** CONFIRMED
- **Effort:** M
- **Evidence:** there is no entry for:
  - admin login success or failure (`admin.py:32-47`);
  - `GET /api/system/db-backup` (all member PII);
  - `/system/qa-member-session` (mints a member session);
  - `/system/rekey`;
  - API-key save and delete (`api_keys.py:141-157`);
  - VPN admin actions (`vpn.py:294-440`; no audit call in `vpn/service.py`).

  There are 9 separate audit stores with no unified view.
- **Impact:** no forensic trail for exactly what an attacker with a stolen session would do.
- **Fix:** one `admin_audit` table written by middleware for every non-GET admin request, plus explicit login and DB-download entries, and an Admin › Activity filter.

### Rank 10: TR-4: feat/tg-trading manual `/open` on LIVE skips the LIVE checks
- **Severity:** Medium (latent: active only once `strategies.telegram_trading.live` is set; the branch is not deployed)
- **Status:** CONFIRMED (reproduced)
- **Effort:** S
- **Evidence:**
  - `tgtrade.py:304-397` checks only `mode_gate` and the referral gate. It never runs `acc.live_enabled_at and live_allowed(...)` (`execution.py:283, 368`) or `check_live_permission` (119-134).
  - Audit test `audit_tg_trading_live_gate.py` (run from the tg-trade worktree): an account with `mode=LIVE` and `live_enabled_at=None` fills a LIVE order. That state arises after `connect_api_key` with open LIVE positions (`accounts.py:525-529`).
- **Impact:** real-money orders for accounts whose LIVE was withdrawn, or whose verified wallet changed.
- **Fix:** call the same LIVE preamble in `_sizing`, plus `allowed_coins` and the v2 checks (TR-11). It must be fixed before the branch merges.

### Rank 11: SEC-8 + SEC-9: Vulnerable dependencies and no lock file
- **Severity:** Medium (High if production resolves to the same versions)
- **Status:** CONFIRMED for the local venv / UNVERIFIED for production
- **Effort:** S
- **Evidence:** `pip-audit` on the project venv:

  | Package | Version | Advisories | Fixed in |
  |---|---|---|---|
  | pillow | 11.3.0 | 56 | 13.0.0 |
  | python-multipart | 0.0.20 | 6 | 0.0.31 |
  | pyasn1 | 0.6.4 | 4 | 0.6.5 |
  | certifi | 2026.7.22 | 1 | 2026.10.18 |
  | opentelemetry-api | 1.45.0 | 1 | 1.45.1 |

  - Pillow parses untrusted uploads (`portfolio/photos.py:27-70`).
  - `npm audit --omit=dev` reports ws (high) through ethers (moderate). Those are node-only paths, so Low.
  - `requirements.txt` uses only `>=`, with no hashes; pytest and pytest-asyncio ship to production (also AR-8).
- **Fix:**
  - Set floors `pillow>=13`, `python-multipart>=0.0.31`, `pyasn1>=0.6.5`.
  - Use a `requirements.lock` with hashes (`--require-hashes`) and a separate dev file.
  - Add Dependabot or Renovate.

### Rank 12: SEC-12 (+UX-9, TG-16): Privacy page vs reality; deletion leaves data
- **Severity:** Medium
- **Status:** CONFIRMED (code and page); deletion side effects SUSPECTED (not run)
- **Effort:** M
- **Evidence:**
  - `community.tsx:302-329` "What we keep" omits:
    - emails;
    - wallets and encrypted Hyperliquid keys;
    - trading data;
    - photos;
    - marketplace data;
    - VPN accounts;
    - Telegram messages republished by `GET /api/website/chat` (live fields `messages[].name` and `.text`);
    - committee runs.
  - UX-9 adds Kalshi, email sign-in and localStorage, and notes the "Draft" banner.
  - Deletion (`members/service.py:283-294`) relies on cascades. Market FKs are `ondelete="RESTRICT"` (`models/market.py:51,119,180,181,257,274`), so a member with market rows probably can't be deleted. Bucket photos are not removed, and Telegram messages remain.
  - TG-16: all allow-listed group messages are stored although the community jobs are off.
- **Fix:**
  - Rewrite the page from the models, with retention periods.
  - Purge bucket objects and anonymise marketplace rows on delete.
  - Document or trim the Telegram preview.
  - Add a data-export endpoint.
  - Minimise message storage.

### Rank 13: VPN-9: Server-generated private keys through Telegram
- **Severity:** Medium
- **Status:** CONFIRMED (code) / SUSPECTED (bot live in prod)
- **Effort:** S
- **Evidence:**
  - `tradingos/bot.py:612-652`: `/vpn add` calls `create_device` without a public key, so the key is generated on the server (`service.py:549`) and the `.conf` with `PrivateKey` is sent as a Telegram document. Telegram bot chats are not end-to-end encrypted.
  - The website flow also generates keys on Railway and returns them through Vercel.
  - The privacy page (`community.tsx:341`) doesn't list Telegram.
- **Fix:** drop `/vpn add` or send a link instead, generate keys in the browser (WebCrypto X25519) and send only `public_key`, and update the privacy page.

### Rank 14: VPN-14: CI uses the owner's real session
- **Severity:** Low
- **Status:** CONFIRMED (workflow)
- **Effort:** S
- **Evidence:** `ci/kiwi-windows.yml:333` `KIWI_CI_SESSION`. Per memory this is the owner's "kd." desktop session, scoped to `/api/members/me` and all of `/api/members/vpn/` (`desktop_scope.py:7-8`). The private repo is shared with the iOS export.
- **Impact:** anyone with push access can exfiltrate the session through a workflow. Tests consume the owner's device slots and rate-limit tokens.
- **Fix:** a dedicated tester account on a testers-only server. Revoke the session after the campaign.

### Rank 15: SEC-15: Small authorization gaps (no member-to-member data exposure)
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:**
  - (a) `market/raw.py:34-38`: comments and condition votes ignore sandbox visibility.
  - (b) `workers/service.py:539-543`: `can_review` ignores `ended_at`.
  - (c) `tradingbot/service.py:679-693`: `signal_act` accepts any `signal_id`, so `earn.first_trade` XP can be farmed.
  - (d) `portfolio.py:254-276`: the QR handoff gives a full session to whoever opens the link within 10 minutes.
  - (e) `market.py:186-189`: photos follow the item, not the listing.
  - (f) `realm_online.py:25,119-121`: Realm WS tickets are reusable for 12 h.
- **Fix:** a one-line check for each. For (d), add a confirm step or a short session.

### Rank 16: SEC-13: Partial CSP; no API headers
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:**
  - CSP is `script-src 'self' youtube…; object-src 'none'; base-uri 'self'; frame-ancestors 'self'`, with no default-src, connect-src, img-src or form-action.
  - `api.pricechat.live` sends only `vary: Origin`.
  - Raw `href={x.url}` from scraped data (`PokemonCardPage.tsx:730`, `contentItems.tsx:62`).
- **Fix:** a fuller CSP, an API header middleware (nosniff, HSTS, `frame-ancestors 'none'`), and only http(s) hrefs.

### Rank 17: SEC-10: CSRF defence is brittle
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `main.py:268-273` skips the check when Origin is absent and runs only in production. No exploit today (strict JSON content type).
- **Fix:** require Origin or `Sec-Fetch-Site: same-origin` on cookie-authenticated writes.

### Rank 18: SEC-16: Deny-list market sanitiser
- **Severity:** Low
- **Status:** CONFIRMED
- **Effort:** S
- **Evidence:** `pokemon/public.py:21,53-70`. New admin fields become public by default, so licensed PriceCharting or PSA data could leak.
- **Fix:** an allow-list per path, plus a test for unknown keys.

---

## 5. What's solid (verified, no finding)

From security.md "Verified OK", trading.md TR-17 and kiwi-vpn.md "What is good".

- **Routing:**
  - `admin_guard` refuses odd paths (audit test `audit_guard_paths.py`: `//api/settings` 404, `/api//settings` 401, `/api/members/../settings` 401, `%2e%2e` 404, `HEAD` 401).
  - `/admin/*` APIs return 401 unauthenticated (`/api/admin/overview`, `/api/rewards-admin/overview`).
  - `/docs` and `/openapi.json` return 404 in production.
- **Admin login:** scrypt, constant-time compare, per-client and global limits, known-device cookie, sessions stored as sha256.
- **Desktop tokens:** `kd.` scope enforced in middleware. The session is read only from the cookie. The exchange is PKCE-style and single-use, and refresh rotates the token.
- **OAuth:**
  - Google and X: PKCE S256, single-use 10-minute state bound to the browser, server-configured redirect URIs. Google `aud`/`iss`/`exp` are checked.
  - YouTube and TikTok connectors use PKCE.
- **Wallet sign-in:** domain-bound, 128-bit nonce spent before verification, 10-minute expiry, signer must match.
- **Email codes:** from `secrets`, 5 attempts per code, 5/h and 10/day per email.
- **Webhooks and workers:**
  - NOWPayments HMAC-SHA512 (constant time); Stripe v1 with timestamp tolerance; TikTok raw-body signature.
  - Worker and Vortex bearer tokens are hashed or compared in constant time.
- **Telegram:**
  - Callbacks act on `from.id` and the caller's own data (`tradingos/bot.py:1051-1130`).
  - HTML output is escaped (`newsdesk/brief.py:276-293`, `esc()`).
  - Bot-token URLs never reach exceptions (`telegram/client.py:221-234`).
- **Logs:** route templates only. A sample of 2,834 production lines had no emails, IPs, tokens or `HTTP Request` lines.
- **Injection and XSS:** no f-string SQL outside the constants in `db_reclaim.py`. No `dangerouslySetInnerHTML` or `innerHTML`. SEO previews and JSON-LD are escaped.
- **Uploads:** 15 MB cap, PIL format check, 50 MP limit, re-encoded to JPEG with EXIF stripped, random keys, private bucket, `nosniff`.
- **SSRF and redirects:** no SSRF from public routes. `/api/r/{placement}` redirects only to admin-configured URLs.
- **IDOR:** a static sweep of all public id-taking routes found no member-to-member exposure (only SEC-15).
- **Trading:**
  - Every member route resolves the account from the session, and the tg callbacks bind to the member.
  - Agent keys are validated with `userRole`, main-wallet keys are refused, keys are never echoed, and one agent maps to one Passport.
  - ApproveAgent checks the EIP-712 signer and the nonce window (TR-17).
- **Kiwi VPN:**
  - The node agent is pinned by certificate fingerprint inside the TLS handshake before the token is sent (`node.py:47-77`, test passes).
  - Constant-time token check, no request logging, 4 KB body cap.
  - The proxy refuses private and own IPs and mail ports.
  - App keys are generated on the device (DPAPI / Android Keystore with `allowBackup=false`), and the backend refuses duplicate public keys.
  - The Windows named pipe is restricted to SYSTEM, interactive users and the signed path, with strict schemas.
- **Real Hyperliquid testnet probe (coordinator, verified):** with a throwaway unfunded key, info reads worked and signing was recognised by the exchange. Leverage, order and stop refusals ("User or API Wallet … does not exist") came back as ERROR results and never as fills.

---

## 6. What couldn't be verified

- **Whether any secret was rotated** (bot token, admin password, bucket keys, OAuth secrets, member Hyperliquid keys). Reading Railway variables or the secret store is forbidden (SEC-2, TR-10, SEC-1).
- **Whether the 2026-10-21 breach included volume or DB access.** This decides whether SEC-2 / TR-10 is a theoretical or a practical exposure.
- **Today's backup outcome** (SEC-7). Only the newest 5,000 log lines were readable. AR-1 did find the 11-07 failure.
- **Production package versions** (SEC-8). There is no lock file, and `railway ssh` is forbidden.
- **The account-deletion failure with market RESTRICT rows** (SEC-12). Inferred from the schema; not run.
- **SEC-1, SEC-4 and PP-1 against production.** No logins were allowed; they are proven by code, local tests and the live providers config.
- **The anonymous VPN key and NOWPayments replay paths.** Read but not exercised. The NOWPayments IPN signature format is unproven because no real payment has happened (VPN Untested).
- **Live VPN server state** (firewalls, ufw, logging, ash1 delete protection, wg0/wg1 isolation). Not touched (VPN Untested).
- **The Invite Bot "personal" mode privacy question.** Needs an owner decision (TG Untested).
