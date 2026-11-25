"""X performance history (app/social/x_history): checkpoint curve, batching, budget, backfill, followers. X is the in-memory FakeX."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config.settings import Settings
from app.connectors.state import get_connector_state
from app.connectors.x.connector import XRuntime
from app.models.enums import Platform
from app.models.newsdesk import NdPost
from app.models.social import SocialPost
from app.models.social_intel import GrowthSnapshot, SocialPostMetric
from app.models.trending import TtPost
from app.social import x_history as xh
from app.social.budget import PlatformBudget
from tests.conftest import connect_x_user
from tests.fakes import FakeX

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


def _settings(**kw) -> Settings:  # type: ignore[no-untyped-def]
    base = {"x_api_base": "https://api.x.test", "x_daily_budget_usd": 5.0, "x_monthly_budget_usd": 30.0}
    return Settings(**{**base, **kw})


@pytest.fixture
def x() -> FakeX:
    return FakeX()


@pytest.fixture
def runtime(x: FakeX) -> XRuntime:
    return XRuntime(configured=True, status="connected", transport=x.transport)


def _tweet(x: FakeX, created: datetime, text: str = "post") -> str:
    tid = x.tweet("1600", text, kind="own", created_at=created.isoformat().replace("+00:00", "Z"),
                  metrics={"like_count": 4, "reply_count": 1, "retweet_count": 2, "quote_count": 0, "bookmark_count": 3, "impression_count": 900})
    return tid


def _nd(db, x: FakeX, age: timedelta, **facts) -> NdPost:  # type: ignore[no-untyped-def]
    pub = NOW - age
    p = NdPost(kind="news", category="crypto", priority="scheduled", status="published", text="Bitcoin record high", key=f"k{pub.timestamp()}",
               facts={"viral": {"hook": "number", "format": "photo", "writer": "model", "question": False, "links": False}, **facts},
               x_id=_tweet(x, pub), published_at=pub)
    db.add(p)
    db.commit()
    return p


def _tt(db, x: FakeX, age: timedelta) -> TtPost:  # type: ignore[no-untyped-def]
    pub = NOW - age
    t = TtPost(topic_id=1, category="politics", status="published", text="Trend post", angle="explainer", writer="rules", x_id=_tweet(x, pub), published_at=pub)
    db.add(t)
    db.commit()
    return t


def _run(db, runtime, now=NOW, **kw):  # type: ignore[no-untyped-def]
    return asyncio.run(xh.collect(db, _settings(**kw), runtime, now))


def _lookups(x: FakeX) -> list[list[str]]:
    return [p["ids"].split(",") for path, p, _ in x.calls if path == "/2/tweets"]


def _curve(db, x_id: str) -> list[str]:  # type: ignore[no-untyped-def]
    sp = db.scalar(select(SocialPost).where(SocialPost.external_id == x_id))
    return [r.source for r in db.scalars(select(SocialPostMetric).where(SocialPostMetric.post_id == sp.id).order_by(SocialPostMetric.id))]


def _backfill_done(db) -> None:  # type: ignore[no-untyped-def]
    xh._save(db, {"backfill_done": NOW.isoformat()})
    db.commit()


# --- scheduling ------------------------------------------------------------------------------------------------------------------------


def test_due_label_takes_the_newest_reached_checkpoint_once() -> None:
    pub = NOW - timedelta(hours=2)
    assert xh.due_label(pub, NOW, set()) == "1h"
    assert xh.due_label(pub, NOW, {"1h"}) is None
    assert xh.due_label(NOW - timedelta(minutes=30), NOW, set()) is None  # too young
    assert xh.due_label(NOW - timedelta(hours=30), NOW, {"1h"}) == "24h"  # 6h was missed: skipped, never taken late
    assert xh.due_label(NOW - timedelta(hours=80), NOW, {"24h"}) == "72h"
    assert xh.due_label(NOW - timedelta(days=7, hours=2), NOW, set()) == "7d"
    assert xh.due_label(NOW - timedelta(days=9), NOW, set()) is None  # the curve has ended


def test_curve_over_a_week_links_desk_post_and_keeps_legacy_metrics(db, x, runtime) -> None:  # type: ignore[no-untyped-def]
    connect_x_user(db)
    _backfill_done(db)
    p = _nd(db, x, timedelta(minutes=10))
    assert db.scalar(select(SocialPost).where(SocialPost.external_id == p.x_id)) is None
    for hours, label in ((0.5, None), (1.2, "1h"), (1.4, None), (6.5, "6h"), (25, "24h"), (73, "72h"), (169, "7d"), (190, None)):
        before = len(_lookups(x))
        out = _run(db, runtime, now=p.published_at + timedelta(hours=hours))
        assert (len(_lookups(x)) - before) == (1 if label else 0), (hours, out)
    assert _curve(db, p.x_id) == ["curve_1h", "curve_6h", "curve_24h", "curve_72h", "curve_7d"]
    sp = db.scalar(select(SocialPost).where(SocialPost.external_id == p.x_id))
    assert sp.is_own and "newsdesk" in sp.source_kinds
    row = db.scalars(select(SocialPostMetric).where(SocialPostMetric.post_id == sp.id)).first()
    assert (row.impressions, row.likes, row.reposts, row.bookmarks, row.url_clicks, row.profile_visits) == (1500, 4, 2, 3, 12, 7)
    db.refresh(p)
    assert p.metrics["impression_count"] == 1500 and p.metrics["like_count"] == 4 and "read_at" in p.metrics  # legacy shape kept


def test_trending_and_other_own_posts_get_curves_too(db, x, runtime) -> None:  # type: ignore[no-untyped-def]
    connect_x_user(db)
    _backfill_done(db)
    t = _tt(db, x, timedelta(hours=7))
    other = _tweet(x, NOW - timedelta(hours=2), "own reply")
    db.add(SocialPost(platform=Platform.X, external_id=other, text="own reply", is_own=True, posted_at=NOW - timedelta(hours=2),
                      referenced=[], source_kinds=["own"], rule_ids=[], metrics={}))
    db.commit()
    out = _run(db, runtime)
    assert out["recorded"] == 2 and len(_lookups(x)) == 1  # one request for both
    assert _curve(db, t.x_id) == ["curve_6h"] and _curve(db, other) == ["curve_1h"]
    db.refresh(t)
    assert t.metrics["impression_count"] == 1500 and t.metrics_at is not None


# --- batching, budget, no writes -------------------------------------------------------------------------------------------------------


def test_batches_at_most_100_ids_per_request(db, x, runtime) -> None:  # type: ignore[no-untyped-def]
    connect_x_user(db)
    _backfill_done(db)
    for i in range(230):
        _nd(db, x, timedelta(hours=2, minutes=i % 50))
    out = _run(db, runtime)
    sizes = [len(ids) for ids in _lookups(x)]
    assert sizes == [100, 100, 30] and out["recorded"] == 230 and out["requests"] == 3
    assert out["usd"] == pytest.approx(0.23)
    assert PlatformBudget(db, _settings()).today()["cost"] == pytest.approx(0.23)  # recorded in the account's X spend too
    assert xh.spent_today(xh._hist(db), NOW) == pytest.approx(0.23)


def test_never_spends_the_posting_reserve(db, x, runtime) -> None:  # type: ignore[no-untyped-def]
    connect_x_user(db)
    _backfill_done(db)
    _nd(db, x, timedelta(hours=2))
    b = PlatformBudget(db, _settings())
    b.record("tweets/search/recent", 0, 0, 3.6)  # $5 a day - $1.50 posting reserve = $3.50 for reads: none left
    out = _run(db, runtime)
    assert out["recorded"] == 0 and "posting reserve" in out["stopped"] and _lookups(x) == []


def test_own_daily_cap_and_account_budget_refuse(db, x, runtime) -> None:  # type: ignore[no-untyped-def]
    connect_x_user(db)
    _backfill_done(db)
    _nd(db, x, timedelta(hours=2))
    out = _run(db, runtime, x_perf_history_daily_usd=0.0)
    assert out["recorded"] == 0 and "own cap" in out["stopped"] and _lookups(x) == []
    out = _run(db, runtime, x_daily_budget_usd=0.0)
    assert out["recorded"] == 0 and _lookups(x) == []
    out = _run(db, runtime)  # budget back: the same checkpoint is taken on the next run
    assert out["recorded"] == 1


def test_only_reads_never_writes(db, x, runtime) -> None:  # type: ignore[no-untyped-def]
    connect_x_user(db)
    for i in range(5):
        _nd(db, x, timedelta(hours=1 + i * 20))
    _run(db, runtime)
    asyncio.run(xh.snapshot_followers(db, _settings(), runtime, NOW))
    assert x.writes == []
    assert {path for path, _, _ in x.calls} <= {"/2/tweets", "/2/users/me"}
    assert all(auth == "user" for _, _, auth in x.calls)  # owned reads (user context), never the $0.005 public lookup


def test_off_switch_and_missing_keys(db, x, runtime) -> None:  # type: ignore[no-untyped-def]
    _nd(db, x, timedelta(hours=2))
    assert "user context" in _run(db, runtime)["skipped"]
    connect_x_user(db)
    assert "off" in _run(db, runtime, x_perf_history_enabled=False)["skipped"]
    assert "off" in asyncio.run(xh.snapshot_followers(db, _settings(x_perf_history_enabled=False), runtime, NOW))["skipped"]
    assert x.calls == []


# --- backfill --------------------------------------------------------------------------------------------------------------------------


def test_backfill_reads_last_30_days_once(db, x, runtime) -> None:  # type: ignore[no-untyped-def]
    connect_x_user(db)
    old = _nd(db, x, timedelta(days=12))
    older = _tt(db, x, timedelta(days=28))
    too_old = _nd(db, x, timedelta(days=40))  # X has no non-public metrics past 30 days: never asked
    recent = _nd(db, x, timedelta(hours=3))
    out = _run(db, runtime)
    assert out["backfill"] == 2 and out["due"] == 1 and out["recorded"] == 3 and len(_lookups(x)) == 1
    assert too_old.x_id not in _lookups(x)[0]
    assert _curve(db, old.x_id) == ["curve_backfill"] and _curve(db, older.x_id) == ["curve_backfill"] and _curve(db, recent.x_id) == ["curve_1h"]
    assert xh._hist(db).get("backfill_done")
    out = _run(db, runtime, now=NOW + timedelta(minutes=15))  # second run: nothing again
    assert out["recorded"] == 0 and len(_lookups(x)) == 1
    db.refresh(too_old)
    assert not too_old.metrics


def test_deleted_post_stops_being_asked(db, x, runtime) -> None:  # type: ignore[no-untyped-def]
    connect_x_user(db)
    _backfill_done(db)
    p = _nd(db, x, timedelta(hours=2))
    x.tweets = [t for t in x.tweets if t["id"] != p.x_id]
    for i in range(xh.MAX_MISSES + 2):
        _run(db, runtime, now=NOW + timedelta(minutes=10 * i))
    assert len(_lookups(x)) == xh.MAX_MISSES and p.x_id in xh._hist(db)["gone"]


# --- followers -------------------------------------------------------------------------------------------------------------------------


def test_follower_snapshot_hourly(db, x, runtime) -> None:  # type: ignore[no-untyped-def]
    connect_x_user(db)
    s = _settings()
    out = asyncio.run(xh.snapshot_followers(db, s, runtime, NOW))
    assert out["recorded"] and out["followers"] == 1200
    assert "last hour" in asyncio.run(xh.snapshot_followers(db, s, runtime, NOW + timedelta(minutes=20)))["skipped"]
    x.users["1600"]["public_metrics"]["followers_count"] = 1210
    asyncio.run(xh.snapshot_followers(db, s, runtime, NOW + timedelta(minutes=61)))
    snaps = list(db.scalars(select(GrowthSnapshot).where(GrowthSnapshot.kind == "x_account").order_by(GrowthSnapshot.taken_at)))
    assert [(g.followers, g.following, g.posts) for g in snaps] == [(1200, 80, 300), (1210, 80, 300)]
    assert sum(1 for path, _, _ in x.calls if path == "/2/users/me") == 2
    assert xh.spent_today(xh._hist(db), NOW) == pytest.approx(0.02)


def test_follower_snapshot_respects_posting_reserve(db, x, runtime) -> None:  # type: ignore[no-untyped-def]
    connect_x_user(db)
    PlatformBudget(db, _settings()).record("tweets/search/recent", 0, 0, 3.5)
    out = asyncio.run(xh.snapshot_followers(db, _settings(), runtime, NOW))
    assert "posting reserve" in out["stopped"] and x.calls == []


# --- legacy jobs and the read API ------------------------------------------------------------------------------------------------------


def test_legacy_daily_jobs_skip_while_history_is_on(client, db) -> None:  # type: ignore[no-untyped-def]
    from app.jobs import build_jobs
    from app.scheduler.scheduler import JobContext, JobSkipped

    specs = {j.name: j for j in build_jobs(client.app.state.runtime)}
    assert {"newsdesk.x_history", "newsdesk.x_followers"} <= set(specs)  # newsdesk. prefix: production's SCHEDULER_ONLY_JOBS runs them
    assert specs["newsdesk.x_history"].interval_seconds <= 900 and specs["newsdesk.x_followers"].interval_seconds == 3600
    for name in ("newsdesk.metrics", "newsdesk.trending_metrics"):
        with pytest.raises(JobSkipped, match="X performance history"):
            asyncio.run(specs[name].func(JobContext(db=db, trigger="manual", now=NOW)))
    with pytest.raises(JobSkipped):  # no X keys here: skipped, nothing read
        asyncio.run(specs["newsdesk.x_history"].func(JobContext(db=db, trigger="manual", now=NOW)))


def test_curves_read_api_has_features(db, x, runtime) -> None:  # type: ignore[no-untyped-def]
    connect_x_user(db)
    _backfill_done(db)
    p = _nd(db, x, timedelta(hours=2))
    _tt(db, x, timedelta(hours=7))
    _run(db, runtime)
    rows = xh.curves(db, now=NOW)
    assert [r["features"]["lane"] for r in rows] == ["newsdesk", "trending"]
    nd = rows[0]
    assert nd["x_id"] == p.x_id and nd["points"][0]["checkpoint"] == "1h" and nd["points"][0]["impressions"] == 1500
    assert 1.9 < nd["points"][0]["age_hours"] < 2.1 and nd["points"][0]["private"] is True
    f = nd["features"]
    assert (f["kind"], f["category"], f["hook"], f["format"], f["writer"], f["hour"], f["weekday"]) == ("news", "crypto", "number", "photo", "model", 10, 2)
    assert f["length"] == len("Bitcoin record high") and f["has_image"] is False
    assert rows[1]["features"]["angle"] == "explainer" and rows[1]["features"]["kind"] == "trend"
    assert [r["x_id"] for r in xh.curves(db, lane="trending", now=NOW)] == [rows[1]["x_id"]]


def test_whole_batch_deleted_counts_misses_and_goes_on(db, x, runtime) -> None:  # type: ignore[no-untyped-def]
    from app.connectors.x.errors import XNotFoundError

    class Gone:
        calls = 0

        async def own_post_metrics(self, ids, media=False):  # type: ignore[no-untyped-def]
            Gone.calls += 1
            raise XNotFoundError("Could not find tweet")

    connect_x_user(db)
    _backfill_done(db)
    p = _nd(db, x, timedelta(hours=2))
    for i in range(xh.MAX_MISSES + 1):
        out = asyncio.run(xh.collect(db, _settings(), runtime, NOW + timedelta(minutes=10 * i), client=Gone()))
        assert out["stopped"] is None
    assert Gone.calls == xh.MAX_MISSES and p.x_id in xh._hist(db)["gone"]
