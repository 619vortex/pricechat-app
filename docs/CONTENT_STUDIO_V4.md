# Content Studio v4: real media first

Discovery, research, fact checks, script approval, publishing, scheduling, analytics, Brand Manager and the content
calendar are unchanged. v4 changes **how a video is made**: authentic footage and photographs carry it, the script is
written for the shots that exist, and the edit looks like a person cut it. v2 (motion graphics) and v1 (slides) remain
selectable as renderers.

## Audit of v2: why the output looked AI-made

| Where | What it did | Why it read as synthetic |
|---|---|---|
| `motion/src/fx.tsx` `Background` | every frame sat on drifting glow blobs, a perspective grid, a light sweep, particles (sparkle / confetti / dust) and grain | "generic glowing background", the same on every story |
| `Video.tsx` `SceneShell` | a push + drift on every scene, a spring punch and camera shake on hard cuts, whip / zoom / slide with motion blur, flashes | constant zooming and random transitions |
| `fx.tsx` `Counter` | prices count up from 0 and land with a bounce and glow (frames show "$17,219") | overdesigned price counters |
| `scenes/index.tsx` | `price_reveal` rays + gold, 3D card flip with holo sweep, kinetic "IN AWE" / "GEM MINT" punchline cards | gold explosions, floating objects, filler type |
| `Captions.tsx` | 58–86 px heavy captions with pop / boxed word animation | huge animated captions over the subject |
| `media.py` | photos only (no video), generic fallback queries ("cryptocurrency", "stock market screen"); card stories got no media but the card scan | no real footage; one picture for 30 s |
| `script2.py` + `storyboard.py` | the script was written first, then mapped to 11 template scenes | the same templates in the same order on every story |
| `creative.py` | "hype"/"bright" narrators at 1.1–1.14× speed, "dramatic" tone, whooshes / impacts / boing | the voice of an ad |
| `qc2.py` | technical checks only | nothing measured whether the pictures were real or relevant |

## v4 pipeline (worker/pricechat_worker)

