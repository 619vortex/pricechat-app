"""Phase 0 benchmark: the production rules gate vs local Ollama models vs Claude, on ops/intel/eval_v1.json.

Every analyser fills app.intel.schema.ItemAnalysis with app.intel.prompt (same prompt everywhere). The decision used for scoring is
deterministic: newsworthy = materiality in {major, significant} and not speculative and not opinion/feature; major = materiality major.

  python ops/intel/bench.py rules  OUT.jsonl
  python ops/intel/bench.py ollama:qwen3.6:27b OUT.jsonl
  python ops/intel/bench.py claude:claude-haiku-5-5 OUT.jsonl [--limit N] [--ids F578,T45631] [--budget-usd 1.0]
Results append one JSON line per item (resumable: items already in OUT are skipped). No secret is ever printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.intel.prompt import PROMPT_VERSION, SYSTEM, json_schema, user_message  # noqa: E402
from app.intel.schema import ItemAnalysis  # noqa: E402

PRICES = {  # USD per million tokens (input, output); Claude API list prices 2026-11
    "claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0),
}
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
# Owner approval 2026-11-25: at most $15 for model evaluation, at most $3 per UTC day. Every paid call is appended to one ledger shared by
# all runs; a call is refused BEFORE it is sent when its worst-case cost would cross either limit.
DAY_CAP_USD, TOTAL_CAP_USD = 3.0, 15.0
MAX_OUT = 4000


def ledger_spent(path: str) -> tuple[float, float]:
    import datetime as dt

    today, day, total = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"), 0.0, 0.0
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            r = json.loads(line)
            total += r["usd"]
            day += r["usd"] if r["day"] == today else 0.0
    return day, total


def ledger_add(path: str, model: str, usd: float, tin: int, tout: int) -> None:
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"day": now.strftime("%Y-%m-%d"), "at": now.isoformat(timespec="seconds"), "model": model, "usd": usd,
                            "in": tin, "out": tout}) + "\n")


def decide(a: ItemAnalysis) -> dict:
    newsworthy = a.materiality in ("major", "significant") and not a.is_speculative and not a.is_opinion_or_feature
    return {"tier": a.materiality, "newsworthy": newsworthy, "major": newsworthy and a.materiality == "major"}


def run_rules(item: dict) -> dict:
    from app.newsdesk import newsworthy

    t0 = time.perf_counter()
    a = newsworthy.assess(item["title"], item.get("summary") or "")
    ms = (time.perf_counter() - t0) * 1000
    tier = {"major": "major", "strong": "significant"}.get(a.strength, "noise") if a.ok else ("notable" if a.strength != "none" else "noise")
    return {"ok": True, "ms": ms, "decision": {"tier": tier, "newsworthy": bool(a.ok), "major": bool(a.ok and a.strength == "major")},
            "why": a.reason or a.why, "in_tok": 0, "out_tok": 0, "usd": 0.0}


def run_ollama(model: str, item: dict, keep_alive: str) -> dict:
    import httpx

    body = {"model": model, "stream": False, "format": json_schema(), "keep_alive": keep_alive,
            "options": {"temperature": 0, "num_ctx": 4096},
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": user_message(item["title"], item.get("summary") or "", item.get("outlets"))}]}
    if model.startswith(("qwen3", "deepseek")):
        body["think"] = False
    elif model.startswith("gpt-oss"):
        body["think"] = "low"
    t0 = time.perf_counter()
    r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=300)
    ms = (time.perf_counter() - t0) * 1000
    r.raise_for_status()
    data = r.json()
    raw = data["message"]["content"]
    out = {"ms": ms, "in_tok": data.get("prompt_eval_count", 0), "out_tok": data.get("eval_count", 0), "usd": 0.0,
           "load_ms": data.get("load_duration", 0) / 1e6}
    try:
        a = ItemAnalysis.model_validate_json(raw)
    except Exception as e:  # schema-invalid output counts as a failure, scored as "not newsworthy"
        return {**out, "ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}", "raw": raw[:400],
                "decision": {"tier": "noise", "newsworthy": False, "major": False}}
    return {**out, "ok": True, "decision": decide(a), "analysis": a.model_dump()}


def _anthropic_client():
    # The official SDK's compiled dependencies are blocked by Windows Application Control on this PC (pydantic_core/jiter in a
    # fresh venv), so the benchmark calls the Messages API over HTTPS with httpx, as the app's existing call sites do.
    import httpx

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key and sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                key = winreg.QueryValueEx(k, "ANTHROPIC_API_KEY")[0]
        except OSError:
            key = None
    if not key:
        raise SystemExit("No ANTHROPIC_API_KEY (process or user environment): Claude runs skipped")
    return httpx.Client(base_url="https://api.anthropic.com", timeout=180,
                        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})


def run_claude(client, model: str, item: dict, effort: str) -> dict:
    body = {"model": model, "max_tokens": MAX_OUT, "system": SYSTEM,
            "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": json_schema()}},
            "messages": [{"role": "user", "content": user_message(item["title"], item.get("summary") or "", item.get("outlets"))}]}
    t0 = time.perf_counter()
    for attempt in range(4):
        r = client.post("/v1/messages", json=body)
        if r.status_code in (429, 500, 502, 503, 529) and attempt < 3:
            time.sleep(float(r.headers.get("retry-after", 2 ** attempt * 3)))
            continue
        break
    ms = (time.perf_counter() - t0) * 1000
    if r.status_code != 200:
        return {"ok": False, "ms": ms, "error": f"HTTP {r.status_code}: {r.text[:240]}", "in_tok": 0, "out_tok": 0, "usd": 0.0,
                "decision": {"tier": "noise", "newsworthy": False, "major": False}}
    msg = r.json()
    usage = msg.get("usage", {})
    pin, pout = PRICES.get(model, (4.0, 20.0))
    usd = usage.get("input_tokens", 0) * pin / 1e6 + usage.get("output_tokens", 0) * pout / 1e6
    out = {"ms": ms, "in_tok": usage.get("input_tokens", 0), "out_tok": usage.get("output_tokens", 0), "usd": usd,
           "stop": msg.get("stop_reason")}
    if msg.get("stop_reason") in ("refusal", "max_tokens"):
        return {**out, "ok": False, "error": msg.get("stop_reason"), "decision": {"tier": "noise", "newsworthy": False, "major": False}}
    text = "".join(b.get("text", "") for b in msg.get("content", []) if b.get("type") == "text")
    try:
        a = ItemAnalysis.model_validate_json(text)
    except Exception as e:
        return {**out, "ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}", "raw": text[:400],
                "decision": {"tier": "noise", "newsworthy": False, "major": False}}
    return {**out, "ok": True, "decision": decide(a), "analysis": a.model_dump()}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("engine")
    p.add_argument("out")
    p.add_argument("--eval", default=str(ROOT / "ops/intel/eval_v1.json"))
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--ids", default="")
    p.add_argument("--budget-usd", type=float, default=0.0, help="stop before a Claude run would exceed this total")
    p.add_argument("--effort", default="low")
    p.add_argument("--keep-alive", default="2m")
    p.add_argument("--ledger", default=str(ROOT / "ops/intel/spend_ledger.jsonl"), help="shared paid-call ledger ($3/day, $15 total)")
    args = p.parse_args()
    items = json.load(open(args.eval, encoding="utf-8"))["items"]
    if args.ids:
        want = set(args.ids.split(","))
        items = [i for i in items if i["id"] in want]
    if args.limit:
        items = items[: args.limit]
    done, spent = set(), 0.0
    if os.path.exists(args.out):
        for line in open(args.out, encoding="utf-8"):
            r = json.loads(line)
            done.add(r["id"])
            spent += r.get("usd", 0.0)
    client = _anthropic_client() if args.engine.startswith("claude:") else None
    with open(args.out, "a", encoding="utf-8") as f:
        for n, item in enumerate(items, 1):
            if item["id"] in done:
                continue
            if args.engine == "rules":
                res = run_rules(item)
            elif args.engine.startswith("ollama:"):
                res = run_ollama(args.engine.split(":", 1)[1], item, args.keep_alive)
            else:
                model = args.engine.split(":", 1)[1]
                pin, pout = PRICES.get(model, (4.0, 20.0))
                worst = (len(SYSTEM) + len(json.dumps(json_schema())) + len(item["title"]) + len(item.get("summary") or "")) / 3 * pin / 1e6 \
                    + MAX_OUT * pout / 1e6
                day, total = ledger_spent(args.ledger)
                if day + worst > DAY_CAP_USD or total + worst > TOTAL_CAP_USD:
                    print(f"cap reached: today ${day:.4f} / ${DAY_CAP_USD}, total ${total:.4f} / ${TOTAL_CAP_USD}")
                    break
                if args.budget_usd and spent + worst > args.budget_usd:
                    print(f"run budget reached: ${spent:.4f}")
                    break
                res = run_claude(client, model, item, args.effort)
                spent += res["usd"]
                if res.get("in_tok") or res.get("out_tok"):
                    ledger_add(args.ledger, model, res["usd"], res.get("in_tok", 0), res.get("out_tok", 0))
            rec = {"id": item["id"], "engine": args.engine, "prompt": PROMPT_VERSION, **res}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            if n % 25 == 0:
                print(f"{args.engine}: {n}/{len(items)} spent ${spent:.4f}", flush=True)
    print(f"done {args.engine} spent ${spent:.4f}")


if __name__ == "__main__":
    main()
