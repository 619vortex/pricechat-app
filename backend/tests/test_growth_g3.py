"""Growth G3 (2026-11-27): claims keep their instrument and coverage, the scoreboard never measures an index on a closed-market day, the
queue replaces older-generator drafts, the editor's top five, and performance intelligence (linking posted Originals, 24 h measurements,
Originals vs News Desk, winning formats, follower estimate). MADE-UP posts and figures only; nothing reads or writes X."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.growth import drafts, editorial, gate, performance, writer
from app.growth.detectors import extremes, insights
from app.growth.detectors.common import us_market_closed
from app.growth.memory import Memory
from app.models.enums import ContentStatus, Platform
from app.models.market_memory import GrDraft
from app.models.newsdesk import NdPost
from app.models.social import SocialPost
from app.models.social_intel import GrowthSnapshot, SocialPostMetric
from tests.test_growth_detectors import AS_OF, crash_tail, walk
from tests.test_growth_g2_more import _year

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


# --- instrument and coverage ---------------------------------------------------------------------------------------------------------


def _rarity():
    mem = Memory.from_points({"px:BTC:close": crash_tail(walk(1000), 30, -0.40)})
    return extremes.rarity_move(mem, AS_OF, ("BTC",))[0]


def test_a_wording_that_drops_the_source_or_coverage_is_refused() -> None:
    f = _rarity()
    since = f.numbers["since"]["text"]
    assert f.required_phrases() == ["Hyperliquid", since]
    move = f.numbers["move"]["text"]
    assert gate.problems(f"Bitcoin {move} in 30 days. Rare in Hyperliquid data since {since}.", f) == []
    bad = gate.problems(f"Bitcoin {move} in 30 days. Rare.", f)
    assert any('must say "Hyperliquid"' in p for p in bad) and any(since in p for p in bad)


def test_the_writer_is_told_what_it_must_keep_without_typing_a_figure() -> None:
    f = _rarity()
    user = writer.build(f)["user"]
    line = next(x for x in user.splitlines() if x.startswith("Must say"))
    assert "Hyperliquid" in line and "{v" in line and f.numbers["since"]["text"] not in line  # the date stays a placeholder


def test_us_market_calendar() -> None:
    assert us_market_closed(date(2026, 11, 26))  # Thanksgiving
    assert not us_market_closed(date(2026, 11, 25))
    assert us_market_closed(date(2026, 11, 28))  # Saturday
    assert us_market_closed(date(2026, 4, 3))  # Good Friday 2026
    assert us_market_closed(date(2026, 7, 3))  # July 4 is a Saturday: observed on Friday
    assert us_market_closed(date(2026, 12, 25)) and us_market_closed(date(2026, 1, 1))
    assert not us_market_closed(date(2026, 11, 27))  # the day after Thanksgiving trades (a short session)


def test_scoreboard_names_perpetuals_and_ends_on_the_last_market_day() -> None:
    thanksgiving = date(2026, 11, 26)
    data = {"px:BTC:close": _year(90000, 60000), "px:ETH:close": _year(3000, 1700), "px:XYZ-GOLD:close": _year(3000, 3600),
            "px:XYZ-NDX:close": _year(25000, 26000)}
    data = {k: v + [(thanksgiving, v[-1][1] * 1.05)] for k, v in data.items()}  # the perps kept trading on the holiday
    f = insights.cross_asset(Memory.from_points(data), thanksgiving)[0]
    assert f.numbers["date"]["text"] == "Nov 25, 2026" and f.as_of == date(2026, 11, 25)
    assert "2026-11-25" in f.calculation and "the last US market day" in f.calculation
    assert all("perpetual" in f.text(i) for i in range(len(f.templates)))
    assert "perpetual" in f.required_phrases() and "Hyperliquid" in f.required_phrases()
    plain = f.text(0).replace("perpetuals", "prices")
    assert any("perpetual" in p for p in gate.problems(plain, f))


def test_drawdown_table_says_the_highs_are_hyperliquid_highs() -> None:
    from tests.test_growth_g2_more import _crash

    coins = ("BTC", "ETH", "SOL", "XRP", "DOGE", "LINK", "AVAX")
    data = {f"px:{c}:close": _crash(k, -0.55 - 0.03 * k) for k, c in enumerate(coins)}
    f = insights.drawdown_table(Memory.from_points(data), AS_OF)[0]
    t = f.text(0)
    assert t.startswith("Bitcoin is ") and "% below its highest close in Hyperliquid data" in t and "−" not in t.split("below")[0]
    assert "Hyperliquid" in f.required_phrases() and f.numbers["since"]["text"] in f.required_phrases()
    assert gate.problems(t, f) == []


# --- the queue: older drafts replaced, never two versions ------------------------------------------------------------------------


def _row(db: Session, key: str, detector: str, generator: str | None, status: str = "pending", writer_name: str = "pc",
         verified: bool = True, subject: str | None = None) -> GrDraft:
    f = _rarity().to_dict() | ({"generator": generator} if generator else {}) | ({"verification": {"ok": True}} if verified else {})
    if subject:
        f["subject"] = subject
    d = GrDraft(finding_key=key, format="extremes", detector=detector, status=status, text="t", original_text="t", writer=writer_name, finding=f,
                chart={}, sources=[], calculation="", score=50, as_of=AS_OF, version=1, image_cache={})
    db.add(d)
    db.commit()
    return d


def test_older_untouched_drafts_are_superseded_or_retired(db: Session) -> None:
    old = _row(db, "rarity:BTC:30d", "rarity", "g2-2")
    funding = _row(db, "funding:MOVE", "funding_extremes", None)
    board = _row(db, "leaderboard:30d", "leaderboard", "g2-1")
    edited = _row(db, "rarity:ETH:30d", "rarity", "g2-1", writer_name="owner")
    approved = _row(db, "drawdown_table:x", "drawdown_table", "g2-1", status="approved")
    keep = _row(db, "seasonality:BTC", "seasonality", "g2-1")
    older = _row(db, "rarity:BTC:30d:2026-11-24", "rarity", "g1", verified=False)  # same detector + subject, older data
    unverified = _row(db, "then_now:BTC", "then_now", "g1", verified=False, subject="BTC-then")
    new = _row(db, "rarity:BTC:30d", "rarity", drafts.GENERATOR)
    out = drafts.supersede(db, [new], NOW)
    assert out == {"superseded": [old.id, older.id], "retired": [funding.id, board.id, unverified.id]}
    db.refresh(unverified)
    assert "before its data was re-read" in unverified.reject_reason
    for d in (old, funding, board, older):
        db.refresh(d)
        assert d.status == "rejected" and d.decided_by == "system"
    assert f"#{new.id}" in old.reject_reason and "major coins only" in funding.reject_reason
    for d, st in ((edited, "pending"), (approved, "approved"), (keep, "pending"), (new, "pending")):
        db.refresh(d)
        assert d.status == st


def test_an_untouched_older_draft_does_not_block_its_redraft(db: Session) -> None:
    f = _rarity()
    _row(db, f.key, "rarity", "g2-2")
    assert [x.key for x in drafts.novelty(db, [f], NOW)] == [f.key]
    _row(db, f.key, "rarity", drafts.GENERATOR)
    assert drafts.novelty(db, [_rarity()], NOW) == []


# --- the editor's shortlist ------------------------------------------------------------------------------------------------------


def _view(i: int, detector: str, subject: str, ok: bool = True, generator: str = drafts.GENERATOR, score: float = 50) -> dict:
    return {"id": i, "detector": detector, "status": "pending", "generator": generator, "verification": {"ok": ok}, "gate_problems": [],
            "writer_pending": False, "score": score, "subject": subject, "finding": {"subject": subject}}


def test_shortlist_ranks_broad_markets_first_one_per_story() -> None:
    views = [_view(1, "rarity", "XRP", score=99), _view(2, "weekly_streak", "BTC"), _view(3, "cross_asset", "cross-asset"),
             _view(4, "fear_aftermath", "FNG-BTC"), _view(5, "sentiment", "FNG"), _view(6, "btc_in_gold", "BTC-gold"),
             _view(7, "drawdown_table", "drawdown-majors"), _view(8, "year_paths", "BTC", ok=False), _view(9, "then_now", "BTC", generator="g2-2")]
    top = editorial.shortlist(views, drafts.GENERATOR)
    assert [t["id"] for t in top] == [2, 3, 4, 6, 7]  # sentiment shares the Fear & Greed story; unverified / older drafts can't be picked
    assert top[0]["rank"] == 1 and top[0]["editorial"]["total"] == 20


def test_measured_engagement_replaces_the_shareability_prior() -> None:
    v = _view(1, "rarity", "BTC")
    assert editorial.score(v)["shareability"] == 3
    s = editorial.score(v, {"rarity": 1.6})
    assert s["shareability"] == 5 and s["measured"]


# --- performance -------------------------------------------------------------------------------------------------------------------


def test_post_links_parse() -> None:
    assert performance.parse_post_id("https://x.com/thepricechat/status/1990000000000000001?s=20") == "1990000000000000001"
    assert performance.parse_post_id("https://twitter.com/i/web/status/123456789") == "123456789"
    assert performance.parse_post_id("1990000000000000001") == "1990000000000000001"
    assert performance.parse_post_id("https://example.com/status/1") is None


def _own(db: Session, x_id: str, text: str, at: datetime, imp: int, eng: int, reposts: int = 0, bookmarks: int = 0) -> SocialPost:
    p = SocialPost(platform=Platform.X, external_id=x_id, text=text, url=f"https://x.com/i/status/{x_id}", is_own=True, status=ContentStatus.PUBLISHED,
                   posted_at=at, referenced=[], source_kinds=["own"], rule_ids=[], metrics={"impressions": imp, "likes": eng, "reposts": reposts,
                                                                                            "bookmarks": bookmarks}, media=[])
    db.add(p)
    db.flush()
    db.add(SocialPostMetric(post_id=p.id, captured_at=at + timedelta(hours=24), source="curve_24h", impressions=imp, likes=eng, replies=0,
                            reposts=reposts, quotes=0, bookmarks=bookmarks, profile_visits=1, url_clicks=0))
    db.commit()
    return p


def _news(db: Session, x_id: str, at: datetime, imp: int, eng: int) -> None:
    db.add(NdPost(kind="news", category="crypto", priority="scheduled", status="published", text=f"news {x_id}", key=f"k{x_id}", facts={},
                  x_id=x_id, published_at=at))
    db.commit()
    _own(db, x_id, f"news {x_id}", at, imp, eng)


def test_report_compares_originals_with_news_and_estimates_followers(db: Session) -> None:
    text = "Bitcoin has closed lower 7 weeks in a row. No losing run this long in Hyperliquid data, which begins in February 2023."
    d = _row(db, "weekly_streak:BTC", "weekly_streak", drafts.GENERATOR, status="approved")
    t0 = NOW - timedelta(days=5)
    d.text, d.created_at = text, t0 - timedelta(hours=6)
    db.commit()
    _own(db, "9001", text + " https://t.co/abc", t0, imp=1000, eng=40, reposts=6, bookmarks=5)  # the owner's post of the draft (with its image link)
    for k in range(4):
        _news(db, f"800{k}", t0 - timedelta(days=1 + k), imp=100, eng=1)
    for h, n in ((0, 50), (24, 58)):  # +8 followers in the 24 h after the Original
        db.add(GrowthSnapshot(kind="x_account", subject="1600", taken_at=t0 + timedelta(hours=h, minutes=-30 if h == 0 else 0), followers=n,
                              source="users_me"))
    db.commit()
    r = performance.report(db, days=30, now=NOW)
    db.refresh(d)
    assert d.finding["posted"]["x_id"] == "9001" and d.finding["posted"]["how"] == "matched"
    g = r["groups"]
    assert g["original"]["posts"] == 1 and g["original"]["median_impressions_24h"] == 1000 and g["original"]["engagement_rate_24h"] == 0.051
    assert g["news"]["posts"] == 4 and g["news"]["engagement_rate_24h"] == 0.01
    assert r["originals"][0]["format"] == "Weekly streak" and r["originals"][0]["draft_id"] == d.id
    assert r["originals"][0]["followers_est"] == 8.0 and r["originals"][0]["followers_window"] == {"change": 8, "posts_in_window": 1}
    assert r["originals_vs_news"]["enough_data"] is False  # one Original isn't a comparison
    news_fmt = next(f for f in r["formats"] if f["group"] == "news")
    assert news_fmt["rank"] == 1 and news_fmt["measured_24h"] == 4
    assert "X does not say which post brought a follower" in r["method"]["followers"]


def test_auto_link_needs_a_close_text_match_and_never_takes_a_desk_post(db: Session) -> None:
    d = _row(db, "k", "rarity", drafts.GENERATOR, status="approved")
    d.text = "Fear & Greed is at 11. In Hyperliquid data since February 2023, it dropped this low in 6 earlier episodes."
    db.commit()
    _own(db, "7001", "Something else entirely about ETF flows today.", NOW - timedelta(hours=3), 10, 0)
    _news(db, "7002", NOW - timedelta(hours=2), 10, 0)
    db.get(SocialPost, db.query(SocialPost).filter_by(external_id="7002").one().id).text = d.text  # a desk post with the same text
    db.commit()
    assert performance.auto_link(db, NOW) == []
    _own(db, "7003", d.text, NOW - timedelta(hours=1), 10, 0)
    assert performance.auto_link(db, NOW) == [d.id]


def test_link_api_only_for_approved_drafts_and_one_post_per_draft(client: TestClient, db: Session) -> None:
    a = _row(db, "a", "rarity", drafts.GENERATOR, status="approved")
    b = _row(db, "b", "rarity", drafts.GENERATOR, status="approved")
    p = _row(db, "c", "rarity", drafts.GENERATOR)
    assert client.post(f"/api/originals/drafts/{p.id}/posted", json={"url": "https://x.com/a/status/55555"}).status_code == 409
    assert client.post(f"/api/originals/drafts/{a.id}/posted", json={"url": "not a link"}).status_code == 422
    ok = client.post(f"/api/originals/drafts/{a.id}/posted", json={"url": "https://x.com/a/status/55555"})
    assert ok.status_code == 200 and ok.json()["posted"]["how"] == "owner"
    assert client.post(f"/api/originals/drafts/{b.id}/posted", json={"url": "55555"}).status_code == 409
    assert client.delete(f"/api/originals/drafts/{a.id}/posted").json()["posted"] is None
    r = client.get("/api/originals/performance").json()
    assert {"groups", "formats", "winners", "originals_vs_news", "method"} <= set(r)
    lst = client.get("/api/originals/drafts?status=pending").json()
    assert "top" in lst


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def boom(self, request):  # type: ignore[no-untyped-def]
        raise AssertionError(f"outbound network call during a G3 test: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", boom)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", boom)


# --- the 7-day experiment ------------------------------------------------------------------------------------------------------------


def test_experiment_window_and_slate_rotate_families() -> None:
    from app.growth import experiment

    assert experiment.window(datetime(2026, 11, 27, 12, tzinfo=timezone.utc))["state"] == "not started"
    w = experiment.window(datetime(2026, 11, 30, 12, tzinfo=timezone.utc))
    assert w["state"] == "running" and w["day"] == 3 and w["end"] == "2026-12-04"
    views = [_view(2, "weekly_streak", "BTC"), _view(3, "cross_asset", "cross-asset"), _view(4, "fear_aftermath", "FNG-BTC"),
             _view(6, "btc_in_gold", "BTC-gold"), _view(7, "drawdown_table", "drawdown-majors")]
    s = experiment.slate(views, drafts.GENERATOR, {})
    assert [p["id"] for p in s] == [2, 3, 4] and [p["slot"] for p in s] == list(experiment.DAILY_SLOTS)
    assert len({p["family"] for p in s}) == 3  # one per family (the gold card shares Cross-market with the scoreboard)
    s2 = experiment.slate(views, drafts.GENERATOR, {"Records & streaks": 3, "Cross-market": 3})
    assert [p["id"] for p in s2] == [4, 7, 2]  # the least-tested families first


def test_experiment_summary_counts_days_families_and_g4_readiness(db: Session) -> None:
    d = _row(db, "weekly_streak:BTC", "weekly_streak", drafts.GENERATOR, status="approved")
    t0 = datetime(2026, 11, 28, 13, 30, tzinfo=timezone.utc)
    d.text, d.created_at = "Bitcoin closed lower 7 weeks in a row, the longest in Hyperliquid data, which begins in February 2023.", t0 - timedelta(hours=3)
    db.commit()
    _own(db, "9101", d.text, t0, imp=500, eng=10)
    _news(db, "9102", t0 + timedelta(hours=1), imp=100, eng=1)
    r = performance.report(db, days=30, now=datetime(2026, 11, 30, 12, tzinfo=timezone.utc))
    e = r["experiment"]
    assert e["posted_per_day"]["2026-11-28"] == 1 and e["day"] == 3
    fam = next(f for f in e["families"] if f["family"] == "Records & streaks")
    assert fam["posts"] == 1 and fam["measured_24h"] == 1 and not fam["enough"]
    assert e["news"]["posts"] == 1 and e["g4_ready"] is False and e["winners"] == []
