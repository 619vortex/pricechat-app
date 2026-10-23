"""Kiwi AI Investment Committee: the downloadable HTML report, the track record shape, and no member data in public answers.

Fixtures are real rules-backend runs (BTC 1h Hyperliquid perp, DOGE 4h Hyperliquid perp, TAO 1h OKX spot)."""

import copy
import json
import re
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.committee import report
from app.db.base import utcnow
from app.models.committee import CmOutcome, CmRun

RUNS = Path(__file__).parent / "committee_runs"
EVIL = '<script>alert("x")</script><img src=x onerror=alert(1)>'


def load(sym: str) -> dict[str, Any]:
    return json.loads((RUNS / f"{sym}.json").read_text(encoding="utf-8"))


def store(db: Session, run: dict[str, Any], *, pid: str, tg: int | None = 4242, member: int | None = None) -> CmRun:
    a = run["asset"]
    row = CmRun(public_id=pid, symbol=a["symbol"], venue=a["venue"], market_type=a["market_type"], market_id=a["market_id"],
                timeframe=run["timeframe"], mode="live", depth="full", status="done", requested_by_tg=tg, requested_by_member=member,
                chat_id=-100555, message_id=77, created_at=utcnow() - timedelta(hours=30), finished_at=utcnow(),
                price_at=run["price"]["value"], verdict=run["verdict"]["action"], rating=run["verdict"]["rating"],
                exec_status=run["execution"]["status"], backend="rules", cost_usd=0, calls=0, result={**run, "id": pid})
    db.add(row)
    db.commit()
    return row


class Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.links: list[dict[str, str | None]] = []
        self.handlers: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        d = dict(attrs)
        self.handlers += [k for k in d if k.startswith("on")]
        if tag == "a":
            self.links.append(d)


def parse(doc: str) -> Tags:
    p = Tags()
    p.feed(doc)
    return p


@pytest.mark.parametrize("sym", ["btc", "doge", "tao"])
def test_report_renders_every_section_for_real_runs(sym: str) -> None:
    run = load(sym)
    doc = report.render(run, {}, None)
    for heading in ("Analysts", "Bull vs bear", "Trader proposal", "Risk committee", "Kiwi risk engine", "Scenarios", "What changes the view",
                    "Subsequent performance", "Sources", "How this report was made"):
        assert f">{heading}<" in doc, heading
    assert "Research only — nothing was traded." in doc and "not financial advice" in doc
    assert run["asset"]["symbol"] in doc and run["verdict"]["rating"] in doc
    assert "Potential LONG" in doc and "Potential SHORT" in doc
    # full derivation strings are shown (escaped)
    long = run["verdict"]["scenarios"]["long"]
    assert report.e(long["derivation"]) in doc and report.e(long["trigger_rule"]) in doc
    t = parse(doc)
    assert "script" not in t.tags and "link" not in t.tags and "img" not in t.tags and "iframe" not in t.tags
    assert "http" not in re.sub(r'href="https?://[^"]+"', "", doc.split("<style>")[1].split("</style>")[0])  # CSS loads nothing
    for a in t.links:
        href = a.get("href") or ""
        assert href.startswith("#ref-") or (href.startswith("https://") or href.startswith("http://"))
        if not href.startswith("#"):
            assert a.get("rel") == "noopener noreferrer"
    assert "prefers-color-scheme:dark" in doc and "@media print" in doc
    if sym == "tao":
        assert "OKX spot TAO-USDT" in doc and "only evaluates Hyperliquid perpetuals" in doc


def test_model_text_is_escaped_and_unsafe_links_are_dropped() -> None:
    run = copy.deepcopy(load("btc"))
    run["verdict"]["summary"] = EVIL
    run["analysts"]["technical"]["summary"] = EVIL
    run["analysts"]["news"]["points"] = [{"text": EVIL, "refs": ["N1", '"><script>x</script>']}]
    run["debate"]["bull"]["thesis"] = EVIL
    run["risk_committee"]["aggressive"]["concerns"] = [EVIL]
    run["meta"]["degraded"] = [EVIL]
    run["meta"]["models"]["fast"] = EVIL
    run["evidence"]["refs"] += [{"id": "X1", "source": "evil js", "url": "javascript:alert(1)", "ts": None, "fetched_at": None},
                                {"id": "X2", "source": "evil data", "url": "data:text/html,<script>1</script>", "ts": None, "fetched_at": None},
                                {"id": "X3", "source": "evil spaced", "url": " JaVaScRiPt:alert(1)", "ts": None, "fetched_at": None},
                                {"id": "X4", "source": EVIL, "url": 'https://ok.example/"onmouseover="alert(1)', "ts": None, "fetched_at": None}]
    run["evidence"]["sections"]["news"]["items"] = [{"title": EVIL, "url": "javascript:alert(2)", "source": "x", "published": None}]
    doc = report.render(run, {"1h": {"ret_pct": 1.0, "direction_ok": None, "source": EVIL}}, None)
    t = parse(doc)
    assert "script" not in t.tags and "img" not in t.tags and not t.handlers
    assert "&lt;script&gt;" in doc
    hrefs = [a.get("href") or "" for a in t.links]
    assert not any("javascript" in h.lower() or h.startswith("data:") for h in hrefs)
    assert "evil js" in doc and "evil data" in doc  # the source is still listed, just not linked
    assert not any("ok.example" in h for h in hrefs)  # a URL carrying quotes / markup is not linked at all


