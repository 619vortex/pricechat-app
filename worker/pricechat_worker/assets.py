"""Authentic media research (Content Studio v4): real footage and photographs for a story, each with its rights record.

Sources, in the order the editor trusts them:
  · brief      media that came with the research: the exact card's official scan, a company's press image...
  · library    assets the owner added in Content Studio's media library (they attest the licence when adding)
  · commons    Wikimedia Commons video and photos: licence read from each file's own metadata (CC0, public domain,
               CC BY, CC BY-SA only; NC/ND and unknown licences are skipped)
  · nasa       NASA Image and Video Library (public domain under NASA's media guidelines; no endorsement implied)
  · pexels / pixabay   licensed stock video, only when PEXELS_API_KEY / PIXABAY_API_KEY are set on the worker
  · openverse  freely licensed photographs (commercial use + modification allowed)

Nothing is assumed free to reuse: an item without a licence we can read is not used. Watermarked pictures, diagrams,
logos and AI-looking images are dropped. The local vision model looks at every candidate (a photo, or a video's poster
frame) and says what it shows and whether it is the story's exact subject, a related one, or generic filler; only then
is it downloaded. Videos are trimmed and re-encoded to H.264 for the renderer.

Every asset record:
  id, kind (video|image), provider, url, page, title, creator, rights_holder, license, license_url,
  permitted {commercial, modify, attribution, editorial_only}, attribution (the credit line shown on screen),
  width, height, duration, orientation, query, shows, match (exact|related|generic|unfit), score (0..1), note,
  authentic (real media, not generated), audio, file (in the render's public folder), status (ok|excluded|failed)."""

import base64
import hashlib
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)
UA = {"User-Agent": "pricechat-worker/4.0 (https://pricechat.live; media research)"}
CACHE = Path(__file__).resolve().parent.parent / "cache" / "assets"
FREE = re.compile(r"^(CC0|CC[- ]?BY(-SA)?[- ]\d(\.\d)?|CC[- ]?BY(-SA)?$|Public domain|PDM|No restrictions)", re.I)
NOT_REAL = re.compile(r"\b(chart|graph|diagram|map|logo|icon|screenshot|infographic|svg|flag|render(ing)?|illustration|drawing|clipart|"
                      r"cartoon|vector|ai[- ]generated|midjourney|dall-?e|stable diffusion|meme|banner|poster)\b", re.I)
MAX_VIDEO_SECONDS = 24
MATCH_SCORE = {"exact": 1.0, "related": 0.6, "generic": 0.25, "unfit": 0.0}


def _client() -> httpx.Client:
    return httpx.Client(timeout=25.0, headers=UA, follow_redirects=True)


def asset_id(url: str) -> str:
    return "a" + hashlib.sha1(url.encode()).hexdigest()[:10]


def orientation(w: int, h: int) -> str:
    return "portrait" if h > w * 1.05 else "landscape" if w > h * 1.05 else "square"


def record(kind: str, provider: str, url: str, **kw: Any) -> dict[str, Any]:
    w, h = int(kw.pop("width", 0) or 0), int(kw.pop("height", 0) or 0)
    lic = str(kw.pop("license", "") or "")
    creator = re.sub(r"\s+", " ", str(kw.pop("creator", "") or "")).strip()[:80]
    attribution_needed = bool(kw.pop("attribution_required", not re.match(r"^(CC0|Public domain|PDM|No restrictions)", lic, re.I)))
    rec = {"id": asset_id(url), "kind": kind, "provider": provider, "url": url, "page": "", "title": "", "creator": creator,
           "rights_holder": creator, "license": lic, "license_url": "", "width": w, "height": h, "duration": None,
           "orientation": orientation(w, h) if w and h else "", "query": "", "shows": "", "match": "", "score": 0.0, "note": "",
           "authentic": True, "audio": False, "file": "", "status": "ok", "thumb": "",
           "permitted": {"commercial": True, "modify": True, "attribution": attribution_needed, "editorial_only": False}}
    rec.update({k: v for k, v in kw.items() if v is not None})
    if not rec.get("attribution"):
        who = creator or rec["rights_holder"] or provider
        rec["attribution"] = f"{'Video' if kind == 'video' else 'Photo'}: {who} · {lic}" if attribution_needed and lic else (
            f"{rec['rights_holder']}" if rec["permitted"]["editorial_only"] else "")
    return rec


