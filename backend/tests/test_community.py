from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.price_chat import AgentRequest
from app.ai.routing import ModelRouter, ModelTier, set_route
from app.community import provenance
from app.community.extraction import extract_memories
from app.community.opportunities import detect_opportunities, draft_opportunity
from app.community.processing import process_pending
from app.community.questions import canonicalise_questions, recurring_questions, refresh_question_stats
from app.community.search import CommunitySearch, answer_from_evidence, parse_time_window
from app.community.summaries import generate_summary, run_daily_summaries
from app.community.topics import compute_trend, name_topics, refresh_topic_stats
from app.community.tuning import CommunityTuning
from app.config.settings import Settings
from app.memory.store import APPROVED_ONLY, MemoryStore
from app.models.agent import AgentConfig
from app.models.community import CommunityQuestion, CommunitySummary, CommunityTopic, ContentOpportunity
from app.models.content import Content
from app.models.enums import (
    ContentChannel,
    ContentStatus,
    DerivedKind,
    MemoryLayer,
    MessageCategory,
    OpportunityStatus,
    ReviewStatus,
    SummaryKind,
    TopicStatus,
    TopicTrend,
)
from app.models.memory import Memory
from app.models.telegram import TelegramMessage, TelegramSource
from app.providers.errors import ProviderUnavailableError
from tests.conftest import FakeEmbedder, FakeProvider

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
TUNING = CommunityTuning()

FUNDING = [
    "funding rates are spiking on bitcoin today",
    "bitcoin funding rates are spiking again",
    "why are funding rates spiking on bitcoin",
    "funding rates on bitcoin are spiking hard today",
]
QUESTIONS = [
    "what does price chat actually do?",
    "what does price chat do exactly?",
    "so what does price chat actually do?",
]


@pytest.fixture
def source(db: Session, agent_config: AgentConfig) -> TelegramSource:
    s = TelegramSource(chat_id=-100111, display_name="Community")
    db.add(s)
    db.commit()
    return s


def add(db: Session, source: TelegramSource, texts: list[str], start: datetime = NOW - timedelta(hours=2), authors: list[int] | None = None) -> list[TelegramMessage]:
    rows = []
    base = int(db.scalar(select(TelegramMessage.message_id).order_by(TelegramMessage.message_id.desc()).limit(1)) or 0)
    for i, text in enumerate(texts):
        author = (authors or list(range(1, len(texts) + 1)))[i]
        rows.append(TelegramMessage(
            source_id=source.id, chat_id=source.chat_id, message_id=base + i + 1, author_id=author,
            author_name=f"Person{author}", sent_at=start + timedelta(minutes=i), text=text,
        ))
    db.add_all(rows)
    db.commit()
    return rows


def router(db: Session, provider: FakeProvider) -> ModelRouter:
    return ModelRouter(db, Settings(), provider)


async def processed(db: Session, source: TelegramSource, provider: FakeProvider, embedder: FakeEmbedder, texts: list[str], **kw) -> list[TelegramMessage]:  # type: ignore[no-untyped-def]
    rows = add(db, source, texts, **kw)
    await process_pending(db, router(db, provider), embedder, TUNING)
    return rows


# --- routing ------------------------------------------------------------------------------------


def test_routing_defaults_and_overrides(db: Session, fake_provider: FakeProvider) -> None:
    r = router(db, fake_provider)
    assert r.resolve("telegram.classify").model == "fast-model"
    assert r.resolve("community.summary.daily").model == "strong-model"
    assert r.resolve("content.opportunity").tier == ModelTier.STRONG
    set_route(db, "telegram.classify", "strong", None, None)
    assert r.resolve("telegram.classify").model == "strong-model"
    set_route(db, "telegram.classify", None, "gemma3:27b", 0.5)
    decision = r.resolve("telegram.classify")
    assert (decision.model, decision.temperature, decision.customised) == ("gemma3:27b", 0.5, True)
    set_route(db, "telegram.classify", None, None, None)
    assert r.resolve("telegram.classify").customised is False


