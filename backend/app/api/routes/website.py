"""Website content: what the public Price Chat website shows, and how the dashboard controls it.

The public endpoints only read stored content (never YouTube), so the website stays fast and keeps
showing the last synced content when YouTube is unavailable. They expose public fields only.
Nothing here is invented: a section with no real data comes back empty and the website hides it.
"""

from datetime import timedelta
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import DbSession, Runtime
from app.api.schemas import StrictInput
from app.connectors.state import get_connector_state
from app.analytics.community import growth as community_growth
from app.db.base import utcnow
from app.models.community import CommunityTopic
from app.models.content_items import ContentItem
from app.models.enums import Platform, TopicStatus
from app.models.website import WebsiteSettings
from app.services.activity import ActivityLogger
from app.youtube import service as yt

router = APIRouter(tags=["website"])

TYPE_LABEL = {"short": "Short", "long_form": "Video"}
SECTIONS = ("latest", "trending", "whats_it_worth", "price_board", "originals", "community", "story")
SERIES = ("whats_it_worth", "originals")
CATEGORIES = ("markets", "crypto", "money", "culture")
NETWORKS = ("telegram", "x", "youtube")
TRENDING_DAYS = 7


def _public_query():  # type: ignore[no-untyped-def]
    return select(ContentItem).where(
        ContentItem.website_visible.is_(True), ContentItem.available.is_(True), ContentItem.privacy_status == "public",
    ).order_by(ContentItem.published_at.desc(), ContentItem.id.desc())


def _public_items(db: Session, limit: int) -> list[ContentItem]:
    return list(db.scalars(_public_query().limit(limit)))


def _duration(d: int | None) -> str | None:
    if not d:
        return None
    h, m, s = d // 3600, d % 3600 // 60, d % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _card(i: ContentItem) -> dict[str, Any]:
    """Public fields only: no ids of internal records beyond the platform's own id. The title is stored and
    sent unchanged; the website tidies hashtags for display only."""
    return {
        "platform": i.platform.value, "external_id": i.external_id, "type": i.content_type,
        "type_label": TYPE_LABEL.get(i.content_type, i.content_type.replace("_", " ").title()),
        "title": i.title, "url": i.url, "thumbnail": i.thumbnail_url or (yt.fallback_thumbnail(i.external_id) if i.platform == Platform.YOUTUBE else None),
        "duration": _duration(i.duration_seconds),
        "published_at": i.published_at.isoformat() if i.published_at else None, "featured": i.featured,
        "views": (i.metrics or {}).get("views"),  # public YouTube count; None = unknown (never estimated)
        "series": i.series, "category": i.category,
    }


def _settings(db: Session) -> WebsiteSettings:
    row = db.get(WebsiteSettings, 1)
    if row is None:
        row = WebsiteSettings(id=1, sections={}, links={})
        db.add(row)
        db.commit()
    return row


def _sections(row: WebsiteSettings) -> dict[str, bool]:
    """What the dashboard has switched on. On by default: a section still needs real data to appear."""
    return {k: bool((row.sections or {}).get(k, True)) for k in SECTIONS}


def _links(db: Session, row: WebsiteSettings) -> dict[str, str | None]:
    """Public profile links: the dashboard's value, else the connected Price Chat account, else none (hidden)."""
    saved = row.links or {}
    out: dict[str, str | None] = {k: saved.get(k) or None for k in NETWORKS}
    if not out["youtube"]:
        st = get_connector_state(db, Platform.YOUTUBE)
        handle = (st.account_username or "").strip()
        if handle:
            out["youtube"] = f"https://www.youtube.com/{handle if handle.startswith('@') else '@' + handle}"
        elif st.account_external_id:
            out["youtube"] = f"https://www.youtube.com/channel/{st.account_external_id}"
    if not out["x"]:
        handle = (get_connector_state(db, Platform.X).account_username or "").strip().lstrip("@")
        out["x"] = f"https://x.com/{handle}" if handle else None
    return out  # Telegram has no safe default: a bot token is not a public group link


