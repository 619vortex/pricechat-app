"""The editorial engine (Content Studio v4): production styles, the media gate, shot selection and the timeline.

Workflow (footage first): research the media (assets.py) → can it carry a good Short? (`assess`) → pick the strongest
shots (`shots`) → the script is written FOR those shots (script4.py) → narration → `build` lays the shots under the
narration as an edit (cuts on sentence boundaries, B-roll inserts for long lines, real crops and slow moves inside
authentic pictures) and adds quiet graphics only where they carry a verified fact.

The timeline is the same document the dashboard's editor changes:
  {"version": 1, "style": id, "duration": s, "end_card": s,
   "video":    [{id, asset, start, dur, in, fit, focus, zoom, move, gain, dim, note}],
   "overlays": [{id, type, start, dur, data}],          type: title | lower_third | price | chart | facts | source | text
   "captions": {"style": clean|boxed|none, "words": [{t, s, e, p, hl}]},
   "narration": {"mode": ai|upload|none, "narrator": id, "gain": 1.0},
   "music": {"track": id|None, "gain": g}}
"""

import re
from typing import Any

from pricechat_worker import storyboard

# --- production styles (Rule 9): each one edits differently, not just a different colour -----------------------------------
STYLES: dict[str, dict[str, Any]] = {
    "real_news": {"label": "Real news", "font": "sans", "accent": "#19e68c", "frameBg": "#0b0b0c", "transition": "cut", "dissolve": 0, "move": 0.04,
                  "captions": "clean", "captionSize": 52, "grade": "", "creditCorner": "top", "frameTop": 560, "frameHeight": 680, "backdrop": "blur", "avg_shot": 2.8, "max_shot": 4.2, "landscape": "frame",
                  "title": True, "narrator": "steady", "music": "market", "music_gain": 0.10, "clip_gain": 0.12,
                  "tone": "Clear reporting. Calm, confident, plain words. Say what happened, then why it matters. No hype, no exclamation marks."},
    "automotive": {"label": "Automotive", "font": "sans", "accent": "#ff5a3c", "frameBg": "#09090a", "transition": "cut", "dissolve": 0, "move": 0.03,
                   "captions": "clean", "captionSize": 50, "grade": "contrast(1.04)", "creditCorner": "top", "frameTop": 330, "frameHeight": 900, "backdrop": "blur", "avg_shot": 2.1, "max_shot": 3.4,
                   "landscape": "fill", "title": False, "narrator": "steady", "music": "none", "music_gain": 0.0, "clip_gain": 0.75,
                   "tone": "An enthusiast who knows cars, talking to a friend. Short lines. Let the car be heard: leave space, don't narrate every second."},
    "luxury": {"label": "Luxury", "backdrop_override": "solid", "font": "serif", "accent": "#d9c08a", "frameBg": "#0d0c0a", "transition": "dissolve", "dissolve": 9, "move": 0.05,
               "captions": "clean", "captionSize": 48, "grade": "saturate(0.92) contrast(1.03)", "creditCorner": "top", "frameTop": 330, "frameHeight": 900, "backdrop": "blur", "avg_shot": 3.8, "max_shot": 5.5,
               "landscape": "fill", "title": False, "narrator": "documentary", "music": "documentary", "music_gain": 0.14, "clip_gain": 0.25,
               "tone": "Measured and elegant. Fewer words, longer pauses. Precise facts, no superlatives the research doesn't state."},
    "collectibles": {"label": "Collectibles", "font": "sans", "accent": "#e6c46e", "frameBg": "#0c0b0a", "transition": "dissolve", "dissolve": 6,
                     "move": 0.045, "captions": "clean", "captionSize": 50, "grade": "", "creditCorner": "top", "frameTop": 330, "frameHeight": 900, "backdrop": "blur", "avg_shot": 3.1, "max_shot": 4.6,
                     "landscape": "frame", "title": False, "narrator": "natural", "music": "documentary", "music_gain": 0.11, "clip_gain": 0.1,
                     "tone": "A knowledgeable collector telling a friend about a sale. Specific details (set, year, grade, where it sold). Warm, understated."},
    "tech_demo": {"label": "Tech demo", "font": "sans", "accent": "#4fb3ff", "frameBg": "#0a0b0d", "transition": "cut", "dissolve": 0, "move": 0.03,
                  "captions": "clean", "captionSize": 50, "grade": "", "creditCorner": "top", "frameTop": 560, "frameHeight": 680, "backdrop": "blur", "avg_shot": 3.0, "max_shot": 4.8, "landscape": "frame",
                  "title": True, "narrator": "natural", "music": "market", "music_gain": 0.09, "clip_gain": 0.3,
                  "tone": "A clear explainer. What it is, what it actually does in the footage, what's new. Plain words, no buzzwords."},
    "meme": {"label": "Funny / meme", "font": "sans", "accent": "#ffd23f", "frameBg": "#000000", "transition": "cut", "dissolve": 0, "move": 0.0,
             "captions": "boxed", "captionSize": 54, "grade": "", "creditCorner": "top", "frameTop": 330, "frameHeight": 900, "backdrop": "blur", "avg_shot": 1.8, "max_shot": 2.8, "landscape": "fill",
             "title": False, "narrator": "steady", "music": "playful", "music_gain": 0.12, "clip_gain": 0.5,
             "tone": "Deadpan. The joke comes from the facts and the timing, never from invented details. Understated delivery."},
}
STYLE_KEYS = ("font", "accent", "frameBg", "transition", "dissolve", "move", "captions", "captionSize", "grade", "creditCorner", "frameTop", "frameHeight",
              "backdrop")
