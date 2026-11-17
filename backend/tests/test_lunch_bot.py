"""Earn › Arbitrage › Lunch Bot. TEST FIXTURES only: a fake pump.fun list, fake Solana bonding-curve accounts and a fake
GeckoTerminal, all through httpx.MockTransport. Paper trading: nothing here can sign or send a transaction."""

import asyncio
import base64
import json
import struct
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.arbitrage import lunch
from app.models.arbitrage import ArbLunchDay, ArbLunchGraduation, ArbLunchTrade

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
RPC = "https://rpc.test"
START_VTR, START_VSR = 1_073_000_000 * 10**6, 30 * 10**9  # pump.fun's starting virtual reserves


def account(vsr: int, vtr: int, rsr: int = 0, complete: bool = False, rtr: int = 0) -> str:
    raw = b"\x17" * 8 + struct.pack("<5Q", vtr, vsr, rtr, rsr, 10**15) + bytes([1 if complete else 0])
    return base64.b64encode(raw).decode()


def filled(progress: float, complete: bool = False) -> str:
    """A curve with `progress` of its sellable tokens sold (constant product from the standard start)."""
    sold = int(lunch.INITIAL_REAL_TOKENS * progress)
    vtr = START_VTR - sold
    vsr = START_VSR * START_VTR // vtr
    return account(vsr, vtr, vsr - START_VSR, complete, lunch.INITIAL_REAL_TOKENS - sold)


