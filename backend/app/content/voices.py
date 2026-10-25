"""The narrator voices of the worker's local Kokoro model (worker/models/kokoro/voices-v1.0.bin), shared by Brand Manager and
Short Market. Only the English voices: the scripts are English. A mix ("am_michael:60+bm_george:40") blends two voices'
styles by weight; the worker's kokoro_tts.py builds it. Each key has a preview clip at /voices/<preview_slug(key)>.mp3
(made by worker/voice_previews.py)."""

import re

# best first (Kokoro's own quality grades), then the rest by accent
KOKORO: dict[str, str] = {
    "am_michael": "Michael · US male, warm news voice", "af_heart": "Heart · US female, warm", "af_bella": "Bella · US female, lively",
    "bm_george": "George · UK male, classic narrator", "bf_emma": "Emma · UK female, crisp", "am_fenrir": "Fenrir · US male, energetic",
    "am_puck": "Puck · US male, upbeat", "af_nicole": "Nicole · US female, soft (ASMR)", "af_aoede": "Aoede · US female",
    "af_kore": "Kore · US female", "af_sarah": "Sarah · US female, clear", "bm_fable": "Fable · UK male, storyteller",
    "bf_isabella": "Isabella · UK female", "af_nova": "Nova · US female", "af_alloy": "Alloy · US female", "af_sky": "Sky · US female, young",
    "am_adam": "Adam · US male, deep", "am_eric": "Eric · US male, calm", "am_liam": "Liam · US male", "am_echo": "Echo · US male",
    "am_onyx": "Onyx · US male, low", "bm_lewis": "Lewis · UK male", "bm_daniel": "Daniel · UK male", "bf_alice": "Alice · UK female",
    "bf_lily": "Lily · UK female", "af_jessica": "Jessica · US female", "af_river": "River · US female",
}
MIXES: dict[str, str] = {
    "am_michael:60+bm_george:40": "Mix · Michael + George, transatlantic anchor",
    "af_heart:50+af_bella:50": "Mix · Heart + Bella, warm and bright",
    "am_fenrir:50+am_puck:50": "Mix · Fenrir + Puck, punchy",
    "bf_emma:60+af_heart:40": "Mix · Emma + Heart, soft British",
}
ALL: dict[str, str] = {**KOKORO, **MIXES}

_PART = re.compile(r"^([ab][fm]_[a-z]+)(?::(\d{1,3}))?$")


def parts(key: str) -> list[tuple[str, int]] | None:
    """[(voice, weight %)] of a voice or a mix of known voices, or None if it isn't one ("am_michael" → [("am_michael", 100)])."""
    out = []
    for p in key.split("+"):
        m = _PART.match(p)
        if not m or m.group(1) not in KOKORO:
            return None
        out.append((m.group(1), int(m.group(2) or 100)))
    return out if len(out) <= 3 and sum(w for _, w in out) == 100 else None


def preview_slug(key: str) -> str:
    return re.sub(r"[^a-z0-9_]", "-", key.lower())
