# Security & Privacy (SEC)

Reviewer: security/privacy. Code at audit worktree `C:\AI\pc-wt\audit` (= main e978cc3). Production was touched only with public, unauthenticated GETs/HEADs and `railway logs` (read).

## Summary

The basic web-app hygiene is good. Every `/api` route is private by default: `admin_guard` (`backend/app/main.py:261`) uses an allow-list (`security/admin_auth.py:209-236`). Of 778 routes, 461 are admin-only, 300 are public-prefix and 17 are public market reads. A static IDOR sweep of every public route that takes an id found nothing that crosses between members. Sessions are opaque random tokens stored server-side as hashes, in HttpOnly + Secure + SameSite=Lax cookies. Writes from another site are refused by an Origin check. OAuth uses PKCE plus state bound to the browser. The wallet sign-in is single-use, domain-bound and expires. Webhooks verify signatures. Uploads are re-encoded with PIL. I found no SQL injection, no `dangerouslySetInnerHTML`, and escaped Telegram HTML. API docs are off in production.

**Biggest risks:**
1. **The 2026-10-21 Railway breach is still not cleaned up (SEC-1, SEC-2, SEC-3).**
   - Today's 05:14 startup shows no key rotation ran, and `PROXY_SHARED_SECRET` is still unset.
   - The Telegram Login Widget endpoint is still live in production even though the site uses deeplink mode. So anyone holding the (possibly leaked) bot token can sign in as any Telegram-linked member, including members with live Hyperliquid trading.
2. **The Telegram deeplink sign-in can be phished with one tap (SEC-4).** The person who presses Start in Telegram signs in whichever browser created the link. There is no confirmation step.
3. **Every per-client rate limit can be bypassed** by forging X-Forwarded-For while the proxy secret is unset (SEC-3). The wallet sign-in limiter also uses the User-Agent, so changing that is enough to bypass it (SEC-6).
4. **Backups:** no evidence of a successful backup.
5. **Dependencies:** the backend has no lock file (SEC-9). The local venv has Pillow 11.3.0 with 56 known advisories, and Pillow decodes untrusted uploads.

**Biggest opportunities:** finish the rotation runbook, turn off the widget route in deeplink mode, add a confirm button to the Telegram sign-in, and require recent re-authentication before live-trading actions.

**Threat model (short)**
- **Assets:**
  - Members' Hyperliquid API-wallet keys. They can trade real money but cannot withdraw.
  - Members' X tokens (can post as the member).
  - The Telegram bot token: it controls the bot, and it is the HMAC key for widget logins.
  - Owner admin access: it controls everything, including the secret store.
  - The SQLite database (about 1.17 GB on the Railway volume) and `secrets.json`.
  - Bucket backups.
  - Personal data: emails, Telegram ids and messages, wallet addresses, photos, VPN accounts.
- **Actors:**
  1. Whoever obtained the Railway environment variables in the 2026-10-21 breach.
  2. Phishers who target members through Telegram.
  3. Automated crawlers and abusers (Sybil accounts for XP or VPN slots, sign-in floods).
  4. A malicious member probing for IDOR.
  5. Supply chain (unpinned PyPI packages).
- **Trust boundaries:**
  - Vercel edge → Railway. It is unauthenticated today because `PROXY_SHARED_SECRET` is unset.
  - Telegram bot polling. This is the identity source for deeplink and widget logins.
  - Hyperliquid, Google and X OAuth.
  - The private bucket.
- **Highest-leverage paths:**
  - (a) Leaked bot token → forged widget login → any member's session → real-money trades.
  - (b) Leaked APP_ENCRYPTION_KEY plus any copy of the database or a backup → every stored secret in plaintext.
  - (c) Phished deeplink tap → victim's session.

## Findings

### SEC-1: The leaked Telegram bot token can forge sign-ins: the widget login route is live even in deeplink mode
Severity: Critical (while the bot token is not proven rotated) · Status: CONFIRMED in code and live config; whether the token was rotated is UNVERIFIED · Effort: S

