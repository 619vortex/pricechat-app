"""Telegram missions around Price Chat's announcement channel (@Pricechatupdates by default).

What Price Chat can actually verify (checked, never assumed):

  Join the channel   getChatMember(channel, member's linked Telegram id). Telegram only answers this for a CHANNEL
                     when our bot is an administrator of it. `check_channel` records whether that is the case; until it
                     is, the mission is not offered at all (never a click-to-claim).
  Join the discussion  The channel's linked discussion group is where comments live. Our bot already reads that group
                     (it must be an authorised Price Chat chat). Each channel post arrives there as an automatic forward
                     from Telegram (sender id 777000); a comment is a message replying to it (or to a reply in its thread).
                     Detection uses only those ingested messages and the member's linked numeric Telegram id.

Anti-spam for discussion rewards: one reward per announcement, the member's own words (15+ characters, not a forward
or command), not a copy of another comment in the thread or of the member's own recent messages, a daily limit, and
only for members who linked Telegram before commenting. Opinions are never judged.
"""

import logging
import re
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.members import missions as mx
from app.members.referrals import program
from app.members.verified import meaningful
from app.models.members import LinkedIdentity, Member, MemberMission, Mission
from app.models.telegram import TelegramMessage, TelegramSource

logger = logging.getLogger(__name__)

TELEGRAM_SERVICE_USER = 777000  # sender of automatic forwards from a linked channel into its discussion group
JOIN_KIND = "telegram_join_channel"
DISCUSSION_KIND = "telegram_discussion"
MEMBER_STATUSES = {"creator", "administrator", "member"}
MAX_THREAD_DEPTH = 20
COPY_LOOKBACK = timedelta(days=14)


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def channel_handle(db: Session) -> str | None:
    h = (program(db).updates_channel or "").strip()
    if not h:
        return None
    h = h.removeprefix("https://t.me/").removeprefix("t.me/").strip("/")
    return h if h.startswith("@") or h.lstrip("-").isdigit() else f"@{h}"


def channel_url(db: Session) -> str | None:
    h = channel_handle(db)
    return f"https://t.me/{h[1:]}" if h and h.startswith("@") else None


# --- what the bot can see ----------------------------------------------------------------------------------------


async def check_channel(db: Session, client: Any) -> dict[str, Any]:
    """Ask Telegram what our bot can see of the announcement channel and record it. Read-only."""
    from app.telegram.errors import TelegramError

    s = program(db)
    handle = channel_handle(db)
    s.channel_checked_at, s.channel_error, s.channel_bot_can_verify = utcnow(), None, False
    if not handle:
        s.channel_error = "No announcement channel is set."
        db.commit()
        return status(db)
    try:
        chat = await client.get_chat(handle)
        s.channel_id, s.channel_title, s.channel_linked_chat_id = int(chat["id"]), chat.get("title"), chat.get("linked_chat_id")
        group = db.scalar(select(TelegramSource).where(TelegramSource.chat_id == s.channel_linked_chat_id)) if s.channel_linked_chat_id else None
        if group is not None and not group.username:  # a public group: links to its posts work for everyone, not only members
            group.username = (await client.get_chat(group.chat_id)).get("username") or None
        me = await client.get_me()
        member = await client.get_chat_member(s.channel_id, int(me["id"]))
        s.channel_bot_can_verify = member.get("status") in ("administrator", "creator")
        if not s.channel_bot_can_verify:
            s.channel_error = "The Price Chat bot is not an administrator of the channel, so Telegram won't say who joined."
    except TelegramError as exc:
        text = str(exc)
        s.channel_error = ("The Price Chat bot is not an administrator of the channel, so Telegram won't say who joined."
                           if "inaccessible" in text or "not enough rights" in text or "member list" in text else text[:300])
    db.commit()
    return status(db)


def status(db: Session) -> dict[str, Any]:
    s = program(db)
    discussion = db.scalar(select(TelegramSource).where(TelegramSource.chat_id == s.channel_linked_chat_id)) if s.channel_linked_chat_id else None
    return {
        "channel": channel_handle(db), "url": channel_url(db), "channel_id": s.channel_id, "title": s.channel_title,
        "bot_can_verify_members": s.channel_bot_can_verify, "checked_at": s.channel_checked_at.isoformat() if s.channel_checked_at else None,
        "error": s.channel_error, "discussion_chat_id": s.channel_linked_chat_id,
        "discussion_authorised": bool(discussion and discussion.enabled),
    }