# --- providers -----------------------------------------------------------------------------------------------------------
def from_brief(brief: dict[str, Any]) -> list[dict[str, Any]]:
    """The research's own media: the exact card's scan (an identification image of the item that sold: editorial use,
    artwork © its owners), or press images the story came with (each must carry its licence to be used)."""
    out = []
    card = brief.get("card") or {}
    for im in brief.get("images") or []:
        url = str((im or {}).get("url") or "")
        if not url.startswith("https://"):
            continue
        if card:
            out.append(record("image", "brief", url, title=f"{card.get('name', '')} ({card.get('set_name', '')}) card scan",
                              rights_holder="The Pokémon Company / Nintendo / Creatures / GAME FREAK", creator="",
                              license="Editorial use: identifies the card in the reported sale", page=str(im.get("page") or ""),
                              permitted={"commercial": False, "modify": False, "attribution": True, "editorial_only": True},
                              attribution="Card image: The Pokémon Company", match="exact", score=1.0,
                              shows=f"the {card.get('name', 'card')} card itself", note="official scan of this exact card",
                              attribution_required=True))
        elif im.get("license"):
            out.append(record(str(im.get("kind") or "image"), "brief", url, title=str(im.get("title") or ""), creator=str(im.get("creator") or ""),
                              license=str(im["license"]), page=str(im.get("page") or ""), rights_holder=str(im.get("rights_holder") or im.get("creator") or "")))
    for m in brief.get("media") or []:  # benchmark/research briefs: candidates found by a researcher with their licence
        url = str(m.get("file_url_medium") or m.get("file_url") or m.get("url") or "")
        if not url.startswith("https://") or not FREE.match(str(m.get("license") or "")) and "nasa" not in str(m.get("license") or "").lower():
            continue
        out.append(record(str(m.get("kind") or "image"), "research", url, title=str(m.get("title") or ""), creator=str(m.get("creator") or ""),
                          rights_holder=str(m.get("rights_holder") or m.get("creator") or ""), license=str(m.get("license") or ""),
                          license_url=str(m.get("license_url") or ""), page=str(m.get("page_url") or ""), width=m.get("width"), height=m.get("height"),
                          duration=m.get("duration_s"), attribution=str(m.get("attribution") or "") or None,
                          shows=str(m.get("shows") or ""), hint_exact=bool(m.get("exact_subject"))))
    return out


def commons(c: httpx.Client, q: str, video: bool, limit: int = 12) -> list[dict[str, Any]]:
    try:
        r = c.get("https://commons.wikimedia.org/w/api.php", params={
            "action": "query", "format": "json", "generator": "search", "gsrsearch": f"{q} filetype:{'video' if video else 'bitmap'}",
            "gsrnamespace": 6, "gsrlimit": limit, "prop": "imageinfo|videoinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 960,
            "viprop": "derivatives"})
        pages = ((r.json().get("query") or {}).get("pages") or {}) if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return []
    out = []
    for p in pages.values():
        ii = (p.get("imageinfo") or [{}])[0]
        m = ii.get("extmetadata") or {}
        lic = str((m.get("LicenseShortName") or {}).get("value") or "")
        if not FREE.match(lic) or re.search(r"\b(NC|ND)\b", lic):
            continue
        mime = str(ii.get("mime") or "")
        if video and not mime.startswith(("video/", "application/ogg")):
            continue
        if not video and mime not in ("image/jpeg", "image/png", "image/webp"):
            continue
        url = str(ii.get("url") or "")
        if video:  # a transcoded derivative (720p/1080p) is far smaller than the original
            ders = [d for d in (p.get("videoinfo") or [{}])[0].get("derivatives") or [] if str(d.get("type", "")).startswith("video/")]
            ders = sorted(ders, key=lambda d: abs(int(d.get("height") or 0) - 1080))
            if ders:
                url = str(ders[0].get("src") or url)
        restrictions = str((m.get("Restrictions") or {}).get("value") or "")
        out.append(record("video" if video else "image", "commons", url, title=str(p.get("title") or "").removeprefix("File:"),
                          creator=re.sub(r"<[^>]+>", "", str((m.get("Artist") or {}).get("value") or "")), license=lic,
                          license_url=str((m.get("LicenseUrl") or {}).get("value") or ""), page=str(ii.get("descriptionurl") or ""),
                          width=ii.get("width"), height=ii.get("height"), duration=ii.get("duration"), thumb=str(ii.get("thumburl") or ""),
                          note=("restrictions: " + restrictions) if restrictions else "", query=q,
                          description=re.sub(r"<[^>]+>", "", str((m.get("ImageDescription") or {}).get("value") or ""))[:300]))
    return out


