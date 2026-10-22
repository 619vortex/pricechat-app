"""The storyboard (props.json for the motion renderer): scenes with their verified data, transitions, beat-synced cuts,
sound design and music. Every value a scene shows is read from the research brief here (never from the AI's text), and
`unsupported()` re-checks every number on screen against the brief before rendering."""

import io
import json
import re
from pathlib import Path
from typing import Any

import httpx

MOTION = Path(__file__).resolve().parent.parent / "motion"
BEDS = json.loads((MOTION / "static" / "music" / "beds.json").read_text(encoding="utf-8")) if (MOTION / "static" / "music" / "beds.json").exists() else {}
TRANSITIONS = {"calm": ["fade", "zoom", "slide", "fade"], "normal": ["whip", "zoom", "slide", "cut", "whip", "fade"],
               "high": ["whip", "flash", "cut", "zoom", "whip", "slide", "cut"]}
SFX_CAP = {"calm": 5, "normal": 8, "high": 11}
MUSIC_GAIN = {"calm": 0.6, "normal": 0.72, "high": 0.82}
GRADE = re.compile(r"\b(PSA|BGS|CGC|SGC|TAG|ACE)\s?(10|[1-9](?:\.5)?)\b")
DATE = re.compile(r"\bon ((?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4})")
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def money(v: float) -> str:
    return f"${v:,.0f}" if v >= 100 else f"${v:,.2f}"


def facts(brief: dict[str, Any]) -> list[str]:
    return [str(f.get("text") if isinstance(f, dict) else f) for f in brief.get("facts") or []]


def story(brief: dict[str, Any]) -> dict[str, Any]:
    """The verified pieces scenes can show, parsed from the brief (missing pieces are simply absent)."""
    nums, card, coin = brief.get("numbers") or {}, brief.get("card") or {}, brief.get("coin") or {}
    text = " ".join([str(brief.get("headline") or "")] + facts(brief))
    g = GRADE.search(text)
    d = DATE.search(" ".join(facts(brief)))
    sale_type = next((m.group(1) for f in facts(brief) if (m := re.match(r"Sale type: (.+?) via ", f))), None)
    src = (brief.get("sources") or [{}])[0]
    out: dict[str, Any] = {"name": card.get("name") or coin.get("name"), "set": card.get("set_name"), "year": card.get("year"),
                           "grade": f"{g.group(1)} {g.group(2)}" if g else None, "date": d.group(1) if d else None, "sale_type": sale_type,
                           "source": src.get("name"), "url": src.get("url"), "symbol": coin.get("symbol")}
    out["gem_mint"] = "Gem Mint" if re.search(r"\bGem Mint\b", text) else None
    for k in ("sale_usd", "price_usd", "market_price_usd", "last_sale_usd", "change_24h_pct", "change_7d_pct", "change_30d_pct"):
        if isinstance(nums.get(k), (int, float)):
            out[k] = float(nums[k])
    return out


