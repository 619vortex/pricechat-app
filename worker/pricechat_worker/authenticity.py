"""Visual authenticity score (Content Studio v4): is this an interesting, well-edited video of the real thing, or filler?

Measured on the finished timeline, per clip and overall:
  relevance       does each shot show what is being said over it (judged by the local model; falls back to the media match)
  authentic       share of screen time on real footage/photos of the story (exact or related), not synthetic or generic
  exact           share of screen time on the exact subject
  verified        share of used assets with a complete rights record (licence, source page, credit when required)
  resolution      how much each picture is enlarged on screen (over 1.6× looks soft)
  repetition      the same picture on screen again and again; identical crops reused
  filler          generic stock that could illustrate any story
  readability     caption length and size, how much of the video is covered by big graphics
  consistency     shot lengths that fit the style (no 0.5 s flickers, no 9 s holds)

Relevance weighs most: lots of authentic footage is not proof of quality if it doesn't match the words."""

import json
import re
import statistics
from typing import Any

from pricechat_worker import script2

FRAME_BOX = (1080, 900)
WEIGHTS = {"relevance": 30, "authentic": 18, "exact": 10, "verified": 10, "resolution": 10, "repetition": 8, "readability": 7, "consistency": 7}


def upscale(c: dict[str, Any], a: dict[str, Any]) -> float:
    w, h = max(1, int(a.get("width") or 1)), max(1, int(a.get("height") or 1))
    if c.get("fit") == "fill":
        s = max(1080 / w, 1920 / h)
    else:
        s = min(FRAME_BOX[0] / w, FRAME_BOX[1] / h)
    return round(s * max(1.0, float(c.get("zoom") or 1)), 2)


def _relevance_llm(job: dict[str, Any], pairs: list[tuple[str, str]]) -> list[int] | None:
    """0 = unrelated, 1 = loosely related, 2 = shows what the line is about. One call for the whole edit."""
    engines = script2._engines(job)
    if not engines or not pairs:
        return None
    listing = "\n".join(f"{i + 1}. LINE: {line or '(no narration)'} | SHOT: {shot}" for i, (line, shot) in enumerate(pairs))
    try:
        text = script2._call(engines[0], "You review video edits. Reply with JSON only.",
                             "For each numbered pair, score how well the SHOT illustrates the narration LINE spoken over it: 2 = it shows exactly "
                             "what the line is about, 1 = related context, 0 = unrelated or misleading.\n" + listing +
                             '\n\nReply {"scores": [..one integer per pair, in order..]}', 0.0)
        scores = json.loads(re.search(r"\{.*\}", text, re.S).group(0)).get("scores")  # type: ignore[union-attr]
        if isinstance(scores, list) and len(scores) == len(pairs):
            return [max(0, min(2, int(x))) for x in scores]
    except Exception:  # noqa: BLE001
        return None
    return None


