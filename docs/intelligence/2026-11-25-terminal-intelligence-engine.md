# Terminal Intelligence Engine: audit, architecture and roadmap (proposal, 2026-11-25)

Status: **proposal only.** Nothing is built, bought or deployed. The owner approves each phase.
Code references are to `release/kiwi-hardening` 058e148 (production, Railway f4642e55).

## 1. Why now: what the keyword gates cost us

**Trending** published once in three days of retained logs: post 1872. In that time, 716 publish cycles found nothing queued.

The stories it held back were real news:

| Peak score | Story | Why it was held |
|---|---|---|
| 90.8 | Capita boss exits, shares crash 57% | "why should anyone care? no concrete consequence" |
| 86.5 | AI rally set to add $2 trillion to Nasdaq-100 | same |
| 85.2 | CrowdStrike $43.9B acquisition | "a major incident not yet verified" (an acquisition is read as an incident) |
| 83.7 | Bitcoin regains $61,000 | "no concrete consequence" |
| 75.7 | Anthropic breaks below its $83 IPO price | "no concrete consequence", then expired as stale |
| 68.0 | Sanofi to buy Insmed for $41 billion | "no concrete consequence" |
| 86.4, then repeated | Nvidia / Jensen Huang return | crossed the line, was replaced by a fresh tweet, expired, and repeated |
| 69.6 | Microsoft "Code Red" | "covered by the News Desk (event, candidate)" (the cross-lane hold still applies) |

**Root cause:** `newsdesk/newsworthy.py` `_consequence()` (:400-480). It recognises a consequence only from regex families:
- deaths;
- a % move in an index, crypto, oil or one of 17 mega-caps;
- money stolen or liquidated;
- war terms;
- central-bank rates;
- a fixed `CORP_HARD` verb list.

It cannot recognise:
- takeovers of any size;
- a crash in a non-mega-cap listed company;
- an IPO-price break;
- "CEO quits" (the list has "resigns" and "steps down", but not "quits" or "exits");
- a level reclaimed.

Both lanes share this gate. Every editorial judgement in production is regex or lexicon: importance, newsworthy, editorial, quality, chaos, virality, urgency, incidents, the claim gate, trending/rank and terminal/intel. No model is used.

## 2. Infrastructure audit (what exists)

### Models and providers
- **Only one provider is registered: Ollama** (`providers/factory.py:33-39`), for chat and embeddings.
- **Production has no Ollama.**
  - Railway has no `OLLAMA_BASE_URL`, so it calls 127.0.0.1:11434 inside its own container.
  - That is why x_ingest logs "X posts stored without embeddings".
  - Every router task (24 tasks in `ai/routing.py:44-71`) and every vector feature is in practice off in production: community, x.*, memory and `topic.merge_judge`.
  - The PC's Ollama works. It was restarted 2026-11-24 21:19 UTC on the RTX 5090 (32 GB). Models on the PC: nomic-embed-text, qwen3.6 27b/35b, qwen3.8 27b, gemma4 26b/31b, gpt-oss 20b, qwen2.5, llama3.1 8b.
- **Anthropic is called directly over HTTP in six places**, each with its own copy of the httpx headers and no shared client:
  - newsdesk writer and network copy (`writer.py:102`, `network.py:427`);
  - the Telegram composer;
  - the Trending writer;
  - Terminal hooks;
  - vision and chief (both off or shadow);
  - portfolio condition.
- **Production writers:** Trending runs `writer=rules`. Railway has no `ANTHROPIC_API_KEY` variable. A key may still be saved in Admin (`credentials.resolve`); the owner should confirm.
- **Spend tracking:**
  - Only committee (`cm_usage`, $2/day) and chief ($1/day, off) have caps.
  - Two price tables disagree (`committee/llm.py:50`, `newsdesk/chief.py:47`). Neither knows the 5-5 model prices.
  - Newsdesk, Trending and Terminal calls are not counted at all.

### PC worker (reusable as is)
- **Connection:** the PC long-polls out to Railway. There are no inbound ports.
- **Auth:** a `pcw_` bearer token, stored only as a SHA-256 hash (`brand_manager/queue.py:31-60`).
- **Job protocol:** leased jobs with claim, result and fail (`api/routes/newsdesk_worker.py:36-86`). A render lease is 6 minutes.
- **Already-proven fallback:** when the worker is away, News Desk images fall back to the article preview or the branded card.