def nasa(c: httpx.Client, q: str, video: bool, limit: int = 8) -> list[dict[str, Any]]:
    try:
        r = c.get("https://images-api.nasa.gov/search", params={"q": q, "media_type": "video" if video else "image", "page_size": limit})
        items = ((r.json().get("collection") or {}).get("items") or []) if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return []
    out = []
    for it in items[:limit]:
        d = (it.get("data") or [{}])[0]
        nid = str(d.get("nasa_id") or "")
        thumb = next((str(link.get("href")) for link in it.get("links") or [] if link.get("rel") == "preview"), "")
        if not nid:
            continue
        out.append(record("video" if video else "image", "nasa", f"nasa:{nid}", title=str(d.get("title") or "")[:120],
                          creator=str(d.get("photographer") or d.get("secondary_creator") or "NASA"), rights_holder="NASA",
                          license="Public domain (NASA media guidelines)", license_url="https://www.nasa.gov/nasa-brand-center/images-and-media/",
                          page=f"https://images.nasa.gov/details/{quote(nid)}", thumb=thumb, query=q, attribution="Credit: NASA",
                          attribution_required=True, description=str(d.get("description") or "")[:300], note="no NASA endorsement implied"))
    return out


def _nasa_file(c: httpx.Client, nid: str, video: bool) -> str:
    r = c.get(f"https://images-api.nasa.gov/asset/{quote(nid)}")
    hrefs = [str(x.get("href")) for x in ((r.json().get("collection") or {}).get("items") or [])] if r.status_code == 200 else []
    prefs = ("~medium.mp4", "~orig.mp4", "~large.mp4", ".mp4") if video else ("~large.jpg", "~orig.jpg", "~medium.jpg", ".jpg")
    for p in prefs:
        hit = next((h for h in hrefs if h.lower().endswith(p)), None)
        if hit:
            return hit.replace("http://", "https://")
    return ""


def pexels(c: httpx.Client, q: str, video: bool, limit: int = 8) -> list[dict[str, Any]]:
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return []
    try:
        r = c.get(f"https://api.pexels.com/{'videos/' if video else 'v1/'}search", params={"query": q, "per_page": limit},
                  headers={"Authorization": key})
        rows = r.json().get("videos" if video else "photos") or [] if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return []
    out = []
    for x in rows:
        if video:
            files = sorted([f for f in x.get("video_files") or [] if f.get("file_type") == "video/mp4"], key=lambda f: abs(int(f.get("height") or 0) - 1920))
            if not files:
                continue
            url, w, h = files[0]["link"], files[0].get("width"), files[0].get("height")
            who = (x.get("user") or {}).get("name") or ""
        else:
            url, w, h, who = (x.get("src") or {}).get("large2x") or "", x.get("width"), x.get("height"), x.get("photographer") or ""
        out.append(record("video" if video else "image", "pexels", url, title=str(x.get("url") or "").rstrip("/").rsplit("/", 1)[-1][:120],
                          creator=who, license="Pexels License", license_url="https://www.pexels.com/license/", page=str(x.get("url") or ""),
                          width=w, height=h, duration=x.get("duration"), thumb=str(x.get("image") or (x.get("src") or {}).get("medium") or ""),
                          query=q, attribution=f"{'Video' if video else 'Photo'}: {who} / Pexels", attribution_required=False))
    return out


