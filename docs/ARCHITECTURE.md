# Price Chat architecture (through Phase 3B/4A)

## Principles

1. **The model layer is abstract.** Only `app/providers/` knows which backend exists. Agents, memory, knowledge, routes, the dashboard and connectors depend only on `LLMProvider` and `EmbeddingProvider`, plus the provider-neutral error types.
2. **Nothing external happens yet.** Connectors and knowledge sources are placeholders. `SafetyPolicy.outbound_enabled` defaults to `False`, and the prompt's hard limits forbid publishing.
3. **Brand personality is data, not code.** Identity, behaviour rules, brand profile and goals live in the database and are editable in the dashboard. `backend/seeds/price_chat.json` supplies initial values only.
4. **Everything is auditable.** Inference, retrieval, memory changes, configuration changes, provider failure/recovery and safety decisions all go to `activity_logs`, with secrets redacted.
5. **Portable data.** Alembic migrations, VARCHAR enums, JSON columns, UTC-aware timestamps (`UTCDateTime`) and deterministic constraint names. Moving to PostgreSQL means a new `DATABASE_URL`.

## Target pipeline (future phases)

```
Telegram community ─▶ community knowledge ─▶ memory ─▶ PriceChatAgent ─▶ topic/opportunity detection
   ─▶ content/reply generation ─▶ approval (SafetyGuard) ─▶ X / Twitter ─▶ analytics ─▶ learning/memory
```

Phase 2 builds the **memory → agent** core. The ingestion and connector contracts for the other stages are in place.

## Request flow: chat

```
Dashboard ─POST /api/agent/chat─▶ routes/agent.py ─▶ services/agent_service.run_chat()
  build_agent(): reads DB config, builds plain dataclasses
     AgentProfile (identity, rules, status, model override)   ← agent_configs
     BrandProfileData                                         ← brand_profiles
     GoalData[] (enabled, by priority)                        ← agent_goals
     SqlConversationMemory (last N turns)                     ← conversation_messages
     HybridRetriever (MemoryStore + EmbeddingIndex)           ← memories / memory_embeddings
     runtime.provider                                         ← LLMProvider (OllamaProvider)
  PriceChatAgent.respond():
     1. retrieve relevant memories (logs "memory_retrieval")
     2. SystemPromptBuilder.build(PromptContext)   → compiled system prompt
     3. provider.chat(system + recent turns + user message)
     4. persist both turns with model, latency, tokens, memory ids
     5. log "ai_inference" (+ "agent_task" for drafting tasks)
  ProviderMonitor records success/failure and logs provider_failure / provider_recovery transitions
```

The response includes the reply, model, latency, prompt and completion tokens, tokens/s, and the memories used with their scores and reasons.

### Error mapping (provider-neutral)

| Error (app.providers.errors) | HTTP | `code` | Dashboard shows |
|---|---|---|---|
| `ProviderUnavailableError` | 503 | `provider_unavailable` | AI unavailable |
| `ProviderTimeoutError` | 504 | `provider_timeout` | AI took too long |
| `ModelNotFoundError` | 503 | `model_unavailable` | Model unavailable |
| `ProviderResponseError` | 502 | `provider_error` | AI unavailable |
| `AgentPausedError` | 409 | `agent_paused` | Agent paused |
| no response / proxy 5xx without JSON | — | — | Backend unavailable |

## Provider migration (Ollama → Vortex / DGX Spark / OpenAI / Anthropic)

1. Create `app/providers/vortex.py` implementing `LLMProvider`: `chat`, `health(model)`, `list_models`, `endpoint`, `aclose`. Map the backend's failures onto the error classes above. Optionally implement `EmbeddingProvider` too.
2. Add its settings to `app/config/settings.py` (e.g. `vortex_base_url`, `vortex_api_key`). Secrets come from `.env` only. Never include credentials in `endpoint`.
3. Register builders in `app/providers/factory.py` (`_BUILDERS`, `_EMBEDDING_BUILDERS`).
4. Set `LLM_PROVIDER=vortex` (and optionally `EMBEDDING_PROVIDER`) in `.env`.

Nothing else changes: not the agent, memory, knowledge, dashboard, connectors or content. Everything Ollama-specific (endpoints, payloads, `keep_alive`, error strings, "ollama pull" hints, nomic task prefixes) is inside `app/providers/ollama.py`.

If the embedding model changes, existing vectors are ignored because they're tagged with their model. Click **Index for meaning search**, or restart, to re-embed. Re-check `RETRIEVAL_MIN_SIMILARITY` for the new model.

## System prompt builder (`app/agents/prompting/`)

- `context.py`: `PromptContext` (plain data: identity, brand, goals, rules, memories, task, platform, memory budget).
- `sections.py`: one renderer per section, in this order: identity, brand profile, voice, claims policy, goals, behaviour rules, platform context, current task, relevant knowledge (char-budgeted, framed as reference data rather than instructions), hard limits. Empty sections are omitted.
- `builder.py`: `SystemPromptBuilder(sections)` produces a `CompiledPrompt` (text plus per-section sizes). Pass a different section tuple to customise.

