"""Content Studio v2 creative settings: visual style, narrator, script tone, animation intensity, music, captions.

The backend sends `job["creative"]` (the brand's defaults merged with the video's own choices); anything missing or "auto"
is decided here from the story (a Pokémon sale gets the collectible look and tone, a price move the trading look...).
Presets are just named bundles of these settings (the three benchmark versions are presets)."""

from typing import Any

STYLES = {  # visual templates: same brand, different look
    "pokemon": "Pokémon card sales: the card in 3D, holo light, gold for rare sales",
    "crypto": "Crypto market news: headline straps, grid, source first",
    "trading": "Trading and price analysis: counters, charts, green/red",
    "viral": "Viral financial stories: big kinetic type, fast cuts",
    "community": "Community highlights: Kiwi up front, warm accents, confetti",
}

# Kokoro voices (local, free). A narrator is a voice + delivery: speed and how long it pauses.
NARRATORS: dict[str, dict[str, Any]] = {
    "anchor": {"label": "News anchor (crisp, US)", "voice": "am_michael", "speed": 1.06, "sentence_gap": 0.22, "beat_gap": 0.45},
    "documentary": {"label": "Documentary (calm, British)", "voice": "bm_george", "speed": 0.94, "sentence_gap": 0.38, "beat_gap": 0.75},
    "hype": {"label": "Fast and punchy (US)", "voice": "am_puck", "speed": 1.14, "sentence_gap": 0.12, "beat_gap": 0.3},
    "wry": {"label": "Dry and funny (US)", "voice": "am_onyx", "speed": 1.04, "sentence_gap": 0.24, "beat_gap": 0.6},
    "warm": {"label": "Warm and friendly (US)", "voice": "af_heart", "speed": 1.03, "sentence_gap": 0.24, "beat_gap": 0.5},
    "bright": {"label": "Bright and upbeat (US)", "voice": "af_nova", "speed": 1.1, "sentence_gap": 0.16, "beat_gap": 0.35},
}

TONES = {
    "breaking": "Breaking news: urgent, clean, factual. Short declarative sentences. The news first, then why it matters.",
    "funny": "Funny / sarcastic: dry wit about the situation (never mocking people). Deadpan observations, a playful turn at the "
             "end. The joke comes from the facts themselves, not from invented details.",
    "dramatic": "Dramatic: build tension. Let the big number land after a pause. Short lines, then a longer one. Cinematic, not hype.",
    "educational": "Educational: make the viewer smarter in 30 seconds. Explain one term the story depends on, in plain words.",
    "collectible": "Collectible discovery: the thrill of the find. Make the viewer see the card. Respect for the hobby, a little awe.",
}

MUSIC = {"documentary": "Calm documentary pad (84 BPM)", "viral": "Driving beat (124 BPM)", "playful": "Playful plucks (108 BPM)",
         "market": "Minimal market pulse (100 BPM)", "none": "No music"}
CAPTIONS = {"pop": "Pop: each phrase pops in, the spoken word lights up", "karaoke": "Karaoke: words fill in as they're said",
            "boxed": "Boxed: the spoken word sits in a green box", "clean": "Clean: calm white captions, key words in colour"}
INTENSITY = {"calm": "Calm: slow moves, soft transitions", "normal": "Normal", "high": "High: fast cuts, punchy motion"}

PRESETS: dict[str, dict[str, Any]] = {
    "documentary": {"label": "Premium collectible documentary", "tone": "collectible", "narrator": "documentary", "intensity": "calm",
                    "music": "documentary", "captions": "clean", "pace": "slow"},
    "viral": {"label": "Fast-paced viral TikTok", "tone": "dramatic", "narrator": "hype", "intensity": "high", "music": "viral",
              "captions": "boxed", "pace": "fast"},
    "funny": {"label": "Funny Price Chat commentary", "tone": "funny", "narrator": "wry", "intensity": "normal", "music": "playful",
              "captions": "pop", "pace": "normal"},
}

DEFAULTS: dict[str, Any] = {"engine": "motion", "style": "auto", "tone": "auto", "narrator": "auto", "intensity": "auto", "music": "auto",
                            "captions": "auto", "seconds": None, "preset": None, "pronunciations": {}, "scenes": {}}

# the story kind (Brand Manager template) -> sensible defaults
BY_TEMPLATE = {
    "pokemon_spotlight": {"style": "pokemon", "tone": "collectible", "narrator": "documentary", "music": "documentary", "captions": "pop"},
    "breaking_news": {"style": "crypto", "tone": "breaking", "narrator": "anchor", "music": "market", "captions": "pop"},
    "price_alert": {"style": "trading", "tone": "breaking", "narrator": "anchor", "music": "market", "captions": "karaoke"},
    "top_facts": {"style": "viral", "tone": "educational", "narrator": "bright", "music": "viral", "captions": "boxed"},
    "youtube_highlight": {"style": "community", "tone": "educational", "narrator": "warm", "music": "playful", "captions": "pop"},
}


def resolve(job: dict[str, Any]) -> dict[str, Any]:
    """The final settings for this job: preset < story defaults < the owner's explicit choices (anything not 'auto')."""
    raw = {**DEFAULTS, **(job.get("creative") or {})}
    out = dict(BY_TEMPLATE.get(str(job.get("template")), BY_TEMPLATE["breaking_news"]))
    out.update({"intensity": "normal", "pace": "normal"})
    if job.get("category") == "community" or job.get("template") == "youtube_highlight":
        out["style"] = "community"
    preset = PRESETS.get(str(raw.get("preset") or ""))
    if preset:
        out.update({k: v for k, v in preset.items() if k != "label"})
    valid = {"style": STYLES, "tone": TONES, "narrator": NARRATORS, "intensity": INTENSITY, "music": MUSIC, "captions": CAPTIONS}
    for k, allowed in valid.items():
        v = raw.get(k)
        if v and v != "auto" and v in allowed:  # an unknown value (an old setting) keeps the story's default
            out[k] = v
    out.update({"engine": raw.get("engine") or "motion", "seconds": raw.get("seconds"), "preset": raw.get("preset"),
                "pronunciations": dict(raw.get("pronunciations") or {}), "scenes": dict(raw.get("scenes") or {})})
    return out


def options() -> dict[str, Any]:
    """Everything Content Studio's selectors offer (the backend has a copy of this list)."""
    return {"styles": STYLES, "narrators": {k: v["label"] for k, v in NARRATORS.items()}, "tones": TONES, "music": MUSIC,
            "captions": CAPTIONS, "intensity": INTENSITY, "presets": {k: v["label"] for k, v in PRESETS.items()}}
