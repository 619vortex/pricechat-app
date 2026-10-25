"""Short Market choices: what stories it looks for, the 3D looks, the voices and the pipeline's stages."""

from typing import Any

from app.content import voices

# The pipeline, in order. `where` = who runs it: the backend (light work) or an AI worker (LLM, voice, video model).
STAGES: tuple[tuple[str, str, str], ...] = (
    ("idea", "Find the story", "backend"),
    ("script", "Write the story", "worker"),
    ("voice", "Record the voice", "worker"),
    ("visuals", "Animate 3D scenes", "worker"),
    ("assemble", "Edit and caption", "worker"),
    ("quality", "Final check", "backend"),
)
WORKER_STAGES = ("script", "voice", "visuals", "assemble")

# `angle` tells the writer what kind of story this is. `weight` ranks categories against each other.
CATEGORIES: dict[str, dict[str, Any]] = {
    "markets": {"label": "Market moves", "weight": 1.0, "disclaimer": True,
                "angle": "A market story: what moved, by how much, and what the numbers look like right now."},
    "news": {"label": "Money news", "weight": 1.0, "disclaimer": True,
             "angle": "A news story: what happened and who reported it. Report it, don't judge it."},
    "history": {"label": "Today in money history", "weight": 0.95, "disclaimer": False,
                "angle": "A history story: take the viewer back to that day, then say why it is remembered."},
    "facts": {"label": "Money facts", "weight": 0.8, "disclaimer": False,
              "angle": "A 'did you know' story: link the facts into one surprising thread."},
    "collectibles": {"label": "Collectibles", "weight": 0.9, "disclaimer": False,
                     "angle": "A collectibles story: one card, what it sold for or how its price moved."},
    "buzz": {"label": "From X and the community", "weight": 0.85, "disclaimer": True,
             "angle": "A 'what people are talking about' story: say who said it and how much attention it got. Paraphrase; never quote more than a few words."},
}

# 3D looks. `prompt` is appended to every scene the video model draws; `accent` colours the captions.
STYLES: dict[str, dict[str, str]] = {
    "cartoon3d": {"label": "3D Cartoon", "accent": "#ffd23f",
                  "prompt": "stylized 3D animated feature film, friendly rounded shapes, soft global illumination, subsurface scattering, "
                            "vibrant saturated colors, shallow depth of field, cinematic lighting, highly detailed CGI render"},
    "clay": {"label": "Claymation", "accent": "#ff8a5c",
             "prompt": "handmade claymation stop-motion look, plasticine characters and props with visible thumbprints, miniature tabletop set, "
                       "soft studio lighting, shallow depth of field, charming and tactile 3D render"},
    "neon": {"label": "Neon City", "accent": "#35f2ff",
             "prompt": "futuristic 3D render, glossy neon-lit city at night, holographic displays, cyan and magenta rim light, volumetric fog, "
                       "reflective wet surfaces, cinematic depth of field, highly detailed CGI"},
    "gold": {"label": "Black and Gold", "accent": "#f5c451",
             "prompt": "premium 3D render, polished gold objects on black marble, dramatic single spotlight, crisp reflections, floating dust "
                       "particles, luxury commercial look, macro lens, highly detailed CGI"},
    "bricks": {"label": "Toy Bricks", "accent": "#ff4d6d",
               "prompt": "a miniature world built from colorful plastic toy bricks, tiny toy figures, tilt-shift macro look, bright playful "
                         "lighting, glossy plastic, highly detailed 3D render"},
    "paper": {"label": "Paper Diorama", "accent": "#7bd88f",
              "prompt": "layered papercraft diorama, cut paper shapes with soft shadows between layers, handmade texture, warm light, "
                        "gentle parallax camera move, detailed 3D render"},
}
DEFAULT_STYLE = "cartoon3d"

# Voices of the local Kokoro speech model (the worker falls back to Piper or the system voice if it isn't installed).
VOICES: dict[str, str] = voices.ALL
DEFAULT_VOICE = "am_michael"

MIN_SECONDS, MAX_SECONDS = 10, 50  # a short is 10 to 50 seconds; one scene is about four seconds
LENGTHS = (10, 20, 30, 40, 50)  # what the options offer; left alone, the length follows the story (auto_seconds)
DEFAULT_LENGTH = 30
AUTO = "auto"  # style: a different look for every short


def auto_seconds(brief: dict[str, Any]) -> int:
    """As long as the story has facts to tell: about the time it takes to say them, 10 to 50 seconds."""
    words = sum(len(str(f.get("text") if isinstance(f, dict) else f).split()) for f in brief.get("facts") or [])
    return max(MIN_SECONDS, min(MAX_SECONDS, round(words / 2.6 / 5) * 5 + 5))


def auto_style(n: int) -> str:
    keys = list(STYLES)
    return keys[n % len(keys)]

# How sharp the video model draws (worker: pricechat_worker/gpu.py). `minutes` = one 30-second short on an RTX 5090.
QUALITIES: dict[str, dict[str, Any]] = {
    "fast": {"label": "Fast", "detail": "480p scenes", "minutes": 9},
    "standard": {"label": "Standard", "detail": "576p scenes", "minutes": 16},
    "best": {"label": "Best", "detail": "720p scenes", "minutes": 28},
}
DEFAULT_QUALITY = "standard"

MAX_ATTEMPTS = 3
MAX_OPEN = 6  # shorts waiting or rendering at once: each one is minutes of GPU time
IDEA_TTL_MINUTES = 30  # the list refreshes itself when it is older than this
IDEA_KEEP_DAYS = 3

BANNED = ("price prediction", "financial advice")  # on top of brand_manager.defaults.FORBIDDEN_CLAIMS

FINANCE_FEEDS: tuple[tuple[str, str], ...] = (
    ("CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
)