`GET /api/agent/prompt?task=&platform=&query=` returns the exact compiled prompt; the dashboard shows it read-only.

## Memory

| Layer | Purpose | Storage | Source today / later |
|---|---|---|---|
| A. Conversation | Recent turns of each conversation | `conversation_messages` (persistent; last `CONVERSATION_WINDOW` sent) | dashboard chat |
| B. Brand | Long-term brand knowledge | `memories` (layer=brand) | Knowledge page / documents, URLs |
| C. Community | Learned community knowledge | `memories` (layer=community) | Knowledge page / **Telegram** |
| D. Social | Social knowledge | `memories` (layer=social) | **X / Twitter** |

Every memory has `id, layer, kind, title, content, tags, source_type, source, external_id, platform, author, occurred_at, importance, created_at, updated_at`. `(agent, source_type, external_id)` is unique, so re-ingesting the same Telegram message or X post updates it instead of duplicating it.

### Retrieval: decision and design

**Choice: hybrid retrieval, with local embeddings plus keyword/tag scoring, behind a swappable `MemoryRetriever` interface.**

- Embeddings come from a local Ollama model (`nomic-embed-text`, 274 MB) through the `EmbeddingProvider` interface. There's no cloud service and no new infrastructure.
- Vectors are stored as normalised float32 blobs in SQLite (`memory_embeddings`, tagged with model and content hash). Search is a numpy dot product, which handles tens of thousands of memories in milliseconds. pgvector or a vector DB can replace `EmbeddingIndex` later without touching the agent.
- `HybridRetriever` blends: semantic similarity (above `RETRIEVAL_MIN_SIMILARITY`) at 0.65, keyword/tag score (IDF-weighted, with title and tag boosts) at 0.35, and importance at 0.1. Without a meaning match, a memory must match at least half the query's keywords, so the brand name alone can't pull in unrelated memories.
- If the embedding model is unavailable, retrieval **automatically falls back to keyword search** and says so in the response. Knowledge is always saved, and unindexed items are embedded later by a startup backfill or the Reindex button.
- The threshold of 0.60 was calibrated on this machine with `nomic-embed-text`: related pairs scored 0.63–0.83, unrelated pairs at most 0.58.

Why not keyword-only: in testing, "What animal represents us?" found "Our mascot is a green owl" only through semantic similarity, with no shared words. That is typical of how people ask questions.

## Knowledge input and ingestion (`app/knowledge/`)

- `KnowledgeService` is the single entry point for adding, updating, deleting and reindexing knowledge. It normalises input (control characters, tags, importance), de-duplicates, embeds and logs.
- `ingestion/base.py` defines `KnowledgeSource` (yields `IngestionItem`s and maps them to `MemoryInput`), `Ingestor` (runs a source through `KnowledgeService`) and `IngestionReport`.
- `ingestion/sources.py` has placeholders for `TelegramCommunitySource` (community layer), `XPostsSource` (social), `DocumentSource` and `UrlSource` (brand). All report "Not connected".

## Data model

| Table | Purpose |
|---|---|
| `agent_configs` | Identity, behaviour rules, status, model/temperature override |
| `brand_profiles` | Brand name, description, mission, audience, tone, writing style, personality; lists: products/services, topics of interest, topics to avoid, terminology, allowed/restricted claims, social objectives |
| `agent_goals` | name, description, priority (1–5), enabled |
| `memories` / `memory_embeddings` | Long-term knowledge and its vectors |
| `conversation_messages` | Every chat turn, with model, latency, tokens and memory ids |
| `content`, `social_accounts`, `social_posts`, `interactions`, `tasks` | Content pipeline and social data (Phase 1) |
| `activity_logs` | Activity and audit trail |
| `safety_policies`, `action_limits` | Approval/automatic mode, kill switch, rate limits (Phase 1) |

Migrations: `0001` is the Phase 1 baseline; `0002` is Phase 2, which copies the Phase 1 brand/goal JSON into the new tables. Existing Phase 1 databases (no version table) are detected and stamped automatically. Migrations run with SQLite foreign-key enforcement off, because SQLite rebuilds tables to alter them and cascades would otherwise delete child rows.

## Observability

`ActivityLogger` writes `activity_logs` rows and mirrors them to the console. `EVENT_CATALOG` maps each event to a category (ai, memory, task, config, safety, content, system) and a plain-English label for the Activity page. Details keys that look like credentials (`*api_key*`, `*secret*`, `*password*`, `*_token`, …) are redacted before storage.

## Safety model (unchanged from Phase 1)

`SafetyGuard.check(platform, action)` evaluates, in order: kill switch, outbound master switch, per-action enablement, global and per-action hourly/daily limits, then approval vs. automatic mode. Every decision is audited. Connectors must call it before any outbound action and log `social_action_executed` afterwards.

## Security

- Secrets are read only from `.env` (git-ignored). The API never returns them, and the activity log redacts them.
- All services bind to `127.0.0.1` (backend, Vite, Ollama). CORS allows only the dashboard origins, with no credentials.
- Request bodies are validated with Pydantic: length limits, ranges, patterns, and unknown fields rejected. Text is NFC-normalised with control characters stripped. React escapes all output.
- Retrieved knowledge is framed in the prompt as reference data, not instructions, which limits prompt injection from future ingested content.