async def test_strong_model_missing_falls_back_to_fast(db: Session, fake_provider: FakeProvider) -> None:
    from app.providers.base import ChatMessage

    fake_provider.missing = {"strong-model"}
    result, route = await router(db, fake_provider).complete("community.summary.daily", [ChatMessage("user", "hi")])
    assert route.model == "fast-model" and fake_provider.models_used() == ["strong-model", "fast-model"]


# --- processing, topics, questions ---------------------------------------------------------------


async def test_processing_classifies_with_fast_model_and_clusters(db: Session, source: TelegramSource, fake_provider: FakeProvider) -> None:
    emb = FakeEmbedder(dims=512)
    rows = await processed(db, source, fake_provider, emb, [*FUNDING, "gm", "the new mobile app login screen is broken for me"])
    assert set(fake_provider.models_used()) == {"fast-model"}  # classification is routed to the fast tier
    by_text = {m.text: m for m in rows}
    assert by_text["gm"].category == MessageCategory.NOISE and by_text["gm"].topic_id is None
    assert all(m.processed_at is not None and m.classified_by.startswith("model:") for m in rows)
    funding_topics = {by_text[t].topic_id for t in FUNDING}
    assert len(funding_topics) == 1 and None not in funding_topics
    assert by_text["the new mobile app login screen is broken for me"].topic_id not in funding_topics


async def test_processing_falls_back_to_rules_when_model_down(db: Session, source: TelegramSource, fake_provider: FakeProvider) -> None:
    fake_provider.fail = True
    rows = await processed(db, source, fake_provider, FakeEmbedder(dims=512), ["what does price chat actually do?"])
    assert rows[0].classified_by == "rules" and rows[0].is_question and rows[0].processed_at is not None


async def test_embedding_outage_leaves_messages_for_retry(db: Session, source: TelegramSource, fake_provider: FakeProvider) -> None:
    emb = FakeEmbedder(dims=512)
    emb.error = ProviderUnavailableError("embedder down")
    add(db, source, FUNDING[:2])
    with pytest.raises(ProviderUnavailableError):
        await process_pending(db, router(db, fake_provider), emb, TUNING)
    db.rollback()
    assert all(m.processed_at is None for m in db.scalars(select(TelegramMessage)))


async def test_topic_lifecycle_naming_and_stats(db: Session, source: TelegramSource, fake_provider: FakeProvider) -> None:
    emb = FakeEmbedder(dims=512)
    await processed(db, source, fake_provider, emb, FUNDING[:1])
    refresh_topic_stats(db, TUNING, NOW)
    topic = db.scalar(select(CommunityTopic))
    assert topic is not None and topic.status == TopicStatus.CANDIDATE  # one message is not a topic yet

    await processed(db, source, fake_provider, emb, FUNDING[1:], authors=[2, 3, 4])
    refresh_topic_stats(db, TUNING, NOW)
    db.refresh(topic)
    assert topic.status == TopicStatus.ACTIVE
    assert (topic.message_count, topic.participant_count, topic.count_24h) == (4, 4, 4)
    assert topic.trend == TopicTrend.NEW
    assert len(topic.representative_message_ids) == 4 and topic.confidence > 0

    assert await name_topics(db, router(db, fake_provider)) == 1
    assert topic.named_by_model == "fast-model" and topic.description and not topic.needs_naming


@pytest.mark.parametrize(
    ("c24", "p24", "first_hours_ago", "expected"),
    [
        (2, 1, 100, TopicTrend.INSUFFICIENT_DATA),
        (12, 3, 100, TopicTrend.RISING),
        (2, 10, 100, TopicTrend.FALLING),
        (8, 7, 100, TopicTrend.STEADY),
        (5, 0, 3, TopicTrend.NEW),
    ],
)
def test_trend_is_honest_about_small_numbers(c24: int, p24: int, first_hours_ago: int, expected: TopicTrend) -> None:
    assert compute_trend(NOW - timedelta(hours=first_hours_ago), NOW, c24, p24, c24 + p24, 3) == expected


