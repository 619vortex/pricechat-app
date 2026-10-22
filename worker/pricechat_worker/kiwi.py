"""Kiwi hook writer (the "new" writer): writer (model, soft) -> validators (code, hard gate) -> critic (model, soft gate).

The prompt is short on purpose: a small local model follows a short prompt far more reliably than a long rule list.
Everything code can check (word caps, banned phrases and hype words, openers, numbers from the research, report verbs,
repeats of recent hooks, the visual format) is enforced in validators.py, and failures go back to the model as one
concrete fix. The visual format, the first-3-seconds beat sheet and the kill list are decided in code too. The critic
only judges what code can't: does the shape fit, is there tension, would a bored person stop scrolling.

Output (field names are fixed; the renderer and the backend read them):
  {hook, shape, on_screen_text, visual_format, first_3_seconds, caption, killed[], pick_reason}  or  {hold, reason}"""

import json
import re
from typing import Any, Callable

from pricechat_worker import script2, validators as v

WRITER = """You are Kiwi, the voice of Price Chat: dry, unimpressed by big numbers, on the side of whoever is holding the bag.
Thesis: Everything has a price. Somebody is on the wrong side of it.
Every piece answers one question: why did the number move, and who is on the wrong side of it? Have an opinion. Don't report.

Use only the FACTS and DERIVED lines you are given. Copy their numbers exactly. Never calculate anything yourself.

Write five candidates, one per shape:
NUMBER SHOCK: start with the number.
COMPARISON: two numbers side by side (a DERIVED line counts).
CONTRADICTION: two true facts pulling opposite ways, from the same period.
TIME JUMP: anchored to a past date.
STAKES: start with the group on the wrong side of the number.

Bad hook: "Rocket's Mewtwo PSA 10 sells for $17,225" (a receipt). Good: "$6,750 in April. $17,225 in October." (a gap).
Bad on-screen text: "Gem Mint" (a label). Good: "Somebody sold in April." (the stake).

Reply with JSON only:
{"candidates": [{"shape": "NUMBER SHOCK", "hook": "...", "on_screen_text": "...", "caption": "the fact and its source, then one question about the viewer's own taste or experience"}]}"""

CRITIC = """You pick the best hook for a short finance video (Price Chat). Code already checked word counts, banned words,
openers, duplicates, periods and that every number is real: don't re-check those.
Judge only: does the shape fit this story, is there real tension (a gap, a loss, a contradiction) or is it a summary,
and would a bored person stop scrolling on a Tuesday afternoon?
Pick the candidate with the most tension. PASS if it would stop a bored person; REWRITE if even the best is a summary.
PASS example: "$6,750 in April. $17,225 in October." (a gap). REWRITE example: "Bitcoin hit $83,000." (a report).
Reply with JSON only: {"pick": <candidate number>, "verdict": "PASS" or "REWRITE", "fix": "if REWRITE: one concrete change to the pick, one sentence"}"""

# shape -> (primary, alternate) visual format; the code picks, the model never does
FORMAT_OF = {"NUMBER SHOCK": ("PRICE SLAM", "RECEIPT"), "COMPARISON": ("THEN/NOW", "CHART DRAW"), "CONTRADICTION": ("CHART DRAW", "THEN/NOW"),
             "TIME JUMP": ("RECEIPT", "THEN/NOW"), "STAKES": ("STAKE LINE", "PRICE SLAM")}
RANK = {"STAKES": 5, "COMPARISON": 5, "CONTRADICTION": 4, "TIME JUMP": 4, "NUMBER SHOCK": 3}
KILL = {"word_cap": "On-screen over six words", "hook_length": "Hook over twelve words", "banned_phrases": "Banned phrase",
        "opener": "Opens with This/It", "opener_screen": "On-screen opens This/It", "hype_words": "Hype word, not number",
        "number_present": "No verified number", "caption_numbers": "Unverified number in caption", "report_verb": "Reports, doesn't provoke",
        "duplicate": "Repeats a recent hook", "shape_signal": "Hook doesn't fit shape", "substance": "Bare number, no subject",
        "period_mix": "Two periods, one frame", "caption_question": "Money question in caption",
        "unsupported_loss": "Claims an unshown loss", "critic": "Critic: no tension"}


def facts_of(brief: dict[str, Any]) -> list[str]:
    out = ["HEADLINE: " + str(brief.get("headline") or "")] if brief.get("headline") else []  # a framing, not a dated fact
    out += [str(f.get("text") if isinstance(f, dict) else f) for f in brief.get("facts") or []]
    out += [f"{k.replace('_', ' ')}: {val}" for k, val in (brief.get("numbers") or {}).items() if isinstance(val, (int, float))]
    return [x for x in out if x.strip()]


def derived_of(brief: dict[str, Any]) -> list[str]:
    """The fact card: sent by the backend with the job; computed locally when the backend code is importable (bench, CLI)."""
    if "derived" in brief:
        return list(brief.get("derived") or [])
    try:
        from app.brand_manager import factcard

        return factcard.derive(brief)["derived"]
    except ImportError:
        return []