## Known limits

- No authentication: the API is for a single local user. Add auth before exposing it beyond localhost.
- Responses are not streamed; the UI waits for the full answer, showing elapsed time.
- Single process: the health cache and provider monitor state are in memory. Conversation memory is in the database.
- Telegram (Phase 3A): a bot only receives messages sent after it joins, so there is no history import. The "chats the bot can see" list is in memory and resets on restart (by design: nothing about unauthorised chats is stored).
- Topics are never merged or split automatically. Phase 3B suggests merges and lets a human merge or split.
- Background jobs share the GPU with chat: the AI gate makes new background calls wait while a chat is active, but a model call already running is not interrupted, so a chat reply can still wait for it (20–60 s worst case).
- X (Phase 3B/4A): read-only. Cost figures are local estimates from published per-request prices; the X console is the authority. Search covers the last 7 days only. Relationship data covers only interactions Price Chat has seen.
- Social writes are phase-locked in the backend (`WRITE_EXECUTION_AVAILABLE = False`); enabling them needs a code change and a reviewed write connector, not a setting.
- DPAPI-encrypted secrets can only be decrypted by the same Windows user on the same computer; after moving machines, paste them again.

---

# Phase 3A: Telegram community intelligence

Read-only. Price Chat observes authorised Telegram chats and turns the discussion into reviewed knowledge and content ideas. Nothing is ever sent to Telegram, and the Bot API client has no send method.

## Pipeline

```
Telegram (official Bot API, long polling)
   │  telegram.ingest      allow-list enforced here; unknown chats discarded on arrival
   ▼
telegram_messages          raw messages (deduplicated, deletable); never written to agent memory directly
   │  community.process     classify (fast model, rule fallback) → embed → assign topic / question group
   ▼
community_topics, community_questions          meaning-based clusters + honest trend statistics
   │  community.topics      recount, trend, promote/archive, name topics, word recurring questions (fast model)
   │  community.summary.*   recent (fast model) / daily & topic (strong model) summaries, with cited sources
   ▼
community_summaries
   │  community.memories    strong model proposes durable insights → PENDING community memories
   ▼
memories (layer=community, review_status=pending → approved by a human)
   │  content.opportunities strong model turns rising topics / recurring questions into ideas
   ▼
content_opportunities  detected → reviewing → approved → drafted (Content draft) → [published: future, via SafetyGuard]
```

Each stage is a scheduled job (`app/jobs.py`). Stages are idempotent and chained: when a stage produces new work it brings the next stage forward (`Scheduler.request_run`). New messages therefore reach opportunities in a few minutes, and a failure in one stage never corrupts another.

## Telegram integration (`app/telegram/`)

- **Approach:** the official Bot API over HTTPS long polling (`getUpdates`), using httpx. There is no extra dependency and no webhook, so no inbound port is needed. An MTProto user-account client was deliberately not used: it would automate a personal account.
- **Client** (`client.py`): getMe, getUpdates, getChat, getChatMemberCount only. Errors map to `TelegramAuthError` (401), `TelegramConflictError` (409), `TelegramRateLimitError` (429, with retry_after), `TelegramUnavailableError` (network/5xx/timeouts) and `TelegramRequestError`. Error messages never include the URL. The token is part of the URL, so httpx exceptions are re-raised `from None`, and `__repr__` hides the token.
- **Parser** (`parser.py`) keeps only: message/chat ids, thread and reply ids, author id and display name, timestamps, text/caption, forward origin *name*, and a media *type* label. Files are never downloaded. Phone numbers, bios and contact cards are dropped. Service messages (joins, pins) are ignored. Malformed updates are counted and skipped.
- **Ingestion** (`ingestion.py`): the allow-list (`telegram_sources`) is checked for every update. Unknown chats are discarded, and only their id and title are kept *in memory* (`TelegramRuntime.discovered`) so the user can choose to authorise them. Disabled chats are ignored. Messages and the new update offset are committed **in one transaction**: if the database fails, nothing is acknowledged, Telegram re-delivers, and the `(chat_id, message_id)` unique key prevents duplicates. Edits update the text and re-queue processing.
- **Credentials:** `TELEGRAM_BOT_TOKEN` in `.env` only (a `SecretStr` in settings). The dashboard shows status, bot identity, chats, last sync, counts and last error; never the token. The activity log redacts token-shaped values and `/bot<token>` URLs.

## Privacy and deletion

- Collection is limited to allow-listed chats and the fields above. There are no per-member pages, profiles or aggregation, and members are **pseudonymised** ("Member 3") in every model prompt.
- Prompts instruct models to refer to people only as "members" and never to include personal information in memories.
- `DELETE /api/telegram/messages/{id}` deletes one message. `DELETE /api/telegram/sources/{id}` removes a chat and **all** its data: messages, vectors and per-chat summaries, plus any memory, summary or (unreviewed) opportunity whose *only* supporting evidence came from the deleted messages. Topic and question statistics are recomputed, and every deletion is written to the audit log.