async def test_recurring_questions(db: Session, source: TelegramSource, fake_provider: FakeProvider) -> None:
    emb = FakeEmbedder(dims=512)
    await processed(db, source, fake_provider, emb, [*QUESTIONS, "is the bitcoin halving priced in?"], authors=[1, 2, 2, 3])
    refresh_question_stats(db)
    groups = list(db.scalars(select(CommunityQuestion).order_by(CommunityQuestion.ask_count.desc())))
    assert groups[0].ask_count == 3 and groups[0].asker_count == 2
    assert [q.id for q in recurring_questions(db, TUNING)] == [groups[0].id]  # the one-off question isn't recurring
    assert groups[0].needs_canonical
    assert await canonicalise_questions(db, router(db, fake_provider)) >= 1
    assert groups[0].text.endswith("?") and groups[0].canonical_model == "fast-model"


# --- summaries ------------------------------------------------------------------------------------


async def test_summary_provenance_and_pseudonyms(db: Session, source: TelegramSource, fake_provider: FakeProvider) -> None:
    await processed(db, source, fake_provider, FakeEmbedder(dims=512), [*FUNDING, *QUESTIONS])
    fake_provider.calls.clear()
    summary = await generate_summary(db, router(db, fake_provider), TUNING, SummaryKind.DAILY, NOW - timedelta(days=1), NOW, source=source)
    assert summary is not None
    prompt = "\n".join(m.content for m in fake_provider.calls[0][0])
    assert "Person1" not in prompt and "Member 1" in prompt  # member names never reach the model
    assert fake_provider.models_used() == ["strong-model"]  # daily synthesis → strong tier
    assert (summary.model, summary.provider) == ("strong-model", "fake") and summary.generated_at
    assert summary.data["invalid_citations_dropped"] == 1  # the made-up id was removed
    assert "m999999" not in str(summary.data["themes"])
    linked = provenance.message_ids_for(db, DerivedKind.SUMMARY, summary.id)
    assert len(linked) == 7 and summary.message_count == 7
    assert "### What the community is talking about" in summary.body


async def test_summary_skipped_without_enough_messages(db: Session, source: TelegramSource, fake_provider: FakeProvider) -> None:
    add(db, source, ["one lonely message here"])
    assert await generate_summary(db, router(db, fake_provider), TUNING, SummaryKind.RECENT, NOW - timedelta(hours=6), NOW, source=source) is None
    assert fake_provider.calls == []


async def test_daily_summary_once_per_day(db: Session, source: TelegramSource, fake_provider: FakeProvider) -> None:
    add(db, source, FUNDING + QUESTIONS, start=NOW.replace(hour=0) - timedelta(hours=10))
    r = router(db, fake_provider)
    assert len(await run_daily_summaries(db, r, TUNING, NOW)) == 1
    assert await run_daily_summaries(db, r, TUNING, NOW) == []


# --- memory extraction & review ------------------------------------------------------------------


async def test_memory_extraction_pending_review_with_provenance(db: Session, source: TelegramSource, fake_provider: FakeProvider, agent_config: AgentConfig) -> None:
    emb = FakeEmbedder(dims=512)
    await processed(db, source, fake_provider, emb, [*QUESTIONS, *FUNDING])
    r = router(db, fake_provider)
    await generate_summary(db, r, TUNING, SummaryKind.RECENT, NOW - timedelta(hours=6), NOW, source=source)
    report = await extract_memories(db, r, agent_config.id, emb)

    assert report.created == 1  # the unsupported insight (bogus message id) was not remembered
    memory = db.get(Memory, report.memory_ids[0])
    assert memory is not None
    assert (memory.layer, memory.review_status) == (MemoryLayer.COMMUNITY, ReviewStatus.PENDING)
    assert memory.importance == 0.7  # AI importance is capped; humans can raise it
    assert memory.extra["generated_by"]["model"] == "strong-model" and memory.extra["confidence"] == 0.8
    assert len(provenance.message_ids_for(db, DerivedKind.MEMORY, memory.id)) == 2
    assert db.scalar(select(CommunitySummary.memories_extracted_at)) is not None

    # Pending memories are invisible to the agent until approved.
    store = MemoryStore(db, agent_config.id)
    assert store.count(APPROVED_ONLY) == 0
    store.update(memory.id, {"review_status": ReviewStatus.APPROVED})
    assert store.count(APPROVED_ONLY) == 1

    # Re-extraction never resets a human decision.
    assert (await extract_memories(db, r, agent_config.id, emb)).created == 0
    db.refresh(memory)
    assert memory.review_status == ReviewStatus.APPROVED