def pixabay(c: httpx.Client, q: str, video: bool, limit: int = 8) -> list[dict[str, Any]]:
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return []
    try:
        r = c.get(f"https://pixabay.com/api/{'videos/' if video else ''}", params={"key": key, "q": q[:100], "per_page": max(3, limit), "safesearch": "true"})
        rows = r.json().get("hits") or [] if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return []
    out = []
    for x in rows:
        if video:
            v = (x.get("videos") or {}).get("large") or (x.get("videos") or {}).get("medium") or {}
            url, w, h, thumb = v.get("url") or "", v.get("width"), v.get("height"), v.get("thumbnail") or ""
        else:
            url, w, h, thumb = x.get("largeImageURL") or "", x.get("imageWidth"), x.get("imageHeight"), x.get("webformatURL") or ""
        if not url:
            continue
        out.append(record("video" if video else "image", "pixabay", url, title=str(x.get("tags") or "")[:120], creator=str(x.get("user") or ""),
                          license="Pixabay Content License", license_url="https://pixabay.com/service/license-summary/", page=str(x.get("pageURL") or ""),
                          width=w, height=h, duration=x.get("duration"), thumb=thumb, query=q, attribution_required=False,
                          attribution=f"{'Video' if video else 'Photo'}: {x.get('user') or 'Pixabay'} / Pixabay"))
    return out


def openverse(c: httpx.Client, q: str, limit: int = 10) -> list[dict[str, Any]]:
    try:
        r = c.get("https://api.openverse.org/v1/images/", params={"q": q, "license_type": "commercial,modification", "category": "photograph",
                                                                   "size": "large", "page_size": limit, "mature": "false"})
        rows = r.json().get("results") or [] if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return []
    out = []
    for x in rows:
        lic = f"{str(x.get('license', '')).upper()} {x.get('license_version') or ''}".strip().replace("CC0 1.0", "CC0").replace("BY-SA", "BY-SA")
        lic = re.sub(r"^(BY|BY-SA)\b", r"CC \1", lic)
        if not FREE.match(lic):
            continue
        out.append(record("image", "openverse", str(x.get("url") or ""), title=str(x.get("title") or ""), creator=str(x.get("creator") or ""), license=lic,
                          license_url=str(x.get("license_url") or ""), page=str(x.get("foreign_landing_url") or ""), width=x.get("width"),
                          height=x.get("height"), thumb=str(x.get("thumbnail") or ""), query=q))
    return out


# --- what to look for ------------------------------------------------------------------------------------------------------
def queries(job: dict[str, Any]) -> dict[str, list[str]]:
    """{"exact": [...], "related": [...]}: search terms for the story's exact subject first, then for related real scenes.
    From the local model when it answers, else from the brief's own words."""
    from pricechat_worker import script2

    brief = job.get("brief") or {}
    head = str(brief.get("headline") or job.get("topic") or "")
    subject = str(brief.get("subject") or (brief.get("card") or {}).get("name") or (brief.get("coin") or {}).get("name") or "")
    facts = [str(f.get("text") if isinstance(f, dict) else f) for f in brief.get("facts") or []][:4]
    out: dict[str, Any] = {"exact": [subject] if subject else [], "related": [], "looks_like": str(brief.get("subject_visual") or "")}
    try:
        engine = script2._engines(job)[0]
        text = script2._call(
            engine, "You are a news video researcher. Reply with JSON only.",
            f"Story: {head}\nSubject: {subject or '?'}\n" + "\n".join(facts) + "\n\nWe need REAL footage and photographs (no illustrations). Give search "
            "terms for a media archive like Wikimedia Commons: {\"exact\": [3 terms naming the exact subject: the exact model, product, company, "
            "building, place or person the story is about], \"related\": [3 terms for real scenes that directly support the story: the company's "
            "headquarters, its factory, the city skyline, a trading floor, a grading company's slab...], \"looks_like\": \"one sentence: what a camera "
            "would show of the exact subject (for a market or a company: its real places, buildings, products or people)\"}. 1-4 words each. "
            "No generic words alone.", 0.2)
        data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))  # type: ignore[union-attr]
        if not out["looks_like"] and isinstance(data.get("looks_like"), str):
            out["looks_like"] = data["looks_like"][:200]
        for k in ("exact", "related"):
            for q in data.get(k) or []:
                q = str(q).strip()[:60]
                if q and q.lower() not in (x.lower() for x in out[k]):
                    out[k].append(q)
    except Exception as e:  # noqa: BLE001 - the model is optional: the brief's own words still search
        logger.info("media queries from the brief (%s)", type(e).__name__)
    if brief.get("card"):
        out["related"] = [q for q in out["related"] if "card" in q.lower() or "psa" in q.lower() or "collect" in q.lower()] or \
                         ["PSA graded card", "Pokémon trading cards", "trading card show"]
    if not out["exact"] and head:
        out["exact"] = [" ".join(re.findall(r"[A-Z][\w']+", head)[:3]) or head[:40]]
    return {k: (v[:4] if isinstance(v, list) else v) for k, v in out.items()}


