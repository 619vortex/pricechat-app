"""Content Studio v4 benchmark: real stories, real media, rendered as editorial Shorts (offline: no server needed).

    .venv/Scripts/python bench_v4.py [mewtwo stock ai car dubai] [--out out/bench_v4] [--stories <bench_stories.json>]

Scripts go through the backend's own fact and safety checks (app.brand_manager.safety). Each video saves the MP4, a
thumbnail, a contact sheet, and a JSON report: script, timeline, media library with rights, QC and authenticity score.

The Mewtwo story uses Price Chat's own recorded PSA 10 sales of the card (api.pricechat.live, fetched 2026-10-20); the other
four briefs were researched with every fact read from its source and every licence read from the host's metadata."""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))
sys.path.insert(0, str(HERE))

from app.brand_manager import safety  # noqa: E402
from app.brand_manager.defaults import STRATEGY  # noqa: E402

from pricechat_worker import pipeline4, render  # noqa: E402

BRAND = {"name": "Price Chat", "website": "https://pricechat.live", "language": "English", "cta": "",
         "personality": "Clear, knowledgeable and honest. Plain English, no hype, no financial advice.",
         "audience": "Crypto traders, investors and collectors, 18-40, who want fast, accurate market stories.",
         "colors": ["#00e58f", "#050706", "#e6c46e"], "logo_url": "", "examples": [], "avoid": []}
BASE = {"brand": BRAND, "strategy": {**{k: STRATEGY[k] for k in ("min_seconds", "max_seconds", "voice", "llm", "ollama_model", "quality", "watermark", "outro_cta",
                                                                "music", "hashtags", "ai_disclosure", "voice_name", "voice_speed")}, "min_seconds": 22, "max_seconds": 34},
        "learned": {"top_hooks": [], "best_length": None}, "suggested_hashtags": ["#pricechat"], "generation": 1, "job_id": 0}


def ts(d: str) -> int:
    return int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp())


# Price Chat's recorded PSA 10 sales of Rocket's Mewtwo (Gym Challenge, 1st Edition), api.pricechat.live/api/pokemon/cards/2885/history?range=1y
MEWTWO_SALES = [("2026-04-20", 6750.00, "ebay"), ("2026-04-26", 9300.00, "fanatics"), ("2026-05-24", 7600.00, "ebay"), ("2026-05-31", 8200.00, "ebay"),
                ("2026-06-14", 10322.00, "ebay"), ("2026-06-17", 8500.00, "ebay"), ("2026-07-05", 8000.00, "ebay"), ("2026-07-12", 14999.99, "ebay"),
                ("2026-07-15", 7522.00, "ebay"), ("2026-07-19", 7500.00, "fanatics"), ("2026-07-26", 8100.00, "fanatics"), ("2026-08-12", 9999.98, "ebay"),
                ("2026-08-14", 8950.00, "ebay"), ("2026-08-30", 8700.00, "fanatics"), ("2026-09-03", 7685.45, "ebay"), ("2026-09-10", 7010.75, "ebay"),
                ("2026-09-15", 10600.00, "ebay"), ("2026-09-16", 11000.00, "ebay"), ("2026-09-21", 12000.00, "ebay"), ("2026-09-21", 12000.00, "ebay"),
                ("2026-09-22", 15999.95, "ebay"), ("2026-10-12", 17225.41, "ebay")]

MEWTWO = {
    **BASE, "video_id": 3, "category": "pokemon_rare", "template": "pokemon_spotlight", "topic": "A Rocket's Mewtwo PSA 10 sold for $17,225",
    "brief": {
        "category": "pokemon_rare", "subject": "Rocket's Mewtwo",
        "headline": "Rocket's Mewtwo PSA 10 sold for $17,225",
        "facts": [{"text": "Rocket's Mewtwo (Gym Challenge, 2000) PSA 10 sold for $17,225 on October 12, 2026.", "source": "eBay"},
                  {"text": "PSA 10 is PSA's highest grade, called Gem Mint.", "source": "PSA grading standards"},
                  {"text": "Price Chat has recorded 22 PSA 10 sales of this card since April 20, 2026, when one sold for $6,750.", "source": "Price Chat Pokémon Market"},
                  {"text": "The sale before it, on September 22, 2026, was $15,999.95.", "source": "eBay"},
                  {"text": "It is the 1st Edition Rocket's Mewtwo, card 14 of 132 in the Gym Challenge set, released October 16, 2000.", "source": "Price Chat Pokémon Market"}],
        "card": {"id": "gym2-14", "name": "Rocket's Mewtwo", "set_name": "Gym Challenge", "year": 2000, "image_url": "https://images.scrydex.com/pokemon/gym2-14/large"},
        "numbers": {"sale_usd": 17225.0, "year": 2000},
        "images": [{"url": "https://images.scrydex.com/pokemon/gym2-14/large", "credit": "Card image", "page": "https://pricechat.live/pokemon/cards/2885"}],
        "chart": {"kind": "sales", "points": [[ts(d), v] for d, v, _ in MEWTWO_SALES], "title": "Every recorded PSA 10 sale since April",
                  "source": "22 sales · eBay and Fanatics · recorded by the Price Chat Pokémon Market", "prefix": "$"},
        "sources": [{"name": "eBay", "url": "https://pricechat.live/pokemon/cards/2885"}, {"name": "Price Chat Pokémon Market", "url": "https://pricechat.live/pokemon/cards/2885"}],
    },
}