def test_safe_url() -> None:
    assert report.safe_url("https://a.b/c?d=1") == "https://a.b/c?d=1"
    for bad in ("javascript:alert(1)", "JAVASCRIPT:x", "data:x", "vbscript:x", "//evil.com", "/relative", "ftp://x", None, 5, "https://", 'https://a.b/"x', "https://a.b/<x>"):
        assert report.safe_url(bad) is None, bad


def test_filename() -> None:
    run = load("doge")
    assert report.filename(run) == "kiwi-committee-DOGE-4h-20261023-0336.html"
    assert report.filename({"asset": {"symbol": '../"x\r\n'}, "timeframe": "1h", "as_of": "2026-01-02T03:04:00Z"}) == "kiwi-committee-X-1h-20260102-0304.html"


def test_public_run_strips_identities() -> None:
    d = {"a": 1, "requested_by": {"tg_user_id": 5}, "x": [{"chat_id": 1, "member_id": 2, "keep": 3}], "message_id": 9}
    assert report.public_run(d) == {"a": 1, "x": [{"keep": 3}]}


def test_report_endpoint_attachment_outcomes_and_no_member_data(client: TestClient, db: Session) -> None:
    run = load("btc")
    run["requested_by"] = {"tg_user_id": 4242, "username": "secret_member"}  # a stray identity must never come out
    row = store(db, run, pid="cm_reportbtc0001", tg=4242, member=None)
    db.add(CmOutcome(run_id=row.id, horizon="4h", price_from=81000.0, price_to=81810.0, ret_pct=1.0, direction_ok=None, mfe_pct=1.4,
                     mae_pct=-0.3, resolved_at=utcnow(), source="hyperliquid 5m candles"))
    db.add(CmOutcome(run_id=row.id, horizon="24h", price_from=81000.0, price_to=79380.0, ret_pct=-2.0, direction_ok=None, mfe_pct=0.5,
                     mae_pct=-2.5, resolved_at=utcnow(), source="hyperliquid 15m candles"))
    db.commit()

    r = client.get("/api/committee/runs/cm_reportbtc0001/report.html")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["content-disposition"] == 'attachment; filename="kiwi-committee-BTC-1h-20261023-0336.html"'
    assert "default-src 'none'" in r.headers["content-security-policy"]
    doc = r.text
    assert "hyperliquid 5m candles" in doc and "+1.00%" in doc and "-2.00%" in doc and "no position" in doc
    assert doc.index(">4h<") < doc.index(">24h<")
    for secret in ("4242", "secret_member", "-100555", "requested_by"):
        assert secret not in doc

    full = client.get("/api/committee/runs/cm_reportbtc0001")
    assert full.status_code == 200
    lst = client.get("/api/committee/runs").json()
    track = client.get("/api/committee/track").json()
    for body in (full.text, json.dumps(lst), json.dumps(track)):
        for secret in ("4242", "secret_member", "-100555", "requested_by", "chat_id", "message_id"):
            assert secret not in body, secret
    assert full.json()["outcomes"]["4h"]["source"] == "hyperliquid 5m candles"
    assert lst["runs"][0]["id"] == "cm_reportbtc0001" and lst["runs"][0]["outcomes"]["24h"]["ret_pct"] == -2.0


def test_report_needs_a_finished_run(client: TestClient, db: Session) -> None:
    db.add(CmRun(public_id="cm_queued000001", symbol="ETH", timeframe="1h", mode="live", depth="full", status="queued", created_at=utcnow(),
                 cost_usd=0, calls=0))
    db.commit()
    assert client.get("/api/committee/runs/cm_queued000001/report.html").status_code == 409
    assert client.get("/api/committee/runs/cm_nope/report.html").status_code == 404


def test_track_shows_sample_sizes_and_keeps_wait_apart(client: TestClient, db: Session) -> None:
    long_run, wait_run = load("btc"), load("doge")
    long_run["verdict"] = {**long_run["verdict"], "action": "LONG", "rating": "Buy"}
    a = store(db, long_run, pid="cm_tracklong0001")
    a.verdict = "LONG"
    b = store(db, wait_run, pid="cm_trackwait0001")
    for row, ret, ok in ((a, 1.5, True), (b, -0.8, None)):
        db.add(CmOutcome(run_id=row.id, horizon="1h", price_from=1.0, price_to=1.0, ret_pct=ret, direction_ok=ok, mfe_pct=0, mae_pct=0,
                         resolved_at=utcnow(), source="hyperliquid 5m candles"))
    db.commit()
    t = client.get("/api/committee/track").json()
    assert t["by_verdict"]["LONG"]["1h"] == {"n": 1, "hits": 1, "hit_rate": 1.0, "avg_ret_pct": 1.5}
    assert t["by_verdict"]["WAIT"]["1h"] == {"n": 1, "hits": None, "hit_rate": None, "avg_ret_pct": -0.8}
    assert t["horizons"] == ["1h", "4h", "24h", "7d"] and t["scored_runs"] == 2
    assert "no position" in t["note"]
    assert {r["id"] for r in t["recent"]} >= {"cm_tracklong0001", "cm_trackwait0001"}
