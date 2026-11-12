"""World Monitor bridge: each feed becomes the right signals, a broken or keyless feed never stops the snapshot, and the app never sees
the Price Chat worker token."""

from typing import Any

import httpx

from pricechat_worker import worldmonitor as wm

QUAKES = {"features": [
    {"id": "a", "properties": {"mag": 7.2, "place": "Kermadec Islands", "time": 1794456452379, "tsunami": 1, "url": "https://earthquake.usgs.gov/a", "status": "reviewed"},
     "geometry": {"coordinates": [-177.1, -29.3, 10]}},
    {"id": "b", "properties": {"mag": 5.1, "place": "Noda, Japan", "time": 1794456000000, "tsunami": 0, "url": "https://earthquake.usgs.gov/b"},
     "geometry": {"coordinates": [141.6, 35.9, 30]}},
    {"id": "c", "properties": {"mag": None}},
]}
FAA = ("<AIRPORT_STATUS_INFORMATION><Update_Time>now</Update_Time>"
       "<Delay_type><Name>Ground Delay Programs</Name><Ground_Delay_List><Ground_Delay><ARPT>SEA</ARPT><Reason>wind</Reason><Avg>32 minutes</Avg><Max>1 hour</Max>"
       "</Ground_Delay></Ground_Delay_List></Delay_type>"
       "<Delay_type><Name>Ground Stop Programs</Name><Program><ARPT>JFK</ARPT><Reason>thunderstorms</Reason><End_Time>6:00 pm</End_Time></Program></Delay_type>"
       "<Delay_type><Name>Airport Closures</Name><Airport_Closure_List><Airport><ARPT>SNA</ARPT><Reason>NOTAM</Reason></Airport></Airport_Closure_List></Delay_type>"
       "</AIRPORT_STATUS_INFORMATION>")


def test_earthquakes_are_ranked_by_magnitude_and_tsunami_flag() -> None:
    sig, summ = wm.from_earthquakes(QUAKES)
    assert summ["count"] == 2
    assert sig[0]["severity"] == "critical" and "tsunami" in sig[0]["title"] and sig[0]["lat"] == -29.3 and sig[0]["lon"] == -177.1
    assert sig[1]["severity"] == "elevated" and sig[1]["title"].startswith("M5.1 earthquake")


def test_faa_ground_stops_outrank_delays_and_closures() -> None:
    sig, _ = wm.from_faa(FAA)
    by = {s["title"].split(":")[0]: s for s in sig}
    assert by["JFK"]["severity"] == "high" and "ground stop" in by["JFK"]["title"]
    assert by["SEA"]["severity"] == "elevated" and "avg 32 minutes" in by["SEA"]["title"]
    assert by["SNA"]["severity"] == "low"


def test_only_unusual_theaters_and_risky_countries_become_signals() -> None:
    sig, summ = wm.from_theaters({"timestamp": "t", "postures": [
        {"theaterId": "iran", "shortName": "IRAN", "postureLevel": "normal", "totalAircraft": 0},
        {"theaterId": "taiwan", "shortName": "TWN", "postureLevel": "elevated", "headline": "Elevated activity - Taiwan Strait", "centerLat": 24, "centerLon": 120}]})
    assert [s["id"] for s in sig] == ["theater:taiwan"] and summ["count"] == 2
    sig, summ = wm.from_risk({"cii": [{"code": "UA", "name": "Ukraine", "score": 59, "level": "high"}, {"code": "CH", "name": "Switzerland", "score": 5, "level": "low"}]})
    assert [s["id"] for s in sig] == ["cii:UA"] and summ["top"][0]["code"] == "UA"


def test_a_broken_or_keyless_feed_never_stops_the_snapshot() -> None:
    def get(route: str) -> httpx.Response:
        req = httpx.Request("GET", f"http://x/api/{route}")
        if route == "earthquakes":
            return httpx.Response(200, json=QUAKES, request=req)
        if route == "faa-status":
            return httpx.Response(200, text="<not xml", request=req)
        if route == "acled-conflict":
            return httpx.Response(200, json={"error": "ACLED not configured", "data": [], "configured": False}, request=req)
        if route == "risk-scores":
            raise httpx.ConnectTimeout("slow")
        return httpx.Response(500, json={}, request=req)

    snap = wm.collect(get)
    f = snap["feeds"]
    assert f["earthquakes"]["ok"] and f["earthquakes"]["count"] == 2
    assert not f["faa-status"]["ok"] and "ParseError" in f["faa-status"]["note"]
    assert not f["acled-conflict"]["ok"] and f["acled-conflict"]["note"] == "ACLED not configured"
    assert not f["risk-scores"]["ok"] and not f["macro-signals"]["ok"]
    assert snap["signals"][0]["severity"] == "critical"


def test_the_app_never_gets_our_server_token(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("PRICECHAT_WORKER_TOKEN", "pcw_secret")
    monkeypatch.setenv("WM_ACLED_ACCESS_TOKEN", "acled123")
    env = wm.Sidecar(tmp_path, port=46199).env()
    assert "PRICECHAT_WORKER_TOKEN" not in env and env["ACLED_ACCESS_TOKEN"] == "acled123"
    assert env["LOCAL_API_PORT"] == "46199" and len(env["LOCAL_API_TOKEN"]) == 48 and env["LOCAL_API_CLOUD_FALLBACK"] == "false"
