"""Writing X replies and posts for the approval queue.

The model sees only context that matters for this one post: the post and what it answers (if
stored), the author relationship, the monitored topic, matching approved knowledge, a matching
Telegram community signal, Price Chat's previous replies to this account, brand rules + X voice,
and what the human reviewer has taught through edits. Generic praise is rejected and rewritten.
"""

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.routing import ModelRouter
from app.ai.structured import complete_structured
from app.learning.operator import drafting_hints
from app.models.enums import ApprovalStatus, CrossSignalStatus, Platform, SocialAction
from app.models.social import SocialPost
from app.models.social_intel import ApprovalRequest, CrossSignal, SocialOpportunity, SocialProfile, SocialRelationship
from app.services.brand_service import get_brand

MAX_CHARS = 280

DEFAULT_X_VOICE = [
    "curious", "knowledgeable", "direct", "informal", "human", "occasionally witty", "interested in value and price",
]
DEFAULT_X_AVOID = [
    "corporate language", "AI-sounding phrases", "fake enthusiasm", "constant promotion", "engagement bait",
    "excessive emojis", "excessive hashtags", "price predictions presented as facts",
]

# Statements about Price Chat itself ("we offer...", "our team is fixing...") need approved knowledge behind them.
SELF_CLAIM = re.compile(
    r"\b(we(?:'re|'ve|'ll|’re|’ve|’ll)\b|we (are|will|do|don'?t|currently|have|offer|surface|support|track|plan|built|added|launched)\b|"
    r"our (team|app|alerts?|support|data|platform|product)\b|team'?s\b|the team is\b|support team\b)", re.I)

_NUMBER = re.compile(r"\$?\d[\d,.]*\s?(?:%|bps|[kmb]n?\b|billion|million|thousand)?", re.I)
_SOURCE = re.compile(r"\b(source|according to|per|via)\s*:?\s+[A-Z][\w.&-]+", re.I)


def unsupported_facts(text: str, evidence: str) -> str | None:
    """A number or a cited source in the draft that appears nowhere in the post or the context."""
    ev = evidence.lower().replace(",", "")
    for m in _NUMBER.finditer(text):
        token = m.group(0).strip()
        digits = re.sub(r"[^\d.]", "", token).strip(".")
        is_figure = token.startswith("$") or token[-1:].isalpha() or token.endswith("%") or len(digits.replace(".", "")) >= 2
        if is_figure and digits and digits not in ev:
            return token
    src = _SOURCE.search(text)
    if src and src.group(0).split()[-1].lower() not in ev:
        return src.group(0)
    return None


# Template leftovers ("[link to recap]", "<name>") and links (the writer may not add links).
PLACEHOLDER = re.compile(r"\[[^\]]{2,80}\]|<[a-z ]{2,40}>|\bhttps?://|\bwww\.", re.I)

GENERIC = re.compile(
    r"^\s*(great (insight|point|post|thread|question)|absolutely|this is so important|couldn'?t agree more|love this|"
    r"so true|well said|100%|exactly this|spot on|amazing|interesting (point|take)|thanks for sharing|thank you for sharing|"
    r"great (read|share|stuff)|this!)", re.I)

WRITER_SYSTEM = (
    "You write X (Twitter) {kind} for Price Chat, a media and community brand about anything that has a price: markets, "
    "crypto, stocks, real estate, gold, collectibles, cars, money and stories about value. Its worldview is 'everything has "
    "a price' (do not force that phrase in).\n\n"
    "Voice: {voice}.\nAvoid: {avoid}.\n\n"
    "Rules:\n"
    "- Respond to what the post actually says and add something: a fact, a number, context, a sharp question or a perspective.\n"
    "- Never open with praise or agreement (no 'Great insight', 'Absolutely', 'Couldn't agree more', 'Love this').\n"
    "- Sound like a person, not a brand account. Short is fine. At most {max_chars} characters.\n"
    "- No hashtags unless essential, at most one emoji, no links.\n"
    "- Mention Price Chat only if it is genuinely natural (usually it is not).\n"
    "- Only state facts that are in the post or in the context given. If unsure, ask or say what is uncertain. "
    "Never present price predictions as facts.\n"
    "- Never use numbers, statistics, dates or named sources unless they appear in the post or the context.\n"
    "- Never state anything about Price Chat itself (its features, data, alerts, plans, team, support, fixes or what it is "
    "'working on') unless it appears under 'Price Chat knowledge' in the context. If someone asks about Price Chat and the "
    "answer is not in that knowledge, do not answer it: acknowledge the question and say you will check, or ask a follow-up.\n"
    "- Community discussion is what members are talking about, not established fact.\n"
    "- Telegram community lines are aggregate. You may say something like 'this keeps coming up in our community' only if a "
    "'Telegram community' line supports it. Never name, quote or identify individual community members.\n"
    "{restricted}{hints}"
    "If there is nothing worthwhile to add, set worth_it to false. Return JSON only."
)


