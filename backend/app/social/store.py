"""Persist normalised social data: profiles, posts, metrics snapshots, interactions, relationships."""

import re
from collections import Counter
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.memory.retrieval.lexical import STOPWORDS
from app.models.enums import ContentStatus, InteractionType, Platform
from app.models.social import Interaction, SocialPost
from app.models.social_intel import SocialPostMetric, SocialProfile, SocialRelationship
from app.social.types import SocialPostData, SocialProfileData

_WORD = re.compile(r"[a-z][a-z0-9]{3,}")


def upsert_profile(db: Session, p: SocialProfileData, is_self: bool = False) -> SocialProfile:
    row = db.scalar(select(SocialProfile).where(SocialProfile.platform == Platform(p.platform), SocialProfile.external_id == p.external_id))
    if row is None:
        row = SocialProfile(platform=Platform(p.platform), external_id=p.external_id, username=p.username)
        db.add(row)
    row.username = p.username
    row.display_name = p.display_name or row.display_name
    for field in ("followers_count", "following_count", "post_count", "verified"):
        value = getattr(p, field)
        if value is not None:
            setattr(row, field, value)
    row.is_self = row.is_self or is_self
    row.last_seen_at = utcnow()
    db.flush()
    return row


def upsert_post(
    db: Session,
    data: SocialPostData,
    source_kind: str,
    author: SocialProfile | None = None,
    rule_id: int | None = None,
    is_own: bool = False,
) -> tuple[SocialPost, bool]:
    platform = Platform(data.platform)
    row = db.scalar(select(SocialPost).where(SocialPost.platform == platform, SocialPost.external_id == data.external_id))
    created = row is None
    if row is None:
        row = SocialPost(
            platform=platform, external_id=data.external_id, text=data.text, url=data.url, is_own=is_own,
            status=ContentStatus.PUBLISHED if is_own else ContentStatus.DRAFT, posted_at=data.created_at,
            author_handle=author.username if author else None, author_profile_id=author.id if author else None,
            conversation_id=data.conversation_id, in_reply_to_external_id=data.in_reply_to_user_external_id,
            referenced=data.referenced, lang=data.lang, source_kinds=[], rule_ids=[], content_type=data.content_type,
        )
        db.add(row)
    if not created and any("text" in r for r in data.referenced or []):
        # newer data knows what the referenced post says: keep it (context for replies), never lose it again
        known = {r.get("id"): r for r in row.referenced or []}
        row.referenced = [r if "text" in r else known.get(r.get("id"), r) for r in data.referenced]
    if data.media:  # a later read without the media expansion never erases it
        row.media = data.media
    row.is_own = row.is_own or is_own
    if source_kind not in (row.source_kinds or []):
        row.source_kinds = [*(row.source_kinds or []), source_kind]
    if rule_id is not None and rule_id not in (row.rule_ids or []):
        row.rule_ids = [*(row.rule_ids or []), rule_id]
    db.flush()
    record_metrics(db, row, data)
    return row, created


def record_metrics(db: Session, row: SocialPost, data: SocialPostData) -> None:
    m = data.metrics.as_dict()
    if not m:
        return
    changed = m != {k: v for k, v in (row.metrics or {}).items() if k in m}
    row.metrics = {**(row.metrics or {}), **m}
    row.metrics_updated_at = utcnow()
    if changed or data.metrics.source != "public":
        mt = data.metrics
        db.add(SocialPostMetric(
            post_id=row.id, captured_at=utcnow(), source=mt.source, impressions=mt.impressions, views=mt.views,
            likes=mt.likes, replies=mt.replies, reposts=mt.reposts, quotes=mt.quotes, bookmarks=mt.bookmarks,
            profile_visits=mt.profile_visits, url_clicks=mt.url_clicks, followers_attributed=mt.followers_attributed,
        ))
    db.flush()


def topic_words(text: str, n: int = 3) -> list[str]:
    text = re.sub(r"@\w+|https?://\S+", " ", text)
    words = [w for w in _WORD.findall(text.lower()) if w not in STOPWORDS and not w.startswith("http")]
    return [w for w, _ in Counter(words).most_common(n)]


def record_interaction(
    db: Session, profile: SocialProfile, post: SocialPost, kind: InteractionType, direction: str, at: datetime | None
) -> None:
    """Log one public interaction and update the (non-sensitive) relationship summary."""
    exists = db.scalar(select(Interaction.id).where(
        Interaction.platform == profile.platform, Interaction.external_id == post.external_id, Interaction.type == kind,
    ))
    if exists:
        return
    db.add(Interaction(
        platform=profile.platform, type=kind, post_id=post.id, external_id=post.external_id, author_handle=profile.username,
        text=post.text[:2000], direction=direction, occurred_at=at, profile_id=profile.id,
    ))
    rel = db.scalar(select(SocialRelationship).where(SocialRelationship.profile_id == profile.id))
    if rel is None:
        rel = SocialRelationship(profile_id=profile.id, topics={}, interaction_count=0, our_reply_count=0, mention_count=0, reply_count=0)
        db.add(rel)
    rel.interaction_count += 1
    if direction == "outbound":
        rel.our_reply_count += 1
    elif kind == InteractionType.MENTION:
        rel.mention_count += 1
    elif kind == InteractionType.REPLY:
        rel.reply_count += 1
    if at is not None:
        rel.first_interaction_at = min(rel.first_interaction_at, at) if rel.first_interaction_at else at
        rel.last_interaction_at = max(rel.last_interaction_at, at) if rel.last_interaction_at else at
    topics = dict(rel.topics or {})
    for w in topic_words(post.text):
        topics[w] = topics.get(w, 0) + 1
    rel.topics = dict(sorted(topics.items(), key=lambda kv: kv[1], reverse=True)[:15])
    db.flush()