# --- looking at a candidate ----------------------------------------------------------------------------------------------
CHART_RE = re.compile(r"\b(chart|graph|price history|price index|candlestick|infographic|diagram|histogram|plot of|data table)\b", re.I)


def is_chart(a: dict[str, Any]) -> bool:
    """Someone else's chart or infographic (by its file title / description): never B-roll. Charts in a Price Chat video are
    drawn from the story's own data, with their source and date; a stock chart image is undated, unsourced and stale."""
    return bool(CHART_RE.search(f"{a.get('title') or ''} {a.get('description') or ''} {a.get('shows') or ''}"))


def judge(job: dict[str, Any], a: dict[str, Any], jpeg: bytes, looks_like: str = "") -> dict[str, Any]:
    """The local vision model's verdict on a picture: what it shows, and exact / related / generic / unfit for the story."""
    from pricechat_worker import script2

    engine = next((e for e in script2._engines(job) if e.startswith("ollama:")), None)
    brief = job.get("brief") or {}
    subject = str(brief.get("subject") or (brief.get("card") or {}).get("name") or "")
    if not engine:
        return {"match": "related" if a.get("hint_exact") is not True else "exact", "shows": a.get("shows") or a.get("title") or "", "watermark": False}
    prompt = (f"News story: {brief.get('headline') or job.get('topic')}\nExact subject: {subject or 'see the story'}"
              + (f" (on camera: {looks_like})" if looks_like else "") + f"\nFile title: {a.get('title')}\n"
              f"File description: {a.get('description') or a.get('shows') or '-'}\n\nLook at this {'video frame' if a['kind'] == 'video' else 'photo'}. "
              "Reply JSON: {\"shows\": \"what is visible, 8-14 words, concrete\", \"match\": \"exact\" if it clearly shows the exact subject (the exact "
              "model/product/building/place/company), \"related\" ONLY if a viewer would see the connection at a glance (the same company's "
              "products, offices or events; the same city or market; the same hobby's real objects), \"generic\" if it is filler that could illustrate "
              "any story (people at desks, crowds, screens with unrelated software, stock skylines), \"unfit\" if irrelevant or misleading "
              "(it could pass for the subject but isn't), \"watermark\": true|false (a visible stock watermark or logo "
              "burned in), \"burned_text\": true|false (subtitles, captions or big titles are part of the picture), \"synthetic\": true|false "
              "(looks like an illustration, 3D render or AI image), \"chart\": true|false (a chart, graph, price history, infographic, "
              "diagram or data table), \"quality\": 1-5}. If the subject is a market, an industry or a city-wide "
              "trend, real views of that place's own buildings, districts and developments count as exact.")
    try:
        r = httpx.post(f"{os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')}/api/chat", timeout=120.0, json={
            "model": engine.partition(":")[2], "stream": False, "format": "json", **script2.think_option(), "options": {"temperature": 0},
            "messages": [{"role": "user", "images": [base64.b64encode(jpeg).decode()], "content": prompt}]})
        ans = json.loads(r.json()["message"]["content"])
    except Exception as e:  # noqa: BLE001
        logger.info("vision check unavailable (%s)", type(e).__name__)
        return {"match": "related", "shows": a.get("shows") or a.get("title") or "", "watermark": False}
    m = str(ans.get("match") or "related").lower()
    if m not in MATCH_SCORE:
        m = "related"
    if ans.get("synthetic") is True or ans.get("watermark") is True or ans.get("chart") is True or is_chart({**a, "shows": ans.get("shows")}):
        m = "unfit"  # an illustration, a stock watermark, or someone else's chart (Price Chat draws its charts from real data)
    elif ans.get("burned_text") is True:  # someone else's captions in the picture: usable, but ranked below clean footage
        m = {"exact": "related", "related": "generic"}.get(m, m)
    if str(ans.get("quality") or "3").isdigit() and int(ans.get("quality")) <= 2:  # shaky, dark, blurry: below the clean shots
        m = {"exact": "related", "related": "generic"}.get(m, m)
    return {"match": m, "shows": str(ans.get("shows") or "")[:160], "watermark": bool(ans.get("watermark")), "synthetic": bool(ans.get("synthetic")),
            "burned_text": bool(ans.get("burned_text")), "chart": bool(ans.get("chart")) or is_chart({**a, "shows": ans.get("shows")}),
            "quality": int(ans.get("quality") or 3) if str(ans.get("quality") or "3").isdigit() else 3}


