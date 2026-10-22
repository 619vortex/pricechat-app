"""Real pictures and live data for a video (Content Studio v2.1), fetched by the worker before the script is written.

  · coin      the story's coin (from the brief, or named in the headline): its logo (CoinGecko) and Hyperliquid's live
              price, 24h change and 48 h hourly chart. Shown labelled as live Hyperliquid data at render time.
  · photos    freely licensed photographs (Openverse, then Wikimedia Commons): commercial use and modification allowed,
              photographs only (no diagrams, maps, screenshots or other people's charts), each with its credit and
              licence, which the video shows on screen.

Nothing here is invented: a missing piece is simply absent and the storyboard falls back to what is there. The spoken
script never uses these numbers (the backend fact-checks it against the research brief only)."""

import io
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)
UA = {"User-Agent": "pricechat-worker/2.1 (https://pricechat.live)"}
COINS = {"bitcoin": "BTC", "btc": "BTC", "ethereum": "ETH", "ether": "ETH", "eth": "ETH", "solana": "SOL", "sol": "SOL", "xrp": "XRP",
         "ripple": "XRP", "dogecoin": "DOGE", "doge": "DOGE", "hyperliquid": "HYPE", "hype": "HYPE", "sui": "SUI", "chainlink": "LINK",
         "cardano": "ADA", "bnb": "BNB", "avalanche": "AVAX", "tron": "TRX", "litecoin": "LTC", "polkadot": "DOT", "pepe": "PEPE",
         "shiba": "SHIB", "toncoin": "TON", "aptos": "APT", "arbitrum": "ARB", "near": "NEAR", "bonk": "BONK", "ena": "ENA", "ethena": "ENA",
         "gold": "PAXG", "tether": None, "usdt": None, "usdc": None}
HL_NAME = {"PEPE": "kPEPE", "SHIB": "kSHIB", "BONK": "kBONK"}  # Hyperliquid lists the small-price coins per 1,000
NOT_PHOTO = re.compile(r"\b(chart|graph|price|map|diagram|logo|icon|screenshot|status|legal|transaction|infographic|svg|flag|"
                       r"wallet_cons|table|plot|banner|meme)\b", re.I)
MAX_PHOTOS = 5


def _client() -> httpx.Client:
    return httpx.Client(timeout=15.0, headers=UA, follow_redirects=True)


def coin_of(brief: dict[str, Any]) -> str | None:
    sym = str((brief.get("coin") or {}).get("symbol") or "").upper()
    if sym:
        return sym
    text = " ".join([str(brief.get("headline") or "")] + [str(f.get("text") if isinstance(f, dict) else f) for f in brief.get("facts") or []][:3])
    for w in re.findall(r"[A-Za-z]+", text.lower()):
        if w in COINS:
            return COINS[w]
    return None


def live_coin(c: httpx.Client, sym: str) -> dict[str, Any] | None:
    """Hyperliquid's 48 hourly closes, the last price and the 24h change for a perp (None if it isn't listed)."""
    now = int(time.time() * 1000)
    name = HL_NAME.get(sym, sym)
    try:
        r = c.post("https://api.hyperliquid.xyz/info", json={"type": "candleSnapshot", "req": {"coin": name, "interval": "1h",
                                                                                                "startTime": now - 49 * 3600_000, "endTime": now}})
        rows = r.json() if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(rows, list) or len(rows) < 25:
        return None
    pts = [[round(int(x["t"]) / 1000), float(x["c"])] for x in rows][-49:]
    last, day = pts[-1][1], pts[-25][1]
    return {"symbol": sym, "price_usd": last, "change_24h_pct": round((last / day - 1) * 100, 2) if day else None,
            "chart": {"points": pts, "label": f"{sym} · last 48h · Hyperliquid, live"}, "as_of": pts[-1][0]}


