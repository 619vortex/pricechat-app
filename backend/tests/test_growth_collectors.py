"""Growth Engine G0 Market Memory: collectors against a fake transport (no real network), idempotent upserts, complete days only, the
licence guard (uncleared sources never run in production, Yahoo never), OI / liquidation rollups, breadth, and the jobs being OFF by default.
Every answer here is a SYNTHETIC fixture."""

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.growth import collectors, sources, store
from app.models.market_memory import MmSeries
from app.models.tradingos import TsLiquidation, TsOiSnapshot

NOW = datetime(2026, 11, 25, 6, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


def ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


class Fake:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_once = True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        if "hyperliquid" in url:
            body = json.loads(request.content)
            if body["type"] == "metaAndAssetCtxs":
                if self.fail_once:  # one 429: retried
                    self.fail_once = False
                    return httpx.Response(429)
                return httpx.Response(200, json=[{"universe": [{"name": "BTC"}, {"name": "kPEPE"}, {"name": "OLD", "isDelisted": True},
                                                               {"name": "TINY"}]},
                                                 [{"dayNtlVlm": "5e9"}, {"dayNtlVlm": "3e6"}, {"dayNtlVlm": "9e9"}, {"dayNtlVlm": "10"}]])
            if body["type"] == "candleSnapshot":
                start = datetime.fromtimestamp(body["req"]["startTime"] / 1000, tz=timezone.utc).date()
                out = []
                for i in range(12):
                    d = start + timedelta(days=i)
                    if d > TODAY:
                        break
                    out.append({"t": ms(d), "c": str(100 + i), "v": "2", "n": 0 if i == 0 else 50})  # the first has no trades
                return httpx.Response(200, json=out)
            if body["type"] == "fundingHistory":
                t0 = body["startTime"]
                if t0 >= ms(TODAY - timedelta(days=1)):
                    return httpx.Response(200, json=[])
                d = TODAY - timedelta(days=2)
                return httpx.Response(200, json=[{"time": ms(d) + h * 3600_000, "fundingRate": "-0.0001"} for h in range(24)]
                                      + [{"time": ms(d) + 86_400_000 + h * 3600_000, "fundingRate": "0.0001"} for h in range(5)])
        if "alternative.me" in url:
            return httpx.Response(200, json={"data": [{"value": "13", "timestamp": str(ms(TODAY - timedelta(days=1)) // 1000)},
                                                      {"value": "40", "timestamp": str(ms(TODAY) // 1000)}]})
        if "coinbase" in url:
            return httpx.Response(200, json=[[ms(TODAY - timedelta(days=1)) // 1000, 1, 2, 1, 1.5, 10]])
        return httpx.Response(404)


def http(fake: Fake) -> collectors.Http:
    return collectors.Http(transport=httpx.MockTransport(fake), sleep=lambda s: None)


def test_collect_writes_complete_days_idempotently(db: Session) -> None:
    fake = Fake()
    rep = collectors.collect(db, http=http(fake), now=NOW, full=True, settings=get_settings(),
                             steps=("hyperliquid", "funding", "fng", "breadth"))
    assert rep["errors"] == [] and rep["universe"] == 2  # BTC and kPEPE: delisted and tiny ones left out
    btc = store.load(db, "px:BTC:close")
    assert btc and all(d < TODAY for d, _ in btc)  # today's candle is still moving: not stored
    assert len(btc) == 11  # 12 candles, minus the no-trade one
    assert store.load(db, "px:BTC:vol")[0][1] == pytest.approx(2 * 101)
    fund = store.load(db, "fund:BTC")
    assert fund == [(TODAY - timedelta(days=2), pytest.approx(-0.0001))]  # the 5-hour day isn't a day yet
    assert store.load(db, "fng") == [(TODAY - timedelta(days=1), 13.0)]
    n1 = db.query(MmSeries).count()
    collectors.collect(db, http=http(Fake()), now=NOW, full=True, settings=get_settings(), steps=("hyperliquid", "funding", "fng"))
    assert db.query(MmSeries).count() == n1  # re-running rewrites the same days
    assert not any("coinbase" in c for c in fake.calls)  # Coinbase is not a default step (not cleared)


def test_uncleared_sources_never_run_in_production(db: Session) -> None:
    prod = SimpleNamespace(is_production=True)
    assert sources.may_collect("hyperliquid", prod) and sources.may_collect("alternative_me", prod)
    assert not sources.may_collect("coinbase", prod) and not sources.may_collect("coingecko", prod)
    assert not sources.may_collect("yahoo", prod) and not sources.may_collect("yahoo", SimpleNamespace(is_production=False))
    assert sources.may_collect("liquidations", prod)  # store-only internal rollup: never surfaced (no detector reads liq:*)
    assert not sources.publishable(["liquidations"]) and not sources.publishable(["hyperliquid", "coinbase"]) and sources.publishable(["hyperliquid"])
    fake = Fake()
    rep = collectors.collect(db, http=http(fake), now=NOW, settings=prod, steps=("coinbase",))
    assert any("coinbase" in s for s in rep["skipped"]) and fake.calls == []
    assert sources.attribution(["alternative_me"]) == ["Fear & Greed Index: alternative.me"]


def test_coinbase_collector_kept_for_research_outside_production(db: Session) -> None:
    fake = Fake()
    rep = collectors.collect(db, http=http(fake), now=NOW, full=False, settings=SimpleNamespace(is_production=False), steps=("coinbase",))
    assert rep["errors"] == [] and store.load(db, "cb:BTC-USD:close")[0][1] == 1.5


def test_rollups_before_pruning(db: Session) -> None:
    d1 = TODAY - timedelta(days=3)
    for h, oi in ((1, 100.0), (23, 300.0)):
        db.add(TsOiSnapshot(at=datetime(d1.year, d1.month, d1.day, h, tzinfo=timezone.utc), coin="btc", mark=1, oi_usd=oi, funding=0.0))
    db.add(TsOiSnapshot(at=NOW, coin="BTC", mark=1, oi_usd=999, funding=0.0))  # today: incomplete, not rolled up
    for i, side in enumerate(("long", "long", "short")):
        db.add(TsLiquidation(venue="okx", coin="ETH", t=ms(d1) + i * 1000, side=side, px=1, qty=1, usd=10.0 * (i + 1)))
    db.commit()
    assert collectors.rollup_oi(db, TODAY) == 1
    assert store.load(db, "oi:BTC") == [(d1, 300.0)]
    collectors.rollup_liquidations(db, TODAY)
    assert store.load(db, "liq:ETH:long") == [(d1, 30.0)] and store.load(db, "liq:ETH:short") == [(d1, 30.0)]
    assert db.query(MmSeries).filter(MmSeries.series.like("liq:%")).first().source == "liquidations"  # type: ignore[union-attr]


def test_breadth_from_closes() -> None:
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(210)]
    closes = {f"px:C{k}:close": [(d, 100.0 + (i if k < 15 else -i * 0.1)) for i, d in enumerate(days)] for k in range(25)}
    b = collectors.compute_breadth(closes)
    assert b["breadth:above200"][-1] == (days[-1], 60.0) and b["breadth:n200"][-1] == (days[-1], 25.0)
    assert b["breadth:up7d"][-1] == (days[-1], 60.0)


def test_limiter_spaces_calls() -> None:
    t = [0.0]
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        t[0] += s

    lim = collectors.Limiter(100, sleep=sleep, clock=lambda: t[0])
    for _ in range(6):
        lim.take(20)
    assert slept and t[0] >= 60.0


def test_jobs_off_by_default_and_registered_when_on(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.jobs import build_jobs

    runtime = client.app.state.runtime
    s = runtime.settings
    assert s.growth_enabled is False and s.growth_collect_enabled is False

    def names() -> set[str]:
        return {j.name for j in build_jobs(runtime)}

    assert not {"growth.collect", "growth.originals"} & names()
    monkeypatch.setattr(s, "growth_enabled", True)
    monkeypatch.setattr(s, "growth_collect_enabled", True)
    assert {"growth.collect", "growth.originals"} <= names()


def test_yahoo_is_registered_never(db: Session) -> None:
    assert sources.get("yahoo") is not None and not sources.commercial_ok("yahoo")
    assert not hasattr(collectors, "yahoo") and "yahoo" not in collectors.__doc__.lower()
    _ = Any


def test_incremental_run_never_stores_today(db: Session) -> None:
    store.upsert(db, "px:BTC:close", [(TODAY - timedelta(days=5), 1.0)], "hyperliquid")
    collectors.collect(db, http=http(Fake()), now=NOW, settings=get_settings(), steps=("hyperliquid",))
    days = [d for d, _ in store.load(db, "px:BTC:close")]
    assert max(days) == TODAY - timedelta(days=1) and TODAY not in days
