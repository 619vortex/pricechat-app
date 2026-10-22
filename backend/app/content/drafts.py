"""Draft generator: approved content opportunity → platform-specific drafts (and variants).

Drafts are saved to Content with status 'draft' and a `context` record explaining
why they exist (opportunity, evidence, memories and community context used, model,
checks). Nothing is ever published from here.
"""

import logging
import re
import uuid
from dataclasses import dataclass

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.prompting import PromptContext, SystemPromptBuilder
from app.ai.routing import ModelRouter
from app.ai.structured import complete_structured
from app.community import provenance
from app.community.context import CommunityContextProvider
from app.community.formatting import format_messages, strip_citations
from app.community.tuning import CommunityTuning
from app.db.base import utcnow
from app.memory.retrieval.base import MemoryRetriever, RetrievalQuery
from app.models.agent import AgentConfig
from app.models.community import ContentOpportunity
from app.models.content import Content
from app.models.enums import AgentTask, ContentStatus, DerivedKind, DraftType, OpportunityStatus, Platform, PromptPlatform
from app.providers.base import EmbeddingProvider
from app.services.activity import ActivityLogger
from app.services.brand_service import brand_data, get_brand, goal_data, list_goals

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DraftSpec:
    label: str
    platform: PromptPlatform
    target: Platform | None
    instructions: str
    styles: tuple[str, str, str]
    max_chars: int | None = None  # per post, where the platform has a hard limit


X_STYLES = ("concise", "educational", "conversational")
SHORT_STYLES = ("curiosity hook", "direct hook", "contrarian hook")
YT_STYLES = ("search-oriented", "curiosity-oriented", "authority-oriented")

SPECS: dict[DraftType, DraftSpec] = {
    DraftType.X_POST: DraftSpec("X post", PromptPlatform.X, Platform.X,
        "Write one X post of at most 280 characters. Self-contained. At most one hashtag, only if useful.", X_STYLES, 280),
    DraftType.X_THREAD: DraftSpec("X thread", PromptPlatform.X, Platform.X,
        "Write an X thread of 4-7 posts. Put each post on its own line starting with '1/', '2/' etc. Each post at most 280 characters. The first post must work on its own.", X_STYLES, 280),
    DraftType.TELEGRAM_POST: DraftSpec("Telegram post", PromptPlatform.TELEGRAM, Platform.TELEGRAM,
        "Write one Telegram community post: short paragraphs, skimmable, friendly, at most 900 characters.", X_STYLES),
    DraftType.YOUTUBE_IDEA: DraftSpec("YouTube video idea", PromptPlatform.YOUTUBE, None,
        "Describe one YouTube video idea: working title, who it is for, the promise of the video, and 3-5 key points.", YT_STYLES),
    DraftType.YOUTUBE_TITLES: DraftSpec("YouTube title ideas", PromptPlatform.YOUTUBE, None,
        "Write 5 YouTube title options, one per line, each under 70 characters. Accurate, no clickbait that the video can't deliver.", YT_STYLES),
    DraftType.YOUTUBE_OUTLINE: DraftSpec("YouTube outline", PromptPlatform.YOUTUBE, None,
        "Write a YouTube video outline: title, a 1-2 sentence hook, then 5-7 numbered sections with the key point of each, and a closing.", YT_STYLES),
    DraftType.SHORT_VIDEO_IDEA: DraftSpec("Short-form video idea", PromptPlatform.YOUTUBE_SHORTS, None,
        "Describe one short vertical video (max 45s): the hook line, 3-5 spoken beats, on-screen text in [brackets], and the closing line.", SHORT_STYLES),
    DraftType.SHORT_HOOK: DraftSpec("Short-form hooks", PromptPlatform.YOUTUBE_SHORTS, None,
        "Write 5 opening hook lines for a short video, one per line, each under 15 words.", SHORT_STYLES),
    DraftType.TIKTOK_IDEA: DraftSpec("TikTok idea", PromptPlatform.TIKTOK, None,
        "Describe one TikTok video (max 45s): hook line, 3-5 spoken beats, on-screen text in [brackets], closing line.", SHORT_STYLES),
}

GENERIC_RISK = re.compile(
    r"\b(guarantee[ds]?|risk[- ]free|will (go|moon|pump|rise|hit)|price target|100x|financial advice|can't lose|sure thing)\b", re.I
)


class _Variant(BaseModel):
    style: str = Field(description="Which requested style this variant follows")
    title: str = Field(description="Short internal title for the draft")
    text: str = Field(description="The draft itself, ready for human review")
    notes: str = Field("", description="One line on what makes this variant different")


class _Drafts(BaseModel):
    variants: list[_Variant]


