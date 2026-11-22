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


# --- sidecar health (2026-11-22: an orphaned sidecar held the port; /api/local-status needs no token, so the bridge thought it was its
# own copy and pushed "signals: 0" for hours while every feed answered 401)

class FakeSidecarHttp:
    """127.0.0.1:<port> answered by a sidecar whose token is `token` (None: nothing listening)."""

    def __init__(self, token: str | None) -> None:
        self.token = token

    def get(self, url: str, headers: dict[str, str] | None = None, timeout: float = 0) -> httpx.Response:
        req = httpx.Request("GET", url)
        if self.token is None:
            raise httpx.ConnectError("refused", request=req)
        if url.endswith("/api/local-status"):
            return httpx.Response(200, json={"success": True}, request=req)
        if (headers or {}).get("Authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": "Unauthorized"}, request=req)
        return httpx.Response(405, json={"error": "POST required"}, request=req)


def _side(monkeypatch: Any, tmp_path: Any, holder_token: str | None) -> "wm.Sidecar":
    side = wm.Sidecar(tmp_path, port=46199)
    http = FakeSidecarHttp(holder_token)
    monkeypatch.setattr(wm.httpx, "get", http.get)
    monkeypatch.setattr(wm.time, "sleep", lambda s: None)
    side._http = http  # type: ignore[attr-defined]
    return side


def test_probe_tells_our_sidecar_from_an_orphan_that_rejects_our_token(monkeypatch: Any, tmp_path: Any) -> None:
    side = _side(monkeypatch, tmp_path, None)
    assert side.probe() == "down" and not side.alive()
    side._http.token = side.token  # type: ignore[attr-defined]
    assert side.probe() == "ours" and side.alive()
    side._http.token = "an-earlier-run"  # type: ignore[attr-defined]
    assert side.probe() == "foreign" and not side.alive()  # /api/local-status alone said success: the 11-22 bug


def _localappdata(monkeypatch: Any, tmp_path: Any) -> str:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    return str(tmp_path / "AppData" / "Local" / "World Monitor" / "sidecar" / "node" / "node.exe")


def test_only_world_monitors_own_sidecar_node_counts_as_ours_to_stop(monkeypatch: Any, tmp_path: Any) -> None:
    node = _localappdata(monkeypatch, tmp_path)
    assert wm.is_wm_sidecar(node)
    assert not wm.is_wm_sidecar(str(tmp_path / "Program Files" / "nodejs" / "node.exe"))  # a separate Node install
    assert not wm.is_wm_sidecar(str(tmp_path / "AppData" / "Local" / "World Monitor" / "World Monitor.exe"))  # the app window itself
    assert not wm.is_wm_sidecar(str(tmp_path / "AppData" / "Local" / "World Monitor" / "sidecar" / "evil.exe"))
    assert not wm.is_wm_sidecar(str(tmp_path / "AppData" / "Local" / "World Monitor" / "sidecar" / ".." / "node.exe"))
    assert not wm.is_wm_sidecar(None) and not wm.is_wm_sidecar("")
    monkeypatch.delenv("LOCALAPPDATA")
    assert not wm.is_wm_sidecar(node)


def test_netstat_listening_row_is_found_in_any_language() -> None:
    out = ("\n  Proto  Adresse locale         Adresse distante       Etat            PID\n"
           "  TCP    127.0.0.1:46124        127.0.0.1:50000        ESTABLISHED     111\n"
           "  TCP    127.0.0.1:46124        0.0.0.0:0              ABHOEREN        4242\n"
           "  TCP    127.0.0.1:46123        0.0.0.0:0              LISTENING       999\n")
    assert wm.parse_listening_pid(out, 46124) == 4242
    assert wm.parse_listening_pid(out, 46125) is None


def _fake_popen(side: "wm.Sidecar", started: list[Any]) -> Any:
    class P:
        pid = 777

        def __init__(self, *a: Any, **k: Any) -> None:
            started.append(k["env"]["LOCAL_API_TOKEN"])
            side._http.token = k["env"]["LOCAL_API_TOKEN"]  # type: ignore[attr-defined]  # our fresh sidecar now holds the port

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            pass

        def wait(self, t: float) -> None:
            pass

    return P


