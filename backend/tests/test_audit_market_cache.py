"""Audit AR-6 / UX-13: the public Pokémon market never rebuilds its whole-catalogue rows on a request while a copy exists
(refresh-ahead from the warm-up job, stale-while-revalidate otherwise, one build at a time), and the public homepage /
Earn boards are cached instead of computed per visitor. Market data here is the test database's seed catalogue only."""

import asyncio
import threading
import time
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.pokemon import PokemonCard
from app.pokemon import catalog, history, market, service
from app.pokemon.grades import Grade


def _age(key: Any, seconds: float) -> None:
    """Pretend `key` was built `seconds` ago."""
    with history._lock:
        gen, at, value = history._cache[key]
        history._cache[key] = (gen, at - seconds, value)


def _wait_refreshes(timeout: float = 10.0) -> None:
    end = time.monotonic() + timeout
    while history._refreshing and time.monotonic() < end:
        time.sleep(0.01)
    assert not history._refreshing


# --- the shared cache ---------------------------------------------------------------------------------------------


def test_an_expired_value_is_served_at_once_and_rebuilt_once_in_the_background() -> None:
    key = ("audit-swr", 1)
    assert history.cached(key, lambda: "old", ttl=60, stale=600) == "old"
    _age(key, 61)
    gate, builds = threading.Event(), []

    def slow() -> str:
        builds.append(threading.current_thread().name)
        gate.wait(5)
        return "new"

    out: list[tuple[str, float]] = []

    def visitor() -> None:
        t = time.monotonic()
        out.append((history.cached(key, slow, ttl=60, stale=600), time.monotonic() - t))

    threads = [threading.Thread(target=visitor) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert [v for v, _ in out] == ["old"] * 8  # nobody waited for the rebuild
    assert max(s for _, s in out) < 1.0
    gate.set()
    _wait_refreshes()
    assert builds == ["cache-refresh"]  # exactly one rebuild, off the request path
    assert history.cached(key, slow, ttl=60, stale=600) == "new"


def test_without_a_copy_concurrent_callers_share_one_build() -> None:
    key = ("audit-swr", "cold")
    builds = []

    def build() -> int:
        builds.append(1)
        time.sleep(0.2)
        return 7

    out: list[int] = []
    threads = [threading.Thread(target=lambda: out.append(history.cached(key, build, ttl=60, stale=600))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert out == [7] * 8 and len(builds) == 1


def test_a_sync_still_shows_new_data_at_once() -> None:
    key = ("audit-swr", "gen")
    history.cached(key, lambda: "before", ttl=60, stale=600)
    history.invalidate()  # an import / sync: never served stale
    assert history.cached(key, lambda: "after", ttl=60, stale=600) == "after"


def test_refresh_rebuilds_ahead_while_callers_keep_the_current_value() -> None:
    key = ("audit-swr", "ahead")
    history.cached(key, lambda: "v1", ttl=900)
    assert history.refresh(key, lambda: "nope", ttl=900, older_than=480) == "v1"  # young enough: nothing rebuilt
    _age(key, 600)  # the next warm-up tick: still inside the TTL
    gate, started = threading.Event(), threading.Event()

    def slow() -> str:
        started.set()
        gate.wait(5)
        return "v2"

    job = threading.Thread(target=lambda: history.refresh(key, slow, ttl=900, older_than=480))
    job.start()
    assert started.wait(5)
    t = time.monotonic()
    assert history.cached(key, lambda: "request-build", ttl=900) == "v1"  # a visitor during the rebuild: no wait
    assert time.monotonic() - t < 0.5
    gate.set()
    job.join(5)
    assert history.cached(key, lambda: "request-build", ttl=900) == "v2"


# --- the market rows ------------------------------------------------------------------------------------------------


def _seed(db: Session) -> int:
    catalog.ensure_catalog(db)
    zard = db.scalar(select(PokemonCard).where(PokemonCard.name == "Charizard", PokemonCard.set_name == "Base Set",
                                               PokemonCard.edition == "1st Edition"))
    assert zard is not None
    rows = [{"card_id": zard.id, "grading_company": "PSA", "grade": "10", "price": p, "source": "ebay", "source_listing_id": f"a{i}",
             "sale_date": (utcnow() - timedelta(days=d)).isoformat()} for i, (p, d) in enumerate([(300_000, 40), (320_000, 10), (330_000, 1)])]
    assert service.import_sales(db, rows, "ebay", verified=True)["imported"] == 3  # TEST FIXTURE sales
    service.store_population(db, zard.id, Grade("PSA", "10"), 120, "psa", utcnow())
    db.commit()
    history.rebuild(db)
    return zard.id


def _count_builds(monkeypatch: pytest.MonkeyPatch, gate: threading.Event | None = None) -> list[str]:
    builds: list[str] = []
    real = market.market_rows

    def counted(*a: Any, **k: Any) -> Any:
        builds.append(threading.current_thread().name)  # only the whole-catalogue build calls it in these tests
        if gate is not None:
            gate.wait(5)
        return real(*a, **k)

    monkeypatch.setattr(market, "market_rows", counted)
    return builds


def test_a_visitor_after_the_ttl_gets_the_rows_at_once_not_a_rebuild(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(db)
    first = market.base_rows(db, market.DEFAULT_GRADE)
    gate = threading.Event()
    builds = _count_builds(monkeypatch, gate)
    _age(("base", market.DEFAULT_GRADE.label), market.BASE_TTL + 1)  # the cache expired (warm-up late or off)
    t = time.monotonic()
    assert market.base_rows(db, market.DEFAULT_GRADE) is first  # the stale rows, straight away
    assert time.monotonic() - t < 1.0
    gate.set()
    _wait_refreshes()
    assert builds == ["cache-refresh"]  # rebuilt once, in the background
    again = market.base_rows(db, market.DEFAULT_GRADE)
    assert again is not first and [r["card"]["id"] for r in again] == [r["card"]["id"] for r in first]


def test_the_warm_up_job_rebuilds_before_the_ttl(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(db)
    first = market.base_rows(db, market.DEFAULT_GRADE)
    builds = _count_builds(monkeypatch)
    market.warm(db, utcnow())
    assert builds == []  # rows younger than BASE_REFRESH: left alone
    _age(("base", market.DEFAULT_GRADE.label), 600)  # the next tick (every 600 s), before BASE_TTL (900 s)
    assert history.is_cached(("base", market.DEFAULT_GRADE.label), market.BASE_TTL)
    market.warm(db, utcnow())
    assert len(builds) == 1 and market.base_rows(db, market.DEFAULT_GRADE) is not first
    # brand new: a visitor at T+900 finds it fresh. "Younger than 60 s" rather than 1 s: on GitHub's slower CI runners the rebuild
    # itself took over a second (main, run 45006596768), and the point is new versus the 600 s-old rows it replaced
    assert history.is_cached(("base", market.DEFAULT_GRADE.label), 60)
    _age(("base", market.DEFAULT_GRADE.label), market.BASE_TTL - 300)
    market.base_rows(db, market.DEFAULT_GRADE)
    assert len(builds) == 1  # requests never rebuilt anything


def test_warm_up_cadence_is_shorter_than_the_ttl(client: TestClient) -> None:
    from app.jobs import build_jobs

    spec = next(s for s in build_jobs(client.app.state.runtime) if s.name == "pokemon.warm")
    assert market.BASE_REFRESH < spec.interval_seconds < market.BASE_TTL  # every tick rebuilds, always before the rows expire


def test_card_rank_matches_the_market_era_view(db: Session) -> None:
    zard = _seed(db)
    card = db.get(PokemonCard, zard)
    view = market.market(db, market.DEFAULT_GRADE, card.era, "", market.MAX_LIMIT, 0)
    expected = {r["card"]["id"]: r["rank"] for r in view["rows"] if r.get("rank")}
    assert market.era_ranks(db, market.DEFAULT_GRADE, card.era, utcnow()) == expected
    assert market.rank_of(db, card, market.DEFAULT_GRADE) == expected[zard] == 1


# --- website homepage and Earn --------------------------------------------------------------------------------------


def test_homepage_is_read_once_per_window_and_dashboard_edits_show_at_once(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.routes import website

    calls = []
    real = website._home
    monkeypatch.setattr(website, "_home", lambda db: calls.append(1) or real(db))
    a = client.get("/api/website/home").json()
    b = client.get("/api/website/home").json()
    assert a == b and len(calls) == 1
    client.post("/api/admin/login", json={"username": "admin", "password": "pw"})
    client.put("/api/website/settings", json={"sections": {"trending": False}})
    assert client.get("/api/website/home").json()["sections"]["trending"] is False


def test_lunch_board_is_cached(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.arbitrage import lunch

    calls = []
    real = lunch.summary
    monkeypatch.setattr(lunch, "summary", lambda db, now: calls.append(1) or real(db, now))
    assert client.get("/api/members/arbitrage/lunch").json() == client.get("/api/members/arbitrage/lunch").json()
    assert len(calls) == 1


def test_funding_board_past_its_age_answers_at_once_and_refreshes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.arbitrage import funding

    builds = []

    async def slow(transport: Any = None) -> dict[str, Any]:
        builds.append(1)
        await asyncio.sleep(0.3)
        return {"board": "new"}

    monkeypatch.setattr(funding, "build", slow)
    monkeypatch.setattr(funding, "_refresh_task", None)
    monkeypatch.setattr(funding, "_cache", {"at": time.monotonic() - funding.CACHE_SECONDS - 1, "data": {"board": "old"}})

    async def go() -> tuple[list[dict[str, Any]], float, dict[str, Any]]:
        t = time.monotonic()
        got = await asyncio.gather(*[funding.board() for _ in range(5)])
        took = time.monotonic() - t
        await funding._refresh_task
        return got, took, await funding.board()

    got, took, after = asyncio.run(go())
    assert got == [{"board": "old"}] * 5 and took < 0.2 and builds == [1] and after == {"board": "new"}


def test_without_keep_stale_the_old_catalogue_goes_before_the_rebuild() -> None:
    key = ("base", "AUDIT-GRADE")  # a whole-catalogue key
    history.cached(key, lambda: ["v1"], ttl=900)
    seen = []
    history.refresh(key, lambda: seen.append(history.is_cached(key, 900)) or ["v2"], ttl=900, older_than=0, keep_stale=False)
    assert seen == [False]  # never two catalogues in memory (POKEMON_MARKET_KEEP_STALE=false)
    seen.clear()
    history.refresh(key, lambda: seen.append(history.is_cached(key, 900)) or ["v3"], ttl=900, older_than=0)
    assert seen == [True] and history.cached(key, list, ttl=900) == ["v3"]  # default: still served during the rebuild