# --- missions ----------------------------------------------------------------------------------------------------


def _group_link(db: Session, chat_id: int, message_id: int) -> str:
    src = db.scalar(select(TelegramSource).where(TelegramSource.chat_id == chat_id))
    if src is not None and src.username:
        return f"https://t.me/{src.username}/{message_id}"
    internal = str(chat_id).removeprefix("-100")
    return f"https://t.me/c/{internal}/{message_id}"  # opens the post in the group for its members


def generate(db: Session) -> int:
    """Create / refresh the channel and discussion missions from what is verifiable right now. Idempotent."""
    s = program(db)
    now = utcnow()
    created = 0
    # Join the channel: only while Telegram lets our bot check membership
    if s.channel_id:
        key = f"tg-join:{s.channel_id}"
        m = db.scalar(select(Mission).where(Mission.key == key))
        if s.channel_bot_can_verify and channel_url(db):
            if m is None:
                created += 1
            m = mx._upsert(db, dict(
                key=key, kind=JOIN_KIND, cadence="once", platform="telegram", xp=s.channel_join_xp, verification="verified",
                requires_platform="telegram", priority=88, title="Join Price Chat Updates",
                description="The official Price Chat announcement channel.",
                verification_method="Telegram confirms to the Price Chat bot that your linked Telegram account is in the channel.",
                target={"url": channel_url(db), "channel": channel_handle(db)}))
            if m.status == "paused" and m.created_by == "system":
                m.status = "active"
        elif m is not None and m.status == "active" and m.created_by == "system":
            m.status = "paused"  # can't be verified any more: not offered (never click-to-claim)
    # Join the discussion: the newest announcement forwarded into the authorised discussion group
    group = db.scalar(select(TelegramSource).where(TelegramSource.chat_id == s.channel_linked_chat_id, TelegramSource.enabled.is_(True))) \
        if s.channel_linked_chat_id else None
    if group is not None:
        post = db.scalar(select(TelegramMessage).where(
            TelegramMessage.chat_id == group.chat_id, TelegramMessage.author_id == TELEGRAM_SERVICE_USER,
            TelegramMessage.forward_from.is_not(None), TelegramMessage.sent_at >= now - timedelta(days=s.discussion_window_days))
            .order_by(TelegramMessage.sent_at.desc()))
        if post is not None:
            key = f"tg-discuss:{post.chat_id}:{post.message_id}"
            if db.scalar(select(Mission.id).where(Mission.key == key)) is None:
                created += 1
            m = mx._upsert(db, dict(
                key=key, kind=DISCUSSION_KIND, cadence="once", platform="telegram", xp=s.discussion_xp, verification="verified",
                requires_platform="telegram", priority=93, title="Join the discussion", description="Comment on the latest Price Chat update.",
                verification_method="A comment in your own words from your linked Telegram account under this update, seen by the Price Chat "
                                    "bot in the discussion group. Counted automatically.",
                target=discussion_target(db, post), starts_at=post.sent_at, expires_at=post.sent_at + timedelta(days=s.discussion_window_days)))
            if m.created_by == "system":
                m.target = discussion_target(db, post)  # e.g. a better link once the group's public username is known
            mx._one_active(db, DISCUSSION_KIND, m)
    db.flush()
    return created


def discussion_target(db: Session, post: TelegramMessage) -> dict[str, Any]:
    return {"url": _group_link(db, post.chat_id, post.message_id), "chat_id": post.chat_id, "message_id": post.message_id,
            "text": (post.text or "")[:280], "posted_at": post.sent_at.isoformat() if post.sent_at else None}


# --- verification ------------------------------------------------------------------------------------------------


def _telegram_id(db: Session, member: Member) -> LinkedIdentity | None:
    return db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == member.id, LinkedIdentity.platform == "telegram",
                                                  LinkedIdentity.status == "active"))