def scene_data(visual: str, s: dict[str, Any], brief: dict[str, Any], seg: dict[str, Any], tone: str, md: dict[str, Any] | None = None) -> dict[str, Any]:
    sub = " · ".join(str(x) for x in (s.get("name"), s.get("grade")) if x)
    live = (md or {}).get("coin") or {}
    if visual == "coin":
        if not live.get("price_usd"):
            return {}
        p = live["price_usd"]
        return {"logo": live.get("logo"), "symbol": live["symbol"], "value": p, "decimals": 0 if p >= 100 else 2 if p >= 1 else 5,
                "change_pct": live.get("change_24h_pct"), "label": "PRICE NOW", "source": "Hyperliquid · live"}
    if visual == "price_reveal":
        for key, label in (("sale_usd", "SOLD FOR"), ("market_price_usd", "MARKET PRICE"), ("price_usd", "PRICE NOW"),
                           ("last_sale_usd", "LAST VERIFIED SALE")):
            if key in s:
                return {"value": s[key], "decimals": 0 if s[key] >= 100 else 2, "label": label, "sub": sub}
    if visual in ("card_entrance", "card_detail"):
        return {"name": s.get("name"), "set": s.get("set"), "year": s.get("year"), "grade": s.get("grade"), "grade_note": s.get("gem_mint")}
    if visual == "evidence":
        rows = []
        price = s.get("sale_usd") or s.get("last_sale_usd")
        if price:
            rows.append({"k": "Price", "v": money(price)})
        for k, label in (("grade", "Grade"), ("date", "Sold"), ("source", "Marketplace"), ("sale_type", "Sale type")):
            if s.get(k):
                rows.append({"k": label, "v": str(s[k])})
        verified = "sale_usd" in s
        return {"title": "Verified sale" if verified else "Sale record", "rows": rows,
                "footer": "Recorded by the Price Chat Pokémon Market" if verified else (f"Source: {s['source']}" if s.get("source") else "")}
    if visual == "chart":
        ch = brief.get("chart") or {}
        if len(ch.get("points") or []) < 2 and live.get("chart"):  # no chart in the research: the coin's live Hyperliquid chart
            ch = live["chart"]
            s = {**s, "price_usd": live["price_usd"], "name": s.get("name") or live["symbol"],
                 **({"change_24h_pct": live["change_24h_pct"]} if live.get("change_24h_pct") is not None else {})}
        pts = [[float(p[0]), float(p[1])] for p in ch.get("points") or [] if isinstance(p, (list, tuple)) and len(p) >= 2]
        change = next(((s[k], lbl) for k, lbl in (("change_24h_pct", "24h"), ("change_7d_pct", "7d"), ("change_30d_pct", "30d")) if k in s), None)
        value = s.get("price_usd", s.get("market_price_usd"))
        return {"points": pts, "label": ch.get("label") or "", "title": s.get("name") or "",
                "value": value, "prefix": "$", "decimals": 0 if (value or 0) >= 100 else 2,
                **({"change_pct": change[0], "change_label": change[1]} if change else {})}
    if visual == "headline":
        return {"text": brief.get("headline") or seg.get("screen") or "", "kicker": "BREAKING" if tone == "breaking" else "NEWS", "source": s.get("source") or ""}
    if visual == "stat":
        st = brief.get("stat") or {}
        if isinstance(st.get("value"), (int, float)):  # the brief's own headline number (funding, OI change, rank, 30-day move...)
            return {k: st.get(k) for k in ("value", "prefix", "suffix", "decimals", "kicker")} | {"label": seg.get("screen") or ""}
        if "change_24h_pct" in s:
            ch = s["change_24h_pct"]
            return {"value": abs(ch), "prefix": "+" if ch >= 0 else "-", "suffix": "%", "decimals": 2, "kicker": "24H MOVE", "label": seg.get("screen") or ""}
        for key in ("price_usd", "sale_usd", "last_sale_usd"):
            if key in s:
                return {"value": s[key], "prefix": "$", "decimals": 0 if s[key] >= 100 else 2, "kicker": "PRICE", "label": seg.get("screen") or ""}
    return {}


def fallback_visual(visual: str, data: dict[str, Any], has_card: bool) -> str:
    if visual in ("card_entrance", "card_detail") and not has_card:
        return "punchline"
    if visual in ("price_reveal", "stat") and data.get("value") is None:
        return "punchline"
    if visual == "evidence" and not data.get("rows"):
        return "punchline"
    if visual == "coin" and data.get("value") is None:
        return "punchline"
    if visual == "chart" and len(data.get("points") or []) < 2:
        return "punchline"
    return visual


def fetch_card(brief: dict[str, Any], public: Path) -> tuple[str | None, float | None]:
    url = ((brief.get("images") or [{}])[0] or {}).get("url")
    if not url or not str(url).startswith("https://"):
        return None, None
    try:
        from PIL import Image

        r = httpx.get(url, timeout=20.0, follow_redirects=True, headers={"User-Agent": "pricechat-worker/2.0"})
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/") or len(r.content) > 12_000_000:
            return None, None
        im = Image.open(io.BytesIO(r.content)).convert("RGBA")
        im.save(public / "card.png")
        return "card.png", round(im.height / im.width, 4)
    except (httpx.HTTPError, OSError, ValueError):
        return None, None


def _beats(bpm: float, until: float) -> list[float]:
    step = 60.0 / bpm
    return [round(k * step, 3) for k in range(int(until / step) + 2)]


def _snap(t: float, beats: list[float], lo: float, hi: float) -> float:
    """Move a cut onto the nearest music beat when one falls in the pause between the two sentences."""
    best = min(beats, key=lambda b: abs(b - t)) if beats else t
    return best if lo <= best <= hi and abs(best - t) <= 0.22 else t


