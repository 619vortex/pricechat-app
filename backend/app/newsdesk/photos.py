"""Real photographs for News Desk posts: discovered automatically, published only with verified reuse rights and a proper credit.

Owner, 2026-11-11: "Real photos first. Automatically discover relevant photographs, but only publish images with verified commercial reuse
rights and proper credits. Never use unrelated or misleading photos. Headlines must not obscure important photographic subjects."

The rules (they generalise the Wikimedia-only rules in `visual.py`):
- Licence (`verify_licence`): only CC0, public domain (Public Domain Mark, PD), CC BY 2.0-4.0, CC BY-SA 2.0-4.0, the Pexels licence and
  the Unsplash licence. Never NC, ND, "all rights reserved", GFDL-only, fair use, non-free, or a licence we cannot read. The licence name,
  its URL, the creator, the source page, the provider and when we looked (`retrieved_at`) travel with the photo (`LicensedPhoto.to_meta`),
  so every credit can be audited later.
- Relevance (`relevant`): the subject (a company, an institution, a place) must be named as a whole word in the title, the human tags or
  categories, or the description. Never a generic stand-in: the server room of some other institution is not a picture of a hacked
  exchange (the 2026-11-09 Mercado Bitcoin post carried CERN's data centre). No logos, maps, diagrams, screenshots, charts, illustrations,
  drawings, renders, clip art, flags, memes; colour, 1995 or later, at least MIN_WIDTH wide, landscape. A photo of a person only when that
  person is the subject. The same photo is not used again within USED_DAYS (the caller passes the used URLs).
- Pictures (`smart_crop`, `text_zone`, `covers`): crops, resizing and mild global tone only. Factual pixels are never altered, nothing is
  generated. The crop keeps the photograph's subject inside the 16:9 frame and says where a headline can sit without covering it.

Providers: Wikimedia Commons and Openverse (anonymous, both searched in parallel), Pexels when PEXELS_API_KEY is set.
- Unsplash is left out on purpose. Its API guidelines require that every use shows the hotlinked image URL returned by the API and that
  the API serve "non-automated" experiences; the desk downloads a photo, crops it, sets a headline on it and uploads the result to
  Telegram and X automatically, which is neither. The licence itself ("Unsplash License") is still recognised by `verify_licence`, so a
  photo licensed that way and stored earlier still audits correctly. Revisit only with Unsplash's written agreement.
- Google Images is not used and must not be scraped: its "usage rights" filter is a search hint, not a licence verification, scraping
  breaks Google's terms, and the Custom Search JSON API is closed to new customers.
"""

from __future__ import annotations

import asyncio
import html
import io
import logging
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx
import numpy as np
from PIL import Image, ImageEnhance, ImageOps

logger = logging.getLogger(__name__)

UA = {"User-Agent": "PriceChatNewsDesk/1.0 (https://pricechat.live)"}
COMMONS = "https://commons.wikimedia.org/w/api.php"
OPENVERSE = "https://api.openverse.org/v1/images/"
PEXELS = "https://api.pexels.com/v1/search"
PEXELS_LICENCE_URL = "https://www.pexels.com/license/"
UNSPLASH_LICENCE_URL = "https://unsplash.com/license"
MIN_WIDTH = 1400
MIN_ASPECT = 1.2  # width / height: landscape enough to crop to 16:9 without losing the scene
MIN_YEAR = 1995
USED_DAYS = 14
MAX_QUERIES = 4  # per provider per discovery: Openverse's anonymous limits are small
TIMEOUT = 20

