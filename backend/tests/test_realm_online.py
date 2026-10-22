"""Vortex Realm online: the ticket is gated like the game, the WebSocket relays heroes, chat and parties between players,
and every number / text from a client is clamped and cleaned (nothing is trusted, nothing is stored)."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.members import realm_online
from app.members.realm_online import Hub
from tests.test_rank_rewards import member_at, min_xp, signed_in


@pytest.fixture(autouse=True)
def fresh_hub(monkeypatch: pytest.MonkeyPatch) -> Hub:
    h = Hub()
    monkeypatch.setattr(realm_online, "hub", h)
    return h


def drain(h: Hub, p: Any) -> list[dict[str, Any]]:
    out = []
    while not p.queue.empty():
        item = p.queue.get_nowait()
        if item is not None:
            out.append(json.loads(item))
    return out


def hello(h: Hub, name: str, member_id: int) -> Any:
    p = h.join(h.redeem(h.mint(member_id, name)))
    h.handle(p, json.dumps({"t": "hello", "look": {"cls": "ranger", "tint": 2}, "s": {"p": [1, 0, 2]}}), now=0)
    return p


# ---------------------------------------------------------------- the hub
def test_players_see_each_other_and_leave_cleanly() -> None:
    h = Hub()
    a = hello(h, "Alice", 1)
    assert drain(h, a)[0] == {"t": "welcome", "id": a.pid, "name": "Alice", "players": []}
    b = hello(h, "Bob", 2)
    welcome = drain(h, b)[0]
    assert welcome["players"][0]["name"] == "Alice" and welcome["players"][0]["look"]["cls"] == "ranger"
    got = drain(h, a)
    assert got[0]["t"] == "join" and got[0]["name"] == "Bob" and "entered the Realm" in got[1]["m"]
    h.handle(b, json.dumps({"t": "s", "p": [5, 1e9, -3], "r": 1.5, "c": "Running_A", "hp": 40, "mh": 50, "lv": 7}), now=1)
    s = drain(h, a)[0]
    assert s["t"] == "s" and s["id"] == b.pid and s["p"] == [5.0, 60.0, -3.0] and s["c"] == "Running_A" and s["lv"] == 7
    h.handle(b, json.dumps({"t": "s", "p": [6, 0, 0]}), now=1.01)  # faster than ~20/s: dropped
    assert drain(h, a) == []
    h.leave(b.pid)
    assert drain(h, a) == [{"t": "bye", "id": b.pid}] and h.online() == 1


def test_one_hero_per_kiwi_and_unique_names() -> None:
    h = Hub()
    a = hello(h, "Kiwi", 1)
    again = hello(h, "Kiwi", 1)  # the same member on another tab: the old session is closed
    assert a.pid not in h.players and again.pid in h.players
    other = hello(h, "Kiwi", 2)
    assert other.name == "Kiwi 2"


def test_chat_is_cleaned_rate_limited_and_party_chat_stays_in_the_party() -> None:
    h = Hub()
    a, b, c = hello(h, "A", 1), hello(h, "B", 2), hello(h, "C", 3)
    for p in (a, b, c):
        drain(h, p)
    h.handle(a, json.dumps({"t": "chat", "m": "  hi‮\x00 there  " + "x" * 300}), now=10)
    m = drain(h, c)[0]
    assert m["t"] == "chat" and m["name"] == "A" and m["m"].startswith("hi there") and len(m["m"]) == 160
    for i in range(6):
        h.handle(a, json.dumps({"t": "chat", "m": f"spam {i}"}), now=10.01 + i * 0.01)
    assert sum(1 for x in drain(h, c) if x["t"] == "chat") == 3  # the burst allowance, then "slow down"
    assert any("Slow down" in x.get("m", "") for x in drain(h, a))
    h.handle(a, json.dumps({"t": "chat", "m": "secret", "to": "party"}), now=30)
    assert "not in a party" in drain(h, a)[-1]["m"]
    h.handle(a, json.dumps({"t": "inv", "id": b.pid}), now=31)
    h.handle(b, json.dumps({"t": "accept", "id": a.pid}), now=32)
    for p in (a, b, c):
        drain(h, p)
    h.handle(a, json.dumps({"t": "chat", "m": "secret", "to": "party"}), now=40)
    assert drain(h, b)[0]["m"] == "secret" and drain(h, c) == []


def test_party_invite_accept_leave_kick_and_limits() -> None:
    h = Hub()
    ps = [hello(h, f"P{i}", i) for i in range(6)]
    lead = ps[0]
    for p in ps:
        drain(h, p)
    h.handle(lead, json.dumps({"t": "inv", "id": ps[1].pid}), now=1)
    inv = drain(h, ps[1])[0]
    assert inv == {"t": "invite", "id": lead.pid, "name": "P0"}
    h.handle(ps[1], json.dumps({"t": "accept", "id": lead.pid}), now=2)
    party = next(x for x in drain(h, ps[1]) if x["t"] == "party")
    assert party["leader"] == lead.pid and party["members"] == [lead.pid, ps[1].pid]
    assert any(x["t"] == "pm" and x["id"] == ps[1].pid for x in drain(h, ps[5]))  # everyone can tint party names
    h.handle(ps[1], json.dumps({"t": "inv", "id": ps[2].pid}), now=3)
    assert "Only the party leader" in drain(h, ps[1])[-1]["m"]
    for i in (2, 3):
        h.handle(lead, json.dumps({"t": "inv", "id": ps[i].pid}), now=4 + i)
        h.handle(ps[i], json.dumps({"t": "accept", "id": lead.pid}), now=4 + i)
    h.handle(lead, json.dumps({"t": "inv", "id": ps[4].pid}), now=10)
    assert "full" in drain(h, lead)[-1]["m"]
    # an expired invite can't be used
    h.handle(ps[4], json.dumps({"t": "accept", "id": ps[5].pid}), now=11)
    assert "expired" in drain(h, ps[4])[-1]["m"]
    # a kill shares XP with the party only (their own game decides who's close enough)
    h.handle(lead, json.dumps({"t": "kill", "xp": 99999, "x": 3, "z": 4}), now=12)
    share = [x for x in drain(h, ps[2]) if x["t"] == "pxp"][0]
    assert share["xp"] == 500 and share["id"] == lead.pid and not [x for x in drain(h, ps[4]) if x["t"] == "pxp"]
    # the leader removes one, another leaves, the leader leaves: the last one is told the party broke up
    h.handle(lead, json.dumps({"t": "kick", "id": ps[3].pid}), now=13)
    assert ps[3].party is None and ps[3].pid not in h.parties[lead.party]
    h.handle(ps[2], json.dumps({"t": "leave"}), now=14)
    h.handle(lead, json.dumps({"t": "leave"}), now=15)
    assert ps[1].party is None and h.parties == {}
    assert any("broke up" in x.get("m", "") for x in drain(h, ps[1]))


def test_garbage_and_oversize_messages_are_ignored() -> None:
    h = Hub()
    a = h.join(h.redeem(h.mint(1, "A")))
    for raw in ("nope", "[]", json.dumps({"t": "s", "p": [1, 2, 3]}), "{" + " " * 5000 + "}"):
        h.handle(a, raw, now=1)
    assert drain(h, a) == [] and not a.look  # nothing before hello
    h.handle(a, json.dumps({"t": "hello", "look": {"cls": "<script>", "tint": 99, "eq": {"weapon": "../../x"}}}), now=2)
    assert a.look == {"cls": "knight", "tint": 31, "helm": True, "eq": {"weapon": "", "armor": "", "helm": "", "shield": ""}}
    assert h.redeem("made-up") is None and h.redeem(h.mint(1, "A", now=0), now=10**9) is None


# ---------------------------------------------------------------- the API
def test_ticket_is_warrior_and_up(db: Session, client: TestClient) -> None:
    assert client.get("/api/members/realm/online/ticket").status_code == 403
    c = signed_in(client, db, member_at(db, min_xp(db, "warrior") - 1, "almost"))
    assert c.get("/api/members/realm/online/ticket").status_code == 403
    c = signed_in(client, db, member_at(db, min_xp(db, "warrior"), "warrior"))
    r = c.get("/api/members/realm/online/ticket").json()
    assert r["name"] == "warrior" and len(r["ticket"]) > 20 and r["ws_url"].endswith("/api/members/realm/ws")


def test_websocket_relays_between_two_players(db: Session, client: TestClient) -> None:
    a_t = realm_online.hub.mint(1, "Alice")
    b_t = realm_online.hub.mint(2, "Bob")
    with pytest.raises(Exception):  # noqa: B017 - a bad ticket is refused before the socket opens
        with client.websocket_connect("/api/members/realm/ws?ticket=bad") as ws:
            ws.receive_text()
    with client.websocket_connect(f"/api/members/realm/ws?ticket={a_t}") as a:
        a.send_text(json.dumps({"t": "hello", "look": {"cls": "knight"}}))
        assert json.loads(a.receive_text())["t"] == "welcome"
        with client.websocket_connect(f"/api/members/realm/ws?ticket={b_t}") as b:
            b.send_text(json.dumps({"t": "hello", "look": {"cls": "sorceress"}}))
            w = json.loads(b.receive_text())
            assert w["t"] == "welcome" and w["players"][0]["name"] == "Alice"
            assert json.loads(a.receive_text())["name"] == "Bob"  # join
            json.loads(a.receive_text())  # "Bob entered the Realm."
            b.send_text(json.dumps({"t": "chat", "m": "hello Alice"}))
            msg = json.loads(a.receive_text())
            assert msg["t"] == "chat" and msg["m"] == "hello Alice" and msg["name"] == "Bob"
        assert json.loads(a.receive_text()) == {"t": "bye", "id": w["id"]}
    assert realm_online.hub.online() == 0


def test_websocket_refuses_a_foreign_origin(client: TestClient) -> None:
    t = realm_online.hub.mint(1, "A")
    with pytest.raises(Exception):  # noqa: B017
        with client.websocket_connect(f"/api/members/realm/ws?ticket={t}", headers={"Origin": "https://evil.example"}) as ws:
            ws.receive_text()
