"""G2 source verification (app/growth/verify.py): a finding's stored inputs are re-read from the source; any difference, or a source that
can't be read, refuses the draft. A fake Hyperliquid / alternative.me answers here; no real network."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import httpx

from app.growth import collectors, verify
from app.growth.findings import Finding, chart, n
from app.growth.memory import Memory

AS_OF = date(2026, 11, 25)
DAYS = [AS_OF - timedelta(days=i) for i in range(40)][::-1]
CLOSES = {d: 100.0 + i for i, d in enumerate(DAYS)}


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def fake(changed: dict[date, float] | None = None, down: bool = False) -> collectors.Http:
    live = {**CLOSES, **(changed or {})}

    def handler(request: httpx.Request) -> httpx.Response:
        if down:
            return httpx.Response(503)
        if request.url.host == "api.hyperliquid.xyz":
            body = json.loads(request.content)
            req = body["req"]
            rows = [{"t": _ms(d), "c": str(v), "v": "10", "n": 5} for d, v in live.items() if req["startTime"] <= _ms(d) <= req["endTime"]]
            return httpx.Response(200, json=rows)
        if request.url.host == "api.alternative.me":
            return httpx.Response(200, json={"data": [{"timestamp": str(_ms(d) // 1000), "value": "13"} for d in DAYS]})
        return httpx.Response(404)

    return collectors.Http(transport=httpx.MockTransport(handler), sleep=lambda _: None, retries=1)


def finding(series: list[str]) -> Finding:
    return Finding(key="t:1", format="extremes", detector="rarity", subject="BTC", headline="h", numbers={"x": n(1, "1", "one")}, words={},
                   templates=["{x}"], window="30d", series=series, sources=["hyperliquid"], as_of=AS_OF, calculation="c", rarity=0.9,
                   chart=chart("rolling_line", "t", "", [], sources=["hyperliquid"], as_of=AS_OF))


def mem() -> Memory:
    return Memory.from_points({"px:BTC:close": list(CLOSES.items()), "fng": [(d, 13.0) for d in DAYS]})


def test_matching_data_verifies_every_stored_value() -> None:
    r = verify.Verifier(mem(), fake()).check(finding(["px:BTC:close", "fng"]))
    assert r["ok"] and r["values_checked"] == 80 and r["problems"] == []
    assert r["series"]["px:BTC:close"]["source"] == "Hyperliquid candleSnapshot" and r["series"]["px:BTC:close"]["max_diff_pct"] == 0


def test_a_changed_close_refuses_the_finding() -> None:
    r = verify.Verifier(mem(), fake({DAYS[10]: 999.0})).check(finding(["px:BTC:close"]))
    assert not r["ok"] and DAYS[10].isoformat() in r["problems"][0] and "Hyperliquid 999" in r["problems"][0]


def test_a_source_that_cannot_be_read_refuses_the_finding() -> None:
    r = verify.Verifier(mem(), fake(down=True)).check(finding(["px:BTC:close"]))
    assert not r["ok"] and "couldn't be read" in r["problems"][0]


def test_an_unknown_series_is_never_waved_through() -> None:
    r = verify.Verifier(mem(), fake()).check(finding(["liq:BTC:long"]))
    assert not r["ok"] and "no verifier" in r["problems"][0]