## Topic detection (`community/topics.py`)

- Substantive messages (not noise or chatter, at least 15 characters, not bots) are embedded with the configured embedding model and join the topic whose centroid is most similar (cosine ≥ `COMMUNITY_TOPIC_SIMILARITY`, default **0.75**). Otherwise they seed a *candidate* topic. Centroids are running means.
- A candidate becomes **active** at ≥ 3 messages from ≥ 2 people, and is **archived** after 21 idle days.
- **Trend** compares the last 24h with the previous 24h. It reports *new* (first seen < 24h ago), *rising* (≥ 2× and +5), *falling* (≤ ½) or *steady*, and **"insufficient data" when the two windows together hold fewer than 6 messages**. No significance is claimed that the data can't support.
- Each topic stores message and participant counts, first/last seen, 24h volumes, representative messages (closest to the centroid), importance (recent volume and breadth) and confidence (cohesion × size). Active topics are named and described by the fast model.
- The thresholds were calibrated on this machine with `nomic-embed-text`: same-topic messages scored ≥ 0.80 to their own centroid. At 0.75, 10% of messages were as close to another topic's centroid (13% at 0.70).

## Recurring questions (`community/questions.py`)

Messages classified as questions are grouped by meaning with a stricter threshold (`COMMUNITY_QUESTION_SIMILARITY`, **0.80**; measured: same question ≥ 0.82, different questions ≤ 0.69). A group asked ≥ 2 times by ≥ 2 people is *recurring*. At 2, 5, 10… asks the fast model rewrites the group as one clear canonical question. From the dashboard a human can **write an FAQ answer**: it becomes an *approved brand* memory, linked to the question's messages. Questions can also be dismissed. Nothing is answered in Telegram.

## Summaries (`community/summaries.py`)

Recent activity summaries cover up to 6h per chat and need ≥ 8 substantive messages. Daily summaries cover the user's local day, once per chat per day. Topic summaries are on demand. Prompts contain pseudonymised messages tagged `[m<id>]`, plus detected topics and recurring questions. The model returns structured JSON (themes, emerging topics, questions, concerns, ideas, content suggestions), and every point cites message ids. Citations to messages the model wasn't shown are dropped and counted. Inline ids are stripped from display text. Each summary stores provider, model, generation time, duration, window and provenance links to every included message.

## Community memory (`community/extraction.py`)

Raw messages never become memories. For each summary, the strong model proposes at most 6 durable insights, typed: recurring question, important topic, observation, terminology, concern, product feedback, content idea or useful fact. Each must cite valid messages or it is discarded. Proposals are de-duplicated against existing community memories using *document-to-document* similarity (≥ 0.80; paraphrases measured 0.81–0.91). They are stored as `layer=community`, `source_type=telegram`, `review_status=pending`, with importance capped at 0.7, metadata in `extra` (model, provider, task, generated_at, confidence, summary id) and provenance links. Re-ingestion never overrides a human's review status or importance.

## Content opportunities (`community/opportunities.py`)

Signals are active topics that are new, rising or important, and recurring questions asked ≥ 3 times. Each signal is evaluated at most once per 72h. The strong model sees a brand brief (audience, objectives, goals, topics to avoid, restricted claims), the signal statistics and representative messages. It returns worthwhile, title, why, angle, platforms (X, YouTube, Shorts, TikTok, Telegram), urgency and confidence, with citations. Unworthy signals are stored as *rejected* with the reason, so they aren't re-evaluated. Evidence counts come from the database, not the model. "Draft with AI" builds the normal system prompt (brand, goals, rules and platform rules for the chosen channel) plus the opportunity and paraphrased evidence, runs the `content.draft` task and saves a `Content` row with status **draft**. The API refuses to mark anything published.

## Task → model routing (`ai/routing.py`)

| Task | Default tier |
|---|---|
| telegram.classify, telegram.topic_naming, telegram.question_canonical, community.summary.recent, community.search_answer | fast |
| community.summary.daily, community.summary.topic, community.memory_extraction, content.opportunity, content.draft | strong |

Tiers map to models in `.env` (`MODEL_TIER_FAST=llama3.1:8b`, `MODEL_TIER_STRONG=gemma3:27b`). Any task can be pointed at another tier or a specific model from **Settings › Model routing** (stored in `model_routes`). If a routed model isn't installed, the router falls back to the fast tier once. Callers name *tasks*, never models or providers, and the router calls the provider through `LLMProvider`. A Vortex / DGX Spark move is a provider registration plus tier configuration.

Structured output: `ChatOptions.response_schema` (JSON Schema) is the provider-neutral contract; Ollama maps it to `format`. `ai/structured.py` validates replies with Pydantic, retries once with the validation error, and normalises 0–10 / 0–100 scores to 0–1, because models sometimes ignore ranges.

## Scheduler (`scheduler/scheduler.py`)

A small in-process asyncio scheduler with persistent state in `scheduled_jobs`: status, last run, next run, duration, last result, last error, run and failure counts.