class _Draft(BaseModel):
    text: str = Field(description="The reply or post text, ready to publish")
    worth_it: bool = Field(True, description="False if Price Chat has nothing useful to add")
    confidence: str = Field("medium", description="high, medium or low")
    why: str = Field("", description="One sentence: why this adds value")


class _Variant(BaseModel):
    style: str = Field(description="direct, conversational or insight-driven")
    text: str


class _Variants(BaseModel):
    worth_it: bool = Field(True, description="False if Price Chat has nothing useful to add")
    confidence: str = Field("medium", description="high, medium or low")
    why: str = Field("", description="One sentence: why this adds value")
    variants: list[_Variant] = Field(description="Exactly three versions: direct, conversational, insight-driven")


VARIANT_STYLES = ("direct", "conversational", "insight-driven")


@dataclass(slots=True)
class DraftedText:
    text: str
    worth_it: bool
    confidence: str
    why: str
    model: str | None
    context: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    variants: list[dict[str, object]] = field(default_factory=list)  # [{style, text, warnings}] for high-priority items


def voice(db: Session, agent_id: int) -> tuple[list[str], list[str]]:
    brand = get_brand(db, agent_id)
    return (brand.x_voice or DEFAULT_X_VOICE), (brand.x_avoid or DEFAULT_X_AVOID)


def _context(db: Session, opp: SocialOpportunity, post: SocialPost) -> tuple[list[str], str | None]:
    """(lines shown to the model AND the reviewer, author username)."""
    lines: list[str] = []
    author = db.get(SocialProfile, post.author_profile_id) if post.author_profile_id else None
    name = author.username if author else post.author_handle

    if post.in_reply_to_external_id:
        parent = db.scalar(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.external_id == post.in_reply_to_external_id))
        if parent is not None:
            lines.append(f"It replies to: \"{parent.text[:240]}\"")
    if opp.topic:
        lines.append(f"Monitored topic: {opp.topic}")
    if author is not None:
        rel = db.scalar(select(SocialRelationship).where(SocialRelationship.profile_id == author.id))
        who = f"@{author.username}" + (f", {author.followers_count:,} followers" if author.followers_count is not None else "")
        if rel and rel.interaction_count:
            who += (f"; {rel.interaction_count} past interactions with Price Chat ({rel.mention_count} mentions, "
                    f"{rel.our_reply_count} replies from us)")
            if rel.notes:
                who += f"; note: {rel.notes[:120]}"
        lines.append(f"Author: {who}")
        previous = list(db.scalars(select(ApprovalRequest).where(
            ApprovalRequest.status == ApprovalStatus.EXECUTED, ApprovalRequest.action == SocialAction.REPLY,
            func.lower(ApprovalRequest.target_username) == author.username.lower()).order_by(ApprovalRequest.executed_at.desc()).limit(2)))
        for a in previous:
            lines.append(f"Our earlier reply to them: \"{(a.payload or {}).get('text', '')[:160]}\" (don't repeat it)")
    for k in (opp.knowledge or [])[:3]:
        lines.append(f"Price Chat knowledge: {(k.get('title') or '').strip()} {k.get('snippet') or k.get('content') or ''}".strip()[:240])
    sig = next((c for c in db.scalars(select(CrossSignal).where(CrossSignal.status == CrossSignalStatus.ACTIVE))
                if post.id in (c.x_post_ids or [])), None)
    if sig is not None:
        lines.append(f"Telegram community signal: members are discussing '{sig.topic_name}' ({sig.telegram_messages_24h} messages in 24h "
                     f"from {sig.telegram_participants} people). This is discussion, not fact.")
        if sig.telegram_topic_id:
            from app.models.community import CommunityQuestion

            for q in db.scalars(select(CommunityQuestion).where(CommunityQuestion.topic_id == sig.telegram_topic_id,
                                                                CommunityQuestion.ask_count >= 2).order_by(CommunityQuestion.ask_count.desc()).limit(2)):
                lines.append(f"Telegram community: recurring question, asked {q.ask_count} times by {q.asker_count} members: \"{q.text[:160]}\"")
    return lines, name


