"""First-deploy restore: only into an empty volume, checksum-verified, never over an existing database."""

import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db import bootstrap


def _bundle(tmp: Path, db_bytes: bytes, corrupt: bool = False) -> Path:
    b = tmp / "prod-bootstrap"
    b.mkdir()
    (b / "price_chat.db.gz").write_bytes(gzip.compress(db_bytes))
    (b / "secrets.json.gz").write_bytes(gzip.compress(b'{"x": {"value": "enc", "saved_at": "2026-10-08"}}'))
    sums = {n: hashlib.sha256((b / n).read_bytes()).hexdigest() for n in ("price_chat.db.gz", "secrets.json.gz")}
    if corrupt:
        sums["price_chat.db.gz"] = "0" * 64
    (b / "manifest.json").write_text(json.dumps({"sha256": sums}), encoding="utf-8")
    return b


def test_restores_only_into_an_empty_volume(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(bootstrap, "BUNDLE", _bundle(tmp_path, b"SNAPSHOT"))
    vol = tmp_path / "data"
    s = SimpleNamespace(is_production=True, database_url=f"sqlite:///{(vol / 'price_chat.db').as_posix()}", secrets_file=vol / "secrets.json")
    out = bootstrap.restore_if_empty(s)
    assert (vol / "price_chat.db").read_bytes() == b"SNAPSHOT" and (vol / "secrets.json").exists() and out[0].startswith("restored")
    (vol / "price_chat.db").write_bytes(b"LIVE DATA")  # the app has been running: a later start must not touch it
    out = bootstrap.restore_if_empty(s)
    assert (vol / "price_chat.db").read_bytes() == b"LIVE DATA" and out[0] == "kept existing price_chat.db"


def test_refuses_a_corrupt_bundle(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(bootstrap, "BUNDLE", _bundle(tmp_path, b"SNAPSHOT", corrupt=True))
    vol = tmp_path / "data"
    s = SimpleNamespace(is_production=True, database_url=f"sqlite:///{(vol / 'price_chat.db').as_posix()}", secrets_file=vol / "secrets.json")
    with pytest.raises(RuntimeError, match="checksum"):
        bootstrap.restore_if_empty(s)
    assert not (vol / "price_chat.db").exists()


def test_no_bundle_no_change(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(bootstrap, "BUNDLE", tmp_path / "missing")
    assert bootstrap.restore_if_empty(SimpleNamespace(is_production=True, database_url="sqlite:///x.db", secrets_file=Path("s.json"))) == []


def test_never_in_development(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(bootstrap, "BUNDLE", _bundle(tmp_path, b"SNAPSHOT"))
    vol = tmp_path / "data"
    s = SimpleNamespace(is_production=False, database_url=f"sqlite:///{(vol / 'price_chat.db').as_posix()}", secrets_file=vol / "secrets.json")
    assert bootstrap.restore_if_empty(s) == [] and not (vol / "price_chat.db").exists()
