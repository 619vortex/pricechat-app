"""YouTube connection and sync (read-only) → platform-independent ContentItems → the website.

Flow: uploads playlist of the selected Price Chat channel → new video ids → videos.list (details,
status, statistics, aspect ratio) → upsert one ContentItem per video id → public items become
visible on the website automatically. Nothing is ever posted, edited or uploaded.

Quota: each list call costs 1 unit. A normal sync is 1 playlist page + 1-2 videos calls (+1 channel
call per hour): about 300 units/day at the default 15-minute interval, well within 10,000.
Network first, then one short write (no write transaction across awaits).
"""

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.connectors.state import get_connector_state
from app.db.base import utcnow
from app.models.content_items import ContentItem, ContentItemMetric
from app.models.enums import ActivityLevel, Platform
from app.security.credentials import info as secret_info
from app.security.credentials import resolve, secret_store
from app.services.activity import ActivityLogger
from app.youtube import oauth
from app.youtube.client import YouTubeAuthError, YouTubeClient, YouTubeError, YouTubeNotConfigured

logger = logging.getLogger(__name__)

SHORT_MAX_SECONDS = 180  # YouTube Shorts can be up to 3 minutes (since Oct 2024)
SHORT_SURE_SECONDS = 60  # up to 60 s without aspect information still counts as a Short
FIRST_SYNC_PAGES = 10  # first sync reads up to 500 uploads
RECHECK_DAYS = 30  # items published in the last 30 days (or on the website) are re-checked every sync
CHANNEL_REFRESH = timedelta(hours=1)
METRIC_EVERY = timedelta(hours=6)
METRIC_EVERY_NEW = timedelta(hours=1)  # videos younger than 48 h
SECRETS = ("youtube_api_key", "youtube_client_id", "youtube_client_secret", "youtube_refresh_token")
FUTURE_DISTRIBUTION = {"x_post": "not_started", "telegram_announcement": "not_started", "tiktok_caption": "not_started"}


@dataclass(slots=True)
class YouTubeRuntime:
    transport: httpx.AsyncBaseTransport | None = None  # tests inject a mock
    pending: dict[str, oauth.PendingLogin] = field(default_factory=dict)
    access_token: str | None = None
    access_expires: float = 0.0
    channels: list[dict[str, Any]] = field(default_factory=list)  # channels found for the signed-in Google account


# --- configuration ------------------------------------------------------------------------------------


def mode(db: Session, settings: Settings) -> str | None:
    state = get_connector_state(db, Platform.YOUTUBE)
    if "oauth" in (state.auth_modes or []) and resolve("youtube_refresh_token", settings):
        return "oauth"
    if resolve("youtube_api_key", settings):
        return "api_key"
    return None


def channel_info(db: Session) -> dict[str, Any]:
    return dict((get_connector_state(db, Platform.YOUTUBE).capabilities or {}).get("channel") or {})


def _set_caps(db: Session, **values: Any) -> None:
    state = get_connector_state(db, Platform.YOUTUBE)
    caps = dict(state.capabilities or {})
    caps.update(values)
    state.capabilities = caps
    ch = values.get("channel")
    if ch and ch.get("id") and ch.get("subscribers") is not None:  # hidden counts are never recorded (or guessed)
        from app.analytics.snapshots import record

        # subscriber history for the website's community rows; the statistics come with every channel read anyway.
        # Stamped with when YouTube reported it, so re-saving a cached reading (sync refreshes it hourly) adds nothing.
        record(db, "youtube_channel", str(ch["id"]), source="youtube_api", label=ch.get("handle") or ch.get("title") or "",
               at=_published(ch.get("checked_at")), followers=int(ch["subscribers"]), posts=ch.get("videos"))


def _count_request(db: Session) -> None:
    state = get_connector_state(db, Platform.YOUTUBE)
    day = utcnow().strftime("%Y-%m-%d")
    usage = dict(state.usage or {})
    u = dict(usage.get(day) or {"requests": 0, "units": 0})
    u["requests"] += 1
    u["units"] += 1
    usage[day] = u
    state.usage = {k: usage[k] for k in sorted(usage)[-30:]}