def score(job: dict[str, Any], tl: dict[str, Any], assets: dict[str, dict[str, Any]], segments: list[dict[str, Any]],
          spans: list[tuple[float, float]], st: dict[str, Any], use_llm: bool = True) -> dict[str, Any]:
    clips = tl["video"]
    body = sum(c["dur"] for c in clips) or 1.0
    words = tl["captions"]["words"]

    def line_at(c: dict[str, Any]) -> str:
        mid = c["start"] + c["dur"] / 2
        for k, (a, b) in enumerate(spans):
            if a - 0.3 <= mid <= b + 0.3:
                return str(segments[k]["text"])
        near = min(range(len(spans)), key=lambda k: min(abs(spans[k][0] - mid), abs(spans[k][1] - mid))) if spans else None
        return str(segments[near]["text"]) if near is not None else ""

    per = []
    for c in clips:
        a = assets.get(c["asset"]) or {}
        per.append({"clip": c["id"], "asset": c["asset"], "dur": c["dur"], "match": a.get("match") or "unknown", "authentic": bool(a.get("authentic", True)),
                    "upscale": upscale(c, a), "line": line_at(c), "shows": a.get("shows") or a.get("title") or "", "kind": a.get("kind"),
                    "verified": bool(a.get("license")) and bool(a.get("page") or a.get("url")) and (bool(a.get("attribution")) or not (a.get("permitted") or {}).get("attribution"))})
    rel = _relevance_llm(job, [(p["line"], p["shows"]) for p in per]) if use_llm else None
    for i, p in enumerate(per):
        p["relevance"] = rel[i] if rel else {"exact": 2, "related": 1}.get(p["match"], 0)
    t = lambda pred: sum(p["dur"] for p in per if pred(p)) / body  # noqa: E731
    m = {
        "relevance": round(sum(p["relevance"] / 2 * p["dur"] for p in per) / body, 3),
        "authentic": round(t(lambda p: p["authentic"] and p["match"] in ("exact", "related")), 3),
        "exact": round(t(lambda p: p["match"] == "exact"), 3),
        "filler": round(t(lambda p: p["match"] == "generic"), 3),
        "synthetic": round(t(lambda p: not p["authentic"]), 3),
        "verified": round(sum(1 for p in per if p["verified"]) / max(1, len(per)), 3),
        "soft": round(t(lambda p: p["upscale"] > 1.6), 3),
        "video_share": round(t(lambda p: p["kind"] == "video"), 3),
    }
    by_asset: dict[str, float] = {}
    for p in per:
        by_asset[p["asset"]] = by_asset.get(p["asset"], 0) + p["dur"]
    crops = [(c["asset"], tuple(c.get("focus") or ()), c.get("zoom"), c.get("in")) for c in clips]
    m["top_asset_share"] = round(max(by_asset.values()) / body, 3) if by_asset else 0
    m["distinct_assets"] = len(by_asset)
    m["identical_crops"] = len(crops) - len(set(crops))
    durs = [c["dur"] for c in clips]
    m["shots"] = len(clips)
    m["avg_shot"] = round(statistics.mean(durs), 2) if durs else 0
    m["shortest"] = round(min(durs), 2) if durs else 0
    m["longest"] = round(max(durs), 2) if durs else 0
    phrases: dict[int, str] = {}
    for w in words:
        phrases[w["p"]] = (phrases.get(w["p"], "") + " " + w["t"]).strip()
    m["longest_caption"] = max((len(x) for x in phrases.values()), default=0)
    big = sum(o["dur"] for o in tl["overlays"] if o["type"] in ("chart", "facts", "title"))
    m["graphics_cover"] = round(big / body, 3)
    card = bool((job.get("brief") or {}).get("card"))
    rep_limit = 0.8 if card else 0.45  # a card story is shot on its own scan: several real details of one picture

    sub = {
        "relevance": m["relevance"],
        "authentic": m["authentic"],
        "exact": min(1.0, m["exact"] / 0.5),
        "verified": m["verified"],
        "resolution": 1 - m["soft"],
        "repetition": max(0.0, 1 - max(0.0, m["top_asset_share"] - rep_limit) * 2.5 - 0.15 * m["identical_crops"]),
        "readability": max(0.0, 1 - max(0, m["longest_caption"] - 40) / 30 - max(0.0, m["graphics_cover"] - 0.35) * 2),
        "consistency": max(0.0, 1 - (0.25 if m["shortest"] < 0.8 else 0) - (0.35 if m["longest"] > st["max_shot"] * 1.5 else 0)
                         - max(0.0, abs(m["avg_shot"] - st["avg_shot"]) / st["avg_shot"] - 0.4)),
    }
    total = round(sum(WEIGHTS[k] * max(0.0, min(1.0, v)) for k, v in sub.items()))
    flags = []
    if m["synthetic"] + m["filler"] > 0.4:
        flags.append("dominated by synthetic or generic visuals")
    if m["filler"] > 0.25:
        flags.append(f"{m['filler'] * 100:.0f}% generic filler footage")
    if m["relevance"] < 0.6:
        flags.append("shots often don't match the narration")
    if m["top_asset_share"] > rep_limit:
        flags.append(f"one picture fills {m['top_asset_share'] * 100:.0f}% of the video")
    if m["identical_crops"]:
        flags.append(f"{m['identical_crops']} identical shot(s) reused")
    if m["soft"] > 0.3:
        flags.append(f"{m['soft'] * 100:.0f}% of the video is enlarged more than 1.6× (soft)")
    if m["verified"] < 1:
        flags.append("an asset has an incomplete rights record")
    grade = "A" if total >= 85 else "B" if total >= 72 else "C" if total >= 58 else "D"
    return {"score": total, "grade": grade, "metrics": m, "parts": {k: round(v, 3) for k, v in sub.items()}, "weights": WEIGHTS, "flags": flags,
            "judge": "llm" if rel else "media match", "clips": per}