async def draft_reply(db: Session, router: ModelRouter, agent_id: int, opp: SocialOpportunity, *, standalone: bool = False,
                      direction: str | None = None, variants: bool = False) -> DraftedText:
    """A reply to the opportunity's post, or (standalone=True) a post inspired by it."""
    post = db.get(SocialPost, opp.post_id)
    if post is None:
        raise LookupError("The original post is gone")
    brand = get_brand(db, agent_id)
    v, avoid = voice(db, agent_id)
    hints = drafting_hints(db)
    restricted = f"- Never make these claims: {'; '.join(brand.restricted_claims)}\n" if brand.restricted_claims else ""
    hint_text = ("Learned from the reviewer's past edits:\n" + "\n".join(f"- {h}" for h in hints) + "\n") if hints else ""
    system = WRITER_SYSTEM.format(kind="standalone posts" if standalone else "replies", voice=", ".join(v), avoid=", ".join(avoid),
                                  max_chars=MAX_CHARS, restricted=restricted, hints=hint_text)
    lines, author = _context(db, opp, post)
    what = ("Write a standalone Price Chat post on this topic, inspired by (not replying to) this post"
            if standalone else f"Write Price Chat's reply to this post by @{author or 'unknown'}")
    user = (f"{what}:\n\"{post.text}\"\n\nWhy it was flagged: {opp.why}\nWhat Price Chat could add: {opp.contribution}\n"
            + (f"Direction from the reviewer: {direction}\n" if direction else "")
            + "\nContext:\n" + ("\n".join(f"- {line}" for line in lines) or "- (none)"))

    has_knowledge = any(line.startswith("Price Chat knowledge") for line in lines)
    evidence = " ".join([post.text, opp.why, opp.contribution, direction or "", *lines])

    if variants:
        try:
            out, result, _ = await complete_structured(
                router, "x.reply", system, user + "\n\nWrite exactly three versions: direct, conversational, insight-driven.", _Variants)
            checked = []
            for v in out.variants[:3]:
                t = v.text.strip().strip('"')
                checked.append({"style": v.style.lower(), "text": t, "warnings": check_text(t, evidence, has_knowledge)[1]})
            if out.worth_it and checked:
                best = next((c for c in checked if not c["warnings"]), checked[0])
                return DraftedText(str(best["text"]), True, (out.confidence or "medium").lower(), out.why, result.model, lines,
                                   list(best["warnings"]), checked)  # type: ignore[arg-type]
            if not out.worth_it:
                return DraftedText("", False, "low", out.why, result.model, lines, [])
        except Exception:  # noqa: BLE001 - fall back to a single draft
            pass

    draft, result, _ = await complete_structured(router, "x.reply", system, user, _Draft)
    problem, _ = check_text(draft.text, evidence, has_knowledge)
    if draft.worth_it and problem:
        draft, result, _ = await complete_structured(
            router, "x.reply", system, user + f"\n\nA previous attempt was rejected because {problem}. Write a better one.", _Draft)
    text = draft.text.strip().strip('"')
    _, warnings = check_text(text, evidence, has_knowledge)
    return DraftedText(text, draft.worth_it, draft.confidence.lower() if draft.confidence else "medium", draft.why,
                       result.model, lines, warnings)


def check_text(text: str, evidence: str, has_knowledge: bool) -> tuple[str | None, list[str]]:
    """(reason to rewrite, warnings for the reviewer). Generic praise, length, placeholders/links,
    invented figures or sources, and unsupported claims about Price Chat itself."""
    warnings: list[str] = []
    problem: str | None = None
    placeholder = PLACEHOLDER.search(text)
    invented = unsupported_facts(text, evidence)
    claim = SELF_CLAIM.search(text) if not has_knowledge else None
    if GENERIC.search(text):
        problem = "it opened with generic praise"
        warnings.append("Opens with generic praise; edit before approving")
    if len(text) > MAX_CHARS:
        problem = problem or f"it was {len(text)} characters"
        warnings.append(f"{len(text)} characters; X allows {MAX_CHARS}")
    if placeholder:
        problem = problem or f"it contained a placeholder or link (\"{placeholder.group(0)}\"); write finished text with no links"
        warnings.append(f"Contains a placeholder or link (\"{placeholder.group(0)}\"): edit before approving")
    if invented:
        problem = problem or (f"it stated a figure or source (\"{invented}\") that is not in the post or context. Use no numbers, "
                              "statistics or sources unless they appear there")
        warnings.append(f"States a figure or source (\"{invented}\") that is not in the post or context: verify or remove")
    if claim:
        problem = problem or (f"it claimed things about Price Chat that are not in the knowledge (\"{claim.group(0)}\"). "
                              "Do not describe Price Chat's features, data, team or plans; acknowledge and say you'll check instead")
        warnings.append("Says something about Price Chat itself (features, team, plans) that is not in approved knowledge: verify before approving")
    if "#" in text:
        warnings.append("Contains a hashtag")
    return problem, warnings
