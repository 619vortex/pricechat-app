"""The Visual Editor: every picture the desk publishes passes here first (owner, 2026-11-19, URGENT: "Every image must be directly relevant
to the actual event, company, asset or location. Add a semantic image-relevance check BEFORE publishing ... If no suitable image exists,
publish clean text only.")

What went out before this existed (Telegram Pricechatupdates, audited 2026-11-19, 41 picture posts 144-195):
- 195 "Microsoft had rallied back to record highs. Then came the election and Outlook outage" carried "File:A small treehouse on
  Microsoft campus.jpg": a wooden treehouse in a forest. It passed because photos.relevant only asked whether the subject's NAME is in
  the title. A name in a title says where a photo was taken, not that it shows anything a reader would recognise.
- 193 Citi and Circle / World Bank: "Citigroup Center" facade; 190 Nvidia earnings: "Nvidia campus aerial" (sprawl, no brand visible);
  185 a campus street beside BTC candles; 180 the Knesset for a Hamas commander killed in Gaza; 176 Xi in the Kremlin for China-US
  tariff talks; 174 the Tehran skyline for the IAEA story; 166 Hebrew room signage for a strike on a Gaza hospital; 167 a Trump-tweet
  screenshot for Russian diesel exports; 162/164/169/170/171 AI-generated server rooms for crypto stories; 161 an AI-generated oil
  tanker for a real tanker attack.
- 194 gold's -3.6% day: a pale thin line over 120 days in which the day itself was the last few pixels; 173/179/185/190 bare 15-minute
  candles with tiny axis numbers and no figure.

The rules, in the order they run:
1. The story's class (`brief`): a market move, a data post, an outage, a cyberattack, a conflict or disaster, corporate news, other.
   Each class has the only kinds of picture that may illustrate it (`ALLOWED`). Market moves and data are Price Chat's own templates
   from verified numbers (app/newsdesk/templates). Outages are the outage card. Conflicts and disasters: a dated photograph of the event
   itself or nothing (a pre-war photo of a building is not the strike, a skyline is not the war). Corporate news: a photo whose
   SUBJECT is the company (its sign, its headquarters, its product, its named executive) or nothing.
2. The photo's depiction (`depiction`), read from what its title, categories and description say it shows, not merely whether the name
   appears: a STRONG depiction identifies the subject by itself (a sign, a headquarters, a store, a product, the named person, the
   event); a WEAK one may or may not (an office building, a campus, an aerial view: only the vision check can say whether the brand is
   visible); a GENERIC scene never does (a treehouse, a garden, a plaza, a stadium that carries a sponsor's name, a lobby).
3. The vision check (`vision`), when the Anthropic key is set: the model looks at the actual pixels and says whether the subject is
   recognisable, whether it is a generic or stock-looking picture, and whether it could mislead. A WEAK depiction needs its yes; a
   STRONG one can still be vetoed by it. Without the key a WEAK depiction is refused (text only), never waved through.
4. The pixels (`pixel_problem`): sharp enough, not washed out, not a screenshot or a text card.
5. Reuse (`reuse_problem`): one photograph illustrates one story; the same picture under an unrelated story is refused for a year.
6. The final gate (`review`) on the finished picture, every style and every lane: no AI illustration stands in for news, no generic
   headline card, a template must be the current one and readable on a phone (templates.readable), a photo must carry the editor's
   verdict. A refusal makes the post text-only (b""), which every lane already sends cleanly; it never blocks the words, which keep all
   their own checks.

Deterministic except the optional vision call; no keyword ever lets a picture skip a rule."""

from __future__ import annotations

import base64
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

_rx = lambda p: re.compile(p, re.I)  # noqa: E731

# --------------------------------------------------------------------------------------------------- 1. the story's class
# "war" as fighting, not "pre-war production" (post 163), a trade war (post 176), a price war or a war chest
CONFLICT = _rx(r"(?<![-\w])(?<!trade )(?<!price )(?<!bidding )(?<!talent )(?<!culture )(?<!currency )(?:wars?)(?![-\w])(?! chest)(?! room)|"
               r"\b(missiles?|drones?|air ?strikes?|strikes? (?:on|against|in|at)|struck|shelling|bombard\w*|troops|military|army|navy|"
               r"naval|invasion|invad\w*|offensive|ceasefire|hostage\w*|militants?|hamas|hezbollah|houthis?|idf|killed|kills|dead|deaths|"
               r"casualt(?:y|ies)|attack(?:s|ed)? (?:on|against)|gunmen|shooting|bomb\w*|explosion|blast|terror\w*)\b")