def parse_channel_ref(ref: str) -> tuple[str | None, str | None]:
    """'@pricechat', 'pricechat', a channel URL or a UC... id → (channel_id, handle)."""
    ref = (ref or "").strip()
    m = re.search(r"youtube\.com/(?:channel/)(UC[\w-]{22})", ref) or re.fullmatch(r"(UC[\w-]{22})", ref)
    if m:
        return m.group(1), None
    m = re.search(r"youtube\.com/@([\w.\-]+)", ref) or re.fullmatch(r"@?([\w.\-]{3,30})", ref)
    if m:
        return None, f"@{m.group(1)}"
    raise ValueError("Enter the channel as @handle, a youtube.com/@handle URL or a channel id (UC...)")


async def _client(db: Session, settings: Settings, rt: YouTubeRuntime) -> YouTubeClient:
    m = mode(db, settings)
    on_request = lambda _endpoint: _count_request(db)  # noqa: E731
    if m == "api_key":
        return YouTubeClient(settings.youtube_api_base, api_key=resolve("youtube_api_key", settings), transport=rt.transport, on_request=on_request)
    if m == "oauth":
        return YouTubeClient(settings.youtube_api_base, access_token=await _access_token(settings, rt), transport=rt.transport, on_request=on_request)
    raise YouTubeNotConfigured("YouTube is not connected: add an API key or sign in with Google on the YouTube page")


async def _access_token(settings: Settings, rt: YouTubeRuntime) -> str:
    if rt.access_token and time.monotonic() < rt.access_expires - 60:
        return rt.access_token
    cid, secret, refresh_token = (resolve(n, settings) for n in ("youtube_client_id", "youtube_client_secret", "youtube_refresh_token"))
    if not (cid and secret and refresh_token):
        raise YouTubeNotConfigured("Sign in with Google again on the YouTube page")
    body = await oauth.refresh(settings.google_token_url, cid, secret, refresh_token, rt.transport)
    rt.access_token, rt.access_expires = body["access_token"], time.monotonic() + int(body.get("expires_in", 3600))
    return rt.access_token


def _channel_summary(ch: dict[str, Any]) -> dict[str, Any]:
    sn, st, cd = ch.get("snippet") or {}, ch.get("statistics") or {}, ch.get("contentDetails") or {}
    hidden = bool(st.get("hiddenSubscriberCount"))
    thumbs = sn.get("thumbnails") or {}
    return {
        "id": ch.get("id"), "title": sn.get("title"), "handle": sn.get("customUrl"),
        "subscribers": None if hidden or "subscriberCount" not in st else int(st["subscriberCount"]),
        "subscribers_hidden": hidden, "videos": int(st["videoCount"]) if "videoCount" in st else None,
        "views": int(st["viewCount"]) if "viewCount" in st else None,
        "uploads_playlist": (cd.get("relatedPlaylists") or {}).get("uploads"),
        "thumbnail": ((thumbs.get("default") or {}).get("url")), "checked_at": utcnow().isoformat(),
    }


def _select(db: Session, ch: dict[str, Any], log: str) -> None:
    state = get_connector_state(db, Platform.YOUTUBE)
    summary = _channel_summary(ch)
    state.account_external_id, state.account_username, state.account_name = summary["id"], summary["handle"], summary["title"]
    state.status, state.last_success_at, state.last_error = "connected", utcnow(), None
    _set_caps(db, channel=summary)
    db.commit()
    ActivityLogger(db).log("youtube_connected", f"YouTube channel {summary['title']} ({summary['handle'] or summary['id']}) {log}", actor="dashboard")


async def connect_api_key(db: Session, settings: Settings, rt: YouTubeRuntime, channel_ref: str, api_key: str | None) -> dict[str, Any]:
    """Check the key and find the channel, then save. A wrong key or channel saves nothing."""
    channel_id, handle = parse_channel_ref(channel_ref)
    key = (api_key or "").strip() or resolve("youtube_api_key", settings)
    if not key:
        raise ValueError("Paste the YouTube Data API key")
    client = YouTubeClient(settings.youtube_api_base, api_key=key, transport=rt.transport)
    try:
        found = await client.channels(ids=[channel_id] if channel_id else None, handle=handle)
    finally:
        await client.aclose()
    if not found:
        raise ValueError(f"No YouTube channel found for {channel_ref!r}")
    if (api_key or "").strip():
        secret_store(settings).set("youtube_api_key", key)
    state = get_connector_state(db, Platform.YOUTUBE)
    state.auth_modes = ["api_key"]
    _count_request(db)
    _select(db, found[0], "connected with an API key (read-only)")
    return status(db, settings, rt)


