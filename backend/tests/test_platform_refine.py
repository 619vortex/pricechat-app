"""Platform fixes: the public Pokémon Market's per-request cost, the market cache's bookkeeping, heavy jobs kept off the event
loop, scheduler chaining. Test database only."""

import asyncio
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.session import SessionLocal
from app.models.pokemon import PokemonCard, PokemonPriceSnapshot
from app.models.routing import ScheduledJob
from app.pokemon import catalog, history, public, service
from app.scheduler.scheduler import JobContext, JobSpec, Scheduler


@contextmanager
def statements(db: Session) -> Iterator[list[str]]:
    seen: list[str] = []

    def grab(conn, cursor, statement, params, context, executemany):  # type: ignore[no-untyped-def]
        seen.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", grab)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", grab)


# --- the public market's per-request cost ---------------------------------------------------------------------------


def test_ensure_catalog_reads_only_the_seed_keys_not_the_whole_catalogue(db: Session) -> None:
    assert catalog.ensure_catalog(db) == len(catalog.SEED)
    db.add(PokemonCard(identity_key="other|1|english|unlimited|", name="Other", pokemon_name="Other", set_name="Other", year=2020,
                       edition="Unlimited", variant="", language="English", era="modern", external_ids={}))
    db.commit()
    with statements(db) as seen:
        assert catalog.ensure_catalog(db) == 0  # every request after the first: nothing to add
    lookups = [s for s in seen if "identity_key" in s]
    assert lookups and all(" IN " in s.upper() for s in lookups)  # never every card's key
    gone = db.scalar(select(PokemonCard).where(PokemonCard.name == "Pikachu Illustrator"))
    db.delete(gone)
    db.commit()
    assert catalog.ensure_catalog(db) == 1  # a missing seed card still comes back