**Evidence:**
- `backend/app/api/routes/members.py:756-770`: `POST /api/members/auth/telegram/widget` is always registered. It is not gated by `settings.members_telegram_deeplink`.
- `backend/app/members/auth.py:306-321`: the only check is an HMAC whose key is `sha256(bot_token)`. That follows Telegram's specification, which means anyone who knows the token can make valid payloads for any Telegram user id.
- Live: `curl https://api.pricechat.live/api/members/auth/providers` returns `telegram: {'available': True, 'bot': 'Cryptopricechatbot', 'mode': 'deeplink'}`. The site therefore doesn't need the widget route at all.
- Memory note `price-chat-railway-todo.md` item 2 says "Revoke at source: Telegram bot token (BotFather)" is still pending. No later note says it was done.

**Impact:** an attacker who has the token can compute a widget hash for `id=<victim telegram id>` with a fresh `auth_date` and get a 30-day `pc_session` for that member. Telegram ids of active group members are visible to anyone in the group. With the victim's session the attacker can, for members with Hyperliquid connected:
- turn on live trading (a typed phrase, no re-authentication; `api/routes/trading.py:256`);
- preview and confirm real-money trades (`:125-150`);
- change limits and AUTO (`:65`);
- close positions;
- read wallet and PnL;
- delete the account.

The token itself also lets the attacker read and send as the bot.

**Repro:** don't run this against production. Locally: build `data` = {id, first_name, auth_date=now}, then `hash = HMAC_SHA256(key=sha256(token), "\n".join(sorted k=v))`, POST it to the widget route, and a session cookie comes back.

**Fix:**
1. Rotate the bot token in BotFather now, and update the secret store or env.
2. Return 404 from the widget route when `members_telegram_deeplink` is on, and keep a feature flag for the widget.
3. Cut `WIDGET_MAX_AGE` from 86400 to about 300 s and make each `hash` single-use.
4. Treat the bot token as a sign-in secret in the rotation runbook.