def from_research(key: str, data: dict, extra: list | None = None) -> dict:
    s = data[key]
    template = {"stocks": "breaking_news", "ai": "breaking_news", "cars": "top_facts", "real_estate": "breaking_news"}.get(s["category"], "breaking_news")
    brief = {k: s[k] for k in ("headline", "subject", "category", "facts", "numbers", "sources", "media") if k in s}
    if (s.get("chart") or {}).get("kind") == "market":  # a market-structure chart (backend marketviz.py): kept as is
        brief["chart"] = s["chart"]
    elif s.get("chart"):
        ch = s["chart"]
        brief["chart"] = {"kind": "line", "points": ch["points"], "title": ch.get("label") or "", "source": ch.get("label") or "", "prefix": "$"}
    brief["numbers"] = {k: v for k, v in (s.get("numbers") or {}).items() if isinstance(v, (int, float))}
    return {**BASE, "video_id": 100 + list(data).index(key), "category": s["category"], "template": template, "topic": s["headline"], "brief": brief}


def approve_for(job: dict):
    def approve(script: dict) -> dict:
        problems = safety.check(script, job["brief"], {**STRATEGY}, [])
        return {"ok": not problems, "problems": problems, "script": script}
    return approve


def sheet(ff: str, video: Path, out: Path, n: int = 12) -> None:
    import subprocess

    meta = render.probe(ff, video)
    step = max(0.5, meta["duration"] / n)
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(video), "-vf", f"fps=1/{step:.3f},scale=270:-1,tile=6x2", "-frames:v", "1", str(out)],
                   capture_output=True, timeout=300)


def main() -> None:
    argv = sys.argv[1:]
    out_dir = Path(argv[argv.index("--out") + 1]) if "--out" in argv else HERE / "out" / "bench_v4"
    sfile = Path(argv[argv.index("--stories") + 1]) if "--stories" in argv else None
    names = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] not in ("--out", "--stories"))] or ["mewtwo"]
    research = json.loads(sfile.read_text(encoding="utf-8")) if sfile else {}
    if research.get("mewtwo_extra"):
        MEWTWO["brief"]["media"] = [m for m in research["mewtwo_extra"]["media"] if "Wagner" not in str(m.get("shows"))]
    ff = render.ffmpeg_exe()
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        job = MEWTWO if name == "mewtwo" else from_research(name, research)
        work = out_dir / f"work_{name}"
        work.mkdir(parents=True, exist_ok=True)
        t0 = time.monotonic()
        log: list[str] = []

        def step(s: str, st: str, n: str = "") -> None:
            line = f"{time.monotonic() - t0:6.1f}s {s} {st} {n}"
            log.append(line)
            print("  " + line, flush=True)

        try:
            made = pipeline4.produce(job, ff, work, approve_for(job), step)
        except pipeline4.EditorialReview as e:
            print(f"{name}: EDITORIAL REVIEW: {e}")
            (out_dir / f"{name}.json").write_text(json.dumps({"review": str(e), "library": e.library, "log": log}, indent=1, default=str), encoding="utf-8")
            continue
        made["meta"]["seconds"] = round(time.monotonic() - t0, 1)
        (out_dir / f"{name}.mp4").write_bytes(made["out"].read_bytes())
        (out_dir / f"{name}.jpg").write_bytes(made["thumb"].read_bytes())
        sheet(ff, out_dir / f"{name}.mp4", out_dir / f"sheet_{name}.png")
        (out_dir / f"{name}.json").write_text(json.dumps({"script": made["script"], "meta": made["meta"], "timeline": made["timeline"], "log": log},
                                                         indent=1, default=str), encoding="utf-8")
        a = made["authenticity"]
        print(f"{name}: {made['meta']['duration']:.1f}s in {made['meta']['seconds']:.0f}s · authenticity {a['score']}/100 {a['grade']} · flags {a['flags']}")
        for c in made["meta"]["checks"]:
            print(f"   [{c['status']:7}] {c['label']}: {c['detail']}")


if __name__ == "__main__":
    main()