# provider trust for ranking: Commons and Openverse records from Flickr that carry a licence URL first
TRUST = {"wikimedia": 1.0, "openverse:flickr": 0.9, "openverse:wikimedia": 0.95, "openverse": 0.7, "pexels": 0.6, "unsplash": 0.6}
SOURCE_NAMES = {"wikimedia": "Wikimedia Commons", "flickr": "Flickr", "pexels": "Pexels", "unsplash": "Unsplash", "nasa": "NASA",
                "smithsonian": "Smithsonian", "europeana": "Europeana", "geographorg": "Geograph", "stocksnap": "StockSnap", "rawpixel": "rawpixel"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _plain(text: Any) -> str:
    """HTML (Commons' Artist and ImageDescription are HTML) to one line of plain text."""
    s = re.sub(r"<[^>]+>", " ", str(text or ""))
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


@dataclass
class LicensedPhoto:
    title: str
    url: str  # the image to download (a ~1920 px rendition when the provider offers one)
    page: str  # the source page that states the licence
    creator: str
    provider: str  # "wikimedia", "openverse", "pexels"
    licence: str  # as the provider states it ("CC BY-SA 4.0", "by" + "2.0", "Pexels License")
    licence_url: str = ""
    source: str = ""  # where the photo lives ("flickr", "wikimedia"); for Commons and Pexels the provider itself
    width: int = 0
    height: int = 0
    description: str = ""
    tags: tuple[str, ...] = ()  # human tags and categories only (Openverse's machine-generated tags are dropped)
    year: int | None = None
    mime: str = ""
    restrictions: str = ""
    query: str = ""
    retrieved_at: str = field(default_factory=_now)
    score: float = 0.0

    @property
    def licence_name(self) -> str:
        """The licence in its canonical short form ("CC BY 2.0", "CC0", "public domain", "Pexels License"), or "" when not allowed."""
        return canonical_licence(self.licence, self.licence_url) or ""

    @property
    def source_name(self) -> str:
        key = (self.source or self.provider).lower()
        return SOURCE_NAMES.get(key, key.title() if key else "")

    @property
    def credit(self) -> str:
        """Short enough for the corner of the picture: "Photo: Jane Doe / Flickr (CC BY 2.0)", "Photo: Wikimedia Commons (public domain)"."""
        who = _plain(self.creator)
        if re.fullmatch(r"(unknown|anonymous|unknown author|author unknown|n/?a)\W*", who, re.I):
            who = ""
        who = re.sub(r"\s*\((?:talk|contribs)\)", "", who)
        who = re.sub(r"^photo(?:graph)?\s*(?:by|:)\s*", "", who, flags=re.I)
        who = re.sub(r"^(.{2,30}?)\s+photo(?:graph)?\s+by\b.*$", r"\1", who, flags=re.I)  # "U.S. Navy photo by Petty Officer ..." -> "U.S. Navy"
        who = re.sub(r"^Official (.{2,30}?) (?:Page|Imagery) from\b.*$", r"\1", who, flags=re.I)  # Flickr imports of official accounts
        if len(who) > 32:
            cut = who[:32].rsplit(" ", 1)[0] if " " in who[:32] else who[:31]
            who = cut.rstrip(" ,;:/-") + "…"
        lic = self.licence_name or self.licence
        where = self.source_name
        return f"Photo: {who + ' / ' if who and who.lower() != where.lower() else ''}{where} ({lic})"

    def to_meta(self) -> dict[str, Any]:
        """Everything needed to audit the credit later; stored on the post."""
        return {"url": self.url, "page": self.page, "title": self.title, "creator": _plain(self.creator), "provider": self.provider,
                "source": self.source, "licence": self.licence_name or self.licence, "licence_raw": self.licence,
                "licence_url": self.licence_url, "retrieved_at": self.retrieved_at, "width": self.width, "height": self.height,
                "credit": self.credit}


# --- licence ------------------------------------------------------------------------------------------------------------

_DENY = re.compile(r"\bnc\b|non[- ]?commercial|\bnd\b|no[- ]?deriv|all rights reserved|fair[- ]use|non[- ]?free|copyrighted free use"
                   r"|editorial use only|\barr\b|by-nc|by-nd|nc-sa|nc-nd", re.I)
_DENY_URL = re.compile(r"/licenses/(by-nc|by-nd|by-nc-sa|by-nc-nd)/|/sampling|nonfree|fair_use", re.I)
_CC = re.compile(r"^(?:cc[- ]?)?(by(?:[- ]sa)?)[- ]?(\d(?:\.\d)?)?(?:[- ][a-z]{2}(?:-[a-z]{2})?)?$", re.I)
_CC_URL = re.compile(r"creativecommons\.org/licenses/(by(?:-sa)?)/(\d\.\d)", re.I)
CC_VERSIONS = {"2.0", "2.5", "3.0", "4.0"}


def canonical_licence(name: str, url: str = "") -> str | None:
    """The allowed licence's short name, or None when the licence is not on the allow-list."""
    raw = re.sub(r"\s+", " ", _plain(name)).strip()
    if not raw or _DENY.search(raw) or (url and _DENY_URL.search(url)):
        return None
    low = raw.lower()
    if low in ("cc0", "cc0 1.0", "cc-zero", "cc zero", "cc0-1.0") or "publicdomain/zero" in url:
        return "CC0"
    if low in ("pdm", "pdm 1.0", "pd", "public domain", "public domain mark", "public domain mark 1.0", "publicdomain") \
            or low.startswith("public domain") or "publicdomain/mark" in url:
        return "public domain"
    if low.startswith("pexels"):
        return "Pexels License"
    if low.startswith("unsplash"):
        return "Unsplash License"
    m = _CC.match(low.replace("attribution-sharealike", "by-sa").replace("attribution", "by"))
    if m:
        kind, ver = m.group(1).replace(" ", "-"), m.group(2) or ""
        if not ver:
            u = _CC_URL.search(url or "")
            ver = u.group(2) if u and u.group(1).lower() == kind else ""
        if ver in ("2", "3", "4"):
            ver += ".0"
        if ver in CC_VERSIONS:
            return f"CC {kind.upper()} {ver}"
    return None  # GFDL-only, CC BY 1.0, "Attribution" with no version, anything we cannot read


def verify_licence(photo: LicensedPhoto) -> str | None:
    """None when the photo may be published with its credit, otherwise why not."""
    if canonical_licence(photo.licence, photo.licence_url) is None:
        return f"licence not allowed: {photo.licence or 'unknown'}"
    if not photo.page.startswith("http"):
        return "no source page to audit the licence"
    if re.search(r"trademark", photo.restrictions or "", re.I):
        return "trademark restriction (a logo)"
    lic = canonical_licence(photo.licence, photo.licence_url) or ""
    if lic.startswith("CC BY") and not photo.licence_url:
        photo.licence_url = f"https://creativecommons.org/licenses/{lic[3:].split()[0].lower()}/{lic.split()[-1]}/"
    if lic == "Pexels License" and not photo.licence_url:
        photo.licence_url = PEXELS_LICENCE_URL
    if lic == "Unsplash License" and not photo.licence_url:
        photo.licence_url = UNSPLASH_LICENCE_URL
    return None


# --- relevance ----------------------------------------------------------------------------------------------------------

NOT_PHOTO = re.compile(r"\b(logos?|logotypes?|wordmarks?|maps?|diagrams?|schematics?|screenshots?|screen ?shots?|screengrabs?|charts?|graphs?|"
                       r"infographics?|illustrations?|illustrated|drawings?|drawn|sketch(?:es)?|renders?|rendering|3d model|cgi|clip ?art|"
                       r"cartoons?|memes?|icons?|vectors?|emblems?|coat of arms|seal of|flags? of|flag|banknotes?|stamps?|engravings?|"
                       r"lithographs?|postcards?|stereographs?|paintings?|posters?|svg|plan|floor ?plan|blueprints?|mock-?ups?|"
                       r"construction|renovation|scaffold(?:ing)?|interior|lobby|cafeteria|toilets?|parking|LCCN|"
                       r"t-?shirts?|shirts?|hoodies?|mugs?|stickers?|merch(?:andise)?|swag|bags?|keychains?)\b", re.I)
# a photo of one specific incident is misleading for another story: allowed only when the story itself names that kind of incident
INCIDENT = re.compile(r"\b(damaged?|collision|collided|crash(?:ed|es)?|fires?|burning|explosions?|exploded|attacks?|attacked|wreck(?:age|ed)?|"
                      r"sinking|sunk|aground|accidents?|aftermath|protests?|protesters?|riots?|seized|seizure|hijack(?:ed|ing)?|"
                      r"flood(?:ed|ing)?|earthquake|ruins?|destroyed|casualt(?:y|ies)|funeral)\b", re.I)
GRAPHIC = re.compile(r"\b(logos?|maps?|diagrams?|charts?|graphs?|infographics?|illustrations?|drawings?|screenshots?|renders?|rendering|"
                     r"cartoons?|clip ?art|memes?|icons?|flags? of|coat of arms|seal of)\b", re.I)  # in the description's first sentence
MONO = re.compile(r"\b(black[- ]and[- ]white|b ?& ?w|monochrome|gr[ae]yscale|sepia)\b", re.I)
PLACE_WORDS = {"building", "tower", "towers", "center", "centre", "bridge", "street", "hall", "memorial", "airport", "square", "park",
               "station", "university", "avenue", "library", "plaza", "house", "headquarters", "hq", "office", "offices", "court",
               "courthouse", "stadium", "arena", "museum", "school", "college", "hospital", "port", "harbour", "harbor", "strait", "bay",
               "island", "river", "lake", "mountain", "boulevard", "road", "palace", "church", "cathedral", "mosque", "temple", "market",
               "exchange", "bank", "reserve", "board", "federal", "national", "city", "county", "state", "district", "terminal"}
GIVEN = set("""aaron abdul adam adrian ahmed alan albert alex alexander alexandre ali alice amanda amy andrea andreas andrew andy angela anna
anne anthony antonio ashley barack ben benjamin bernie bill bob brad brian bruce carl carlos caroline catherine changpeng charles charlie chris
christian christine christopher claudia daniel david dennis donald doug edward elizabeth elon emily emma emmanuel eric erik frank gary gavin
george gina giorgia greg hassan hillary howard ian ivan jack jacob james jamie jan janet jared jason javier jay jeff jennifer jens jerome
jessica jim joe joel john jon jonathan jose joseph josh juan julia justin kamala karen keir kevin kim kristalina larry laura lee liz lisa
lloyd luis marco maria mark martin mary matt matthew max michael michelle mike mohammed muhammad narendra nancy nathan nayib neil nicholas nick
nicolas olaf olivia pam patrick paul peter philip rachel ralph raoul ray richard rishi robert roger ron ronald ryan sam samuel sarah scott
sergey shinzo simon stephen steve steven susan ted thomas tim todd tom tony ursula vitalik vladimir volodymyr walter warren william xi yanis
yuan zhao""".split())
TITLE_PERSON = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|President|Senator|Sen|Rep|Chairman|Chairwoman|Chair|Governor|Gov|Secretary|CEO|Minister|"
                          r"Founder|Co-founder|Director|Chief|Ambassador|Commissioner|Mayor|Prime Minister|Chancellor|King|Queen|Prince|"
                          r"Princess|Sheikh|General|Judge)\.?\s+((?:[A-Z][\w'\-]+\s?){1,3})")