def grown(sol_in: float, complete: bool = False) -> str:
    """The curve after buyers put `sol_in` SOL in (constant product)."""
    vsr = START_VSR + int(sol_in * 1e9)
    return account(vsr, START_VSR * START_VTR // vsr, int(sol_in * 1e9), complete)


def launch(mint: str, seconds_ago: int = 20, quote: str = lunch.SOL_QUOTE, **kw: Any) -> dict[str, Any]:
    return {"mint": mint, "symbol": mint.upper(), "name": f"{mint} coin", "bonding_curve": f"curve-{mint}", "quote_mint": quote, "complete": False,
            "created_timestamp": int((NOW - timedelta(seconds=seconds_ago)).timestamp() * 1000), "virtual_sol_reserves": START_VSR,
            "virtual_token_reserves": START_VTR, "image_uri": None, **kw}


class Fake:
    def __init__(self) -> None:
        self.launches: list[dict[str, Any]] = []
        self.near: list[dict[str, Any]] = []  # pump.fun's fullest curves (sort=market_cap)
        self.graduated: list[dict[str, Any]] = []
        self.accounts: dict[str, str | None] = {}
        self.pools: dict[str, dict[str, Any]] = {}
        self.candles: dict[str, list[list[float]]] = {}
        self.rate_limited = False
        self.pump_status = 200  # e.g. 403: pump.fun refusing the scanner
        self.rpc_down = False
        self.calls: list[str] = []

    def transport(self) -> httpx.MockTransport:
        def handle(r: httpx.Request) -> httpx.Response:
            url = str(r.url)
            self.calls.append(url)
            if url.startswith(lunch.PUMP_API):
                if self.rate_limited:
                    return httpx.Response(429)
                if self.pump_status != 200:
                    return httpx.Response(self.pump_status)
                if r.url.params.get("complete") == "true":
                    return httpx.Response(200, json=self.graduated)
                return httpx.Response(200, json=self.near if r.url.params.get("sort") == "market_cap" else self.launches)
            if url.startswith(RPC):
                if self.rpc_down:
                    return httpx.Response(503)
                keys = json.loads(r.content)["params"][0]
                return httpx.Response(200, json={"result": {"value": [
                    {"data": [self.accounts[k], "base64"]} if self.accounts.get(k) else None for k in keys]}})
            if "/tokens/" in url and url.split("?")[0].endswith("/pools"):
                mint = url.split("/tokens/")[1].split("/")[0]
                return httpx.Response(200, json={"data": [{"attributes": {"address": addr, "pool_created_at": f"2026-10-21T11:0{i}:00Z"},
                                                           "relationships": {"dex": {"data": {"id": dex}}}}
                                                          for i, (dex, addr) in enumerate(self.pools.get(mint, {}).items())]})
            if "/ohlcv/" in url:
                pool = url.split("/pools/")[1].split("/")[0]
                return httpx.Response(200, json={"data": {"attributes": {"ohlcv_list": self.candles.get(pool, [])}}})
            return httpx.Response(404)
        return httpx.MockTransport(handle)


@pytest.fixture(autouse=True)
def _fresh_bot() -> None:
    lunch._watch.clear()
    lunch._seen.clear()
    lunch._near.clear()
    lunch._triggers.clear()
    lunch._stream_new = 0
    lunch._state.update(runs=1, last_run_at=None, last_error=None, blocked_until=None, watching=0)  # runs=1: skip the graduation check


def run(db: Session, fake: Fake, at: datetime) -> dict[str, Any]:
    return asyncio.run(lunch.tick(db, at, RPC, fake.transport()))


# --- maths ---------------------------------------------------------------------------------------------------------------------


def test_curve_maths_round_trip_costs_exactly_the_fees() -> None:
    c = lunch.parse_curve(account(START_VSR, START_VTR))
    assert c is not None and round(c.mc_sol, 2) == 27.96 and not c.complete
    tokens = lunch.buy_tokens(c, 0.1)
    assert 3_500_000 < tokens < 3_600_000  # about 3.5M tokens for 0.1 SOL at launch
    back = lunch.sell_sol(lunch.Curve(c.vtr - int(tokens * 1e6), c.vsr + int(0.1 * (1 - lunch.CURVE_FEE) * 1e9), 0, 0, False), tokens)
    assert back == pytest.approx(0.1 * (1 - lunch.CURVE_FEE) ** 2, rel=1e-6)


# --- the bot ---------------------------------------------------------------------------------------------------------------------


def test_watches_sol_launches_enters_on_momentum_and_takes_profit(db: Session) -> None:
    f = Fake()
    f.launches = [launch("hot"), launch("cold"), launch("usdc", quote="pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"), launch("old", seconds_ago=900)]
    f.accounts = {"curve-hot": grown(0.5), "curve-cold": grown(0.5)}
    out = run(db, f, NOW)
    assert out["new"] == 2 and out["entered"] == 0  # only the fresh SOL-quoted launches; never on the first sight
    assert set(lunch._watch) == {"hot", "cold"}
    f.accounts = {"curve-hot": grown(6.0), "curve-cold": grown(1.0)}  # hot: 6 SOL in, value way up; cold: no momentum
    out = run(db, f, NOW + timedelta(seconds=60))
    assert out["entered"] == 1
    t = db.scalar(select(ArbLunchTrade))
    assert t.mint == "hot" and t.status == "open" and t.entry_sol == pytest.approx(0.101)
    assert t.detail["signal"]["real_sol"] == 6.0 and t.detail["signal"]["growth_pct"] > 30
    assert db.scalar(select(ArbLunchDay)).launches_seen == 2 and db.scalar(select(ArbLunchDay)).entries == 1
    f.accounts["curve-hot"] = grown(40.0)  # the curve keeps filling: our bag is worth more than double
    out = run(db, f, NOW + timedelta(seconds=120))
    db.refresh(t)
    assert out["closed"] == 1 and t.exit_reason == "target" and t.pnl_sol > t.entry_sol


def test_stop_timeout_and_graduation_exits(db: Session) -> None:
    f = Fake()
    for m in ("a", "b", "c"):
        f.launches.append(launch(m))
        f.accounts[f"curve-{m}"] = grown(0.2)
    run(db, f, NOW)
    for m in ("a", "b", "c"):
        f.accounts[f"curve-{m}"] = grown(20.0)  # entered high on the curve (from 5 SOL in, the curve's floor is only -27% away)
    assert run(db, f, NOW + timedelta(seconds=60))["entered"] == 3
    trades = {t.mint: t for t in db.scalars(select(ArbLunchTrade))}
    f.accounts["curve-a"] = grown(3.0)  # dumped: stop
    f.accounts["curve-c"] = grown(85.0, complete=True)  # graduated: sold in the pool
    f.pools["c"] = {"pump-fun": "curve-pool-c", "pumpswap": "pool-c"}
    final = lunch.final_curve_price(lunch.FINAL_VTR)
    f.candles["pool-c"] = [[1, final, 0, 0, final * 0.98, 0], [2, final * 0.98, 0, 0, final * 1.05, 0]]  # latest close: 5% over the curve's end
    run(db, f, NOW + timedelta(minutes=2))
    assert trades["a"].exit_reason == "stop" and trades["a"].pnl_sol < 0
    assert trades["c"].exit_reason == "graduated" and trades["c"].detail["pool"] == "pool-c" and trades["c"].last_price == pytest.approx(final * 1.05)
    assert trades["c"].pnl_sol > 0
    assert trades["b"].status == "open"
    run(db, f, NOW + timedelta(minutes=lunch.TIMEOUT_MINUTES + 2))
    assert trades["b"].exit_reason == "timeout"


def test_a_rate_limit_backs_off_but_open_positions_are_still_priced(db: Session) -> None:
    f = Fake()
    f.launches = [launch("hot")]
    f.accounts = {"curve-hot": grown(0.3)}
    run(db, f, NOW)
    f.accounts["curve-hot"] = grown(5.0)
    run(db, f, NOW + timedelta(seconds=60))
    f.rate_limited = True
    f.accounts["curve-hot"] = grown(7.0)
    out = run(db, f, NOW + timedelta(seconds=120))
    assert out["pump_rate_limited"] and lunch._state["blocked_until"] == NOW + timedelta(seconds=120) + lunch.BACKOFF
    t = db.scalar(select(ArbLunchTrade))
    assert t.last_checked_at == NOW + timedelta(seconds=120)  # the chain read still happened
    pump_calls = len([c for c in f.calls if c.startswith(lunch.PUMP_API)])
    run(db, f, NOW + timedelta(seconds=180))
    assert len([c for c in f.calls if c.startswith(lunch.PUMP_API)]) == pump_calls  # backing off: no pump.fun call


def test_graduation_gap_is_measured_from_the_pools_first_candle(db: Session) -> None:
    f = Fake()
    done = {"complete": True, "virtual_token_reserves": lunch.FINAL_VTR, "virtual_sol_reserves": 368_344_648}  # the API's leftover SOL is ignored
    f.graduated = [launch("grad", **done), launch("unindexed", **done), launch("odd", complete=True, virtual_token_reserves=5 * 10**14)]
    curve_price = lunch.final_curve_price(lunch.FINAL_VTR)
    assert curve_price == pytest.approx(4.109e-7, rel=1e-3)  # 115 SOL / 279.9M tokens
    f.pools["grad"] = {"pump-fun": "curve-g", "pumpswap": "pool-g"}  # "unindexed" has no pool yet; "odd" isn't a standard curve
    f.pools["odd"] = {"pumpswap": "pool-odd"}
    f.candles["pool-g"] = [[1792590000, curve_price * 1.02, 0, 0, 0, 0], [1792589940, curve_price * 0.99, 0, 0, 0, 0]]  # newest first
    n = asyncio.run(_measure(db, f))
    g = db.scalar(select(ArbLunchGraduation))
    assert n == 1 and g.curve_price == pytest.approx(curve_price) and g.pool_price == pytest.approx(curve_price * 0.99)
    assert g.gap_pct == pytest.approx(-1.0, abs=1e-3) and g.pool_dex == "pumpswap"
    assert asyncio.run(_measure(db, f)) == 0  # measured once


async def _measure(db: Session, f: Fake) -> int:
    async with httpx.AsyncClient(transport=f.transport()) as c:
        return await lunch.measure_graduations(db, c, NOW)


def test_the_public_page_shows_paper_results_and_the_assumptions(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.routes import arbitrage

    # the page reads "now" from the clock; the bot ran at the test's fixed NOW, so the last-7-days list was empty a week after it
    monkeypatch.setattr(arbitrage, "utcnow", lambda: NOW + timedelta(seconds=60))
    f = Fake()
    f.launches = [launch("hot")]
    f.accounts = {"curve-hot": grown(0.3)}
    run(db, f, NOW)
    f.accounts["curve-hot"] = grown(5.0)
    run(db, f, NOW + timedelta(seconds=60))
    client.cookies.clear()
    body = client.get("/api/members/arbitrage/lunch").json()  # public: no sign-in needed
    assert body["mode"] == "paper" and body["stats"]["open"] == 1 and body["open"][0]["symbol"] == "HOT"
    assert body["open"][0]["pump_url"] == "https://pump.fun/coin/hot"
    assert body["costs"] == {"curve_fee_pct": 1.25, "pool_fee_pct": 0.3, "tx_cost_sol": 0.001}
    assert body["menu"]["size_sol"] == 0.1 and body["days"][0]["launches_seen"] == 1


def test_graduation_run_buys_a_nearly_full_curve_that_is_still_filling_and_sells_at_graduation(db: Session) -> None:
    f = Fake()
    f.near = [launch("full", seconds_ago=3600), launch("stall", seconds_ago=3600), launch("late", seconds_ago=3600)]
    f.accounts = {"curve-full": filled(0.82), "curve-stall": filled(0.85), "curve-late": filled(0.96)}
    out = run(db, f, NOW)  # runs 1 -> 2: the fullest-curves scan runs on even runs
    assert out["near_graduation"] == 3 and out["entered"] == 0  # first reading: nothing to compare with yet
    f.accounts = {"curve-full": filled(0.86), "curve-stall": filled(0.853), "curve-late": filled(0.985)}
    out = run(db, f, NOW + timedelta(minutes=1))
    trades = {t.mint: t for t in db.scalars(select(ArbLunchTrade).where(ArbLunchTrade.strategy == "graduation"))}
    assert set(trades) == {"full"}  # stall rose <1 point; late is past 97%: never bought that close to the line
    t = trades["full"]
    assert t.detail["signal"]["progress_pct"] == 86.0 and t.detail["signal"]["rise_pts"] == 4.0
    f.accounts["curve-full"] = filled(1.0, complete=True)  # it graduates: sold in its PumpSwap pool
    f.pools["full"] = {"pump-fun": "c-full", "pumpswap": "pool-full"}
    final = lunch.final_curve_price(lunch.FINAL_VTR)
    f.candles["pool-full"] = [[1, final, 0, 0, final, 0]]
    run(db, f, NOW + timedelta(minutes=2))
    db.refresh(t)
    assert t.exit_reason == "graduated" and t.pnl_sol > 0  # bought at 86%, sold at the graduation price


def test_graduation_run_has_its_own_stop_and_no_profit_target(db: Session) -> None:
    f = Fake()
    f.near = [launch("full", seconds_ago=3600)]
    f.accounts = {"curve-full": filled(0.82)}
    run(db, f, NOW)
    f.accounts["curve-full"] = filled(0.86)
    run(db, f, NOW + timedelta(minutes=1))
    t = db.scalar(select(ArbLunchTrade).where(ArbLunchTrade.strategy == "graduation"))
    f.accounts["curve-full"] = filled(0.95)  # well up, but no target: it rides to graduation
    run(db, f, NOW + timedelta(minutes=2))
    db.refresh(t)
    assert t.status == "open"
    f.accounts["curve-full"] = filled(0.40)  # dumped: the 25% stop
    run(db, f, NOW + timedelta(minutes=3))
    db.refresh(t)
    assert t.exit_reason == "stop" and t.pnl_sol < 0


def test_both_strategies_can_hold_the_same_launch_and_are_scored_apart(client: TestClient, db: Session) -> None:
    f = Fake()
    f.launches, f.near = [launch("hot")], [launch("hot")]
    f.accounts = {"curve-hot": filled(0.80)}
    lunch._state["runs"] = 0  # next run is odd: no scan yet
    run(db, f, NOW)
    lunch._state["runs"] = 1
    run(db, f, NOW + timedelta(seconds=60))  # even: the scan sees it
    f.accounts["curve-hot"] = filled(0.84)
    run(db, f, NOW + timedelta(seconds=120))
    got = {t.strategy for t in db.scalars(select(ArbLunchTrade).where(ArbLunchTrade.mint == "hot"))}
    assert got == {"starter", "graduation"}
    body = client.get("/api/members/arbitrage/lunch").json()
    assert body["by_strategy"]["starter"]["open"] == 1 and body["by_strategy"]["graduation"]["open"] == 1
    assert {r["strategy"] for r in body["open"]} == {"starter", "graduation"} and body["menu"]["graduation"]["min_progress_pct"] == 80.0


# --- the live feed (PumpPortal): launches join at once; the entry is at the moment the rule was met -----------------------


def stream_msg(kind: str, mint: str, vsol: float, **kw: Any) -> dict[str, Any]:
    vtok = START_VSR * START_VTR / (vsol * 1e9) / 1e6  # whole tokens on the constant-product curve
    return {"txType": kind, "mint": mint, "bondingCurveKey": f"curve-{mint}", "symbol": mint.upper(), "name": mint, "pool": "pump",
            "vSolInBondingCurve": vsol, "vTokensInBondingCurve": vtok, **kw}


def test_the_live_feed_adds_launches_and_enters_at_the_trigger_price(db: Session) -> None:
    from app.arbitrage import lunch_stream

    lunch._triggers.clear()
    lunch._stream_new = 0
    t0 = NOW
    assert lunch_stream.handle(stream_msg("create", "zap", 30.0), t0) == "zap"  # subscribe to its trades
    assert "zap" in lunch._watch and lunch._stream_new == 1
    assert lunch_stream.handle(stream_msg("create", "zap", 30.0), t0) is None  # seen once
    assert lunch_stream.handle({**stream_msg("create", "bonk", 30.0), "pool": "bonk"}, t0) is None  # other launchpads: ignored
    lunch_stream.handle(stream_msg("buy", "zap", 36.0), t0 + timedelta(seconds=5))  # too soon: not a trigger yet
    assert "zap" not in lunch._triggers
    lunch_stream.handle(stream_msg("buy", "zap", 36.0), t0 + timedelta(seconds=20))  # 6 real SOL in, value up ~44%: the rule is met
    trigger_curve, at = lunch._triggers["zap"]
    assert at == t0 + timedelta(seconds=20)
    f = Fake()
    f.accounts = {"curve-zap": grown(15.0)}  # by the next run the price ran further: the paper entry keeps the trigger price
    out = run(db, f, t0 + timedelta(seconds=55))
    t = db.scalar(select(ArbLunchTrade).where(ArbLunchTrade.mint == "zap"))
    assert out["entered"] == 1 and out["new_from_stream"] == 1 and t.strategy == "starter"
    assert t.entry_price == pytest.approx(trigger_curve.price) and t.detail["signal"]["live"] is True
    assert t.detail["signal"]["seconds_before_this_run"] == 35
    assert db.scalar(select(ArbLunchDay)).launches_seen >= 1 and "zap" not in lunch._triggers


def test_the_fast_chain_check_records_the_trigger_without_a_trade_feed() -> None:
    from app.arbitrage import lunch_stream

    lunch_stream.handle(stream_msg("create", "fast", 30.0), NOW)
    c = lunch.parse_curve(grown(1.0))
    assert lunch_stream.check("fast", c, NOW + timedelta(seconds=20)) is False  # 1 SOL in: not momentum yet
    c = lunch.parse_curve(grown(6.0))
    assert lunch_stream.check("fast", c, NOW + timedelta(seconds=5)) is False  # watched for under 10 s
    assert lunch_stream.check("fast", c, NOW + timedelta(seconds=30)) is True
    assert lunch._triggers["fast"][1] == NOW + timedelta(seconds=30)
    assert lunch_stream.check("fast", lunch.parse_curve(grown(9.0)), NOW + timedelta(seconds=40)) is False  # first moment only
    assert lunch_stream.check("unknown", c, NOW) is False


# --- failures: a refusal from pump.fun or the RPC never stops open trades from being priced, closed and saved -------------


def test_a_pump_fun_403_still_prunes_closes_and_commits(db: Session) -> None:
    f = Fake()
    f.launches = [launch("hot"), launch("cold")]
    f.accounts = {"curve-hot": grown(0.3), "curve-cold": grown(0.3)}
    run(db, f, NOW)
    f.accounts["curve-hot"] = grown(5.0)
    run(db, f, NOW + timedelta(seconds=60))
    assert set(lunch._watch) == {"cold"}
    f.pump_status = 403
    later = NOW + timedelta(minutes=lunch.TIMEOUT_MINUTES + 2)
    out = run(db, f, later)
    assert out["pump_error"] == "HTTPStatusError" and out["closed"] == 1 and "pump_rate_limited" not in out
    assert lunch._watch == {}  # "cold" is past its watch window: pruned even though the list read failed
    db.expire_all()
    t = db.scalar(select(ArbLunchTrade))
    assert t.status == "closed" and t.exit_reason == "timeout"  # committed
    assert lunch._state["last_run_at"] == later and lunch._state["last_error"] is None


def test_an_rpc_failure_leaves_trades_unread_not_crashed(db: Session) -> None:
    f = Fake()
    f.launches = [launch("hot")]
    f.accounts = {"curve-hot": grown(0.3)}
    run(db, f, NOW)
    f.accounts["curve-hot"] = grown(5.0)
    run(db, f, NOW + timedelta(seconds=60))
    f.rpc_down = True
    out = run(db, f, NOW + timedelta(seconds=120))
    t = db.scalar(select(ArbLunchTrade))
    assert out["closed"] == 0 and t.status == "open" and t.last_checked_at == NOW + timedelta(seconds=60)  # unread this run


def test_graduation_lookups_are_bounded_by_tries_not_only_successes(db: Session) -> None:
    f = Fake()
    done = {"complete": True, "virtual_token_reserves": lunch.FINAL_VTR}
    f.graduated = [launch(f"g{i}", **done) for i in range(8)]  # none indexed by GeckoTerminal yet
    asyncio.run(_measure(db, f))
    assert len([c for c in f.calls if "/tokens/" in c]) == lunch.GRADUATION_TRIES


def test_the_page_stats_come_from_sql_and_list_at_most_30_closed(client: TestClient, db: Session) -> None:
    for i in range(35):
        db.add(ArbLunchTrade(strategy="starter" if i % 5 else "graduation", mint=f"m{i}", bonding_curve=f"c{i}", symbol="X", name="x", launched_at=NOW,
                             status="closed", opened_at=NOW, closed_at=NOW + timedelta(seconds=i), entry_sol=0.101, tokens=1.0, entry_price=1.0,
                             entry_mc_sol=1.0, exit_sol=0.101 + (0.05 if i % 2 else -0.02), pnl_sol=0.05 if i % 2 else -0.02, exit_reason="target" if i % 2 else "stop"))
    db.commit()
    body = client.get("/api/members/arbitrage/lunch").json()
    st = body["stats"]
    assert st["trades"] == 35 and st["wins"] == 17 and st["win_rate"] == round(17 / 35 * 100, 1)
    assert st["pnl_sol"] == round(17 * 0.05 - 18 * 0.02, 4) and st["best_sol"] == 0.05 and st["worst_sol"] == -0.02
    assert st["by_exit"] == {"target": 17, "stop": 18}
    assert body["by_strategy"]["graduation"]["trades"] == 7 and body["by_strategy"]["starter"]["trades"] == 28
    assert len(body["closed"]) == 30 and body["closed"][0]["mint"] == "m34"


def test_the_live_feed_prunes_stale_launches_and_stops_with_the_bot() -> None:
    from app.arbitrage import lunch_stream

    lunch_stream.handle(stream_msg("create", "stale", 30.0), NOW - timedelta(minutes=lunch.WATCH_MINUTES + 1))
    lunch._triggers["stale"] = (lunch.parse_curve(grown(6.0)), NOW)
    assert lunch.prune(NOW) == 1 and "stale" not in lunch._watch and "stale" not in lunch._triggers

    async def go() -> tuple[bool, bool]:
        lunch_stream._task = asyncio.get_running_loop().create_task(asyncio.sleep(3600))
        lunch_stream._poller = asyncio.get_running_loop().create_task(asyncio.sleep(3600))
        task = lunch_stream._task
        stopped = lunch_stream.stop()
        await asyncio.sleep(0)
        return stopped, task.cancelled()

    assert asyncio.run(go()) == (True, True)
    assert lunch_stream._task is None and lunch_stream._poller is None and lunch_stream.stop() is False


def test_one_ownership_query_for_any_number_of_candidates(db: Session) -> None:
    from sqlalchemy import event

    f = Fake()
    f.launches = [launch(f"m{i}") for i in range(6)]
    f.accounts = {f"curve-m{i}": grown(0.3) for i in range(6)}
    run(db, f, NOW)
    db.add(ArbLunchTrade(strategy="starter", mint="m0", bonding_curve="curve-m0", symbol="M0", name="m0", launched_at=NOW, status="closed",
                         opened_at=NOW, entry_sol=0.101, tokens=1.0, entry_price=1.0, entry_mc_sol=1.0))  # traded before: never entered twice
    db.commit()
    f.accounts = {f"curve-m{i}": grown(6.0) for i in range(6)}  # all six meet the rule
    seen: list[str] = []
    listen = lambda *a: seen.append(a[2])  # noqa: E731 - (conn, cursor, statement, ...)
    event.listen(db.get_bind(), "before_cursor_execute", listen)
    try:
        out = run(db, f, NOW + timedelta(seconds=60))
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", listen)
    trade_selects = [s for s in seen if s.lstrip().upper().startswith("SELECT") and "arb_lunch_trades" in s]
    assert out["entered"] == 5 and len(trade_selects) == 2  # the open trades + ONE ownership query, not one per candidate
    assert db.scalar(select(ArbLunchDay)).entries == 5


@pytest.mark.parametrize("answer", [httpx.Response(200, text="<html>Bad gateway</html>"), httpx.Response(200, json=["not", "an", "object"]),
                                    httpx.Response(200, json={"result": {"value": [{"data": ["%%%not base64", "base64"]}]}})])
def test_a_garbled_rpc_answer_leaves_that_chunk_unread_not_crashed(answer: httpx.Response) -> None:
    async def read() -> dict[str, Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: answer)) as client:
            return await lunch.curves(client, RPC, ["curve-a"])

    assert asyncio.run(read()) == {}


def test_geckoterminal_down_never_blocks_the_other_exits(db: Session) -> None:
    """A graduated trade's pool price can't be read (timeout): the run still closes the others and commits; the graduated one
    waits, then closes at the curve's last price."""
    f = Fake()
    for m in ("a", "c"):
        f.launches.append(launch(m))
        f.accounts[f"curve-{m}"] = grown(0.2)
    run(db, f, NOW)
    for m in ("a", "c"):
        f.accounts[f"curve-{m}"] = grown(20.0)
    assert run(db, f, NOW + timedelta(seconds=60))["entered"] == 2
    trades = {t.mint: t for t in db.scalars(select(ArbLunchTrade))}
    f.accounts["curve-a"] = grown(3.0)  # dumped: stop
    f.accounts["curve-c"] = grown(85.0, complete=True)  # graduated, but GeckoTerminal times out
    inner = f.transport()

    def gecko_down(r: httpx.Request) -> httpx.Response:
        if str(r.url).startswith(lunch.GECKO):
            raise httpx.ReadTimeout("timed out", request=r)
        return inner.handle_request(r)

    later = NOW + timedelta(minutes=2)
    asyncio.run(lunch.tick(db, later, RPC, httpx.MockTransport(gecko_down)))
    db.expire_all()
    assert trades["a"].exit_reason == "stop" and trades["c"].status == "open" and trades["c"].detail.get("awaiting_pool")
    asyncio.run(lunch.tick(db, later + timedelta(minutes=lunch.TIMEOUT_MINUTES + lunch.POOL_WAIT_MINUTES), RPC, httpx.MockTransport(gecko_down)))
    db.expire_all()
    assert trades["c"].exit_reason == "graduated" and "no pool price" in trades["c"].detail["note"]


def test_a_tick_holds_no_write_lock_during_its_reads(db: Session) -> None:
    from tests.locks import http_calls_under_write_lock

    f = Fake()
    f.launches = [launch("hot"), launch("cold")]
    f.accounts = {"curve-hot": grown(0.5), "curve-cold": grown(0.5)}
    with http_calls_under_write_lock() as held:
        run(db, f, NOW)
        f.accounts = {"curve-hot": grown(6.0), "curve-cold": grown(1.0)}
        assert run(db, f, NOW + timedelta(seconds=60))["entered"] == 1
    assert held == []