def _jpeg(data: bytes, max_side: int = 768) -> bytes | None:
    import io

    from PIL import Image

    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
    except (OSError, ValueError):
        return None
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


# --- downloading ---------------------------------------------------------------------------------------------------------
def _get(c: httpx.Client, url: str, limit: int) -> bytes | None:
    try:
        with c.stream("GET", url) as r:
            if r.status_code != 200:
                return None
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > limit:
                    return None
            return bytes(buf)
    except httpx.HTTPError:
        return None


def probe(ffmpeg: str, path: Path) -> dict[str, Any]:
    p = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True, errors="replace", timeout=60)
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", p.stderr)
    v = re.search(r"Video: .*?(\d{2,5})x(\d{2,5})", p.stderr)
    return {"duration": (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) if m else None,
            "width": int(v.group(1)) if v else 0, "height": int(v.group(2)) if v else 0, "audio": "Audio:" in p.stderr}


def fetch(c: httpx.Client, a: dict[str, Any], public: Path, ffmpeg: str) -> dict[str, Any] | None:
    """Download one accepted asset into the render's public folder (videos: trimmed, H.264, 30 fps). Cached by URL."""
    url = a["url"]
    if url.startswith("nasa:"):
        url = _nasa_file(c, url[5:], a["kind"] == "video")
        if not url:
            return None
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1((url + "|v3").encode()).hexdigest()[:16]  # v2: short side up to 1920 (sharp full-screen crops of 4K footage)
    if a["kind"] == "image":
        cached = CACHE / f"{key}.jpg"
        if not cached.exists():
            data = _get(c, url, 30_000_000)
            if not data:
                return None
            import io

            from PIL import Image

            try:
                im = Image.open(io.BytesIO(data)).convert("RGB")
            except (OSError, ValueError):
                return None
            if max(im.size) > 2600:
                im.thumbnail((2600, 2600), Image.LANCZOS)
            im.save(cached, "JPEG", quality=92)
        from PIL import Image

        with Image.open(cached) as im:
            w, h = im.size
        name = f"{a['id']}.jpg"
        (public / name).write_bytes(cached.read_bytes())
        return {**a, "file": name, "width": w, "height": h, "orientation": orientation(w, h)}
    cached = CACHE / f"{key}.mp4"
    if not cached.exists():
        url = _commons_derivative(c, url)
        raw = CACHE / f"{key}.src"
        data = _get(c, url, 400_000_000)
        if not data:
            return None
        raw.write_bytes(data)
        info = probe(ffmpeg, raw)
        dur = float(info.get("duration") or 0)
        start = 0.0 if dur <= MAX_VIDEO_SECONDS + 2 else min(dur * 0.1, dur - MAX_VIDEO_SECONDS)
        p = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", str(raw), "-t", str(MAX_VIDEO_SECONDS),
                            "-vf", "scale='if(gt(iw,ih),-2,min(1920,iw))':'if(gt(iw,ih),min(1920,ih),-2)',fps=30,format=yuv420p",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-movflags", "+faststart",
                            str(cached)], capture_output=True, timeout=900)
        raw.unlink(missing_ok=True)
        if p.returncode != 0 or not cached.exists():
            cached.unlink(missing_ok=True)
            return None
    info = probe(ffmpeg, cached)
    name = f"{a['id']}.mp4"
    (public / name).write_bytes(cached.read_bytes())
    return {**a, "file": name, "width": info["width"] or a.get("width") or 0, "height": info["height"] or a.get("height") or 0,
            "duration": round(float(info["duration"] or 0), 2), "audio": bool(info["audio"]),
            "orientation": orientation(info["width"] or 1, info["height"] or 1)}


