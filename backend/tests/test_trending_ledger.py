"""Production measurement (owner, 2026-11-17): official status feeds, and a ledger for every significant event with first report, first
source, detection, verification and publication (or the reason it never went out)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.newsdesk import NdPost
from app.models.trending import TtPost, TtTopic
from app.newsdesk import incidents
from app.trending import engine, rank, status_feeds

NOW = datetime(2026, 11, 17, 6, 0, tzinfo=timezone.utc)

# --- official status feeds ---------------------------------------------------------------------------------------------------------------


def _sp(impact: str, status: str = "investigating", name: str = "Widespread 5xx errors on the dashboard and API") -> dict:
    return {"page": {"name": "Cloudflare"}, "incidents": [{"id": "abc123", "name": name, "status": status, "impact": impact,
                                                            "started_at": "2026-11-17T05:41:00.000Z", "shortlink": "https://stspg.io/abc",
                                                            "incident_updates": [{"body": "We are investigating elevated errors."}]}]}


def test_a_major_statuspage_incident_is_an_official_item_that_reads_as_an_incident() -> None:
    items = status_feeds.parse_statuspage("Cloudflare", "https://www.cloudflarestatus.com", _sp("major"))
    assert len(items) == 1
    it = items[0]
    assert it["reliable"] and it["extra"]["official"] and it["outlet"] == "Cloudflare status"
    assert it["published_at"] == datetime(2026, 11, 17, 5, 41, tzinfo=timezone.utc)  # the provider's own start time
    inc = incidents.detect(it["title"])
    assert inc is not None and inc.major and "Cloudflare" in inc.entities


def test_minor_and_resolved_statuspage_incidents_are_not_items() -> None:
    assert status_feeds.parse_statuspage("Cloudflare", "https://x", _sp("minor")) == []
    assert status_feeds.parse_statuspage("Cloudflare", "https://x", _sp("none")) == []
    assert status_feeds.parse_statuspage("Cloudflare", "https://x", _sp("critical", status="resolved")) == []


def test_gcp_only_open_high_or_outage_incidents() -> None:
    body = [{"id": "a", "begin": "2026-11-17T05:00:00+00:00", "end": None, "severity": "high", "status_impact": "SERVICE_OUTAGE", "external_desc": "Cloud Run unavailable in us-central1", "uri": "incidents/a"},
            {"id": "b", "begin": "2026-11-17T05:00:00+00:00", "end": None, "severity": "medium", "status_impact": "SERVICE_DISRUPTION", "external_desc": "Slow builds", "uri": "incidents/b"},
            {"id": "c", "begin": "2026-11-16T05:00:00+00:00", "end": "2026-11-16T07:00:00+00:00", "severity": "high", "status_impact": "SERVICE_OUTAGE", "external_desc": "old", "uri": "c"}]
    items = status_feeds.parse_gcp(body, NOW)
    assert [i["external_id"] for i in items] == ["status:google cloud:a"]
    assert incidents.detect(items[0]["title"]).entities == frozenset({"Google"})


def test_aws_rss_keeps_recent_disruptions_only() -> None:
    rss = ("<?xml version='1.0'?><rss version='2.0'><channel>"
           "<item><title><![CDATA[Service disruption: Increased Error Rates]]></title><link>https://status.aws.amazon.com/</link>"
           "<pubDate>Tue, 17 Nov 2026 05:30:00 GMT</pubDate><guid>https://status.aws.amazon.com/#multipleservices-us-east-1_1790000000</guid></item>"
           "<item><title><![CDATA[Service disruption: Increased Error Rates]]></title><pubDate>Tue, 15 Sep 2026 03:27:10 PDT</pubDate><guid>old</guid></item>"
           "<item><title><![CDATA[Informational message: [RESOLVED] Increased latency]]></title><pubDate>Tue, 17 Nov 2026 05:00:00 GMT</pubDate><guid>x</guid></item>"
           "</channel></rss>")
    import re

    items = status_feeds.parse_rss("AWS", rss, NOW, must=re.compile(r"^service (?:disruption|impact)", re.I))
    assert len(items) == 1 and "us-east-1" in items[0]["title"]
    assert "AWS" in incidents.detect(items[0]["title"]).entities


def test_a_status_page_joins_the_same_companys_news_never_another_company() -> None:
    cf = status_feeds.parse_statuspage("Cloudflare", "https://x", _sp("major"))[0]["title"]
    assert incidents.same_incident(cf, "Cloudflare outage takes down thousands of websites")
    assert not incidents.same_incident(cf, "AWS outage hits Amazon customers in us-east-1")


# --- the ledger ----------------------------------------------------------------------------------------------------------------------------


def _topic(db: Session, now: datetime, score: float, **parts) -> TtTopic:
    t = TtTopic(key=f"t:{score}:{now.timestamp()}", title="Cloudflare outage takes down thousands of websites", category="technology", status="active",
                score=score, peak_score=score, signal_ids=[], sources=[], metrics={}, first_seen=now - timedelta(minutes=6), last_seen=now,
                parts={"timeline": {"first_report": (now - timedelta(minutes=19)).isoformat(), "event_time": (now - timedelta(minutes=12)).isoformat(),
                                    "outlets": 2, "verified": True}, **parts})
    db.add(t)
    db.flush()
    return t


def test_the_ledger_records_detection_verification_and_publication(db: Session) -> None:
    now = utcnow()
    s = engine.settings_row(db)
    engine.watch_ledger(db, s, [], now - timedelta(minutes=30))  # the ledger starts before the event
    t = _topic(db, now, 80.0, incident={"major": True, "verified": True, "label": "cloud, email or app outage", "entities": ["Cloudflare"]})
    lines = engine.watch_ledger(db, s, [t], now)
    e = (s.state or {})["ledger"][0]
    assert e["latency_min"]["detect"] == 13 and e["latency_min"]["verify"] == 19 and not e["backfilled"]
    assert lines and "trending ledger: topic" in lines[0] and "detected +13m" in lines[0]
    p = TtPost(topic_id=t.id, category="technology", status="published", text="x", angle="", writer="rules", fingerprint="x", score=80, sources=[],
               source_text="", checks=[], metrics={}, published_at=now + timedelta(minutes=5))
    db.add(p)
    db.commit()
    lines = engine.watch_ledger(db, s, [t], now + timedelta(minutes=5))
    e = (s.state or {})["ledger"][0]
    assert e["latency_min"]["publish"] == 24 and e["via"].startswith("trend post")
    assert "published +24m" in lines[0]
    assert engine.watch_ledger(db, s, [t], now + timedelta(minutes=10)) == []  # nothing changed: no line
    summary = engine.ledger_summary((s.state or {})["ledger"])
    assert summary["measured_live"] == 1 and summary["publish"]["median"] == 24


def test_the_ledger_credits_a_news_desk_post_that_covered_the_story(db: Session) -> None:
    now = utcnow()
    s = engine.settings_row(db)
    engine.watch_ledger(db, s, [], now - timedelta(minutes=30))
    t = _topic(db, now, 78.0)
    nd = NdPost(kind="news", category="politics", priority="breaking", status="published", text="t", key="k1", score=80, facts={}, sources=[], published_at=now - timedelta(minutes=2))
    db.add(nd)
    db.flush()
    db.add(TtPost(topic_id=t.id, category="technology", status="held", text="x", angle="", writer="rules", fingerprint="x", score=78, sources=[], source_text="",
                  checks=[], metrics={}, hold_reason=f"covered by the News Desk (post {nd.id}, published)"))
    db.commit()
    engine.watch_ledger(db, s, [t], now)
    e = (s.state or {})["ledger"][0]
    assert e["via"].startswith("News Desk post") and e["latency_min"]["publish"] == 17


def test_the_ledger_keeps_the_rejection_reason_and_marks_backfilled_events(db: Session) -> None:
    now = utcnow()
    s = engine.settings_row(db)
    t = _topic(db, now - timedelta(hours=2), 76.0)
    db.add(TtPost(topic_id=t.id, category="technology", status="expired", text="x", angle="", writer="rules", fingerprint="x", score=76, sources=[], source_text="",
                  checks=[], metrics={}, note="Went stale before it was sent"))
    db.commit()
    engine.watch_ledger(db, s, [], now)  # found in the database: an event from before the ledger existed
    e = (s.state or {})["ledger"][0]
    assert e["backfilled"] and e["status"] == "expired" and "stale" in e["reason"] and e["verified"] is None


def test_scores_carry_first_report_and_verification() -> None:
    def sig(i, title, outlet, mins, official=False):
        return rank.Sig(id=i, source="status" if official else "news", title=title, outlet=outlet, reliable=True, engagement=1.0,
                        first_seen=NOW - timedelta(minutes=mins - 2), last_seen=NOW, published_at=NOW - timedelta(minutes=mins), extra={"official": official})
    c = rank.cluster([sig(1, "Cloudflare confirms major outage: Widespread 5xx errors on the dashboard and API", "Cloudflare status", 30, official=True),
                      sig(2, "Cloudflare outage takes down thousands of websites", "Reuters", 12)])
    assert len(c) == 1
    _, parts = rank.score(c[0], NOW)
    assert parts["timeline"]["first_report"] == (NOW - timedelta(minutes=30)).isoformat()
    assert parts["timeline"]["verified"] and parts["incident"]["verified"]