def save_oauth_client(db: Session, settings: Settings, client_id: str, client_secret: str | None) -> None:
    client_id = client_id.strip()
    if not client_id.endswith(".apps.googleusercontent.com"):
        raise ValueError("That does not look like a Google OAuth client ID (it ends with .apps.googleusercontent.com)")
    store = secret_store(settings)
    store.set("youtube_client_id", client_id)
    if client_secret and client_secret.strip():
        store.set("youtube_client_secret", client_secret.strip())
    elif not resolve("youtube_client_secret", settings):
        raise ValueError("Paste the OAuth client secret")
    ActivityLogger(db).log("youtube_oauth_client_saved", "Google OAuth client saved for YouTube (encrypted)", actor="dashboard")


def start_oauth(settings: Settings, rt: YouTubeRuntime) -> str:
    cid = resolve("youtube_client_id", settings)
    if not cid or not resolve("youtube_client_secret", settings):
        raise ValueError("Save the Google OAuth client ID and secret first")
    now = time.monotonic()
    rt.pending = {s: p for s, p in rt.pending.items() if now - p.created < oauth.STATE_TTL}
    state, pending, challenge = oauth.new_login()
    rt.pending[state] = pending
    return oauth.authorization_url(settings.google_auth_url, cid, settings.youtube_redirect_uri, state, challenge)


async def finish_oauth(db: Session, settings: Settings, rt: YouTubeRuntime, code: str, state_value: str) -> dict[str, Any]:
    pending = rt.pending.pop(state_value, None)
    if pending is None or time.monotonic() - pending.created > oauth.STATE_TTL:
        raise YouTubeAuthError("This sign-in link expired or was already used; start again from the YouTube page")
    cid, secret = resolve("youtube_client_id", settings), resolve("youtube_client_secret", settings)
    body = await oauth.exchange_code(settings.google_token_url, cid or "", secret or "", code, pending.verifier,
                                     settings.youtube_redirect_uri, rt.transport)
    if body.get("refresh_token"):
        secret_store(settings).set("youtube_refresh_token", body["refresh_token"])
    elif not resolve("youtube_refresh_token", settings):
        raise YouTubeAuthError("Google did not return a refresh token; remove Price Chat's access in your Google account and sign in again")
    rt.access_token, rt.access_expires = body["access_token"], time.monotonic() + int(body.get("expires_in", 3600))
    state = get_connector_state(db, Platform.YOUTUBE)
    state.auth_modes = ["oauth"]
    db.commit()
    client = await _client(db, settings, rt)
    try:
        rt.channels = [_channel_summary(c) for c in await client.channels(mine=True)]
    finally:
        await client.aclose()
    db.commit()
    if not rt.channels:
        raise YouTubeError("This Google account has no YouTube channel. Sign in with the account that owns the Price Chat channel.")
    ActivityLogger(db).log("youtube_oauth_connected", f"Signed in with Google (read-only); {len(rt.channels)} channel(s) found", actor="dashboard")
    current = state.account_external_id
    if len(rt.channels) == 1 and current in (None, rt.channels[0]["id"]):
        await select_channel(db, settings, rt, rt.channels[0]["id"])
    return {"channels": rt.channels, "selected": get_connector_state(db, Platform.YOUTUBE).account_external_id}