### Vectors and memory
- **Storage and search:** float32 L2-normalised BLOBs in SQLite, searched by brute-force numpy (`memory/embeddings.py:158`). Tables: `memory_embeddings`, `telegram_message_vectors`, `social_post_vectors`.
- **News and Trending have no vectors:**
  - Identity checks are lexical: `checks.same_event`, `rank.related` and `cluster`, `newsworthy.same_event`, `incidents.same_incident`, `quality.same_story`.
  - Story memory (`NdStoryline`) runs in shadow and is rule-based.
- **No candle table.** Prices are fetched live from Yahoo, Hyperliquid xyz, Binance, OKX and Bybit.
- **Useful history:**
  - News Desk tables: `nd_events` (evidence, score parts, facts, why), `nd_validation` (decisions), `nd_posts`, `nd_feedback`.
  - Trending tables: `tt_topics` and `tt_signals`.
  - Trending latency and incident ledgers are capped JSON in `tt_settings.state`, not tables.

### Verification concepts already in code (keep them)
- **Evidence levels:** official, two outlets, price data, attributed (`chaos.py:5-12`). There is also `facts.two_sources` and the claim gate (`checks.claim_problems`), which refuses figures, names or quotes the sources don't contain.
- **Separation of what a story says:** `importance.Structure` splits main, context, stance and claimed. `chaos.RUMOUR` and `OPINION`, and the rumour watch, cover hedged reports. `NdStoryline` keeps `lead_at` and `confirmed_at` apart.
- **Cross-asset figures:** `moves.cross_asset` reports them, and `moves.cross_problems` forbids causal wording.

## 3. Volume and deduplication

**Measured on 2026-11-24 (24 scans, 2 hours):**
- news: 70 new items;
- Google Trends: 455 new items;
- X accounts: 3;
- HN: 2.

That is about **6,000 new items a day, roughly 85% of them Google Trends queries.** About 70 News Desk candidates a day reach the editorial log.

Dedup funnel, each stage cheaper than the next. The stage 0 and 1 rates are estimates, to be measured in Phase 1.

| Stage | What | Where | Cost | Expected survivors from 6,000 |
|---|---|---|---|---|
| 0. Exact | Canonical URL, normalised title hash, (source, guid), repeated Trends query within 6 h | Railway, SQL | free | ~2,500 |
| 1. Near-duplicate | Embedding (nomic-embed-text v1.5, 256-dim Matryoshka). Cosine ≥ 0.90 to an item in the last 48 h = same item; otherwise attach to the nearest open cluster at ≥ 0.80 | Railway CPU or PC GPU, same model | free | ~2,500 embedded |
| 2. Cluster change | Only a new cluster, or an update that adds something (a new outlet, a new figure, an official statement, a price crossing), goes to analysis | rules | free | ~1,500 item analyses |
| 3. Item analysis | Structured extraction: event type, entities, stated consequence, claims with their source, hedges | local LLM or Haiku 5.5 | cheap | ~150 clusters pass the pre-score |
| 4. Cluster synthesis | Narrative, materiality, novelty against memory, cross-asset and catalyst context | local LLM or Sonnet 5.5 | moderate | ~15 MAJOR candidates |
| 5. Adjudication | Breaking, MAJOR only: a second-opinion check of the publishable claims | Opus 5.5 | highest | |

Stages 0-2 remove about three quarters of the paid work. The bigger lever is that stages 4 and 5 run only for clusters that already pass a pre-score.

## 4. Architecture

```
 sources (RSS, Google, X, status feeds, prices)
        │
  [0] canonicalise + exact dedup ─────────── Railway (always on)
        │
  [1] embed (nomic v1.5) ── PC GPU when online, Railway CPU otherwise (same model, so the same vector space)
        │
  [2] cluster store (nd_clusters, 30-day hot window; sqlite-vec or numpy)
        │  only new or changed clusters
  [3] item analysis ─┐
  [4] synthesis ─────┼─ AI router: PC worker job (leased) → on timeout or offline → Claude API (capped)
  [5] adjudication ──┘                                      → over cap → today's rules (never silent)
        │
  intel.assess(cluster)  ← ONE shared judgement used by News Desk, Trending and Terminal
        │
  deterministic gates (evidence, claim gate, cross_problems, liq pause, caps) → publish / hold / reject
```

### 4.1 Hybrid AI, and production that runs with the PC off

The PC never receives a connection.
- It keeps the existing outbound long-poll, with new job types `intel.embed` and `intel.analyze`. Each job type gets its own scoped `pcw_` token, stored hashed.
- Results are schema-validated and HMAC-signed with the job's nonce.
- The PC proposes labels and extractions, but cannot publish anything: every publish decision stays in Railway's deterministic gates.
- Ollama stays bound to 127.0.0.1. **No tunnel, no open port.**