- A job never overlaps itself.
- Each job has a timeout, and failures back off exponentially from the interval up to 30 minutes, so there are no tight retry loops. `JobSkipped` covers "nothing to do", e.g. Telegram not configured.
- Activity logs one entry per failure streak and one on recovery.
- Jobs interrupted by a restart are marked failed.
- The dashboard shows every job and can run, pause or resume it.

There are no extra services: on one Windows machine this runs inside the backend process.

## Telegram search (`community/search.py`)

Search understands simple time phrases in the user's local timezone (today, yesterday, this/last week, this month, last N days/hours). It returns traceable evidence:

- raw messages (meaning and keyword match, with reasons)
- community memories (with review status)
- overlapping summaries
- matching topics

With `answer=true`, the fast model answers **only from that evidence**, citing ids like `[m12] [s3] [t4] [k7]`. Citations are checked against the evidence and the answer is labelled AI-generated.

## Agent integration

`PriceChatAgent` gets an optional `CommunitySource` (implemented by `community/context.py`). For each request it adds only *relevant* intelligence:

- topics whose centroid matches the query
- recurring questions that match
- the latest summary, when the request is about the community
- up to 4 verbatim, anonymous member messages from the last 30 days

The whole section is limited to 2,500 characters. The prompt section labels each kind: observations are not brand facts, summaries are interpretations rather than quotes, and raw messages are verbatim but unattributed. Community memories reach the agent through normal retrieval **only after approval**. Telegram code never calls Ollama: every model call goes through the router and `LLMProvider`.

## Data model additions (migration `0003`)

| Table | Purpose |
|---|---|
| `telegram_sources` | Allow-list: chat id, name, type, enabled, ingestion enabled, added, last sync, message count, last error |
| `telegram_messages` | Raw messages + processing results (category, is_question, topic, question group) |
| `telegram_message_vectors` | Embeddings of messages |
| `telegram_bot_state` | Bot identity, update offset, status, last success/error, totals |
| `community_topics`, `community_questions`, `community_summaries` | Derived intelligence |
| `content_opportunities` | Idea queue with evidence and status |
| `provenance_links` | (derived kind, derived id) → supporting message, for memories, summaries and opportunities |
| `model_routes` | Per-task model overrides |
| `scheduled_jobs` | Job state |
| `memories.review_status` | approved / pending / rejected (existing memories → approved) |

## Failure handling

| Failure | Behaviour |
|---|---|
| Invalid token | `telegram_auth` error on status and in Activity; job backs off (max 30 min); rest of the app unaffected |
| Telegram/network down, timeouts, 5xx | `telegram_unavailable`; offset not advanced; automatic recovery is logged |
| Rate limited (429) | error recorded with retry_after; job backs off |
| Two readers / webhook (409) | clear message to stop the other reader |
| Malformed update | skipped and counted; the batch continues |
| DB failure during ingestion | transaction rolled back, offset not advanced, re-delivered without duplicates |
| Model unavailable during processing | classification falls back to rules; embedding failures leave messages unprocessed for retry |
| Model unavailable for summaries/memories/opportunities | job fails cleanly (nothing half-saved) and retries with backoff; strong→fast fallback if only the strong model is missing |
| Model returns malformed JSON | one retry with the validation error, then a clean failure |


# Phase 3B/4A: community pilot and X intelligence

## Secrets (`app/security/`)

- `SecretStore` keeps secrets entered on the dashboard in `data\secrets.dpapi`, each value encrypted with **Windows DPAPI** (`CryptProtectData`, bound to the current Windows user, with app-specific entropy). Off Windows it falls back to a `chmod 600` file. Writes are atomic.
- `credentials.resolve(name)` returns the dashboard value first, then `.env`. Only `info()` (configured / source / saved_at) ever leaves the backend; there is no API that returns a secret. Secrets are redacted from logs and errors and never put into prompts.
- Saving the Telegram token verifies it with `getMe` before storing; an invalid token is not saved. X keys are saved, then probed with **Test connection**.

## Telegram pilot

`/telegram/setup` drives a six-step wizard (create bot, privacy mode, token, add to group, authorise, receiving), each step checked automatically where possible (`done: null` when it cannot be). The allow-list is unchanged: adding the bot to a group never ingests anything. **Pause/Start ingestion** toggles the `telegram.ingest` job. `/community/pilot` reports received / processed / ignored counts and pipeline job state; `/telegram/messages/{id}/trace` shows everything derived from one message.

## Human feedback and preference learning (`app/learning/feedback.py`)

Every approve / reject / edit / important / not-important decision on a memory, topic, question, opportunity, X opportunity or draft is stored as a `FeedbackEvent` with **interpretable features** (keywords, angle, platforms, insight type, origin) and an embedding. `PreferenceModel` learns one weight per feature, `(approvals − rejections) / (approvals + rejections + 2)`, and also looks at the nearest past decisions by meaning. Its score is one ranking signal (`learned_preference`) with human-readable reasons ("similar to 3 ideas you approved"). No model is fine-tuned; nothing is hidden.

## Opportunity ranking (`app/community/ranking.py`)