NAME = re.compile(r"\b([A-Z][a-z'\-]+)\s+(?:[A-Z]\.\s+)?([A-Z][a-z'\-]+(?:\s+[A-Z][a-z'\-]+)?)")
YEAR = re.compile(r"(?<!\d)(1[89]\d\d|20\d\d)(?!\d)")


def _term_rx(term: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in re.split(r"[\s_\-]+", term.strip()) if w]
    return re.compile(r"(?<![\w])" + r"[\s_\-]+".join(words) + r"(?![\w])", re.I)


def _has(text: str, terms: Iterable[str]) -> list[str]:
    return [t for t in terms if t.strip() and _term_rx(t).search(text or "")]


def _title(photo: LicensedPhoto) -> str:
    t = re.sub(r"^File:", "", photo.title or "")
    t = re.sub(r"\.(jpe?g|png|webp|tiff?|gif)$", "", t, flags=re.I)
    return t.replace("_", " ")


def people_named(text: str) -> list[str]:
    """Names of people a title mentions ("Jerome Powell", "President Lula"): a given name and a surname, or an honorific and a name, not
    followed by a place word (so "John Hancock Tower" is a building, not a person)."""
    out = []
    for m in TITLE_PERSON.finditer(text):
        name = m.group(1).strip()
        if name and not any(w.lower() in PLACE_WORDS for w in name.split()):
            out.append(name)
    for m in NAME.finditer(text):
        first, rest = m.group(1), m.group(2)
        if first.lower() not in GIVEN:
            continue
        nxt = text[m.end():].lstrip().split(" ", 1)[0].strip(",.;:()").lower()
        if any(w.lower() in PLACE_WORDS for w in rest.split()) or nxt in PLACE_WORDS:
            continue
        out.append(f"{first} {rest}")
    return list(dict.fromkeys(out))


def _lead(description: str) -> str:
    """The description's first sentence: what the photo is of. Later sentences tend to be background ("... an extension of the Indian
    Ocean through the Strait of Hormuz") and would let a beach in Sharjah pass for Hormuz."""
    d = (description or "").strip()
    m = re.search(r"(?<=[a-z0-9\)])[.!?](?:\s|$)", d)
    return (d[:m.start() + 1] if m else d)[:240]


# Sensitive stories (2026-11-11): under a headline about an affair, a fraud or a death, a photo of people implies they are the people
# involved. A Commons search for "Nvidia" returned "Director Le Nguyen Thien Nga and NVIDIA Founder and CEO Jensen Huang.jpg"; under a
# story about the CEO's affair that would have been defamatory. For these stories, places and objects only.
SENSITIVE = re.compile(r"\b(scandals?|affairs?|allegations?|alleged(?:ly)?|accus\w*|fraud\w*|scams?|scammers?|crimes?|criminal\w*|"
                       r"arrest\w*|detain\w*|indict\w*|charged|lawsuits?|sued|sues|suing|hack\w*|exploit\w*|breach\w*|stolen|theft|"
                       r"thie(?:f|ves)|death|deaths|dead|dies|died|killed|kills?|murder\w*|suicide|wars?|casualt(?:y|ies)|harass\w*|abuse\w*|"
                       r"misconduct|assault\w*|rape|sexual|brib\w*|corrupt\w*|launder\w*|convict\w*|guilty|sentenced|jail\w*|"
                       r"prison|probe|investigat\w*|extort\w*|ransom\w*|kidnap\w*|shooting|terror\w*)\b", re.I)