# --- opportunities ------------------------------------------------------------------------------


async def test_opportunities_from_topics_with_evidence(db: Session, source: TelegramSource, fake_provider: FakeProvider, agent_config: AgentConfig) -> None:
    emb = FakeEmbedder(dims=512)
    await processed(db, source, fake_provider, emb, FUNDING)
    refresh_topic_stats(db, TUNING, NOW)
    r = router(db, fake_provider)
    report = await detect_opportunities(db, r, agent_config.id, TUNING, now=NOW)
    assert report.created == 1
    opp = db.get(ContentOpportunity, report.ids[0])
    assert opp is not None and opp.status == OpportunityStatus.DETECTED
    assert opp.evidence["messages"] == 4 and opp.evidence["participants"] == 4 and opp.evidence["signal"] == "topic"
    assert opp.platforms == ["x", "youtube_shorts"] and opp.model == "strong-model"
    assert len(provenance.message_ids_for(db, DerivedKind.OPPORTUNITY, opp.id)) == 3

    # Cooldown: the same topic is not re-evaluated immediately.
    assert (await detect_opportunities(db, r, agent_config.id, TUNING, now=NOW)).evaluated == 0

    content = await draft_opportunity(db, r, agent_config.id, opp, ContentChannel.X)
    assert content.status == ContentStatus.DRAFT and content.extra["opportunity_id"] == opp.id
    assert opp.status == OpportunityStatus.DRAFTED and opp.content_id == content.id
    system_prompt = fake_provider.calls[-1][0][0].content
    assert "280 characters" in system_prompt  # X platform rules applied
    assert db.scalar(select(Content.status)) == ContentStatus.DRAFT  # drafted, never published


async def test_unworthy_signal_is_set_aside(db: Session, source: TelegramSource, fake_provider: FakeProvider, agent_config: AgentConfig) -> None:
    await processed(db, source, fake_provider, FakeEmbedder(dims=512), ["the sunset photo in the chat looks amazing tonight"] * 1 + ["the sunset photo in the chat looks amazing really"] * 1 + ["that sunset photo in the chat looks amazing tonight"], authors=[1, 2, 3])
    refresh_topic_stats(db, TUNING, NOW)
    report = await detect_opportunities(db, router(db, fake_provider), agent_config.id, TUNING, now=NOW)
    assert report.skipped == 1 and report.created == 0
    opp = db.scalar(select(ContentOpportunity))
    assert opp is not None and opp.status == OpportunityStatus.REJECTED and "set aside" in opp.notes


# --- search -------------------------------------------------------------------------------------


def test_time_windows() -> None:
    assert parse_time_window("biggest topics yesterday?", NOW).label == "yesterday"
    w = parse_time_window("what did people say this week", NOW)
    assert w.start == datetime(2026, 9, 28, tzinfo=timezone.utc)
    assert parse_time_window("last 3 days", NOW).start == NOW - timedelta(days=3)
    assert parse_time_window("bitcoin", NOW).start is None


async def test_search_returns_traceable_evidence_and_grounded_answer(db: Session, source: TelegramSource, fake_provider: FakeProvider, agent_config: AgentConfig) -> None:
    emb = FakeEmbedder(dims=512)
    await processed(db, source, fake_provider, emb, [*FUNDING, "the new mobile app login screen is broken for me"])
    refresh_topic_stats(db, TUNING, NOW)
    results = await CommunitySearch(db, agent_config.id, emb).search("What has the community said about bitcoin funding rates?")
    texts = [h.message.text for h in results.messages]
    assert texts and all("funding" in t for t in texts[:4]) and "login" not in " ".join(texts)
    assert results.topics and results.topics[0][0].status == TopicStatus.ACTIVE

    answer = await answer_from_evidence(router(db, fake_provider), "what about funding?", results)
    assert answer["cited"] and "m999999" not in answer["cited"]  # citations are checked against evidence
    assert answer["model"] == "fast-model"


