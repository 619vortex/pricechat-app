# Brand Manager — the AI social media manager (Phase 1: TikTok)

Dashboard › Social media › **Brand Manager** (`/admin/brand-manager`). Tabs: Overview · TikTok · Content Studio ·
Calendar · Analytics · Settings. X, Instagram, Facebook and YouTube are listed as "soon"; only TikTok works in Phase 1.

The main screen is one button. **START AUTO** checks everything, then the system researches, writes, voices, renders,
schedules and sends videos to TikTok on its own, measures them and adjusts. **STOP AUTO** starts nothing new; videos
already rendering finish. **EMERGENCY STOP** (Settings › Safety) cancels every queued job and holds every finished video.

## 1. Architecture

```
Dashboard ──► Backend API (Railway, light work only)
                 │  scheduler "brand.tick" every minute: plan slots · discover + research topics · publish · poll TikTok · analytics
                 ▼
             Persistent job queue (bm_jobs: leases, retries, idempotency keys)
                 ▲  outbound HTTPS only, worker token
             AI worker (DGX Spark; optional cloud fallback): script (local LLM) · voice · visuals · FFmpeg · captions · QC
                 │  uploads the finished MP4 + thumbnail
                 ▼
             Object storage (the private S3 bucket, prefix brand-manager/)  ──►  TikTok pulls the video from a signed URL
```

- No AI inference and no rendering in the web API process (the backend streams uploads to the bucket, never into memory).
- The Spark exposes no endpoint: it claims jobs over HTTPS with a token whose hash is stored. Offline Spark = jobs wait.
- Survives restarts: every stage, job, lease and schedule is in the database. A worker that dies loses its lease; the job
  returns to the queue (3 attempts, exponential backoff). One render job per video generation (idempotency key).
- Never silent: anything that blocks AUTO is shown with its exact reason (status BLOCKED/ERROR) and logged.

## 2. The pipeline (each stage visible per video)

| Stage | Where | What |
|---|---|---|
| Discover topic | backend | crypto news RSS (CoinDesk, Cointelegraph, Decrypt), CoinGecko market data, Price Chat's Pokémon market (validated movers, verified sales), a hand-checked fact bank, Price Chat's own YouTube videos. No TikTok trend scraping (no authorized API), no Google Trends (no official API). |
| Research | backend | the brief: facts, numbers, images, sources; a 7-day CoinGecko chart for market videos |
| Write script | worker | local LLM (Ollama, gemma3:27b) → Anthropic (if set) → facts-only template. The prompt asks for a ≤12-word hook that leads with the surprising fact, one idea per beat, 2-5 words on screen, a comment question at the end. Before the backend sees it, the worker sends back weak craft (long hook, filler phrases, long screen text) and a second AI pass fact-checks every sentence against the brief (4 AI tries, then the template). |
| (check) | backend | every number must come from the brief (digits and spelled-out numbers, honest rounding allowed); no claims the brief doesn't make (rare, record, most expensive, one of only a few…); no predictions/advice/hype; banned topics; sources cited; length. The worker rewrites with the feedback. |
| Generate voice | worker | Kokoro (local neural, the narrator + speed from Settings › Content) → OpenAI → Piper → system voice (robotic, last resort); numbers rewritten for speech ($150,000 → 150,000 dollars), silences trimmed, compressed and loudness-normalized; per segment, so scenes and captions line up |
| Create visuals | worker | Pillow scenes per template, 1080×1920, kept clear of TikTok's on-screen UI: the hook shows the card/thumbnail big or the hook words huge; numbers in the accent colour; fact scenes keep the card or a mini chart in view |
| Assemble + subtitles | worker | FFmpeg: slow zoom + fades, word-by-word karaoke captions that pop in, a progress bar along the top, narration (+ licensed music if configured), H.264/AAC 30 fps |
| Quality check | worker + backend | 1080×1920, length in range, narration present, captions present, MP4, size, checksum |
| Hashtags | backend | app/brand_manager/hashtags.py: always-used (brand) → this story's own (coin, card, PSA grade) → one from the writer → the category's, cleaned, no hype tags, ≤ max (5) |
| Schedule | backend | the brand's posting times in its time zone; approval mode holds videos for review |
| Publish | backend | TikTok, authorized flow only (below) |
| Collect analytics | backend | every 6 h from TikTok's API |
| Improve | backend | the analyzer adjusts topic ranking and shows the script writer which hooks worked |