PEOPLE_WORDS = re.compile(r"\b(portraits?|people|persons?|man|men|woman|women|girls?|boys?|ceos?|founders?|co-?founders?|directors?|"
                          r"ministers?|presidents?|chair(?:man|woman|person)?|executives?|governors?|senators?|officials?|speaks|speaking|"
                          r"speech|speakers?|meets|meeting|meetings|poses|posing|posed|with|and|crowds?|protests?|protesters?|selfies?|"
                          r"events?|conferences?|summit|interviews?|headshots?|panel|audience|staff|employees|workers|delegation|visits?|"
                          r"visiting|ceremony|press|keynote|smiling|faces?|family|wedding|party|hands|handshake|greets?|award)\b", re.I)
PEOPLE_CORE = re.compile(PEOPLE_WORDS.pattern.replace("|with|and|", "|"), re.I)
VESSELS = {"uss", "usns", "hms", "hmas", "hmcs", "mv", "ins", "rv", "ss", "uscgc"}
PREFER = re.compile(r"\b(headquarters|hq|building|facade|façade|campus|exterior|skyline|port|harbou?r|ship|ships|tanker|tankers|vessel|"
                    r"facility|plant|refinery|office|tower|terminal|strait|aerial)\b", re.I)
NAME_RUN = re.compile(r"(?:\b[A-Z][^\W\d_]*\.?(?:\s+|$)){2,}")
NOT_NAMES = {"the", "file", "uss", "usns", "hms", "mv", "ins", "rv", "flickr", "official", "dvidshub", "nasa", "modis", "jpg", "png", "photo",
             "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
             "north", "south", "east", "west", "new", "old", "upper", "lower", "united", "states", "kingdom", "us", "uk", "eu", "un", "of",
             "navy", "army", "air", "force", "marine", "marines", "corps", "guard", "imagery", "transits", "transit", "view", "views", "night",
             "day", "aerial", "main", "entrance", "side", "front", "rear", "interior", "exterior", "photograph", "image", "picture", "and",
             "a", "at", "in", "on", "from", "to", "by", "for", "with", "known"}
PLACE_WORDS |= {"peninsula", "gulf", "sea", "ocean", "coast", "beach", "desert", "valley", "skyline", "canal", "carrier", "group", "ship",
                "fleet", "refinery", "plant", "facility", "campus", "pier", "dock", "embassy", "parliament", "ministry"}


def is_sensitive(title: str) -> bool:
    """A story where a photo of people would imply who is involved: scandal, fraud, crime, a lawsuit, a hack, a death, a war."""
    return bool(SENSITIVE.search(title or ""))


def may_show_people(photo: LicensedPhoto, subject_terms: Iterable[str] = (), story_terms: Iterable[str] = ()) -> str | None:
    """Why the photo might show people (for sensitive stories), or None. Strict on purpose: a missed place photo costs a fallback picture,
    a wrong photo of a person can cost a defamation claim."""
    title, tags, desc = _title(photo), " | ".join(photo.tags), _lead(photo.description)
    # "and"/"with" only count in the title ("X and Y", "X with Y"); categories say "Buildings and structures in ..."
    for text, rx in ((title, PEOPLE_WORDS), (desc, PEOPLE_CORE), (tags, PEOPLE_CORE)):
        m = rx.search(text)
        if m:
            return f"may show people: {m.group(0).lower()}"
    named = people_named(title) or people_named(desc)
    if named:
        return f"names a person: {named[0]}"
    # known organisation and place names do not count as people; a known term that is itself name-shaped ("Jensen Huang") still does
    known = [t for t in list(subject_terms) + list(story_terms) if t.strip() and not name_runs(t)]
    for text in (title, desc):
        runs = name_runs(text, known)
        if runs:
            return f"may name a person: {runs[0]}"
    return None


def name_runs(text: str, known: Iterable[str] = ()) -> list[str]:
    """Runs of two or more name-shaped words ("Jensen Huang": a capital and lower-case letters, no digits, not all capitals) that are not
    a known term, a place ("... Board Building", "Musandam Peninsula") or a ship ("USS Dwight D. Eisenhower")."""
    for t in known:
        # each word of a known term becomes the placeholder "Known": still part of its run (so "Marriner S. Eccles Federal Reserve Board
        # Building" still ends in a place word), never part of a name
        text = _term_rx(t).sub(lambda m: " ".join("Known" for _ in m.group(0).split()), text)
    out = []
    for run in NAME_RUN.finditer(text):
        words = run.group(0).split()
        low = [w.strip(".,").lower() for w in words]
        if low[0] in VESSELS or any(w in PLACE_WORDS for w in low):
            continue
        cur: list[str] = []
        for w, lw in [*zip(words, low), ("", "")]:
            name_shaped = bool(w) and w[0].isupper() and re.fullmatch(r"[^\W\d_][^\W\d_A-Z]+", w.strip(".,"))
            if lw and lw not in NOT_NAMES and not PEOPLE_WORDS.fullmatch(lw) and name_shaped:
                cur.append(w.strip(".,"))
                continue
            if lw and re.fullmatch(r"[A-Z]\.", w):  # a middle initial
                continue
            if len(cur) >= 2:
                out.append(" ".join(cur))
            cur = []
    return out


PANEL_MIN_WIDTH = 700  # a collage panel (app/newsdesk/clean) is 795 px wide: a portrait of the subject fits it
PANEL_MIN_ASPECT = 0.55  # down to a 9:16 portrait (a headshot)