# --- agent integration -----------------------------------------------------------------------------


async def test_agent_receives_labelled_relevant_community_context(db: Session, source: TelegramSource, fake_provider: FakeProvider, agent_config: AgentConfig) -> None:
    from app.services.agent_service import build_agent
    from app.services.runtime import AppRuntime

    emb = FakeEmbedder(dims=512)
    await processed(db, source, fake_provider, emb, [*FUNDING, *QUESTIONS], authors=[1, 2, 3, 4, 5, 6, 5])
    refresh_topic_stats(db, TUNING, NOW)
    refresh_question_stats(db)
    db.commit()
    runtime = AppRuntime.from_settings(Settings(), fake_provider, emb)
    fake_provider.calls.clear()

    reply = await build_agent(db, runtime, "c-community").respond(AgentRequest("What is the community saying: are bitcoin funding rates spiking today?"))
    system = fake_provider.calls[0][0][0].content
    assert "## Community intelligence" in system
    assert "observations, not brand facts" in system and "never quotes" in system
    assert "Detected discussion topics:" in system and "Questions members keep asking:" in system
    assert "Raw member messages (verbatim, anonymous):" in system
    assert "Person1" not in system  # members are never named to the model
    assert reply.community is not None and reply.community.topics

    fake_provider.calls.clear()
    await build_agent(db, runtime, "c-other").respond(AgentRequest("Write a haiku about owls"))
    assert "## Community intelligence" not in fake_provider.calls[0][0][0].content  # irrelevant → not included


@pytest.mark.parametrize(("raw", "expected"), [(0.4, 0.4), (5, 0.5), (85, 0.85), ("0.7", 0.7), (-2, 0.0), ("high", 0.5)])
def test_model_scores_are_normalised(raw: object, expected: float) -> None:
    from app.ai.structured import unit_score

    assert unit_score(raw) == expected


def test_local_day_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config.settings import get_settings
    from app.timeutil import local_day_start

    monkeypatch.setattr(get_settings(), "timezone", "Asia/Dubai")  # UTC+4
    early = datetime(2026, 10, 3, 1, 0, tzinfo=timezone.utc)  # 05:00 local on 3 Oct
    assert local_day_start(early) == datetime(2026, 10, 2, 20, 0, tzinfo=timezone.utc)
    assert parse_time_window("what happened today", early).start == datetime(2026, 10, 2, 20, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Members get fake DMs. [m15, m16, m17]", "Members get fake DMs."),
        ("The questions ([m6], [m21]) show a need", "The questions show a need"),
        ("Members warn each other (m40).", "Members warn each other."),
        ("Nothing to strip, see item 3", "Nothing to strip, see item 3"),
    ],
)
def test_citations_are_stripped_from_text(raw: str, clean: str) -> None:
    from app.community.formatting import strip_citations

    assert strip_citations(raw) == clean


async def test_unparseable_extraction_does_not_block_later_summaries(db: Session, source: TelegramSource, fake_provider: FakeProvider, agent_config: AgentConfig) -> None:
    emb = FakeEmbedder(dims=512)
    await processed(db, source, fake_provider, emb, [*QUESTIONS, *FUNDING])
    r = router(db, fake_provider)
    await generate_summary(db, r, TUNING, SummaryKind.RECENT, NOW - timedelta(hours=6), NOW, source=source)
    fake_provider.mangle = lambda title, content: "sorry, no JSON today"  # every attempt is unusable

    report = await extract_memories(db, r, agent_config.id, emb)  # used to raise, leaving the summary first in line forever
    assert report.created == 0
    assert db.scalar(select(CommunitySummary.memories_extracted_at)) is not None