def _commons_derivative(c: httpx.Client, url: str) -> str:
    """An original Commons video (often 4K, hundreds of MB) -> its ~1080p transcode, when Commons has one."""
    m = re.match(r"https://upload\.wikimedia\.org/wikipedia/commons/(?!transcoded/)[0-9a-f]/[0-9a-f]{2}/(.+\.(webm|ogv|ogg|mpg|mpeg))$", url, re.I)
    if not m:
        return url
    from urllib.parse import unquote

    try:
        r = c.get("https://commons.wikimedia.org/w/api.php", params={"action": "query", "format": "json", "titles": "File:" + unquote(m.group(1)),
                                                                     "prop": "videoinfo", "viprop": "derivatives"})
        pages = (r.json().get("query") or {}).get("pages") or {}
        ders = [d for p in pages.values() for d in ((p.get("videoinfo") or [{}])[0].get("derivatives") or []) if str(d.get("type", "")).startswith("video/")]
    except (httpx.HTTPError, ValueError):
        return url
    ders = sorted(ders, key=lambda d: (not 1080 <= int(d.get("height") or 0) <= 2160, -int(d.get("height") or 0)))
    return str(ders[0].get("src")) if ders else url


def _poster(c: httpx.Client, a: dict[str, Any], public: Path, ffmpeg: str) -> bytes | None:
    """A picture to judge: the image itself, a video's poster frame, or a frame from the downloaded clip."""
    if a.get("thumb"):
        data = _get(c, a["thumb"], 8_000_000)
        if data:
            return _jpeg(data)
    if a["kind"] == "image" and not a["url"].startswith("nasa:"):
        data = _get(c, a["url"], 30_000_000)
        return _jpeg(data) if data else None
    got = fetch(c, a, public, ffmpeg)
    if not got:
        return None
    frame = public / f"{a['id']}_poster.jpg"
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", str(max(0.5, (got.get("duration") or 4) * 0.4)), "-i", str(public / got["file"]),
                    "-frames:v", "1", str(frame)], capture_output=True, timeout=60)
    return _jpeg(frame.read_bytes()) if frame.exists() else None