def relevant(photo: LicensedPhoto, subject_terms: Iterable[str], *, story_terms: Iterable[str] = (), sensitive: bool = False,
             min_width: int = MIN_WIDTH, min_aspect: float = MIN_ASPECT) -> str | None:
    """None when the photo is a real, recent, landscape colour photograph of the subject; otherwise why not.

    A subject term must appear as a whole word in the title, a human tag/category, or the description's first sentence. A description-only
    mention must also share a word with `story_terms` when those are given (a description that names the subject is weaker evidence).
    With `sensitive` (see `is_sensitive`), any sign of people rejects the photo (`may_show_people`)."""
    subject_terms = [t for t in subject_terms if t and t.strip()]
    story_terms = [t for t in story_terms if t and t.strip()]
    title, tags, desc = _title(photo), " | ".join(photo.tags), _lead(photo.description)
    if not subject_terms:
        return "no subject terms"
    in_title, in_tags, in_desc = _has(title, subject_terms), _has(tags, subject_terms), _has(desc, subject_terms)
    if not (in_title or in_tags or in_desc):
        return "subject not named in title, tags or description"
    if not (in_title or in_tags) and story_terms and not _has(title + " " + desc, story_terms):
        return "subject only mentioned in passing in the description"
    bad = NOT_PHOTO.search(title) or NOT_PHOTO.search(tags) or GRAPHIC.search(desc)
    if bad:
        return f"not a news photograph: {bad.group(0).lower()}"
    generic = generic_scene(photo, story_terms + subject_terms)
    if generic:
        return generic
    if photo.mime and photo.mime.lower() not in ("image/jpeg", "image/png", "image/webp", "jpg", "jpeg", "png", "webp"):
        return f"not a photograph file: {photo.mime}"
    if MONO.search(title) or MONO.search(tags):
        return "black and white"
    years = [int(y) for y in YEAR.findall(title)]
    if photo.year and photo.year < MIN_YEAR:
        return f"too old: {photo.year}"
    if years and max(years) < MIN_YEAR:
        return f"too old: {max(years)}"
    if not photo.width or not photo.height:
        return "size unknown"
    if photo.width < min_width:
        return f"too small: {photo.width} px wide"
    if photo.width < photo.height * min_aspect:
        return "portrait or square"
    incidents = [m.group(0) for m in INCIDENT.finditer(title)]
    if incidents and not _has(" ".join(story_terms + subject_terms), incidents):
        return f"a photo of a different incident: {incidents[0].lower()}"
    joined = " ".join(subject_terms).lower()
    named = people_named(title)
    if PEOPLE_WORDS.search(title):  # "Jensen Huang Nvidia Keynote": a name next to a people word is a person even without a known given name
        named += name_runs(title, subject_terms)
    for person in dict.fromkeys(named):
        parts = [p.lower().strip(".") for p in person.split()]
        if not any(_term_rx(p).search(joined) for p in parts if len(p) > 2):
            return f"names another person: {person}"
    if sensitive:
        return may_show_people(photo, subject_terms, story_terms)
    return None


def used_urls(records: Iterable[tuple[str, Any]], *, days: int = USED_DAYS, now: datetime | None = None) -> set[str]:
    """The photo URLs (and pages) used within `days`, from (url, when) pairs; `when` is a datetime or an ISO string."""
    now = now or datetime.now(timezone.utc)
    out: set[str] = set()
    for url, when in records:
        if isinstance(when, str):
            try:
                when = datetime.fromisoformat(when.replace("Z", "+00:00"))
            except ValueError:
                continue
        if when is None:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if now - when <= timedelta(days=days):
            out.add(url)
    return out


# --- providers ----------------------------------------------------------------------------------------------------------


