"""Topic detection: incremental meaning-based clustering plus honest trend statistics.

Each substantive message joins the most similar existing topic (cosine similarity
to the topic centroid ≥ threshold) or seeds a new *candidate* topic. Candidates
become *active* only once enough messages from enough different people support
them. Trends are only reported when there is enough data to compare two periods.
"""

import logging
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

import numpy as np
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.ai.routing import ModelRouter
from app.ai.structured import complete_structured
from app.community.formatting import format_messages
from app.community.tuning import CommunityTuning
from app.community.vectors import from_blob, load_vectors, normalise, to_blob
from app.db.base import utcnow
from app.memory.retrieval.lexical import STOPWORDS
from app.models.community import CommunityTopic
from app.models.enums import MessageCategory, TopicStatus, TopicTrend
from app.models.telegram import TelegramMessage

logger = logging.getLogger(__name__)

NAMING_MILESTONES = (3, 10, 30, 100, 300)
SKIP_CATEGORIES = {MessageCategory.NOISE, MessageCategory.CHATTER}
_WORD = re.compile(r"[a-z][a-z0-9'\-]{2,}")


def eligible_for_topic(m: TelegramMessage) -> bool:
    return not m.author_is_bot and m.category not in SKIP_CATEGORIES and len(m.text.strip()) >= 15


def top_keywords(texts: list[str], n: int = 4) -> list[str]:
    counts: Counter[str] = Counter()
    for t in texts:
        counts.update({w for w in _WORD.findall(t.lower()) if w not in STOPWORDS and len(w) > 3})
    return [w for w, _ in counts.most_common(n)]


class TopicAssigner:
    """Assigns messages to topics within one processing batch."""

    def __init__(self, db: Session, embedding_model: str, tuning: CommunityTuning) -> None:
        self.db = db
        self.model = embedding_model
        self.tuning = tuning
        self.topics: list[CommunityTopic] = list(
            db.scalars(
                select(CommunityTopic).where(
                    CommunityTopic.status.not_in((TopicStatus.ARCHIVED, TopicStatus.MERGED)), CommunityTopic.embedding_model == embedding_model
                )
            )
        )
        self._centroids = [from_blob(t.centroid).copy() if t.centroid else None for t in self.topics]

    def assign(self, message: TelegramMessage, vec: np.ndarray) -> CommunityTopic:
        best, best_sim = -1, -1.0
        for i, c in enumerate(self._centroids):
            if c is None or c.shape != vec.shape:
                continue
            sim = float(c @ vec)
            if sim > best_sim:
                best, best_sim = i, sim

        if best >= 0 and best_sim >= self.tuning.topic_similarity:
            topic = self.topics[best]
            n = topic.message_count
            centroid = normalise(self._centroids[best] * n + vec)  # type: ignore[operator]
            self._centroids[best] = centroid
            topic.centroid = to_blob(centroid)
            topic.message_count = n + 1
            topic.last_seen_at = max(topic.last_seen_at, message.sent_at)
            topic.first_seen_at = min(topic.first_seen_at, message.sent_at)
            if topic.message_count in NAMING_MILESTONES:
                topic.needs_naming = True
        else:
            keywords = top_keywords([message.text])
            topic = CommunityTopic(
                name=" ".join(keywords[:3]).capitalize() or "New topic",
                description="",
                keywords=keywords,
                status=TopicStatus.CANDIDATE,
                centroid=to_blob(vec),
                embedding_model=self.model,
                first_seen_at=message.sent_at,
                last_seen_at=message.sent_at,
                message_count=1,
                needs_naming=True,
            )
            self.db.add(topic)
            self.db.flush()
            self.topics.append(topic)
            self._centroids.append(vec.copy())
        message.topic_id = topic.id
        return topic


def compute_trend(first_seen: datetime, now: datetime, c24: int, p24: int, total: int, min_messages: int) -> TopicTrend:
    if first_seen >= now - timedelta(hours=24) and total >= min_messages:
        return TopicTrend.NEW
    if c24 + p24 < 6:
        return TopicTrend.INSUFFICIENT_DATA  # too little activity to call a direction
    if c24 >= max(2 * p24, p24 + 5):
        return TopicTrend.RISING
    if c24 * 2 <= p24:
        return TopicTrend.FALLING
    return TopicTrend.STEADY


