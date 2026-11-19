"""Editorial intelligence (owner, 2026-11-19): the AI Editor-in-Chief in shadow (no paid call without approval), one-click story feedback,
persistent story memory across lanes with X Intelligence leads, normalized post performance, photo gaps and the optional second look."""
import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.newsdesk import NdEvent, NdPost, NdStoryline, NdValidation
from app.models.trending import TtTopic
from app.newsdesk import chief, feedback, memory, newsworthy, performance, validation


def event(db: Session, title: str, **kw: Any) -> NdEvent:
    e = NdEvent(key=f"story:{title[:60]}:{kw.get('status', 'candidate')}", kind="news", title=title, headline=title, summary=kw.pop("summary", ""),
                evidence=kw.pop("evidence", "two outlets"), sources=kw.pop("sources", [{"name": "Reuters", "url": "https://r"}, {"name": "AP", "url": "https://a"}]),
                first_seen=utcnow(), **kw)
    db.add(e)
    db.commit()
    return e


def boom(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"no paid call may be made while the AI editor is off: {request.url}")


ON = SimpleNamespace(newsdesk_chief_enabled=True, newsdesk_chief_model="claude-opus-5-5", newsdesk_chief_daily_usd=1.0, newsdesk_chief_effort="low")
OFF = SimpleNamespace(newsdesk_chief_enabled=False, newsdesk_chief_model="claude-opus-5-5", newsdesk_chief_daily_usd=1.0, newsdesk_chief_effort="low")


# ------------------------------------------------------------------------------------------------------------------ AI editor
async def test_the_ai_editor_is_off_by_default_and_never_calls(db: Session) -> None:
    from app.config.settings import Settings

    assert Settings().newsdesk_chief_enabled is False
    e = event(db, "Binance receives Abu Dhabi court order to freeze assets after $6.3B heist")
    chief.shortlist(db, e, {"decision": "PUBLISH", "interest": 90, "why": "the $6.3B heist case", "evidence": "two outlets"}, None, settings=OFF)
    db.commit()
    row = db.scalar(select(NdValidation).where(NdValidation.kind == "chief"))
    assert row.data["status"] == "off" and "$6.3B heist" in row.data["packet"] and row.data["tokens_in_est"] > 300
    assert "PUBLISH" not in row.data["packet"] and "interest" not in row.data["packet"]  # an independent judgment: no gate verdict shown
    assert set(row.data["cost_est"]) == set(chief.PRICES)
    out = await chief.tick(db, settings=OFF, transport=httpx.MockTransport(boom))
    assert out["reviewed"] == 0 and out["off"].startswith("off")
    ev = chief.evaluation(db, settings=OFF)
    assert ev["enabled"] is False and ev["shortlisted"] == 1 and ev["cost"]["by_model"]["claude-opus-5-5"]["per_review"] > 0


async def test_when_approved_it_reviews_in_shadow_within_its_cap(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.security import credentials

    monkeypatch.setattr(credentials, "resolve", lambda name, settings=None: "chief-key" if name == "newsdesk_chief_api_key" else None)
    seen: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        assert request.headers["x-api-key"] == "chief-key" and body["output_config"]["format"]["type"] == "json_schema"
        verdict = {"verdict": "publish", "why_care": "Binance customers learn a court has frozen assets tied to the $6.3B heist, so recovery is now possible.",
                   "scores": dict.fromkeys(chief.SCORES, 8), "risks": [], "best_angle": "a court froze the stolen funds"}
        return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps(verdict)}], "usage": {"input_tokens": 1200, "output_tokens": 400},
                                         "stop_reason": "end_turn"})

    e = event(db, "Binance receives Abu Dhabi court order to freeze assets after $6.3B heist")
    chief.shortlist(db, e, {"decision": "PUBLISH", "evidence": "two outlets"}, None, settings=ON)
    db.commit()
    out = await chief.tick(db, settings=ON, transport=httpx.MockTransport(answer))
    row = db.scalar(select(NdValidation).where(NdValidation.kind == "chief"))
    assert out["reviewed"] == 1 and row.data["status"] == "reviewed" and row.data["verdict"] == "publish" and row.data["problems"] == []
    assert row.data["cost_usd"] == pytest.approx(1200 / 1e6 * 4 + 400 / 1e6 * 20)
    assert db.get(NdEvent, e.id).status == "candidate"  # shadow: nothing about the event changed
    # the hard daily cap
    for i in range(3):
        x = event(db, f"Second story number {i} about Tether reserves")
        chief.shortlist(db, x, {"decision": "REJECT", "evidence": "two outlets"}, None, settings=ON)
    db.commit()
    tight = SimpleNamespace(**{**vars(ON), "newsdesk_chief_daily_usd": chief.spent_today(db, utcnow()) + 0.001})
    out = await chief.tick(db, settings=tight, transport=httpx.MockTransport(answer))
    assert out["reviewed"] == 0 and "cap" in out["capped"]