async def search_commons(client: httpx.AsyncClient, query: str, limit: int = 20) -> list[LicensedPhoto]:
    """Bitmaps on Wikimedia Commons with their extmetadata (licence, licence URL, artist, description, categories, date)."""
    params = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": "6", "gsrsearch": f"{query} filetype:bitmap",
              "gsrlimit": str(limit), "prop": "imageinfo", "iiprop": "url|size|extmetadata|mime", "iiurlwidth": "1920"}
    r = await client.get(COMMONS, params=params, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    pages = sorted((r.json().get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}

        def mv(k: str) -> str:
            return str((meta.get(k) or {}).get("value", "") or "")

        if mv("NonFree").lower() == "true":
            continue
        year = YEAR.search(_plain(mv("DateTimeOriginal")))
        out.append(LicensedPhoto(
            title=page.get("title", ""), url=info.get("thumburl") or info.get("url", ""), page=info.get("descriptionurl", ""),
            creator=_plain(mv("Artist")), provider="wikimedia", source="wikimedia", licence=_plain(mv("LicenseShortName")),
            licence_url=mv("LicenseUrl"), width=int(info.get("width") or 0), height=int(info.get("height") or 0),
            description=_plain(mv("ImageDescription"))[:600], tags=tuple(c.strip() for c in mv("Categories").split("|") if c.strip()),
            year=int(year.group(1)) if year else None, mime=info.get("mime", ""), restrictions=mv("Restrictions"), query=query))
    return out


async def search_openverse(client: httpx.AsyncClient, query: str, limit: int = 20) -> list[LicensedPhoto]:
    """Openverse, anonymous: commercial-use licences only (license_type=commercial and an explicit allow-list), photographs, wide, large.
    Anonymous requests get at most 20 results a page and a small daily budget, so one page per query."""
    params = {"q": query, "license_type": "commercial", "license": "by,by-sa,cc0,pdm", "category": "photograph", "aspect_ratio": "wide",
              "size": "large", "mature": "false", "page_size": str(min(limit, 20))}
    r = await client.get(OPENVERSE, params=params, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for rec in r.json().get("results") or []:
        lic, ver = str(rec.get("license") or ""), str(rec.get("license_version") or "")
        name = {"cc0": "CC0", "pdm": "Public Domain Mark"}.get(lic.lower(), f"CC {lic.upper()} {ver}".strip() if lic else "")
        tags = tuple(str(t.get("name")) for t in rec.get("tags") or [] if isinstance(t, dict) and t.get("name") and t.get("accuracy") is None)
        out.append(LicensedPhoto(
            title=str(rec.get("title") or ""), url=str(rec.get("url") or ""), page=str(rec.get("foreign_landing_url") or ""),
            creator=str(rec.get("creator") or ""), provider="openverse", source=str(rec.get("source") or rec.get("provider") or ""),
            licence=name, licence_url=str(rec.get("license_url") or ""), width=int(rec.get("width") or 0), height=int(rec.get("height") or 0),
            description=str(rec.get("description") or "")[:600], tags=tags, year=None, mime=str(rec.get("filetype") or ""), query=query))
    return out


async def search_pexels(client: httpx.AsyncClient, query: str, limit: int = 15) -> list[LicensedPhoto]:
    """Pexels, only with PEXELS_API_KEY. Pexels asks for a link to Pexels and the photographer's credit; both are in the credit and meta."""
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not key:
        return []
    params = {"query": query, "orientation": "landscape", "size": "large", "per_page": str(min(limit, 80))}
    r = await client.get(PEXELS, params=params, headers={**UA, "Authorization": key}, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for p in r.json().get("photos") or []:
        src = p.get("src") or {}
        out.append(LicensedPhoto(
            title=str(p.get("alt") or ""), url=str(src.get("large2x") or src.get("original") or ""), page=str(p.get("url") or ""),
            creator=str(p.get("photographer") or ""), provider="pexels", source="pexels", licence="Pexels License",
            licence_url=PEXELS_LICENCE_URL, width=int(p.get("width") or 0), height=int(p.get("height") or 0),
            description=str(p.get("alt") or ""), query=query))
    return out


async def _safe(name: str, coro: Any) -> list[LicensedPhoto]:
    """One dead or rate-limited provider never breaks discovery."""
    try:
        return await coro
    except Exception as e:  # noqa: BLE001 - every provider failure is the same to the caller: no photos from it
        status = getattr(getattr(e, "response", None), "status_code", None)
        logger.warning("newsdesk photos: %s failed (%s%s)", name, type(e).__name__, f" {status}" if status else "")
        return []


# --- discovery ----------------------------------------------------------------------------------------------------------


def _norm_url(u: str) -> str:
    s = urlsplit(unquote(u or ""))
    return f"{s.netloc.lower().removeprefix('www.')}{s.path}".rstrip("/").lower()


def _keys(p: LicensedPhoto) -> set[str]:
    keys = {k for k in (_norm_url(p.url), _norm_url(p.page)) if k}
    # Commons thumbnails: .../thumb/a/ab/Name.jpg/1920px-Name.jpg and the file page File:Name.jpg are the same file
    for u in (p.url, p.page):
        m = re.search(r"(?:/thumb/\w/\w\w/|/\w/\w\w/|File:)([^/?#]+?\.(?:jpe?g|png|webp|tiff?))", unquote(u or ""), re.I)
        if m:
            keys.add("file:" + m.group(1).replace(" ", "_").lower())
    stem = re.sub(r"[^a-z0-9]+", "", _title(p).lower())
    if len(stem) >= 12:
        keys.add("title:" + stem)
    # a numbered series from one shoot ("... 2025-02-10 12-05-42", "... October 2023 (3)") collapses to its best frame
    series = re.sub(r"[^a-z]+", "", re.sub(r"\(\d+\)|\d", "", _title(p).lower()))
    if len(series) >= 16:
        keys.add("series:" + series)
    return keys


# Owner, 2026-11-13: "prefer recognizable people, dramatic real photography, verified charts; reject generic server rooms, office buildings,
# meaningless AI illustrations". A generic scene says nothing about the story even when its caption names the company (a server room, a
# data centre, an office interior, a skyline, a stock concept): rejected unless the story itself is about that kind of place.
GENERIC = re.compile(r"\b(server ?rooms?|servers?|server racks?|data ?cent(?:er|re)s?|racks?|cables?|cubicles?|open[- ]plan|office (?:interior|space|desk|"
                     r"workers?|staff)|coworking|meeting rooms?|conference rooms?|boardrooms?|skylines?|cityscapes?|business district|"
                     r"stock (?:photo|image)|abstract|concept|binary|keyboards?|laptops?|computer screens?|monitors?|trading screens?|"
                     r"stock tickers?|handshakes?|coins? (?:stack|pile)|piggy ?bank|hoodie|hacker)\b", re.I)
BUILDING = re.compile(r"\b(headquarters|hq|office building|offices|office|building|facade|façade|campus|exterior|tower)\b", re.I)


def generic_scene(photo: LicensedPhoto, story_terms: Iterable[str] = ()) -> str | None:
    """Why this photo is a generic stand-in rather than a picture of the story, or None."""
    text = f"{_title(photo)} | {' | '.join(photo.tags)} | {_lead(photo.description)}"
    hit = GENERIC.search(text)
    if hit and not _has(" ".join(story_terms), [hit.group(0)]):
        return f"a generic scene, not the story: {hit.group(0).lower()}"
    return None


def scene_points(p: LicensedPhoto, subject_terms: list[str], sensitive: bool) -> float:
    """Ranking: a recognizable person who is the story's subject first (never on sensitive stories), an office building last."""
    title = _title(p)
    pts = 0.0
    if not sensitive:
        named = [n for n in people_named(title) if any(_term_rx(w).search(" ".join(subject_terms)) for w in n.split() if len(w) > 2)]
        if named:
            pts += 2.5
        if BUILDING.search(title) and not named:
            pts -= 1.5
    return pts


def _score(p: LicensedPhoto, subject_terms: list[str], story_terms: list[str], sensitive: bool = False) -> float:
    title, tags = _title(p), " | ".join(p.tags)
    in_title = _has(title, subject_terms)
    in_tags = [t for t in _has(tags, subject_terms) if t not in in_title]
    match = 3.0 * len(in_title) + 1.5 * len(in_tags) + 0.05 * max((len(t) for t in in_title), default=0)
    if not match:
        match = 0.5  # the description's first sentence only
    match += 0.5 * len(_has(title + " " + p.description, story_terms))
    res = min(p.width, 4000) / 4000
    recency = min(max(((p.year or 0) - MIN_YEAR) / 30, 0), 1) if p.year else 0.3
    trust = TRUST.get(f"{p.provider}:{p.source}" if p.provider == "openverse" else p.provider, 0.5)
    if p.provider == "openverse" and not p.licence_url:
        trust -= 0.3
    aspect = 1 - min(abs(p.width / max(p.height, 1) - 16 / 9), 1)
    place = 1.0 if sensitive and (PREFER.search(title) or PREFER.search(tags)) else 0.0  # places and objects first
    return round(match + res + recency + 1.5 * trust + 0.5 * aspect + place + scene_points(p, subject_terms, sensitive), 4)


async def discover(client: httpx.AsyncClient, queries: list[str], subject_terms: list[str], *, used: Iterable[str] = frozenset(),
                   limit: int = 8, story_terms: Iterable[str] = (), sensitive: bool = False, panel: bool = False) -> list[LicensedPhoto]:
    """Licensed, relevant photographs for a story, best first. Commons and Openverse are searched in parallel (Pexels too when keyed);
    duplicates across providers collapse to the most trusted copy; anything in `used` (URLs or pages) is skipped. `sensitive` (a scandal,
    fraud, crime, hack, death or war story: `is_sensitive(headline)`) admits only photos of places and objects, never of people."""
    queries = [q for q in dict.fromkeys(q.strip() for q in queries) if q][:MAX_QUERIES]
    story = list(story_terms)
    jobs = []
    for q in queries:
        jobs.append(_safe(f"commons[{q}]", search_commons(client, q)))
        jobs.append(_safe(f"openverse[{q}]", search_openverse(client, q)))
        if os.environ.get("PEXELS_API_KEY", "").strip():
            jobs.append(_safe(f"pexels[{q}]", search_pexels(client, q)))
    found = [p for batch in await asyncio.gather(*jobs) for p in batch]
    used_keys = {_norm_url(u) for u in used if u}
    ok: list[LicensedPhoto] = []
    for p in found:
        if not p.url or _norm_url(p.url) in used_keys or _norm_url(p.page) in used_keys:
            continue
        why = verify_licence(p) or relevant(p, subject_terms, story_terms=story, sensitive=sensitive,
                                            **({"min_width": PANEL_MIN_WIDTH, "min_aspect": PANEL_MIN_ASPECT} if panel else {}))
        if why:
            logger.debug("newsdesk photos: skip %s (%s)", p.title[:80], why)
            continue
        p.score = _score(p, subject_terms, story, sensitive)
        ok.append(p)
    ok.sort(key=lambda p: p.score, reverse=True)
    seen: set[str] = set()
    out = []
    for p in ok:
        keys = _keys(p)
        if keys & seen:
            continue
        seen |= keys
        out.append(p)
    return out[:limit]


async def fetch(client: httpx.AsyncClient, photo: LicensedPhoto, min_width: int = MIN_WIDTH) -> Image.Image | None:
    """The photograph itself, or None when the pixels fail the rules the metadata could not check (black and white, too small)."""
    try:
        r = await client.get(photo.url, headers=UA, timeout=30, follow_redirects=True)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception as e:  # noqa: BLE001
        logger.warning("newsdesk photos: download failed for %s (%s)", photo.url[:120], type(e).__name__)
        return None
    if img.width < min_width * 0.9 or not is_colour(img) or looks_like_graphic(img):  # a 1920 rendition can be a little narrower
        return None
    return img


def looks_like_graphic(img: Image.Image) -> bool:
    """Maps, charts and logos are flat: a few colours cover most of the picture. A photograph's colours are spread out."""
    small = img.convert("RGB").resize((160, 90)).quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    counts = sorted((c for c, _ in small.getcolors(256) or []), reverse=True)
    return sum(counts[:8]) / (160 * 90) > 0.6


def is_colour(img: Image.Image) -> bool:
    """Black-and-white archive pictures read as history, not news. A hazy colour photograph (sea and sky) has low average saturation but
    still real chroma; a toned or yellowed black-and-white print (sepia) is almost all one warm hue."""
    small = img.convert("RGB").resize((96, 54))
    rgb = np.asarray(small, dtype=np.float64)
    hsv = np.asarray(small.convert("HSV"), dtype=np.float64)
    chroma = rgb.max(axis=-1) - rgb.min(axis=-1)
    if np.percentile(chroma, 90) < 14:
        return False  # grey all over
    tinted = chroma >= 10
    hue = hsv[..., 0][tinted] * (2 * np.pi / 255)
    mean = np.exp(1j * hue).mean() if hue.size else 0j
    spread = 1 - abs(mean)  # circular variance of the tinted pixels' hue
    warm = 10 <= (np.degrees(np.angle(mean)) % 360) <= 60
    if spread < 0.02 and warm and tinted.mean() > 0.6:
        return False  # sepia: one warm hue over nearly every pixel
    return bool(hsv[..., 1].mean() > 28 or tinted.mean() > 0.25)


# --- subject-aware cropping ---------------------------------------------------------------------------------------------
# Saliency, chosen for being cheap and dependency-free (Pillow + numpy; no OpenCV): the average of
#  - spectral residual (Hou & Zhang 2007) on a 128 px-wide luminance image: what the log spectrum's smooth trend cannot explain is what
#    stands out, and
#  - centre-surround colour contrast (a fine blur against a coarse blur, in RGB): an object differs from what is around it, while a
#    plain sky or sea does not (a global "distance from the mean colour" would call a bright sky the subject),
# then blurred and weighted by a soft centre prior (photographers frame the subject near the middle). The subject box is the band that
# holds the central 88% of the saliency mass along each axis (6th to 94th percentile of the marginals) once the weakest 80% of the map
# is discounted, which ignores texture and stray specks, plus a 2% margin.

WORK_W = 128


def saliency(img: Image.Image) -> np.ndarray:
    """A saliency map in [0, 1], WORK_W wide, same aspect as the image."""
    w, h = img.size
    sw, sh = WORK_W, max(8, round(WORK_W * h / w))
    rgb = np.asarray(img.convert("RGB").resize((sw, sh), Image.BILINEAR), dtype=np.float64) / 255.0
    lum = rgb @ np.array([0.299, 0.587, 0.114])
    f = np.fft.fft2(lum)
    log_amp = np.log(np.abs(f) + 1e-9)
    phase = np.angle(f)
    k = np.ones((3, 3)) / 9.0
    pad = np.pad(log_amp, 1, mode="wrap")
    smooth = sum(pad[i:i + sh, j:j + sw] * k[i, j] for i in range(3) for j in range(3))
    sr = np.abs(np.fft.ifft2(np.exp((log_amp - smooth) + 1j * phase))) ** 2
    sr = _blur(sr, 2.5)
    fine = np.stack([_blur(rgb[..., c], 1.0) for c in range(3)], axis=-1)
    coarse = np.stack([_blur(rgb[..., c], 8.0) for c in range(3)], axis=-1)
    cs = np.linalg.norm(fine - coarse, axis=-1)
    s = 0.5 * _unit(sr) + 0.5 * _unit(cs)
    yy, xx = np.mgrid[0:sh, 0:sw]
    centre = np.exp(-(((xx - sw / 2) / (sw * 0.55)) ** 2 + ((yy - sh / 2) / (sh * 0.55)) ** 2))
    edge = np.minimum(np.minimum(xx, sw - 1 - xx) / (0.04 * sw), np.minimum(yy, sh - 1 - yy) / (0.04 * sh)).clip(0, 1)  # FFT wrap and frame edges
    return _unit(_blur(s, 1.5) * (0.65 + 0.35 * centre) * (0.3 + 0.7 * edge))


def _unit(a: np.ndarray) -> np.ndarray:
    lo, hi = float(a.min()), float(a.max())
    return (a - lo) / (hi - lo) if hi > lo else np.zeros_like(a)


def _blur(a: np.ndarray, sigma: float) -> np.ndarray:
    r = max(1, int(3 * sigma))
    x = np.arange(-r, r + 1)
    g = np.exp(-(x ** 2) / (2 * sigma ** 2))
    g /= g.sum()
    a = np.apply_along_axis(lambda v: np.convolve(np.pad(v, r, mode="edge"), g, mode="valid"), 0, a)
    return np.apply_along_axis(lambda v: np.convolve(np.pad(v, r, mode="edge"), g, mode="valid"), 1, a)


def _box_from(s: np.ndarray, lo: float = 0.06, hi: float = 0.94) -> tuple[float, float, float, float]:
    mass = np.clip(s - np.percentile(s, 80), 0, None) ** 2  # the background's low hum carries no subject
    if mass.sum() <= 0:
        return (0.25, 0.25, 0.75, 0.75)
    sh, sw = s.shape

    def band(marg: np.ndarray, n: int) -> tuple[float, float]:
        c = np.cumsum(marg) / marg.sum()
        return float(np.searchsorted(c, lo)) / n, float(np.searchsorted(c, hi) + 1) / n

    x0, x1 = band(mass.sum(axis=0), sw)
    y0, y1 = band(mass.sum(axis=1), sh)
    pad = 0.02
    return (max(0.0, x0 - pad), max(0.0, y0 - pad), min(1.0, x1 + pad), min(1.0, y1 + pad))


def subject_box(img: Image.Image) -> tuple[float, float, float, float]:
    """The main subject's box, normalised (x0, y0, x1, y1)."""
    return _box_from(saliency(img))


def smart_crop(img: Image.Image, size: tuple[int, int] = (1600, 900)) -> tuple[Image.Image, tuple[float, float, float, float]]:
    """Crop to the target aspect keeping the subject box inside when it fits (among the windows that contain it, the one with the most
    saliency; otherwise simply the most saliency), resize, and return the picture with the subject box in its own normalised coordinates.
    Only a crop and a resize: no pixel is altered."""
    img = img.convert("RGB")
    w, h = img.size
    tw, th = size
    target = tw / th
    s = saliency(img)
    box = _box_from(s)
    sh, sw = s.shape
    if w / h > target:  # wider than 16:9: slide a full-height window horizontally
        cw, ch = h * target, float(h)
        span, frac, marg, b0, b1 = w - cw, cw / w, s.sum(axis=0), box[0], box[2]
    else:  # taller: slide a full-width window vertically
        cw, ch = float(w), w / target
        span, frac, marg, b0, b1 = h - ch, ch / h, s.sum(axis=1), box[1], box[3]
    n = len(marg)
    win = max(1, round(frac * n))
    csum = np.concatenate([[0.0], np.cumsum(marg)])
    best, best_val, fallback, fb_val = None, -1.0, 0, -1.0
    for i in range(0, n - win + 1):
        val = float(csum[i + win] - csum[i])
        if val > fb_val:
            fallback, fb_val = i, val
        start, end = i / n, (i + win) / n
        if start <= b0 + 1e-9 and end >= b1 - 1e-9 and val > best_val:
            best, best_val = i, val
    i = best if best is not None else fallback
    off = min(span, max(0.0, i / n * (w if w / h > target else h)))
    if w / h > target:
        crop = (off, 0.0, off + cw, ch)
    else:
        crop = (0.0, off, cw, off + ch)
    out = img.resize(size, Image.LANCZOS, box=crop)
    cx0, cy0, cx1, cy1 = crop
    bx = ((box[0] * w - cx0) / (cx1 - cx0), (box[1] * h - cy0) / (cy1 - cy0), (box[2] * w - cx0) / (cx1 - cx0), (box[3] * h - cy0) / (cy1 - cy0))
    return out, tuple(round(min(1.0, max(0.0, v)), 4) for v in bx)  # type: ignore[return-value]


def mild_tone(img: Image.Image) -> Image.Image:
    """A gentle, global tone lift (half-strength auto-contrast, a touch of contrast): never local edits, never altered content."""
    auto = ImageOps.autocontrast(img.convert("RGB"), cutoff=0.5)
    return ImageEnhance.Contrast(Image.blend(img.convert("RGB"), auto, 0.5)).enhance(1.05)


ZONES = ("lower", "left", "right", "upper")  # order breaks ties: a lower band reads most naturally


def zone_rect(zone: str, box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """The free band beside the subject for a zone, normalised (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = box
    return {"left": (0.0, 0.0, x0, 1.0), "right": (x1, 0.0, 1.0, 1.0), "upper": (0.0, 0.0, 1.0, y0), "lower": (0.0, y1, 1.0, 1.0)}[zone]


def text_zone(box: tuple[float, float, float, float]) -> str:
    """Where a headline block can sit without covering the subject: the band (left, right, upper, lower) with the largest free area."""
    x0, y0, x1, y1 = box
    free = {"lower": 1 - y1, "left": x0, "right": 1 - x1, "upper": y0}
    return max(ZONES, key=lambda z: (round(free[z], 3), -ZONES.index(z)))


def covers(box: tuple[float, float, float, float], rect: tuple[float, float, float, float]) -> float:
    """The fraction of the subject box a rectangle covers (both normalised); callers keep a headline under 0.1."""
    ix = max(0.0, min(box[2], rect[2]) - max(box[0], rect[0]))
    iy = max(0.0, min(box[3], rect[3]) - max(box[1], rect[1]))
    area = max(box[2] - box[0], 0.0) * max(box[3] - box[1], 0.0)
    return 0.0 if area <= 0 else (ix * iy) / area


__all__ = ["SENSITIVE", "LicensedPhoto", "canonical_licence", "covers", "discover", "fetch", "is_colour", "is_sensitive", "looks_like_graphic",
           "may_show_people", "mild_tone", "name_runs", "people_named", "relevant",
           "saliency", "search_commons", "search_openverse", "search_pexels", "smart_crop", "subject_box", "text_zone", "used_urls",
           "verify_licence", "zone_rect"]