How work moves to the cloud:
- **Heartbeat:** the worker heartbeats every 30 s.
- **Routing:** a job is offered to the PC only while its heartbeat is fresh.
- **Timeouts:** if the PC hasn't claimed the job in 20 s (breaking) or 3 min (slot), or a lease expires, Railway runs it itself.
- **Breaking stories are cloud-first by default.** A local answer arriving later is kept as a shadow comparison.
- **Embeddings:** Railway runs nomic-embed-text v1.5 on CPU through ONNX, the same weights as the PC, so vectors are interchangeable.
  - About 2,500 short texts a day is light CPU work; the GPU is not needed at this volume.
  - The GPU's role is bulk backfill (re-embedding months of history) and local LLM analysis.
  - This also fixes the "stored without embeddings" gap in production without a public Ollama.
- **When everything is down** (PC offline, cloud budget reached, API error), the lanes fall back to today's rule gates, and Admin says so. Production never stops because the PC is off.

### 4.2 Facts, correlations and speculation

Every assertion the engine handles is a `Claim` with an epistemic class.

**The model may extract and label claims. Only deterministic code assigns the class, from the sources:**

| Class | Rule (computed, not model-judged) | May appear in an automatic post |
|---|---|---|
| VERIFIED | Official source, or ≥2 independent outlets stating it, or our own price data | yes, plainly |
| REPORTED | One outlet, attributed | yes, always attributed ("…, Reuters reports") |
| CORRELATION | Measured co-movement (`moves.cross_asset`), historical analogue | figures only, "in the same hour"; `cross_problems` refuses causal wording |
| SPECULATION | Hedged, forecast, opinion, rumour watch, unnamed sources | never in automatic posts; Terminal shows it labelled |
| INFERENCE | Anything the model concluded itself (causes, "likely" impact) | never published; Admin and Terminal only, labelled "model view" |

- The claim gate (`checks.claim_problems`) runs on every model output, as it does on copy today.
- A model output that turns a CORRELATION into a cause is rejected, with the existing reason.

### 4.3 The shared semantic gate (replacing the Trending/News Desk keyword gate)

`intel.assess(cluster)` returns:
- the event type;
- the entities;
- the **stated** consequence (kind, magnitude, the source that states it);
- novelty against memory;
- audience materiality (crypto / markets / world);
- claims with their classes.

How it is introduced:
- Both lanes call it, which ends the cross-lane hold deadlock, because both read the same decision.
- The regex rules stay as **hard vetoes** (safety, hedges, liquidation pause, caps) and as the fallback.
- The model replaces only the "is there a consequence" judgement, the part that rejected Capita, Sanofi and the Anthropic IPO break.
- Rollout: shadow first, logging every disagreement with the current gate for the owner's review, then switch one lane at a time.

A cheap interim, if the owner wants Trending working sooner, is a rule patch to `_consequence()`: takeovers ≥ $10B, a listed company moving ≥ 20%, a CEO exit together with a move, IPO-price breaks. It is not proposed as the long-term fix.

### 4.4 The six capabilities

| Capability | Built from |
|---|---|
| Semantic analysis | Stage 3 extraction with a strict JSON schema (existing `ai/structured.py`) |
| Event clustering | Stage 1-2 vectors combined with the existing lexical vetoes (place, price level, name, figure), so two different numbers never merge |
| Emerging narratives | Clusters of clusters over 72 h (centroid drift, outlet growth, cross-lane signals). Terminal-only until proven |
| Cross-asset intelligence | `moves.cross_asset`, plus a stored reaction record per cluster (1 h / 24 h moves of related markets). Shown as CORRELATION only |
| Catalyst analysis | Scheduled catalysts (central banks, CPI, earnings, unlocks), matched to clusters, with past reactions to similar catalysts. Historical CORRELATION, never a forecast |
| Historical memory | `nd_clusters` + `nd_claims` + a new daily-closes table (also needed by milestones), 30-day hot vectors, everything else archived in the nightly backup |

## 5. Local versus cloud models

The latency and accuracy figures here are **expectations to be measured in Phase 0**, not results.

| | Local on RTX 5090 (qwen3.6 27b, gemma4 31b, gpt-oss 20b; nomic embeddings) | Claude API (Haiku 5.5 / Sonnet 5.5 / Opus 5.5) |
|---|---|---|
| Price | $0 per call; electricity ≈ 0.4-0.6 kW while busy (≈ 1-2 kWh a day at this volume) | Haiku $0.10/$0.50, Sonnet $2/$10, Opus $4/$20 per million input/output tokens; Batch −50% for non-urgent work; cached prompt reads much cheaper |
| Availability | Only while the PC is on and the worker is running | Always (rate limits, provider outages) |
| Expected latency | Embedding: thousands a second. 27-31B model: ~3-10 s per extraction, queued one at a time behind image renders | Haiku ~1-3 s, Sonnet ~5-15 s, Opus with thinking 15-60 s |
| Expected accuracy | Good at extraction and labelling; weaker at subtle materiality and long-context synthesis | Stronger on judgement and synthesis; Opus as the last check |
| Privacy | Nothing leaves the PC | Headlines and snippets are sent to Anthropic (public news, so low sensitivity) |
| Contention | Shares the GPU with FLUX/Qwen images and Wan video | none |

