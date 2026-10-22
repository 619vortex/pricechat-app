# Short Market — money stories, filmed by your AI

Dashboard › Social media › **Short Market** (`/admin/short-market`).

It finds a real story, writes it as a short story, voices it and animates it in 3D, all on the owner's own machine.
There is no sign-in to any network, no API key and no posting: the finished video is **downloaded** and uploaded to
TikTok by hand. (Brand Manager is the one that talks to TikTok; Short Market never does.)

## 1. How one short is made

```
Dashboard ──► Backend API (light work only: finds stories, checks scripts, stores the MP4)
                 ▲  outbound HTTPS only, the same worker token Brand Manager uses
             AI worker (this PC / DGX Spark):  story (local LLM) · voice (Kokoro) · 3D scenes (Wan 2.2) · FFmpeg
```

| Stage | Where | What |
|---|---|---|
| Find the story | backend | story ideas from the sources below, each with its facts, numbers and sources (the brief) |
| Write the story | worker | local LLM (Ollama, gemma3:27b) → Anthropic only if its key is on the worker → facts-only fallback. One scene per ~4 s: the words spoken, 2-4 words shown big, and what the 3D shot shows. A made-up cartoon guide walks through the film. |
| (check) | backend | Brand Manager's script checks (every number must come from the brief, no predictions/advice/hype, sources cited, length) plus the story's shape (4-12 scenes, each short enough for one picture, each with a picture). The worker rewrites with the feedback (3 tries), then uses the facts-only story. |
| Record the voice | worker | Kokoro (local neural voice, 6 voices) → Piper → system voice. One recording per scene, so pictures and captions line up. |
| Animate 3D scenes | worker | Wan 2.2 TI2V-5B (text → video) draws one clip per scene, exactly as long as its words, in the chosen 3D look. The writing model is unloaded first so both fit the GPU. A scene the model can't draw becomes a still. |
| Edit and caption | worker | FFmpeg: clips cut together at 1080×1920 / 30 fps, the scene's words big at the top, word-by-word captions, the voice. H.264/AAC MP4. |
| Final check | backend | MP4, 1080×1920, has a voice track and captions, length, checksum. A failed check is made again (3 attempts). |

The short is its own job (`sm_shorts`: lease, attempts, retry with back-off), so a crashed worker or a restarted server
never loses one. **Make it again** gives the same story a new script, voice take and scenes (optionally another look,
voice, length or picture quality); the old video is replaced when the new one is ready.

## 2. Where stories come from (no keys, no sign-in)

| Topic | Source |
|---|---|
| Market moves | CoinGecko: big 24-hour movers, the whole market's size and move, Bitcoin's share, coins trending in search (top 300 only); the Crypto Fear & Greed Index (alternative.me) |
| Money news | RSS headlines + summaries, linked and attributed, never copied in full: CoinDesk, Cointelegraph, Decrypt, CNBC, MarketWatch, Federal Reserve press releases |
| Today in money history | Wikipedia "on this day" events about money (banks, markets, currencies, crashes…) |
| Money facts | the hand-checked fact bank (`brand_manager/defaults.py`), three facts per story |
| Collectibles | Price Chat's own Pokémon market: validated 30-day movers and verified sales |
| From X and the community | posts the X connector already collected (100+ likes, last 48 h), reported as "according to a post by @…" |

A story needs at least two facts with a source; a bare headline is never a story. A source that is down is skipped.
A topic with nothing real to tell shows nothing (some days have no money history). The list refreshes itself when it
is older than 30 minutes; stories already made sink to the bottom.

## 3. Choices

The page is one button, **Make a video**: it takes today's best unused story, gives it the next look in turn and a
length that fits its facts (10 to 50 seconds). The choices below are folded under Options and are remembered.

- **Look**: 3D Cartoon · Claymation · Neon City · Black and Gold · Toy Bricks · Paper Diorama (`short_market/defaults.py`).
- **Voice**: six Kokoro voices (American and British). **Length**: 10 to 50 seconds.
- **Picture**: Fast (480p scenes) · Standard (576p) · Best (720p), always delivered at 1080×1920. On an RTX 5090 a
  30-second short takes about 9 / 16 / 28 minutes (9 and 17 measured; Best estimated).

## 4. Rules it keeps

- Only the brief's facts: no invented numbers, names or quotes; no predictions or advice. Market and news captions
  end with "Not financial advice."
- Pictures never show real people, logos or written words (asked of the writer, and refused in the video model's prompt).
- The caption does not hide that it is AI: the detail page reminds the owner to switch on TikTok's
  **AI-generated content** label when uploading.
- Nothing is posted, scheduled or sent anywhere. The API is behind the admin login; only `/api/short-market/worker/*`
  is reachable without it, and only with a registered worker's token.

## 5. Worker setup (once per machine)

The worker itself stays light. The video model and the voice run in a second Python environment with PyTorch:

```powershell
cd worker
py -3.12 -m venv .venv-gpu          # Python 3.12: on Windows, Smart App Control blocks PyTorch's 3.14 build
.venv-gpu\Scripts\python -m pip install --prefer-binary torch==2.11.0 --index-url https://download.pytorch.org/whl/cu128
.venv-gpu\Scripts\python -m pip install --prefer-binary -r requirements-gpu.txt
```

Models (kept under `worker/models/`, or point the settings at them in `worker.env`):

| What | Setting | Files |
|---|---|---|
| Video | `PCW_VIDEO_MODEL` (default `models/Wan2.2-TI2V-5B-Diffusers`) | `Wan-AI/Wan2.2-TI2V-5B-Diffusers` from Hugging Face, ~34 GB, Apache-2.0 |
| Voice | `KOKORO_MODEL`, `KOKORO_VOICES` (default `models/kokoro/`) | `kokoro-v1.0.onnx` + `voices-v1.0.bin` from the kokoro-onnx releases, ~350 MB, Apache-2.0 |
| GPU Python | `PCW_GPU_PYTHON` (default `.venv-gpu`) | |

Without the GPU environment or the video model the worker still finishes shorts, with still scenes, and the page says
so ("No video model: still scenes"). The video model peaks at about 14 GB of GPU memory.

## 6. Data and API

Tables `sm_ideas`, `sm_shorts` (migration 0047). Videos and thumbnails live in object storage under
`brand-manager/short-market/`, never in the database.

`GET /overview` · `GET /ideas?category=` · `POST /ideas/refresh` · `POST /shorts` (`idea_id` or the best fresh story) ·
`GET /shorts` · `GET /shorts/{id}` · `POST /shorts/{id}/remake` · `DELETE /shorts/{id}` ·
`GET /shorts/{id}/media/video?download=1`; workers: `POST /worker/claim`, `/worker/jobs/{id}/progress|script|result|fail`
(the same protocol as Brand Manager's).

## 7. Not included

Music (no licensed track is bundled), posting or scheduling, analytics, and the owner typing a free topic (a story
needs sourced facts; nothing is made up).