async def verify_join(db: Session, member: Member, mission: Mission, client: Any) -> dict[str, Any]:
    """The member pressed Verify: ask Telegram whether their linked account is in the channel. XP only on a yes."""
    from app.telegram.errors import TelegramError

    if mission.kind != JOIN_KIND or mission.status != "active":
        return {"ok": False, "code": "unavailable", "message": "This mission isn't available right now."}
    ident = _telegram_id(db, member)
    if ident is None:
        return {"ok": False, "code": "link_telegram", "message": "Connect Telegram first, so we know which account to check."}
    s = program(db)
    if not (s.channel_bot_can_verify and s.channel_id):
        return {"ok": False, "code": "unverifiable", "message": "We can't check channel membership right now, so this can't be completed yet."}
    period = mx.period_for(mission.cadence)
    mm = mx._row(db, member, mission, period)
    if mm.status == "completed":
        return {"ok": True, "code": "done", "message": "Already done.", "xp": mm.xp_awarded}
    try:
        info = await client.get_chat_member(s.channel_id, int(ident.platform_user_id))
    except TelegramError as exc:
        logger.warning("Channel membership check failed: %s", exc)
        return {"ok": False, "code": "unavailable", "message": "Telegram didn't answer. Please try again in a moment."}
    state = info.get("status")
    joined = state in MEMBER_STATUSES or (state == "restricted" and info.get("is_member"))
    if not joined:
        db.commit()
        return {"ok": False, "code": "not_joined", "message": "We don't see your Telegram account in the channel yet. Join it, then press Verify."}
    paid = mx._reward(db, member, mission, period, mm, reason="Joined Price Chat Updates on Telegram")
    db.commit()
    if not paid and mm.status != "completed":
        return {"ok": False, "code": "limit", "message": "Verified, but you've reached today's XP limit. Try again tomorrow."}
    return {"ok": True, "code": "verified", "message": "Verified", "xp": mm.xp_awarded}


def _root(db: Session, msg: TelegramMessage) -> int | None:
    """The message a comment ultimately replies to (walking up the reply chain in the ingested data)."""
    cur, seen = msg, 0
    while cur.reply_to_message_id and seen < MAX_THREAD_DEPTH:
        parent = db.scalar(select(TelegramMessage).where(TelegramMessage.chat_id == cur.chat_id,
                                                        TelegramMessage.message_id == cur.reply_to_message_id))
        if parent is None:
            return cur.reply_to_message_id
        cur, seen = parent, seen + 1
    return cur.message_id if cur is not msg else None


def _paid_discussions_today(db: Session, member: Member) -> int:
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(select(func.count()).select_from(MemberMission).join(Mission, Mission.id == MemberMission.mission_id).where(
        MemberMission.member_id == member.id, Mission.kind == DISCUSSION_KIND, MemberMission.status == "completed",
        MemberMission.completed_at >= today)) or 0


def check_discussion(db: Session, member: Member) -> int:
    """Complete discussion missions for genuine comments already ingested. Returns how many completed now."""
    ident = _telegram_id(db, member)
    if ident is None:
        return 0
    uid, limit, done = int(ident.platform_user_id), program(db).discussion_daily_limit, 0
    for mission in db.scalars(select(Mission).where(Mission.kind == DISCUSSION_KIND, Mission.status == "active")):
        t = mission.target or {}
        chat_id, root_id = t.get("chat_id"), t.get("message_id")
        if not chat_id or not root_id:
            continue
        period = mx.period_for(mission.cadence)
        mm = db.scalar(select(MemberMission).where(MemberMission.member_id == member.id, MemberMission.mission_id == mission.id,
                                                   MemberMission.period == period))
        if mm is not None and mm.status == "completed":
            continue
        if _paid_discussions_today(db, member) >= limit:
            break
        since = max(mission.starts_at or mission.created_at, ident.verified_at)
        mine = list(db.scalars(select(TelegramMessage).where(
            TelegramMessage.chat_id == chat_id, TelegramMessage.author_id == uid, TelegramMessage.author_is_bot.is_(False),
            TelegramMessage.sent_at >= since).order_by(TelegramMessage.sent_at)))
        for msg in mine:
            if _root(db, msg) != root_id or not meaningful(msg.text or "", msg.forward_from):
                continue
            text = _norm(msg.text)
            # copied: the same words as another comment under this update, or as the member's own recent messages
            others = db.scalars(select(TelegramMessage.text).where(
                TelegramMessage.chat_id == chat_id, TelegramMessage.id != msg.id,
                TelegramMessage.sent_at >= msg.sent_at - COPY_LOOKBACK, TelegramMessage.sent_at <= msg.sent_at))
            if any(_norm(o) == text for o in others):
                continue
            row = mm or mx._row(db, member, mission, period)
            if mx._reward(db, member, mission, period, row, reason="Joined the discussion on a Price Chat update", occurred_at=msg.sent_at):
                done += 1
            break
    db.flush()
    return done