DISASTER = _rx(r"\b(earthquakes?|quakes?|tsunami|floods?|flooding|hurricane|typhoon|cyclone|wildfires?|bushfires?|eruption|volcano|landslide|"
               r"tornado|storm|heatwave|drought|derail\w*|plane crash|crash(?:ed)? (?:landing|into)|collapse[ds]?)\b")
# an incident told as background to another story (post 195: "Microsoft had rallied back to record highs. Then came the election and
# Outlook outage" is a stock story; the outage was a week old): never an OUTAGE card stamped with today's time
PAST_CONTEXT = _rx(r"\b(then came|had (?:rallied|risen|climbed|surged|been|hit)|in the wake of|weeks? after|days after|"
                   r"months? after|a (?:week|month) after|looks? back|lessons? from|anniversary)\b")
CLASSES = ("move", "data", "outage", "cyber", "conflict", "disaster", "corporate", "person", "news")
# what may illustrate each class (the picture's `kind`/`layout` after clean.picture): anything else is refused at the final gate
ALLOWED = {
    "move": {"photo", "template", "chart"},
    "data": {"photo", "template", "data"},
    "outage": {"template", "photo"},
    "cyber": {"template", "photo"},
    "conflict": {"photo"},  # only with depiction "event" (dated, of this event)
    "disaster": {"photo"},
    "corporate": {"template", "photo", "chart"},
    "person": {"photo", "template", "chart"},
    "news": {"photo", "template", "chart"},
}


@dataclass
class Brief:
    cls: str
    subject: str = ""  # the company, asset, person or place the picture must show
    incident: Any = None
    text: str = ""
    year: int | None = None
    places: list[str] = field(default_factory=list)


def brief(ev: Any) -> Brief:
    """What the story is, for the picture: its class and the subject a photo would have to show."""
    from app.newsdesk import incidents, visual

    head = f"{getattr(ev, 'headline', '') or ''} {getattr(ev, 'title', '') or ''}".strip()
    kind = getattr(ev, "kind", "") or ""
    when = getattr(ev, "released_at", None) or getattr(ev, "first_seen", None)
    year = when.year if isinstance(when, datetime) else None
    try:
        from app.newsdesk import checks

        places = list(checks.places_in(head))
    except Exception:  # noqa: BLE001
        places = []
    if kind in ("market", "recap"):
        f = getattr(ev, "facts", None) or {}
        return Brief("move", str(f.get("name") or f.get("coin") or ""), text=head, year=year, places=places)
    if kind == "data":
        return Brief("data", text=head, year=year, places=places)
    inc = None
    for t in (getattr(ev, "title", "") or "", getattr(ev, "headline", "") or ""):
        inc = incidents.detect(t, getattr(ev, "summary", "") or "")
        if inc is not None:
            break
    if inc is not None and not inc.hedged and (inc.entities or inc.scope) and not PAST_CONTEXT.search(head):
        who = sorted(inc.entities)[0] if inc.entities else ""
        return Brief("cyber" if inc.kind == "cyber" else "outage", who, inc, head, year, places)
    if CONFLICT.search(head):
        return Brief("conflict", places[0] if places else "", text=head, year=year, places=places)
    if DISASTER.search(head):
        return Brief("disaster", places[0] if places else "", text=head, year=year, places=places)
    m = visual.COMPANIES.search(head)
    if m:
        return Brief("corporate", m.group(0), text=head, year=year, places=places)
    return Brief("news", text=head, year=year, places=places)


# --------------------------------------------------------------------------------------------------- 2. what the photo shows
# words in a photo's title/categories that say it shows the subject itself
SIGN = _rx(r"\b(signs?|signage|storefronts?|store|stores|shop|shops|showroom|flagship|booth|billboards?|branch|branches|atms?|neon|"
           r"entrance sign|name on|lettering)\b")
HQ = _rx(r"\b(headquarters|hq|head office|main office|world headquarters|global headquarters)\b")
HQ_NAMES = _rx(r"\b(apple park|googleplex|amazon spheres|nvidia (?:endeavor|voyager)|tesla gigafactory|gigafactory \w+|"
               r"one microsoft way|microsoft (?:building \d+|commons)|meta (?:hq|headquarters)|1 hacker way)\b")
