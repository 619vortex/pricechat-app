"""Content Studio v2 benchmark: one verified story rendered with each creative preset, offline (no server needed).

    .venv/Scripts/python bench_v2.py [preset ...] [--out out/bench]

Scripts go through the backend's own fact and safety checks (app.brand_manager.safety, imported from ../backend), exactly
as the server would check them. Each version saves the MP4, a thumbnail, the script, the storyboard and the QC report."""

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))
sys.path.insert(0, str(HERE))

from app.brand_manager import safety  # noqa: E402
from app.brand_manager.defaults import STRATEGY  # noqa: E402

from pricechat_worker import pipeline2, render  # noqa: E402

# The Mewtwo story exactly as discovery builds it (app/brand_manager/discovery.py, verified-sale branch). The sale's
# details are the ones the published v1 video states (Whisper transcript of it): $17,225, PSA 10, eBay, October 12, 2026.
# The "Sale type" fact is left out: the v1 video doesn't say it, so it can't be confirmed here.
MEWTWO = {
    "job_id": 0, "video_id": 3, "generation": 1, "category": "pokemon_rare", "template": "pokemon_spotlight",
    "topic": "A Rocket's Mewtwo PSA 10 just sold for $17,225",
    "brief": {
        "headline": "Rocket's Mewtwo PSA 10 sold for $17,225",
        "facts": [{"text": "Rocket's Mewtwo (Gym Challenge, 2000) PSA 10 sold for $17,225 on October 12, 2026.", "source": "eBay"},
                  {"text": "PSA 10 is PSA's highest grade, called Gem Mint.", "source": "PSA grading standards"}],
        "card": {"id": "gym2-14", "name": "Rocket's Mewtwo", "set_name": "Gym Challenge", "year": 2000,
                 "image_url": "https://images.scrydex.com/pokemon/gym2-14/large"},
        "numbers": {"sale_usd": 17225.0, "year": 2000},
        "images": [{"url": "https://images.scrydex.com/pokemon/gym2-14/large", "credit": "Card image"}],
        "sources": [{"name": "eBay", "url": "https://pricechat.live/pokemon/cards/gym2-14"}],
    },
    "brand": {"name": "Price Chat", "website": "https://pricechat.live", "language": "English", "cta": "Follow Price Chat for daily market news.",
              "personality": "Fast, clear, confident and honest. Plain English, no hype, no financial advice.",
              "audience": "Crypto traders and Pokémon card collectors, 18-40, who want fast, accurate market news.",
              "colors": ["#00e58f", "#050706", "#e6c46e"], "logo_url": "", "examples": [], "avoid": []},
    "strategy": {k: STRATEGY[k] for k in ("min_seconds", "max_seconds", "voice", "llm", "ollama_model", "quality", "watermark", "outro_cta",
                                          "music", "hashtags", "ai_disclosure", "voice_name", "voice_speed")},
    "suggested_hashtags": ["#pricechat", "#pokemoncards", "#vintagepokemon"],
    "template_style": {"label": "C · Pokémon Spotlight", "scenes": ["hook", "card", "facts", "outro"]},
    "learned": {"top_hooks": [], "best_length": None},
}

# A crypto news story as discovery builds it from an RSS item (no chart, no price, no image in the brief): job 5's story.
BTC_NEWS = {
    **{k: MEWTWO[k] for k in ("brand", "strategy", "learned")}, "job_id": 0, "video_id": 5, "generation": 1, "category": "crypto_news",
    "template": "breaking_news", "topic": "Bitcoin is rejected at $83K as US bond yields reverse higher with equities",
    "brief": {"headline": "Bitcoin is rejected at $83K as US bond yields reverse higher with equities",
              "facts": [{"text": "Bitcoin was rejected near $83,000 as US Treasury yields reversed higher.", "source": "Cointelegraph"},
                        {"text": "US equities reversed lower alongside the move in bond yields.", "source": "Cointelegraph"}],
              "numbers": {}, "sources": [{"name": "Cointelegraph", "url": "https://cointelegraph.com/"}]},
    "suggested_hashtags": ["#pricechat", "#bitcoin", "#crypto"], "template_style": {"label": "B · Breaking News", "scenes": ["hook", "facts", "outro"]},
}
STORIES = {"mewtwo": MEWTWO, "btc": BTC_NEWS}
STORY = STORIES[sys.argv[sys.argv.index("--story") + 1]] if "--story" in sys.argv else MEWTWO


def approve(script: dict) -> dict:
    problems = safety.check(script, STORY["brief"], {**STRATEGY}, [])
    return {"ok": not problems, "problems": problems, "script": script}


def main() -> None:
    args = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and sys.argv[i - 1] not in ("--out", "--story")]
    out_dir = HERE / "out" / "bench"
    if "--out" in sys.argv:
        out_dir = Path(sys.argv[sys.argv.index("--out") + 1])
    presets = args or ["documentary", "viral", "funny"]
    ff = render.ffmpeg_exe()
    out_dir.mkdir(parents=True, exist_ok=True)
    for preset in presets:
        work = out_dir / f"work_{preset}"
        work.mkdir(parents=True, exist_ok=True)
        job = {**STORY, "creative": {"preset": preset}}
        t0 = time.monotonic()
        log: list[str] = []
        made = pipeline2.produce(job, ff, work, approve, lambda s, st, n="": log.append(f"{time.monotonic() - t0:6.1f}s {s} {st} {n}"))
        made["meta"]["seconds"] = round(time.monotonic() - t0, 1)
        (out_dir / f"{preset}.mp4").write_bytes(made["out"].read_bytes())
        (out_dir / f"{preset}.jpg").write_bytes(made["thumb"].read_bytes())
        (out_dir / f"{preset}.json").write_text(json.dumps({"script": made["script"], "meta": made["meta"], "props": made["props"], "log": log},
                                                           indent=1, default=str), encoding="utf-8")
        print(f"{preset}: {made['meta']['duration']:.1f}s video in {made['meta']['seconds']:.0f}s · timings {made['meta']['timings']}")
        for c in made["qc"]["checks"]:
            print(f"   [{c['status']:7}] {c['label']}: {c['detail']}")


if __name__ == "__main__":
    main()
