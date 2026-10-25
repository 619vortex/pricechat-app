"""Content Studio v4: the media library and the editable timeline (docs/CONTENT_STUDIO_V4.md).

The worker researches authentic media for each story and stores it in `bm_videos.media` with a rights record per asset;
the owner can add assets they hold a licence for, or exclude ones they don't want. The timeline (`bm_videos.timeline`) is
the edit the worker made; the owner can trim, reorder, replace and reframe shots, change graphics and narration, and send
it back for a final render. Everything is validated here: shots only use assets from the library, and graphics never show
a number the research doesn't contain."""

import hashlib
import re
from typing import Any

from fastapi import HTTPException

from app.brand_manager import safety
from app.db.base import utcnow
from app.models.brand_manager import BmVideo

FITS = ("fill", "frame")
MOVES = ("none", "push", "pull", "pan_left", "pan_right", "pan_up", "pan_down")
OVERLAYS = ("title", "lower_third", "price", "chart", "facts", "source", "text")
CAPTIONS = ("clean", "boxed", "none")
MATCHES = ("exact", "related", "generic")
FREE = re.compile(r"^(CC0|CC[- ]?BY(-SA)?([- ]\d(\.\d)?)?|Public domain|PDM|Pexels License|Pixabay Content License|Licensed|Owned|Editorial use)", re.I)
TEXT_LIMIT = {"text": 160, "kicker": 30, "name": 60, "detail": 80, "label": 30, "value": 24, "note": 80, "title": 80, "footer": 100}


def assets_of(v: BmVideo) -> list[dict[str, Any]]:
    return [a for a in (v.media or {}).get("assets") or [] if isinstance(a, dict) and a.get("id")]


def add_asset(v: BmVideo, data: dict[str, Any]) -> dict[str, Any]:
    """An asset the owner adds: they attest the licence; it's recorded as theirs and never replaced by research."""
    if not data.get("attest"):
        raise HTTPException(400, "Confirm that you hold the rights to use this media.")
    url = str(data.get("url") or "")
    if not url.startswith("https://"):
        raise HTTPException(400, "The media URL must be https.")
    lic = str(data.get("license") or "").strip()
    if not lic or re.search(r"\b(NC|ND)\b", lic) or not FREE.match(lic):
        raise HTTPException(400, "Give the licence: CC0, CC BY, CC BY-SA, public domain, Pexels/Pixabay, 'Licensed' (you bought it), "
                                 "'Owned' (you made it) or 'Editorial use'. NonCommercial and NoDerivatives licences can't be used.")
    kind = data.get("kind") if data.get("kind") in ("video", "image") else ("video" if re.search(r"\.(mp4|webm|mov|m4v|ogv)(\?|$)", url, re.I) else "image")
    creator = str(data.get("creator") or "")[:80]
    aid = "a" + hashlib.sha1(url.encode()).hexdigest()[:10]
    attribution_needed = not re.match(r"^(CC0|Public domain|PDM|Owned|Licensed|Pexels|Pixabay)", lic, re.I)
    rec = {"id": aid, "kind": kind, "provider": "library", "origin": "owner", "url": url, "page": str(data.get("page") or "")[:300],
           "title": str(data.get("title") or url.rsplit("/", 1)[-1])[:120], "creator": creator, "rights_holder": str(data.get("rights_holder") or creator)[:80],
           "license": lic[:60], "license_url": str(data.get("license_url") or "")[:300],
           "permitted": {"commercial": not lic.lower().startswith("editorial"), "modify": True, "attribution": attribution_needed,
                         "editorial_only": lic.lower().startswith("editorial")},
           "attribution": str(data.get("attribution") or (f"{'Video' if kind == 'video' else 'Photo'}: {creator} · {lic}" if attribution_needed and creator else ""))[:120],
           "match": data.get("match") if data.get("match") in MATCHES else "exact", "shows": str(data.get("shows") or "")[:160],
           "authentic": True, "status": "ok", "score": 0.9, "added_at": utcnow().isoformat(), "note": "added by the owner"}
    cur = [a for a in assets_of(v) if a["id"] != aid]
    v.media = {"assets": [rec] + cur, "updated_at": utcnow().isoformat()}
    return rec


def set_asset(v: BmVideo, aid: str, data: dict[str, Any]) -> dict[str, Any]:
    cur = assets_of(v)
    hit = next((a for a in cur if a["id"] == aid), None)
    if hit is None:
        raise HTTPException(404, "No such asset in this video's media library.")
    if data.get("status") in ("ok", "excluded"):
        if data["status"] == "excluded" and aid in {c.get("asset") for c in (v.timeline or {}).get("video") or []}:
            raise HTTPException(409, "This asset is used in the timeline: replace its shots first.")
        hit["status"] = data["status"]
    if data.get("match") in MATCHES:
        hit["match"] = data["match"]
    v.media = {"assets": cur, "updated_at": utcnow().isoformat()}
    return hit


def _num(x: Any, lo: float, hi: float, name: str) -> float:
    try:
        f = float(x)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{name} must be a number.") from None
    if not lo <= f <= hi:
        raise HTTPException(400, f"{name} must be between {lo:g} and {hi:g}.")
    return f


