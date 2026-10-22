"""Which local model writes the best Content Studio scripts? Each model writes the same stories in several tones through
the real v2 writing loop (hook workshop, style + claim checks, AI fact check, the backend's own safety.check).

    .venv/Scripts/python model_bakeoff.py gemma3:27b gemma4:31b qwen3.6:27b ...  [--think 0|1]

Writes out/bakeoff/<model>.json (every script + problems) and prints a table: approved AI scripts, drafts needed,
template fallbacks, seconds per script. Quality is then read by a person: the table only says who follows the rules."""

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))
sys.path.insert(0, str(HERE))

from app.brand_manager import safety  # noqa: E402
from app.brand_manager.defaults import STRATEGY  # noqa: E402

import bench_v2  # noqa: E402
from pricechat_worker import creative, pipeline2  # noqa: E402

BTC = {**bench_v2.MEWTWO, "video_id": 11, "category": "crypto_market", "template": "price_alert", "topic": "Bitcoin is up 3.2% in 24 hours",
       "brief": {"headline": "Bitcoin is up 3.2% in 24 hours", "coin": {"id": "bitcoin", "name": "Bitcoin", "symbol": "BTC"},
                 "facts": [{"text": "Bitcoin (BTC) price: $67,412 (CoinGecko).", "source": "CoinGecko"},
                           {"text": "24-hour change: +3.20%.", "source": "CoinGecko"},
                           {"text": "24-hour trading volume: $38.1 billion.", "source": "CoinGecko"}],
                 "numbers": {"price_usd": 67412.33, "change_24h_pct": 3.2, "volume_24h_usd": 38100000000},
                 "images": [], "sources": [{"name": "CoinGecko", "url": "https://www.coingecko.com/en/coins/bitcoin"}]},
       "template_style": {"label": "B · Crypto Price Alert", "scenes": ["hook", "chart", "facts", "outro"]}}
CASES = [("mewtwo", bench_v2.MEWTWO, "documentary"), ("mewtwo", bench_v2.MEWTWO, "viral"), ("mewtwo", bench_v2.MEWTWO, "funny"),
         ("bitcoin", BTC, None)]


def main() -> None:
    args = sys.argv[1:]
    if "--think" in args:
        i = args.index("--think")
        os.environ["PCW_OLLAMA_THINK"] = args[i + 1]
        del args[i:i + 2]
    out = HERE / "out" / "bakeoff"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for model in args:
        os.environ["OLLAMA_MODEL"] = model
        results = []
        for name, job0, preset in CASES:
            job = {**job0, "creative": {"preset": preset} if preset else {}}
            cr = creative.resolve(job)
            notes: list[str] = []

            def approve(script: dict, brief: dict = job["brief"]) -> dict:
                problems = safety.check(script, brief, {**STRATEGY}, [])
                return {"ok": not problems, "problems": problems, "script": script}

            t0 = time.monotonic()
            try:
                s = pipeline2.write_script(job, cr, approve, lambda st, status, n="": notes.append(n))
                err = None
            except Exception as exc:  # noqa: BLE001
                s, err = {}, f"{type(exc).__name__}: {exc}"
            secs = time.monotonic() - t0
            results.append({"case": f"{name}/{preset or 'auto'}", "engine": s.get("engine"), "drafts": 1 + len(notes), "seconds": round(secs, 1),
                            "problems": notes, "error": err, "hook": s.get("hook_workshop") or (s.get("segments") or [{}])[0].get("text"),
                            "segments": [{k: g.get(k) for k in ("beat", "visual", "text", "screen")} for g in s.get("segments") or []],
                            "title": s.get("title")})
            r = results[-1]
            print(f"{model:24} {r['case']:22} {('AI' if (r['engine'] or '').startswith('ollama') else 'TEMPLATE'):8} drafts {r['drafts']} "
                  f"{r['seconds']:6.1f}s  {(r['hook'] or '')[:80]}", flush=True)
        (out / f"{model.replace(':', '_')}{'_think' if os.environ.get('PCW_OLLAMA_THINK') == '1' else ''}.json").write_text(
            json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
        ai = [r for r in results if (r["engine"] or "").startswith("ollama")]
        rows.append((model, len(ai), len(results), sum(r["drafts"] for r in results) / len(results), sum(r["seconds"] for r in results) / len(results)))
    print("\nmodel                     AI scripts approved   avg drafts   avg seconds")
    for m, a, n, d, sec in rows:
        print(f"{m:24}  {a}/{n}                  {d:4.1f}        {sec:6.1f}")


if __name__ == "__main__":
    main()