PRODUCT = _rx(r"\b(iphone|ipad|macbook|apple watch|vision pro|surface (?:pro|laptop)|xbox|hololens|pixel \d|galaxy s\d+|cybertruck|"
              r"model [3sxy]\b|tesla semi|starship|falcon 9|geforce|rtx \d+|gpu|graphics cards?|h100|b200|blackwell|wafers?|chips?|"
              r"ledger nano|trezor)\b")
VENUE = _rx(r"\b(new york stock exchange|nyse|nasdaq marketsite|trading floor|stock exchange|federal reserve (?:building|board)|"
            r"eccles building|bank of england|ecb (?:tower|headquarters)|opec (?:headquarters|secretariat))\b")
WEAK = _rx(r"\b(building|buildings|tower|towers|cent(?:er|re)|office|offices|campus|facility|facilities|plant|factory|data ?cent(?:er|re)|"
           r"aerial|exterior|facade|façade|complex|premises|site)\b")
# a scene that does not identify any company, asset or event, whatever its title is named after
GENERIC = _rx(r"\b(tree ?houses?|trees?|forests?|woods|woodland|gardens?|trails?|paths?|walkways?|boardwalk|plazas?|courtyards?|"
              r"intersections?|roads?|streets?|highways?|expressway|parking|bikes?|bicycles?|cyclists?|sculptures?|statues?|art|artworks?|"
              r"murals?|fountains?|ponds?|lakes?|rivers?|flowers?|benches|bench|lawns?|sunsets?|sunrise|clouds?|sky|"
              r"cafeterias?|kitchens?|lobby|lobbies|interiors?|hallways?|corridors?|stairs?|staircases?|elevators?|desks?|chairs?|"
              r"meeting rooms?|conference rooms?|playgrounds?|pumpkins?|christmas|halloween|parties|party|food|lunch|dinner|dogs?|cats?|"
              r"birds?|stadium|stadiums|field|arena|ballpark|ball ?park|park|bridge|bus|buses|shuttles?|trains?|cranes?|"
              r"neighbou?rhood|residential|apartments?|houses?|homes?|villages?|towns?|countryside|landscape|panorama|"
              r"skyline|cityscape|city view|night view|view from|tourists?|museum|church|cathedral|mosque|temple|palace|parliament)\b")
ADDRESS = _rx(r"(?:streets?|roads?|highways?|expressway|intersections?|avenue|boulevard|plazas?)")
INCIDENT_WORDS = _rx(r"\b(strike|struck|attack(?:ed)?|bomb(?:ed|ing)?|explosion|blast|fire|burning|damage[ds]?|destroyed|ruins|rubble|"
                     r"wreckage|aftermath|flood(?:ed|ing)?|earthquake|collapse[ds]?|evacuat\w*|rescue\w*|protest\w*|crash(?:ed)?)\b")
NOT_CONTENT_TAG = _rx(r"\b(files?|self-published|taken with|uploaded|upload bot|licen[cs]e|cc[- ]|pd[- ]|flickr|panoramio|photographs taken on|"
                      r"featured pictures|quality images|valued images|extracted images|derivative|coordinates|sdc|wikidata|"
                      r"categor|media needing|images? from|^\d{4}\b|\bin \d{4}\b|by \w+)")


# regulators, central banks and exchanges (visual.SUBJECTS): when the institution itself acts, its own landmark building identifies it
INSTITUTIONS = {"eccles|federal reserve", "treasury", "securities|sec ", "commodity futures", "comptroller", "stock exchange|wall street",
                "nasdaq", "european central bank", "bank of england", "bank of japan", "berlaymont"}


def _names(subject: str, text: str) -> bool:
    alts = [a.strip() for a in (subject or "").split("|") if a.strip()]
    return any(re.search(rf"(?<!\w){re.escape(a)}(?!\w)", text, re.I) for a in alts)


@dataclass
class Fit:
    ok: bool
    depiction: str  # strong | weak | generic | none | event | person
    role: str  # sign | headquarters | product | venue | person | event | building | ...
    why: str

    def out(self) -> dict[str, Any]:
        return {"ok": self.ok, "depiction": self.depiction, "role": self.role, "why": self.why}