def test_cache_build_locks_do_not_pile_up() -> None:
    history.invalidate()
    for i in range(50):  # keys carry the minute / the query: each one used to leave a lock behind forever
        assert history.cached(("refine-test", i), lambda i=i: i) == i
    assert not [k for k in history._building if isinstance(k, tuple) and k[:1] == ("refine-test",)]

    builds = {"n": 0}
    gate = threading.Event()

    def slow() -> str:
        builds["n"] += 1
        gate.wait(2)
        return "v"

    out: list[str] = []
    threads = [threading.Thread(target=lambda: out.append(history.cached(("refine-test", "same"), slow))) for _ in range(4)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join(5)
    assert out == ["v"] * 4 and builds["n"] == 1  # still one build per key at a time
    assert ("refine-test", "same") not in history._building


def test_snapshot_reads_the_newest_snapshot_per_card_not_the_whole_history(db: Session) -> None:
    from tests.test_pokemon import card, sale

    c = card(db, "Venusaur", "1st Edition")
    service.import_sales(db, [sale(c.id, "10", 20_000, 5, "v1")], verified=True)
    now = utcnow()
    db.add(PokemonPriceSnapshot(card_id=c.id, grading_company="PSA", grade="10", market_price_cents=1, taken_at=now - timedelta(minutes=10)))
    db.add(PokemonPriceSnapshot(card_id=c.id, grading_company="PSA", grade="10", market_price_cents=1, taken_at=now - timedelta(days=30)))
    db.commit()  # the newest is inserted first: "latest" must be the newest by time, not the last row read
    with statements(db) as seen:
        assert service.snapshot(db, now) == 0  # one 10 minutes ago: not again within the hour
    assert any("max(" in s.lower() and "pokemon_price_snapshots" in s for s in seen)
    assert service.snapshot(db, now + timedelta(hours=2)) == 1


def test_market_recently_sold_is_the_ten_newest_verified_sales(client: TestClient, db: Session) -> None:
    from tests.test_pokemon import card, sale

    c = card(db, "Charizard", "1st Edition")
    service.import_sales(db, [sale(c.id, "10", 1_000 + i, i + 1, f"r{i}") for i in range(14)], verified=True)
    history.invalidate()
    sold = client.get("/api/pokemon/market?era=all").json()["sections"]["recently_sold"]
    assert [s["price"] for s in sold] == [1_000_00 + i * 100 for i in range(10)]


def test_visitors_get_the_listed_grades_only_for_catalogue_wide_views(client: TestClient, db: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pydantic import SecretStr

    from app.security.admin_auth import hash_password

    assert public.grade_refused("/api/pokemon/market", {"grade": "PSA 6"})
    assert public.grade_refused("/api/pokemon/movers", {"grade": "BGS 7.5"})
    assert public.grade_refused("/api/pokemon/market", {"grade": "PSA 9"}) is None
    assert public.grade_refused("/api/pokemon/market", {}) is None  # PSA 10 by default
    assert public.grade_refused("/api/pokemon/market", {"grade": "PSA 6", "ids": "1,2"}) is None  # a few cards: cheap
    assert public.grade_refused("/api/pokemon/cards/1/chart", {"grade": "PSA 6"}) is None  # one card: cheap
    assert public.grade_refused("/api/pokemon/market", {"grade": "nonsense"}) is None  # the route explains

    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime.settings, "admin_username", "admin")
    monkeypatch.setattr(runtime.settings, "admin_password_hash", SecretStr(hash_password("pw")))
    catalog.ensure_catalog(db)
    r = client.get("/api/pokemon/market", params={"grade": "PSA 6", "era": "all"})
    assert r.status_code == 400 and r.json()["code"] == "grade_not_listed"
    assert client.get("/api/pokemon/market", params={"grade": "BGS 9.5", "era": "all"}).status_code == 200
    client.post("/api/admin/login", json={"username": "admin", "password": "pw"})
    assert client.get("/api/pokemon/market", params={"grade": "PSA 6", "era": "all"}).status_code == 200  # the owner: any grade


# --- heavy jobs off the event loop -----------------------------------------------------------------------------------


def _spec(client: TestClient, name: str) -> JobSpec:
    from app.jobs import build_jobs

    return next(s for s in build_jobs(client.app.state.runtime) if s.name == name)  # type: ignore[attr-defined]


def test_market_warm_up_and_retention_run_in_a_worker_thread(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app import retention
    from app.pokemon import market

    threads: list[str] = []
    monkeypatch.setattr(market, "base_rows", lambda *a, **k: threads.append(threading.current_thread().name) or [1, 2])
    monkeypatch.setattr(market, "market", lambda *a, **k: {})
    monkeypatch.setattr(retention, "run", lambda *a, **k: threads.append(threading.current_thread().name) or {"activity_logs": 0})

    async def run(name: str) -> dict:
        loop_thread = threading.current_thread().name
        with SessionLocal() as db:
            out = await _spec(client, name).func(JobContext(db=db, trigger="manual", now=utcnow()))
        assert threads[-1] != loop_thread  # the website keeps answering while it builds
        return out

    assert asyncio.run(run("pokemon.warm")) == {"rows": 2}
    assert asyncio.run(run("system.retention")) == {"activity_logs": 0}


# --- scheduler ---------------------------------------------------------------------------------------------------------


async def test_a_job_that_chains_itself_runs_again_soon_not_after_its_interval() -> None:
    s: Scheduler

    async def job(ctx: JobContext) -> dict:
        s.request_run("test.job", 5)  # "more may be waiting"
        return {}

    s = Scheduler(SessionLocal, [JobSpec("test.job", "Test job", "testing", 3600, job, initial_delay_seconds=0)])
    s.sync_jobs()
    assert (await s.run_job("test.job")).status == "success"
    with SessionLocal() as db:
        row = db.get(ScheduledJob, "test.job")
        assert row is not None and row.next_run_at is not None
        assert row.next_run_at - utcnow() < timedelta(seconds=30)

    async def plain(ctx: JobContext) -> dict:
        return {}

    s.specs["test.job"].func = plain
    await s.run_job("test.job", trigger="manual")
    with SessionLocal() as db:
        row = db.get(ScheduledJob, "test.job")
        assert row is not None and row.next_run_at - utcnow() > timedelta(minutes=59)  # no request: the regular interval