BY_CATEGORY = {"pokemon_rare": "collectibles", "pokemon_market": "collectibles", "pokemon_spotlight": "collectibles", "stocks": "real_news",
               "cars": "automotive", "real_estate": "luxury", "ai": "tech_demo", "crypto_news": "real_news", "crypto_market": "real_news",
               "crypto_derivatives": "real_news", "facts": "real_news", "pricechat_highlights": "real_news",
               "finance_trends": "real_news"}
KICKER = {"stocks": "Markets", "ai": "Technology", "cars": "Cars", "real_estate": "Real estate", "crypto_news": "Crypto", "crypto_market": "Crypto",
          "crypto_derivatives": "Crypto", "pokemon_rare": "Collectibles", "pokemon_market": "Collectibles",
          "finance_trends": "Trending"}
# a small set of narrators, chosen for natural delivery (Kokoro voices that tested best), plus "none" and "upload"
NARRATORS = {"natural": {"label": "Natural (warm, US)", "voice": "af_heart", "speed": 1.0, "sentence_gap": 0.3, "beat_gap": 0.55},
             "steady": {"label": "Steady (calm, US)", "voice": "am_michael", "speed": 0.98, "sentence_gap": 0.32, "beat_gap": 0.6},
             "documentary": {"label": "Documentary (calm, British)", "voice": "bm_george", "speed": 0.95, "sentence_gap": 0.4, "beat_gap": 0.75}}
_VOICE = re.compile(r"^[ab][fm]_[a-z]+(?::\d{1,3})?(?:\+[ab][fm]_[a-z]+(?::\d{1,3})?){0,2}$")


def is_voice(key: str) -> bool:
    """A Kokoro voice ("af_bella") or a mix ("am_michael:60+bm_george:40"), as Content Studio's narrator menu offers them."""
    return bool(_VOICE.match(key))


NARRATION_MODES = {"ai": "AI narrator", "upload": "Uploaded human narration", "none": "No narration (on-screen text)"}


def style_of(job: dict[str, Any]) -> dict[str, Any]:
    cr = job.get("creative") or {}
    brief = job.get("brief") or {}
    sid = str(cr.get("production") or "")
    if sid not in STYLES:
        sid = BY_CATEGORY.get(str(brief.get("category") or job.get("category") or ""), "") or BY_CATEGORY.get(str(job.get("template") or ""), "") or "real_news"
    return {"id": sid, **STYLES[sid]}


def spec(st: dict[str, Any]) -> dict[str, Any]:
    """The part of a style the renderer needs."""
    return {"id": st["id"], "label": st["label"], "ink": "#ffffff", **{k: st[k] for k in STYLE_KEYS},
            **({"backdrop": st["backdrop_override"]} if st.get("backdrop_override") else {})}