def _clean_title(title: str) -> str:
    t = re.sub(r"^File:", "", title or "")
    t = re.sub(r"\.(jpe?g|png|webp|tiff?|gif)$", "", t, flags=re.I)
    t = re.sub(r"\s*\((?:\d+|cropped\d*|FL\d+)\)\s*", " ", t, flags=re.I)  # "(3)", "(cropped)", Flickr ids
    return re.sub(r"[_\s]+", " ", t).strip()


def content_tags(tags: Any) -> str:
    """The categories that say what is IN the picture (not "Self-published work", "Taken with Google Pixel 4a")."""
    return " | ".join(str(t) for t in (tags or ()) if t and not NOT_CONTENT_TAG.search(str(t)))


def depiction(title: str, tags: Any, description: str, subject: str, b: Brief, *, person: bool = False) -> Fit:
    """What the photo shows, from its own record, judged against the story. See the module docstring, rule 2."""
    t = _clean_title(title)
    ctags = content_tags(tags)
    desc = (description or "")[:240]
    if person:
        return Fit(True, "person", "person", f"a photo of {subject}, the person the story is about")
    shown = f"{t} | {ctags}"
    named = _names(subject, f"{t} | {ctags} | {desc}")
    # a named landmark headquarters first (Apple Park is a park in name only)
    stripped = HQ_NAMES.sub(" ", t)
    if b.cls in ("conflict", "disaster"):
        inc = INCIDENT_WORDS.search(t)
        same_year = b.year is not None and str(b.year) in f"{t} {desc} {ctags}"
        place = any(_names(p, t) for p in b.places) if b.places else named
        if inc and same_year and place:
            return Fit(True, "event", "event", f"a dated photograph of the event ({inc.group(0)}, {b.year})")
        return Fit(False, "none", "", "a war or disaster needs a dated photograph of the event itself; a place or building photo is not the event")
    generic = [m.group(0).lower() for m in GENERIC.finditer(stripped)]
    strong_word = bool(HQ_NAMES.search(t) or HQ.search(t) or SIGN.search(t))
    scene = [g for g in generic if not (strong_word and ADDRESS.fullmatch(g))]  # "Tesla store, Main Street": the street is its address
    if scene:
        return Fit(False, "generic", scene[0], f"a generic scene ({scene[0]}) does not show {subject or 'the story'}")
    if not named:
        return Fit(False, "none", "", f"the photo's record does not name {subject or 'the subject'}")
    if HQ_NAMES.search(t) or HQ.search(shown):
        return Fit(True, "strong", "headquarters", "the subject's headquarters")
    if SIGN.search(t):
        return Fit(True, "strong", "sign", "the subject's sign or store")
    if PRODUCT.search(t) and b.cls in ("corporate", "news", "outage", "cyber"):
        return Fit(True, "strong", "product", "the subject's product")
    if VENUE.search(t):
        return Fit(True, "strong", "venue", "the market's own venue")
    if subject in INSTITUTIONS and _names(subject, t) and WEAK.search(t):
        return Fit(True, "strong", "institution", "the acting institution's own landmark building")
    w = WEAK.search(t) or WEAK.search(ctags)
    if w:
        return Fit(False, "weak", w.group(0).lower(), f"a {w.group(0).lower()} photo: only a look at the pixels can say whether {subject} is recognisable")
    return Fit(False, "none", "", f"the record names {subject} but says nothing that would identify it in the picture")


# --------------------------------------------------------------------------------------------------- 3. the vision check
def vision_config(settings: Any = None) -> tuple[str, str, str]:
    """(key, model, why-not) for the image check: its own switch, its own key, its own model. Never the writers' anthropic_api_key."""
    from app.security import credentials

    if settings is None:
        from app.config.settings import get_settings

        settings = get_settings()
    if not getattr(settings, "newsdesk_vision_enabled", False):
        return "", "", "the image check is off (NEWSDESK_VISION_ENABLED is not set): no paid call without the owner's approval"
    key = credentials.resolve("newsdesk_vision_api_key", settings) or ""
    model = str(getattr(settings, "newsdesk_vision_model", "") or "")
    if not key or not model:
        return "", "", "the image check is on but has no newsdesk_vision_api_key or model"
    return key, model, ""


