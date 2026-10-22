"""Cross-cutting HTTP hardening: request-body limits on public routes, image decompression bombs, DB connections held by
streaming responses, sign-in rate limits, and inputs that used to turn into 500s (inf prices, odd digits, huge ids)."""

import io
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.security import body_limit, rate_limit
from tests.test_production import prod  # noqa: F401 - the production app (owner login required)


@pytest.fixture(autouse=True)
def _limits() -> Iterator[None]:
    rate_limit.reset_for_tests()
    yield
    rate_limit.reset_for_tests()


# --- request-body limits ------------------------------------------------------------------------------------------------


def test_body_limit_rules() -> None:
    mb = body_limit.MB
    assert body_limit.limit_for("POST", "/api/members/auth/email/start", 120) == body_limit.DEFAULT
    assert body_limit.limit_for("POST", "/api/members/portfolio/scan", 120) == body_limit.UPLOAD
    assert body_limit.limit_for("POST", "/api/members/portfolio/holdings/7/photos", 120) == body_limit.UPLOAD
    assert body_limit.limit_for("POST", "/api/members/market/listings/7/photos", 120) == body_limit.UPLOAD
    assert body_limit.limit_for("POST", "/api/brand-manager/worker/jobs/3/result", 120) == 120 * mb + body_limit.UPLOAD
    assert body_limit.limit_for("POST", "/api/short-market/worker/jobs/3/result", 50) == 50 * mb + body_limit.UPLOAD
    assert body_limit.limit_for("POST", "/api/brand-manager/worker/claim", 120) == body_limit.WORKER
    assert body_limit.limit_for("POST", "/api/admin/login", 120) == body_limit.DEFAULT
    assert body_limit.limit_for("GET", "/api/members/me", 120) is None  # no body
    assert body_limit.limit_for("POST", "/api/knowledge/import", 120) is None  # the owner's routes: behind the login


def test_public_json_body_over_the_limit_is_refused_before_it_is_read(prod: TestClient) -> None:  # noqa: F811
    big = b'{"email": "a@b.co", "pad": "' + b"x" * (body_limit.DEFAULT + 10) + b'"}'
    r = prod.post("/api/members/auth/email/start", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["code"] == "too_large"
    r = prod.post("/api/admin/login", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413
    # a normal body still works (the login answers 401 for wrong credentials, not 413)
    assert prod.post("/api/admin/login", json={"username": "owner", "password": "nope"}).status_code == 401


def test_chunked_body_is_counted_as_it_arrives(prod: TestClient) -> None:  # noqa: F811
    def chunks() -> Iterator[bytes]:
        yield b'{"email": "a@b.co", "pad": "'
        for _ in range(20):
            yield b"x" * (128 * 1024)
        yield b'"}'

    r = prod.post("/api/members/auth/email/start", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_anonymous_upload_over_the_limit_is_refused(prod: TestClient) -> None:  # noqa: F811
    files = {"file": ("x.jpg", b"\xff" * (body_limit.UPLOAD + 1), "image/jpeg")}
    assert prod.post("/api/members/portfolio/scan", files=files).status_code == 413
    small = {"file": ("x.jpg", b"\xff" * 1000, "image/jpeg")}
    assert prod.post("/api/members/portfolio/scan", files=small).status_code == 401  # under it: the sign-in check answers


def test_owner_routes_are_not_limited_here(prod: TestClient) -> None:  # noqa: F811
    big = b'{"pad": "' + b"x" * (body_limit.DEFAULT + 10) + b'"}'
    # refused for the missing login (401), not for the size: the body is never read without the login
    assert prod.post("/api/knowledge/import", content=big, headers={"content-type": "application/json"}).status_code == 401


# --- image decompression bombs ------------------------------------------------------------------------------------------


def test_highly_compressed_huge_png_is_refused_before_decoding() -> None:
    from PIL import Image

    from app.portfolio import photos

    buf = io.BytesIO()
    Image.new("1", (8000, 7000)).save(buf, "PNG")  # 56 MP in a few KB: between MAX_PIXELS and PIL's own 2x refusal
    assert len(buf.getvalue()) < photos.MAX_UPLOAD
    with pytest.raises(photos.PhotoError):
        photos.process(buf.getvalue())
    ok = io.BytesIO()
    Image.new("RGB", (900, 1200), "white").save(ok, "PNG")
    assert photos.process(ok.getvalue())[2:4] == (900, 1200)


# --- streaming responses don't hold the request's DB connection ---------------------------------------------------------


def test_kitchen_stream_releases_the_request_connection(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db.session import engine
    from app.tradingos import kitchen
    from tests.test_burner import signed_in

    signed_in(client, db)
    db.close()
    seen: list[int] = []

    def events_after(s: Session, after_id: int) -> list[dict[str, object]]:
        s.execute(text("SELECT 1"))
        seen.append(engine.pool.checkedout())  # this read's own session + anything else still checked out
        return []

    monkeypatch.setattr(kitchen, "events_after", events_after)
    r = client.get("/api/members/trading-bot/kitchen/stream?after=0&once=true", headers={"last-event-id": "\u00b2".encode("latin-1")})  # "²": not a 500
    assert r.status_code == 200 and seen and seen[0] == 1  # only the read's own session: the request's one was released