def checks_for(spec: DraftSpec, text: str, restricted: list[str]) -> dict[str, object]:
    warnings: list[str] = []
    if spec.max_chars:
        posts = [p for p in text.split("\n") if p.strip()] if spec.label == "X thread" else [text]
        for i, p in enumerate(posts, 1):
            body = re.sub(r"^\s*\d+/\s*", "", p).strip()
            if len(body) > spec.max_chars:
                warnings.append(f"{'post ' + str(i) + ' ' if len(posts) > 1 else ''}is {len(body)} characters (limit {spec.max_chars})")
    for m in GENERIC_RISK.finditer(text):
        warnings.append(f"possible restricted claim: '{m.group(0)}'")
    lower = text.lower()
    for claim in restricted:
        words = [w for w in re.findall(r"[a-z]{5,}", claim.lower())]
        if words and sum(w in lower for w in words) >= max(2, len(words) // 2 + 1):
            warnings.append(f"may touch restricted claim: '{claim}'")
    return {"length_ok": not any("characters" in w for w in warnings), "warnings": warnings}


async def generate_drafts(
    db: Session,
    router: ModelRouter,
    agent_id: int,
    opp: ContentOpportunity,
    draft_type: DraftType,
    retriever: MemoryRetriever,
    embedder: EmbeddingProvider | None,
    tuning: CommunityTuning,
    variants: int = 1,
    actor: str = "dashboard",
) -> list[Content]:
    if opp.status not in (OpportunityStatus.APPROVED, OpportunityStatus.DRAFTED, OpportunityStatus.REVIEWING):
        raise ValueError("Approve the opportunity before generating drafts")
    spec = SPECS[draft_type]
    config = db.get(AgentConfig, agent_id)
    assert config is not None
    brand = brand_data(get_brand(db, agent_id))
    query = f"{opp.title}. {opp.angle}"

    retrieval = await retriever.retrieve(RetrievalQuery(query, limit=5))
    community = await CommunityContextProvider(db, embedder, tuning).gather(query)
    prompt = SystemPromptBuilder().build(
        PromptContext(
            identity=config.identity, brand=brand,
            goals=goal_data(list_goals(db, agent_id, enabled_only=True)),
            behavior_rules=list(config.behavior_rules or []),
            memories=retrieval.memories, task=AgentTask.DRAFT_POST, platform=spec.platform, community=community,
        )
    )
    evidence, _ = format_messages(provenance.messages_for(db, DerivedKind.OPPORTUNITY, opp.id, limit=10), 2500, 250)
    styles = spec.styles[:variants] if variants > 1 else ("best fit",)
    user = (
        f"Content opportunity: {opp.title}\nWhy it matters: {opp.why}\nSuggested angle: {opp.angle}\n"
        f"What members said (paraphrase only; never quote or name members):\n{evidence or '(no messages)'}\n\n"
        f"Format: {spec.instructions}\n"
        f"Write {len(styles)} variant(s), one for each style: {', '.join(styles)}. "
        "Each must follow the brand voice and claims policy. No invented facts, numbers or promises. Return JSON only."
    )
    task = "content.variants" if variants > 1 else "content.draft"
    out, result, route = await complete_structured(router, task, prompt.text, user, _Drafts)

    group = uuid.uuid4().hex[:12]
    context_base = {
        "why": f"Created from content opportunity #{opp.id}: {opp.title}",
        "opportunity": {"id": opp.id, "title": opp.title, "why": opp.why, "angle": opp.angle,
                        "priority": opp.priority_label, "reasons": opp.reasons, "evidence": opp.evidence},
        "memories_used": [{"id": m.record.id, "title": m.record.title, "layer": m.record.layer.value} for m in retrieval.memories],
        "community_context": {
            "topics": [t.name for t in community.topics] if community else [],
            "questions": [q.text for q in community.questions] if community else [],
        },
        "brand_profile": {"brand_name": brand.brand_name, "restricted_claims": brand.restricted_claims},
        "generated_by": {"provider": result.provider, "model": result.model or route.model, "task": route.task},
        "generated_at": utcnow().isoformat(),
        "draft_type": draft_type.value,
    }
    drafts: list[Content] = []
    for v in out.variants[: len(styles)]:
        text = strip_citations(v.text)
        if not text:
            continue
        content = Content(
            agent_id=agent_id, title=strip_citations(v.title)[:256] or opp.title[:256], body=text, status=ContentStatus.DRAFT,
            target_platform=spec.target, created_by=f"ai:{result.model or route.model}", draft_type=draft_type.value,
            opportunity_id=opp.id, variant_group=group, variant_label=v.style[:64] if variants > 1 else None,
            context={**context_base, "variant_notes": v.notes, "checks": checks_for(spec, text, brand.restricted_claims)},
            extra={"opportunity_id": opp.id},
        )
        db.add(content)
        drafts.append(content)
    if not drafts:
        raise ValueError("The model returned no usable draft")
    db.flush()
    opp.status = OpportunityStatus.DRAFTED
    opp.content_id = drafts[0].id
    db.commit()
    ActivityLogger(db, agent_id).log(
        "content_drafted",
        f'Generated {len(drafts)} {spec.label} draft{"s" if len(drafts) > 1 else ""} for "{opp.title}" with {result.model} (not published)',
        actor=actor, details={"opportunity_id": opp.id, "content_ids": [d.id for d in drafts], "draft_type": draft_type.value},
    )
    return drafts