### SEC-2: The breach rotation hasn't been done: no re-key at the latest startup, and the old key still decrypts every stored secret
Severity: Critical · Status: SUSPECTED (strong indication from logs; variables can't be read under the rules) · Effort: M

**Evidence:**
- `railway logs --service api --lines 5000` covers today's 05:14:13 startup. It has no `Rekey` line.
- `main.py:150-156` starts `rekey.run_at_startup` whenever `APP_ENCRYPTION_KEY_PREVIOUS` is set, and `security/rekey.py:136,146` always logs `Rekey: ...` / `Rekey finished`. So PREVIOUS is not set in the running deploy, meaning no rotation is in progress.
- The memory note (2026-11-03) records that PREVIOUS was absent and that items 1 and 4 are open.

**What the leaked key decrypts:** stored secrets are Fernet under `APP_ENCRYPTION_KEY` (`security/secrets.py:59-88`). That covers:
- members' Hyperliquid API-wallet keys (`tradingos/exchange.py:65-70`, `models/trading.py:24,27`);
- members' X tokens (`members/x_actions.py:55,75`);
- the dashboard secret store: bot token, Telethon session, X/YouTube/Resend/Anthropic keys.

**Impact:**
- Anyone with the leaked environment plus any copy of the database (volume, backup, local copy) can decrypt every member's trading key and every platform credential.
- `ADMIN_PASSWORD_HASH` was in the same environment. Scrypt at N=2^14 can be attacked offline, and existing admin sessions are not tied to the password (SEC-11).
- The admin device-cookie key is derived only from `ADMIN_PASSWORD_HASH` + `APP_ENCRYPTION_KEY` (`admin_auth.py:117-122`). Holders of the leaked environment can mint "known device" cookies, which lets them skip the global login-lockout bucket.

**Fix:** run runbook items 1–2 in order:
1. New Fernet key, with PREVIOUS set to the old one. Confirm `failed=0` in the log, then remove PREVIOUS.
2. New admin password.
3. Rotate the bucket keys.
4. Rotate the Google/X/TikTok/PSA secrets.
5. Revoke at source: bot token, Telethon session, the other API keys.
6. Ask members to re-approve their Hyperliquid API wallets. Re-sealing does not help if the old ciphertext plus the old key were copied.

Also add an owner-only "key fingerprint and last re-key date" line to the startup self-check, so this state shows up without reading variables.

### SEC-3: `PROXY_SHARED_SECRET` is unset in production, so every per-client rate limit can be bypassed with a forged X-Forwarded-For
Severity: High · Status: CONFIRMED · Effort: S

**Evidence:**
- Production log `2026-11-08 05:14:13,433 | WARNING | app.security.client_ip | PROXY_SHARED_SECRET is not set: rate limits still trust the left-most X-Forwarded-For entry, which direct callers can forge.`
- Code: `security/client_ip.py:111-114` (the legacy branch).
- The API can be reached directly at `https://api.pricechat.live`, and `vercel.json` also rewrites to `api-production-b1be.up.railway.app`.

**Impact:** every per-IP bucket becomes per-request for someone who sets `X-Forwarded-For: <random>`:
- admin login (5 per 15 min per client);
- email codes (10 per hour per IP);
- `signin`, `vpn_new` (anonymous VPN keys), `vpn_pay`, `committee`, `market`, `chart`, the Vortex failed-token limiter.

Only the shared caps still hold:
- Admin login keeps its global 30 per 15 min. That is an owner lockout from any new browser; known devices are exempt.
- Email keeps "all" 300 per hour. An attacker can use that to burn the whole site's email-code budget and the Resend sending reputation, which locks out email sign-in.
- The market keeps 1200 shared.

**Fix:** set the same 64-hex value in Vercel (Production) first, redeploy the frontend, then set it in Railway. Confirm the log line "Website traffic carries the proxy secret". Optionally reject direct-to-Railway API traffic that lacks the secret, except for webhooks and workers.

### SEC-4: Telegram deeplink sign-in can be phished with one tap (login and account-link hijack)
Severity: High · Status: CONFIRMED (code path) · Effort: S–M

**Evidence:**
- `members/auth.py:266-303` and `telegram/ingestion.py:267-290`. The attacker's browser calls `POST /members/auth/telegram/start` and gets `t.me/Cryptopricechatbot?start=pc_<code>`, valid 10 minutes.
- Whoever sends `/start pc_<code>` becomes the identity (`telegram_seen_start`).
- The browser that created the link then polls `/auth/telegram/status/{id}` and receives the session.
- The bot only sends "✅ Done! You're signed in" afterwards. There is no "Sign in on a browser? [Confirm]" step, and no code for the user to compare.
- Live mode is `deeplink`.

**Impact:**
- The attacker posts the link in the group or sends it by DM ("tap Start to claim your XP / VIP access"). The victim taps Start, and the attacker gets the victim's member session, with the same consequences as SEC-1 for trading members.
- With `purpose=link` the attacker instead binds the victim's Telegram to the attacker's Passport. The attacker gets the victim's Telegram reputation and VIP group gating, and the victim can no longer link it to their own account.

**Fix:**
- On a valid code, have the bot reply with an inline "Confirm sign-in to Price Chat in a browser (started HH:MM, from <coarse location/UA>)" button, and verify only after that callback from the same `from.id`.
- And/or show a short code in the browser and ask the user to type it to the bot.
- Shorten the time-to-live to 3 minutes.

### SEC-5: Live-trading actions need only a 30-day session cookie: no step-up re-authentication
Severity: Medium · Status: CONFIRMED · Effort: M

**Evidence:**
- `api/routes/trading.py:256` (`live/enable` takes a typed phrase), `:65` (settings), `:142` (confirm), `:367` (paste a Hyperliquid API key).
- Member sessions slide for 30 days (`members/service.py:162-174`, `settings.member_session_days=30`).

**Impact:** this makes SEC-1, SEC-4 and any session theft much worse. A stolen cookie can trade real money for weeks.

**Fix:**
- Require a fresh wallet signature (the SIWE flow already exists) or a Telegram confirm button for `live/enable`, for raising limits, for enabling AUTO, and for connecting or replacing API keys.
- Send a Telegram notice on every new session and every live-mode change.
- Show active sessions with a "sign out everywhere" button, like the existing desktop-session revoke.

### SEC-6: Wallet sign-in creates unlimited free Passports with a free rank-up; its limiter is keyed on IP + User-Agent and is never cleaned up
Severity: Medium · Status: CONFIRMED (code); abuse not tested · Effort: S

**Evidence:**
- `api/routes/tradingbot.py:53-56`: the limiter key `_visitor` = sha256(client_key | User-Agent). A new User-Agent gives a new bucket, even after SEC-3 is fixed. There is no global cap.
- `tradingbot/service.py:440-484`: `login_nonce` inserts a `TbNonce` row per call, and nothing deletes them. A grep for TbNonce delete or retention found nothing.
- `login_verify` creates a new member for any new wallet, and `free_level` pays enough XP to reach the next rank (`:395-417`).
- `_hits` (`service.py:56`) is an in-memory dict that grows with every new key.

**Impact:**
- Scripted fresh EOAs give unlimited ranked Passports. That is Sybil access to whatever rank unlocks: VPN slots, XP leaderboards, gated content.
- Unauthenticated callers can grow the database (and memory) without limit.

**Fix:**
- Key on `client_key` only and add a shared cap.
- Purge expired `TbNonce` rows in `retention.py`.
- Pay the wallet XP only after some real signal (age, a Telegram link, a deposit), or cap new wallet sign-ups per day.

### SEC-7: Backups are unproven, and old backups would be readable with the leaked key
Severity: High (availability / privacy) · Status: UNVERIFIED today; memory says 12 of 12 nightly runs failed with `SignatureDoesNotMatch` up to 2026-11-03 · Effort: S

**Evidence:**
- `jobs.py:916-926`. The job is not in today's "Background jobs turned off" list.
- The run due at about 05:29 logged nothing in the 5000 lines I could read. Failures would show as `Owner alert (no owner Telegram chat set, logged only)`, and none appeared for `system.backup` today.
- The startup self-check still warns: "owner alerts have no Telegram chat".

**Impact:**
- A database of about 1.17 GB on a single volume may have no off-site copy.
- Owner alerts go nowhere, so failures stay silent.
- If any backup did succeed before the breach, it is decryptable with the old key (SEC-2).

**Fix:**
- Fix the bucket credentials as part of rotation.
- Set the owner alert chat.
- Add a public-safe `backup_age_hours` to the owner health view.
- After re-keying, purge backups that were sealed under the old key.

### SEC-8: Pillow and python-multipart have known CVEs locally; Pillow parses untrusted member uploads
Severity: Medium (High if production resolves to the same versions) · Status: CONFIRMED for the local venv; UNVERIFIED for production (no lock file, see SEC-9) · Effort: S

**Evidence:** `pip-audit -r freeze.txt --no-deps --disable-pip` on `C:/AI/price-chat/backend/.venv` reported:

| Package | Version | Unique advisories | Fixed in |
|---|---|---|---|
| pillow | 11.3.0 | 56 | 13.0.0 |
| python-multipart | 0.0.20 | 6 | 0.0.31 |
| pyasn1 | 0.6.4 | 4 | 0.6.5 |
| certifi | 2026.7.22 | 1 | 2026.10.18 |
| opentelemetry-api | 1.45.0 | 1 | 1.45.1 |

Uploads go through PIL (`portfolio/photos.py:27-70`). There is a decompression-bomb guard and `verify()`, but decoder bugs fire before re-encoding.

Frontend: `npm audit --omit=dev` reports ws 8.0–8.20.1 (high) through ethers 6.16 (moderate). That is node-only code paths; the browser uses native WebSocket, so it is Low.

**Fix:** set floors `pillow>=13`, `python-multipart>=0.0.31`, `pyasn1>=0.6.5`, and pin them (SEC-9). Upgrade ethers when a release with a fixed ws ships.

### SEC-9: The backend has no lock file, so every Railway build pulls the newest packages, and test tools ship to production
Severity: Medium · Status: CONFIRMED · Effort: S

**Evidence:**
- `backend/requirements.txt` uses only `>=` floors: fastapi>=0.115, cryptography>=44, telethon>=1.45,<2, and so on.
- There is no uv.lock, poetry.lock or hashes. Railpack builds from it (`railway.json`).
- `pytest` and `pytest-asyncio` are in the production requirements.

**Impact:**
- Supply-chain exposure: a compromised or broken new release reaches production on the next deploy.
- Builds can't be reproduced, and auditing production versions is impossible.

**Fix:** a `requirements.lock` with hashes (`pip-compile --generate-hashes` or `uv pip compile`), built with `--require-hashes`; test dependencies in a separate dev file; Dependabot or Renovate.

### SEC-10: CSRF defence rests on SameSite=Lax plus an Origin check that only runs in production and only when Origin is present
Severity: Low · Status: CONFIRMED · Effort: S

**Evidence:** `main.py:268-273`. The check is skipped when the `Origin` header is absent. CORS has `allow_credentials=False`.

**Impact:** browsers do send Origin on cross-site POST, and FastAPI's strict JSON content type blocks simple form posts, so I see no exploit today. The defence is brittle, though, if a route ever accepts form data. That includes the admin's `/api/system/*` routes.

**Fix:** require Origin or `Sec-Fetch-Site: same-origin` on cookie-authenticated writes, exempting only the bearer/webhook prefixes.

### SEC-11: Admin hardening gaps: no second factor, and sessions survive a password change
Severity: Medium · Status: CONFIRMED · Effort: M

**Evidence:**
- `security/admin_auth.py:170-205`: one password. `current()` only checks the token hash and expiry, and nothing ties a session to the password hash.
- Sessions last 12 h by default.

**Impact:** after the breach (the hash was leaked), changing `ADMIN_PASSWORD_HASH` leaves existing sessions valid for up to 12 h. There is no TOTP or WebAuthn on the account that can read every secret's metadata, replace secrets, delete members, and so on.

**Fix:**
- Store a fingerprint of the password hash on each AdminSession and reject a mismatch.
- Add TOTP or a Telegram confirm-to-login for the owner.
- Add a "sign out all admin sessions" button.

### SEC-12: Privacy page doesn't match what is collected; account deletion leaves data behind
Severity: Medium (compliance / trust) · Status: CONFIRMED (code and page); deletion side effects SUSPECTED · Effort: M

**Evidence:** `frontend/src/website/community.tsx:302-329` ("What we keep") lists only member id, username, settings, connected account ids and XP. In reality the app also stores:
- email addresses (email sign-in, `members/email_login.py`);
- wallet addresses and Hyperliquid API-wallet private keys, encrypted;
- trading account, orders and positions;
- uploaded portfolio and market photos (bucket);
- marketplace orders and wallets;
- Kiwi VPN accounts;
- public Telegram group messages and names. These are republished on the website by `GET /api/website/chat`; live fields are `messages[].name`, `messages[].text`.
- Committee runs (requester id on the row).

Deletion (`members/service.py:283-294`) relies on database cascades:
- Market tables use `ondelete="RESTRICT"` (`models/market.py:51,119,180,181,257,274`). Deleting a member who has a market item or order will raise, so the account can't be deleted (SUSPECTED; not run).
- Bucket photo objects are not removed. Nothing in `delete_member` touches storage.
- Telegram messages keyed by Telegram user id remain.

**Fix:**
- Rewrite "What we keep" from the actual models, adding retention periods.
- Make deletion explicitly purge bucket objects and anonymise marketplace rows instead of RESTRICT.
- Document the Telegram preview, or drop names from it.
- Add a data-export endpoint.

### SEC-13: The live site's CSP is partial; the API sends no security headers
Severity: Low · Status: CONFIRMED · Effort: S

**Evidence:**
- `curl -I https://pricechat.live`: `Content-Security-Policy: script-src 'self' https://www.youtube.com https://s.ytimg.com; object-src 'none'; base-uri 'self'; frame-ancestors 'self'`. HSTS, nosniff, XFO, Referrer-Policy and Permissions-Policy are present.
- `curl -I https://api.pricechat.live`: only `vary: Origin`, with no HSTS or nosniff.
- `/docs` and `/openapi.json` return 404 (good). Errors are generic `{"detail":"Internal server error","request_id":...}` (good).

**Impact:**
- With no `default-src`, `connect-src`, `img-src` or `form-action`, an injection could still exfiltrate data or post forms elsewhere.
- Raw `href={x.url}` links from scraped data (for example `pages/pokemon/PokemonCardPage.tsx:730`, `components/contentItems.tsx:62`) are protected from `javascript:` only by script-src having no `unsafe-inline`.

**Fix:**
- Add `default-src 'self'; connect-src 'self' <needed hosts>; img-src 'self' data: https:; form-action 'self'`.
- Add a small header middleware on the API (nosniff, HSTS, `frame-ancestors 'none'`).
- Allow only http(s) in external `href`s.

### SEC-14: Telegram Login Widget payloads can be replayed for 24 h
Severity: Low (on its own) · Status: CONFIRMED · Effort: S

**Evidence:** `members/auth.py:41,317-318`: `WIDGET_MAX_AGE = 86400`, and the hash is never recorded as used.

**Impact:** a leaked widget payload (from a log, a screenshot, a proxy) is a 24-hour login token. This becomes moot if the widget route is turned off (SEC-1).

**Fix:** a maximum age of about 300 s, plus a single-use hash table.

### SEC-15: Small authorization gaps from the IDOR sweep (no member-to-member data exposure)
Severity: Low · Status: CONFIRMED · Effort: S

**Evidence:**
- (a) `market/raw.py:34-38`: `listing_comments` (unauthenticated), `add_listing_comment` and `condition_vote` ignore the sandbox-visibility rule that `service.listing_detail` (`service.py:346-348`) applies.
- (b) `workers/service.py:539-543`: `can_review` ignores `WkAssignment.ended_at`, so ex-managers keep approve/reject rights over pending reports.
- (c) `tradingbot/service.py:679-693`: `signal_act` accepts any `signal_id`, so a member can farm the one-time `earn.first_trade` XP without a published signal.
- (d) The portfolio QR handoff claim (`api/routes/portfolio.py:254-276`) gives a full-length session to whoever opens the link within 10 minutes. It is not bound to the computer's browser.
- (e) Market photos follow the item, not the listing (`api/routes/market.py:186-189`). Photos added after an item was ever public become public.
- (f) Realm WebSocket tickets can be reused for 12 h (`members/realm_online.py:25,119-121`).

**Fix:** a one-line check for each. For (d), bind the claim to a confirm step on the computer, or issue a short session.

### SEC-16: The public market sanitiser is a deny-list
Severity: Low · Status: CONFIRMED · Effort: S

**Evidence:** `pokemon/public.py:21,53-70` removes keys in `_DROP` from responses that admin handlers built. Any new field added to those handlers is public by default.

**Impact:** licensed price data (PriceCharting / PSA) or owner-only fields could leak.

**Fix:** switch to an allow-list per path, plus a test that fails when an unknown key appears.

## Verified OK (evidence, no finding)

**Routing and guard:**
- `admin_guard` refuses odd path spellings. `backend/tests/audit_guard_paths.py`: `//api/settings` → 404, `/api//settings` → 401, `/api/members/../settings` → 401, `%2e%2e` → 404, `HEAD` → 401.
- Admin login itself: scrypt, constant-time compare, per-client and global failure limits, known-device cookie, server-side sessions stored as sha256.
- Desktop `kd.` tokens: the scope is enforced in middleware (`members/desktop_scope.py`). Session tokens are read from the cookie only (`SessionToken = Cookie(alias="pc_session")`, `api/routes/members.py:36`), so a desktop token can't bypass that scope through a header. The code exchange is PKCE-style and single-use; refresh rotates the token.

**OAuth:**
- Google and X member sign-in: PKCE S256, single-use state bound to the browser with a 10-minute time-to-live, redirect URI built only from server configuration. Google: `aud`/`iss`/`exp` checked, `openid` scope only. The X token is stored with `protect()` (`members/x_actions.py:55,75`).
- YouTube and TikTok connector OAuth: single-use in-memory state plus PKCE. The TikTok attempt is bound to the dashboard session.

**Wallet and email sign-in:**
- SIWE-style wallet sign-in: domain-bound message, 128-bit nonce, spent atomically before the signature check, 10-minute expiry, signer equals the address that requested the nonce.
- Email codes: 6 digits from `secrets`, 5 attempts per code, per-email limits of 5 per hour and 10 per day. That allows about 50 guesses per day per address.

**Webhooks, workers and bot:**
- NOWPayments: HMAC-SHA512, constant-time compare. Stripe: v1 signature with timestamp tolerance. TikTok: signature over the raw body.
- Worker and Vortex bearer tokens are compared as hashes or with constant time. Failed Vortex attempts are rate-limited.
- Telegram callbacks act on `from.id` and the caller's own intents and positions (`tradingos/bot.py:1051-1130`).

**Logs and secrets:**
- Bot-token URLs are never chained into exceptions (`telegram/client.py:221-234`). httpx logs at WARNING.
- Production logs use route templates. A sample of 2,834 lines had no emails, IPs, tokens or `HTTP Request` lines.

**Injection and XSS:**
- No `text(f"...")` or f-string SQL. The only f-string SQL is constants in `db_reclaim.py`.
- No `dangerouslySetInnerHTML` or `innerHTML` in `frontend/src`.
- Telegram HTML output escapes user and feed text (`newsdesk/brief.py:276-293`, `tradingos/bot.py` `esc()`).
- SEO link-preview HTML escapes every value, and JSON-LD escapes `<`.

**Uploads, SSRF, redirects:**
- Uploads: 15 MB cap, real format checked by PIL, 50 MP limit, re-encoded to JPEG with EXIF stripped, random keys in a private bucket, served as `image/jpeg` with `nosniff`.
- I found no SSRF from public routes. Every URL-taking route is admin-only (vault catalog and photos, members_admin, pokemon, brand manager, VPN `agent_url`).
- `/api/r/{placement}` redirects only to the URL the admin configured.

## Untested / couldn't verify (and why)

- **Whether any secret was rotated** (bot token, admin password, bucket keys, OAuth secrets, Hyperliquid member keys). Reading Railway variables or the secret store is forbidden. The inference comes from logs only.
- **Whether the Railway breach included volume or database access.** That decides whether SEC-2 is a theoretical or a practical exposure.
- **Today's backup outcome.** Only the newest 5000 log lines were readable, and successful runs log nothing I could find.
- **Production package versions.** There is no lock file and no `pip freeze` from the running container (that would need `railway ssh`, which is forbidden).
- **The account-deletion failure with market RESTRICT rows (SEC-12)** is inferred from the schema. I didn't run it, to avoid touching the database.
- **SEC-1 and SEC-4 were not exercised against production** (no logins, per the brief). They are proven from code and the live providers config.
- **The anonymous VPN key and NOWPayments replay paths** were read but not exercised.

## Improvement & feature ideas for this area (ranked)

1. **Finish the breach runbook as one tracked operation** (SEC-1/2/3/7): rotate in order, set `PROXY_SHARED_SECRET`, set the owner alert chat, confirm a successful backup. Highest risk reduction for about an hour of owner time.
2. **Telegram sign-in confirm button, and turn off the widget in deeplink mode** (SEC-1/4). Closes both account-takeover paths with small code changes.
3. **Step-up re-authentication plus notices for live trading** (SEC-5). A wallet signature or Telegram confirm on the money-moving settings, and a "new sign-in" DM. This also limits the damage from any future session theft.
4. **A lock file with hashes and Dependabot** (SEC-8/9). Makes production auditable and patches Pillow.
5. **An owner security panel:**
   - active admin and member session counts;
   - "sign out everywhere";
   - encryption-key fingerprint and last re-key date;
   - proxy-secret status;
   - last successful backup age;
   - rate-limit hits.
   This turns today's "read the logs" checks into one screen.
6. **A privacy page generated from a data inventory, plus an export endpoint** (SEC-12). Trust and GDPR-style readiness.
7. **Rate-limit consolidation.** One limiter (`security/rate_limit.py`) everywhere, instead of the separate ones in tradingbot/service.py, email_login and admin_auth. Shared caps per action, and keys never derived from the User-Agent.
8. **An allow-list sanitiser and an API header middleware** (SEC-13/16).

## Commands run (for the evidence appendix)

- Route inventory (new diagnostic): `PYTHONPATH=<pyshim> C:/AI/price-chat/backend/.venv/Scripts/python.exe backend/tests/audit_route_inventory.py > routes.tsv`
  - Output: TOTAL 778, public 300, marketpub 17, admin 461.
  - Public routes with no auth marker: 53, each reviewed. They are public by design (market and exchange reads, ranks, committee reads, sitemaps), signature- or key-authenticated (webhooks, the anonymous VPN key), or sign-in steps.
  - Full table: `C:\Users\USER\AppData\Local\Temp\claude\8bda9b0a0137d3c5\ffd3112d-5e3a-4168-8656-5c5285927885\scratchpad\routes.tsv`
- Guard path test (new diagnostic): `backend/tests/audit_guard_paths.py`. Results are under "Verified OK" above.
- `curl -s -I https://pricechat.live`, `https://api.pricechat.live`, `/docs`, `/openapi.json`, `/api/admin/overview` (401), `/api/health/public`.
- `curl -s https://api.pricechat.live/api/members/auth/providers` → telegram mode `deeplink`. `curl -s https://api.pricechat.live/api/website/chat` → field names only were printed.
- `cd C:/AI/price-chat/backend && npx @railway/cli logs --service api --lines 5000`, grepped for the PROXY_SHARED_SECRET warning, Rekey, backup, the self-check, and PII/token patterns (0 hits).
- `pip-audit` in a scratch venv: `pa/Scripts/python.exe -m pip_audit -r freeze.txt --no-deps --disable-pip`, where freeze.txt comes from the project venv's `pip freeze`.
- `cd frontend && npm audit --omit=dev --json` → 1 high (ws), 1 moderate (ethers).
- Greps: `text(f`/`execute(f`, `dangerouslySetInnerHTML|innerHTML`, `html=True` vs `esc(`, logging of token/secret/key, `foreign_keys`, `ondelete` on members FKs, `TbNonce` cleanup.
- A static IDOR sweep of all public id-taking routes, delegated to one reviewer sub-agent. Its findings are in SEC-15; everything else was verified owner-scoped.