VISION_PROMPT = """You are the photo editor of a financial breaking-news channel. A post will carry the attached image.

Story: {story}
The image is supposed to show: {expect}

Look at the image itself, not its file name. Reply with JSON only:
{{"shows_subject": true/false (the subject above is clearly what the picture is of),
 "recognisable": true/false (a reader would recognise the company/person/place/event without being told: visible logo, signage, the person's face, the landmark, the event's damage),
 "generic": true/false (could illustrate any company or story: trees, a lobby, an anonymous office block, a road, stock-photo look),
 "misleading": true/false (implies something the story does not say: a different event, people who are not involved, a fabricated scene),
 "text_heavy": true/false (a screenshot, a document, a chart or a card rather than a photograph),
 "watermark": true/false (a stock-photo watermark or a visible "preview" mark),
 "what": "what the image shows, at most 10 words"}}"""


async def vision(img: Image.Image, story: str, expect: str, *, settings: Any = None, transport: Any = None) -> dict[str, Any] | None:
    """The model's look at the pixels, or None when it cannot look (no key, no model, an error: the deterministic rules then stand)."""
    import httpx

    from app.newsdesk.writer import ANTHROPIC_URL

    try:
        key, model, _ = vision_config(settings)
    except Exception as e:  # noqa: BLE001
        logger.info("visual editor: no vision (%s)", type(e).__name__)
        return None
    if not key:
        return None
    small = img.convert("RGB").copy()
    small.thumbnail((1024, 1024))
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=85)
    body = {"model": model, "max_tokens": 200, "messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}},
        {"type": "text", "text": VISION_PROMPT.format(story=story[:400], expect=expect[:200])}]}]}
    try:
        async with httpx.AsyncClient(timeout=30, transport=transport) as client:
            r = await client.post(ANTHROPIC_URL, headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json=body)
        if r.status_code != 200:
            logger.info("visual editor: vision HTTP %s", r.status_code)
            return None
        text = "".join(b.get("text", "") for b in r.json().get("content") or [] if isinstance(b, dict) and b.get("type") == "text")
        m = re.search(r"\{.*\}", text, re.S)
        out = json.loads(m.group(0)) if m else None
        return out if isinstance(out, dict) else None
    except (httpx.HTTPError, ValueError) as e:
        logger.info("visual editor: vision %s", type(e).__name__)
        return None


def vision_verdict(v: dict[str, Any] | None, fit: Fit) -> tuple[bool, str]:
    """Combine the record's depiction with the model's look. A weak depiction needs a clear yes; anything the model calls generic,
    misleading or text-heavy is refused."""
    if v is None:
        return (fit.ok, fit.why if fit.ok else f"{fit.why}; no vision check available, so it is not used")
    what = str(v.get("what") or "")[:80]
    if v.get("misleading"):
        return False, f"vision: could mislead ({what})"
    if v.get("watermark"):
        return False, f"vision: watermarked ({what})"
    if v.get("text_heavy"):
        return False, f"vision: a screenshot, document or card, not a photograph ({what})"
    if v.get("generic"):
        return False, f"vision: generic picture ({what})"
    if not (v.get("shows_subject") and v.get("recognisable")):
        return False, f"vision: the subject is not recognisable ({what})"
    return True, f"vision: {what}"


# --------------------------------------------------------------------------------------------------- 4. the pixels
# edge sharpness: the mean of the strongest 2% of Laplacian responses at 800 px wide. A whole-picture variance called clean, dark or
# minimal compositions "blurry" (a Microsoft Store wall, a nugget on black); the sharpest edges don't care how much of the frame is
# empty. Calibrated 2026-11-19: the 41 audit pictures 132 or more, the library photos 108 or more, the same photos blurred 2 px 32.
SHARP_MIN = 60.0
CONTRAST_MIN = 26.0  # luminance standard deviation: below this the picture is washed out
UI_FLAT_MAX = 0.09  # share of the picture in perfectly flat near-white blocks: post 167 (a tweet screenshot) 0.10, real photos at most 0.05


