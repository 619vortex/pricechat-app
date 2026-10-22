"""QA fixes (2026-10-20): edge caching of public answers, sitemaps + link previews, and the nightly database backup."""

import gzip
import io
import sqlite3
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pokemon import PokemonCard
from app.pokemon import catalog


@pytest.fixture
def owner_login(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr

    from app.security.admin_auth import hash_password

    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime.settings, "admin_username", "admin")
    monkeypatch.setattr(runtime.settings, "admin_password_hash", SecretStr(hash_password("pw")))


def test_only_viewer_independent_answers_are_kept_at_the_edge(client: TestClient, db: Session, owner_login: None) -> None:
    catalog.ensure_catalog(db)
    home = client.get("/api/website/home")
    assert home.status_code == 200 and "s-maxage=60" in home.headers["cache-control"] and "max-age=0" in home.headers["cache-control"]
    assert "s-maxage" in client.get("/api/members/ranks").headers["cache-control"]
    # a leaderboard includes the signed-in member's own place: never shared
    assert "s-maxage" not in client.get("/api/members/leaderboard").headers.get("cache-control", "")
    # the public website's market reads (?pub=1) may be cached; the same address without the mark (the dashboard) may not
    assert "s-maxage=60" in client.get("/api/pokemon/market?era=all&limit=5&pub=1").headers["cache-control"]
    assert client.get("/api/pokemon/market?era=all&limit=5").headers["cache-control"] == "public, max-age=60"
    client.post("/api/admin/login", json={"username": "admin", "password": "pw"})
    owner = client.get("/api/pokemon/market?era=all&limit=5&pub=1")
    assert owner.status_code == 200 and "s-maxage" not in owner.headers.get("cache-control", "")  # the owner's view is never cached


def test_sitemaps_list_pages_cards_and_only_public_profiles(client: TestClient, db: Session) -> None:
    from app.members import service as members

    catalog.ensure_catalog(db)
    shown, hidden = members.create_member(db), members.create_member(db)
    shown.username, shown.username_key, shown.public_profile = "kiwi_fan", "kiwi_fan", True
    hidden.username, hidden.username_key, hidden.public_profile = "quiet_one", "quiet_one", False
    db.commit()
    index = client.get("/api/website/seo/sitemap.xml")
    assert index.status_code == 200 and index.headers["content-type"].startswith("application/xml")
    assert "/sitemaps/pages-1.xml" in index.text and "/sitemaps/cards-1.xml" in index.text
    assert "s-maxage=3600" in index.headers["cache-control"]
    pages = client.get("/api/website/seo/sitemaps/pages.xml").text
    assert "/pokemon</loc>" in pages and "/earn</loc>" in pages and "passport" not in pages
    card = db.scalar(select(PokemonCard).order_by(PokemonCard.id))
    assert f"/pokemon/cards/{card.id}</loc>" in client.get("/api/website/seo/sitemaps/cards-1.xml").text
    profiles = client.get("/api/website/seo/sitemaps/profiles.xml").text
    assert "/u/kiwi_fan" in profiles and "quiet_one" not in profiles
    assert client.get("/api/website/seo/sitemaps/cards-99.xml").status_code == 404
    assert client.get("/api/website/seo/sitemaps/secrets.xml").status_code == 404


def test_link_previews_show_what_the_page_shows(client: TestClient, db: Session) -> None:
    from app.members import service as members

    catalog.ensure_catalog(db)
    card = db.scalar(select(PokemonCard).where(PokemonCard.name == "Charizard", PokemonCard.edition == "1st Edition"))
    html = client.get(f"/api/website/seo/og/pokemon/cards/{card.id}").text
    assert "<title>Charizard 1st Edition · 1999 Base Set" in html and 'property="og:image"' in html and f"/pokemon/cards/{card.id}" in html
    assert "1st Edition" in html
    private = members.create_member(db)
    private.username, private.username_key, private.public_profile = "secret_kiwi", "secret_kiwi", False
    db.commit()
    p = client.get("/api/website/seo/og/u/secret_kiwi").text
    assert "secret_kiwi" not in p and "Everything has a price" in p and 'content="noindex"' in p  # a private profile gets the site preview
    private.public_profile = True
    db.commit()
    assert "secret_kiwi · Kiwi Passport · Price Chat" in client.get("/api/website/seo/og/u/secret_kiwi").text
    earn = client.get("/api/website/seo/og/earn").text
    assert "Kiwi Trading Bot" in earn and "summary_large_image" in earn
    assert "<script" not in client.get("/api/website/seo/og/u/%3Cscript%3E").text  # names are escaped


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {f"backups/db/price_chat-202601{d:02d}-030000.db.gz": b"old" for d in range(1, 16)}

    def upload_fileobj(self, f: Any, bucket: str, key: str, ExtraArgs: dict[str, str]) -> None:  # noqa: N803 - boto3's name
        buf = io.BytesIO()
        while chunk := f.read(1 << 16):
            buf.write(chunk)
        self.objects[key] = buf.getvalue()

    def head_object(self, Bucket: str, Key: str) -> dict[str, int]:  # noqa: N803
        return {"ContentLength": len(self.objects[Key])}

    def list_objects_v2(self, Bucket: str, Prefix: str) -> dict[str, Any]:  # noqa: N803
        return {"Contents": [{"Key": k} for k in self.objects if k.startswith(Prefix)]}

    def delete_object(self, Bucket: str, Key: str) -> None:  # noqa: N803
        del self.objects[Key]


def test_nightly_backup_is_a_sound_compressed_copy_and_keeps_14(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from app.config.settings import Settings
    from app.services import db_backup

    path = tmp_path / "live.db"
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE members (id INTEGER PRIMARY KEY, name TEXT)")
    con.executemany("INSERT INTO members (name) VALUES (?)", [(f"kiwi {i}",) for i in range(5000)])
    con.commit()
    s3 = FakeS3()
    monkeypatch.setattr(db_backup, "_client", lambda settings: s3)
    out = db_backup.run(Settings(portfolio_bucket="bucket"), str(path), datetime(2026, 10, 20, 3, 0, tzinfo=timezone.utc))
    con.close()
    assert out["key"] == "backups/db/price_chat-20261020-030000.db.gz" and out["kept"] == 14 and out["removed"] == 2
    assert len(s3.objects) == 14 and "backups/db/price_chat-20260101-030000.db.gz" not in s3.objects
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(s3.objects[out["key"]]))
    check = sqlite3.connect(restored)
    assert check.execute("SELECT count(*) FROM members").fetchone()[0] == 5000
    check.close()
    assert out["compressed_mb"] < out["database_mb"] or out["database_mb"] < 1


def test_backup_uses_the_real_bucket_client() -> None:
    from app.config.settings import Settings
    from app.services import db_backup

    s3 = db_backup._client(Settings(portfolio_bucket="bucket", portfolio_s3_endpoint="https://example.invalid", portfolio_s3_region="auto"))
    assert all(hasattr(s3, m) for m in ("upload_fileobj", "head_object", "list_objects_v2", "delete_object"))
