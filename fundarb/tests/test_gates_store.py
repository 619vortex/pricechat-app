"""Phase B: operator overrides, the capital model, the Module 2 gates and Module 6 persistence. TEST FIXTURES only:
made-up gate inputs, plus the recorded Hyperliquid order book (hl_l2book.json)."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import Router, fixture, settings

from fundarb.capital import market_bucket, pair_capital
from fundarb.gates import GateConfig, cheap_pass, decide, oracle_stats, parse_book, read_books, run_gates
from fundarb.store import UniverseStore
from fundarb.universe import AssetClass, classify, load_overrides, pairing_rule

CFG = GateConfig(min_volume_24h_usd=5e6, min_oi_usd=2e6, max_spread_bps=10, min_depth_usd=250_000, max_oi_cap_share=0.8,
                 oracle_min_samples=100, oracle_persistent_bps=50, oracle_stable_std_bps=10, oracle_recent_samples=20)
BOOK = {"spread_bps": 1.0, "depth_usd_1pct": 1e6, "complete": True, "source": "l2Book full precision"}


def x(**kw: Any) -> dict[str, Any]:
    base = {"raw_name": "TEST", "pairing_eligible": True, "pairing_note": "eligible", "listing_live": True, "funding_interval_hours": 1.0,
            "volume_24h_usd": 1e7, "open_interest_usd": 5e6, "oi_cap_usd": None, "impact_spread_bps": 2.0, "basis_bps": 1.0,
            "book": BOOK, "book_error": None, "oracle": {"n": 0, "p99_abs_bps": None, "recent_bps": []}}
    return {**base, **kw}


# --- overrides ------------------------------------------------------------------------------------------------------

def test_the_shipped_overrides_load_and_win_over_the_registry() -> None:
    ov = load_overrides(settings().universe_overrides_path)
    assert classify("io", "io:EWY", {}, ov)[0] is AssetClass.EQUITY  # missing from the registry, classified by the operator
    for name in ("para:BTCD", "para:TOTAL2", "para:OTHERS"):
        cls, rule = classify("para", name, {name: "crypto"}, ov)
        assert cls is AssetClass.INDEX and rule.startswith("override:")
        assert pairing_rule(name, cls, ov)[0] is False  # no second leg for funding arb
    assert pairing_rule("io:EWY", AssetClass.EQUITY, ov) == (True, "eligible")
    assert pairing_rule("mkts:BVIV", AssetClass.UNCLASSIFIED, ov)[0] is False  # unclassified stays out of the pool


def test_an_override_without_a_reason_or_with_an_unknown_class_fails_fast(tmp_path: Path) -> None:
    p = tmp_path / "o.json"
    p.write_text(json.dumps({"markets": {"a:X": {"class": "EQUITY", "reason": ""}}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_overrides(p)
    p.write_text(json.dumps({"markets": {"a:X": {"class": "STOCKZ", "reason": "r"}}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_overrides(p)


# --- capital model --------------------------------------------------------------------------------------------------

def test_cross_margin_only_inside_one_account_with_one_collateral() -> None:
    native, xyz = market_bucket(False, "unset", "USDC"), market_bucket(False, "unset", "USDC", dex="xyz")
    iso, usdh = market_bucket(False, "noCross", "USDC", dex="xyz"), market_bucket(False, "unset", "USDH", dex="flx")
    assert native == "cross:hyperliquid:native:USDC" and iso.startswith("isolated:")
    assert market_bucket(True, "unset", "USDC").startswith("isolated:")  # onlyIsolated
    assert market_bucket(False, "strictIsolated", "USDC").startswith("isolated:")
    same = pair_capital(native, native, 2.0, 0.25)
    assert same.mode == "cross" and same.capital_per_notional == pytest.approx(2.25 / 2)
    for a, b, why in ((native, xyz, "separate margin accounts"), (native, usdh, "different collateral"), (xyz, iso, "isolated-only")):
        c = pair_capital(a, b, 2.0, 0.25)
        assert c.mode == "isolated" and why in c.reason and c.capital_per_notional == pytest.approx(2.5 / 2)  # the 2.5x tax at 1x
    lighter = market_bucket(False, "unset", "USDC", venue="lighter")
    assert pair_capital(native, lighter, 2.0, 0.25).mode == "isolated"  # cross-venue: always two accounts


# --- gates ----------------------------------------------------------------------------------------------------------

def test_a_market_passing_everything() -> None:
    d = decide(x(), CFG)
    assert d["passed"] and d["failed_gate"] is None
    assert [c["gate"] for c in d["checks"]] == ["pairing_eligible", "listing_live", "funding_interval", "volume_24h", "open_interest",
                                                 "oi_cap", "spread", "depth", "oracle"]


@pytest.mark.parametrize(("over", "gate"), [
    ({"pairing_eligible": False}, "pairing_eligible"), ({"listing_live": False}, "listing_live"),
    ({"funding_interval_hours": None}, "funding_interval"), ({"funding_interval_hours": 0}, "funding_interval"),
    ({"volume_24h_usd": 4_999_999}, "volume_24h"), ({"open_interest_usd": 1e6}, "open_interest"),
    ({"oi_cap_usd": 5.5e6}, "oi_cap"), ({"book": {**BOOK, "spread_bps": 10.5}}, "spread"),
    ({"book": {**BOOK, "depth_usd_1pct": 249_999}}, "depth"), ({"basis_bps": None}, "oracle"),
])
def test_each_gate_rejects_and_says_which(over: dict[str, Any], gate: str) -> None:
    d = decide(x(**over), CFG)
    assert not d["passed"] and d["failed_gate"] == gate


def test_oi_cap_at_80_percent_passes_and_above_fails() -> None:
    assert decide(x(open_interest_usd=8e6, oi_cap_usd=1e7), CFG)["passed"]
    assert decide(x(open_interest_usd=8.01e6, oi_cap_usd=1e7), CFG)["failed_gate"] == "oi_cap"


def test_book_gates_are_not_measured_when_a_cheap_gate_failed_and_fail_when_the_book_is_missing() -> None:
    d = decide(x(volume_24h_usd=1, book=None), CFG)
    assert {c["gate"]: c["ok"] for c in d["checks"]}["depth"] is None and d["failed_gate"] == "volume_24h"
    d = decide(x(book=None, book_error="HTTPStatusError: 500"), CFG)
    assert d["failed_gate"] == "spread" and "500" in d["checks"][6]["note"]


def test_oracle_gate_uses_the_markets_own_99th_percentile_after_warm_up() -> None:
    hist = [float(i % 10) for i in range(200)]  # |basis| 0..9 bps
    o = oracle_stats(hist, CFG)
    assert o["n"] == 200 and o["p99_abs_bps"] == 9.0 and len(o["recent_bps"]) == 19
    assert decide(x(basis_bps=9.0, oracle=o), CFG)["passed"]
    assert decide(x(basis_bps=-9.5, oracle=o), CFG)["failed_gate"] == "oracle"  # beyond its own p99, either sign
    warm = decide(x(basis_bps=500.0, oracle=oracle_stats(hist[:50], CFG)), CFG)
    assert warm["passed"] and "warming up (50/100)" in warm["flags"][0]


def test_a_persistent_stable_basis_is_flagged_not_rejected() -> None:
    hist = [60.0 + (i % 3) for i in range(150)]
    d = decide(x(basis_bps=61.0, oracle=oracle_stats(hist, CFG)), CFG)
    assert d["passed"] and any("thin or lagging oracle" in f for f in d["flags"])
    noisy = [60.0 + (i % 2) * 40 for i in range(150)]
    assert not any("lagging" in f for f in decide(x(basis_bps=60.0, oracle=oracle_stats(noisy, CFG)), CFG)["flags"])


def test_report_counts_every_market_and_names_the_top_rejects_by_volume() -> None:
    inputs = [x(raw_name="OK"), x(raw_name="THIN", volume_24h_usd=1e6), x(raw_name="DEAD", listing_live=False, volume_24h_usd=0, book=None)]
    rep = run_gates(inputs, CFG, "r1", "t", {"OK": 1e7, "THIN": 1e6, "DEAD": 0})
    assert rep.total == 3 and rep.passed == ["OK"]
    assert [(r["raw_name"], r["gate"]) for r in rep.top_rejects] == [("THIN", "volume_24h"), ("DEAD", "listing_live")]
    assert rep.counts["volume_24h"] == {"pass": 1, "fail": 2, "not_measured": 0}
    assert cheap_pass(x(), CFG) and not cheap_pass(x(open_interest_usd=0), CFG)


def test_parse_the_recorded_book_spread_and_depth() -> None:
    raw = fixture("hl_l2book")
    b = parse_book(raw, "l2Book full precision")
    bids, asks = raw["levels"]
    bid, ask = float(bids[0]["px"]), float(asks[0]["px"])
    assert b is not None and b.spread_bps == pytest.approx((ask - bid) / ((ask + bid) / 2) * 1e4)
    assert b.depth_usd_1pct > 0 and b.complete is False  # 20 BTC levels span far less than 1%: a lower bound, labelled
    assert parse_book({"levels": [[], []]}, "x") is None


def test_read_books_falls_back_to_the_aggregated_book_only_when_needed() -> None:
    def handle(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture("hl_l2book"))
    router = Router(handle)
    books, fails, calls = asyncio.run(read_books(settings(), ["BTC"], router.transport(), min_depth=1e12))
    assert not fails and calls == 2 and "nSigFigs=3" in books["BTC"].source
    books, _, calls = asyncio.run(read_books(settings(), ["BTC"], Router(handle).transport(), min_depth=0))
    assert calls == 1 and books["BTC"].source == "l2Book full precision"


# --- persistence + replay -------------------------------------------------------------------------------------------

def test_store_round_trip_history_and_exact_replay(tmp_path: Path) -> None:
    st = UniverseStore(tmp_path / "u.sqlite3")
    st.save_quotes([{"ts": f"2026-10-24T00:{i:02d}:00", "raw_name": "TEST", "basis_bps": float(i), **{k: None for k in (
        "funding_rate_raw", "funding_interval_hours", "funding_apr", "premium", "mark_price", "oracle_price", "open_interest_usd",
        "volume_24h_usd", "max_leverage", "spread_bps", "spread_source", "depth_usd_1pct", "depth_source")}} for i in range(5)])
    assert st.basis_history(["TEST"], before_ts="2026-10-24T00:03:00") == {"TEST": [0.0, 1.0, 2.0]}  # strictly before, oldest first
    inputs = [x(raw_name="A"), x(raw_name="B", volume_24h_usd=1)]
    rep = run_gates(inputs, CFG, "run-1", "2026-10-24T00:10:00")
    from dataclasses import asdict
    st.save_gate_run({"run_id": "run-1", "ts": rep.ts, "schema_hash": "h", "config": asdict(CFG), "counts": rep.counts, "elapsed_s": 1.0,
                      "calls": 3}, rep.results)
    run, results = st.load_gate_run("run-1")
    assert st.latest_gate_run() == "run-1" and GateConfig(**run["config"]) == CFG
    for r in results:
        assert decide(r["inputs"], GateConfig(**run["config"])) == {"passed": bool(r["passed"]), "failed_gate": r["failed_gate"],
                                                                    "checks": r["checks"], "flags": r["flags"]}
    assert decide(results[1]["inputs"], replace(CFG, min_volume_24h_usd=0))["passed"]  # a different config gives a different answer
    assert st.counts()["gate_results"] == 2
