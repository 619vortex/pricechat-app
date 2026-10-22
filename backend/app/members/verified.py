"""Verified platform XP (Telegram, X), computed from data Price Chat already reads.

Only members who linked the platform by signing in count, only activity after they linked it, and only from
sources Price Chat is allowed to read (authorised Telegram chats; posts the X integration ingested). Nothing
here calls a platform API. Telegram pays for the first meaningful message of the day and a little for further
real discussion, within daily caps; X pays per real post that replies to or mentions Price Chat.
"""

import re
from collections import defaultdict
from collections.abc import Callable
from functools import wraps
from datetime import datetime, timedelta
from typing import Any, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import xp as ledger
from app.models.enums import Platform
from app.models.members import LinkedIdentity, Member, ReputationEvent
from app.models.social import SocialPost
from app.models.social_intel import SocialProfile
from app.models.telegram import TelegramMessage, TelegramSource

LOOKBACK = timedelta(days=14)
MIN_CHARS = 15  # a message / post must say something: "gm", "lol" or an emoji never count
CHAT_MIN_CHARS = 3  # the per-message XP ("telegram.message"): any real message, just not empty / a single emoji
REPEAT_FLAG_AT = 5  # the same text this many times in a day raises a flag for review
_MENTION_OR_LINK = re.compile(r"(@\w+|https?://\S+)")


_F = TypeVar("_F", bound=Callable[..., Any])


def _batched(fn: _F) -> _F:
    """Run a verification pass as one XP-ledger batch (rules, totals and daily amounts read once per member; past activity
    already over a daily cap is skipped instead of being refused again on every run for 14 days)."""
    @wraps(fn)
    def run(db: Session, *a: Any, **k: Any) -> Any:
        with ledger.batch(db):
            return fn(db, *a, **k)
    return run  # type: ignore[return-value]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def meaningful(text: str, forward_from: str | None = None) -> bool:
    """Explainable heuristic, no AI: long enough, own words (not a forward), not a bot command."""
    t = _norm(text or "")
    return len(t) >= MIN_CHARS and not t.startswith("/") and not forward_from


def _identities(db: Session, platform: str) -> list[tuple[LinkedIdentity, Member]]:
    rows = db.execute(select(LinkedIdentity, Member).join(Member, Member.id == LinkedIdentity.member_id).where(
        LinkedIdentity.platform == platform, LinkedIdentity.status == "active", Member.status == "active"))
    return [(i, m) for i, m in rows]


def telegram_messages(db: Session, ident: LinkedIdentity, since: datetime) -> list[TelegramMessage]:
    """The member's own messages in enabled (authorised) Price Chat chats since `since`."""
    return list(db.scalars(select(TelegramMessage).join(TelegramSource, TelegramSource.id == TelegramMessage.source_id).where(
        TelegramSource.enabled.is_(True), TelegramMessage.author_id == int(ident.platform_user_id),
        TelegramMessage.author_is_bot.is_(False), TelegramMessage.sent_at >= max(since, ident.verified_at))
        .order_by(TelegramMessage.sent_at)))


def daily_breakdown(msgs: list[TelegramMessage]) -> dict[str, dict[str, Any]]:
    """Per UTC day: all messages, the meaningful distinct ones (in order), and the most repeated text."""
    days: dict[str, dict[str, Any]] = defaultdict(lambda: {"all": 0, "texts": defaultdict(int), "eligible": [], "each": []})
    for m in msgs:
        d = days[ledger.day_of(m.sent_at)]
        d["all"] += 1
        t = _norm(m.text or "")
        d["texts"][t] += 1
        if meaningful(m.text, m.forward_from) and d["texts"][t] == 1:
            d["eligible"].append(m)  # distinct own words only: repeats never add up
        if d["texts"][t] == 1 and len(t) >= CHAT_MIN_CHARS and not t.startswith("/") and not m.forward_from:
            d["each"].append(m)  # every distinct message of your own: +1 XP each (telegram.message, 50 a day)
    return days


def _paid_refs(db: Session, member: Member, event_types: tuple[str, ...], since: datetime) -> set[tuple[str, str]]:
    """(event type, source ref) already in the member's ledger since `since`: the job re-reads LOOKBACK days of activity
    every run, and what was paid is skipped here instead of being sent to the ledger (and refused) again."""
    return {(t, r) for t, r in db.execute(select(ReputationEvent.event_type, ReputationEvent.source_ref).where(
        ReputationEvent.member_id == member.id, ReputationEvent.event_type.in_(event_types),
        ReputationEvent.day >= ledger.day_of(since)))}