Recommended split, to be confirmed by the eval:
- Embeddings: the same model everywhere.
- Stage 3: local first, with Haiku as fallback.
- Stage 4: Sonnet for breaking and major clusters; local first for slot clusters.
- Stage 5: Opus, MAJOR only.

## 6. Monthly costs (30 days, cloud list prices, no batch or cache discount)

Assumptions per call:
- Stage 3: 700 tokens in, 200 out.
- Stage 4: 4,000 in, 700 out.
- Stage 5: 8,000 in, 1,200 out.

Funnel shares of raw items: 25% reach stage 3, 2.5% stage 4, 0.25% stage 5.

| Raw items/day | All cloud | Hybrid, PC online (stage 3 + 70% of stage 4 local) | PC offline all month (= all cloud) | No dedup, Sonnet on every item (for comparison) |
|---|---|---|---|---|
| 2,000 | ~$34 | ~$15 | ~$34 | ~$300 |
| **6,000 (today)** | **~$100** | **~$46** | **~$100** | **~$900** |
| 15,000 | ~$250 | ~$115 | ~$250 | ~$2,250 |
| 30,000 | ~$500 | ~$230 | ~$500 | ~$4,500 |

- Embeddings add nothing: local or Railway CPU. The Railway CPU fallback needs about 0.3-0.6 GB more RAM on the API service; check against the Railway bill.
- The PC's electricity is extra, at the owner's tariff.
- Batch pricing on the non-urgent work (daily memory consolidation, backfills, narrative reviews) cuts those parts in half.
- **A hard daily cap in one shared AI ledger** (proposed default $3/day, about $90 a month, owner-set):
  - Past the cap: stage 4 goes local-only, stage 5 is skipped, and if needed the lanes return to rules.
  - Admin shows when it happens.
  - This follows the committee budget pattern that already exists.
- The Phase 0 bake-off itself would cost roughly $5-15 of API calls. It needs the owner's approval, and a key set in Railway or Admin.

## 7. Roadmap (each phase ships only with the owner's approval; shadow before any publishing change)

| Phase | Scope | Production effect |
|---|---|---|
| **0. Measure** (~2-3 days) | Labelled eval from real data: Trending 11-22..11-25 (the table above), `tests/fixtures/nd_replay_7d.json`, the 11-23 misses (Nvidia +31%, Nasdaq +6.5%), known false positives ("Russia warns", Amazon drone "strikes"). Score the regex gate vs local qwen3.6/gemma4 vs Haiku vs Sonnet on newsworthiness (precision/recall), claim classes, cluster purity and latency p50/p95. Also: one Anthropic client and provider, one price table, one AI spend ledger | none |
| **1. Embeddings + dedup** | Stages 0-2; `nd_clusters`; Railway CPU nomic + PC `intel.embed` job; fixes production embeddings for X ingest too | shadow: clusters logged next to today's lexical identity |
| **2. Shared semantic gate** | `intel.assess` + `Claim` classes, called by both lanes in shadow; daily disagreement report in Admin | shadow |
| **3. Hybrid routing** | Worker heartbeat, scoped token, HMAC results, lease/timeout fallback to cloud, cap → rules | shadow, then live for analysis only |
| **4. Switch lanes** | Trending first (largest gap), then News Desk slots, then breaking. Each needs the eval threshold met and the owner's go | live, per lane, with a rollback switch |
| **5. Terminal intelligence** | Narratives, cross-asset reaction records, catalyst calendar, historical analogues. Admin/Terminal only, every item labelled with its class | display only |

## 8. Owner decisions needed
1. **API key and spend cap:** is a Claude key saved in Admin, and what daily cap? There is no Railway `ANTHROPIC_API_KEY` today.
2. **Phase 0 bake-off:** approve about $5-15 of API calls.
3. **PC hours:** should it be treated as always-on, or best-effort? This only changes the cost mix, not production's ability to run.
4. **Interim fix:** apply the interim Trending rule patch (§4.3) now, or wait for Phase 2?
5. **PR #16 and #17 merges:** still pending on GitHub.
