"""Topic merge suggestions, merging and splitting.

Merges are only ever *suggested* automatically; a human clicks Merge or Keep
separate. A merged topic is kept as an alias of its canonical topic (status
MERGED, canonical_id set), so nothing is silently lost.
"""

import logging
from dataclasses import dataclass
from itertools import combinations

import numpy as np
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import Session, aliased

from app.ai.routing import ModelRouter
from app.ai.structured import complete_structured
from app.community.formatting import format_messages
from app.community.topics import refresh_topic_stats
from app.community.tuning import CommunityTuning
from app.community.vectors import from_blob, load_vectors, normalise, to_blob
from app.db.base import utcnow
from app.models.community import CommunityQuestion, CommunityTopic
from app.models.enums import ActivityLevel, MergeStatus, TopicStatus
from app.models.social_intel import CrossSignal, TopicMergeSuggestion
from app.models.telegram import TelegramMessage
from app.services.activity import ActivityLogger

logger = logging.getLogger(__name__)

# Candidate thresholds (nomic-embed-text): centroid similarity alone, or with a shared keyword.
CANDIDATE_SIMILARITY = 0.80
CANDIDATE_WITH_KEYWORD = 0.65


class _Verdict(BaseModel):
    verdict: str = Field(description="'same' if both are the same discussion topic, 'related' if distinct but connected, 'different' otherwise")
    reason: str = Field(description="One sentence explaining the decision")


JUDGE_SYSTEM = (
    "You compare two discussion topics detected in a community chat. Decide whether they are the SAME topic "
    "(e.g. 'BTC' and 'Bitcoin price'), RELATED but distinct (e.g. 'Bitcoin price' and 'Bitcoin ETF flows'), or "
    "DIFFERENT. Be conservative: only say 'same' when a reader would clearly treat them as one subject. Return JSON only."
)


@dataclass(slots=True)
class MergeReport:
    candidates: int = 0
    suggested: int = 0


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _shared(db: Session, a: int, b: int) -> tuple[int, int]:
    M = TelegramMessage
    authors_a = set(db.scalars(select(M.author_id).where(M.topic_id == a, M.author_id.is_not(None))))
    authors_b = set(db.scalars(select(M.author_id).where(M.topic_id == b, M.author_id.is_not(None))))
    parent = aliased(TelegramMessage)
    threads = db.scalar(
        select(func.count())
        .select_from(M)
        .join(parent, and_(parent.chat_id == M.chat_id, parent.message_id == M.reply_to_message_id))
        .where(((M.topic_id == a) & (parent.topic_id == b)) | ((M.topic_id == b) & (parent.topic_id == a)))
    )
    return len(authors_a & authors_b), int(threads or 0)


async def suggest_merges(db: Session, router: ModelRouter, limit: int = 8) -> MergeReport:
    report = MergeReport()
    topics = [
        t for t in db.scalars(select(CommunityTopic).where(CommunityTopic.status == TopicStatus.ACTIVE))
        if t.centroid is not None
    ]
    known = {_pair(s.topic_a_id, s.topic_b_id) for s in db.scalars(select(TopicMergeSuggestion))}
    candidates: list[tuple[float, CommunityTopic, CommunityTopic]] = []
    for a, b in combinations(topics, 2):
        if _pair(a.id, b.id) in known or b.id in (a.related_ids or []):
            continue
        va, vb = from_blob(a.centroid), from_blob(b.centroid)  # type: ignore[arg-type]
        if va.shape != vb.shape:
            continue
        sim = float(va @ vb)
        shared_kw = set(a.keywords or []) & set(b.keywords or [])
        if sim >= CANDIDATE_SIMILARITY or (shared_kw and sim >= CANDIDATE_WITH_KEYWORD):
            candidates.append((sim, a, b))
    candidates.sort(key=lambda c: c[0], reverse=True)
    report.candidates = len(candidates)

    for sim, a, b in candidates[:limit]:
        shared_people, shared_threads = _shared(db, a.id, b.id)
        msgs_a = list(db.scalars(select(TelegramMessage).where(TelegramMessage.topic_id == a.id).limit(6)))
        msgs_b = list(db.scalars(select(TelegramMessage).where(TelegramMessage.topic_id == b.id).limit(6)))
        text_a, _ = format_messages(msgs_a, 1500, 200)
        text_b, _ = format_messages(msgs_b, 1500, 200)
        user = (
            f"Topic A: {a.name}. {a.description}\nExamples:\n{text_a}\n\n"
            f"Topic B: {b.name}. {b.description}\nExamples:\n{text_b}\n\n"
            f"Meaning similarity: {sim:.2f}; shared participants: {shared_people}; reply threads across them: {shared_threads}"
        )
        out, result, route = await complete_structured(router, "topic.merge_judge", JUDGE_SYSTEM, user, _Verdict)
        verdict = out.verdict.strip().lower()
        verdict = verdict if verdict in ("same", "related", "different") else "related"
        lo, hi = _pair(a.id, b.id)
        reason = (
            f"Semantic similarity {sim:.2f}"
            + (f" + {shared_people} shared participants" if shared_people else "")
            + (f" + {shared_threads} reply threads between them" if shared_threads else "")
            + f". {out.reason.strip()}"
        )
        db.add(
            TopicMergeSuggestion(
                topic_a_id=lo, topic_b_id=hi, similarity=round(sim, 3), shared_participants=shared_people,
                shared_threads=shared_threads, verdict=verdict, reason=reason[:2000], judged_by=result.model or route.model,
                # "different" is recorded so the pair is not re-judged, but never shown as a suggestion.
                status=MergeStatus.KEPT_SEPARATE if verdict == "different" else MergeStatus.PENDING,
            )
        )
        if verdict != "different":
            report.suggested += 1
    db.commit()
    return report