def validate(v: BmVideo, tl: dict[str, Any]) -> dict[str, Any]:
    """The owner's edited timeline, cleaned: known assets only, sane times and framing, verified numbers only."""
    lib = {a["id"]: a for a in assets_of(v) if a.get("status") != "excluded"}
    raw = tl.get("video") or []
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, "The timeline needs at least one shot.")
    if len(raw) > 60:
        raise HTTPException(400, "At most 60 shots.")
    clips = []
    for i, c in enumerate(raw):  # list order is the cut (starts are recomputed below)
        aid = str(c.get("asset") or "")
        if aid not in lib:
            raise HTTPException(400, f"Shot {i + 1}: that asset isn't in the media library (or was excluded).")
        a = lib[aid]
        dur = _num(c.get("dur"), 0.4, 30, f"Shot {i + 1} duration")
        cin = _num(c.get("in") or 0, 0, 3600, f"Shot {i + 1} start in the source")
        if a.get("kind") == "video" and a.get("duration") and cin + dur > float(a["duration"]) + 0.05:
            raise HTTPException(400, f"Shot {i + 1} runs past the end of its clip ({float(a['duration']):.1f}s).")
        focus = c.get("focus") or [0.5, 0.5]
        clips.append({"id": str(c.get("id") or f"c{i + 1}")[:12], "asset": aid, "start": round(_num(c.get("start") or 0, 0, 180, f"Shot {i + 1} start"), 3),
                      "dur": round(dur, 3), "in": round(cin, 3), "fit": c.get("fit") if c.get("fit") in FITS else "frame",
                      "focus": [round(_num(focus[0], 0, 1, "Focus x"), 3), round(_num(focus[1], 0, 1, "Focus y"), 3)],
                      "zoom": round(_num(c.get("zoom") or 1, 1, 3, f"Shot {i + 1} zoom"), 3), "move": c.get("move") if c.get("move") in MOVES else "none",
                      "gain": round(_num(c.get("gain") or 0, 0, 1, f"Shot {i + 1} clip volume"), 3), "dim": round(_num(c.get("dim") or 0, 0, 0.8, "Dim"), 3)})
    t = 0.0
    for c in clips:  # shots play back to back: the order and durations define the cut
        c["start"] = round(t, 3)
        t += c["dur"]
    body = t
    allowed = safety._allowed(v.brief or {})
    chart = (v.brief or {}).get("chart") or {}
    overlays = []
    for i, o in enumerate(tl.get("overlays") or []):
        typ = o.get("type")
        if typ not in OVERLAYS:
            raise HTTPException(400, f"Graphic {i + 1}: unknown type.")
        start = _num(o.get("start") or 0, 0, body, f"Graphic {i + 1} start")
        dur = min(_num(o.get("dur") or 2, 0.5, 30, f"Graphic {i + 1} duration"), body - start)
        data = dict(o.get("data") or {})
        if typ == "chart":  # the research's own data, never edited points
            if len(chart.get("points") or []) < 2:
                raise HTTPException(400, "This story has no verified chart data.")
            data = {**{k: data.get(k) for k in ("title", "kind") if data.get(k)}, "points": chart["points"], "source": chart.get("source") or chart.get("label") or "",
                    "prefix": chart.get("prefix", "$")}
        texts = []
        for k, lim in TEXT_LIMIT.items():
            if k in data and data[k] is not None:
                data[k] = str(data[k])[:lim]
                texts.append(data[k])
        if typ == "facts":
            data["rows"] = [{"k": str(r.get("k", ""))[:30], "v": str(r.get("v", ""))[:30]} for r in (data.get("rows") or [])[:6] if isinstance(r, dict)]
            texts += [r["v"] for r in data["rows"]]
        bad = [n for txt in texts if typ != "chart" for n in safety.numbers(txt) if not safety._ok_number(n, allowed)]
        if bad:
            raise HTTPException(400, f"Graphic {i + 1} ({typ}): the number {bad[0]:g} isn't in the research.")
        overlays.append({"id": str(o.get("id") or f"o{i + 1}")[:12], "type": typ, "start": round(start, 3), "dur": round(dur, 3), "data": data})
    cap = (tl.get("captions") or {}).get("style")
    nar = tl.get("narration") or {}
    mus = tl.get("music") or {}
    old = v.timeline or {}
    return {"version": 1, "style": str(tl.get("style") or old.get("style") or "real_news")[:20], "duration": round(body + 1.4, 3), "end_card": 1.4,
            "video": clips, "overlays": sorted(overlays, key=lambda o: o["start"]),
            "captions": {"style": cap if cap in CAPTIONS else "clean", "words": (old.get("captions") or {}).get("words") or []},
            "narration": {"mode": nar.get("mode") if nar.get("mode") in ("ai", "upload", "none") else "ai", "narrator": str(nar.get("narrator") or "")[:40],
                          "gain": round(_num(nar.get("gain") if nar.get("gain") is not None else 1, 0, 1.5, "Narration volume"), 3)},
            "music": {"track": mus.get("track") if mus.get("track") in ("documentary", "market", "playful", "viral") else None,
                      "gain": round(_num(mus.get("gain") or 0, 0, 0.6, "Music volume"), 3)},
            "edited_at": utcnow().isoformat()}
