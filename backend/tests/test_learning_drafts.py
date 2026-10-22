from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.community.merging import merge_topics, split_topic, suggest_merges
from app.community.ranking import WEIGHTS, rank_opportunities
from app.community.topics import refresh_topic_stats
from app.content.drafts import SPECS, checks_for
from app.db.base import utcnow
from app.learning.feedback import PreferenceModel, record_feedback
from app.models.agent import AgentConfig
from app.models.community import CommunityTopic, ContentOpportunity
from app.models.enums import (
    DraftType,
    FeedbackDecision,
    FeedbackTarget,
    MergeStatus,
    OpportunityStatus,
    ReviewStatus,
    TopicStatus,
    Urgency,
)
from app.models.memory import Memory
from app.models.social_intel import TopicMergeSuggestion
from app.models.telegram import TelegramMessage, TelegramSource
from tests.conftest import FakeEmbedder, FakeProvider
from tests.test_community import FUNDING, NOW, TUNING, add, processed, router


def _opp(db: Session, title: str, angle: str = "explainer", status: OpportunityStatus = OpportunityStatus.DETECTED, **kw) -> ContentOpportunity:  # type: ignore[no-untyped-def]
    o = ContentOpportunity(title=title, why="why", angle=angle, evidence={"messages": 5, "participants": 3}, platforms=["x"],
                           urgency=Urgency.MEDIUM, confidence=0.6, status=status, origin_kind=kw.pop("origin_kind", "manual"), **kw)
    db.add(o)
    db.commit()
    return o


@pytest.fixture
def source(db: Session, agent_config: AgentConfig) -> TelegramSource:
    s = TelegramSource(chat_id=-100111, display_name="Community")
    db.add(s)
    db.commit()
    return s


# --- feedback & preferences -------------------------------------------------------------------------


async def test_feedback_applies_decisions_and_is_recorded(db: Session, agent_config: AgentConfig) -> None:
    mem = Memory(agent_id=agent_config.id, layer="community", kind="community_insight", content="Members ask about fees",
                 review_status=ReviewStatus.PENDING, importance=0.5, tags=["concern"], source_type="telegram", extra={"insight_type": "concern"})
    db.add(mem)
    db.commit()
    emb = FakeEmbedder(dims=256)
    await record_feedback(db, FeedbackTarget.MEMORY, mem.id, FeedbackDecision.APPROVE, emb)
    assert mem.review_status == ReviewStatus.APPROVED
    await record_feedback(db, FeedbackTarget.MEMORY, mem.id, FeedbackDecision.IMPORTANT, emb)
    assert mem.importance >= 0.9
    ev = await record_feedback(db, FeedbackTarget.MEMORY, mem.id, FeedbackDecision.EDIT, emb, edits={"content": "Members ask about trading fees"})
    assert mem.content == "Members ask about trading fees" and "insight:concern" in ev.features and ev.vector is not None

    opp = _opp(db, "Bitcoin market structure explained")
    await record_feedback(db, FeedbackTarget.OPPORTUNITY, opp.id, FeedbackDecision.REJECT, emb, note="too generic")
    assert opp.status == OpportunityStatus.REJECTED


async def test_preferences_learn_from_repeated_decisions(db: Session, agent_config: AgentConfig) -> None:
    emb = FakeEmbedder(dims=256)
    for title in ("Bitcoin market structure basics", "Unusual market structure in bitcoin", "Market structure: what changed"):
        o = _opp(db, title, angle="educational market structure explainer")
        await record_feedback(db, FeedbackTarget.OPPORTUNITY, o.id, FeedbackDecision.APPROVE, emb)
    for title in ("Bitcoin price prediction for next week", "Will bitcoin price hit a new high", "Price prediction thread"):
        o = _opp(db, title, angle="price prediction")
        await record_feedback(db, FeedbackTarget.OPPORTUNITY, o.id, FeedbackDecision.REJECT, emb)

    prefs = PreferenceModel(db, "opportunity")
    weights = {w.feature: w.weight for w in prefs.table()}
    assert weights["keyword:structure"] > 0.5 and weights["keyword:prediction"] < -0.5
    good = prefs.score(["keyword:market", "keyword:structure"])
    bad = prefs.score(["keyword:price", "keyword:prediction"])
    assert good.score > 0 > bad.score
    assert any("approved" in r for r in good.reasons) and any("rejected" in r for r in bad.reasons)