def _trending(db: Session) -> list[str]:
    """Community topics a person approved, seen in the last week. Names only: no messages, counts or people."""
    since = utcnow() - timedelta(days=TRENDING_DAYS)
    rows = db.scalars(select(CommunityTopic).where(
        CommunityTopic.status == TopicStatus.ACTIVE, CommunityTopic.review_status == "approved",
        CommunityTopic.canonical_id.is_(None), CommunityTopic.last_seen_at >= since,
    ).order_by(CommunityTopic.count_24h.desc(), CommunityTopic.importance.desc()).limit(10))
    return [t.name.strip() for t in rows if t.name.strip()][:8]


def price_board_quotes(db: Session) -> list[dict[str, Any]]:
    """INTEGRATION BOUNDARY: The Price Board.

    TODO(market-data): there is no market-data source yet, so this returns nothing and the Price Board stays
    hidden. When one is connected, return quotes from stored, timestamped data only, e.g.
    {"symbol": "BTC", "name": "Bitcoin", "price": 0.0, "currency": "USD", "change_24h_pct": 0.0,
     "as_of": "<ISO time>", "source": "<provider>"}. Never estimate, interpolate or invent a price.
    """
    return []


def _home(db: Session) -> dict[str, Any]:
    row = _settings(db)
    public = _public_query()
    worth = list(db.scalars(public.where(ContentItem.series == "whats_it_worth").limit(4)))
    originals = list(db.scalars(public.where(ContentItem.series == "originals").limit(6)))
    categories = set(db.scalars(select(ContentItem.category).where(
        ContentItem.website_visible.is_(True), ContentItem.available.is_(True), ContentItem.privacy_status == "public",
        ContentItem.category.is_not(None)).distinct()))
    return {
        "sections": _sections(row),
        "latest": [_card(i) for i in _public_items(db, 3)],  # newest first: the first one is the featured item
        "trending": _trending(db),
        "whats_it_worth": [_card(i) for i in worth],
        "originals": [_card(i) for i in originals],
        "price_board": price_board_quotes(db),
        "categories": [c for c in CATEGORIES if c in categories],
        "links": (links := _links(db, row)),
        "growth": community_growth(db, links),  # real counts and history per linked network; None = not measured yet
        "site_url": row.site_url,
        "trading_bot": _trading_bot_open(db),  # the TRADING BOT tab shows only once the owner opens the funnel
    }


def _trading_bot_open(db: Session) -> bool:
    from app.models.tradingbot import TbSettings

    return bool(db.scalar(select(TbSettings.public_enabled).limit(1)))


@router.get("/website/home")
def home(db: DbSession) -> dict[str, Any]:
    """Everything the homepage needs in one read. Empty lists mean "hide that section"."""
    return _home(db)


@router.get("/website/latest")
def latest(db: DbSession, limit: int = Query(3, ge=1, le=12)) -> dict[str, Any]:
    """Latest Content: newest published first; the newest is the featured item."""
    return {"items": [_card(i) for i in _public_items(db, limit)]}


@router.get("/website/content")
def archive(db: DbSession, limit: int = Query(60, ge=1, le=200)) -> dict[str, Any]:
    """'View all': every item that is visible on the website, newest first."""
    return {"items": [_card(i) for i in _public_items(db, limit)]}