def build(job: dict[str, Any], cr: dict[str, Any], segments: list[dict[str, Any]], spans: list[tuple[float, float]],
          words: list[dict[str, Any]], public: Path, voice_file: str = "voice.wav") -> dict[str, Any]:
    brief = job.get("brief") or {}
    md = job.get("media") or {}
    photos = list(md.get("photos") or [])
    s = story(brief)
    card, ratio = fetch_card(brief, public)
    shot = [0]

    def next_photo(avoid: str | None) -> dict[str, Any] | None:
        """The next licensed photo in turn, never the one on screen just before."""
        for _ in range(len(photos)):
            ph = photos[shot[0] % len(photos)]
            shot[0] += 1
            if ph["src"] != avoid or len(photos) == 1:
                return {"src": ph["src"], "ratio": ph["ratio"], "credit": ph["credit"]}
        return None

    intensity = cr["intensity"]
    tail = 1.6 if intensity != "high" else 1.2
    voice_end = spans[-1][1] if spans else 0.0
    duration = round(voice_end + tail, 3)
    music = None if cr["music"] == "none" else cr["music"]
    bed = BEDS.get(music or "", {})
    beats = _beats(float(bed.get("bpm") or 100), duration) if music else []
    edits = cr.get("scenes") or {}
    scenes: list[dict[str, Any]] = []
    for k, seg in enumerate(segments):
        ed = edits.get(str(k)) or {}
        visual = str(ed.get("visual") or seg.get("visual") or seg.get("scene") or "punchline")
        screen = str(ed.get("screen") or seg.get("screen") or "")
        data = scene_data(visual, s, brief, {**seg, "screen": screen}, cr["tone"], md)
        visual = fallback_visual(visual, data, bool(card))
        if scenes and visual == scenes[-1]["kind"] and visual not in ("punchline", "photo") and screen:
            visual = "punchline"  # the same visual twice in a row reads as a stuck video: the words take this beat
        prev_photo = (scenes[-1]["data"].get("photo") or {}).get("src") if scenes else None
        if visual == "punchline" and photos and not (scenes and scenes[-1]["kind"] == "photo" and scenes[-2:-1] and scenes[-2]["kind"] == "photo"):
            visual = "photo"  # a real picture behind the words instead of a plain text card (after two photos, one type card)
        if visual == "punchline":
            data = {}
        if visual == "photo":
            ph = next_photo(prev_photo)
            data = {"photo": ph} if ph else {}
            visual = "photo" if ph else "punchline"
        if visual == "headline" and photos:
            data = {**data, "photo": next_photo(prev_photo)}
        start = 0.0 if k == 0 else spans[k][0] - 0.08
        if k:
            start = _snap(start, beats, spans[k - 1][1] - 0.05, spans[k][0] + 0.05)
        scenes.append({"kind": visual, "start": round(start, 3), "screen": screen, "data": data, "seg": k})
    # fast pace: a long segment gets a second shot (its screen words as kinetic type) at a sentence end near its middle
    if cr.get("pace") == "fast":
        extra = []
        for sc in scenes:
            k = sc["seg"]
            s0, s1 = spans[k]
            if s1 - s0 < 4.2 or sc["kind"] == "punchline" or not sc["screen"]:
                continue
            ends = [w["e"] for w in words if s0 + 1.6 < w["e"] < s1 - 1.4 and w["t"][-1:] in ".,!?"]
            cut = min(ends, key=lambda t: abs(t - (s0 + s1) / 2)) if ends else None
            if cut and photos:  # a second shot: another real picture (b-roll), never the same words twice
                ph = next_photo((sc["data"].get("photo") or {}).get("src"))
                if ph:
                    extra.append({"kind": "photo", "start": round(cut + 0.02, 3), "screen": "", "data": {"photo": ph, "broll": True}, "seg": k})
        scenes = sorted(scenes + extra, key=lambda x: x["start"])
    # the branded ending: Kiwi + wordmark over the last line and the tail
    outro_at = max(spans[-1][0] + 1.0, voice_end - 1.1) if spans else 0.0
    scenes = [x for x in scenes if x["start"] < outro_at - 0.6] or scenes[:1]
    scenes.append({"kind": "outro", "start": round(outro_at, 3), "screen": "", "data": {"cta": (job.get("brand") or {}).get("cta") or ""}, "seg": len(segments) - 1})
    for a, b in zip(scenes, scenes[1:]):
        a["end"] = b["start"]
    scenes[-1]["end"] = duration
    # transitions: from the intensity's palette, never the same twice in a row
    pal = TRANSITIONS[intensity]
    seed = int(job.get("video_id") or 0) + len(segments)
    prev = "cut"
    for i, sc in enumerate(scenes):
        if i == 0:
            sc["transition"] = "cut"
            continue
        if sc["kind"] == "outro":
            choice = next(c for c in (["zoom", "fade"] if intensity == "calm" else ["whip", "zoom"]) if c != prev)
        else:
            choice = pal[(seed + i) % len(pal)]
            if choice == prev:
                choice = pal[(seed + i + 1) % len(pal)]
        sc["transition"] = prev = choice
    sfx = plan_sfx(scenes, cr, segments)
    return {
        "theme": cr["style"], "gold": cr["style"] == "pokemon" and (s.get("sale_usd") or s.get("last_sale_usd") or 0) >= 5000,
        "intensity": intensity, "captionStyle": cr["captions"], "duration": duration,
        "brand": {"name": (job.get("brand") or {}).get("name") or "Price Chat", "cta": (job.get("brand") or {}).get("cta") or "",
                  "site": re.sub(r"^https?://", "", str((job.get("brand") or {}).get("website") or "pricechat.live")).rstrip("/")},
        "scenes": [{k: v for k, v in x.items() if k != "seg"} for x in scenes], "words": words, "beats": beats,
        "audio": {"voice": voice_file, "music": f"music/{music}.wav" if music else None, "musicGain": MUSIC_GAIN[intensity], "sfx": sfx},
        "assets": {**({"card": card, "cardRatio": ratio} if card else {}), **({"coin": md["coin"]["logo"]} if (md.get("coin") or {}).get("logo") else {})},
    }