def refresh_topic_stats(db: Session, tuning: CommunityTuning, now: datetime | None = None) -> int:
    """Recompute counts, trend, status, importance and representatives for every live topic."""
    now = now or utcnow()
    day, two_days = now - timedelta(hours=24), now - timedelta(hours=48)
    M = TelegramMessage
    rows = db.execute(
        select(
            M.topic_id,
            func.count(M.id),
            func.count(func.distinct(M.author_id)),
            func.min(M.sent_at),
            func.max(M.sent_at),
            func.sum(case((M.sent_at >= day, 1), else_=0)),
            func.sum(case((and_(M.sent_at >= two_days, M.sent_at < day), 1), else_=0)),
        )
        .where(M.topic_id.is_not(None))
        .group_by(M.topic_id)
    ).all()
    stats = {r[0]: r[1:] for r in rows}

    updated = 0
    for topic in db.scalars(select(CommunityTopic).where(CommunityTopic.status.not_in((TopicStatus.ARCHIVED, TopicStatus.MERGED)))):
        count, participants, first, last, c24, p24 = stats.get(topic.id, (0, 0, None, None, 0, 0))
        if count == 0:
            db.delete(topic)  # all of its messages were deleted
            continue
        first, last = _aware(first), _aware(last)
        topic.message_count, topic.participant_count = int(count), int(participants)
        topic.first_seen_at, topic.last_seen_at = first, last
        topic.count_24h, topic.count_prev_24h = int(c24 or 0), int(p24 or 0)
        topic.trend = compute_trend(first, now, topic.count_24h, topic.count_prev_24h, topic.message_count, tuning.topic_min_messages)

        qualifies = topic.message_count >= tuning.topic_min_messages and topic.participant_count >= tuning.topic_min_participants
        if topic.status == TopicStatus.CANDIDATE and qualifies:
            topic.status = TopicStatus.ACTIVE
            topic.needs_naming = True
        if last < now - timedelta(days=tuning.topic_archive_days):
            topic.status = TopicStatus.ARCHIVED

        recent_volume = min(1.0, math.log1p(topic.count_24h) / math.log1p(50))
        breadth = min(1.0, topic.participant_count / 15)
        topic.importance = round(0.55 * recent_volume + 0.45 * breadth, 3)
        _representatives(db, topic)
        updated += 1
    db.flush()
    return updated


def _aware(value: datetime | str | None) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    assert value is not None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _representatives(db: Session, topic: CommunityTopic) -> None:
    """The messages closest to the topic's centre, and how tightly the topic hangs together."""
    if topic.centroid is None or topic.embedding_model is None:
        return
    ids = list(
        db.scalars(
            select(TelegramMessage.id)
            .where(TelegramMessage.topic_id == topic.id)
            .order_by(TelegramMessage.sent_at.desc())
            .limit(300)
        )
    )
    vectors = load_vectors(db, ids, topic.embedding_model)
    if not vectors:
        return
    centroid = from_blob(topic.centroid)
    sims = sorted(((float(v @ centroid), mid) for mid, v in vectors.items() if v.shape == centroid.shape), reverse=True)
    topic.representative_message_ids = [mid for _, mid in sims[:5]]
    cohesion = sum(s for s, _ in sims) / len(sims) if sims else 0.0
    topic.confidence = round(max(0.0, min(1.0, cohesion)) * min(1.0, topic.message_count / 10), 3)


class _TopicName(BaseModel):
    name: str = Field(description="2-6 word topic name, no quotes")
    description: str = Field(description="One or two sentences on what members are discussing")
    keywords: list[str] = Field(description="up to 6 short keywords")


NAMING_SYSTEM = (
    "You name discussion topics found in a community chat. Given representative messages, return a short, "
    "specific topic name, a neutral one-to-two sentence description of what members are discussing, and keywords. "
    "Describe; do not judge or add facts that are not in the messages. Return JSON only."
)


async def name_topics(db: Session, router: ModelRouter, limit: int = 10) -> int:
    topics = list(
        db.scalars(
            select(CommunityTopic)
            .where(CommunityTopic.status == TopicStatus.ACTIVE, CommunityTopic.needs_naming.is_(True))
            .order_by(CommunityTopic.importance.desc())
            .limit(limit)
        )
    )
    named = 0
    for topic in topics:
        ids = topic.representative_message_ids or []
        recent = list(
            db.scalars(
                select(TelegramMessage)
                .where(TelegramMessage.topic_id == topic.id)
                .order_by(TelegramMessage.sent_at.desc())
                .limit(8)
            )
        )
        reps = list(db.scalars(select(TelegramMessage).where(TelegramMessage.id.in_(ids)))) if ids else []
        messages = list({m.id: m for m in [*reps, *recent]}.values())
        text, _ = format_messages(messages, char_budget=5000, max_chars_each=300)
        parsed, _, route = await complete_structured(router, "telegram.topic_naming", NAMING_SYSTEM, f"Messages:\n{text}", _TopicName)
        topic.name = parsed.name.strip().strip('"')[:160] or topic.name
        topic.description = parsed.description.strip()[:1000]
        topic.keywords = [k.strip().lower()[:40] for k in parsed.keywords if k.strip()][:6]
        topic.needs_naming = False
        topic.named_by_model = route.model
        topic.named_at = utcnow()
        db.flush()
        named += 1
    return named
