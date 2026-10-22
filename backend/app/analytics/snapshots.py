"""Audience snapshots for the growth dashboard, recorded from now on.

X follower counts are taken from responses Price Chat already receives (our own user object comes
back with own-post reads and the connection test). Only when nothing fresh arrived for
X_SNAPSHOT_HOURS does the X reading job make one small account lookup. Telegram member counts come
from the free Bot API (or the logged-in account's dialog list) every few hours.

History is never back-filled or estimated: a chart starts at the first snapshot.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.social_intel import GrowthSnapshot
from app.models.telegram import TelegramMessage, TelegramSource

logger = logging.getLogger(__name__)

X_SNAPSHOT_HOURS = 6  # max age before the X job pays for one account lookup
MIN_GAP = timedelta(minutes=55)  # unchanged values are stored at most ~hourly
TELEGRAM_SNAPSHOT_SECONDS = 6 * 3600


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    from datetime import timezone

    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def last_snapshot(db: Session, kind: str, subject: str) -> GrowthSnapshot | None:
    return db.scalar(select(GrowthSnapshot).where(GrowthSnapshot.kind == kind, GrowthSnapshot.subject == subject)
                     .order_by(GrowthSnapshot.taken_at.desc(), GrowthSnapshot.id.desc()).limit(1))


def record(db: Session, kind: str, subject: str, *, source: str, label: str = "", at: datetime | None = None,
           **values: int | None) -> GrowthSnapshot | None:
    """Store a snapshot unless an identical one was taken within MIN_GAP. Unknown values stay NULL."""
    now = at or utcnow()
    values = {k: v for k, v in values.items() if k in ("followers", "following", "posts", "members", "messages")}
    if all(v is None for v in values.values()):
        return None
    last = last_snapshot(db, kind, subject)
    if last is not None:
        same = all(getattr(last, k) == v for k, v in values.items() if v is not None)
        if same and now - _aware(last.taken_at) < MIN_GAP:  # type: ignore[operator]
            return None
    snap = GrowthSnapshot(kind=kind, subject=subject, label=label, taken_at=now, source=source, **values)
    db.add(snap)
    db.flush()
    return snap


def record_x_account(db: Session, user: dict[str, Any] | Any, source: str, at: datetime | None = None) -> GrowthSnapshot | None:
    """From an X user object ({"id", "username", "public_metrics": {...}}) or a SocialProfileData."""
    if isinstance(user, dict):
        pm = user.get("public_metrics") or {}
        uid, name = user.get("id"), user.get("username") or ""
        followers, following, posts = pm.get("followers_count"), pm.get("following_count"), pm.get("tweet_count")
    else:
        uid, name = getattr(user, "external_id", None), getattr(user, "username", "")
        followers, following, posts = user.followers_count, user.following_count, user.post_count
    if not uid:
        return None
    return record(db, "x_account", str(uid), source=source, label=f"@{name}" if name else "", at=at,
                  followers=followers, following=following, posts=posts)


def x_snapshot_due(db: Session, account_id: str | None, now: datetime | None = None) -> bool:
    if not account_id:
        return False
    last = last_snapshot(db, "x_account", account_id)
    return last is None or (now or utcnow()) - _aware(last.taken_at) >= timedelta(hours=X_SNAPSHOT_HOURS)  # type: ignore[operator]


def message_counts(db: Session) -> dict[int, int]:
    return {sid: int(n) for sid, n in db.execute(select(TelegramMessage.source_id, func.count()).group_by(TelegramMessage.source_id))}


async def snapshot_telegram(db: Session, settings, runtime) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Members per authorised chat (bot: getChatMemberCount; account: dialog list) plus messages collected.

    Network first, then one short write. A chat whose member count can't be read still gets its message
    count recorded (members stay unknown, never guessed).
    """
    sources = list(db.scalars(select(TelegramSource).where(TelegramSource.enabled.is_(True))))
    if not sources:
        return {"chats": 0, "recorded": 0}
    members: dict[int, int | None] = {}
    errors: list[str] = []
    bot_sources = [s for s in sources if (getattr(s, "via", "bot") or "bot") == "bot"]
    account_sources = [s for s in sources if getattr(s, "via", "bot") == "account"]
    if bot_sources:
        from app.security.credentials import telegram_token
        from app.telegram.service import make_client

        if telegram_token(settings):
            client = make_client(settings, runtime.telegram.transport)
            try:
                for s in bot_sources:
                    try:
                        members[s.chat_id] = await client.get_chat_member_count(s.chat_id)
                    except Exception as exc:  # noqa: BLE001 - one chat failing must not stop the others
                        errors.append(f"{s.display_name}: {type(exc).__name__}")
            finally:
                await client.aclose()
    if account_sources:
        from app.telegram import account_service

        if account_service.has_session(settings):
            try:
                async with account_service.session(db, settings, runtime.telegram_account) as gw:
                    dialogs = {d.chat_id: d for d in await gw.dialogs()}
                for s in account_sources:
                    d = dialogs.get(s.chat_id)
                    members[s.chat_id] = d.members if d else None
            except Exception as exc:  # noqa: BLE001
                errors.append(f"account: {type(exc).__name__}")
    counts = message_counts(db)
    recorded = 0
    for s in sources:
        snap = record(db, "telegram_chat", str(s.chat_id), source="bot_api" if s in bot_sources else "account_api",
                      label=s.display_name, members=members.get(s.chat_id), messages=counts.get(s.id, 0))
        recorded += snap is not None
    db.commit()
    return {"chats": len(sources), "recorded": recorded, "errors": errors}