def test_feedback_api_validation(client: TestClient) -> None:
    assert client.post("/api/feedback/opportunity/999", json={"decision": "approve"}).status_code == 404
    assert client.post("/api/feedback/nonsense/1", json={"decision": "approve"}).status_code == 422
    assert client.get("/api/feedback/preferences").json() == []


# --- ranking ------------------------------------------------------------------------------------------


async def test_ranking_exposes_separate_signals_and_reasons(db: Session, source: TelegramSource, fake_provider: FakeProvider, agent_config: AgentConfig) -> None:
    emb = FakeEmbedder(dims=512)
    await processed(db, source, fake_provider, emb, FUNDING, start=utcnow() - timedelta(hours=2))
    refresh_topic_stats(db, TUNING)
    topic = db.scalar(select(CommunityTopic).where(CommunityTopic.status == TopicStatus.ACTIVE))
    assert topic is not None
    o = _opp(db, "Why funding rates are spiking", angle="funding rates explainer", origin_kind="topic", origin_id=topic.id, content_potential=0.8)
    assert await rank_opportunities(db, agent_config.id, emb) == 1
    db.refresh(o)
    assert set(o.signals) == set(WEIGHTS)
    assert all(0.0 <= v <= 1.0 for v in o.signals.values())
    assert o.priority_label in ("high", "medium", "low") and o.ranked_at is not None
    assert "4 messages from 4 participants" in o.reasons
    assert any("volume" in r for r in o.reasons)

    o.user_importance = 1.0
    db.commit()
    before = o.priority
    await rank_opportunities(db, agent_config.id, emb)
    db.refresh(o)
    assert o.priority > before and "marked important by you" in o.reasons


# --- topic merge / split ----------------------------------------------------------------------------


async def test_merge_suggestions_never_auto_merge(db: Session, source: TelegramSource, fake_provider: FakeProvider) -> None:
    emb = FakeEmbedder(dims=512)
    db.add_all([
        CommunityTopic(name="BTC", description="btc talk", keywords=["btc", "price"], status=TopicStatus.ACTIVE, centroid=None,
                       first_seen_at=NOW, last_seen_at=NOW, message_count=5, embedding_model=emb.model),
        CommunityTopic(name="Bitcoin price", description="bitcoin price", keywords=["bitcoin", "price"], status=TopicStatus.ACTIVE, centroid=None,
                       first_seen_at=NOW, last_seen_at=NOW, message_count=3, embedding_model=emb.model),
    ])
    db.commit()
    import numpy as np

    from app.community.vectors import normalise, to_blob

    a, b = list(db.scalars(select(CommunityTopic)))
    base = normalise(np.ones(512))
    a.centroid, b.centroid = to_blob(base), to_blob(normalise(base + 0.05 * np.eye(512)[0]))
    db.commit()
    report = await suggest_merges(db, router(db, fake_provider))
    assert report.suggested == 1
    s = db.scalar(select(TopicMergeSuggestion))
    assert s is not None and s.status == MergeStatus.PENDING and s.verdict == "same" and "Semantic similarity" in s.reason
    assert {t.status for t in db.scalars(select(CommunityTopic))} == {TopicStatus.ACTIVE}  # nothing merged yet
    assert (await suggest_merges(db, router(db, fake_provider))).suggested == 0  # not re-suggested


