"""The picture behind every Terminal story (owner, 2026-11-23: "in the Terminal I want the image ... wherever it sources the images from").

For a Terminal story (a trending topic) the picture is worked out by the same Visual Editor the News Desk posts with (app/newsdesk/visual,
clean, editor); for a News Desk event it is the picture the post used or will use. When no licensed picture passes, the source article's
own preview is looked up (app/newsdesk/preview); when that fails too, the story is shown as held, with every reason.

`out` turns a picture into what the Terminal's image panel shows: the image itself, where it came from (the editor's library, a Commons
search, Price Chat's own chart, the article's preview), the photo's page, its author, its licence, the credit line, and why it was chosen
or what was refused. Nothing here sends anything; reading a picture is free (no paid AI call).
"""

from __future__ import annotations

import base64
import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.newsdesk import NdEvent, NdTelegramSettings
from app.models.trending import TtTopic

logger = logging.getLogger(__name__)

CACHE_SECONDS = 600
_cache: dict[tuple[str, int, str], tuple[float, bytes, dict[str, Any]]] = {}


def topic_event(t: TtTopic, now: datetime) -> NdEvent:
    """A Terminal story as the event the Visual Editor reads (never added to the database)."""
    srcs = [{"name": str(x.get("outlet") or x.get("source") or ""), "url": str(x.get("url") or ""), "title": str(x.get("title") or "")}
            for x in (t.sources or [])[:6] if isinstance(x, dict)]
    ev = NdEvent(key=f"terminal:{t.id}", kind="news", title=t.title, headline=t.title, topic=t.category or "", label="", sources=srcs, facts={},
                 released_at=now, first_seen=t.first_seen or now, evidence="")
    ev.id = None
    return ev


def _used(db: Session) -> tuple[set[str], list[list[Any]]]:
    tg = db.get(NdTelegramSettings, 1)
    records = [list(r) for r in (((tg.state or {}) if tg is not None else {}).get("used_photos") or []) if r]
    return {r[0] for r in records if r and r[0]}, records


async def picture_of(db: Session, ev: NdEvent, now: datetime, *, transport: Any = None, platform: str = "telegram") -> tuple[bytes, dict[str, Any]]:
    """The picture a post of this event carries: a licensed picture, else the article's preview, else held (b"" and the reasons)."""
    from app.newsdesk import preview, visual

    used, records = _used(db)
    try:
        image, meta = await visual.for_event(ev, used=used, transport=transport, now=now, db=db, platform=platform, records=records)
    except visual.NumbersRefused as e:
        return b"", {"kind": "held", "layout": "held", "why": f"the story's figures did not check out: {e}"}
    if image:
        return image, meta
    links = [str(x.get("url") or "") for x in ev.sources or []]
    got, looks = await preview.first_working(links, transport)
    if got is not None:
        return b"", preview.meta_for(got, meta)
    return b"", {**meta, "kind": "held", "layout": "held", "previews": [p.out() for p in looks],
                 "why": meta.get("why") or "no licensed picture passed the Visual Editor"}


async def for_topic(db: Session, t: TtTopic, now: datetime, *, transport: Any = None, fresh: bool = False) -> tuple[bytes, dict[str, Any]]:
    key = ("topic", t.id, t.title)
    hit = _cache.get(key)
    if hit and not fresh and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1], hit[2]
    image, meta = await picture_of(db, topic_event(t, now), now, transport=transport)
    _keep(key, image, meta)
    return image, meta


async def for_event(db: Session, event_id: int, now: datetime, *, transport: Any = None) -> tuple[bytes, dict[str, Any]] | None:
    """A News Desk event's picture: the stored article preview or hold as it was decided, else drawn again from what was stored."""
    ev = db.get(NdEvent, event_id)
    if ev is None:
        return None
    stored = dict((ev.facts or {}).get("visual") or {})
    if stored.get("kind") in ("link_preview", "held"):
        return b"", stored
    key = ("event", ev.id, f"{ev.headline}|{ev.updated_at}")
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1], hit[2]
    copy = NdEvent(key=ev.key, kind=ev.kind, title=ev.title, headline=ev.headline, topic=ev.topic, label=ev.label, sources=ev.sources,
                   facts=dict(ev.facts or {}), virality=ev.virality, released_at=ev.released_at, first_seen=ev.first_seen, evidence=ev.evidence)
    copy.id = ev.id
    image, meta = await picture_of(db, copy, now, transport=transport)
    _keep(key, image, meta)
    return image, meta


def _keep(key: tuple[str, int, str], image: bytes, meta: dict[str, Any]) -> None:
    if len(_cache) > 60:
        _cache.clear()
    _cache[key] = (time.monotonic(), image, meta)


def source_label(meta: dict[str, Any]) -> str:
    """Where the picture came from, in the owner's words."""
    from app.newsdesk import photos

    kind = str(meta.get("kind") or "")
    if kind == "link_preview":
        return f"Article preview from {meta.get('site') or 'the source'} (shown by Telegram, not re-uploaded)"
    if kind == "held":
        return "No picture: held for your review"
    if kind == "template":
        return f"Price Chat graphic ({meta.get('layout') or 'chart'}) from verified market data"
    if kind in ("chart", "data"):
        return "Price Chat chart from verified market data"
    if kind == "ai":
        return "AI illustration from the GPU PC (labelled)"
    if kind == "photo" or meta.get("url"):
        p = meta.get("photo") or {}
        where = photos.SOURCE_NAMES.get(str(p.get("source") or p.get("provider") or "wikimedia").lower(), "Wikimedia Commons")
        if meta.get("library"):
            return f"Editor's photo library ({meta['library']}), from {where}"
        if meta.get("person"):
            return f"Photo of {meta['person']} found on {where}"
        return f"Photo found on {where}"
    return kind or "unknown"


def out(image: bytes, meta: dict[str, Any]) -> dict[str, Any]:
    """The image panel: the picture and everything about it."""
    p = meta.get("photo") or {}
    editor = meta.get("editor") or {}
    mime = "image/jpeg" if image[:3] == b"\xff\xd8\xff" else "image/png"
    kind = str(meta.get("kind") or ("photo" if meta.get("url") else "text"))
    return {
        "kind": kind,
        "held": kind == "held" or (not image and kind != "link_preview"),
        "image": f"data:{mime};base64,{base64.b64encode(image).decode()}" if image else "",
        "preview_image": str(meta.get("image_url") or "") if kind == "link_preview" else "",
        "source": source_label(meta),
        "page": str(meta.get("page") or p.get("page") or ""),
        "title": str(meta.get("title") or p.get("title") or ""),
        "author": str(p.get("creator") or ""),
        "licence": str(meta.get("licence") or p.get("licence") or ""),
        "licence_url": str(p.get("licence_url") or ""),
        "credit": str(meta.get("credit_line") or meta.get("credit") or p.get("credit") or ""),
        "site": str(meta.get("site") or ""),
        "size": [int(meta.get("width") or p.get("width") or 0), int(meta.get("height") or p.get("height") or 0)],
        "why": str(editor.get("why") or meta.get("why") or ""),
        "depiction": str(editor.get("depiction") or ""),
        "previews": [{"site": x.get("site") or x.get("url"), "ok": bool(x.get("ok")), "why": x.get("why", "")} for x in meta.get("previews") or []],
    }