@_batched
def telegram_participation(db: Session) -> dict[str, int]:
    """telegram.first_participation (+3, once a day) and telegram.discussion (+1 per further real message, capped)."""
    out = {"members": 0, "awarded": 0, "flags": 0}
    since = utcnow() - LOOKBACK
    for ident, member in _identities(db, "telegram"):
        out["members"] += 1
        paid = _paid_refs(db, member, ("telegram.active_day", "telegram.first_participation", "telegram.discussion"), since)
        for day, info in daily_breakdown(telegram_messages(db, ident, since)).items():
            repeats = max(info["texts"].values(), default=0)
            if repeats >= REPEAT_FLAG_AT:
                if ledger.raise_flag(db, member, "repeated_messages", f"Sent the same Telegram message {repeats} times on {day}",
                                     {"day": day, "count": repeats}):
                    out["flags"] += 1
                continue  # a spamming day earns nothing
            if len(info["eligible"]) >= 3 and ("telegram.active_day", f"tg-day:{day}") not in paid \
                    and not ledger.known_capped(db, member, "telegram.active_day", info["eligible"][2].sent_at):  # an active community day (+2)
                r = ledger.award(db, member, "telegram.active_day", f"tg-day:{day}", occurred_at=info["eligible"][2].sent_at,
                                 reason=f"Active community day on {day}", commit=False)
                out["awarded"] += int(r.awarded)
            for i, m in enumerate(info["eligible"]):
                if i == 0 and ("telegram.first_participation", f"tg-first:{day}") in paid:
                    continue
                if i > 0 and ("telegram.discussion", f"tg-msg:{m.chat_id}:{m.message_id}") in paid:
                    continue
                if ledger.known_capped(db, member, "telegram.first_participation" if i == 0 else "telegram.discussion", m.sent_at):
                    continue
                if i == 0:
                    r = ledger.award(db, member, "telegram.first_participation", f"tg-first:{day}", occurred_at=m.sent_at,
                                     reason=f"Took part in the Price Chat Telegram on {day}", commit=False)
                else:
                    r = ledger.award(db, member, "telegram.discussion", f"tg-msg:{m.chat_id}:{m.message_id}", occurred_at=m.sent_at,
                                     reason="Kept the Price Chat Telegram discussion going", commit=False)
                out["awarded"] += int(r.awarded)
        from app.members.missions import sync_event_missions
        from app.members.telegram_missions import check_discussion

        sync_event_missions(db, member)
        out["discussions"] = out.get("discussions", 0) + check_discussion(db, member)
    db.commit()
    return out


@_batched
def telegram_message_xp(db: Session) -> dict[str, int]:
    """telegram.message: +1 XP for every distinct message of your own in an authorised Price Chat chat, up to 50 a day,
    5 seconds apart (the rule's daily cap and cooldown). Its own ledger label ("tgchat"), so neither the Telegram cap nor the
    overall automatic cap squeezes it, and a spamming day (the same text REPEAT_FLAG_AT times) still earns nothing."""
    from app.members.catalog import ensure_catalog

    ensure_catalog(db)  # the rule exists before the first message pays
    out = {"members": 0, "awarded": 0}
    since = utcnow() - LOOKBACK
    for ident, member in _identities(db, "telegram"):
        out["members"] += 1
        paid = _paid_refs(db, member, ("telegram.message",), since)
        for info in daily_breakdown(telegram_messages(db, ident, since)).values():
            if max(info["texts"].values(), default=0) >= REPEAT_FLAG_AT:
                continue
            for m in info["each"]:
                if ("telegram.message", f"tg-each:{m.chat_id}:{m.message_id}") in paid \
                        or ledger.known_capped(db, member, "telegram.message", m.sent_at):
                    continue
                r = ledger.award(db, member, "telegram.message", f"tg-each:{m.chat_id}:{m.message_id}", occurred_at=m.sent_at,
                                 reason="Sent a message in the Price Chat Telegram", source="tgchat", commit=False)
                out["awarded"] += int(r.awarded)
    db.commit()
    return out


# kept for the scheduled job name used before Telegram XP v2
telegram_active_days = telegram_participation


def replied_to_ids(post: SocialPost) -> set[str]:
    """The post ids this post replies to / quotes (X gives the replied-to USER in in_reply_to_external_id)."""
    return {str(r.get("id")) for r in (post.referenced or []) if r.get("type") in ("replied_to", "quoted") and r.get("id")}


@_batched
def x_interactions(db: Session) -> dict[str, int]:
    """Replies to Price Chat posts / mentions of Price Chat by linked X accounts, from posts already ingested.
    A reply that answers an active mission completes that mission instead of paying the general reply XP."""
    from app.members.missions import complete_x_reply_mission

    out = {"members": 0, "awarded": 0, "missions": 0}
    own_ids = set(db.scalars(select(SocialPost.external_id).where(SocialPost.platform == Platform.X, SocialPost.is_own.is_(True))))
    self_ids = set(db.scalars(select(SocialProfile.external_id).where(SocialProfile.platform == Platform.X, SocialProfile.is_self.is_(True))))
    for ident, member in _identities(db, "x"):
        out["members"] += 1
        profile = db.scalar(select(SocialProfile).where(SocialProfile.platform == Platform.X,
                                                        SocialProfile.external_id == ident.platform_user_id))
        if profile is None or profile.is_self:
            continue
        since = max(ident.verified_at, utcnow() - LOOKBACK)
        paid = _paid_refs(db, member, ("x.reply_pricechat", "x.mention_pricechat"), since)
        for p in db.scalars(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.author_profile_id == profile.id,
                                                    SocialPost.is_own.is_(False),
                                                    func.coalesce(SocialPost.posted_at, SocialPost.created_at) >= since)):
            when = p.posted_at or p.created_at
            if when < since or not p.external_id:
                continue
            if len(_MENTION_OR_LINK.sub("", p.text or "").strip()) < MIN_CHARS:
                continue  # bare mentions / links earn nothing
            targets = replied_to_ids(p) & own_ids
            if targets or (p.in_reply_to_external_id and p.in_reply_to_external_id in self_ids):
                outcome = complete_x_reply_mission(db, member, targets, p, when) if targets else None
                if outcome:
                    out["missions"] += int(outcome == "new")
                    continue  # the mission pays for this reply (or already did); never the general reply XP too
                kind, reason = "x.reply_pricechat", "Replied to Price Chat on X"
            elif "mention" in (p.source_kinds or []):
                kind, reason = "x.mention_pricechat", "Mentioned Price Chat on X"
            else:
                continue
            if (kind, f"x-post:{p.external_id}") in paid or ledger.known_capped(db, member, kind, when):
                continue
            r = ledger.award(db, member, kind, f"x-post:{p.external_id}", occurred_at=when, reason=reason, commit=False)
            out["awarded"] += int(r.awarded)
    db.commit()
    return out