async def select_channel(db: Session, settings: Settings, rt: YouTubeRuntime, channel_id: str) -> dict[str, Any]:
    """Explicit choice of the Price Chat channel (required when the Google account has several)."""
    if mode(db, settings) == "oauth" and rt.channels and channel_id not in {c["id"] for c in rt.channels}:
        raise ValueError("That channel is not one of the channels of the signed-in Google account")
    client = await _client(db, settings, rt)
    try:
        found = await client.channels(ids=[channel_id])
    finally:
        await client.aclose()
    if not found:
        raise ValueError("Channel not found")
    state = get_connector_state(db, Platform.YOUTUBE)
    if state.account_external_id and state.account_external_id != channel_id:
        # A different channel: its content must not mix with the previous one on the website.
        for item in db.scalars(select(ContentItem).where(ContentItem.platform == Platform.YOUTUBE, ContentItem.channel_id != channel_id)):
            item.website_visible, item.visibility_source = False, "auto"
    _select(db, found[0], "selected as the Price Chat channel")
    return status(db, settings, rt)


async def test_connection(db: Session, settings: Settings, rt: YouTubeRuntime) -> dict[str, Any]:
    state = get_connector_state(db, Platform.YOUTUBE)
    if not state.account_external_id:
        raise YouTubeNotConfigured("Choose the Price Chat channel first")
    client = await _client(db, settings, rt)
    try:
        found = await client.channels(ids=[state.account_external_id])
    except YouTubeError as exc:
        _record_error(db, exc)
        raise
    finally:
        await client.aclose()
    if not found:
        exc = YouTubeError("The selected channel is no longer available")
        _record_error(db, exc)
        raise exc
    _set_caps(db, channel=_channel_summary(found[0]))
    state.status, state.last_success_at, state.last_error = "connected", utcnow(), None
    db.commit()
    ActivityLogger(db).log("youtube_tested", f"YouTube connection tested: {state.account_name}", actor="dashboard")
    return status(db, settings, rt)


def disconnect(db: Session, settings: Settings, rt: YouTubeRuntime) -> dict[str, Any]:
    """Forget credentials and the token. Synced content stays (the website keeps working); hide items in Content if needed."""
    store = secret_store(settings)
    for name in SECRETS:
        store.delete(name)
    rt.access_token, rt.access_expires, rt.channels, rt.pending = None, 0.0, [], {}
    state = get_connector_state(db, Platform.YOUTUBE)
    state.status, state.auth_modes = "not_configured", []
    db.commit()
    ActivityLogger(db).log("youtube_disconnected", "YouTube disconnected; credentials removed. Synced content was kept.", actor="dashboard")
    return status(db, settings, rt)


def _record_error(db: Session, exc: Exception) -> None:
    state = get_connector_state(db, Platform.YOUTUBE)
    state.last_error, state.last_error_at = str(exc), utcnow()
    if isinstance(exc, YouTubeAuthError):
        state.status = "error"
    db.commit()


# --- Short detection -------------------------------------------------------------------------------------


_DURATION = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def parse_duration(iso: str | None) -> int | None:
    """ISO-8601 duration (PT1M5S) → seconds. Live/upcoming videos report P0D → None."""
    if not iso:
        return None
    m = _DURATION.fullmatch(iso)
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    total = d * 86400 + h * 3600 + mi * 60 + s
    return total or None


def classify(duration: int | None, embed_w: int | None, embed_h: int | None, text: str = "") -> tuple[str, str]:
    """Short vs long form, from what the Data API returns (it has no "is Short" flag).

    Rule: a Short is at most 3 minutes AND vertical or square. The aspect ratio comes from the
    player embed size the API reports. Without it: up to 60 s counts as a Short; 61-180 s only with
    #shorts in the title, description or tags. A title merely containing "Shorts" is never enough.
    """
    if duration is None:
        return "long_form", "duration unknown (live, upcoming or processing)"
    if duration > SHORT_MAX_SECONDS:
        return "long_form", f"{duration} s is longer than 3 minutes"
    if embed_w and embed_h:
        if embed_h >= embed_w:
            return "short", f"{duration} s and {'vertical' if embed_h > embed_w else 'square'} ({embed_w}x{embed_h})"
        return "long_form", f"{duration} s but horizontal ({embed_w}x{embed_h})"
    if duration <= SHORT_SURE_SECONDS:
        return "short", f"{duration} s (aspect ratio not reported)"
    if re.search(r"#shorts\b", text, re.I):
        return "short", f"{duration} s with #shorts (aspect ratio not reported)"
    return "long_form", f"{duration} s, aspect ratio not reported, no #shorts"


