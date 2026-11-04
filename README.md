# Price Chat

Local-first AI brand-management platform.

- **Phase 1:** foundation. FastAPI backend, SQLite, provider abstraction, safety model, dashboard.
- **Phase 2:** a working local brand brain. A real local model through Ollama, an editable brand profile and goals, a compiled system prompt, persistent conversation memory, a knowledge base with meaning-based (vector) recall, and an activity log written for non-developers.
- **Phase 3A:** Telegram community intelligence. Read-only ingestion from allow-listed chats, topic and trend detection, recurring questions, AI summaries, reviewed community memory, content opportunities, community search, task-based model routing and background jobs.

- **Phase 3B/4A (current):** real community pilot and X intelligence. Dashboard setup for Telegram and X with encrypted secret storage, pilot statistics and message tracing, human feedback that shapes ranking, topic merge/split, transparent opportunity ranking, a draft generator with variants, read-only X monitoring (mentions, accounts, topics, own posts, metrics), X opportunities, Telegram ↔ X signals, relationship memory, a command center, and a safety guard with an approval queue.

Price Chat never sends anything to Telegram. The News Desk posts news and market moves on its own once you switch it live (X › News Desk). Everything else on X only acts after you approve each action (see **X operator**); automatic mode is locked.

## Requirements (Windows)

| Tool | Version | Check |
|---|---|---|
| Python | 3.12+ (tested on 3.14) | `python --version` |
| Node.js | 20+ (tested on 24) | `node --version` |
| Ollama | 0.35+ | `ollama --version` |

### Install Ollama and the models

Install from the official source: either https://ollama.com/download (run `OllamaSetup.exe`) or

```powershell
winget install --id Ollama.Ollama -e
```

Then pull the models. If `ollama` isn't on PATH yet, open a new terminal or use `"$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"`.

```powershell
ollama pull llama3.1:8b        # default chat model (~4.9 GB)
ollama pull nomic-embed-text   # memory search embeddings (~274 MB)
ollama pull gemma3:27b         # optional: higher-quality writing (~17 GB, fits a 32 GB GPU)
```

Ollama runs in the background (tray icon) and listens on `127.0.0.1:11434` only.

## Setup (once)

From `C:\AI\price-chat`:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

This creates `backend\.venv`, installs Python and npm dependencies, and copies `.env.example` to `.env` if needed. All configuration and secrets live in `.env`; nothing is hard-coded.

## Run

Use two terminals.