| Module | What it does |
|---|---|
| `assets.py` | media research: the brief's own media (the exact card's scan), owner-added library assets, Wikimedia Commons video + photos (licence read from each file's metadata; transcoded ~1080p derivative), NASA (public domain), Pexels / Pixabay (only with `PEXELS_API_KEY` / `PIXABAY_API_KEY`), Openverse. CC0 / public domain / CC BY / CC BY-SA only; NC/ND/unknown skipped. Gemma 4 looks at each candidate (photo or poster frame): exact / related / generic / unfit, watermark, synthetic-looking. Each asset gets a rights record (source URL, creator, rights holder, licence + URL, permitted uses, attribution, resolution, duration, orientation, what it shows). Videos are trimmed to 24 s, H.264 30 fps. Cache: `worker/cache/assets`. |
| `editorial.py` | six production styles (real news, automotive, luxury, collectibles, tech demo, funny/meme: pacing, cut vs dissolve, typography, frame geometry, music, clip sound, narration tone), three narrators (natural, steady, documentary), the **editorial gate** (`assess`: needs the exact subject and enough real shots, else the story goes to editorial review), shot selection (a card scan gives four real close-ups), the edit (`build`: cuts on sentence boundaries, B-roll inserts for long lines that never take a shot another line asked for, prices/names held on the line's own shot), verified graphics (`callouts`, `facts_rows`, `chart_of`: values from the brief only), `unsupported()` number check, renderer props |
| `script4.py` | the script is written **for the shots** (what each shows, how long it can run) and the verified graphics available; understated voice per style; v2's claim filter + backend safety check unchanged |
| `authenticity.py` | the visual authenticity score (see below) |
| `pipeline4.py` | research → gate → shots → script → narration (AI / uploaded human narration / none = on-screen text) → edit → render → QC; `mode: timeline` renders the owner's edited timeline |
| `motion/src/editorial/` | Remotion composition `Editorial`: real clips (`OffthreadVideo`, own sound ducked under the narration) and photos with crop/focus/zoom and slow linear moves, cuts or short dissolves, lower third, static price card, real line/sales charts drawn once, record card, source tag, small calm captions, licence credits on screen, quiet end card. No particles, glows, shakes, whips or counters. Fonts: Inter, Playfair Display, JetBrains Mono (OFL). |

The worker uses v4 when the brand's renderer is "editorial" (the default; migration 0073 moves brands off the old
default) and Node/Remotion + Kokoro are present. `EditorialReview` → the job fails without retries with the reason, and
the media library shows what was found.

## Visual authenticity score

Per clip and overall, out of 100: relevance of each shot to the line spoken over it (30, judged by the local model),
real media share (18), exact-subject share (10), rights records complete (10), sharpness (enlargement > 1.6×, 10),
repetition (8), readability (caption length, share of time under big graphics, 7), pacing vs the style (7). Flags:
synthetic/generic-dominated, filler > 25 %, shots not matching the words, one picture > 45 % of the video (80 % for a
card story), identical shots reused, soft pictures, incomplete rights. A high real-media share alone can't score well:
relevance weighs most. Shown in Content Studio and stored in `render_info.authenticity`.

## Content Studio (dashboard)

Video page → **Editor**: the authenticity card; the last render next to an inspector; a multi-track timeline (video
shots with thumbnails coloured by relevance, graphics, captions, narration, music) with a playhead synced to the player.
Shot inspector: replace from the library, duration, start inside the clip, full-screen crop vs framed, focus, zoom, camera
move, clip sound, earlier/later, split at the playhead, remove, and a 9:16 framing preview. Graphic inspector: timing and
text (numbers checked against the research on save; charts always draw the research's own data). Sound: captions style,
narration and music volume, music track. **Render this edit** saves the timeline and re-renders it (same script and
narration). **Media library**: every asset looked at, with licence, creator, credit, source and licence links, relevance,
resolution/duration/orientation; exclude/include; **Add media** (https file link + licence + attestation; NC/ND refused).
Creative: renderer, production style, narration mode (AI / your recording link / none), AI narrator.

API: `GET/POST /brand-manager/videos/{id}/media`, `PATCH /videos/{id}/media/{asset_id}`, `PUT /videos/{id}/timeline`,
`POST /videos/{id}/rerender {"scope": "timeline"}`, worker `POST /worker/jobs/{id}/media`. Columns `bm_videos.media`,
`bm_videos.timeline` (migration 0073).

## Benchmarks (`worker/bench_v4.py`, outputs in `worker/out/bench_v4/`)

`bench_v4.py mewtwo stock ai car dubai --stories bench_stories.json`. Each writes the MP4, thumbnail, a contact sheet,
and a JSON report (script, timeline, media library with rights, QC, authenticity). Story research
(`bench_stories.json`, 2026-10-20): every fact read from its source page, every licence read from Commons / NASA metadata.

- **Mewtwo**: the official card scan (four real close-ups), Price Chat's 22 recorded PSA 10 sales since April as a
  sales chart, the sale record, licensed photos of Pokémon TCG events labelled "Not the card in this story".
- **Stock**: Nvidia: Nasdaq daily closes (May 1–Oct 19, 2026) as the chart, Q2 FY27 results from NVIDIA's press release,
  CC BY Computex / product footage, HQ photos.
- **AI**: NASA Astrobee: public-domain NASA footage and photos.
- **Car**: Bugatti Veyron 16.4: manufacturer figures; only two Commons videos of the exact model exist (one usable).
- **Dubai**: 2025 transaction totals (Dubai Media Office) and H1 2026 completions (DLD via Khaleej Times); Google Earth /
  Landsat time-lapses and CC photos. No individual property prices or interiors.

## Known limits

- The vision check is a local model: it is lenient on "related" (e.g. it accepted a Proxmox screenshot for an Nvidia
  story). The authenticity score's relevance judge and the owner's exclude button are the backstops.
- No licensed stock keys are configured on the worker (Pexels / Pixabay code paths are untested until keys are added).
- Commons has little modern footage for some subjects (Dubai drone footage, most supercars): those stories lean on photos.
- Card scans are 600 px wide: close-ups are capped at ~1.6× enlargement.

## Kiwi hook writer (A/B, default off)

writer (gemma, soft) → validators (code, hard gate) → critic (gemma, soft gate) → renderer.

- `worker/pricechat_worker/validators.py`: every code-checkable rule (word caps, banned phrases, hype words, This/It
  openers, numbers only from facts or the fact card, report verbs without a qualifier, format repeats (last 3), schema,
  fuzzy duplicates (last 50), shape signals, bare-number hooks, two periods set side by side, money questions in the
  caption, loss language without a decline in the research). Pure functions, no model calls.
- `backend/app/brand_manager/factcard.py`: derived comparisons (ratios, % changes, averages, spans, conversions)
  computed from the brief, ≤2 significant figures, sent as `brief.derived`. `safety` recomputes the card from the stored
  brief, so the fact checker accepts those numbers and only those.
- `worker/pricechat_worker/kiwi.py`: the short Kiwi prompt (1,151 chars vs 9,104 for the frontier version in
  `docs/prompts/`), one repair round with each failure's fix, a comparative critic (one call, picks the candidate with
  the most tension: PASS or REWRITE + one fix). Visual format, first-3-seconds and kill list are decided in code.
- Turn on per video or per brand: creative `writer: "new"` (Content Studio › Creative › Hook writer). Kiwi HOLD falls
  back to the standard writer.
- A/B: `python -m pricechat_worker.script4 --ab --out out/ab_compare.json` (both writers on the benchmark stories, with
  the hard-gate results and the backend fact check for each).