def recompute_centroid(db: Session, topic: CommunityTopic) -> None:
    if topic.embedding_model is None:
        return
    ids = list(db.scalars(select(TelegramMessage.id).where(TelegramMessage.topic_id == topic.id)))
    vecs = list(load_vectors(db, ids, topic.embedding_model).values())
    if vecs:
        topic.centroid = to_blob(normalise(np.mean(np.vstack(vecs), axis=0)))


def merge_topics(db: Session, tuning: CommunityTuning, keep_id: int, merge_id: int, actor: str = "dashboard") -> CommunityTopic:
    if keep_id == merge_id:
        raise ValueError("Cannot merge a topic into itself")
    keep, gone = db.get(CommunityTopic, keep_id), db.get(CommunityTopic, merge_id)
    if keep is None or gone is None:
        raise LookupError("Topic not found")
    if TopicStatus.MERGED in (keep.status, gone.status):
        raise ValueError("One of these topics has already been merged")

    db.execute(update(TelegramMessage).where(TelegramMessage.topic_id == gone.id).values(topic_id=keep.id))
    db.execute(update(CommunityQuestion).where(CommunityQuestion.topic_id == gone.id).values(topic_id=keep.id))
    db.execute(update(CommunityTopic).where(CommunityTopic.canonical_id == gone.id).values(canonical_id=keep.id))
    keep.aliases = sorted({*(keep.aliases or []), gone.name, *(gone.aliases or [])} - {keep.name})
    keep.keywords = list(dict.fromkeys([*(keep.keywords or []), *(gone.keywords or [])]))[:10]
    keep.related_ids = sorted(set(keep.related_ids or []) | set(gone.related_ids or []) - {keep.id, gone.id})
    gone.status, gone.canonical_id = TopicStatus.MERGED, keep.id
    keep.needs_naming = True
    for s in db.scalars(select(CrossSignal).where(CrossSignal.telegram_topic_id == gone.id)):
        db.delete(s)  # recomputed for the canonical topic on the next run
    for s in db.scalars(select(TopicMergeSuggestion).where(
        TopicMergeSuggestion.status == MergeStatus.PENDING,
        ((TopicMergeSuggestion.topic_a_id == gone.id) | (TopicMergeSuggestion.topic_b_id == gone.id)),
    )):
        s.status = MergeStatus.MERGED if {s.topic_a_id, s.topic_b_id} == {keep.id, gone.id} else MergeStatus.KEPT_SEPARATE
        s.decided_at = utcnow()
    db.flush()
    recompute_centroid(db, keep)
    refresh_topic_stats(db, tuning)
    db.commit()
    ActivityLogger(db).log(
        "topic_merged", f'Merged topic "{gone.name}" into "{keep.name}" (kept as an alias)',
        level=ActivityLevel.AUDIT, actor=actor, details={"kept": keep.id, "merged": gone.id},
    )
    return keep


def keep_separate(db: Session, suggestion: TopicMergeSuggestion, actor: str = "dashboard") -> None:
    a, b = db.get(CommunityTopic, suggestion.topic_a_id), db.get(CommunityTopic, suggestion.topic_b_id)
    suggestion.status, suggestion.decided_at = MergeStatus.KEPT_SEPARATE, utcnow()
    if a is not None and b is not None:
        a.related_ids = sorted(set(a.related_ids or []) | {b.id})
        b.related_ids = sorted(set(b.related_ids or []) | {a.id})
    db.commit()
    ActivityLogger(db).log(
        "topic_kept_separate", f'Kept "{a.name if a else "?"}" and "{b.name if b else "?"}" as separate, related topics',
        level=ActivityLevel.AUDIT, actor=actor,
    )


def split_topic(db: Session, tuning: CommunityTuning, topic_id: int, message_ids: list[int], name: str, actor: str = "dashboard") -> CommunityTopic:
    source = db.get(CommunityTopic, topic_id)
    if source is None:
        raise LookupError("Topic not found")
    moving = list(db.scalars(select(TelegramMessage).where(TelegramMessage.id.in_(message_ids), TelegramMessage.topic_id == topic_id)))
    if len(moving) != len(set(message_ids)):
        raise ValueError("Every selected message must belong to this topic")
    remaining = int(db.scalar(select(func.count()).select_from(TelegramMessage).where(TelegramMessage.topic_id == topic_id)) or 0) - len(moving)
    if remaining <= 0:
        raise ValueError("Leave at least one message in the original topic (rename it instead)")
    new = CommunityTopic(
        name=name, description="", keywords=[], status=TopicStatus.CANDIDATE, embedding_model=source.embedding_model,
        first_seen_at=min(m.sent_at for m in moving), last_seen_at=max(m.sent_at for m in moving),
        message_count=len(moving), needs_naming=False, related_ids=[source.id], review_status="approved",
    )
    db.add(new)
    db.flush()
    for m in moving:
        m.topic_id = new.id
    source.related_ids = sorted(set(source.related_ids or []) | {new.id})
    db.flush()
    recompute_centroid(db, new)
    recompute_centroid(db, source)
    refresh_topic_stats(db, tuning)
    db.commit()
    ActivityLogger(db).log(
        "topic_split", f'Split {len(moving)} messages from "{source.name}" into new topic "{name}"',
        level=ActivityLevel.AUDIT, actor=actor, details={"from": source.id, "to": new.id},
    )
    return new