# --- can the media carry a Short? -------------------------------------------------------------------------------------------
def usable(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [a for a in assets if a.get("status") == "ok" and a.get("file") and a.get("match") in ("exact", "related", "generic")]


def assess(assets: list[dict[str, Any]], job: dict[str, Any]) -> dict[str, Any]:
    """The editorial gate. A Short needs the real subject on screen: at least one exact picture, and enough distinct real
    shots (videos count double: they hold the eye) that the edit doesn't loop one picture. Data alone (a chart) can't carry
    it. Below the bar the story goes to editorial review instead of being padded with filler."""
    ok = usable(assets)
    exact = [a for a in ok if a["match"] == "exact"]
    related = [a for a in ok if a["match"] == "related"]
    videos = [a for a in ok if a["kind"] == "video" and a["match"] in ("exact", "related")]
    card = bool((job.get("brief") or {}).get("card"))
    # videos count double (they hold the eye); so does a card's own scan (it gives several real close-ups)
    weight = sum((2 if a["kind"] == "video" or (card and a["provider"] == "brief") else 1) * (1 if a["match"] == "exact" else 0.6) for a in exact + related)
    has_data = bool(((job.get("brief") or {}).get("chart") or {}).get("points"))
    need = 2.2 if card else 3.0  # a card story is shot on its own scan (several real details of one picture) + evidence
    problems = []
    if not exact:
        problems.append("no picture of the exact subject")
    if weight < need:
        problems.append(f"only {len(exact)} exact and {len(related)} related real shots")
    return {"ok": not problems, "exact": len(exact), "related": len(related), "videos": len(videos), "weight": round(weight, 2), "data": has_data,
            "generic": sum(1 for a in ok if a["match"] == "generic"), "problems": problems}


# --- the shots ---------------------------------------------------------------------------------------------------------------
CARD_DETAILS = [  # real close-ups inside the card's own scan: (what, focus, zoom, move)
    ("the whole card", (0.5, 0.5), 1.0, "push"),
    ("the artwork", (0.5, 0.3), 1.45, "pull"),
    ("the name and HP line", (0.35, 0.07), 1.6, "pan_right"),
    ("the attacks and text box", (0.5, 0.72), 1.3, "pan_down"),
]


def shots(assets: list[dict[str, Any]], job: dict[str, Any], st: dict[str, Any], limit: int = 12) -> list[dict[str, Any]]:
    """Candidate shots, best first: exact videos, exact photos, then related ones; the card's scan gives several real
    details. Generic filler only if nothing else is left."""
    ok = usable(assets)
    order = sorted(ok, key=lambda a: (-{"exact": 3, "related": 2, "generic": 0}[a["match"]], a["kind"] != "video", -float(a.get("score") or 0)))
    out: list[dict[str, Any]] = []
    for a in order:
        if a["match"] == "generic" and sum(1 for s in out) >= 4:
            continue
        if a["provider"] == "brief" and (job.get("brief") or {}).get("card"):
            for what, focus, zoom, move in CARD_DETAILS:
                out.append({"asset": a["id"], "kind": "image", "shows": f"{a.get('shows') or 'the card'}: {what}", "fit": "frame", "focus": list(focus),
                            "zoom": zoom, "move": move, "match": "exact", "max": 99.0})
            continue
        fit = framing(a, st)
        if a["kind"] == "video":
            out.append({"asset": a["id"], "kind": "video", "shows": a.get("shows") or a.get("title") or "", "fit": fit, "focus": [0.5, 0.5], "zoom": 1.0,
                        "move": "none", "match": a["match"], "max": max(1.0, float(a.get("duration") or 4) - 0.4)})
        else:
            moves = ["push", "pan_right", "pull", "pan_left"]
            out.append({"asset": a["id"], "kind": "image", "shows": a.get("shows") or a.get("title") or "", "fit": fit, "focus": [0.5, 0.45], "zoom": 1.0,
                        "move": moves[len(out) % len(moves)] if st["move"] else "none", "match": a["match"], "max": 99.0})
    for i, s in enumerate(out[:limit]):
        s["id"] = f"s{i + 1}"
    return out[:limit]


def framing(a: dict[str, Any], st: dict[str, Any]) -> str:
    """Full-bleed only when the picture can fill a 1080×1920 frame without turning to mush; otherwise framed."""
    w, h = int(a.get("width") or 0), int(a.get("height") or 0)
    if not w or not h:
        return "frame"
    upscale = max(1080 / w, 1920 / h)
    if h >= w:
        return "fill" if upscale <= 1.5 else "frame"
    if upscale <= 1.3:  # a 4K landscape source crops to a sharp full-screen shot
        return "fill"
    return "fill" if st["landscape"] == "fill" and upscale <= 2.0 else "frame"


# --- verified callouts (overlays never show a number the research doesn't have) -------------------------------------------
UNITS = [("_usd", "$", ""), ("_eur", "€", ""), ("_gbp", "£", ""), ("_pct", "", "%"), ("_hp", "", " hp"), ("_ps", "", " PS"), ("_kmh", "", " km/h"), ("_mph", "", " mph"), ("_kw", "", " kW"),
         ("_aed_bn", "AED ", " bn"), ("_aed_m", "AED ", " m"), ("_aed", "AED ", ""), ("_bn", "$", " bn"), ("_s", "", " s"), ("_m", "", " m"), ("_units", "", "")]


def fmt_number(key: str, v: float) -> str:
    pre, suf = "", ""
    for end, p, s in UNITS:
        if key.endswith(end):
            pre, suf = p, s
            break
    if key.endswith("_pct"):
        return f"{'+' if v > 0 else ''}{v:.2f}".rstrip("0").rstrip(".") + "%"
    body = f"{v:,.0f}" if float(v).is_integer() or abs(v) >= 10000 else f"{v:,.2f}"  # $217.09 stays exact; $17,225.41 sales read as $17,225
    if suf.strip() in ("bn", "m"):  # $96.2 bn, as the source writes it
        body = body.rstrip("0").rstrip(".") if "." in body else body
    return f"{pre}{body}{suf}"


def label_of(key: str) -> str:
    base = key
    while True:
        nxt = re.sub(r"_(usd|eur|pct|hp|ps|kmh|mph|kw|aed|bn|m|s|units|gaap)$", "", base)
        if nxt == base:
            break
        base = nxt
    return {"sale": "Sold for", "price": "Price", "last_sale": "Last sale", "market_price": "Market price", "change": "Change", "change_24h": "24h change",
            "power": "Power", "top_speed": "Top speed", "close": "Close"}.get(base, re.sub(r"\b(q\d|fy\d+|yoy|h\d)\b", lambda m: m.group(0).upper(),
                                                                                  base.replace("_", " ").capitalize(), flags=re.I))


def callouts(brief: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """key -> {label, value, note}: the numbers an overlay may show, with their display text, from the brief only."""
    out: dict[str, dict[str, Any]] = {}
    for c in brief.get("callouts") or []:
        if c.get("key") and c.get("value"):
            out[str(c["key"])] = {"label": str(c.get("label") or label_of(c["key"])), "value": str(c["value"]), "note": str(c.get("note") or "")}
    s = storyboard.story(brief)
    for k, v in (brief.get("numbers") or {}).items():
        if isinstance(v, (int, float)) and k not in out and k not in ("year",):
            note = ""
            if k in ("sale_usd", "last_sale_usd"):
                note = " · ".join(x for x in (s.get("grade"), s.get("source"), _short_date(s.get("date"))) if x)
            # "transactions_aed_bn_2025": the year goes into the label, the unit is read from the rest
            m = re.match(r"(.+?)_((?:19|20)\d\d)$", k)
            unit_key, year = (m.group(1), m.group(2)) if m else (k, "")
            out[unit_key if unit_key not in out else k] = {"label": (label_of(unit_key) + (f" {year}" if year else "")), "value": fmt_number(unit_key, float(v)),
                                                           "note": note}
    return out


def _short_date(d: str | None) -> str:
    if not d:
        return ""
    m = re.match(r"(\w{3})\w* (\d{1,2}), (\d{4})", d)
    return f"{m.group(1)} {m.group(2)}, {m.group(3)}" if m else d


def facts_rows(brief: dict[str, Any]) -> tuple[str, list[dict[str, str]], str]:
    """The record card: (title, rows, footer) from verified data only."""
    if brief.get("facts_table"):
        t = brief["facts_table"]
        return str(t.get("title") or "The facts"), [{"k": str(r["k"]), "v": str(r["v"])} for r in t.get("rows") or []][:5], str(t.get("footer") or "")
    s = storyboard.story(brief)
    rows = []
    price = s.get("sale_usd") or s.get("last_sale_usd")
    if not price:  # not a sale: the story's own verified numbers
        for k, c in [(k, c) for k, c in callouts(brief).items() if any(k.endswith(u) for u, _, _ in UNITS[:-1])][:4]:
            rows.append({"k": c["label"], "v": c["value"]})
        return "The numbers", rows, ""
    if price:
        rows.append({"k": "Price", "v": storyboard.money(price)})
    for k, label in (("grade", "Grade"), ("date", "Sold"), ("source", "Marketplace")):
        if s.get(k):
            rows.append({"k": label, "v": str(s[k])})
    if rows:
        return ("Verified sale" if "sale_usd" in s else "Sale record"), rows, "Recorded by the Price Chat Pokémon Market" if "sale_usd" in s else ""
    for k, c in list(callouts(brief).items())[:4]:
        rows.append({"k": c["label"], "v": c["value"]})
    return "The numbers", rows, ""


def chart_of(brief: dict[str, Any]) -> dict[str, Any] | None:
    ch = brief.get("chart") or {}
    pts = [[float(p[0]), float(p[1])] for p in ch.get("points") or [] if isinstance(p, (list, tuple)) and len(p) >= 2]
    if len(pts) < 2:
        return None
    if ch.get("kind") == "market":  # a market-structure chart (backend marketviz.py): drawn as its variant, with basis, venues and UTC window
        keep = ("variant", "basis", "basis_label", "venues", "unit", "price", "bars", "levels", "mid", "walls", "marks", "side", "start", "end", "at")
        return {"points": pts, "kind": "market", "title": ch.get("title") or "", "source": ch.get("source") or "", "prefix": ch.get("prefix", "$"),
                "suffix": ch.get("suffix", ""), **{k: ch[k] for k in keep if k in ch}}
    return {"points": pts, "kind": ch.get("kind") or "line", "title": ch.get("title") or ch.get("label") or "", "source": ch.get("source") or ch.get("label") or "",
            "prefix": ch.get("prefix", "$"), "suffix": ch.get("suffix", ""), **({"highlight": ch["highlight"]} if "highlight" in ch else {})}


# --- the edit ------------------------------------------------------------------------------------------------------------------
def build(job: dict[str, Any], st: dict[str, Any], segments: list[dict[str, Any]], spans: list[tuple[float, float]], words: list[dict[str, Any]],
          shot_list: list[dict[str, Any]], assets: dict[str, dict[str, Any]], narration: str = "ai") -> dict[str, Any]:
    brief = job.get("brief") or {}
    by_id = {s["id"]: s for s in shot_list}
    used: dict[str, int] = {}
    reserved = {i for seg in segments for i in seg.get("shots") or []}  # shots a line asked for: never taken as someone else's B-roll
    calls = callouts(brief)
    voice_end = spans[-1][1] if spans else 0.0
    tail = 0.9
    end_card = 1.4
    body = round(voice_end + tail, 3)
    clips: list[dict[str, Any]] = []
    cid = [0]

    def spare(avoid: set[str], tier: tuple[str, ...]) -> dict[str, Any] | None:
        """An unused shot for a B-roll insert (exact first), never the asset already on screen."""
        for s in sorted(shot_list, key=lambda s: (used.get(s["id"], 0), {"exact": 0, "related": 1, "generic": 2}[s["match"]])):
            if s["asset"] not in avoid and s["match"] in tier and used.get(s["id"], 0) == 0 and s["id"] not in reserved:
                return s
        # nothing fresh left: another moment of a video that's already in the edit (place() starts it further into the clip)
        for s in sorted(shot_list, key=lambda s: used.get(s["id"], 0)):
            if s["kind"] == "video" and s["asset"] not in avoid and s["match"] in tier and used.get(s["id"], 0) >= 1 and s["max"] >= 6:
                return s
        return None

    def place(s: dict[str, Any], start: float, dur: float) -> None:
        a = assets[s["asset"]]
        cid[0] += 1
        used[s["id"]] = used.get(s["id"], 0) + 1
        clip = {"id": f"c{cid[0]}", "asset": s["asset"], "shot": s["id"], "start": round(start, 3), "dur": round(dur, 3), "fit": s["fit"],
                "focus": list(s["focus"]), "zoom": s["zoom"], "move": s["move"], "gain": 0.0, "dim": 0.0}
        if a["kind"] == "video":
            avail = float(a.get("duration") or dur)
            reuse = used[s["id"]] - 1
            clip["in"] = round(min(max(0.0, avail - dur - 0.1), 0.4 + reuse * dur), 3)
            clip["gain"] = st["clip_gain"] if a.get("audio") else 0.0
        clips.append(clip)

    for k, seg in enumerate(segments):
        a0 = 0.0 if k == 0 else max(clips[-1]["start"] + 0.6 if clips else 0.0, spans[k][0] - 0.12)
        b0 = body if k == len(segments) - 1 else spans[k + 1][0] - 0.12
        window = max(0.6, b0 - a0)
        ids = [i for i in seg.get("shots") or [] if i in by_id] or [shot_list[k % len(shot_list)]["id"]]
        # long windows get more shots (B-roll on detail), short ones keep only the first; videos can't run past their end
        plan = [by_id[i] for i in ids]
        while window / len(plan) > st["max_shot"] or any(window / len(plan) > s["max"] for s in plan):
            extra = spare({s["asset"] for s in plan} | {clips[-1]["asset"]} if clips else {s["asset"] for s in plan}, ("exact", "related"))
            if not extra:
                break
            plan.append(extra)
        while len(plan) > 1 and window / len(plan) < 1.2:
            plan.pop()
        # a graphic that names the subject or states its price stays on the line's own (main) shot
        hold = 0.0
        want = str(seg.get("overlay") or "")
        if want.startswith("price") and calls:
            c = calls.get(want.partition(":")[2]) or next(iter(calls.values()))
            at = _word_at([w for w in words if a0 - 0.2 <= w["s"] <= b0], c["value"])
            hold = (at or a0 + 0.5) + 3.0 - a0
        elif want in ("lower_third", "facts", "chart"):
            hold = min(window, {"lower_third": 3.7, "facts": 5.0, "chart": 6.0}[want])  # the graphic's own time on screen + its fade
        if hold and len(plan) > 1 and window - hold < 1.2:
            plan = plan[:1]
        t = a0
        for j, s in enumerate(plan):
            share = window / len(plan)
            if j == 0 and hold and len(plan) > 1:
                share = min(window - 1.2 * (len(plan) - 1), max(share, hold))
            elif j and hold and len(plan) > 1:
                share = (b0 - a0 - max(window / len(plan), min(window - 1.2 * (len(plan) - 1), hold))) / (len(plan) - 1)
            d = (b0 - t) if j == len(plan) - 1 else share
            d = min(d, s["max"]) if j < len(plan) - 1 or len(plan) == 1 else d
            place(s, t, d)
            t += d
        if t < b0 - 0.05:  # a video ran out: the last clip holds on a still-able shot or extends to the cut
            clips[-1]["dur"] = round(b0 - clips[-1]["start"], 3)
    for a, b in zip(clips, clips[1:]):  # no gaps, no overlaps
        a["dur"] = round(b["start"] - a["start"], 3)
    clips[-1]["dur"] = round(body - clips[-1]["start"], 3)
    overlays = place_overlays(job, st, segments, spans, words, clips, assets, body)
    return {"version": 1, "style": st["id"], "duration": round(body + end_card, 3), "end_card": end_card, "video": clips, "overlays": overlays,
            "captions": {"style": "none" if narration == "none" else st["captions"], "words": words},
            "narration": {"mode": narration, "narrator": (job.get("creative") or {}).get("narrator") or st["narrator"], "gain": 1.0},
            "music": {"track": None if st["music"] == "none" else st["music"], "gain": st["music_gain"]}}


def _word_at(words: list[dict[str, Any]], text: str, after: float = 0.0) -> float | None:
    """When a written number (e.g. "$17,225") is spoken: the start of the caption word that carries it."""
    want = re.sub(r"[^\d.]", "", text)
    if not want:
        return None
    for w in words:
        if w["s"] >= after and want and re.sub(r"[^\d.]", "", w["t"]).rstrip(".") == want.rstrip("."):
            return w["s"]
    return None


def place_overlays(job: dict[str, Any], st: dict[str, Any], segments: list[dict[str, Any]], spans: list[tuple[float, float]],
                   words: list[dict[str, Any]], clips: list[dict[str, Any]], assets: dict[str, dict[str, Any]], body: float) -> list[dict[str, Any]]:
    brief = job.get("brief") or {}
    cats = str(brief.get("category") or job.get("category") or "")
    out: list[dict[str, Any]] = []
    busy: list[tuple[float, float]] = []

    def free(a: float, b: float) -> bool:
        return all(b <= x or a >= y for x, y in busy)

    def add(typ: str, start: float, dur: float, data: dict[str, Any], force: bool = False) -> bool:
        start, dur = max(0.0, start), min(dur, body - max(0.0, start))
        if dur < 1.0 or (not force and not free(start, start + dur)):
            return False
        out.append({"id": f"o{len(out) + 1}", "type": typ, "start": round(start, 3), "dur": round(dur, 3), "data": data})
        if typ not in ("title", "source"):  # the headline sits at the top and the source tag in the corner: neither blocks the lower graphics
            busy.append((start, start + dur))
        return True

    seg_window = [(0.0 if k == 0 else spans[k][0] - 0.12, body if k == len(segments) - 1 else spans[k + 1][0] - 0.12) for k in range(len(segments))]
    screen = str((job.get("plan") or {}).get("on_screen_text") or "")
    if screen:  # the Kiwi writer's on-screen line opens the video (instead of the headline), whatever the style
        add("title", 0.15, min(2.8, seg_window[0][1] - 0.15), {"text": screen[:60]})
    elif st.get("title") and brief.get("headline"):
        add("title", 0.15, min(3.2, seg_window[0][1] - 0.15), {"kicker": KICKER.get(cats, "News"), "text": str(brief["headline"])[:110]})
    # what each segment asked for (from the script); the values come from the brief, never from the script
    calls = callouts(brief)
    chart = chart_of(brief)
    for k, seg in enumerate(segments):
        a, b = seg_window[k]
        want = str(seg.get("overlay") or "")
        if want.startswith("price") and calls:
            key = want.partition(":")[2] or next(iter(calls))
            c = calls.get(key) or next(iter(calls.values()))
            at = _word_at([w for w in words if a - 0.2 <= w["s"] <= b], c["value"]) or (a + 0.5)
            add("price", at - 0.15, min(3.0, b - at + 0.1), {"label": c["label"], "value": c["value"], "note": c["note"]})
        elif want == "chart" and chart:
            end, cap = b, 5.5
            if chart.get("kind") == "market":  # market data is the story: it stays up over the next lines that ask for no graphic
                cap, j = 10.0, k + 1
                while j < len(segments) and str(segments[j].get("overlay") or "none") == "none" and seg_window[j][1] - a <= cap + 0.3:
                    end, j = seg_window[j][1], j + 1
            dur = min(end - a - 0.3, cap)
            if add("chart", a + 0.25, dur, chart):
                for cl in clips:  # the picture under the chart steps back
                    if cl["start"] < a + 0.25 + dur and cl["start"] + cl["dur"] > a + 0.25:
                        cl["dim"] = 0.55
        elif want == "facts":
            title, rows, footer = facts_rows(brief)
            if rows and add("facts", a + 0.25, min(b - a - 0.3, 4.5), {"title": title, "rows": rows, "footer": footer}):
                for cl in clips:
                    if cl["start"] < a + 4.5 and cl["start"] + cl["dur"] > a + 0.25:
                        cl["dim"] = 0.5
        elif want == "lower_third" or (want == "" and k == 1 and not any(o["type"] == "lower_third" for o in out)):
            name = short_name(brief)
            if name:
                add("lower_third", a + 0.3, min(3.2, b - a - 0.2), {"name": name, "detail": detail_of(brief)})
    title = next((o for o in out if o["type"] == "title"), None)
    first = min((o["start"] for o in out if o["type"] in ("price", "lower_third", "facts", "chart")), default=None)
    if title and first is not None and first < title["start"] + title["dur"]:
        title["dur"] = round(max(1.4, first - title["start"] - 0.1), 3)
        if title["dur"] > first - title["start"]:  # too early to share the screen: the graphic waits for the headline
            for o in out:
                if o["type"] in ("price", "lower_third") and o["start"] < title["start"] + title["dur"]:
                    shift = title["start"] + title["dur"] + 0.1 - o["start"]
                    o["start"], o["dur"] = round(o["start"] + shift, 3), round(max(1.2, o["dur"] - shift), 3)
    for o in [o for o in out if o["type"] == "lower_third" and o["start"] > body - 4.0]:  # not on top of the closing source tag
        out.remove(o)
    if not any(o["type"] == "lower_third" for o in out):  # name the subject once, on its first exact shot
        name = short_name(brief)
        first = next((c for c in clips if assets[c["asset"]].get("match") == "exact" and c["start"] > 0.5), None)
        if name and first:
            add("lower_third", first["start"] + 0.3, min(3.0, first["dur"]), {"name": name, "detail": detail_of(brief)})
    srcs = [str(s.get("name")) for s in brief.get("sources") or [] if s.get("name")]
    if srcs:
        add("source", max(0.0, body - 3.4), 3.0, {"text": "Source: " + ", ".join(dict.fromkeys(srcs))[:80]}, force=True)
    return sorted(out, key=lambda o: o["start"])


def short_name(brief: dict[str, Any]) -> str:
    """The subject as a lower third can say it: 'NASA Astrobee free-flying robots (Honey, ...) aboard the ISS' -> 'NASA Astrobee free-flying robots'."""
    name = str(brief.get("subject") or (brief.get("card") or {}).get("name") or "")
    name = re.split(r"\s[(\[]|,\s| - | aboard | at the ", name)[0].strip()
    return name if len(name) <= 42 else name[:40].rsplit(" ", 1)[0]


def detail_of(brief: dict[str, Any]) -> str:
    card = brief.get("card") or {}
    if card:
        s = storyboard.story(brief)
        return " · ".join(str(x) for x in (card.get("set_name"), card.get("year"), s.get("grade")) if x)
    return str(brief.get("subject_detail") or "")


# --- checks and the renderer's props ---------------------------------------------------------------------------------------
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def unsupported(tl: dict[str, Any], brief: dict[str, Any]) -> list[str]:
    """Numbers in on-screen graphics that the research doesn't contain (charts are the research's own data)."""
    allowed = storyboard.brief_numbers(brief) | {float(c) for c in NUM.findall(" ".join(str(x.get("value", "")) + " " + str(x.get("note", ""))
                                                                                          for x in brief.get("callouts") or []).replace(",", ""))}
    allowed |= {float(n.replace(",", "")) for r in ((brief.get("facts_table") or {}).get("rows") or []) for n in NUM.findall(str(r.get("v")))}

    def ok(v: float) -> bool:
        return any(abs(v - a) <= max(0.011, abs(a) * 0.0005) for a in allowed) or v == 0

    bad = []
    for o in tl.get("overlays") or []:
        d = o.get("data") or {}
        text = " ".join(str(d.get(k, "")) for k in ("text", "value", "note", "name", "detail", "title", "footer", "label"))
        text += " " + " ".join(str(r.get("v", "")) for r in d.get("rows") or [])
        for n in NUM.findall(text):
            v = float(n.replace(",", ""))
            if not ok(v):
                bad.append(f"{o['type']}: {n}")
    return bad


def props(tl: dict[str, Any], assets: dict[str, dict[str, Any]], st: dict[str, Any], brand: dict[str, Any], voice: str | None,
          music_file: str | None) -> dict[str, Any]:
    used = {c["asset"] for c in tl["video"]}
    amap = {}
    for aid in used:
        a = assets[aid]
        credit = a.get("attribution") or ""
        if a.get("note") == "not the card in this story":
            credit = (credit + " · " if credit else "") + "Not the card in this story"
        amap[aid] = {"src": a["file"], "kind": a["kind"], "w": int(a.get("width") or 1080), "h": int(a.get("height") or 1920),
                     "duration": a.get("duration"), "credit": credit, "audio": bool(a.get("audio"))}
    overlays = list(tl["overlays"])
    clips = []
    for c in tl["video"]:
        clips.append({k: c[k] for k in ("id", "asset", "start", "dur", "fit", "focus", "zoom", "move", "gain") if k in c} | {"in": c.get("in", 0)})
        if c.get("dim"):  # a darker picture under a chart or record card: drawn as a translucent black overlay layer under it
            overlays.insert(0, {"id": f"dim_{c['id']}", "type": "dim", "start": c["start"], "dur": c["dur"], "data": {"amount": c["dim"]}})
    words = tl["captions"]["words"] if tl["captions"]["style"] != "none" else []
    return {"style": {**spec(st), "captions": tl["captions"]["style"]}, "duration": tl["duration"],
            "brand": {"name": brand.get("name") or "Price Chat", "site": re.sub(r"^https?://", "", str(brand.get("website") or "pricechat.live")).rstrip("/")},
            "assets": amap, "video": clips, "overlays": overlays, "captions": {"words": words},
            "narration": {"src": voice, "gain": float(tl["narration"].get("gain") or 1.0)},
            "music": {"src": music_file, "gain": float(tl["music"].get("gain") or 0.0) if music_file else 0.0}, "endCard": tl.get("end_card", 1.4)}