Each opportunity gets separate 0–1 signals: community relevance, recency, volume/growth, participants, novelty (not covered in 30 days), brand relevance, content potential (the model's own estimate), confidence and learned preference. `WEIGHTS` combine them into a priority; "marked important" adds ±0.25 and an active Telegram ↔ X signal adds a boost. Signals, reasons and the label (high/medium/low) are stored and shown; the dashboard never shows a single opaque score.

## Topic merge / split (`app/community/merging.py`)

Candidate pairs come from centroid similarity (≥ 0.80, or ≥ 0.65 with shared keywords), shared members and threads; the fast model judges "same / related / different". Results are **suggestions only**. Merging moves messages and questions to the kept topic, recomputes its centroid, marks the other `merged` with `canonical_id` and keeps its name as an alias. Split moves selected messages to a new topic linked via `related_ids`. Nothing is deleted.

## Drafts (`app/content/drafts.py`)

Nine draft types (X post/thread, Telegram post, YouTube idea/titles/outline, short-form idea/hook, TikTok idea), each with its own length rules and three variant styles. Drafts are built with the normal `SystemPromptBuilder` (brand profile, approved memories, community context) through the `content.draft` / `content.variants` routes (strong tier), checked for length, hype words and restricted claims, and stored as `Content` with a `context` explaining why they exist and what they used. Drafting requires an approved opportunity. Drafts are never published.

## X connector (`app/connectors/x/`)

- Official X API v2 only. App-only **Bearer** auth for public data; optional **OAuth 1.0a user context** (signed per request, verified against the published example signature) for the account's own posts and private metrics.
- Endpoints: `users/by/username`, `users/me`, `tweets/search/recent`, `users/:id/tweets`, `users/:id/mentions`, `tweets?ids` (non-public metrics). **The client has no write methods.**
- Errors map to typed exceptions: 401 → auth, 403 → forbidden (feature unavailable on this access level), 429 → rate limit (with reset time) or usage cap, plus not-found, bad request and network failures.
- Cost: pay-per-use estimates (≈ $0.005 per post, $0.01 per user, $0.001 per owned read) are recorded per UTC day in `ConnectorState.usage`; `PlatformBudget.check()` runs **before** every request and stops at the daily / monthly budget.
- **Test connection** probes each capability once and stores the result (available / unavailable / not configured / disabled). Ingestion only uses what is available.

## Social layer (`app/social/`, platform-independent)

`SocialProfile`, `SocialPost` (+ vectors and metric snapshots), `SocialRelationship`, `SocialOpportunity`, `CrossSignal`, `ApprovalRequest` and `SocialActionLog` carry a `platform` column; the X specifics live in `x_ingest.py` / `x_opportunities.py` / `x_service.py`. A future platform adds an ingestor and reuses the store, ranking, guard and approvals. `PriceChatAgent` does not know about X: it only sees memories and context.

- **Ingestion** (`x.ingest`, every 15 min): own posts, mentions, monitoring rules and watchlist accounts, each incremental via `since_id` watermarks, at most 20 requests per run. A rate limit pauses reading until the reset; a budget stop ends the run cleanly. New posts are embedded for matching.
- **Metrics**: stored exactly as returned; anything X does not return is `null` (shown as "n/a"), never estimated. A snapshot is stored only when numbers change.
- **Relationship memory**: only public interactions with our account (mention, reply) — counts, first/last date, topic words, and the user's own notes. Follower relationships are `null` unless the API says so. No sensitive or demographic inference.
- **X opportunities** (`x.opportunities`): the fast model filters a batch of new posts (`x.relevance`), the strong model explains the promising ones (`x.opportunity`: why, what Price Chat could add, suggested action, relevant knowledge). Spam / giveaways are dismissed. Ranked by relevance, author reach, engagement, recency, community overlap, watchlist and mention signals.
- **Cross-signals** (`cross.signals`): an active Telegram topic whose centroid matches ≥ 2 recent X posts (similarity ≥ 0.68, 72 h window) becomes a `CrossSignal` with both sides' volume and trend and a model-written recommendation; stale after 48 h without activity.

## Social action safety (`app/social/guard.py`, `approvals.py`)

`SocialActionGuard.evaluate()` runs, in order: kill switch → outbound master switch → platform enabled → per-action permission (disabled / approval) → hourly and daily limits → duplicate text → per-account cooldown → blocked accounts → watchlist-only → approval → **phase lock**. It returns `allowed` (the rules permit it) separately from `executable`; `WRITE_EXECUTION_AVAILABLE = False` makes `executable` always false in this phase. "Automatic" permission is rejected by the API. Approving an approval request records the decision and a simulated execution (`executed: false, simulated: true`); no connector has a write method to call. Pending requests expire (`approvals.expire`).

## Performance

- **AI gate** (`app/ai/gate.py`): interactive chat and draft requests take priority; background model calls run one at a time and wait (up to 120 s) while a chat is active. `/system/processing` exposes what is running.
- **Scheduler**: jobs run one at a time (model calls are serialised anyway) and chain stages via `request_run` (ingest → process → topics → merges; X ingest → X opportunities + cross-signals → ranking).
- **SQLite**: WAL journal and a 60 s busy timeout so dashboard writes and background jobs don't fail on contention.

## Model routing additions

`content.variants` (strong), `topic.merge_judge` (fast), `x.relevance` (fast), `x.opportunity` (strong), `cross.synthesis` (strong). `content.draft` allows longer output for threads and outlines.

## Data model additions (migration `0004`)

`social_profiles`, `watched_accounts`, `monitor_rules`, `social_post_vectors`, `social_post_metrics`, `social_relationships`, `social_opportunities`, `cross_signals`, `approval_requests`, `social_action_logs`, `feedback_events`, `topic_merge_suggestions`, `connector_states`; new columns on topics (aliases, canonical, review, importance), questions, opportunities (signals, reasons, priority), content (draft type, variants, context), social posts and safety policy (platforms, blocked targets, watchlist-only, cooldown, duplicate window). All new columns have server defaults so existing rows migrate in place.

# X Operator V1 (supervised writes)

- **Single path to a write:** `approvals.decide()` (human) → `approvals.execute()` → `SocialActionGuard.evaluate(approved=True)` → dry run stops here and records `would_execute` → otherwise the guard issues a single-use `ExecutionPermit` → `x_executor.perform()` → `XClient.create_post/like/repost/follow`, which call `require_permit()` before any request. No other code can mint a permit, and `execute()` refuses anything not human-approved.
- **Guard checks:** social actions active (kill switch) → outbound → platform → X connection (incl. rate-limit block) → user-context credentials → write capability (from the reported access level or a previous 403) → permission → hourly/daily limits (only executed actions count; dry runs don't) → duplicate action (same post/account) → duplicate text → per-account cooldown → one reply per account per hour → blocked list / watchlist-only → human approval. AUTOMATIC mode is locked (`AUTOMATIC_AVAILABLE = False`) and rejected by the API.
- **Executed only on confirmation:** X must return the new post id / `liked` / `retweeted` / `following`. Errors mark the request Failed; a timeout is marked uncertain. A 403 marks that write capability unavailable so the guard stops proposing it.
- **After execution:** our post is stored as an own SocialPost (performance is attached by later reads, cached 6 h), relationship memory gains our replies/likes/reposts/follow, activity and audit log the action.
- **Replies:** `social/x_replies.py` (task `x.reply`, strong tier) builds a compact context (post, parent, author relationship, our previous replies, topic, approved knowledge, Telegram signal, brand rules, X voice, learned hints) and rejects generic praise or over-length output once.
- **Learning:** `learning/operator.py` stores each decision as a FeedbackEvent (`social_action`) with readable features and edit features; `operator_signals()` and `drafting_hints()` turn them into visible signals and writer instructions.
- **Migration 0006:** `safety_policies.dry_run` (default on), relationship like/repost counters, brand `x_voice`/`x_avoid`, `social_actions.target_account`/`external_id`; existing X action limits become approval-required with the conservative ceilings.

# Supervised social growth pilot (X + Telegram)

- **Telegram account mode** (`app/telegram/account.py`, `account_service.py`, routes `/telegram/account/*`): official MTProto client (Telethon) behind an `AccountGateway` protocol (faked in tests). Login: `send_code_request` → `sign_in(code)` → optional `sign_in(password)`; codes and passwords are passed through, never stored or logged. The StringSession and API Hash are DPAPI secrets (`telegram_session`, `telegram_api_hash`), never returned by the API. `log_out` revokes the session at Telegram and deletes it locally; a revoked session found during sync is deleted and the status becomes `needs_login`. `get_dialogs` lists only groups/channels; a chat can be authorised only if it was in the discovered list. `sync` reads only enabled `via="account"` sources, starting from authorisation time (or the last message id), network first, then one short write. History import is opt-in: 24 h / 7 d / 30 d, estimated first, capped at 5,000 messages. Bot and account messages share `ingestion.store_message` (dedup on chat_id + message_id).
- **Early community signal** (`social/cross_signals.py`): Telegram first-seen ≥ 60 min before the first matching X post, X monitoring already active at that time, within 72 h. Adds +0.15 to the X relevance signal with an explicit reason. Not a market signal.
- **Growth feed** (`social/growth.py`, routes `/growth/*`): one feed of X opportunities and Telegram opportunities with 13 named ranking signals (HIGH ≥ 0.55, MEDIUM ≥ 0.38) and a quality filter (stale, recently engaged, nothing to contribute, low relevance, similar recent post, unclear context, weak evidence, inactive, rejected before). `review_session` picks 5–10 passing items, at most 2 per topic/author. `prepare` drafts a reply (variants for high priority) or a post and proposes it through the normal approval queue; skip records a SKIP feedback event and leaves the approval pending.
- **Reply drafting:** Telegram context enters X drafts only as aggregate lines (counts, topic, recurring questions); the prompt forbids naming or quoting members. `check_text` re-checks edited/variant text for generic phrasing, unsupported facts and placeholders.
- **Learning controls:** `operator_preferences` (accept / disable / reset with `reset_at`); disabled rules are never used as drafting hints.
- **Performance checkpoints:** forced metric snapshots ~1 h, 6 h and 24 h after an executed post (`checkpoint_{n}h`).
- **Usage:** in-memory counters (`services/usage.py`, model calls by tier/task, Telegram requests) flushed to `usage_counters` after each job so no write transaction spans an await.
- **Unchanged:** every X write still goes through approval → guard → single-use permit; automatic mode is locked; nothing is ever sent to Telegram.
- **Migration 0007:** `telegram_account`, `operator_preferences`, `usage_counters`, `discovery_ignores`; `telegram_sources.via`, `cross_signals.early_signal` / `lead_minutes`.

# X likes pilot

- **Recommendation** (`social/likes.py`): candidates are X opportunities whose AI advice set `like`, filtered deterministically (age ≤ 72 h, relevance ≥ 0.5, not a question/mention, no farming patterns, not brand-avoided topics, not already liked or duplicate text, account not liked in 12 h or ≥ 3 times in 7 days) and ranked by relevance, watchlist, freshness and priority; one per account, top 5. `GET /x/likes` (status + candidates), `POST /x/likes/{opportunity_id}/propose` (reuses a pending request, 409 for weak candidates).
- **Guard additions:** `like_cooldown` (one like per account per `LIKE_ACCOUNT_COOLDOWN_HOURS` = 12) and `post_available` (a post X answered 404 for is not targeted again), on top of kill switch, connection, user auth, capability, permission, hourly/daily limits, duplicate action and cooldown.
- **Execution record:** approval `execution_result` holds target post/author ids, executing account, approval id, timestamp, X's `data` and the post URL. Errors are mapped to readable reasons (`explain_x_error`) with `error_kind` (auth, billing, permission, unavailable, rate_limit, x_service, timeout, budget).
- **Migration 0008:** `social_relationships.last_liked_at`, `last_liked_post_id`.

# Opportunity screening fix and growth dashboard

- **Screening** (`social/x_opportunities.screen_posts`): `x.relevance` runs on batches of `SCREEN_BATCH` = 10 posts (40 per run → 4 calls) instead of one 40-post call that overran the 1,500-token output limit. A cut-off or malformed answer keeps every complete verdict (`ai/structured.salvage_items`, using `StructuredOutputError.raw`); only missing posts are re-screened, in halves, down to single posts; a post with no usable verdict stays unscreened (never guessed). One failed `x.opportunity` answer skips only that post. Job results report `model_calls`, `salvaged`, `unscreened`, `advice_failed`.
- **Snapshots** (`analytics/snapshots.py`, table `growth_snapshots`, migration 0009): `x_account` rows from our own user object in X responses (connection test, own-post pages) or one `users/me` lookup when the latest is older than 6 h (taken before the budget-heavy searches); `telegram_chat` rows (members via Bot API `getChatMemberCount` or the account's dialog list, plus messages collected) from job `growth.snapshots` every 6 h. Identical values are stored at most hourly.
- **Dashboard** (`analytics/growth.py`, `GET /api/analytics/growth?period=24h|7d|30d|all`): pure reads of local data. Previous-period comparisons only where collected data covers them; unavailable metrics are `null` with a note; dry runs never count as actions.

# YouTube integration and website content

- **Content items** (`models/content_items.py`, migration 0010): platform-independent published content (`platform`, `external_id` unique per platform, `content_type`, title, description, thumbnails, url, duration, `published_at`, privacy/availability, `website_visible` + `visibility_source`, `featured`, metrics, `distribution` hooks). `content_item_metrics` holds view/like/comment snapshots (hourly for videos < 48 h old, then every 6 h). Future platforms (X, TikTok, articles) add rows, not tables.
- **YouTube** (`youtube/client.py`, `oauth.py`, `service.py`; routes `/api/youtube/*`): official Data API v3 list endpoints only (channels, playlistItems, videos: 1 unit each). Auth: API key (secret `youtube_api_key`) or OAuth authorization code + PKCE with `youtube.readonly` (client id/secret and refresh token are DPAPI secrets; access tokens live in memory). The channel is chosen explicitly; switching channels hides the old channel's items. Sync (job `youtube.sync`, `JOB_YOUTUBE_SYNC_SECONDS`, default 900): uploads playlist (first sync up to 10 pages, then 1 page) → `videos.list` for new ids plus re-checks (last 30 days or on the website) → upsert; ids that disappear become unavailable and leave the website. Network first, then one write.
- **Short rule** (`service.classify`): ≤ 180 s and embed height ≥ width (videos.list `maxHeight=8192` makes the API report the aspect ratio); fallback ≤ 60 s, or 61–180 s with `#shorts`. Manual override sets `type_source=manual`.
- **Website** (`api/routes/website.py`): public `GET /api/website/latest?limit=3` and `/api/website/content` read stored items only (public fields, no metrics); `/api/website/status` and `/api/content-items` are for the dashboard. The site (`website/Website.tsx`) fetches them, caches the last answer in the browser, falls back to the next thumbnail size, and never calls YouTube.
- **Distribution hooks:** new items get `distribution = {x_post, telegram_announcement, tiktok_caption: "not_started"}` and an activity entry; nothing is generated or posted in this phase.

