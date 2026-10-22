"""Content opportunity engine: community signals → evidence-backed content ideas → drafts.

Ideas and drafts only. Nothing here can publish; publication would require a
connector, the SafetyGuard and a human, none of which this module touches.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.prompting import PromptContext, SystemPromptBuilder
from app.ai.routing import ModelRouter
from app.ai.structured import complete_structured, unit_score
from app.community import provenance
from app.community.formatting import format_messages, strip_citations, valid_ids
from app.community.questions import recurring_questions
from app.community.tuning import CommunityTuning
from app.db.base import utcnow
from app.models.agent import AgentConfig
from app.models.community import CommunityQuestion, CommunityTopic, ContentOpportunity
from app.models.content import Content
from app.models.enums import (
    AgentTask,
    ContentChannel,
    ContentStatus,
    DerivedKind,
    OpportunityStatus,
    Platform,
    PromptPlatform,
    TopicStatus,
    TopicTrend,
    Urgency,
)
from app.models.telegram import TelegramMessage
from app.providers.base import ChatMessage
from app.services.activity import ActivityLogger
from app.services.brand_service import brand_data, get_brand, goal_data, list_goals

logger = logging.getLogger(__name__)

COOLDOWN = timedelta(hours=72)


class _Opportunity(BaseModel):
    worthwhile: bool = Field(description="false if this signal is not worth content (off-brand, trivial, or restricted)")
    reason: str = Field(description="Short reason for the decision")
    title: str = Field(description="The topic, max 10 words")
    why: str = Field(description="Why it matters now, grounded in the community evidence")
    angle: str = Field(description="Suggested content angle or working headline")
    platforms: list[ContentChannel] = Field(description="1-3 best-fitting platforms")
    urgency: Urgency
    confidence: float = Field(description="0.0 to 1.0")
    content_potential: float = Field(0.5, description="0.0 to 1.0: how good this would be as content")
    message_ids: list[str] = Field(description="Supporting message ids like m123")

    _score = field_validator("confidence", "content_potential", mode="before")(lambda v: unit_score(v))


SYSTEM = (
    "You are a content strategist for a brand. You receive one signal from the brand's community (a topic gaining "
    "attention or a question many members ask) with supporting messages. Decide whether it is worth creating "
    "content about and, if so, propose one idea.\nRules:\n"
    "- Respect the brand's topics to avoid and restricted claims; if the idea would need them, set worthwhile=false.\n"
    "- Ground 'why' in the evidence given (counts, trend, what members said). Do not invent statistics.\n"
    "- Prefer educational and genuinely useful angles over hype.\n"
    "Return JSON only."
)


@dataclass(slots=True)
class OpportunityReport:
    evaluated: int = 0
    created: int = 0
    skipped: int = 0
    ids: list[int] = field(default_factory=list)


def _recently_evaluated(db: Session, kind: str, origin_id: int, now: datetime) -> bool:
    return (
        db.scalar(
            select(ContentOpportunity.id).where(
                ContentOpportunity.origin_kind == kind,
                ContentOpportunity.origin_id == origin_id,
                ContentOpportunity.created_at >= now - COOLDOWN,
            )
        )
        is not None
    )


def _brand_brief(db: Session, agent_id: int) -> str:
    b = brand_data(get_brand(db, agent_id))
    goals = goal_data(list_goals(db, agent_id, enabled_only=True))
    parts = [f"Brand: {b.brand_name}"]
    if b.audience:
        parts.append(f"Audience: {b.audience}")
    if b.social_objectives:
        parts.append("Social objectives: " + "; ".join(b.social_objectives))
    if goals:
        parts.append("Goals: " + "; ".join(g.name for g in goals[:6]))
    if b.topics_to_avoid:
        parts.append("Topics to avoid: " + "; ".join(b.topics_to_avoid))
    if b.restricted_claims:
        parts.append("Never claim: " + "; ".join(b.restricted_claims))
    return "\n".join(parts)


async def detect_opportunities(
    db: Session, router: ModelRouter, agent_id: int, tuning: CommunityTuning, limit: int = 3, now: datetime | None = None
) -> OpportunityReport:
    now = now or utcnow()
    report = OpportunityReport()
    signals: list[tuple[str, CommunityTopic | CommunityQuestion, float]] = []
    from app.models.social_intel import CrossSignal

    cross_active = set(db.scalars(select(CrossSignal.telegram_topic_id).where(CrossSignal.status == "active")))

    for t in db.scalars(
        select(CommunityTopic).where(CommunityTopic.status == TopicStatus.ACTIVE, CommunityTopic.review_status != "rejected")
    ):
        on_x = t.id in cross_active
        if t.trend in (TopicTrend.NEW, TopicTrend.RISING) or t.importance >= 0.5 or on_x:
            signals.append(("topic", t, t.importance + (0.3 if t.trend == TopicTrend.RISING else 0) + (0.2 if on_x else 0)))
    for q in recurring_questions(db, tuning, limit=20):
        if q.review_status == "rejected":
            continue
        if q.ask_count >= max(3, tuning.recurring_min_asks):
            signals.append(("question", q, min(1.0, q.ask_count / 10) + 0.2))

    signals.sort(key=lambda s: s[2], reverse=True)
    brief = _brand_brief(db, agent_id)
    for kind, item, _ in signals:
        if report.evaluated >= limit:
            break
        if _recently_evaluated(db, kind, item.id, now):
            continue
        report.evaluated += 1
        opp = await _evaluate(db, router, brief, kind, item, now)
        if opp.status == OpportunityStatus.DETECTED:
            report.created += 1
            report.ids.append(opp.id)
        else:
            report.skipped += 1
    db.commit()
    return report


def _signal_messages(db: Session, kind: str, item: CommunityTopic | CommunityQuestion) -> list[TelegramMessage]:
    column = TelegramMessage.topic_id if kind == "topic" else TelegramMessage.question_id
    ids = item.representative_message_ids if isinstance(item, CommunityTopic) else []
    recent = list(db.scalars(select(TelegramMessage).where(column == item.id).order_by(TelegramMessage.sent_at.desc()).limit(15)))
    reps = list(db.scalars(select(TelegramMessage).where(TelegramMessage.id.in_(ids)))) if ids else []
    return sorted({m.id: m for m in [*reps, *recent]}.values(), key=lambda m: m.sent_at)


async def _evaluate(
    db: Session, router: ModelRouter, brief: str, kind: str, item: CommunityTopic | CommunityQuestion, now: datetime
) -> ContentOpportunity:
    messages = _signal_messages(db, kind, item)
    text, included = format_messages(messages, char_budget=6000, max_chars_each=300)
    if isinstance(item, CommunityTopic):
        evidence: dict[str, Any] = {
            "signal": "topic",
            "name": item.name,
            "messages": item.message_count,
            "participants": item.participant_count,
            "messages_24h": item.count_24h,
            "messages_prev_24h": item.count_prev_24h,
            "trend": item.trend.value,
        }
        signal = (
            f"Topic: {item.name}\nDescription: {item.description}\nMessages: {item.message_count} from "
            f"{item.participant_count} members; last 24h: {item.count_24h} (previous 24h: {item.count_prev_24h}); trend: {item.trend.value}"
        )
    else:
        evidence = {"signal": "question", "question": item.text, "asked": item.ask_count, "askers": item.asker_count}
        signal = f"Recurring question: {item.text}\nAsked {item.ask_count} times by {item.asker_count} members"

    out, result, route = await complete_structured(
        router, "content.opportunity", SYSTEM, f"{brief}\n\nSignal:\n{signal}\n\nSupporting messages:\n{text}", _Opportunity
    )
    ids = valid_ids(out.message_ids, set(included)) or included[:5]
    evidence["sample_message_ids"] = ids[:10]
    opp = ContentOpportunity(
        title=strip_citations(out.title)[:300] or (item.name if isinstance(item, CommunityTopic) else item.text[:300]),
        why=strip_citations(out.why),
        angle=strip_citations(out.angle),
        evidence=evidence,
        platforms=[p.value for p in out.platforms][:3],
        urgency=out.urgency,
        confidence=round(out.confidence, 3),
        content_potential=round(out.content_potential, 3),
        status=OpportunityStatus.DETECTED if out.worthwhile else OpportunityStatus.REJECTED,
        origin_kind=kind,
        origin_id=item.id,
        notes="" if out.worthwhile else f"Automatically set aside by the model: {out.reason.strip()}",
        provider=result.provider,
        model=result.model or route.model,
        generated_at=now,
    )
    db.add(opp)
    db.flush()
    provenance.link(db, DerivedKind.OPPORTUNITY, opp.id, ids)
    return opp


# --- drafting -----------------------------------------------------------------------------

CHANNEL_TO_PROMPT = {
    ContentChannel.X: PromptPlatform.X,
    ContentChannel.TELEGRAM: PromptPlatform.TELEGRAM,
    ContentChannel.YOUTUBE: PromptPlatform.YOUTUBE,
    ContentChannel.YOUTUBE_SHORTS: PromptPlatform.YOUTUBE_SHORTS,
    ContentChannel.TIKTOK: PromptPlatform.TIKTOK,
}
CHANNEL_TO_PLATFORM = {ContentChannel.X: Platform.X, ContentChannel.TELEGRAM: Platform.TELEGRAM}


async def draft_opportunity(
    db: Session, router: ModelRouter, agent_id: int, opp: ContentOpportunity, channel: ContentChannel, actor: str = "dashboard"
) -> Content:
    """Write a draft for review. The draft is saved as Content with status 'draft'; nothing is posted."""
    config = db.get(AgentConfig, agent_id)
    assert config is not None
    prompt = SystemPromptBuilder().build(
        PromptContext(
            identity=config.identity,
            brand=brand_data(get_brand(db, agent_id)),
            goals=goal_data(list_goals(db, agent_id, enabled_only=True)),
            behavior_rules=list(config.behavior_rules or []),
            task=AgentTask.DRAFT_POST,
            platform=CHANNEL_TO_PROMPT[channel],
        )
    )
    messages = provenance.messages_for(db, DerivedKind.OPPORTUNITY, opp.id, limit=10)
    evidence_text, _ = format_messages(messages, char_budget=3000, max_chars_each=250)
    user = (
        f"Create content for this opportunity.\nTopic: {opp.title}\nWhy it matters: {opp.why}\nAngle: {opp.angle}\n\n"
        f"What community members said (paraphrase; do not quote or name members):\n{evidence_text}"
    )
    result, route = await router.complete(
        "content.draft", [ChatMessage("system", prompt.text), ChatMessage("user", user)], timeout_seconds=300
    )
    content = Content(
        agent_id=agent_id,
        title=opp.title[:256],
        body=result.content.strip(),
        status=ContentStatus.DRAFT,
        target_platform=CHANNEL_TO_PLATFORM.get(channel),
        created_by=f"ai:{result.model or route.model}",
        extra={
            "opportunity_id": opp.id,
            "channel": channel.value,
            "generated_by": {"provider": result.provider, "model": result.model or route.model, "task": route.task},
            "generated_at": utcnow().isoformat(),
        },
    )
    db.add(content)
    db.flush()
    opp.content_id = content.id
    opp.status = OpportunityStatus.DRAFTED
    db.commit()
    ActivityLogger(db, agent_id).log(
        "content_drafted",
        f'Drafted {channel.value.replace("_", " ")} content for "{opp.title}" with {result.model} (saved as a draft; not published)',
        actor=actor,
        details={"opportunity_id": opp.id, "content_id": content.id, "model": result.model, "latency_ms": result.latency_ms},
    )
    return content
