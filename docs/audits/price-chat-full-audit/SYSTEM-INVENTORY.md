# Price Chat: system inventory

Audit of 2026-11-08. Code was read at the audit worktree `C:\AI\pc-wt\audit` (branch `audit/full-2026-11`, equal to main **e978cc3**). Since then main has moved forward to **5616ecd**, which is the ND-1 News Desk fix (see TEST-EVIDENCE.md). This file builds on the SYSTEM MAP in `notes/architecture-admin.md` (the "AR" notes). Every other fact cites the note it came from. Environment variable **names** come from code. No values were read.

---

## 1. Products

| Product | What it is | Where it lives | State on 2026-11-08 | Source |
|---|---|---|---|---|
| Public website | Vite/React single-page app at https://pricechat.live: home, Latest, About, Giveaway, Earn, VPN, Leaderboard, Join, Learn, Passport, legal pages | `frontend/src/website/`, Vercel | Live. tsc is clean and vitest passes 460/460. Every URL serves the same 2,252-byte shell to search crawlers (UX-1) | website-ux.md |
| Admin dashboard | Owner console at `/admin/*` (AdminApp.tsx + `pages/*`) | `frontend/src/AdminApp.tsx`, `pages/` | Live. One owner identity, no roles, no 2FA (AR-10) | architecture-admin.md |
| Kiwi Passport | Member identity: sign-in through Google, X, Telegram (deeplink), wallet (SIWE) and email (off in prod). Includes the XP ledger, missions, ranks (Royal = free VPN), leaderboards, referrals/crew, streaks, packs | `backend/app/members/*`, `routes/members.py`, `tradingbot/service.py` (wallet) | Live. Providers say `telegram.mode=deeplink`, `email.available=False`, `x.write=False`. 38 visible members on the leaderboard | passport-gamification.md |
| Earn / Kiwi Trading Bot | Hyperliquid trading for members (PAPER / TESTNET / LIVE, AUTO). Also the Lunch Bot (paper) and Funding (read-only), plus the Kalshi page | `backend/app/tradingos/*`, `tradingbot/*`, `arbitrage/`, `routes/trading.py`, `frontend/src/website/terminal.tsx` | Live. LIVE is open to every member with a verified wallet (TR-9). The public track record returns `configured:false` (UX-14) | trading.md, website-ux.md |
| Telegram bot | @Cryptopricechatbot. About 30 commands (price, chart/fc, signal, scan, committee, alerts, `/vpn`, account) | `backend/app/telegram/*`, `tradingos/bot.py` | Live inside the `telegram.ingest` job, which shares the global scheduler lock (TG-2) | telegram.md |
| Telegram group + channel | Community group (@thepricechat, PP-8). News channel @Pricechatupdates | — | The last News Desk post is #145 (2026-11-06 14:30 UTC). Views per post are 45-72 | newsdesk-content.md |
| Telegram trading (branch) | `/open`, `/close`, `/closeall`, `/sl`, `/tp` from Telegram | branch `feat/tg-trading` (worktree `C:\AI\pc-wt\tg-trade`), `tradingos/tgtrade.py` | **Not merged, not deployed.** TG-7 and TG-8 were fixed on the branch in 9e8e943. TR-4, TR-5, TR-11, TR-13, TR-14 and TR-16 are still open | telegram.md, trading.md, coordinator |
| News Desk | An autonomous event desk feeding the Telegram channel (live) and X (**dry run**) | `backend/app/newsdesk/*`, `routes/newsdesk.py`, `pages/x/NewsDesk.tsx`, `TelegramLane.tsx` | Down from 2026-11-08 10:54 UTC (ND-1). Fixed in 5616ecd and deployed at 16:06 UTC. Confirmation of the next production run is pending | newsdesk-content.md, coordinator |
| Pokémon market | Public card price tracker plus owner tools: 465 market URLs, 25,000+ card URLs in the sitemap, Vault (eBay sellers), portfolios, card marketplace. The Paper Exchange is retired | `backend/app/pokemon/*`, `market/`, `exchange/`, `vault/`, `frontend/src/pages/pokemon/*` | Live. Stalls for 10-39 s every 15 minutes (AR-6). Has no link in the main site's navigation (UX-4) | architecture-admin.md, website-ux.md |
| Brand Manager / Short Market | TikTok automation. The video worker runs on the owner's DGX | `backend/app/brand_manager/`, `worker/` | Only lightly reviewed (ND Untested) | newsdesk-content.md |
| Kiwi VPN backend | Member and anonymous VPN accounts, devices, trial, billing (USDT/NOWPayments), desktop sign-in, admin | `backend/app/vpn/*`, `routes/vpn.py`, `desktop.py` | Live: `enabled:true, trial:true, payments:false` (VPN-1) | kiwi-vpn.md |
| Kiwi VPN servers | 3 Hetzner nodes, all in one Hetzner project: **hel1** (Helsinki, 2.29.149.29, the owner's personal VPS; customers on wg1), **fsn1** (Germany, no browser proxy), **ash1** (Ashburn, 178.156.252.184, public and "recommended", 1 TB included) | `vpn-node/` (agent, proxy, `install-shared.sh`) | Live. A Hetzner account block on 2026-11-05 caused a roughly one-day full outage (memory). No backups (VPN-4) | kiwi-vpn.md |
| Kiwi VPN Windows 0.1.1 | The public Windows client | `windows-client/` on main, `/downloads/KiwiVPN-Setup.exe` | Live. Unsigned (NotSigned); SHA-256 077ae0f6…0598 matches vpnConfig.ts. No kill switch, no updater (VPN-7) | kiwi-vpn.md |
| Kiwi VPN Windows 2.0 | A rewritten client (Tauri app + Rust service, WFP kill switch, Smart Connect) | branch `feat/kiwi-windows-2` (334f45d); CI in the private repo 619vortex/KIWIVPN | Not shipped. Unsigned. GitHub Actions run 42479850219 built it and passed 36/36 real-server core checks; the window tests were 7/10 (see TEST-EVIDENCE.md) | kiwi-vpn.md, coordinator |
| Kiwi VPN Chrome extension | A browser proxy extension | `vpn-extension/` | **1.0.0 live** on the Chrome Web Store. **1.0.3 is fixed and tested but not submitted.** VPN-12 refers to Smart Connect in "extension 1.0.4" | memory index, coordinator, kiwi-vpn.md |
| Kiwi VPN Android | A WireGuard app with Keystore keys and Auto-Call | branch `feat/kiwi-autocall` (d0a051c). Published **RC1** e113fc6 at `/downloads/KiwiVPN-0.1.0-rc1.apk` | RC1 is public; hash cb52f939…06c7 matches. It carries Play-sensitive permissions and has had no team test on a physical phone (VPN-13) | kiwi-vpn.md |
| Kiwi VPN iOS | A Swift client | branch `feat/kiwi-ios-cloud` (5fd451e); Codemagic | **No usable build**: it has never run on an iPhone. Memory says it compiles unsigned on Codemagic. The App Store needs an organization account. There is no in-app account deletion (VPN-13) | kiwi-vpn.md, coordinator |
| Off-platform clients | Vortex (owner's home AI on a NUC, long-polls `/api/vortex/*`), Kalshi bot (`C:\AI\kalshi-bot`, posts to `/api/kalshi/bot/*`), `fundarb/` (a library used by `arbitrage/funding.py`) | — | Out of scope except as API clients | architecture-admin.md |

---

## 2. Services and environments

| Service | Details | Source |
|---|---|---|
| **Railway `api`** (project `pricechat`, env production, region iad) | FastAPI + uvicorn, a single process, `numReplicas: 1`, builder RAILPACK, Python 3.14, SQLAlchemy 2.1. Start command: `python -m app.db_reclaim; exec uvicorn app.main:app --proxy-headers --forwarded-allow-ips=* --no-access-log`. Healthcheck `/api/health/live` (120 s); restart ON_FAILURE ×5. The same process also runs the scheduler (44 jobs), the Telegram long-poll, the watchdog and the WebSocket tapes (Hyperliquid, Bybit, OKX, PumpPortal). Live deployment 20ebb668 started 05:14 UTC, then a93c797a at 16:06 UTC (ND-1 fix) | AR SYSTEM MAP, coordinator |
| **Railway volume** `api-volume` at `/data` | 4.0 / 48.8 GB used. Holds `price_chat.db` (SQLite, **1.17 GB**, WAL, synchronous=NORMAL, busy_timeout 15 s, pool 10+30), `secrets.json` and `cache/` | AR-12, AR SYSTEM MAP |
| **Railway bucket** `portfolio-photos` (S3-compatible) | Portfolio photos, Brand Manager media, DB backups (`backups/db/`, keep 14). **No backup has ever succeeded** (AR-1) | AR-1 |
| **Vercel** project `pricechat` | Serves https://pricechat.live (SPA). `/admin/*` loads AdminApp. `vercel.json` rewrites `/api/*` to `https://api-production-b1be.up.railway.app` and adds the `x-pc-edge: $PROXY_SHARED_SECRET` header, which is **unset** (SEC-3). Social-bot user agents get OG pages; sitemaps come from the backend; security headers and a partial CSP are set (SEC-13). Deployed with `vercel --prod` | AR SYSTEM MAP, SEC-13 |
| **api.pricechat.live** | The API is also reachable directly, bypassing Vercel (SEC-3) | security.md |
| **GitHub `619vortex/KIWIVPN`** (private) | GitHub Actions for the Windows client (`ci/kiwi-windows.yml`: build, kill-switch job, real-server e2e using the `KIWI_CI_SESSION` secret, which is the owner's desktop session, VPN-14). Shared with the iOS export | kiwi-vpn.md, coordinator |
| **Codemagic** | iOS build pipeline for `feat/kiwi-ios-cloud`. No device build yet | kiwi-vpn.md, memory index |
| **Hetzner** (one project) | hel1, fsn1 and ash1 VPN nodes. Agent port 8443 (`install-shared.sh:202` opens it to anyone through ufw; the Hetzner firewall is the only narrowing layer) | VPN-4 |
| **Owner hardware** | DGX Spark (Brand Manager video worker), NUC (Vortex) | AR SYSTEM MAP |
| **CI/CD for Price Chat** | **None.** There are no `.github/workflows`. The backend is deployed with `railway up` from a laptop working tree (AR-8) | AR-8 |
| **Datastores** | One SQLite file. No Redis, no Postgres. In-process caches (market rows of about 200-400 MB per grade) | AR SYSTEM MAP |

---

## 3. Repositories, branches and worktrees

- **Main repo:** `C:\AI\price-chat` (private GitHub repo since 2026-10-22, per memory). Main was at e978cc3 during the audit and is at 5616ecd now.
- **Worktrees:** 29 (AR-16), under `C:\AI\pc-wt\*`. The audit worktree is `C:\AI\pc-wt\audit`.
- **Branches:** 39 local, of which **17 are not merged into main** (AR-16). 21 merged branches were never deleted.

Unmerged branches (from `git branch --no-merged main`, as in AR-16; notes on each come from AR-16 and VPN-11):

| Branch | Notes |
|---|---|
| feat/kiwi-payments | Adds migration `0103_vpn_payment_recovery` with `down_revision='0102_desktop_codes'`. That **conflicts** with main's 0103 → 0104 → 0105 and would create two Alembic heads, so boot would fail (AR-16, VPN-11). Holds the billing reconcile, the reminders and the VPN terms drafts (VPN-1, VPN-10) |
| feat/kiwi-monitoring | Migration 0106 (down 0105), fine today. Adds `proxy_host`/`live_load` for Smart Connect and traffic alerts (VPN-5, VPN-12) |
| fix/kiwi-stage0 | 31c6148: one failed check no longer takes a server offline (VPN-6). Undeployed |
| feat/kiwi-windows-2 | Windows 2.0 (334f45d) |
| feat/kiwi-autocall | Android (d0a051c) |
| feat/kiwi-android, feat/kiwi-android-rc, feat/kiwi-downloads, feat/kiwi-ios, feat/kiwi-unified | 17 commits behind main (AR-16) |
| feat/kiwi-ios-cloud | iOS (5fd451e) |
| feat/kiwi-infra | Hetzner infrastructure manager. `install.sh` honours `AGENT_ALLOW` (VPN-4) |
| feat/kiwi-cws-release, feat/kiwi-ext-smart, feat/kiwi-germany-proxy, docs/kiwi-upgrade-plan | Kiwi side work (from the branch list) |
| feat/tg-trading | Telegram trading (8d14a7c at review time; TG-7/TG-8 fixes in 9e8e943) |

`git branch -a | grep kiwi` lists 25 Kiwi branches in total (VPN-11).

---

## 4. Route modules

Everything is mounted under `/api`. Routes are private unless listed in `admin_auth.PUBLIC_*`. The route inventory (`backend/tests/audit_route_inventory.py`) counted **778 routes: 461 admin-only, 300 public-prefix, 17 public market reads** (security.md).

| Module | Serves |
|---|---|
| health | `/health` (owner detail), `/health/live` (Railway), `/health/public` (uptime, public) |
| admin | Owner login, logout and session (public) |
| home, overview | Admin home "what's waiting" and the overview tiles |
| agent, brand, model, knowledge, content, connectors, activity, settings (`/settings/safety`), api_keys (`/settings/api-keys`) | AI Brain chat, config and prompt; brand goals; model routes and test; knowledge sources, search and reindex; content items; connectors; activity log; safety and kill switch; provider API keys |
| telegram, telegram_account, community | Bot connection and ingestion; Telethon account mode; community topics, questions, summaries and pilot |
| opportunities, social, growth, analytics | Content opportunities; approvals, drafts, feedback, cross-signals and command center; growth pilot; growth analytics |
| x, newsdesk (`/x/newsdesk`) | X connection, monitoring, posts and opportunities; News Desk settings, posts and Telegram lane |
| youtube, website, seo | YouTube sync and OAuth; public website content (home, latest, content and chat are public); sitemaps and OG (public prefix `/api/website/seo/`) |
| members (public prefix `/api/members/`), members_admin (`/members-admin`) | Member sign-in and Passport; admin moderation, XP, ranks and badges |
| pokemon, portfolio, market, exchange, vault | Pokémon market (owner routes plus sanitized public GETs via market_public), portfolios, card marketplace, Paper Exchange (retired), Vault |
| brand_manager, short_market | Brand Manager (`/worker/` and `/media/` are public with their own auth), Short Market |
| invites, workers, rewards | Invite Bot, worker system, rank rewards plus Vortex Realm |
| tradingbot, tradingos, trading, kiwibrain, arbitrage, committee, vortex, kalshi | VIP trading-bot funnel (plus `/api/r/` referral links), Trading OS admin and public charts, trade execution (member and admin), Kiwi Brain, Earn Lunch/Funding (public read-only), AI Investment Committee, Vortex worker API (bearer), Kalshi |
| vpn, desktop, burner | Kiwi VPN member, admin, payments and webhooks; VPN desktop sign-in; Passport burner wallet |
| system | DB download, email domain (Resend), QA member, owner alert settings and test, rekey |
| webhooks | Inbound signed webhooks (public prefix) |
| jobs | List, run and enable scheduled jobs |

Dead code: `app/tradingos/edge.py` is used only by tests. `frontend/src/pages/ConnectorPage.tsx` is imported nowhere (AR SYSTEM MAP). The News Desk has dead paths: `detect_markets`, `detect_liquidations`, `plan_news`, `writer.rewrite` (ND-9).

---

## 5. Scheduled jobs

60 jobs are defined in `app/jobs.py`. **44 run in production and 16 are off**, according to the "Background jobs turned off" log line at 2026-11-08 05:14. The exact `SCHEDULER_ONLY_JOBS` value and any `JOB_*_SECONDS` overrides were not read (env values are off-limits). All 44 jobs share one global lock (AR-3).

| Job | Interval | Prod |
|---|---|---|
| telegram.ingest | 20 s (+90 s listen) | on |
| tradingos.vortex_expire | 20 s | on |
| pokemon.exchange.auto | 15 s | on (retired feature, AR-18) |
| committee.llm_expire | 30 s | on |
| tradingos.kitchen | 45 s | on |
| tradingos.riskteam, tradingos.alerts, tradingos.track, brand.tick, tradingos.lunch_bot, tradingos.autodelete, pokemon.exchange | 60 s | on |
| tradingos.structure, pokemon.market_expire | 120 s | on |
| newsdesk.telegram, invites.poll | 180 s | on |
| tradingos.shadow, tradingos.brief, newsdesk.tick, vpn.sync, committee.paper | 300 s | on |
| members.verify, system.alerts, pokemon.warm | 600 s | on |
| tradingos.scan, tradingos.oi, tradingos.ideas, x.own, youtube.sync, committee.outcomes, pokemon.pulse | 900 s | on |
| brand.trends | 1800 s | on |
| tradingbot.affiliate, pokemon.catalog | 3600 s | on |
| vault.sync | 3 h | on |
| growth.snapshots, pokemon.top100, pokemon.portfolios, pokemon.sync | 6 h | on |
| newsdesk.metrics, **system.backup (failing, AR-1)**, system.retention, pokemon.snapshot | 24 h | on |
| pokemon.sales | 7 d | on |
| workers.verify, tradingos.funding_alerts (pinned off by SCHEDULER_DISABLED_JOBS), community.process, community.topics, community.summary.recent, community.summary.daily, community.memories, content.opportunities, knowledge.reindex, combot.sync, x.ingest, x.opportunities, cross.signals, topic.merges, opportunities.rank, approvals.expire | various | **OFF** |

The Admin "Background jobs" page shows the 16 server-disabled jobs as enabled (AR-5).

---

## 6. External APIs

Taken from the hostnames in `app/` (AR SYSTEM MAP):

- **Messaging and social:** Telegram Bot API, MTProto (Telethon), X API (api.x.com, OAuth2), YouTube Data API, Google OAuth, TikTok.
- **AI:** Anthropic, OpenAI (committee/optional), Ollama (local default; possibly unreachable in prod, AR-15).
- **Crypto market data:** Hyperliquid (mainnet, testnet, WebSocket), Binance (blocked from iad), OKX, Bybit (WS), Deribit, CoinGecko, GeckoTerminal, DexScreener, alternative.me, mempool.space, Solana RPC, pump.fun / PumpPortal.
- **Traditional markets and FX:** Yahoo Finance, Frankfurter + ECB.
- **Pokémon data:** Scrydex, TCGdex, PokémonTCG.io, PriceCharting, PSA, GemRate, eBay (**failing: invalid_client** in the 2026-11-08 log), Combot.
- **Prediction markets:** Kalshi.
- **Payments:** Stripe, NOWPayments, Etherscan / Tronscan / Sepolia RPC.
- **Email and storage:** Resend, the S3-compatible bucket.
- **RSS:** CoinDesk, Cointelegraph, Decrypt, The Block, CNBC, MarketWatch / Dow Jones (the `mw_marketpulse` feed is dead, ND-4), FT, BBC, NPR, Reuters, Al Jazeera, SEC, Federal Reserve, White House.
- **VPN nodes:** agent API on port 8443 (certificate-pinned).

---

## 7. Environment variable names

Names only, from settings fields and `os.environ`. No values were read (AR SYSTEM MAP).

- **Core:** ADMIN_USERNAME, ADMIN_PASSWORD_HASH, ADMIN_SESSION_HOURS, APP_ENCRYPTION_KEY, APP_ENCRYPTION_KEY_PREVIOUS, APP_NAME, ENVIRONMENT, LOG_LEVEL, TIMEZONE, DATABASE_URL, SECRETS_FILE, SEED_FILE, CORS_ORIGINS, DASHBOARD_URL, MEMBERS_PUBLIC_URL, PROXY_SHARED_SECRET, TRUSTED_PROXY_HOPS, BUILD_ID, RAILWAY_GIT_COMMIT_SHA
- **Scheduler:** SCHEDULER_ENABLED, SCHEDULER_ONLY_JOBS, SCHEDULER_DISABLED_JOBS, JOB_{APPROVALS_EXPIRE, COMBOT_SYNC, COMMUNITY_PROCESS, COMMUNITY_TOPICS, CROSS_SIGNALS, KNOWLEDGE_REINDEX, MEMBERS_VERIFY, MEMORY_EXTRACTION, NEWSDESK, NEWSDESK_TELEGRAM, OPPORTUNITIES, OPPORTUNITY_RANK, POKEMON_CATALOG, POKEMON_PULSE, POKEMON_SNAPSHOT, POKEMON_SYNC, SUMMARY_DAILY, SUMMARY_RECENT, TELEGRAM_INGEST, TOPIC_MERGE, X_INGEST, X_OPPORTUNITIES, X_OWN, YOUTUBE_SYNC}_SECONDS
- **AI:** LLM_PROVIDER, LLM_MODEL, LLM_TEMPERATURE, LLM_TIMEOUT_SECONDS, LLM_STRONG_TIMEOUT_SECONDS, MODEL_TIER_FAST, MODEL_TIER_STRONG, OLLAMA_BASE_URL, OLLAMA_KEEP_ALIVE, EMBEDDING_PROVIDER, EMBEDDING_MODEL, ANTHROPIC_API_KEY, OPENAI_API_KEY, CONDITION_MODEL, NEWSDESK_MODEL, CONVERSATION_WINDOW, MEMORY_PROMPT_CHAR_BUDGET, MEMORY_RECALL_LIMIT, RETRIEVAL_MIN_SIMILARITY, COMMUNITY_* (6), COMMITTEE_BACKEND, COMMITTEE_CALL_TIMEOUT_SECONDS, COMMITTEE_COSTS, COMMITTEE_DAILY_CALLS, COMMITTEE_DAILY_LIMIT, COMMITTEE_DAILY_LIMIT_RANKED, COMMITTEE_DAILY_USD, COMMITTEE_MAX_CONCURRENT, COMMITTEE_MODEL_{FAST,DEEP,SYNTH}, COMMITTEE_NEWS_FEEDS
- **Telegram:** TELEGRAM_BOT_TOKEN, TELEGRAM_API_BASE, TELEGRAM_LISTEN_SECONDS, TELEGRAM_LONG_POLL_SECONDS, MEMBERS_TELEGRAM_DEEPLINK
- **X:** X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET, X_BEARER_TOKEN, X_ACCOUNT_USERNAME, X_API_BASE, X_DAILY_BUDGET_USD, X_MONTHLY_BUDGET_USD, X_WRITE_COST_ESTIMATE_USD, X_MEMBER_CLIENT_ID, X_MEMBER_CLIENT_SECRET, X_MEMBER_WRITE, X_OAUTH2_{AUTHORIZE,TOKEN,REVOKE}_URL, MEMBER_X_REDIRECT_URI
- **Google, YouTube, email, TikTok:** YOUTUBE_API_BASE, YOUTUBE_API_KEY, YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REDIRECT_URI, YOUTUBE_REFRESH_TOKEN, GOOGLE_AUTH_URL, GOOGLE_TOKEN_URL, GOOGLE_MEMBER_CLIENT_ID, GOOGLE_MEMBER_CLIENT_SECRET, MEMBER_GOOGLE_REDIRECT_URI, MEMBER_SESSION_DAYS, EMAIL_CODE_MINUTES, EMAIL_FROM, RESEND_API_KEY, TIKTOK_API_BASE, TIKTOK_AUTH_URL, TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_SCOPES
- **Brand Manager and storage:** BM_LEASE_SECONDS, BM_MAX_VIDEO_MB, BM_MEDIA_BASE_URL, BM_TICK_SECONDS, BM_WORKER_BOOTSTRAP_SHA256, PORTFOLIO_BUCKET, PORTFOLIO_S3_ENDPOINT, PORTFOLIO_S3_KEY_ID, PORTFOLIO_S3_REGION, PORTFOLIO_S3_SECRET, PORTFOLIO_UPLOAD_DIR, PORTFOLIO_CONDITIONS_PER_DAY, PORTFOLIO_CONDITIONS_TOTAL_PER_DAY, PORTFOLIO_SCANS_PER_DAY, PORTFOLIO_SCANS_TOTAL_PER_DAY
- **Pokémon data:** SCRYDEX_API_BASE, SCRYDEX_API_KEY, SCRYDEX_TEAM_ID, SCRYDEX_CACHE_DIR, SCRYDEX_CACHE_HOURS, SCRYDEX_MAX_REQUESTS_PER_SYNC, TCGDEX_API_BASE, TCGDEX_CACHE_DIR, TCGDEX_CACHE_HOURS, POKEMONTCG_API_BASE, POKEMONTCG_API_KEY, PRICECHARTING_API_BASE, PRICECHARTING_API_KEY, PSA_API_BASE, PSA_API_TOKEN, GEMRATE_API_BASE, GEMRATE_API_KEY, EBAY_API_BASE, EBAY_CLIENT_ID, EBAY_CLIENT_SECRET, EBAY_DEV_ID, EBAY_MARKETPLACE, EBAY_CARDS_PER_SYNC, FX_API_BASE, COINGECKO_API_KEY, POKEMON_CATALOG_REQUESTS_PER_RUN, POKEMON_CATALOG_SETS_PER_RUN, POKEMON_TOP100_BACKFILL_REQUESTS, POKEMON_TOP100_MAX_AGE_DAYS, POKEMON_TOP100_MIN_HISTORY, VAULT_EBAY_DAILY_CALLS, VAULT_EBAY_DETAILS_PER_SYNC, VAULT_EBAY_MAX_PAGES, VAULT_EBAY_SELLERS, VAULT_INCLUDE_GRADED, VAULT_MAX_AGE_HOURS, VAULT_REQUESTS_ENABLED, VAULT_SYNC_SECONDS
- **Marketplace and trading:** MARKET_BLOCKED_COUNTRIES, MARKET_BUYER_FEE_BPS, MARKET_SELLER_FEE_BPS, MARKET_HIGH_VALUE_CENTS, MARKET_KYC_PROVIDER, MARKET_LIVE_APPROVED, MARKET_MODE, MARKET_OFFER_HOURS, MARKET_ORDER_MINUTES, MARKET_PAYMENT_PARTNER, MARKET_TESTNET_RECEIVE, MARKET_TESTNET_RPC, MARKET_VAULT_PARTNERS, EXCHANGE_ENABLED, LUNCH_BOT_ENABLED, PUMPPORTAL_API_KEY, SOLANA_RPC_URL, KALSHI_FEE_COEFFICIENT
- **Other integrations and payments:** VORTEX_ASSIST_TOKEN, REALM_WS_URL, COMBOT_API_BASE, COMBOT_API_KEY, NOWPAYMENTS_API_KEY, NOWPAYMENTS_IPN_SECRET, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET
- **VPN and website:** VPN_EXTENSION_ORIGINS, VPN_REVIEWER_EMAIL, VPN_REVIEWER_PASSWORD_HASH, VPN_REVIEWER_UNTIL, WEBSITE_CHAT_ENABLED
- **Vercel:** PROXY_SHARED_SECRET
- **Kiwi Windows CI (KIWIVPN repo):** secret `KIWI_CI_SESSION` (VPN-14)

What the logs show about these variables (no values were read):
- `PROXY_SHARED_SECRET` is unset in production (SEC-3, AR-13).
- `APP_ENCRYPTION_KEY_PREVIOUS` is not set at the latest startup (SEC-2).
- `BUILD_ID` and `RAILWAY_GIT_COMMIT_SHA` are unset, so health reports `version: null` (AR-8).

---

## 8. Deployment configuration

| Item | Config | Risk |
|---|---|---|
| `backend/railway.json` | RAILPACK, the start command, healthcheck `/api/health/live` 120 s, 1 replica, ON_FAILURE ×5 | Config-as-Code stops being honoured on **2026-12-01** (AR-9) |
| `backend/railpack.json` | Start command only | — |
| `frontend/vercel.json` | `/api/*` rewrite with `x-pc-edge`, social-bot OG rewrites (no search crawlers, UX-1), sitemaps, security headers, CSP | CSP is partial (SEC-13) |
| `backend/requirements.txt` | Every package uses a `>=` floor. No lock file. pytest ships to production | AR-8, SEC-9 |
| Migrations | 105 Alembic versions. `command.upgrade("head")` runs at every boot. 0054, 0066 and 0072 have empty downgrades | AR-7 |
| Dockerfile | None for the API (`worker/` has one for the DGX) | — |
| Backend deploy | `cd backend; npx @railway/cli up --service api --ci` from a laptop. 20 deployments between 2026-11-03 and 11-08, 3 FAILED. On 2026-11-03 the frontend was deployed onto the api service by mistake (outage) | AR-8 |
| Frontend deploy | `vercel --prod`. The VPN binaries are tracked in git and redeployed with every build (`dist` is 105 MB) | UX-20 |
| Windows CI | `ci/kiwi-windows.yml` in 619vortex/KIWIVPN. Output is `…-unsigned-Setup.exe` | VPN-7, VPN-14 |