@router.get("/website/status")
def website_status(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Dashboard only (Website page): sources, sync, what visitors see, and the section switches."""
    state = get_connector_state(db, Platform.YOUTUBE)
    sync = (state.capabilities or {}).get("sync") or {}
    home_data = _home(db)
    row = _settings(db)
    return {
        "source": "YouTube", "mode": yt.mode(db, runtime.settings),
        "status": state.status if yt.mode(db, runtime.settings) else "not_configured",
        "channel": state.account_name, "last_sync_at": sync.get("last_sync_at"), "last_error": state.last_error,
        "visible_items": int(db.scalar(select(func.count()).select_from(ContentItem).where(
            ContentItem.website_visible.is_(True), ContentItem.available.is_(True), ContentItem.privacy_status == "public")) or 0),
        "showing": home_data["latest"],
        "featured": home_data["latest"][0] if home_data["latest"] else None,
        "home": home_data,
        "saved_links": {k: (row.links or {}).get(k) or "" for k in NETWORKS},
        "price_board_source": None,  # no market-data integration yet
    }


class WebsiteSettingsUpdate(StrictInput):
    sections: dict[str, bool] | None = None
    links: dict[str, str] | None = None  # "" clears a link (back to the connected account, or hidden)
    site_url: str | None = None  # "" clears it

    @field_validator("sections")
    @classmethod
    def _known_sections(cls, v: dict[str, bool] | None) -> dict[str, bool] | None:
        if v and (unknown := set(v) - set(SECTIONS)):
            raise ValueError(f"unknown section(s): {', '.join(sorted(unknown))}")
        return v

    @field_validator("links")
    @classmethod
    def _valid_links(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        for k, url in (v or {}).items():
            if k not in NETWORKS:
                raise ValueError(f"unknown network: {k}")
            if url and not _is_web_url(url):
                raise ValueError(f"{k}: enter a full https:// link")
        return v

    @field_validator("site_url")
    @classmethod
    def _valid_site_url(cls, v: str | None) -> str | None:
        if v and not _is_web_url(v):
            raise ValueError("enter the full public address, e.g. https://example.com")
        return v.rstrip("/") if v else v


def _is_web_url(url: str) -> bool:
    p = urlparse(url.strip())
    return p.scheme in ("http", "https") and bool(p.netloc) and len(url) <= 256


@router.put("/website/settings")
def update_settings(payload: WebsiteSettingsUpdate, db: DbSession) -> dict[str, Any]:
    row = _settings(db)
    changes = []
    if payload.sections:
        row.sections = {**(row.sections or {}), **payload.sections}
        changes += [f"{k.replace('_', ' ')} {'on' if on else 'off'}" for k, on in payload.sections.items()]
    if payload.links is not None:
        row.links = {**(row.links or {}), **{k: u.strip() for k, u in payload.links.items()}}
        changes.append("links updated")
    if payload.site_url is not None:
        row.site_url = payload.site_url.strip() or None
        changes.append(f"public address {row.site_url or 'cleared'}")
    db.commit()
    if changes:
        ActivityLogger(db).log("website_settings_updated", f"Website: {', '.join(changes)}", actor="dashboard")
    return _home(db)


# --- dashboard: published content management ---------------------------------------------------------------


@router.get("/content-items")
def list_items(db: DbSession, platform: Platform | None = None, limit: int = Query(200, ge=1, le=1000)) -> list[dict[str, Any]]:
    stmt = select(ContentItem).order_by(ContentItem.published_at.desc(), ContentItem.id.desc()).limit(limit)
    if platform is not None:
        stmt = stmt.where(ContentItem.platform == platform)
    return [yt.item_out(i) for i in db.scalars(stmt)]


class ItemUpdate(StrictInput):
    website_visible: bool | None = None  # Show on website / Hide from website (manual from then on)
    featured: bool | None = None
    content_type: Literal["short", "long_form", "auto"] | None = None  # manual override, or back to automatic detection
    visibility: Literal["auto"] | None = None  # back to automatic visibility
    series: Literal["whats_it_worth", "originals", "none"] | None = None  # editorial franchise placement
    category: Literal["markets", "crypto", "money", "culture", "none"] | None = None  # website section / nav


SERIES_LABEL = {"whats_it_worth": "What's It Worth?", "originals": "Price Chat Originals"}


@router.patch("/content-items/{item_id}")
def update_item(item_id: int, payload: ItemUpdate, db: DbSession) -> dict[str, Any]:
    item = db.get(ContentItem, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Content item not found")
    changes = []
    if payload.website_visible is not None:
        item.website_visible, item.visibility_source = payload.website_visible, "manual"
        changes.append("shown on the website" if payload.website_visible else "hidden from the website")
    if payload.visibility == "auto":
        item.visibility_source = "auto"
        item.website_visible = item.available and item.privacy_status == "public"
        changes.append("website visibility back to automatic")
    if payload.featured is not None:
        item.featured = payload.featured
        changes.append("featured" if payload.featured else "no longer featured")
    if payload.content_type is not None:
        if payload.content_type == "auto":
            item.type_source = "auto"
            item.type_reason = "automatic detection on the next sync"
            changes.append("type back to automatic")
        else:
            item.content_type, item.type_source, item.type_reason = payload.content_type, "manual", "set by you"
            if item.platform == Platform.YOUTUBE:
                item.url = yt.video_url(item.external_id, item.content_type)
            changes.append(f"marked as {TYPE_LABEL.get(payload.content_type, payload.content_type)}")
    if payload.series is not None:
        item.series = None if payload.series == "none" else payload.series
        changes.append(f"placed in {SERIES_LABEL[item.series]}" if item.series else "removed from series")
    if payload.category is not None:
        item.category = None if payload.category == "none" else payload.category
        changes.append(f"category {item.category}" if item.category else "category cleared")
    db.commit()
    if changes:
        ActivityLogger(db).log("content_item_updated", f"“{item.title[:80]}”: {', '.join(changes)}", actor="dashboard",
                               details={"content_item_id": item.id})
    return yt.item_out(item)


# --- live community chat (public, read-only preview of the public Telegram group) ---------------------------------------

CHAT_LIMIT = 40
CHAT_TTL = 8.0  # seconds: every visitor polls; the database is read at most once per window
_MEDIA = {"photo": "📷 Photo", "video": "🎬 Video", "animation": "GIF", "sticker": "Sticker", "voice": "🎤 Voice message",
          "document": "📎 File", "audio": "🎵 Audio", "video_note": "🎬 Video message", "poll": "📊 Poll"}


def _first_name(name: str | None) -> str:
    """Only a first name is shown on the website (never a surname or handle)."""
    first = (name or "").strip().split(" ")[0][:24]
    return first or "Member"


@router.get("/website/chat")
def website_chat(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """The latest messages of the PUBLIC Price Chat Telegram group, read-only, for the website's chat bubble. Only a
    public group (one with a @username, so anyone can already read it in Telegram) is ever shown; first names only;
    bot accounts and /commands left out. Members chat in Telegram itself (the link)."""
    from app.pokemon import history
    from app.models.social_intel import GrowthSnapshot
    from app.models.telegram import TelegramMessage, TelegramSource

    if not runtime.settings.website_chat_enabled:
        return {"enabled": False}

    def build() -> dict[str, Any]:
        src = db.scalar(select(TelegramSource).where(TelegramSource.enabled.is_(True), TelegramSource.ingestion_enabled.is_(True),
                                                     TelegramSource.username.is_not(None), TelegramSource.username != "")
                        .order_by(TelegramSource.last_message_at.desc().nulls_last(), TelegramSource.id.desc()).limit(1))
        if src is None:
            return {"enabled": False}
        rows = list(db.scalars(select(TelegramMessage).where(TelegramMessage.chat_id == src.chat_id, TelegramMessage.author_is_bot.is_(False))
                               .order_by(TelegramMessage.sent_at.desc(), TelegramMessage.id.desc()).limit(CHAT_LIMIT * 2)))
        out = []
        for m in rows:
            text = (m.text or "").strip()
            if text.startswith("/"):
                continue
            if not text:
                text = _MEDIA.get(m.media_type or "", "")
            if not text:
                continue
            out.append({"id": m.message_id, "name": _first_name(m.author_name), "text": text[:600], "at": m.sent_at.isoformat(),
                        "reply_to": m.reply_to_message_id, "forwarded": bool(m.forward_from)})
            if len(out) >= CHAT_LIMIT:
                break
        snap = db.scalar(select(GrowthSnapshot).where(GrowthSnapshot.kind == "telegram_chat", GrowthSnapshot.subject == str(src.chat_id))
                         .order_by(GrowthSnapshot.taken_at.desc()).limit(1))
        return {"enabled": True, "title": src.display_name, "username": src.username, "link": f"https://t.me/{src.username}",
                "members": snap.members if snap else None, "messages": list(reversed(out)),
                "last_message_at": out[0]["at"] if out else None, "as_of": utcnow().isoformat()}

    return history.cached(("website-chat",), build, ttl=CHAT_TTL)