def coin_logo(c: httpx.Client, sym: str, name: str | None, public: Path) -> str | None:
    try:
        r = c.get("https://api.coingecko.com/api/v3/search", params={"query": name or sym})
        hit = next((x for x in (r.json().get("coins") or []) if str(x.get("symbol", "")).upper() == sym), None) if r.status_code == 200 else None
        if not hit or not str(hit.get("large", "")).startswith("https://"):
            return None
        img = c.get(hit["large"])
        if img.status_code != 200 or not img.headers.get("content-type", "").startswith("image/"):
            return None
        from PIL import Image

        im = Image.open(io.BytesIO(img.content)).convert("RGBA")
        im = im.resize((512, int(512 * im.height / im.width)), Image.LANCZOS) if im.width < 512 else im
        im.save(public / "coin.png")
        return "coin.png"
    except (httpx.HTTPError, OSError, ValueError):
        return None


def photo_queries(job: dict[str, Any], brief: dict[str, Any], coin: str | None) -> list[str]:
    """Short, photographable search terms for the story: from the local model when it answers, else from the brief."""
    from pricechat_worker import script2

    head = str(brief.get("headline") or "")
    facts = [str(f.get("text") if isinstance(f, dict) else f) for f in brief.get("facts") or []][:4]
    out: list[str] = []
    try:
        engine = script2._engines(job)[0]
        text = script2._call(
            engine, "You pick stock-photo search terms. Reply with JSON only: {\"queries\": [\"...\"]}.",
            "Story: " + head + "\n" + "\n".join(facts) + "\n\nGive 4 search queries (1-3 plain words each) for real PHOTOGRAPHS that "
            "would illustrate this story in a news video: physical things and places a camera can shoot (e.g. \"bitcoin coin\", "
            "\"trading screen\", \"wall street\", \"treasury building\"). No people's names, no charts, no logos.", 0.3)
        out = [str(q).strip() for q in (json.loads(re.search(r"\{.*\}", text, re.S).group(0)).get("queries") or []) if str(q).strip()]  # type: ignore[union-attr]
    except Exception as e:  # noqa: BLE001 - the model is optional here: the brief's own words still find photos
        logger.info("photo queries from the brief (%s)", type(e).__name__)
    name = {"BTC": "bitcoin", "ETH": "ethereum", "PAXG": "gold bars"}.get(coin or "", (brief.get("coin") or {}).get("name") or "")
    for q in ([f"{name} coin" if name and coin != "PAXG" else name] if name else []) + ["cryptocurrency", "stock market screen"]:
        if q and q not in out:
            out.append(q)
    return [q[:40] for q in out][:6]


def _openverse(c: httpx.Client, q: str) -> list[dict[str, Any]]:
    try:
        r = c.get("https://api.openverse.org/v1/images/", params={"q": q, "license_type": "commercial,modification", "category": "photograph",
                                                                   "size": "large", "page_size": 12, "mature": "false"})
        rows = r.json().get("results") or [] if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return []
    return [{"url": x["url"], "w": x.get("width") or 0, "h": x.get("height") or 0, "title": x.get("title") or "", "creator": x.get("creator") or "",
             "license": f"{str(x.get('license', '')).upper()} {x.get('license_version') or ''}".strip().replace("CC0 1.0", "CC0"),
             "page": x.get("foreign_landing_url") or "", "source": x.get("source") or "openverse"} for x in rows]