def video_url(video_id: str, content_type: str) -> str:
    return f"https://www.youtube.com/shorts/{video_id}" if content_type == "short" else f"https://www.youtube.com/watch?v={video_id}"


def best_thumbnail(thumbs: dict[str, Any]) -> str | None:
    for size in ("maxres", "standard", "high", "medium", "default"):
        url = (thumbs.get(size) or {}).get("url")
        if url:
            return url
    return None


def fallback_thumbnail(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


# --- sync ---------------------------------------------------------------------------------------------------


@dataclass(slots=True)
class SyncReport:
    listed: int = 0
    new: int = 0
    updated: int = 0
    unavailable: int = 0
    metric_snapshots: int = 0
    requests: int = 0
    new_titles: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"listed": self.listed, "new": self.new, "updated": self.updated, "unavailable": self.unavailable,
                "metric_snapshots": self.metric_snapshots, "requests": self.requests, "new_titles": self.new_titles[:10]}


def _aware(dt: datetime | None) -> datetime | None:
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))


def _published(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def sync(db: Session, settings: Settings, rt: YouTubeRuntime) -> SyncReport:
    report = SyncReport()
    state = get_connector_state(db, Platform.YOUTUBE)
    channel_id = state.account_external_id
    if not channel_id:
        raise YouTubeNotConfigured("Choose the Price Chat channel first")
    ch = channel_info(db)
    known = {i.external_id: i for i in db.scalars(select(ContentItem).where(ContentItem.platform == Platform.YOUTUBE))}
    now = utcnow()

    # 1. network (no database writes held open meanwhile)
    client = await _client(db, settings, rt)
    counted = client.on_request

    def count(endpoint: str) -> None:
        report.requests += 1
        if counted:
            counted(endpoint)

    client.on_request = count
    db.commit()
    try:
        last = _published(ch.get("checked_at"))
        if not ch.get("uploads_playlist") or last is None or now - last >= CHANNEL_REFRESH:
            found = await client.channels(ids=[channel_id])
            if not found:
                raise YouTubeError("The selected channel is no longer available")
            ch = _channel_summary(found[0])
        playlist_ids: list[str] = []
        token, pages = None, 0
        while True:
            body = await client.playlist_items(ch["uploads_playlist"], token)
            pages += 1
            ids = [(it.get("contentDetails") or {}).get("videoId") for it in body.get("items") or []]
            ids = [i for i in ids if i]
            playlist_ids += ids
            token = body.get("nextPageToken")
            first_sync = not known
            if not token or (not first_sync and any(i in known for i in ids)) or pages >= (FIRST_SYNC_PAGES if first_sync else 3):
                break
        report.listed = len(playlist_ids)
        recent = now - timedelta(days=RECHECK_DAYS)
        recheck = [i.external_id for i in sorted(known.values(), key=lambda x: _aware(x.published_at) or now, reverse=True)
                   if i.channel_id == channel_id and (i.website_visible or (_aware(i.published_at) or now) >= recent)][:100]
        wanted = list(dict.fromkeys([i for i in playlist_ids if i not in known] + recheck + [i for i in playlist_ids if i in known][:50]))
        videos: dict[str, dict[str, Any]] = {}
        for start in range(0, len(wanted), 50):
            for v in await client.videos(wanted[start:start + 50]):
                videos[v["id"]] = v
    except YouTubeError as exc:
        db.rollback()
        _record_error(db, exc)
        raise
    finally:
        await client.aclose()

    # 2. one short write
    new_items: list[ContentItem] = []
    ids = [known[v].id for v in wanted if v in known]
    last_capture = dict(db.execute(select(ContentItemMetric.item_id, func.max(ContentItemMetric.captured_at))
                                   .where(ContentItemMetric.item_id.in_(ids)).group_by(ContentItemMetric.item_id)).all()) if ids else {}
    for vid in wanted:
        v = videos.get(vid)
        item = known.get(vid)
        if v is None:
            if item is not None and item.available:  # deleted, or private to an API key
                item.available, item.unavailable_since = False, now
                item.privacy_status = "unavailable"
                report.unavailable += 1
            continue
        created = item is None
        if created:
            item = ContentItem(platform=Platform.YOUTUBE, external_id=vid, distribution=dict(FUTURE_DISTRIBUTION))
            db.add(item)
            known[vid] = item
        _apply(item, v, channel_id, now)
        if created:
            item.website_visible = item.privacy_status == "public" and item.available
            report.new += 1
            report.new_titles.append(item.title)
            new_items.append(item)
        else:
            report.updated += 1
        if _snapshot_due(item, last_capture.get(item.id) if item.id else None, now):
            db.flush()
            m = item.metrics or {}
            db.add(ContentItemMetric(item_id=item.id, captured_at=now, views=m.get("views"), likes=m.get("likes"), comments=m.get("comments")))
            report.metric_snapshots += 1
    _set_caps(db, channel=ch, sync={"last_sync_at": now.isoformat(), **report.as_dict()})
    state.status, state.last_success_at, state.last_error = "connected", now, None
    db.commit()
    log = ActivityLogger(db)
    first_sync = len(new_items) == len(known)  # everything was new: a first sync, summarised in one line
    if first_sync and len(new_items) > 3:
        log.log("youtube_new_content", f"First YouTube sync: {len(new_items)} videos found "
                f"({sum(1 for i in new_items if i.content_type == 'short')} Shorts)", platform=Platform.YOUTUBE)
    else:
        for item in new_items[:10]:
            kind = "Short" if item.content_type == "short" else "video"
            shown = " Shown on the website automatically." if public(item) else ""
            log.log("youtube_new_content", f"New YouTube {kind} detected: {item.title}.{shown}", platform=Platform.YOUTUBE,
                    details={"video_id": item.external_id, "content_item_id": item.id})
    if report.unavailable:
        log.log("youtube_content_unavailable", f"{report.unavailable} YouTube video(s) are no longer public; hidden from the website",
                level=ActivityLevel.WARNING, platform=Platform.YOUTUBE)
    return report


def _apply(item: ContentItem, v: dict[str, Any], channel_id: str, now: datetime) -> None:
    sn, cd, st, stats, player = (v.get(k) or {} for k in ("snippet", "contentDetails", "status", "statistics", "player"))
    item.channel_id = sn.get("channelId") or channel_id
    item.title = (sn.get("title") or "")[:512]
    item.description = sn.get("description") or ""
    item.tags = list(sn.get("tags") or [])[:30]
    item.published_at = _published(sn.get("publishedAt")) or item.published_at
    item.thumbnails = {k: {"url": t.get("url"), "width": t.get("width"), "height": t.get("height")} for k, t in (sn.get("thumbnails") or {}).items()}
    item.thumbnail_url = best_thumbnail(sn.get("thumbnails") or {}) or fallback_thumbnail(item.external_id)
    item.duration_seconds = parse_duration(cd.get("duration"))
    if item.type_source != "manual":
        w, h = player.get("embedWidth"), player.get("embedHeight")
        item.content_type, item.type_reason = classify(item.duration_seconds, int(w) if w else None, int(h) if h else None,
                                                       " ".join([item.title, item.description, " ".join(f"#{t}" for t in item.tags)]))
    item.url = video_url(item.external_id, item.content_type)
    privacy = st.get("privacyStatus") or "public"  # an API key only ever sees public videos
    was_public = item.privacy_status == "public" and item.available
    item.privacy_status = privacy
    item.available = st.get("uploadStatus", "processed") not in ("deleted", "rejected", "failed")
    if not item.available:
        item.unavailable_since = item.unavailable_since or now
    else:
        item.unavailable_since = None
    if item.id and not was_public and privacy == "public" and item.available and item.visibility_source == "auto":
        item.website_visible = True  # became public again (or finished processing)
    item.metrics = {k: int(stats[src]) if src in stats else None for k, src in (("views", "viewCount"), ("likes", "likeCount"), ("comments", "commentCount"))}
    item.metrics_updated_at = now
    item.last_synced_at = now


def _snapshot_due(item: ContentItem, last: datetime | None, now: datetime) -> bool:
    """`last`: the item's newest metric capture (read for the whole sync in one query, not one per video)."""
    if not item.id or last is None:
        return True
    young = (_aware(item.published_at) or now) >= now - timedelta(hours=48)
    return now - _aware(last) >= (METRIC_EVERY_NEW if young else METRIC_EVERY)  # type: ignore[operator]


# --- status -----------------------------------------------------------------------------------------------


def public(item: ContentItem) -> bool:
    """May appear on the website: public, still available, and not hidden."""
    return item.website_visible and item.available and item.privacy_status == "public"


def status(db: Session, settings: Settings, rt: YouTubeRuntime, next_sync_at: datetime | None = None) -> dict[str, Any]:
    state = get_connector_state(db, Platform.YOUTUBE)
    caps = state.capabilities or {}
    items = list(db.scalars(select(ContentItem).where(ContentItem.platform == Platform.YOUTUBE)))
    mine = [i for i in items if i.channel_id == state.account_external_id] if state.account_external_id else []
    latest = max(mine, key=lambda i: _aware(i.published_at) or datetime.min.replace(tzinfo=timezone.utc), default=None)
    m = mode(db, settings)
    tot = lambda k: sum((i.metrics or {}).get(k) or 0 for i in mine if (i.metrics or {}).get(k) is not None) if any((i.metrics or {}).get(k) is not None for i in mine) else None  # noqa: E731
    today = (state.usage or {}).get(utcnow().strftime("%Y-%m-%d")) or {}
    return {
        "status": state.status if m else "not_configured", "mode": m,
        "api_key": _secret("youtube_api_key", settings),
        "oauth_client": bool(resolve("youtube_client_id", settings) and resolve("youtube_client_secret", settings)),
        "oauth_signed_in": bool(resolve("youtube_refresh_token", settings)),
        "redirect_uri": settings.youtube_redirect_uri,
        "channel": caps.get("channel") if state.account_external_id else None,
        "channels_available": rt.channels,
        "last_sync": caps.get("sync"), "next_sync_at": next_sync_at.isoformat() if next_sync_at else None,
        "last_success_at": state.last_success_at.isoformat() if state.last_success_at else None,
        "last_error": state.last_error, "last_error_at": state.last_error_at.isoformat() if state.last_error_at else None,
        "quota_today": {"requests": today.get("requests", 0), "units": today.get("units", 0), "daily_limit": 10000},
        "content": {
            "shorts": sum(1 for i in mine if i.content_type == "short"), "long_form": sum(1 for i in mine if i.content_type == "long_form"),
            "on_website": sum(1 for i in mine if public(i)), "unavailable": sum(1 for i in mine if not i.available or i.privacy_status != "public"),
            "latest": item_out(latest) if latest else None,
        },
        "performance": {"views": tot("views"), "likes": tot("likes"), "comments": tot("comments")},
    }


def _secret(name: str, settings: Settings) -> dict[str, Any]:
    i = secret_info(name, settings)
    return {"configured": i.configured, "source": i.source, "saved_at": i.saved_at.isoformat() if i.saved_at else None}


def item_out(i: ContentItem) -> dict[str, Any]:
    return {
        "id": i.id, "platform": i.platform.value, "external_id": i.external_id, "channel_id": i.channel_id,
        "content_type": i.content_type, "type_source": i.type_source, "type_reason": i.type_reason,
        "title": i.title, "description": i.description[:500], "url": i.url, "thumbnail_url": i.thumbnail_url,
        "duration_seconds": i.duration_seconds, "published_at": _aware(i.published_at).isoformat() if i.published_at else None,  # type: ignore[union-attr]
        "privacy_status": i.privacy_status, "available": i.available, "website_visible": i.website_visible,
        "visibility_source": i.visibility_source, "featured": i.featured, "on_website": public(i),
        "metrics": i.metrics or {}, "last_synced_at": _aware(i.last_synced_at).isoformat() if i.last_synced_at else None,  # type: ignore[union-attr]
        "distribution": i.distribution or {}, "series": i.series, "category": i.category,
    }