@pytest.mark.parametrize("why,evidence,verdict,problem", [
    ("This is a significant development that markets may react to in the coming days.", "two outlets", "invalid", "generic"),
    ("Binance customers learn a court froze $9B tied to the heist, so recovery is possible.", "two outlets", "publish", "figure not in the sources"),
    ("Binance customers learn a court froze assets tied to the $6.3B heist, so recovery is possible.", "single outlet", "skip", "unverified"),
    ("Binance customers learn a court froze assets tied to the $6.3B heist, so recovery is possible.", "two outlets", "publish", ""),
])
def test_the_editor_must_give_a_specific_true_reason_and_cannot_beat_the_evidence(why: str, evidence: str, verdict: str, problem: str) -> None:
    text = "HEADLINE: Binance receives Abu Dhabi court order to freeze assets after $6.3B heist\nEVIDENCE: two outlets"
    out = chief.check({"verdict": "publish", "why_care": why, "scores": {}, "risks": []}, text, evidence)
    assert out["verdict"] == verdict
    assert any(problem in p for p in out["problems"]) if problem else out["problems"] == []


# ------------------------------------------------------------------------------------------------------------------ feedback
def test_one_click_feedback_is_stored_against_the_real_decision(db: Session) -> None:
    out = event(db, "Saudi-led coalition intercepts Houthi missiles targeting Riyadh", status="released", tg_status="delivered",
                facts={"visual": {"kind": "photo", "library": "riyadh", "credit_line": "Photo: X"}})
    skipped = event(db, "Archaeologists unearth 2,000-year-old Roman gold coin hoard")
    validation.record_candidate(db, skipped, newsworthy.shadow(skipped.title, "", outlets=2, evidence="two outlets"))
    db.commit()
    assert feedback.give(db, f"event:{out.id}", "boring")["verdicts"] == ["boring"]
    assert feedback.give(db, f"event:{out.id}", "great")["verdicts"] == ["great"]  # great and boring exclude each other
    assert feedback.give(db, f"event:{out.id}", "wrong_image")["verdicts"] == ["great", "wrong_image"]
    assert feedback.give(db, f"event:{out.id}", "wrong_image")["verdicts"] == ["great"]  # a second click removes it
    with pytest.raises(feedback.FeedbackError, match="published"):
        feedback.give(db, f"event:{out.id}", "missed")
    with pytest.raises(feedback.FeedbackError, match="picture"):
        feedback.give(db, f"event:{skipped.id}", "wrong_image")
    feedback.give(db, f"event:{skipped.id}", "missed")
    s = feedback.summary(db)
    assert s["counts"] == {"great": 1, "boring": 0, "wrong_image": 0, "missed": 1}
    missed = next(r for r in s["rows"] if r["verdict"] == "missed")
    assert missed["gate"] == "REJECT" and missed["published"] is False


# ------------------------------------------------------------------------------------------------------------------ story memory
def test_story_memory_spans_lanes_and_knows_what_is_new(db: Session) -> None:
    t0 = utcnow() - timedelta(days=9)  # older than the 7-day posting history the repeat gate reads
    memory.remember(db, "telegram", "501", "Hackers drain $400M from Bybit exchange", "", t0)
    memory.remember(db, "x-newsdesk", "190", "Hackers drain $400M from Bybit exchange", "", t0 + timedelta(minutes=20))
    db.commit()
    assert len(list(db.scalars(select(NdStoryline)))) == 1
    same = memory.followup(db, "Bybit confirms hackers drained $400M from its hot wallet")
    assert same["verdict"] == "follow-up with nothing substantially new" and same["told_on"] == ["telegram", "x-newsdesk"]
    more = memory.followup(db, "Bybit hack losses rise to $1.5B")
    assert more["verdict"] == "follow-up with new information" and "1,500M" in more["new"]
    assert memory.followup(db, "Nvidia beats earnings estimates")["verdict"] == "new story"
    assert memory.disagrees(same, "PUBLISH").startswith("story memory") and memory.ENFORCE is False