Templates: A Breaking News · B Crypto Price Alert · C Pokémon Card Spotlight · D Top 5 Facts · E YouTube Highlight
(colour + banner editable in Settings › Content).

## 3. TikTok: what the rules allow (researched 2026-10-11)

Sources: developers.tiktok.com — Content Posting API (direct post, upload), Content Sharing Guidelines, Login Kit.

- **OAuth v2** (Login Kit). Scopes requested: `user.info.basic, user.info.profile, user.info.stats, video.list,
  video.upload, video.publish`. Tokens are encrypted in the secret store (never in the database), refreshed
  automatically, revoked on disconnect; an expired/revoked sign-in blocks AUTO with "Reconnect TikTok".
- **Unaudited apps**: direct posts are limited to private (SELF_ONLY) viewing; public posting needs TikTok's audit.
- **Consent + creator controls**: TikTok requires the creator to choose privacy (no default), interactions (off by
  default, greyed when disabled), commercial content disclosure, and to accept the Music Usage Confirmation before an
  upload. So:
  - **Drafts mode (default, fully automatic)**: the owner consents once (checkbox at START AUTO); each finished video is
    sent with the inbox upload (`video.upload`) and the creator posts it from the TikTok app.
  - **Direct mode**: each video waits in Content Studio; **Approve** opens TikTok's posting screen (creator nickname,
    privacy dropdown from `creator_info`, interactions, disclosure, declarations, preview, editable title). Approved
    videos post at their slot; fresh `creator_info` is queried before each post.
- **No watermarks**: TikTok's guidelines forbid apps from superimposing brand names/logos/watermarks on shared content.
  Branding comes from colours and the spoken/on-screen call to action; the logo watermark is a setting, off by default.
- **Video source**: `PULL_FROM_URL` from `https://api.pricechat.live/api/brand-manager/media/…` (signed, expiring).
  This URL prefix must be verified in the TikTok app. (FILE_UPLOAD is meant for files on the user's device.)
- **Caps**: TikTok limits posts per creator (~15/day across apps); Brand Manager stays at ≤10/day.
- **Honest status**: "Sent to your drafts" ≠ published. A video is "published" only when TikTok's status endpoint says
  PUBLISH_COMPLETE; private posts are labelled private.

## 4. Setup

1. TikTok developer app (developers.tiktok.com): Login Kit + Content Posting API (+ Display API). Redirect URI
   `https://pricechat.live/api/brand-manager/tiktok/callback`. Verify the URL prefix above. Paste the Client key/secret
   on Settings › API keys.
2. Brand Manager › TikTok › Connect TikTok.
3. Settings › AI › Add worker → run the shown command on the DGX Spark (`worker/README.md`).
4. Overview › tick the upload consent › START AUTO.

## 5. Settings

General (brand profile, language, time zone) · Content (categories, videos/day, length, voice, hashtags, templates,
music) · Publishing (posting times, drafts/direct, approval, audited, AI label, watermark) · AI (script engine, local
model, quality, cloud cost limit, workers) · Safety (rules, banned topics, sources, duplicate window, risk, emergency stop).

## 6. Data

Tables `bm_brands`, `bm_automation`, `bm_accounts`, `bm_videos`, `bm_jobs`, `bm_workers`, `bm_events`, `bm_metrics`
(migration 0046). Videos and thumbnails live in object storage, never in the database.

## 7. Not in Phase 1

Other networks; generative video models; trend data from TikTok (no authorized API); comment replies.