# --- the research run ----------------------------------------------------------------------------------------------------
def research(job: dict[str, Any], public: Path, ffmpeg: str, step: Any = None, max_checks: int = 22) -> list[dict[str, Any]]:
    """Find, check and download the story's authentic media. Returns every asset looked at (rejected ones too, with the
    reason), so the media library shows the editor what was considered. Never raises."""
    brief = job.get("brief") or {}
    library = [dict(x) for x in (job.get("media_library") or []) if x.get("status") != "excluded" and x.get("origin") == "owner"]
    excluded = {x["id"] for x in job.get("media_library") or [] if x.get("status") == "excluded"}
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(items: list[dict[str, Any]]) -> None:
        for a in items:
            k = re.sub(r"\W+", "", (a.get("title") or a["url"]).lower())[:50]
            if a["url"] in seen or k in seen or a["id"] in excluded:
                continue
            if NOT_REAL.search(f"{a.get('title')} {a['url'] if not a['url'].startswith('nasa:') else ''}") and a["provider"] not in ("brief", "library"):
                continue
            seen.update({a["url"], k})
            found.append(a)

    add(from_brief(brief))
    add([{**x, "provider": "library"} for x in library])
    qs = queries(job)
    if step:
        step("visuals", "running", "searching real footage: " + ", ".join(qs["exact"][:2] + qs["related"][:1]))
    try:
        with _client() as c:
            for tier in ("exact", "related"):
                for q in qs[tier]:
                    add(commons(c, q, video=True, limit=10))
                    add(nasa(c, q, video=True, limit=4))
                    add(pexels(c, q, video=True) + pixabay(c, q, video=True))
                    add(commons(c, q, video=False, limit=10))
                    add(openverse(c, q, limit=8))
            generic = {"the", "and", "market", "markets", "real", "estate", "company", "stock", "land", "department", "totals", "news", "price",
                       "sale", "card", "photo", "video", "official"}
            import unicodedata

            def plain(t: str) -> str:  # "Pokémon" == "Pokemon"
                return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()

            keys = {w for q in qs["exact"] + qs["related"] for w in re.findall(r"[a-z0-9]{4,}", plain(q))} - generic

            def named(a: dict[str, Any]) -> bool:
                text = plain(f"{a.get('title')} {a.get('description') or ''} {a.get('shows') if a['provider'] == 'research' else ''}")
                return any(re.search(rf"\b{re.escape(k)}\b", text) for k in keys)

            ranked = sorted(found, key=lambda a: (a["provider"] not in ("brief", "library", "research"), not named(a), a["kind"] != "video",
                                                  -(int(a.get("height") or 0) >= 720), (a.get("query") or "") not in qs["exact"]))
            checked = 0
            out = []
            for a in ranked:
                if a["provider"] in ("brief", "library") and a.get("match"):
                    pass  # the research's own scan / the owner's own pick: trusted, not re-judged
                else:
                    if checked >= max_checks:
                        out.append({**a, "status": "skipped", "note": (a.get("note") + "; " if a.get("note") else "") + "not checked (enough media)"})
                        continue
                    if is_chart(a):
                        out.append({**a, "status": "failed", "match": "unfit", "note": "someone else's chart (Price Chat draws its own from real data)"})
                        continue
                    checked += 1
                    small = (a["kind"] == "image" and max(int(a.get("width") or 0), int(a.get("height") or 0)) and
                             max(int(a.get("width") or 0), int(a.get("height") or 0)) < 900)
                    small = small or (a["kind"] == "video" and 0 < min(int(a.get("width") or 0), int(a.get("height") or 0)) < 540)
                    if small:
                        out.append({**a, "status": "failed", "match": "unfit", "note": "resolution too low"})
                        continue
                    pic = _poster(c, a, public, ffmpeg)
                    if not pic:
                        out.append({**a, "status": "failed", "note": "couldn't be downloaded"})
                        continue
                    v = judge(job, a, pic, qs.get("looks_like") or "")
                    a = {**a, "match": v["match"], "shows": v.get("shows") or a.get("shows") or "", "score": MATCH_SCORE[v["match"]] * (0.7 + 0.06 * v.get("quality", 3)),
                         **({"note": (a.get("note") + "; " if a.get("note") else "") + "has burned-in text"} if v.get("burned_text") else {})}
                    if v["match"] in ("exact", "related") and a["provider"] not in ("brief", "library") and not named(a) and a.get("hint_exact") is not True:
                        v["match"] = "generic"  # nothing in its title or description names the subject: the picture alone isn't proof
                        a = {**a, "match": "generic", "score": MATCH_SCORE["generic"]}
                    if a["provider"] == "research" and a.get("hint_exact") is False and v["match"] == "exact":
                        a = {**a, "match": "related"}  # the researcher said it isn't the exact subject
                    if v["match"] == "unfit":
                        out.append({**a, "status": "failed", "note": "watermark" if v.get("watermark") else "synthetic-looking" if v.get("synthetic")
                                    else "someone else's chart (Price Chat draws its own from real data)" if v.get("chart") else "doesn't fit the story"})
                        continue
                got = fetch(c, a, public, ffmpeg) if not a.get("file") else a
                if not got:
                    out.append({**a, "status": "failed", "note": "download failed"})
                    continue
                if brief.get("card") and got["match"] != "exact":  # another card could pass for the one in the story: say so on screen
                    got["note"] = "not the card in this story"
                out.append({**got, "status": "ok"})
                if step:
                    step("visuals", "running", f"{sum(1 for x in out if x['status'] == 'ok')} real assets · last: {got.get('shows') or got.get('title')}"[:200])
    except Exception as e:  # noqa: BLE001
        logger.warning("media research failed: %s: %s", type(e).__name__, str(e)[:200])
        out = [a for a in found if a.get("file")]
    for a in out:
        a.pop("description", None)
        a.pop("hint_exact", None)
    return out


def public_record(a: dict[str, Any]) -> dict[str, Any]:
    """What the backend's media library stores (no local paths)."""
    return {k: a.get(k) for k in ("id", "kind", "provider", "url", "page", "title", "creator", "rights_holder", "license", "license_url", "permitted",
                                  "attribution", "width", "height", "duration", "orientation", "query", "shows", "match", "score", "note",
                                  "authentic", "audio", "status", "thumb", "origin")}