def plan_sfx(scenes: list[dict[str, Any]], cr: dict[str, Any], segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sound design: a whoosh on moving transitions (three variants, rotated), a chime + hit when a price lands, a pop when
    Kiwi arrives, at most one comic sound for the funny tone. Capped per intensity, never two sounds within 0.35 s."""
    out: list[dict[str, Any]] = []
    whoosh = 0
    cap = SFX_CAP[cr["intensity"]]

    def add(src: str, at: float, gain: float) -> None:
        if len(out) < cap and all(abs(at - x["at"]) >= 0.35 for x in out) and at >= 0:
            out.append({"src": f"sfx/{src}.wav", "at": round(at, 3), "gain": gain})

    for i, sc in enumerate(scenes):
        t = sc["start"]
        tr = sc.get("transition")
        if sc["kind"] == "price_reveal":
            land = t + (1.0 if cr["intensity"] != "high" else 0.75) + 0.14
            add("shine", land - 0.05, 0.32)
            if cr["intensity"] != "calm":
                add("impact", land - 0.02, 0.42)
        if i and tr in ("whip", "slide", "zoom"):
            whoosh += 1
            add(f"whoosh{(whoosh % 3) + 1}", t - 0.28, 0.22 if cr["intensity"] == "calm" else 0.3 if tr != "zoom" else 0.24)
        elif i and tr == "flash":
            add("impact", t - 0.02, 0.3)
        if sc["kind"] == "outro":
            add("pop", t + 0.35, 0.35)
    if cr["tone"] == "funny":
        payoff = next((sc for sc in scenes if sc.get("seg") is not None and str(segments[min(sc.get("seg", 0), len(segments) - 1)].get("beat")) == "payoff"), None)
        if payoff:
            add("boing", payoff["start"] + 0.9, 0.28)
    return sorted(out, key=lambda x: x["at"])


def brief_numbers(brief: dict[str, Any], md: dict[str, Any] | None = None) -> set[float]:
    text = json.dumps({k: brief.get(k) for k in ("headline", "facts", "numbers", "card", "coin", "chart", "stat", "derived")}, ensure_ascii=False)
    live = (md or {}).get("coin") or {}
    extra = [abs(float(v)) for v in (live.get("price_usd"), live.get("change_24h_pct")) if isinstance(v, (int, float))]
    return {float(n.replace(",", "")) for n in NUM.findall(text)} | set(extra)


def unsupported(props: dict[str, Any], brief: dict[str, Any], md: dict[str, Any] | None = None) -> list[str]:
    """Numbers on screen (scene data and screen words) that neither the brief nor the live Hyperliquid data contain."""
    allowed = brief_numbers(brief, md)

    def ok(v: float) -> bool:
        return any(abs(v - a) <= max(0.011, abs(a) * 0.0005) for a in allowed) or v in (0.0,)

    bad = []
    for sc in props["scenes"]:
        d = sc.get("data") or {}
        vals: list[float] = []
        for k in ("value",):
            if isinstance(d.get(k), (int, float)):
                vals.append(float(d[k]))
        for r in d.get("rows") or []:
            vals += [float(n.replace(",", "")) for n in NUM.findall(str(r.get("v")))]
        vals += [float(n.replace(",", "")) for n in NUM.findall(str(sc.get("screen") or ""))]
        bad += [f"{sc['kind']}: {v:g}" for v in vals if not ok(v)]
    return bad