def pixel_problem(img: Image.Image, ui: bool = True, sharp_min: float = SHARP_MIN) -> str | None:
    g = img.convert("L")
    if g.width > 800:
        g = g.resize((800, max(1, round(g.height * 800 / g.width))))
    a = np.asarray(g, dtype=np.float64)
    lap = np.abs(a[1:-1, 1:-1] * -4 + a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:]).ravel()
    edge = float(np.mean(np.partition(lap, lap.size - max(1, lap.size // 50))[-max(1, lap.size // 50):]))
    if edge < sharp_min:
        return f"blurry (edge sharpness {edge:.0f} < {sharp_min:.0f})"
    if a.std() < CONTRAST_MIN:
        return f"washed out (contrast {a.std():.0f} < {CONTRAST_MIN:.0f})"
    h, w = (a.shape[0] // 8) * 8, (a.shape[1] // 8) * 8
    blocks = a[:h, :w].reshape(h // 8, 8, w // 8, 8).swapaxes(1, 2)
    flat_white = (blocks.std(axis=(2, 3)) < 1.0) & (blocks.mean(axis=(2, 3)) > 225)
    if ui and flat_white.mean() > UI_FLAT_MAX:
        return f"looks like a screenshot or a text card ({flat_white.mean():.0%} flat white)"
    return None


# --------------------------------------------------------------------------------------------------- 5. reuse
REUSE_DAYS = 365


def reuse_problem(url: str, ev: Any, records: list[Any], now: datetime | None = None) -> str | None:
    """The same photograph under a different story within REUSE_DAYS. records: [url, at, title, key] (older [url, at] pairs count as
    a different story: their story is unknown)."""
    from app.newsdesk import newsworthy

    if not url:
        return None
    now = now or datetime.now(timezone.utc)
    mine = newsworthy.event_of(f"{getattr(ev, 'headline', '') or ''}") if getattr(ev, "headline", "") else None
    for r in records or []:
        if not r or r[0] != url:
            continue
        try:
            at = datetime.fromisoformat(str(r[1]).replace("Z", "+00:00"))
        except ValueError:
            continue
        at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
        if now - at > timedelta(days=REUSE_DAYS):
            continue
        key = r[3] if len(r) > 3 else ""
        if key and key == getattr(ev, "key", None):
            continue  # the same story again (an update, X after Telegram)
        title = r[2] if len(r) > 2 else ""
        if title and mine is not None and newsworthy.same_event(mine, newsworthy.event_of(title)):
            continue
        return f"this photo already illustrated another story ({(title or 'unknown')[:60]}, {at:%d %b})"
    return None


# --------------------------------------------------------------------------------------------------- 6. the final gate
AI_KINDS = {"cinematic", "artwork", "graphic", "card"}


def review(ev: Any, image: bytes, meta: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """The last look before any lane publishes the picture. Returns the picture, or b"" (text only) with the reason in meta["editor"]."""
    if not image:
        return image, meta
    b = brief(ev)
    kind, layout = str(meta.get("kind") or ""), str(meta.get("layout") or "")
    why = ""
    if meta.get("ai_illustration") or kind in AI_KINDS or layout == "ai":
        why = "an AI illustration or a headline card does not stand in for news (owner, 2026-11-19)"
    elif meta.get("template"):
        from app.newsdesk import templates

        why = templates.readable(meta) or ""
        if not why and "template" not in ALLOWED.get(b.cls, set()):
            why = f"a {meta['template']} graphic does not fit a {b.cls} story"
    elif kind == "photo" or layout in ("photo", "collage"):
        verdict = meta.get("editor") or {}
        if not verdict.get("ok"):
            # a photo chosen before the editor existed, or by a path that did not ask it: judge its record now (no vision here)
            from app.newsdesk import visual

            person = bool(meta.get("person"))
            photo = meta.get("photo") or {}
            terms = [str(meta.get("person"))] if person else list(dict.fromkeys([b.subject, *(m for _, m in visual.subject_queries(b.text))]))
            fits = [depiction(str(meta.get("title") or ""), photo.get("tags") or (), str(photo.get("description") or ""), term, b, person=person)
                    for term in terms if term]
            good = [x for x in fits if x.ok and x.depiction in ("strong", "person", "event")]
            if not good:
                why = (fits[0].why if fits else "the photo's subject is unknown")
        if not why and not meta.get("library") and not (meta.get("editor") or {}).get("vision"):
            why = "not visually verified and not an editor-approved library photo (owner, 2026-11-19)"
        if not why and "photo" not in ALLOWED.get(b.cls, set()):
            why = f"a photograph does not fit a {b.cls} story"
        if not why:
            try:
                lib = bool(meta.get("library"))  # an editor-vetted library photograph (app/newsdesk/photography): only a broken download fails
                why = pixel_problem(Image.open(io.BytesIO(image)), ui=not lib, sharp_min=35.0 if lib else SHARP_MIN) or ""
            except Exception:  # noqa: BLE001
                why = "the picture could not be read"
    elif kind in ("chart", "data", "timeline"):
        if b.cls in ("conflict", "disaster") and kind == "chart":
            why = "a price chart does not illustrate a war or a disaster"
    if why:
        logger.info("visual editor: event %s goes without a picture: %s", getattr(ev, "id", None), why)
        return b"", {**meta, "kind": "text", "layout": "text", "editor": {"ok": False, "why": why, "class": b.cls, "was": kind or layout}}
    return image, {**meta, "editor": {**(meta.get("editor") or {}), "ok": True, "class": b.cls}}


# ---------------------------------------------------------------------------------------------------- production self-test
VISION_STATUS: dict[str, Any] = {"state": "unknown"}
NEGATIVE = "File:A small treehouse on Microsoft campus.jpg"  # post 195's photo: the model must refuse it for a Microsoft story


async def self_test(settings: Any = None, transport: Any = None) -> dict[str, Any]:
    """Once after each start: does visual verification work HERE? One look at the library's gold photograph (must be recognised as gold)
    and one at post 195's treehouse against "Microsoft" (must be refused). The result is logged ("visual editor self-test: ...") and
    kept in VISION_STATUS. Without a working check, only library photos are published (clean.find_photo and review refuse the rest)."""
    import httpx

    from app.newsdesk import photography, photos

    try:
        if settings is None:
            from app.config.settings import get_settings

            settings = get_settings()
        key, _, why_not = vision_config(settings)
        if not key:  # off or unconfigured: no paid call at all, not even the self-test
            out = {"state": "unavailable", "why": f"{why_not}: library photos or text only"}
        else:
            gold = await photography.library_photo(photography.BY_KEY["gold"], transport=transport)
            if gold is None:
                out = {"state": "untested", "why": "the gold library photo could not be read from Commons"}
            else:
                yes = await vision(gold[0], "Gold's biggest one-day drop since March", "gold: the metal itself (nuggets, bars, coins)",
                                   settings=settings, transport=transport)
                no = None
                async with httpx.AsyncClient(transport=transport) as client:
                    r = await client.get(photos.COMMONS, params={"action": "query", "format": "json", "titles": NEGATIVE, "prop": "imageinfo",
                                                                 "iiprop": "url", "iiurlwidth": "1280"}, headers=photos.UA, timeout=photos.TIMEOUT)
                    page = next(iter(((r.json().get("query") or {}).get("pages") or {}).values()), {})
                    url = ((page.get("imageinfo") or [{}])[0]).get("thumburl")
                    if url:
                        img = Image.open(io.BytesIO((await client.get(url, headers=photos.UA, timeout=photos.TIMEOUT)).content)).convert("RGB")
                        no = await vision(img, "Microsoft had rallied back to record highs. Then came the election and Outlook outage",
                                          "Microsoft: its recognisable branding (logo, signage, store, headquarters) or its own product", settings=settings, transport=transport)
                fit = Fit(True, "strong", "subject", "")
                passed_yes = yes is not None and vision_verdict(yes, fit)[0]
                refused_no = no is not None and not vision_verdict(no, fit)[0]
                out = {"state": "working" if passed_yes and refused_no else "unreliable" if yes is not None else "failing",
                       "gold": (yes or {}).get("what"), "treehouse": (no or {}).get("what"), "gold_ok": passed_yes, "treehouse_refused": refused_no}
    except Exception as e:  # noqa: BLE001 - a self-test never stops anything
        out = {"state": "failing", "why": type(e).__name__}
    VISION_STATUS.clear()
    VISION_STATUS.update({**out, "at": datetime.now(timezone.utc).isoformat()})
    level = logging.INFO if out["state"] == "working" else logging.WARNING
    logger.log(level, "visual editor self-test: vision %s %s", out["state"].upper(), json.dumps({k: v for k, v in out.items() if k != "state"}))
    return out


__all__ = ["Brief", "Fit", "brief", "depiction", "pixel_problem", "reuse_problem", "review", "vision", "vision_verdict"]
