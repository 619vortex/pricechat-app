"""Script v4 (Content Studio v4): written for the footage, not the other way round.

The writer gets the shots the media research actually found (what each one shows, how long it can run) and the verified
graphics available (a price, a real chart, a record card). It returns one short spoken line per shot group, so every
sentence lands on a picture that shows what it says. Delivery is understated: a person who knows the subject, talking
plainly. The fact rules and the backend's checks are the same as v2's."""

import json
import re
from typing import Any

from pricechat_worker import editorial, kiwi, llm, script2

WPS = 2.45  # words per second for a calm, natural read


def overlay_menu(brief: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, c in editorial.callouts(brief).items():
        if not any(k.endswith(u) for u, _, _ in editorial.UNITS[:-1]):  # a bare count ("3 robots") is said, not put on a card
            continue
        out[f"price:{k}"] = f"shows {c['label']} {c['value']} on screen as it is said"
    if editorial.chart_of(brief):
        out["chart"] = "draws the real data: " + (editorial.chart_of(brief) or {}).get("title", "")
    title, rows, _ = editorial.facts_rows(brief)
    if rows:
        out["facts"] = f"a record card: {title} (" + ", ".join(f"{r['k']} {r['v']}" for r in rows) + ")"
    out["lower_third"] = "names the subject on screen"
    out["none"] = "just the picture"
    return out


def _prompt(job: dict[str, Any], shots: list[dict[str, Any]], st: dict[str, Any], feedback: list[str]) -> tuple[str, str]:
    b, strat, brief = job["brand"], job["strategy"], job["brief"]
    secs = (job.get("creative") or {}).get("seconds")
    lo, hi = (int(secs) - 4, int(secs) + 4) if secs else (int(strat["min_seconds"]), int(strat["max_seconds"]))
    words = int((lo + hi) / 2 * WPS)
    menu = overlay_menu(brief)
    cta = b.get("cta") if b.get("cta") and strat.get("outro_cta") else ""
    shot_lines = "\n".join(f"- {s['id']}: {s['kind']}, {s['match']} subject — {s['shows']}" + (f" (up to {s['max']:.0f}s)" if s["kind"] == "video" else "")
                           for s in shots)
    system = (
        f"You are the editor and narrator of short vertical videos for {b['name']} ({b['personality']}). Audience: {b['audience']}. "
        f"Language: {b['language']}.\nYou write AFTER looking at the footage: every line is said over a shot that shows what the line is about.\n\n"
        "FACT RULES (mandatory; the script is rejected otherwise):\n"
        "- Use ONLY facts in the brief. Never add a number, price, date, name, quote, comparison or claim it doesn't give.\n"
        "- Say each number exactly as the brief gives it. Never call something rare, a record, the most/biggest/first unless the brief says so.\n"
        "- No predictions, no advice, no hype words. Attribute news to its source. Opinions must be clearly opinions.\n"
        "- Describe only what the shots really show. Never claim a shot shows something it doesn't (a related shot is context, not the subject).\n\n"
        f"VOICE: {st['tone']}\n"
        "Natural and conversational: contractions, plain words, varied sentence length. No dramatic emphasis on every sentence, no "
        "rhetorical questions in a row, no 'Imagine', 'Did you know', 'Let that sink in', 'You won't believe'. Sound like a person, not an ad.\n\n"
        "THE EDIT:\n"
        "- Open on the strongest shot of the exact subject with the most interesting true fact, in under 12 words.\n"
        "- Then context, the detail that makes it interesting, and a natural close" + (f" (then: {cta})" if cta else "") +
        ", with at most one short comment question about the viewer's taste or experience (never about prices or what to buy).\n"
        "- Each segment: 1 sentence (5-20 words) and 1-2 shot ids that fit it. Use exact-subject shots for lines about the subject.\n"
        "- A video shot can carry a line up to its length. Don't reuse a shot unless you've run out.\n"
        f"- \"overlay\": one of {', '.join(menu)} (each price/chart/facts at most once):\n  " + "\n  ".join(f"{k} = {v}" for k, v in menu.items()) + "\n"
        f"- About {words} words in total ({lo}-{hi} seconds at a calm pace). 5 to 8 segments.\n"
        f"- Never use: {', '.join(llm.BANNED_PHRASES + script2.EXTRA_BANNED)}.\n"
    )
    if b.get("examples"):
        system += "\nApproved examples of the brand's voice (match the voice, not the words):\n" + "\n".join(f"- {e[:300]}" for e in b["examples"])
    user = (
        f"Topic: {job['topic']}\nBrief (the only facts you may use):\n"
        f"{json.dumps({k: brief.get(k) for k in ('headline', 'subject', 'facts', 'numbers', 'card', 'sources', 'note')}, ensure_ascii=False)}\n\n"
        f"SHOTS AVAILABLE (from the media research; best first):\n{shot_lines}\n\n"
        'Return JSON only: {"segments": [{"beat": "hook|context|development|payoff|ending", "shots": ["s1"], "text": "spoken line", '
        '"overlay": "none", "emphasis": ["word"]}], "title": "max 70 chars, the key fact, no clickbait", '
        '"description": "1 sentence with context that cites the source, then the comment question", "hashtags": ["#two", "#specific"]}'
    )
    trend = kiwi.trend_line(job)
    if trend:
        user += f"\n\n{trend} The title and description may use it too."
    hook = (job.get("plan") or {}).get("hook")
    if hook:  # the new writer chose the hook: the narration opens with it, word for word
        user += f'\n\nThe first segment\'s text must be exactly: "{hook}"'
    if feedback:
        user += "\n\nYour previous script was rejected. Fix ALL of these:\n" + "\n".join(f"- {f}" for f in feedback)
    return system, user


def _parse(text: str, shots: list[dict[str, Any]], menu: dict[str, str]) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON in the answer")
    data = json.loads(m.group(0))
    ids = {s["id"] for s in shots}
    segs = []
    for s in data.get("segments") or []:
        if not isinstance(s, dict) or not str(s.get("text", "")).strip():
            continue
        sh = [str(x) for x in (s.get("shots") or []) if str(x) in ids][:3]
        ov = str(s.get("overlay") or "none")
        segs.append({"beat": str(s.get("beat") or ""), "shots": sh, "text": str(s["text"]).replace("*", "").strip(), "overlay": ov if ov in menu else "none",
                     "emphasis": [str(w) for w in (s.get("emphasis") or [])][:2], "visual": "footage", "screen": ""})
    if len(segs) < 3:
        raise ValueError("too few segments")
    seen: set[str] = set()
    for s in segs:  # each graphic once
        if s["overlay"] != "none" and s["overlay"].split(":")[0] in seen:
            s["overlay"] = "none"
        seen.add(s["overlay"].split(":")[0])
    require(segs, menu)
    return {"hook": segs[0]["text"], "segments": segs, "title": str(data.get("title") or "").strip(), "description": str(data.get("description") or "").strip(),
            "hashtags": [str(h) for h in data.get("hashtags") or []][:8]}


def require(segs: list[dict[str, Any]], menu: dict[str, str]) -> None:
    """The real data must be seen: a story with a verified chart shows it, and its key price is on screen when it's said."""
    used = {s["overlay"].split(":")[0] for s in segs}
    free = [s for s in segs[1:-1] if s["overlay"] == "none"] or [s for s in segs[1:] if s["overlay"] == "none"]
    if "chart" in menu and "chart" not in used and free:
        pick = next((s for s in free if re.search(r"\d", s["text"])), free[0])
        pick["overlay"] = "chart"
    prices = [k for k in menu if k.startswith("price:")]
    if prices and "price" not in used:
        for s in segs:
            if s["overlay"] == "none":
                key = next((k for k in prices if re.sub(r"[^\d.]", "", menu[k].split(" on screen")[0].split()[-1]).rstrip(".") in re.sub(r"[^\d. ]", "", s["text"])), None)
                if key:
                    s["overlay"] = key
                    break


def template(job: dict[str, Any], shots: list[dict[str, Any]]) -> dict[str, Any]:
    """No AI: the brief's own facts, one per shot, said plainly."""
    brief = job["brief"]
    facts = [str(f.get("text") if isinstance(f, dict) else f) for f in brief.get("facts") or []]
    lines = [str(brief.get("headline") or job["topic"]).rstrip(".") + "."] + [f if f.endswith(".") else f + "." for f in facts[:5]]
    src = [s.get("name") for s in brief.get("sources") or [] if s.get("name")]
    if src:
        lines.append(f"Source: {', '.join(dict.fromkeys(src))}.")
    menu = overlay_menu(brief)
    segs = []
    for k, line in enumerate(lines):
        ov = "lower_third" if k == 1 else next((m for m in menu if m.startswith("price:")), "none") if k == 2 else "none"
        segs.append({"beat": "hook" if k == 0 else "ending" if k == len(lines) - 1 else "development", "shots": [shots[k % len(shots)]["id"]], "text": line,
                     "overlay": ov, "emphasis": [], "visual": "footage", "screen": ""})
    return {"hook": lines[0], "segments": segs, "title": str(brief.get("headline") or job["topic"])[:70], "description": lines[0], "hashtags": [],
            "engine": "template"}


def write(job: dict[str, Any], shots: list[dict[str, Any]], st: dict[str, Any], feedback: list[str], attempt: int) -> dict[str, Any]:
    menu = overlay_menu(job["brief"])
    errors = []
    for engine in script2._engines(job):
        system, user = _prompt(job, shots, st, feedback)
        try:
            text = script2._call(engine, system, user, temperature=0.55 + 0.05 * attempt, json_mode=False)
            out = _parse(text, shots, menu)
            return apply_plan({**out, "engine": engine}, job.get("plan"))
        except Exception as e:  # noqa: BLE001 - try the next engine, then the template
            errors.append(f"{engine}: {type(e).__name__}: {str(e)[:120]}")
    return {**template(job, shots), "engine_errors": errors}


PLAN_FIELDS = ("shape", "on_screen_text", "visual_format", "first_3_seconds", "caption", "killed", "pick_reason")


def apply_plan(script: dict[str, Any], plan: dict[str, Any] | None) -> dict[str, Any]:
    """The new writer's hook layer on the existing script fields: the hook opens the narration and is the hook and title,
    the caption is the description. Existing field names are unchanged; the plan's own fields ride along."""
    if not plan or plan.get("hold") or not plan.get("hook"):
        return script
    segs = [dict(s) for s in script["segments"]]
    if segs:
        segs[0]["text"] = plan["hook"]
    return {**script, "segments": segs, "hook": plan["hook"], "title": plan["hook"][:70], "description": plan["caption"] or script.get("description", ""),
            **{k: plan[k] for k in PLAN_FIELDS if k in plan}, "writer": "new"}


def finish(script: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    return script2.finish(script, job)


VIEWER_QUESTION = re.compile(r"^\W*(do|would|have|are|did|will|could|can) you\b|what do you think|let me know|comment below", re.I)


def style_problems(script: dict[str, Any]) -> list[str]:
    """v2's style checks minus the visual-repeat rule (shots repeat by design: two details of one card are two shots)."""
    out = [p for p in script2.style_problems({**script, "segments": [{**s, "visual": "punchline"} for s in script["segments"]]})]
    texts = [s["text"] for s in script["segments"]]
    if sum(t.count("!") for t in texts) > 1:
        out.append("Drop the exclamation marks: say it calmly.")
    if sum(1 for t in texts if t.rstrip().endswith("?")) > 1:
        out.append("At most one question in the whole script.")
    if any(VIEWER_QUESTION.search(t) for t in texts):
        out.append("Don't ask the viewer about themselves (\"Do you...?\", \"Would you...?\"): end on a fact from the story.")
    if any(not s.get("shots") for s in script["segments"]):
        out.append("Every segment needs at least one shot id from the list.")
    return out


# --- CLI: python -m pricechat_worker.script4 --writer=old|new [--ab] [--stories bench_stories.json] [--out ab_compare.json] -----
def _stories(path: str | None) -> dict[str, dict[str, Any]]:
    """The benchmark stories (bench_v4.py) with the shots their last render found (out/bench_v4/<name>.json)."""
    import sys
    from pathlib import Path

    here = Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(here), str(here.parent / "backend")]
    argv, sys.argv = sys.argv, [sys.argv[0]]
    import bench_v4  # noqa: PLC0415 - the benchmark briefs live there

    sys.argv = argv
    research = json.loads(Path(path or here / "bench_stories.json").read_text(encoding="utf-8"))
    out = {"mewtwo": bench_v4.MEWTWO, **{k: bench_v4.from_research(k, research) for k in ("stock", "ai", "car", "dubai") if k in research}}
    for name, job in out.items():
        rep = here / "out" / "bench_v4" / f"{name}.json"
        shots = json.loads(rep.read_text(encoding="utf-8"))["meta"]["shots"] if rep.exists() else []
        job["_shots"] = shots or [{"id": "s1", "kind": "image", "match": "exact", "shows": job["brief"].get("headline", ""), "max": 99.0}]
    return out


def run(job: dict[str, Any], writer: str, history: list[dict[str, Any]]) -> dict[str, Any]:
    """One story through one writer: the hook layer, the full script, and the backend's own fact check of it."""
    from pricechat_worker import editorial, kiwi, validators

    st = editorial.style_of(job)
    plan = kiwi.plan(job, history) if writer == "new" else None
    script = finish(write({**job, "plan": plan}, job["_shots"], st, [], 1), job)
    try:
        from app.brand_manager import safety
        from app.brand_manager.defaults import STRATEGY

        fact_check = safety.check(script, job["brief"], {**STRATEGY}, [])
    except ImportError:
        fact_check = ["backend not importable"]
    hook = script.get("hook") or ""
    screen = script.get("on_screen_text") or next((s.get("screen") for s in script["segments"] if s.get("screen")), "") or script.get("title", "")
    facts, derived = kiwi.facts_of(job["brief"]), kiwi.derived_of(job["brief"])
    gates = validators.gate({"hook": hook, "on_screen_text": screen, "caption": script.get("description", ""), "shape": script.get("shape", "")},
                            facts, derived, history)
    used = "new" if script.get("writer") == "new" else ("old (Kiwi held: " + str((plan or {}).get("reason")) + ")" if writer == "new" else "old")
    return {"writer_used": used, "hook": hook, "on_screen_text": screen, "caption": script.get("description", ""), "title": script.get("title"),
            "shape": script.get("shape"), "visual_format": script.get("visual_format"), "first_3_seconds": script.get("first_3_seconds"),
            "killed": script.get("killed"), "pick_reason": script.get("pick_reason"),
            "hard_gate": {n: (ok, r) for n, ok, r in gates}, "hard_gate_failed": [f"{n}: {r}" for n, ok, r in gates if not ok],
            "backend_fact_check": fact_check or "pass", "plan": plan, "derived": derived}


def main(argv: list[str] | None = None) -> int:
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description="Price Chat script writer (old: script v4; new: Kiwi hook layer + validators + critic)")
    ap.add_argument("--writer", choices=("old", "new"), default="old")
    ap.add_argument("--ab", action="store_true", help="run both writers on the same stories and write ab_compare.json")
    ap.add_argument("--stories", default=None, help="bench_stories.json")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--out", default="ab_compare.json")
    a = ap.parse_args(argv)
    stories = _stories(a.stories)
    names = a.only or list(stories)
    writers = ("old", "new") if a.ab else (a.writer,)
    history: dict[str, list[dict[str, Any]]] = {w: [] for w in writers}
    result = []
    for name in names:
        row: dict[str, Any] = {"story": name, "headline": stories[name]["brief"].get("headline")}
        for w in writers:
            out = run(stories[name], w, history[w])
            row[w] = out
            if out.get("hook"):  # the next story sees this one as published (duplicates, format repeats)
                history[w].insert(0, {"hook": out["hook"], "visual_format": out.get("visual_format") or ""})
            print(f"[{w}] {name} <{out['writer_used']}>: {out['hook']!r} | on-screen {out['on_screen_text']!r} | {out.get('visual_format')} | gate fails {out['hard_gate_failed']}"
                  f" | fact check {out['backend_fact_check']}", flush=True)
        result.append(row)
    Path(a.out).write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