def _call(job: dict[str, Any], system: str, user: str, temperature: float) -> dict[str, Any]:
    engine = script2._engines(job)[0]
    text = script2._call(engine, system, user, temperature, json_mode=True)
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


def trend_line(job: dict[str, Any]) -> str:
    """The live trend words this story contains (backend trend scan): people search and scroll for them today."""
    words = [str(w) for w in job.get("trends") or [] if str(w).strip()][:3]
    return ("TRENDING NOW (words many finance headlines use today, and they are in this story): " + ", ".join(words)
            + ". Put one in the hook or the on-screen text if it fits naturally. It adds no facts.") if words else ""


def _user(job: dict[str, Any], facts: list[str], derived: list[str]) -> str:
    trend = trend_line(job)
    return ("STORY: " + str((job.get("brief") or {}).get("headline") or job.get("topic") or "") + "\n\nFACTS:\n" + "\n".join(f"- {f}" for f in facts)
            + ("\n\nDERIVED (already calculated, quote as written):\n" + "\n".join(f"- {d}" for d in derived) if derived else "")
            + (f"\n\n{trend}" if trend else ""))


def candidates(job: dict[str, Any], facts: list[str], derived: list[str], fixes: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    user = _user(job, facts, derived)
    if fixes:  # the repair round: only the candidates that failed a hard gate, each with its one concrete fix
        user += "\n\nThese candidates failed a check. Return corrected versions (same shapes), in the same JSON shape:\n" + "\n".join(
            f"- {f['shape']}: \"{f['hook']}\" / on-screen \"{f['on_screen_text']}\" -> fix: {f['fix']}" for f in fixes)
    try:
        data = _call(job, WRITER, user, 0.7 if not fixes else 0.4)
    except Exception:  # noqa: BLE001 - no model: no candidates (the caller falls back to the old writer)
        return []
    out = []
    for c in data.get("candidates") or []:
        if isinstance(c, dict) and str(c.get("hook") or "").strip():
            shape = str(c.get("shape") or "").upper().replace("_", " ").strip()
            out.append({"shape": shape if shape in v.SHAPES else "NUMBER SHOCK", "hook": str(c["hook"]).strip(),
                        "on_screen_text": str(c.get("on_screen_text") or "").strip(), "caption": str(c.get("caption") or "").strip()})
    return out


def critic(job: dict[str, Any], cands: list[dict[str, Any]], facts: list[str], derived: list[str] = (),
           rejected: list[dict[str, Any]] = ()) -> dict[str, Any]:
    """One call: the candidates that passed every hard check, side by side (a small model compares far more reliably than it
    grades). Returns {"pick": index, "verdict": PASS|REWRITE, "fix": one change}."""
    user = ("STORY FACTS:\n" + "\n".join(f"- {f}" for f in facts[:8])
            + ("\nDERIVED (already calculated):\n" + "\n".join(f"- {d}" for d in derived) if derived else "")
            + "\n\nCANDIDATES (all passed the code checks):\n"
            + "\n".join(f"{i + 1}. [{c['shape']}] hook: \"{c['hook']}\" / on-screen: \"{c['on_screen_text']}\"" for i, c in enumerate(cands)))
    if rejected:  # so the fix never asks for something the hard gate will kill (e.g. two periods set side by side)
        user += "\n\nCODE REJECTED THESE (never suggest them or anything like them):\n" + "\n".join(f"- \"{r['hook']}\": {r['why']}" for r in rejected[:6])
    user += "\nA fix may only use the numbers above and must not add adjectives."
    try:
        ans = _call(job, CRITIC, user, 0.0)
    except Exception:  # noqa: BLE001 - no critic available: the hard gate already passed, take the code's top-ranked hook
        return {"pick": 0, "verdict": "PASS", "fix": "", "note": "critic unavailable"}
    try:
        pick = min(max(int(ans.get("pick") or 1) - 1, 0), len(cands) - 1)
    except (TypeError, ValueError):
        pick = 0
    verdict = "PASS" if str(ans.get("verdict") or "").upper().startswith("PASS") else "REWRITE"
    return {"pick": pick, "verdict": verdict, "fix": str(ans.get("fix") or "")[:200]}


def pick_format(shape: str, history: list[dict[str, Any]], last_n: int = 3) -> str:
    """The shape's primary format, else its alternate, else any format not used in the last `last_n` videos."""
    for f in FORMAT_OF.get(shape, ("PRICE SLAM", "RECEIPT")) + v.FORMATS:
        if v.check_format_repeat(f, history, last_n)[0]:
            return f
    return FORMAT_OF.get(shape, ("PRICE SLAM",))[0]


def beat_sheet(fmt: str, hook: str, screen: str) -> str:
    """first_3_seconds, written by code from the format (the renderer's own timings)."""
    lead = {"PRICE SLAM": "0.0 cut in on the exact subject; 0.4 the verified price card lands lower-left",
            "THEN/NOW": "0.0 the subject; 0.6 the earlier price card (grey) lower-left; 1.8 the newer one stacks above it",
            "CHART DRAW": "0.0 dimmed subject footage; 0.2 the real chart draws left to right; 1.8 the key point is labelled",
            "RECEIPT": "0.0 the record card (date, price, source) as the cold open; 2.0 cut to the subject",
            "STAKE LINE": "0.0 full-bleed subject; 0.3 the on-screen line top-left; 1.8 the number as a lower third"}[fmt]
    return f"{lead}. VO from 0.2: \"{hook}\". On-screen: \"{screen}\"."


def plan(job: dict[str, Any], history: list[dict[str, Any]] | None = None, step: Callable[[str, str, str], None] | None = None,
         max_critic: int = 3) -> dict[str, Any]:
    """writer -> hard gate -> (one repair round) -> rank -> critic. Returns the pick or {"hold": True, "reason": ...}."""
    history = history if history is not None else list(job.get("history") or [])
    brief = job.get("brief") or {}
    facts, derived = facts_of(brief), derived_of(brief)
    log: list[dict[str, Any]] = []
    cands = candidates(job, facts, derived)
    if not cands:
        return {"hold": True, "reason": "the writer returned no candidates", "log": log}
    killed: list[dict[str, str]] = []

    def gated(cs: list[dict[str, Any]]) -> tuple[list[tuple[dict[str, Any], list]], list[tuple[dict[str, Any], list]]]:
        good, bad = [], []
        for c in cs:
            res = v.gate(c, facts, derived, history)
            log.append({"hook": c["hook"], "shape": c["shape"], "failed": [f"{n}: {r}" for n, ok, r in res if not ok]})
            (good if all(ok for _, ok, _ in res) else bad).append((c, res))
        return good, bad

    rejected: list[dict[str, Any]] = []
    good, bad = gated(cands)
    rejected += [{"hook": c["hook"], "why": "; ".join(r for _, ok, r in res if not ok)} for c, res in bad]
    if bad:  # one repair round, one model call, each failure with its concrete fix
        fixes = [{**c, "fix": "; ".join(r for _, ok, r in res if not ok)} for c, res in bad]
        repaired = candidates(job, facts, derived, fixes)
        good2, bad2 = gated(repaired)
        good += good2
        rejected += [{"hook": c["hook"], "why": "; ".join(r for _, ok, r in res if not ok)} for c, res in bad2]
        for c, res in bad2 if repaired else bad:
            first = next(n for n, ok, _ in res if not ok)
            killed.append({"shape": c["shape"], "hook": c["hook"], "reason": KILL.get(first, "Failed a hard check")})
    if step:
        step("script", "running", f"Kiwi: {len(good)} of {len(cands)} hooks passed the hard checks")
    uses = lambda c: any(any(abs(n - d) < 1e-9 for d in v.numbers(" ".join(derived))) for n in v.numbers(c["hook"]))  # noqa: E731
    good.sort(key=lambda cr: (-(RANK.get(cr[0]["shape"], 3) + (1 if uses(cr[0]) else 0)), len(v.words(cr[0]["hook"]))))
    pick = None
    if good:
        pool = good[:max_critic + 2]
        verdict = critic(job, [c for c, _ in pool], facts, derived, rejected)
        best, res = pool[verdict["pick"]]
        log.append({"critic": verdict, "hook": best["hook"]})
        if verdict["verdict"] == "PASS":
            pick = (best, res, verdict)
        elif verdict.get("fix"):  # one rewrite of the pick with the critic's single fix; it goes back through the hard gate
            fixed = gated(candidates(job, facts, derived, [{**best, "fix": verdict["fix"]}]))[0][:1]
            if fixed:
                again = critic(job, [best, fixed[0][0]], facts, derived, rejected)
                log.append({"critic": again, "hook": [best, fixed[0][0]][again["pick"]]["hook"]})
                if again["verdict"] == "PASS":
                    pick = (fixed[0] if again["pick"] == 1 else (best, res)) + (again,)
        if not pick:
            killed += [{"shape": c["shape"], "hook": c["hook"], "reason": KILL["critic"]} for c, _ in pool]
    if not pick:
        return {"hold": True, "reason": "no hook passed the hard checks and the critic", "killed": killed, "log": log}
    c, res, verdict = pick
    for oc, _ in good:
        if oc is not c and not any(k["hook"] == oc["hook"] for k in killed):
            killed.append({"shape": oc["shape"], "hook": oc["hook"], "reason": "Ranked below the pick"})
    fmt = pick_format(c["shape"], history)
    out = {"hook": c["hook"], "shape": c["shape"], "on_screen_text": c["on_screen_text"], "visual_format": fmt,
           "first_3_seconds": beat_sheet(fmt, c["hook"], c["on_screen_text"]), "caption": c["caption"], "killed": killed,
           "pick_reason": f"{c['shape']}: passed {len(res)} hard checks; critic PASS" + (f" ({verdict['note']})" if verdict.get("note") else ""),
           "log": log}
    ok, why = v.check_schema(out)
    if not ok:
        return {"hold": True, "reason": f"schema: {why}", "log": log}
    return out