def _commons(c: httpx.Client, q: str) -> list[dict[str, Any]]:
    try:
        r = c.get("https://commons.wikimedia.org/w/api.php", params={"action": "query", "format": "json", "generator": "search",
                                                                     "gsrsearch": f"{q} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": 10,
                                                                     "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1400})
        pages = (r.json().get("query") or {}).get("pages") or {} if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return []
    out = []
    for p in pages.values():
        ii = (p.get("imageinfo") or [{}])[0]
        m = ii.get("extmetadata") or {}
        lic = str((m.get("LicenseShortName") or {}).get("value") or "")
        if ii.get("mime") != "image/jpeg" or not re.match(r"(CC0|CC BY(-SA)? \d|Public domain)", lic, re.I):
            continue
        out.append({"url": ii.get("thumburl") or ii.get("url"), "w": ii.get("width") or 0, "h": ii.get("height") or 0, "title": p.get("title") or "",
                    "creator": re.sub(r"<[^>]+>", "", str((m.get("Artist") or {}).get("value") or "")).strip()[:60], "license": lic,
                    "page": ii.get("descriptionurl") or "", "source": "wikimedia"})
    return out


def fits(job: dict[str, Any], story: str, jpeg: bytes) -> bool:
    """The local vision model looks at the photo: does it fit the story? (Unsure or no model: kept.)"""
    import base64
    import os

    from pricechat_worker import script2

    engine = next((e for e in script2._engines(job) if e.startswith("ollama:")), None)
    if not engine:
        return True
    try:
        r = httpx.post(f"{os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')}/api/chat", timeout=90.0, json={
            "model": engine.partition(":")[2], "stream": False, "format": "json", **script2.think_option(), "options": {"temperature": 0},
            "messages": [{"role": "user", "images": [base64.b64encode(jpeg).decode()], "content":
                          f"Story for a short news video: {story}\n\nWould this photo be a fitting, relevant background for that story (the "
                          "objects, places or markets in it)? Reply JSON {\"fit\": true|false, \"what\": \"5 words\"}."}]})
        ans = json.loads(r.json()["message"]["content"])
        logger.info("photo check: %s -> %s", ans.get("what"), ans.get("fit"))
        return ans.get("fit") is not False
    except Exception:  # noqa: BLE001
        return True


def photos(c: httpx.Client, queries: list[str], public: Path, n: int = MAX_PHOTOS, job: dict[str, Any] | None = None,
           story: str = "") -> list[dict[str, Any]]:
    """Up to n distinct licensed photos (at least 1000 px wide) that fit the story, saved as photo_k.jpg, with credit lines."""
    from PIL import Image

    got: list[dict[str, Any]] = []
    seen: set[str] = set()
    for q in queries:
        found = _openverse(c, q) or _commons(c, q)
        for x in found:
            if len(got) >= n or sum(1 for g in got if g["query"] == q) >= 2:
                break
            key = re.sub(r"\W+", "", (x["title"] or x["url"]).lower())[:40]
            if not x["url"] or key in seen or x["w"] < 1000 or NOT_PHOTO.search(f"{x['title']} {x['url']}") or x["url"].lower().endswith((".svg", ".png", ".gif")):
                continue
            try:
                r = c.get(x["url"])
                if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/") or len(r.content) > 15_000_000:
                    continue
                im = Image.open(io.BytesIO(r.content)).convert("RGB")
            except (httpx.HTTPError, OSError, ValueError):
                continue
            if im.width < 900 or im.height < 600:
                continue
            scale = 1400 / max(im.width, im.height)
            if scale < 1:
                im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
            seen.add(key)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=90)
            if job is not None and story and not fits(job, story, buf.getvalue()):
                continue
            name = f"photo_{len(got)}.jpg"
            (public / name).write_bytes(buf.getvalue())
            who = x["creator"] or "unknown"
            got.append({"src": name, "ratio": round(im.height / im.width, 4), "query": q, "credit": f"Photo: {who} · {x['license']}",
                        "page": x["page"], "source": x["source"]})
        if len(got) >= n:
            break
    return got


def gather(job: dict[str, Any], public: Path) -> dict[str, Any]:
    """Everything above for one job (each piece optional). Never raises: a failed lookup only means fewer visuals."""
    brief = job.get("brief") or {}
    out: dict[str, Any] = {}
    try:
        with _client() as c:
            sym = coin_of(brief)
            if sym and not brief.get("card"):
                live = live_coin(c, sym)
                if live:
                    out["coin"] = live
                    logo = coin_logo(c, sym, (brief.get("coin") or {}).get("name"), public)
                    if logo:
                        out["coin"]["logo"] = logo
            if not brief.get("card"):  # a card story shows its own verified card; photos of other cards could pass for it
                out["photos"] = photos(c, photo_queries(job, brief, sym), public, job=job, story=str(brief.get("headline") or job.get("topic") or ""))
    except Exception as e:  # noqa: BLE001
        logger.warning("media lookup failed: %s: %s", type(e).__name__, str(e)[:160])
    return out
