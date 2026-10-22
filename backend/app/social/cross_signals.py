"""Cross-platform signals: the same topic active in the Telegram community AND on X.

A signal is informational: it says two audiences are discussing something
similar at the same time. It does not claim one caused the other.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.routing import ModelRouter
from app.ai.structured import complete_structured
from app.community.formatting import strip_citations
from app.community.vectors import from_blob
from app.db.base import utcnow
from app.models.community import CommunityTopic
from app.models.enums import CrossSignalStatus, Platform, TopicStatus
from app.models.social import SocialPost
from app.models.social_intel import CrossSignal, MonitorRule, SocialPostVector, WatchedAccount
from app.providers.errors import ProviderError

logger = logging.getLogger(__name__)

MATCH_SIMILARITY = 0.68  # X post vs Telegram topic centroid (document-document, nomic-embed-text)
MIN_POSTS = 2
WINDOW = timedelta(hours=72)
STALE_AFTER = timedelta(hours=48)


class _Synthesis(BaseModel):
    recommendation: str = Field(description="1-2 sentences: what the overlap suggests and a cautious next step")


SYNTH_SYSTEM = (
    "A topic is being discussed in a brand's Telegram community and also on X. Write a short, cautious recommendation "
    "for the brand's content team. State it as a signal worth considering, not proof of anything. Activity in both "
    "places shows people are talking about the topic; it does not show that a market event is happening or that one "
    "platform caused the other. Do not predict prices. Return JSON only."
)


def _trend(c24: int, p24: int) -> str:
    if c24 + p24 < 4:
        return "insufficient_data"
    if c24 >= max(2 * p24, p24 + 3):
        return "rising"
    if c24 * 2 <= p24:
        return "falling"
    return "steady"


def _engagement(p: SocialPost) -> int | None:
    m = p.metrics or {}
    vals = [m.get(k) for k in ("likes", "replies", "reposts", "quotes") if m.get(k) is not None]
    return sum(int(v) for v in vals) if vals else None


@dataclass(slots=True)
class CrossReport:
    topics_checked: int = 0
    active: int = 0
    new: int = 0
    stale: int = 0


EARLY_LEAD = timedelta(minutes=60)


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _early(tg_first: datetime | None, x_times: list[datetime | None], monitoring_since: datetime | None, now: datetime) -> tuple[bool, int | None]:
    """EARLY COMMUNITY SIGNAL: Telegram discussed it before monitored X sources did.

    Only claimed when X was already being monitored at the time (otherwise X may simply have been talking
    about it before Price Chat looked), and only for recent Telegram discussion. A social signal, not a forecast."""
    xs = [_aware(t) for t in x_times if t is not None]
    tg, mon = _aware(tg_first), _aware(monitoring_since)
    if not xs or tg is None or mon is None or tg < now - WINDOW or mon > tg:
        return False, None
    lead = min(xs) - tg  # type: ignore[operator]
    return (lead >= EARLY_LEAD, int(lead.total_seconds() // 60) if lead >= EARLY_LEAD else None)


async def detect_cross_signals(db: Session, router: ModelRouter | None, embedding_model: str | None) -> CrossReport:
    report = CrossReport()
    now = utcnow()
    if embedding_model is None:
        return report
    rows = db.execute(
        select(SocialPost, SocialPostVector.vector)
        .join(SocialPostVector, SocialPostVector.post_id == SocialPost.id)
        .where(SocialPost.platform == Platform.X, SocialPost.is_own.is_(False), SocialPostVector.model == embedding_model,
               (SocialPost.posted_at.is_(None)) | (SocialPost.posted_at >= now - WINDOW))
    ).all()
    posts = [(p, from_blob(v)) for p, v in rows]
    topics = [t for t in db.scalars(select(CommunityTopic).where(CommunityTopic.status == TopicStatus.ACTIVE, CommunityTopic.review_status != "rejected")) if t.centroid]
    existing = {s.telegram_topic_id: s for s in db.scalars(select(CrossSignal))}
    # When did Price Chat start watching X? An "early" claim is only fair if X was being monitored already.
    monitoring_since = min([r.created_at for r in db.scalars(select(MonitorRule).where(MonitorRule.platform == Platform.X))]
                           + [w.created_at for w in db.scalars(select(WatchedAccount))], default=None)

    for t in topics:
        report.topics_checked += 1
        tv = from_blob(t.centroid)  # type: ignore[arg-type]
        matched = [(float(tv @ v), p) for p, v in posts if v.shape == tv.shape and float(tv @ v) >= MATCH_SIMILARITY]
        if len(matched) < MIN_POSTS:
            continue
        day, two = now - timedelta(hours=24), now - timedelta(hours=48)
        in24 = [p for _, p in matched if p.posted_at and p.posted_at >= day]
        prev = [p for _, p in matched if p.posted_at and two <= p.posted_at < day]
        eng24 = [e for e in (_engagement(p) for p in in24) if e is not None]
        engprev = [e for e in (_engagement(p) for p in prev) if e is not None]
        sig = existing.get(t.id)
        is_new = sig is None
        if sig is None:
            sig = CrossSignal(telegram_topic_id=t.id, first_detected_at=now, topic_name=t.name, last_updated_at=now)
            db.add(sig)
        if sig.status == CrossSignalStatus.DISMISSED:
            continue
        sig.topic_name = t.name
        sig.similarity = round(max(s for s, _ in matched), 3)
        sig.telegram_messages_24h, sig.telegram_messages_total, sig.telegram_participants = t.count_24h, t.message_count, t.participant_count
        sig.telegram_trend = t.trend.value
        sig.x_posts, sig.x_posts_24h = len(matched), len(in24)
        sig.x_engagement_24h = sum(eng24) if eng24 else None
        sig.x_engagement_prev_24h = sum(engprev) if engprev else None
        sig.x_trend = _trend(len(in24), len(prev))
        sig.x_post_ids = [p.id for _, p in sorted(matched, key=lambda m: m[0], reverse=True)[:20]]
        sig.early_signal, sig.lead_minutes = _early(t.first_seen_at, [p.posted_at for _, p in matched], monitoring_since, now)
        sig.status, sig.last_updated_at = CrossSignalStatus.ACTIVE, now
        default = (
            f"'{t.name}' is being discussed in your Telegram community ({t.count_24h} messages in 24h, {t.participant_count} people) "
            f"and in {len(matched)} monitored X posts. This is a signal worth considering for content, not proof of a trend."
        )
        if is_new and router is not None:
            try:
                out, _, _ = await complete_structured(
                    router, "cross.synthesis", SYNTH_SYSTEM,
                    f"Topic: {t.name}\n{t.description}\nTelegram: {t.count_24h} messages/24h, {t.participant_count} people, trend {t.trend.value}\n"
                    f"X: {len(matched)} monitored posts ({len(in24)} in 24h), X trend {sig.x_trend}\nExamples from X:\n"
                    + "\n".join(f"- {' '.join(p.text.split())[:200]}" for _, p in matched[:5]),
                    _Synthesis,
                )
                sig.recommendation = strip_citations(out.recommendation) or default
            except ProviderError as exc:
                logger.info("Cross-signal synthesis unavailable: %s", exc)
                sig.recommendation = default
        elif not sig.recommendation:
            sig.recommendation = default
        report.active += 1
        report.new += is_new

    for sig in existing.values():
        if sig.status == CrossSignalStatus.ACTIVE and sig.last_updated_at < now - STALE_AFTER:
            sig.status = CrossSignalStatus.STALE
            report.stale += 1
    db.commit()
    return report
