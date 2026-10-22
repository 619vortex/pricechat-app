"""X growth opportunities: posts where Price Chat could genuinely add value.

Read-only and advisory. Two stages, both via model routing:
1. 'x.relevance' (fast tier) filters a batch of recent monitored posts.
2. 'x.opportunity' (strong tier) explains how Price Chat could contribute, using
   only approved knowledge, and suggests an action (reply, standalone post,
   quote candidate, research, ignore). Nothing is ever executed.
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.routing import ModelRouter
from app.ai.structured import StructuredOutputError, complete_structured, salvage_items, unit_score
from app.community.formatting import strip_citations
from app.community.vectors import from_blob
from app.db.base import utcnow
from app.learning.feedback import PreferenceModel, features_for
from app.memory.retrieval.base import MemoryRetriever, RetrievalQuery
from app.models.community import CommunityTopic
from app.models.enums import FeedbackTarget, Platform, SocialOpportunityKind, SocialOpportunityStatus, SuggestedAction, TopicStatus
from app.models.social import SocialPost
from app.models.social_intel import SocialOpportunity, SocialPostVector, SocialProfile, WatchedAccount
from app.social.budget import PlatformBudget
from app.services.brand_service import brand_data, get_brand

logger = logging.getLogger(__name__)

WINDOW = timedelta(hours=48)
SCREEN_LIMIT = 40  # posts considered per run
SCREEN_BATCH = 10  # posts per x.relevance call: keeps each answer far below the task's output limit
COMMUNITY_OVERLAP = 0.68  # post vs Telegram topic centroid (document-document, nomic-embed-text)


class _Item(BaseModel):
    id: str
    relevant: bool
    kind: SocialOpportunityKind
    topic: str = ""
    relevance: float = 0.0
    _s = field_validator("relevance", mode="before")(lambda v: unit_score(v))


class _Batch(BaseModel):
    items: list[_Item]


class _Advice(BaseModel):
    worthwhile: bool
    topic: str = Field(description="Short topic label")
    why: str = Field(description="Why Price Chat might contribute here")
    contribution: str = Field(description="What useful thing Price Chat could add (not a full reply)")
    suggested_action: SuggestedAction
    relevance: float = Field(description="0.0 to 1.0")
    like: bool = Field(False, description="Worth a like as a light acknowledgement (only if genuinely relevant)")
    repost: bool = Field(False, description="Worth reposting to Price Chat's audience (rare: only clearly valuable information)")
    follow_author: bool = Field(False, description="Worth following the author (they repeatedly post relevant, quality content)")
    engagement_reason: str = Field("", description="Why the like/repost/follow suggestion, if any")
    _s = field_validator("relevance", mode="before")(lambda v: unit_score(v))


FILTER_SYSTEM = (
    "You screen social media posts for a brand's social team. For each post decide whether it is relevant to the "
    "brand (its topics, audience or community) and what kind of opportunity it is: mention (mentions the brand), "
    "question (someone asks something the brand could answer), conversation (a discussion the brand could add to), "
    "growing_topic, knowledge_match, community_crossover, creator_discussion. Spam, giveaways and unrelated posts are "
    "not relevant. Return JSON only."
)
ADVICE_SYSTEM = (
    "You advise a brand's social team. Given one public post and the brand's knowledge, explain whether the brand "
    "could add genuine value and how. Rules: never suggest engagement bait, never invent facts, respect the brand's "
    "topics to avoid and restricted claims, prefer 'research' or 'ignore' when unsure. Suggest like/repost/follow sparingly: "
    "never to fill quotas, never mass engagement. You only suggest; a human decides. "
    "Return JSON only."
)


@dataclass(slots=True)
class XOpportunityReport:
    screened: int = 0
    unscreened: int = 0  # posts the model could not give a usable verdict for (skipped, not guessed)
    model_calls: int = 0
    salvaged: int = 0  # verdicts recovered from a cut-off or malformed answer
    advice_failed: int = 0
    relevant: int = 0
    created: int = 0
    set_aside: int = 0
    ids: list[int] = field(default_factory=list)


def _brief(db: Session, agent_id: int) -> str:
    b = brand_data(get_brand(db, agent_id))
    lines = [f"Brand: {b.brand_name}"]
    if b.description:
        lines.append(f"About: {b.description}")
    if b.topics_of_interest:
        lines.append("Topics of interest: " + "; ".join(b.topics_of_interest))
    if b.topics_to_avoid:
        lines.append("Topics to avoid: " + "; ".join(b.topics_to_avoid))
    if b.restricted_claims:
        lines.append("Never claim: " + "; ".join(b.restricted_claims))
    return "\n".join(lines)


def _post_line(p: SocialPost, profiles: dict[int, SocialProfile]) -> str:
    author = profiles.get(p.author_profile_id or -1)
    return f"[p{p.id}] @{author.username if author else 'unknown'} ({', '.join(p.source_kinds)}): {' '.join(p.text.split())[:400]}"


def _collect(items: list[_Item], wanted: set[int], verdicts: dict[int, _Item]) -> None:
    for item in items:
        try:
            pid = int(str(item.id).strip().lstrip("[pP").rstrip("]"))
        except ValueError:
            continue
        if pid in wanted:
            verdicts[pid] = item


async def screen_posts(router: ModelRouter, brief: str, posts: list[SocialPost], profiles: dict[int, SocialProfile],
                       report: XOpportunityReport) -> dict[int, _Item]:
    """Relevance verdicts in small batches.

    A cut-off or malformed answer keeps every complete verdict it contains; only the posts still
    missing are screened again, in halves, down to single posts. A post that still gets no usable
    verdict is left unscreened (never guessed). Model calls stay ~len(posts)/SCREEN_BATCH normally.
    """
    verdicts: dict[int, _Item] = {}
    queue = [posts[i:i + SCREEN_BATCH] for i in range(0, len(posts), SCREEN_BATCH)]
    while queue:
        chunk = queue.pop(0)
        wanted = {p.id for p in chunk}
        user = f"{brief}\n\nPosts:\n" + "\n".join(_post_line(p, profiles) for p in chunk)
        report.model_calls += 1
        try:
            batch, _, _ = await complete_structured(router, "x.relevance", FILTER_SYSTEM, user, _Batch, retries=0)
            _collect(batch.items, wanted, verdicts)
        except StructuredOutputError as exc:
            before = len(verdicts)
            _collect(salvage_items(exc.raw, "items", _Item), wanted, verdicts)
            report.salvaged += len(verdicts) - before
            logger.warning("x.relevance: unusable answer for %d posts (%d verdicts salvaged)", len(chunk), len(verdicts) - before)
            missing = [p for p in chunk if p.id not in verdicts]
            if len(missing) > 1:
                half = (len(missing) + 1) // 2
                queue[:0] = [missing[:half], missing[half:]]
            else:
                report.unscreened += len(missing)
    return verdicts


async def detect_x_opportunities(
    db: Session, router: ModelRouter, agent_id: int, retriever: MemoryRetriever, settings, embedding_model: str | None, limit: int = 5  # type: ignore[no-untyped-def]
) -> XOpportunityReport:
    report = XOpportunityReport()
    budget = PlatformBudget(db, settings)
    watermark = int(budget.watermark("x_opportunities") or 0)
    since = utcnow() - WINDOW
    posts = list(db.scalars(
        select(SocialPost).where(
            SocialPost.platform == Platform.X, SocialPost.is_own.is_(False), SocialPost.id > watermark,
            (SocialPost.posted_at.is_(None)) | (SocialPost.posted_at >= since),
        ).order_by(SocialPost.id).limit(SCREEN_LIMIT)
    ))
    if not posts:
        return report
    budget.set_watermark("x_opportunities", str(max(p.id for p in posts)))
    posts = [p for p in posts if len(p.text.strip()) >= 25]
    report.screened = len(posts)
    if not posts:
        db.commit()
        return report

    profiles = {p.id: p for p in db.scalars(select(SocialProfile).where(SocialProfile.id.in_([x.author_profile_id for x in posts if x.author_profile_id])))}
    watch = {w.profile_id: w for w in db.scalars(select(WatchedAccount))}
    brief = _brief(db, agent_id)

    verdicts = await screen_posts(router, brief, posts, profiles, report)

    candidates = []
    for p in posts:
        v = verdicts.get(p.id)
        mention = "mention" in p.source_kinds
        if mention or (v and v.relevant and v.relevance >= 0.5):
            candidates.append((p, v))
    report.relevant = len(candidates)

    topic_vecs = [(t, from_blob(t.centroid)) for t in db.scalars(select(CommunityTopic).where(
        CommunityTopic.status == TopicStatus.ACTIVE, CommunityTopic.review_status != "rejected")) if t.centroid]
    post_vecs = {r.post_id: from_blob(r.vector) for r in db.scalars(select(SocialPostVector).where(
        SocialPostVector.post_id.in_([p.id for p, _ in candidates]), SocialPostVector.model == (embedding_model or "")))}
    prefs = PreferenceModel(db, "social_opportunity")

    def reach(p: SocialPost) -> float:
        a = profiles.get(p.author_profile_id or -1)
        return min(1.0, math.log10((a.followers_count or 0) + 1) / 6) if a else 0.0

    candidates.sort(key=lambda c: ((c[1].relevance if c[1] else 0.6) + reach(c[0]) * 0.5 + (0.5 if "mention" in c[0].source_kinds else 0)), reverse=True)
    for p, v in candidates[:limit]:
        author = profiles.get(p.author_profile_id or -1)
        recall = await retriever.retrieve(RetrievalQuery(p.text, limit=4))
        knowledge = [{"memory_id": m.record.id, "title": m.record.title, "layer": m.record.layer.value, "snippet": m.record.content[:200]} for m in recall.memories]
        vec = post_vecs.get(p.id)
        overlap_topic, overlap = None, 0.0
        if vec is not None:
            for t, tv in topic_vecs:
                if tv.shape == vec.shape and (sim := float(tv @ vec)) > overlap:
                    overlap, overlap_topic = sim, t
        knowledge_text = "\n".join(f"- {k['title'] or ''}: {k['snippet']}" for k in knowledge) or "(no relevant approved knowledge)"
        community_line = f"\nThe brand's Telegram community is also discussing '{overlap_topic.name}'." if overlap_topic and overlap >= COMMUNITY_OVERLAP else ""
        user = (
            f"{brief}\n\nPost by @{author.username if author else 'unknown'} ({author.followers_count if author and author.followers_count is not None else 'unknown'} followers):\n"
            f"{p.text}\nPublic engagement: {p.metrics or 'unknown'}{community_line}\n\nBrand knowledge (approved):\n{knowledge_text}"
        )
        try:
            advice, result, route = await complete_structured(router, "x.opportunity", ADVICE_SYSTEM, user, _Advice)
        except StructuredOutputError as exc:  # one unusable answer must not lose the whole run
            logger.warning("x.opportunity: no usable advice for post %s: %s", p.id, str(exc)[:200])
            report.advice_failed += 1
            continue

        kind = SocialOpportunityKind.MENTION if "mention" in p.source_kinds else (v.kind if v else SocialOpportunityKind.CONVERSATION)
        if overlap_topic and overlap >= COMMUNITY_OVERLAP and kind == SocialOpportunityKind.CONVERSATION:
            kind = SocialOpportunityKind.COMMUNITY_CROSSOVER
        hours = max(0.0, (utcnow() - p.posted_at).total_seconds() / 3600) if p.posted_at else 24.0
        m = p.metrics or {}
        engagement = sum(int(m.get(k, 0) or 0) for k in ("likes", "replies", "reposts", "quotes"))
        w = watch.get(p.author_profile_id or -1)
        signals = {
            "relevance": round(advice.relevance, 3),
            "author_reach": round(reach(p), 3),
            "engagement": round(min(1.0, math.log1p(engagement) / math.log1p(500)), 3) if m else None,
            "recency": round(math.exp(-hours / 24), 3),
            "watchlist_priority": round((6 - w.priority) / 5, 3) if w else 0.0,
            "community_overlap": round(max(0.0, (overlap - 0.5) / 0.4), 3) if overlap else 0.0,
            "knowledge_available": 1.0 if knowledge else 0.0,
        }
        reasons = []
        if "mention" in p.source_kinds:
            reasons.append("mentions your account")
        if w:
            reasons.append(f"from a watched account ({w.category.value.replace('_', ' ')})")
        if author and author.followers_count:
            reasons.append(f"author has {author.followers_count:,} followers")
        if m:
            reasons.append(f"{engagement} public engagements so far")
        if overlap_topic and overlap >= COMMUNITY_OVERLAP:
            reasons.append(f"your Telegram community is discussing '{overlap_topic.name}'")
        if knowledge:
            reasons.append(f"{len(knowledge)} relevant approved knowledge item(s)")
        opp = SocialOpportunity(
            platform=Platform.X, post_id=p.id, kind=kind, topic=strip_citations(advice.topic)[:200] or (v.topic if v else ""),
            relevance=advice.relevance, why=strip_citations(advice.why), contribution=strip_citations(advice.contribution),
            suggested_action=advice.suggested_action, knowledge=knowledge,
            engagement={**m, "suggestions": {"like": advice.like, "repost": advice.repost, "follow_author": advice.follow_author,
                                             "reason": strip_citations(advice.engagement_reason)}},
            signals=signals, reasons=reasons,
            status=SocialOpportunityStatus.NEW if advice.worthwhile and advice.suggested_action != SuggestedAction.IGNORE else SocialOpportunityStatus.DISMISSED,
            provider=result.provider, model=result.model or route.model, generated_at=utcnow(),
        )
        feats, _, _ = features_for(db, FeedbackTarget.SOCIAL_OPPORTUNITY, opp)
        pref = prefs.score(feats)
        opp.reasons = [*reasons, *pref.reasons]
        score = (0.35 * signals["relevance"] + 0.15 * signals["author_reach"] + 0.1 * (signals["engagement"] or 0)
                 + 0.15 * signals["recency"] + 0.1 * signals["watchlist_priority"] + 0.1 * signals["community_overlap"]
                 + 0.05 * signals["knowledge_available"]) + 0.1 * pref.score
        opp.priority = round(max(0.0, min(1.0, score)), 3)
        db.add(opp)
        db.flush()
        if opp.status == SocialOpportunityStatus.NEW:
            report.created += 1
            report.ids.append(opp.id)
        else:
            report.set_aside += 1
    db.commit()
    return report
