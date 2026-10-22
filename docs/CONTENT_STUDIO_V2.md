# Content Studio v2: creative production

Brand Manager's discovery, research, fact checks, scheduling and TikTok drafts are unchanged. v2 replaces how a video is
*made* on the worker: script → voice → captions → visuals → sound → quality check.

## Audit of v1 (what the Mewtwo PSA 10 video was made with)

| Stage | v1 | Bottleneck found |
|---|---|---|
| Script | Ollama `gemma3:27b`, one prompt, 4 fixed scene slots | generic hooks ("$17,225 for a Pokémon card?"), no structure or tone |
| Voice | Kokoro `am_michael` per segment | Whisper heard "Paceman" (Pokémon), "Mutewo" (Mewtwo), "P.S. of 10": the v1 rule spelled acronyms out ("P S A") |
| Visuals | Pillow: 4 still PNGs + FFmpeg zoompan | static slides, small card, half-empty frames |
| Captions | ASS karaoke, word times guessed from word length | drift; lone words ("2026.") |
| Audio | voice only (music only with an owner-supplied URL) | no sound design |
| QC | size / length / audio present | nothing about content, layout or sync |

## v2 pipeline (worker/pricechat_worker)

| Module | What it does |
|---|---|
| `creative.py` | styles, narrators, tones, intensity, music, captions, presets; `resolve()` = story defaults < preset < owner choices |
| `script2.py` | 5-beat structure (hook, context, development, payoff, ending), tone, a visual per segment from the visuals the brief supports, emphasis words, `[beat]` pauses; a *hook workshop* (5 angles, clichés and unsupported numbers filtered in code, model picks); claim filter (`CLAIMS`: popular, investment, perfect, sought-after…) that blocks approval |
| `voice2.py` | narrator = Kokoro voice + speed + pause profile; pronunciation table (+ the brand's own); one master pass (-16 LUFS); cached by text + narrator |
| `align_asr.py` / `timing.py` | Whisper small.en word timestamps, per segment; aligned to the script's written words; balanced caption phrases |
| `storyboard.py` | scenes with data read from the brief only; transitions by intensity (never twice in a row); cuts snapped to the music beat; sound-effect plan; `unsupported()` re-checks every number on screen |
| `motion.py` + `worker/motion/` | Remotion 4 (React): 1080×1920 30 fps H.264/AAC |
| `qc2.py` | render, narration-vs-script, caption sync, numbers on screen, card image, blank scenes, frame edges, pacing, branding, loudness |
| `pipeline2.py` | the whole run; `mode: rerender` reuses the approved script |

The v1 pipeline stays: used when the brand's renderer is "classic", or the worker has no Node/Remotion or no Kokoro.

## Renderer (worker/motion)

`npm install` once, then the worker calls `node render.mjs props.json public/ out.mp4`. Remotion downloads its own
headless Chrome on first use. Scenes: price reveal (counter + rays + card), 3D card entrance (holo sweep, parallax),
card close-up + grade badge, sale record (our own info card, never a fake screenshot), verified chart, headline, big
number, kinetic punchline, countdown, Kiwi end card. Fonts: Anton + Inter (SIL OFL, `static/fonts`).
**Licence note:** Remotion is free for individuals and companies up to 3 people; larger companies need a Remotion
company licence.

Music and sound effects are synthesized by `motion/make_audio.py` (numpy, fixed seeds): original, no third-party audio.
Music is ducked under the voice frame by frame.

## Content Studio (dashboard)

Video page → **Creative**: preset, style, narrator, tone, animation, music, captions, renderer, duration; per-scene
visual + on-screen words; **Regenerate scene / voice / captions / Final render** (`POST /videos/{id}/rerender`, same
script, no new research); **New script** (= the old Regenerate). The last render's quality checks are listed with
render time and cost. Scene words with a number not in the research are refused (400).

API: `GET /brand-manager/creative/options`, `PUT /brand-manager/videos/{id}/creative`, `POST /brand-manager/videos/{id}/rerender`.
Brand defaults: `strategy.creative`. Per video: `bm_videos.creative` (migration 0070).

## Benchmark

`worker/bench_v2.py` renders one verified story with each preset offline (scripts go through the backend's own
`safety.check`). Outputs in `worker/out/bench/`.

## Voice providers

Kokoro (local, $0) stays: the pronunciation fixes were verified with Whisper. Cloud voices (OpenAI `gpt-4o-mini-tts`,
ElevenLabs) were not tested: no API key on this worker. They are only worth trying side by side with the same script.

## Script model (2026-10-19 bake-off, `worker/model_bakeoff.py`)

Same 4 stories (Mewtwo documentary / viral / funny, a Bitcoin move) through the real writing loop and checks.

| Model | AI scripts approved | Avg drafts | Notes |
|---|---|---|---|
| **gemma4:31b** (chosen) | 4/4 | 1.5 | accurate, some wit; ~6-12 s a script once loaded |
| qwen3.8:27b | 4/4 | 2.5 | fastest and punchiest, but invented claims ("most people ignore the villain cards") |
| qwen3.6:27b | 4/4 | 2.8 | fine, slower |
| gemma4:26b | 3/4 | 2.8 | very fast, flat and repetitive |
| gemma3:27b (old) | 3/4 | 3.5 | invents claims ("buys less than yesterday" on an up day) |
| qwen3.6:35b | 2/4 | 5.5 | |
| gpt-oss:20b | 0/4 | – | no usable JSON |

`worker.env`: `OLLAMA_MODEL=gemma4:31b`, `PCW_OLLAMA_THINK=0` (with thinking on, Gemma 4 returned no usable script).
Ollama's model store is `D:\AI\ollama\models` (the desktop app's "Model location" setting).

## Story sources added (backend/app/brand_manager/sources.py)

Hyperliquid (24h moves ≥6% on liquid perps, extreme funding, 7-day open-interest swings, 48 h charts), CoinGecko
trending, Pokémon 30-day drops (not only gains) with each card's verified price line, Pokémon Top 100 entries, and the
community's weekly totals (aggregates only). New category: `crypto_derivatives`.

## v2.1: real photos and live data (2026-10-19)

Why job 5 (Bitcoin rejected at $83K) was all text: a news brief has no chart, price or image, so every visual fell back
to kinetic text, and fast pace repeated the same words as a second shot. Now `media.py` runs before the script:

- **Coin**: found in the brief or headline; Hyperliquid's 48 h hourly chart, last price and 24h change, CoinGecko logo.
  Shown only on labelled scenes (`coin`: 3D spinning logo + "PRICE NOW · Hyperliquid · live"; `chart`: the live line with a
  price tag riding its head). Never under the script's own words (no live `stat`), never in the spoken script.
- **Photos**: Openverse (photographs only, commercial use + modification allowed), then Wikimedia Commons (CC0 / CC BY /
  CC BY-SA JPEGs). Diagrams, maps, logos, screenshots and other charts are filtered out; Gemma 4 looks at each photo and
  drops the ones that don't fit the story. Every photo shows its credit and licence on screen and in `meta.photo_credits`.
  Card stories get no stock photos (other cards could pass for the one that sold).
- **Storyboard**: kinetic-text beats become `photo` scenes (Ken Burns push/pan, brand grade, light leak), at most two in
  a row; headlines get a photo behind them; fast-pace second shots are photo b-roll, never repeated words.
- **Motion**: camera shake on hard cuts, RGB split on breaking headlines.
