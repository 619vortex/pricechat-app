"""Admin › X › Originals API (/api/originals) and the drafts service: owner auth, list / get / approve / edit / regenerate / reject,
generate-now, the writer's results applied only when they pass the gates, the image endpoint answering 503 until the renderer branch is
merged, and NOTHING ever reaching X or Telegram (every outbound network transport is made to fail during these tests). SYNTHETIC data."""

from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.growth import drafts, writer
from app.growth.detectors import extremes
from app.growth.findings import Finding
from app.growth.memory import Memory
from app.models.intel import IntelJob
from app.models.market_memory import GrDraft
from app.security import admin_auth
from tests.test_growth_detectors import AS_OF, crash_tail, walk

P = "/api/originals"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any real outbound HTTP (X, Telegram, anything) fails the test. TestClient uses its own in-process transport, so it still works."""
    attempts: list[str] = []

    def boom(self: Any, request: httpx.Request) -> Any:
        attempts.append(str(request.url))
        raise AssertionError(f"outbound network call during an Originals test: {request.url}")

    async def aboom(self: Any, request: httpx.Request) -> Any:
        return boom(self, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", boom)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", aboom)
    return attempts


@pytest.fixture
def mem() -> Memory:
    return Memory.from_points({"px:BTC:close": crash_tail(walk(1000), 30, -0.40), "px:ETH:close": crash_tail(walk(1000, seed=4), 30, -0.45)})


@pytest.fixture
def finding(mem: Memory) -> Finding:
    return extremes.rarity_move(mem, AS_OF, ("BTC",))[0]


@pytest.fixture
def draft(db: Session, finding: Finding) -> GrDraft:
    d = drafts.create(db, finding, use_writer=False)
    assert d is not None
    return d


def test_needs_the_owner_login(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert not admin_auth.is_public("GET", f"{P}/drafts") and not admin_auth.is_public("POST", f"{P}/drafts/1/approve")
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime.settings, "admin_username", "admin")
    monkeypatch.setattr(runtime.settings, "admin_password_hash", SecretStr(admin_auth.hash_password("pw")))
    for method, path in (("GET", f"{P}/drafts"), ("POST", f"{P}/drafts/1/approve"), ("POST", f"{P}/generate"), ("GET", f"{P}/drafts/1/image.png")):
        assert client.request(method, path).status_code == 401


def test_list_get_and_view(client: TestClient, draft: GrDraft) -> None:
    r = client.get(f"{P}/drafts")
    assert r.status_code == 200
    b = r.json()
    assert b["counts"]["pending"] == 1 and b["switches"]["growth_enabled"] is False and "nothing is posted" in b["publishing"]
    d = b["drafts"][0]
    assert d["id"] == draft.id and d["status"] == "pending" and d["chars"] == len(d["text"]) <= 280
    assert d["gate_problems"] == [] and d["calculation"] and d["chart"]["kind"] == "histogram_marker"
    assert d["sources"][0]["name"] == "hyperliquid" and d["sources"][0]["commercial_ok"] is True
    assert client.get(f"{P}/drafts", params={"status": "approved"}).json()["drafts"] == []
    assert client.get(f"{P}/drafts", params={"status": "bogus"}).status_code == 422
    assert client.get(f"{P}/drafts/{draft.id}").json()["finding_key"] == draft.finding_key
    assert client.get(f"{P}/drafts/999999").status_code == 404


def test_image_is_503_until_the_renderer_lands(client: TestClient, draft: GrDraft) -> None:
    r = client.get(f"{P}/drafts/{draft.id}/image.png", params={"fmt": "portrait"})
    assert r.status_code == 503 and r.json()["detail"] == "renderer not installed"
    assert client.get(f"{P}/drafts/{draft.id}/image.png", params={"fmt": "square"}).status_code == 422


def test_image_through_the_adapter(client: TestClient, draft: GrDraft, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.growth import graphics

    monkeypatch.setattr(graphics, "graphic_png", lambda chart, fmt: b"\x89PNG fake " + fmt.encode())
    r = client.get(f"{P}/drafts/{draft.id}/image.png", params={"fmt": "x"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content.endswith(b"x")


def test_approve_only_marks_it(client: TestClient, draft: GrDraft, db: Session, no_network: list[str]) -> None:
    r = client.post(f"{P}/drafts/{draft.id}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["decided_by"] == "owner"
    db.refresh(draft)
    assert draft.status == "approved" and draft.decided_at is not None
    assert no_network == []


def test_edit_runs_the_figures_gate(client: TestClient, draft: GrDraft, finding: Finding) -> None:
    bad = client.post(f"{P}/drafts/{draft.id}/edit", json={"text": "Bitcoin −41.0% in 30 days."})
    assert bad.status_code == 422 and "isn't in the finding" in bad.json()["detail"]
    good_text = "Bitcoin: " + finding.numbers["move"]["text"] + " in " + finding.numbers["days"]["text"] + " days. Rare in Hyperliquid data."
    ok = client.post(f"{P}/drafts/{draft.id}/edit", json={"text": good_text})
    assert ok.status_code == 200, ok.text
    b = ok.json()
    assert b["text"] == good_text and b["writer"] == "owner" and b["version"] == 2 and b["original_text"] == draft.original_text


def test_edit_after_approval_needs_a_new_approval(client: TestClient, draft: GrDraft, finding: Finding) -> None:
    client.post(f"{P}/drafts/{draft.id}/approve")
    t = finding.text(1)
    assert client.post(f"{P}/drafts/{draft.id}/edit", json={"text": t}).json()["status"] == "pending"


def test_regenerate_without_ai_uses_the_next_template(client: TestClient, draft: GrDraft, finding: Finding) -> None:
    before = draft.text
    r = client.post(f"{P}/drafts/{draft.id}/regenerate", json={"chart_variant": False})
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "pending" and b["text"] != before and b["text"] == finding.text(1) and b["version"] == 2 and not b["writer_pending"]


def test_regenerate_chart_variant(client: TestClient, db: Session) -> None:
    data = {f"fund:C{k}": [(AS_OF - timedelta(days=i), 0.00001) for i in range(90)] for k in range(12)}
    data["fund:ADA"] = [(AS_OF - timedelta(days=i), 0.00001 if i else -0.0001) for i in range(90)]
    data["fund:XLM"] = [(AS_OF - timedelta(days=i), 0.00001 if i else -0.00008) for i in range(90)]
    from app.growth.detectors import crowded

    f = crowded.funding_extremes(Memory.from_points(data), AS_OF)[0]
    d = drafts.create(db, f, use_writer=False)
    assert d is not None and d.chart["kind"] == "rank_table"
    b = client.post(f"{P}/drafts/{d.id}/regenerate", json={"chart_variant": True}).json()
    assert b["chart"]["kind"] == "heat_grid" and b["chart"]["variant_of"] == "rank_table"


def test_regenerate_with_ai_goes_through_the_router(client: TestClient, draft: GrDraft, db: Session, finding: Finding,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "intel_enabled", True)
    b = client.post(f"{P}/drafts/{draft.id}/regenerate").json()
    assert b["status"] == "regenerating" and b["writer_pending"] is True
    assert client.post(f"{P}/drafts/{draft.id}/approve").status_code == 409
    db.refresh(draft)
    job = db.get(IntelJob, draft.writer_job_id)
    assert job is not None and job.kind == "growth.write" and job.priority == "slot" and "Write it differently" in job.payload["user"]
    # the model answers with placeholders: applied after the gates
    pm = writer.placeholder_map(finding)
    job.status, job.result_by = "done", "pc"
    job.result = {"output": {"text": f"Bitcoin fell {{{pm['move_abs']}}} in {{{pm['days']}}} days, a move only {{{pm['share']}}} of windows in "
                                     f"Hyperliquid data since {{{pm['since']}}} matched."}}
    db.commit()
    assert drafts.apply_writer_results(db)["applied"] == 1
    db.refresh(draft)
    assert draft.status == "pending" and draft.writer == "pc" and "40.0%" in draft.text and draft.writer_job_id is None


def test_writer_output_that_fails_the_gate_is_not_applied(db: Session, draft: GrDraft, finding: Finding) -> None:
    job = IntelJob(kind="growth.write", priority="bulk", route="pc", status="done", payload={}, result_by="pc",
                   result={"output": {"text": "Bitcoin fell 40% and will bounce."}})
    db.add(job)
    db.commit()
    draft.writer_job_id = job.id
    db.commit()
    before = draft.text
    assert drafts.apply_writer_results(db)["refused"] == 1
    db.refresh(draft)
    assert draft.text == before and draft.writer == "template" and draft.finding["writer_refusals"]


def test_owner_edit_is_never_overwritten_by_a_late_writer(db: Session, draft: GrDraft, finding: Finding) -> None:
    drafts.edit(db, draft.id, finding.text(1), "owner")
    pm = writer.placeholder_map(finding)
    job = IntelJob(kind="growth.write", priority="bulk", route="pc", status="done", payload={}, result_by="pc",
                   result={"output": {"text": f"Bitcoin {{{pm['move']}}} in {{{pm['days']}}} days."}})
    db.add(job)
    db.commit()
    draft.writer_job_id = job.id
    db.commit()
    drafts.apply_writer_results(db)
    db.refresh(draft)
    assert draft.text == finding.text(1) and draft.writer == "owner"


def test_reject_keeps_the_reason(client: TestClient, draft: GrDraft) -> None:
    b = client.post(f"{P}/drafts/{draft.id}/reject", json={"reason": "too gloomy"}).json()
    assert b["status"] == "rejected" and b["reject_reason"] == "too gloomy"
    assert client.post(f"{P}/drafts/{draft.id}/edit", json={"text": "x"}).status_code == 409


def test_generate_drafts_dedupes_and_holds_unlicensed(db: Session, mem: Memory, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.growth import detectors

    unlicensed = extremes.rarity_move(mem, AS_OF, ("ETH",))[0]
    unlicensed.sources = ["coinbase"]
    unlicensed.key = "rarity:ETH:cb"
    real_run_all = detectors.run_all

    def run_all(m: Memory, as_of: Any = None, only: Any = None) -> Any:
        found, errors = real_run_all(m, as_of, only)
        return found + [unlicensed], errors

    monkeypatch.setattr(detectors, "run_all", run_all)
    out = drafts.generate(db, mem=mem, now=utcnow())
    assert out["held_unlicensed"] == 1 and out["drafted"] >= 2 and out["errors"] == []
    keys = {d.finding_key for d in db.query(GrDraft)}
    assert "rarity:ETH:cb" not in keys
    for _ in range(3):  # later runs only draft what wasn't drafted yet (one subject per format per run), never a key twice in a week
        again = drafts.generate(db, mem=mem, now=utcnow())
        new = {db.get(GrDraft, i).finding_key for i in again["draft_ids"]}  # type: ignore[union-attr]
        assert not (new & keys)
        keys |= new
    assert again["drafted"] == 0


def test_generate_now_endpoint_with_empty_memory(client: TestClient) -> None:
    r = client.post(f"{P}/generate")
    assert r.status_code == 200 and r.json()["drafted"] == 0 and r.json()["errors"] == ["no Bitcoin closes stored yet"]


def test_select_top_variety() -> None:
    def fake(fmt_: str, subject: str, rarity: float) -> Any:
        class F:
            pass

        f = F()
        f.format, f.subject, f.score = fmt_, subject, rarity * 100  # type: ignore[attr-defined]
        return f

    fs = [fake("extremes", "BTC", 0.9), fake("extremes", "BTC", 0.8), fake("extremes", "ETH", 0.7), fake("breadth", "U", 0.5),
          fake("crowded", "F", 0.4)]
    got = drafts.select_top(fs, per_format=2, max_total=4)  # type: ignore[arg-type]
    assert [(g.format, g.subject) for g in got] == [("extremes", "BTC"), ("breadth", "U"), ("crowded", "F"), ("extremes", "ETH")]


def test_memory_endpoint_lists_the_licence_registry(client: TestClient) -> None:
    b = client.get(f"{P}/memory").json()
    reg = {s["name"]: s for s in b["sources"]}
    assert reg["hyperliquid"]["commercial_ok"] and reg["alternative_me"]["commercial_ok"]
    assert not reg["coinbase"]["commercial_ok"] and not reg["coingecko"]["commercial_ok"] and not reg["yahoo"]["commercial_ok"]
    assert not reg["liquidations"]["commercial_ok"] and b["families"] == []


def test_growth_code_never_imports_a_publisher() -> None:
    """Static guard: the growth package and its route never import an X / Telegram sender."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    files = list((root / "growth").rglob("*.py")) + [root / "api" / "routes" / "originals.py"]
    banned = ("app.social", "app.x", "app.newsdesk.delivery", "app.newsdesk.telegram", "app.telegram", "tweepy", "create_tweet", "sendMessage",
              "sendPhoto", "app.newsdesk.xpost", "app.trending.publish")
    for f in files:
        src = f.read_text(encoding="utf-8")
        for b in banned:
            assert b not in src, f"{f.name} references {b}"