async def test_merge_and_split_preserve_messages(db: Session, source: TelegramSource, fake_provider: FakeProvider) -> None:
    emb = FakeEmbedder(dims=512)
    await processed(db, source, fake_provider, emb, FUNDING + ["the mobile app login screen is broken",
                    "the mobile app login screen is broken again", "the mobile app login screen is broken for me"], start=utcnow() - timedelta(hours=1))
    refresh_topic_stats(db, TUNING)
    topics = list(db.scalars(select(CommunityTopic).where(CommunityTopic.status == TopicStatus.ACTIVE).order_by(CommunityTopic.id)))
    assert len(topics) == 2
    keep, gone = topics
    kept = merge_topics(db, TUNING, keep.id, gone.id)
    db.refresh(gone)
    assert gone.status == TopicStatus.MERGED and gone.canonical_id == keep.id and gone.name in kept.aliases
    assert db.scalar(select(TelegramMessage.id).where(TelegramMessage.topic_id == gone.id)) is None
    assert kept.message_count == 7

    login_ids = list(db.scalars(select(TelegramMessage.id).where(TelegramMessage.text.like("%login%"))))
    new = split_topic(db, TUNING, kept.id, login_ids, "Mobile login problems")
    db.refresh(kept)
    assert new.message_count == 3 and kept.message_count == 4 and kept.id in new.related_ids
    with pytest.raises(ValueError):
        split_topic(db, TUNING, kept.id, [login_ids[0]], "wrong topic")  # message isn't in this topic any more
    with pytest.raises(ValueError):
        merge_topics(db, TUNING, kept.id, gone.id)  # already merged


def test_merge_api_keep_separate(x_client: TestClient) -> None:
    assert x_client.get("/api/community/merges").json() == []
    assert x_client.post("/api/community/merges/999/merge").status_code == 404


# --- drafts & variants ---------------------------------------------------------------------------------


def test_draft_checks_flag_length_and_restricted_claims() -> None:
    spec = SPECS[DraftType.X_POST]
    ok = checks_for(spec, "Short and fine.", ["Price predictions or price targets"])
    assert ok == {"length_ok": True, "warnings": []}
    long = checks_for(spec, "x" * 300, [])
    assert long["length_ok"] is False
    risky = checks_for(spec, "This is guaranteed to pump. Price predictions and price targets inside.", ["Price predictions or price targets"])
    assert any("guaranteed" in w for w in risky["warnings"]) and any("restricted claim" in w for w in risky["warnings"])


def test_draft_generation_and_variants_api(x_client: TestClient, db: Session, fake_provider: FakeProvider) -> None:
    opp = _opp(db, "Funding rates explained", status=OpportunityStatus.DETECTED)
    assert x_client.post(f"/api/opportunities/{opp.id}/drafts", json={"draft_type": "x_post"}).status_code == 409  # not approved yet
    x_client.post(f"/api/feedback/opportunity/{opp.id}", json={"decision": "approve"})

    fake_provider.calls.clear()
    one = x_client.post(f"/api/opportunities/{opp.id}/drafts", json={"draft_type": "x_thread"}).json()
    assert len(one) == 1 and one[0]["draft_type"] == "x_thread" and one[0]["status"] == "draft"
    ctx = one[0]["context"]
    assert ctx["why"].startswith(f"Created from content opportunity #{opp.id}")
    assert ctx["generated_by"]["model"] == "strong-model" and "checks" in ctx and "memories_used" in ctx
    system = fake_provider.calls[-1][0][0].content
    assert "280 characters" in system and "## Brand profile" in system

    three = x_client.post(f"/api/opportunities/{opp.id}/drafts", json={"draft_type": "youtube_titles", "variants": 3}).json()
    assert [d["variant_label"] for d in three] == ["search-oriented", "curiosity-oriented", "authority-oriented"]
    assert len({d["variant_group"] for d in three}) == 1
    assert fake_provider.models_used()[-1] == "strong-model"  # content.variants → strong tier

    shorts = x_client.post(f"/api/opportunities/{opp.id}/drafts", json={"draft_type": "short_hook", "variants": 3}).json()
    assert [d["variant_label"] for d in shorts] == ["curiosity hook", "direct hook", "contrarian hook"]
    assert len(x_client.get("/api/drafts", params={"opportunity_id": opp.id}).json()) == 7
    assert x_client.get(f"/api/opportunities/{opp.id}").json()["opportunity"]["status"] == "drafted"