def _never(*a: Any) -> Any:
    raise AssertionError("must not be called")


def test_an_orphaned_world_monitor_sidecar_is_stopped_and_a_fresh_one_started(monkeypatch: Any, tmp_path: Any) -> None:
    node = _localappdata(monkeypatch, tmp_path)
    side = _side(monkeypatch, tmp_path, "an-earlier-run")
    killed: list[int] = []
    holder: dict[str, Any] = {"pid": 4242}
    started: list[Any] = []
    monkeypatch.setattr(wm, "listening_pid", lambda port: holder["pid"])
    monkeypatch.setattr(wm, "process_path", lambda pid: node)

    def term(pid: int) -> bool:
        killed.append(pid)
        holder["pid"] = None
        side._http.token = None  # type: ignore[attr-defined]
        return True

    monkeypatch.setattr(wm, "terminate_wm_sidecar", term)
    monkeypatch.setattr(wm.subprocess, "Popen", _fake_popen(side, started))
    assert side.ensure() is True
    assert killed == [4242] and started == [side.token] and side.note == ""


def test_a_port_held_by_any_other_program_is_left_alone(monkeypatch: Any, tmp_path: Any, caplog: Any) -> None:
    _localappdata(monkeypatch, tmp_path)
    side = _side(monkeypatch, tmp_path, "someone-else")
    started: list[Any] = []
    monkeypatch.setattr(wm, "listening_pid", lambda port: 5150)
    monkeypatch.setattr(wm, "process_path", lambda pid: str(tmp_path / "Program Files" / "nodejs" / "node.exe"))
    monkeypatch.setattr(wm, "terminate_wm_sidecar", _never)
    monkeypatch.setattr(wm.subprocess, "Popen", _fake_popen(side, started))
    with caplog.at_level("ERROR", logger="worldmonitor"):
        assert side.ensure() is False
    assert started == [] and "pid 5150" in side.note and "left alone" in caplog.text
    snap = wm.down_snapshot(side.note)
    assert snap["feeds_ok"] == 0 and snap["feeds_failed"] == len(wm.FEEDS) and snap["signals"] == []
    assert all("left alone" in f["note"] for f in snap["feeds"].values())


def test_our_own_running_sidecar_is_kept(monkeypatch: Any, tmp_path: Any) -> None:
    side = _side(monkeypatch, tmp_path, None)
    started: list[Any] = []
    monkeypatch.setattr(wm, "listening_pid", _never)
    monkeypatch.setattr(wm.subprocess, "Popen", _fake_popen(side, started))
    assert side.ensure() and len(started) == 1
    assert side.ensure() and len(started) == 1  # alive and accepting our token: not restarted


def test_feed_counts_split_answered_failed_and_keyless() -> None:
    def get(route: str) -> httpx.Response:
        req = httpx.Request("GET", f"http://x/api/{route}")
        if route == "earthquakes":
            return httpx.Response(200, json=QUAKES, request=req)
        if route == "acled-conflict":
            return httpx.Response(200, json={"error": "ACLED not configured", "data": [], "configured": False}, request=req)
        return httpx.Response(401, json={"error": "Unauthorized"}, request=req)

    snap = wm.collect(get)
    assert snap["feeds_ok"] == 1 and snap["feeds_failed"] == len(wm.FEEDS) - 2


def test_push_falls_back_for_a_server_that_predates_the_new_fields(monkeypatch: Any) -> None:
    sent: list[dict[str, Any]] = []

    def post(url: str, json: dict[str, Any], timeout: float, headers: dict[str, str]) -> httpx.Response:
        sent.append(json)
        req = httpx.Request("POST", url)
        if "feeds_ok" in json:
            return httpx.Response(422, json={"detail": "extra fields not permitted"}, request=req)
        return httpx.Response(200, json={"ok": True}, request=req)

    monkeypatch.setattr(wm.httpx, "post", post)
    assert wm.push("https://s", "pcw_x", {"signals": [], "feeds_ok": 3, "feeds_failed": 0}) == {"ok": True}
    assert len(sent) == 2 and "feeds_ok" not in sent[1]