def test_x_leads_are_told_apart_from_verified_facts(db: Session) -> None:
    now = utcnow()
    t = TtTopic(key="k1", title="Explosion reported at Rotterdam port terminal", category="world", first_seen=now, last_seen=now,
                sources=[{"source": "x_accounts", "outlet": "osint_watch", "title": "Explosion at Rotterdam port", "at": (now - timedelta(minutes=50)).isoformat()},
                         {"source": "news", "outlet": "Reuters", "reliable": True, "title": "Explosion reported at Rotterdam port terminal", "at": (now - timedelta(minutes=12)).isoformat()}])
    lead_only = TtTopic(key="k2", title="Unconfirmed: power cut across Lisbon metro", category="world", first_seen=now, last_seen=now,
                        sources=[{"source": "x_accounts", "outlet": "pt_alerts", "title": "Power cut across Lisbon metro", "at": now.isoformat()}])
    db.add_all([t, lead_only])
    db.commit()
    assert memory.watch_leads(db, [t, lead_only], now) == 2
    s = memory.summary(db)
    assert s["leads"] == {**s["leads"], "seen": 2, "confirmed": 1, "unconfirmed": 1, "median_lead_minutes": 38}
    assert {"account": "@osint_watch", "leads": 1, "confirmed": 1} in s["leads"]["accounts"]


# ------------------------------------------------------------------------------------------------------------------ performance
def test_performance_is_normalized_and_bait_is_never_a_lesson(db: Session) -> None:
    now = utcnow()
    for i, (imp, likes, topic, text) in enumerate([(4000, 80, "macro", "Fed cuts rates by 50 bp"), (1000, 10, "macro", "UK inflation 3.1%"),
                                                   (1500, 12, "macro", "ECB holds rates"), (9000, 400, "hack", "YOU WON'T BELIEVE this hack!!")]):
        db.add(NdPost(kind="news", category=topic, text=text, key=f"p{i}", status="published", published_at=now - timedelta(hours=10), x_id=str(100 + i),
                      facts={"topic": topic}, metrics={"impression_count": imp, "like_count": likes, "read_at": now.isoformat()}))
    db.add(NdPost(kind="news", category="macro", text="no metrics yet", key="p9", status="published", published_at=now, metrics={}))
    db.commit()
    m = performance.measure(db, now)
    assert m["measured"] == 4 and m["excluded_bait_or_boring"] == 1 and "not measurable" in m["telegram"]
    best = m["best"][0]
    assert best["bait"] is True and best["lesson"] is False  # it did best, and it teaches nothing
    assert [(g["topic"], g["posts"]) for g in m["by_topic"]] == [("macro", 3)]  # the bait post's topic teaches nothing
    fed = next(r for r in m["best"] if r["text"].startswith("Fed"))
    assert fed["engagement"] > 1 and fed["reach"] > 1  # above the typical post on both counts


# ------------------------------------------------------------------------------------------------------------------ photography
def test_photo_gaps_name_the_subjects_the_library_lacks() -> None:
    assert validation.photo_subject("Trump tells Xi the US and China will sign a trade deal")["library"] in ("trump_xi", "trump", "xi")
    gap = validation.photo_subject("Equinor shuts Johan Sverdrup field after power failure")
    assert gap["library"] == "" and gap["gap"]


async def test_the_second_look_does_nothing_while_vision_is_off(db: Session) -> None:
    assert await validation.second_look(db, "telegram", "1", b"jpeg", "Gold falls 3%", {"kind": "photo", "library": "gold"},
                                        transport=httpx.MockTransport(boom)) is None


# ------------------------------------------------------------------------------------------------------------------ the Terminal
def test_terminal_feedback_insight_and_the_validation_page(client: Any, db: Session) -> None:
    e = event(db, "Archaeologists unearth 2,000-year-old Roman gold coin hoard")
    r = client.post("/api/terminal/feedback", json={"target": f"event:{e.id}", "verdict": "missed"})
    assert r.status_code == 200 and r.json()["verdicts"] == ["missed"]
    assert client.post("/api/terminal/feedback", json={"target": f"event:{e.id}", "verdict": "wrong_image"}).status_code == 409
    assert client.post("/api/terminal/feedback", json={"target": "nope", "verdict": "great"}).status_code == 422
    i = client.get(f"/api/terminal/insight?target=event:{e.id}").json()
    assert i["feedback"] == ["missed"] and i["published"] is False
    v = client.get("/api/terminal/validation").json()
    assert v["chief"]["enabled"] is False and v["feedback"]["counts"]["missed"] == 1 and "leads" in v["memory"] and "telegram" in v["performance"]
    assert v["vision"] == "off" and "photo_gaps" in v