**Backend** (http://127.0.0.1:8000, API docs at http://127.0.0.1:8000/docs):

```powershell
cd C:\AI\price-chat\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

**Frontend / dashboard** (http://127.0.0.1:5173):

```powershell
cd C:\AI\price-chat\frontend
npm run dev
```

Shortcuts: `scripts\run-backend.ps1` and `scripts\run-frontend.ps1`.

On startup the backend applies any pending database migrations to `data\price_chat.db` and creates defaults from `backend\seeds\price_chat.json` if they're missing. Existing data is never overwritten.

## Telegram setup (community intelligence)

Price Chat reads Telegram in one of two official ways, chosen on **Community › Connection**. It never sends messages into Telegram and only reads chats you authorise (an explicit allow-list).

### Option A: Bot (simplest)

1. In Telegram, open **@BotFather**, send `/newbot` and follow the prompts. Copy the token it gives you.
2. Still in @BotFather, send `/setprivacy`, choose your bot and select **Disable**. Otherwise a bot only sees commands and replies to itself.
3. In the dashboard open **Community › Connection**, paste the token and click **Save & test**. Price Chat checks it with Telegram, then stores it encrypted on this computer (Windows DPAPI, `data\secrets.dpapi`). It is never shown again, sent to the browser, logged or given to the AI. No restart is needed.
4. Add the bot to your community group and send any message there.
5. Click **Refresh chats**. The group appears under "Chats the bot can see". Click **Authorise**. Only authorised chats are ever read.
6. Ingestion starts automatically. Use **Pause ingestion / Start ingestion** at any time. The **Pilot** tab shows messages received, processed and ignored, every pipeline step with **Run now**, and on **Messages** each message has a **Trace** link showing which topic, question, summary, memory and opportunity it fed.

Notes:
- Adding the bot to a group does nothing by itself. Messages from chats you have not authorised are discarded on arrival; only the chat's name and id are shown.
- A bot only receives messages sent **after** it joins (and after you authorise the chat). It cannot read older history.
- Telegram allows one reader per bot token. Don't run two Price Chat backends (or other tools) with the same token.
- To remove a community and all its data: **Community › Connection › Remove & delete data**. To remove the token: **Remove token**.
- Prefer files? `TELEGRAM_BOT_TOKEN` in `.env` still works; a token saved on the dashboard takes priority.

### Option B: Telegram account (official client API)

Use this when you cannot add a bot, or want to import recent history. It logs in with a Telegram account that is already a member of the Price Chat groups, using Telegram's official client API (MTProto, via Telethon).

1. Go to **https://my.telegram.org**, log in with your phone number and open **API development tools**. Create an app (any name) and copy the **API ID** and **API Hash**.
2. In the dashboard open **Community › Connection**, choose **Telegram account**, paste the API ID and API Hash and save. The API Hash is stored encrypted (Windows DPAPI) and never shown again.
3. Enter your phone number (with country code) and click **Send code**. Telegram sends a login code to your Telegram app.
4. Type the code. If your account has two-step verification, enter your password. The code and password go straight to Telegram; they are never stored or logged.
5. Click **Show my groups and channels**. Only groups and channels are listed (never private chats). Click **Authorize for Price Chat** on the communities Price Chat should read. Nothing else is ever read.
6. Optionally import recent history for an authorised chat: **24 hours**, **7 days** or **30 days** (an estimate is shown first; at most 5,000 messages). By default reading starts from the moment you authorise.
7. **Log out / revoke session** ends the Telegram session (it also disappears from Telegram › Settings › Devices) and deletes it from this computer.

The session is stored encrypted on this computer only. Treat it like a password: anyone with it could read the account.

## X / Twitter setup (read-only monitoring)

Price Chat uses only the **official X API v2**. It never scrapes X and, in this phase, **never posts, replies, likes, reposts or follows**. The backend refuses every write even if asked.

1. Go to **https://console.x.com** and sign in with the X account Price Chat should use. Create an app (any name). X API access is pay-per-use: add credit in the console.
2. In the app, open **Keys and tokens** and copy the **Bearer Token**.
3. In the dashboard open **X / Twitter › Connection**. Paste the Bearer Token and your X username, then click **Save & test**.
4. Optional, for your own posts and private metrics (impressions, link clicks): in **Keys and tokens** also copy the **API Key and Secret** and generate the **Access Token and Secret**, open "Account keys" on the Connection page and paste all four.
5. The Connection page shows what your access allows (account lookup, mentions, search, private metrics). Anything X refuses is shown as "Not available" and simply skipped.
6. Add **Monitored topics** (keywords or X search queries) and **Monitored accounts**. Price Chat reads every 15 minutes, only what is new since the last read.

Cost control: X charges about $0.005 per post read and $0.01 per account lookup. Price Chat reads at most 10 posts per topic per check, tracks the estimated spend, and stops at `X_DAILY_BUDGET_USD` (default $1/day) and `X_MONTHLY_BUDGET_USD` (default $20/month). **Pause reading** on the Connection page stops all X requests. If X rate-limits or caps usage, Price Chat waits until the reset time.

Keys are stored encrypted on this computer (Windows DPAPI) and are never shown again, logged, or sent to any AI model. `X_BEARER_TOKEN` etc. in `.env` also work.

## Real data pilot

The **Command center** (home page) starts with **Price Chat pilot status**: AI, Telegram, X, Memory, Opportunities and Write actions. A row shows READY only after the underlying capability has really been tested: the model service answered and the models are installed, the Telegram bot was verified and an ingestion run succeeded, X answered a public read, or opportunities were actually produced from real data. Otherwise it shows NEEDS SETUP, WAITING FOR DATA or PROBLEM, with the next step.

- **Data is shown immediately; AI runs in the background.** Pages never wait for a model. Strong-model analysis (gemma3:27b) may take a minute or more, especially while the model loads; it has its own time limit (`LLM_STRONG_TIMEOUT_SECONDS`, default 300). Activity shows "AI analysis started" and "completed in N s", and the X Overview shows whether analysis is running, done, timed out or unavailable.
- **X monitoring:** add topics on **X / Twitter › Monitored topics** (one click for Bitcoin, Ethereum, Gold, Real estate, Collectibles, Cars, Markets, or your own X search query) and accounts on **Monitored accounts**. Each shows priority, posts found, last checked and last successful check. Only new posts are read (no re-fetching).
- **X cost:** the X Overview shows today's estimated spend, requests, the daily limit and the month's estimated spend. These are Price Chat's local estimates from X's published prices; X does not report exact charges through the API, so the X console is the authority. Each **Test connection** reads a few items to check every capability (roughly $0.10–0.20 estimated), so don't press it repeatedly.
- **Telegram:** **Community › Pilot** shows connection, authorised chats, last message, last processing run, Start/Pause ingestion and Process now.
- **Community discussion is not fact:** topics, summaries and community memories describe what members are discussing. Community memories wait for your approval, and the agent treats approved ones as community observations, never as brand facts.

## X operator (supervised)

Price Chat can operate the Price Chat X account, but **you are the final approval gate**: every post, reply, like, repost and follow is proposed, waits in **Approvals**, and is only executed after you approve it. Automatic mode exists in the design but is locked.

Flow: read → understand → find opportunity → **propose** → **you approve** (or edit & approve, or reject) → the safety guard re-checks everything → executed through the official X API → result recorded → Price Chat learns from your decisions.

- **What X needs:** the Access Token and Access Token Secret of an app whose permissions are **Read and write**. Tokens generated before switching to Read and write stay read-only: regenerate them. Paste them on **X / Twitter › Connection** and click **Save & test**. The Connection page shows Public read, User account and Write capability separately; write capability comes from the access level X reports (nothing is written to find out).
- **Dry run (on by default):** approved actions run the whole pipeline, including every safety check, and show exactly what WOULD be sent, but nothing reaches X. Turn it off with **Go live** on **X / Twitter › Overview** (operator panel) or **Approvals › Permissions & safety**.
- **Social actions ACTIVE / PAUSED:** pausing blocks every new write immediately. Reading, analysis and proposals continue.
- **Safety ceilings (not targets), per day / hour:** posts 3 / 1, replies 10 / 3, likes 20 / 5, reposts 5 / 2, follows 5 / 2. Plus: no repeating the same text, no liking/reposting/following the same thing twice, a per-account cooldown (30 min) and at most one reply per account per hour. Adjust on **Approvals › Permissions & safety**.
- **Proposals:** on **X / Twitter › Opportunities** use **Draft reply with AI**, **Draft post**, **Like**, **Repost** or **Follow** (✦ marks the AI's suggestions). Replies are written with the post, its context, the author relationship, the monitored topic, approved knowledge, matching Telegram discussion, previous replies and the X voice from **AI Brain › Brand profile**; generic praise is rejected. Content drafts can be sent to the queue from **Content › AI drafts**. **Prepare test post** queues one harmless post for a first test.
- **Execution:** an action is marked **Executed** only when X confirms it (with a link to the post). Errors are shown as **Failed** with the reason; a timed-out write is flagged as uncertain so you can check X before retrying. Performance (likes, replies, reposts, impressions when available) is attached to executed posts after later reads; metrics are cached for 6 hours. Missing numbers stay "n/a".
- **Learning:** approvals, rejections and especially your edits (shortened, removed hashtags, removed the opening, ...) become plain-English signals shown on the operator panel and used as instructions for the next drafts. No model is fine-tuned.
- **Cost:** X write requests are counted separately; their cost is a local estimate (`X_WRITE_COST_ESTIMATE_USD`, default $0.01) and counts toward the same daily/monthly budget.

## YouTube → website (read-only)

Upload a Short to YouTube as usual; Price Chat finds it on the next sync (every 15 minutes, or **Sync now**) and the website's **Latest Content** shows it automatically. Nothing is ever uploaded or changed on YouTube.

**Connect (API key, recommended):**
1. Go to **https://console.cloud.google.com**, sign in, create a project (any name).
2. **APIs & Services › Library** → search **YouTube Data API v3** → **Enable**.
3. **APIs & Services › Credentials › Create credentials › API key**. Edit it: under *API restrictions* choose **Restrict key** → **YouTube Data API v3** → Save. Copy the key.
4. In the dashboard open **YouTube**, paste the key, enter the channel as **@yourhandle** (or the channel URL) and click **Connect**. The key is stored encrypted (Windows DPAPI) and never shown again.
5. Click **Sync now**. Your videos appear under **Recent uploads**, in **Content › Published (YouTube)**, and on **Website › Open preview**.

**Optional: sign in with Google** (also shows private/unlisted status of your own videos): in Google Cloud create an **OAuth client ID** (type *Web application*), add the redirect URI `http://127.0.0.1:8000/api/youtube/oauth/callback`, configure the consent screen (External, add yourself as a test user), then paste the client ID and secret on the YouTube page and click **Sign in with Google**. Scope: `youtube.readonly`. If the account has several channels you choose the Price Chat one explicitly.

**How it works:** the backend reads the channel's uploads playlist (only new videos after the first sync), stores one record per video id (re-syncs update it, never duplicate it), detects Shorts, records view/like/comment snapshots, and serves the website from its own database (`/api/website/latest`). If YouTube is unavailable the website keeps showing the last synced content; problems appear only on the dashboard. Quota: about 300 of the 10,000 free daily units at the default interval (`JOB_YOUTUBE_SYNC_SECONDS`).

**Short detection:** the API has no "is Short" flag. A video counts as a Short when it is at most 3 minutes long **and** vertical or square (aspect ratio from the API's player size). If the aspect ratio is missing: up to 60 s counts as a Short; 61–180 s only with `#shorts`. The word "Shorts" in a title alone never decides it. Override per video in **Content › Published** (Short / Long form / Auto).

**Website controls:** public videos are shown automatically, newest first (homepage: latest 3; **View all**: `/website/content`). **Hide from website** / **Show on website** is remembered across syncs; **Feature** is stored for later priority placement. Private, unlisted or deleted videos are removed from the website automatically and kept internally.

## Analytics › Growth

**Analytics** shows whether the social activity is producing growth, from collected data only (24H / 7D / 30D / ALL):
- **KPIs:** X followers and change, impressions, engagements and engagement rate (on your posts published in the period), posts, replies, likes/reposts/follows executed by Price Chat, profile visits (only where X provides private metrics), X API spend. A comparison with the previous period appears only when data was collected for it; otherwise "no comparison".
- **Growth chart:** follower count (from snapshots, never back-filled) with optional impressions, engagements, posts, replies, likes and reposts; Price Chat actions are marked along the bottom. Shown side by side, not as cause and effect.
- **Actions, content performance** (with AI-drafted / edited by you and ~1h/6h/24h checkpoints), **AI performance** (acceptance, edit rate, recommended vs approved, weekly trust trend), **relationships**, **Telegram** and **cost** (today / 7 / 30 days, per useful opportunity and per approved action). Details stay on **Activity**.
- **Snapshots:** our follower count is recorded from X responses Price Chat already receives; if none arrived for 6 hours, the X reading job makes one account lookup (~$0.01). Telegram member and message counts every 6 hours (free). The dashboard itself never calls X.

## X likes pilot

Price Chat can like relevant X posts, one human-approved like at a time.

- **Where:** **X / Twitter › Growth Pilot › Likes: AI-recommended** shows the strongest few posts the AI flagged as worth a like, with the reason, today's likes used and the limits. **Review like** puts it in the queue; **Approve like** or **Reject**. Likes also appear in **Approvals** (no text to edit). The opportunity feed can be filtered to *Suggested action = Like*.
- **Not recommended:** posts older than 3 days, questions/mentions that deserve a reply, engagement farming (giveaways, "like & RT"), off-brand topics (brand profile), posts already liked, duplicate content, and accounts liked in the last 12 hours or 3+ times this week. Only one post per account is listed. Zero candidates is normal.
- **Limits (ceilings, not targets):** 20 likes/day, 5/hour, one like per account per 12 hours, plus the general 30-minute account cooldown; the same post is never liked twice. Adjust day/hour on **Approvals › Permissions & safety**.
- **Execution:** official endpoint `POST /2/users/{your id}/likes` with OAuth 1.0a user context (Access Token + Secret of an app with **Read and write** permission). Marked **LIKED** only when X answers `liked: true`. Dry run shows **WOULD LIKE POST** and sends nothing. Errors are explained: 401 credentials, 402 billing/credit, 403 permission, 404 post deleted (that post is then blocked), 429 rate limit, 5xx X service.
- **Memory:** each like updates the author's relationship (like count, last liked post and time, topics) and the activity log ("Liked @account's post about …").

## Social growth pilot (X + Telegram)

**X / Twitter › Growth Pilot** brings the supervised pilot together:

- **Status:** X read, X user auth, Telegram, community data, AI, opportunities, approvals, X execution (dry run / supervised), automatic mode (always LOCKED), plus today's numbers.
- **Start review session:** gathers the 5–10 strongest opportunities from X and Telegram that pass the quality filter and shows them one at a time with a proposed post or reply (high-priority items get Direct / Conversational / Insight-driven variants). **Approve**, edit then approve, **Reject** or **Skip**. Every write still passes the safety guard; with dry run on, nothing reaches X.
- **Opportunity feed:** labelled X, Telegram or Telegram + X, with the ranking signals and the reason any item was filtered out. Zero actions is an acceptable result.
- **Early community signal:** a topic discussed on Telegram at least an hour before monitored X accounts mention it. A content-timing signal only, never a market prediction.
- **What Price Chat is learning:** plain rules from your decisions and edits; Accept, Disable or Reset each one.
- **Analytics, daily internal summary, compute & API use, creator discovery** (add to watchlist or ignore; Price Chat never follows on its own).
- Telegram discussion is used in aggregate only. Members are never named or quoted in X drafts.

## Approvals and permissions

Every social action (post, reply, like, repost, follow) has a permission: **Disabled** (default) or **Needs my approval**. "Automatic" exists in the design but cannot be selected in this phase. Proposed actions (from X opportunities or drafts) go to **Approvals**, where each shows the full safety check: kill switch, outbound switch, platform, permission, rate limits, duplicates, per-account cooldown, blocked accounts and watchlist-only. Approving an item records your decision and runs the check as a **simulation**: nothing is sent to X. **Approvals › Test the safety check** explains exactly which rule would allow or block an action.

### Try it without Telegram or X accounts

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-demo.ps1
```

This starts a local mock Telegram (`scripts\mock_telegram.py`) with a scripted community, a local mock X API (`scripts\mock_x.py`, read endpoints only, no cost), a demo backend on port 8001 using a **separate** database (`data\demo.db`) and a **separate** secret store (`data\demo-secrets.dpapi`), and a demo dashboard at http://127.0.0.1:5174.

1. **Community › Connection**: authorise "Price Chat Community (demo)". The scripted history arrived before authorisation and was discarded, so replay it:
   ```powershell
   Invoke-RestMethod -Method Post http://127.0.0.1:8081/mock/replay
   ```
2. **X / Twitter › Connection**: click **Test connection** (the demo Bearer Token is preset). Then add the monitored topic `funding rates` and the accounts `bigtrader` and `chartlearner`, and click **Read now** on the Overview tab.

Within a few minutes the background jobs produce topics, questions, summaries, memories, ranked opportunities, X opportunities and Telegram ↔ X signals. Your real data and keys are never touched.

## Using the brain

1. **Settings → Model status**: check the model shows **Online**, click **Test Model**, and choose the model and temperature.
2. **AI Brain → Brand profile**: fill in description, mission, products, terminology, allowed claims and social objectives. Leave a field empty rather than guessing.
3. **AI Brain → Goals**: edit, prioritise (P1 is the highest) and enable or disable goals.
4. **Knowledge**: add brand facts, notes and community insights. Use **Test recall** to check that the agent finds them.
5. **AI Brain → Test chat**: talk to Price Chat or draft X and Telegram content. Each reply shows the model, latency, tokens and the memories used.
6. **AI Brain → Compiled prompt**: see exactly what the model receives.
7. **Community** (Telegram): topics and trends, recurring questions, AI summaries, search with an evidence-based answer, raw messages, and chat setup.
8. **Knowledge › Community brain**: review what Price Chat learned from the community. Approve, edit, reject, delete, or change importance. The agent only uses approved memories.
9. **Content › Opportunities**: review AI-detected content ideas, ranked by separate signals (click **Why this rank?**). Approve, reject or mark important; Price Chat remembers your decisions and ranks similar ideas accordingly (see the learned weights with `GET /api/feedback/preferences`). Approve an idea, then **Generate draft** or **Generate 3 variants** for an X post or thread, Telegram post, YouTube idea/titles/outline, short-form idea or hook, or TikTok idea. Each draft shows why it exists and which memories and community context it used. Nothing is published.
10. **Community › Merge suggestions**: Price Chat suggests topics that look like duplicates. Nothing is merged unless you click **Merge**; **Keep separate** stops the suggestion. A topic page has **Split this topic** to move messages into a new topic.
11. **X / Twitter**: Overview, Mentions, Monitored accounts (with relationship memory and your notes), Monitored topics, Opportunities, Our posts, Analytics (metrics X does not provide are shown as "n/a", never estimated) and Connection.
12. **Command center** (home page): AI status, communities, X, trending topics, Telegram ↔ X signals, top opportunities, drafts and approvals waiting for you.
13. **Approvals**: the approval queue, per-action permissions and safety rules.
14. **Settings**: model status, **model routing** (which model handles which task) and **background jobs** (status, last and next run, last error, Run now).
15. **Activity**: see everything that happened, in plain English.

## Test

```powershell
cd C:\AI\price-chat\backend
.\.venv\Scripts\python.exe -m pytest -q

cd C:\AI\price-chat\frontend
npm run build     # type-check + production build
```

Or run `scripts\test.ps1`. Backend tests use temporary databases and fake model providers, so they don't need Ollama.

The full backend suite (~1300 tests) takes about 4 minutes. Migrations run once per session into a template SQLite
file, and every test starts from an exact copy of it (`reset_database` in `backend/tests/conftest.py`). Don't move
migrations or `create_all` back into a per-test fixture: that made the suite take ~30 minutes.

## Project layout

```text
price-chat/
  backend/
    app/
      main.py           app factory: migrations, seed, providers, error mapping
      api/              HTTP routes + Pydantic schemas
      agents/           PriceChatAgent and prompting/ (system-prompt builder)
      memory/           conversation log, memory store, embeddings index, retrieval/
      knowledge/        KnowledgeService + ingestion/ (future X/docs/URLs)
      telegram/         Bot API client (read-only), parser, ingestion, allow-list & deletion
      community/        processing, topics, questions, summaries, memory extraction,
                        opportunities, ranking, merge/split, search, agent context
      content/          draft generator (9 draft types, 3 variants)
      learning/         human feedback + interpretable preference learning
      social/           platform-independent social layer: X reading, budget, store,
                        X opportunities, cross-signals, SocialActionGuard, approvals
      security/         encrypted secret store (Windows DPAPI), credential resolution
      ai/               task → model routing, structured (JSON) model output,
                        AI gate (chat has priority over background work)
      scheduler/        lightweight in-process job scheduler
      jobs.py           background job definitions
      providers/        LLMProvider + EmbeddingProvider interfaces, Ollama, factory
      connectors/       SocialConnector interface; x/ = official X API v2 client (read-only)
      services/         activity log, brand/goals, model status, safety, runtime
      models/           SQLAlchemy tables
      db/               engine/session, migrations runner, seed
      config/           settings (.env), logging
    migrations/         Alembic migrations (0001 Phase 1, 0002 Phase 2, 0003 Phase 3A, 0004 Phase 3B)
    seeds/              initial brand profile / goals / rules (JSON)
    tests/
  frontend/             Vite + React + TypeScript dashboard
  data/                 local SQLite database + encrypted secrets.dpapi (git-ignored)
  docs/ARCHITECTURE.md
  scripts/              setup / run / test, run-demo.ps1 + mock_telegram.py + mock_x.py (development only)
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and extension points.

## Troubleshooting

- **Dashboard says "Backend unavailable"**: start the backend (port 8000).
- **"AI unavailable" / model status Offline**: Ollama isn't running. Start "Ollama" from the Start menu.
- **"Model unavailable" / "Model not installed"**: `ollama pull <model>`, or pick an installed model in Settings.
- **First answer after starting or switching models is slow (10–30 s)**: the model is loading into GPU memory. Later answers are fast. `OLLAMA_KEEP_ALIVE` controls how long it stays loaded.
- **Knowledge shows "not indexed"**: the embedding model wasn't available when you added it. Run `ollama pull nomic-embed-text`, then click **Index for meaning search** on the Knowledge page. Indexing also runs automatically on startup.
- **`pip` fails with "An Application Control policy has blocked this file"**: Windows Smart App Control blocked a source build. `requirements.txt` already prefers prebuilt wheels; if it still happens, pin the previous version of the failing package.
- **Community page says "Not configured"**: paste the bot token on **Community › Connection** (or set `TELEGRAM_BOT_TOKEN` in `.env` and restart).
- **X "rejected the credentials"**: the Bearer Token or account keys are wrong or were regenerated. Copy them again from console.x.com and **Save & test**.
- **X feature shows "Not available"**: your X access or plan doesn't include that endpoint. Price Chat skips it; the rest keeps working.
- **X reading stopped with "budget reached"**: raise `X_DAILY_BUDGET_USD` / `X_MONTHLY_BUDGET_USD` in `.env` and restart, or wait for the next day (UTC).
- **X "rate limit" / "usage cap"**: Price Chat pauses until the reset time shown on the X Overview and resumes automatically. A usage cap can also mean your X credit is used up.
- **Saved keys stop working after moving Price Chat to another Windows user or computer**: DPAPI encryption is tied to your Windows account. Paste the keys again.
- **Telegram "rejected the bot token"**: the token is wrong or was revoked. Get a new one from @BotFather.
- **Telegram "Another program is reading updates for this bot"**: another backend or tool uses the same token, or a webhook is set. Stop the other program.
- **Group messages never arrive**: privacy mode is still on (`/setprivacy` → Disable), or the chat isn't authorised yet.
- **A background job keeps failing**: see **Settings › Background jobs** for the error. Failing jobs retry with growing delays (max 30 min) and recover automatically.
- **Undo a migration**: pre-migration copies are in `data\` (`price_chat.phase1-backup.db`, `price_chat.pre-phase3a-*.db`).
- **Undo the Phase 2 migration**: a pre-migration copy of the Phase 1 database was saved as `data\price_chat.phase1-backup.db`.
