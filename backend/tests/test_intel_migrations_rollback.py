"""Intelligence Engine migrations (0121+): upgrade from production's revision, roll back, and upgrade again without touching the data
production already has. The rollback target is 0120_trending_text_ok, the revision production runs before this branch."""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from app.db.base import Base
from app.db.migrate import alembic_config
from app.db.session import build_engine

PROD = "0120_trending_text_ok"
INTEL_TABLES = {"intel_workers", "intel_jobs", "ai_usage", "intel_shadow"}


def _run(url: str, fn: str, rev: str) -> None:
    engine = build_engine(url, enforce_foreign_keys=False)
    with engine.begin() as conn:
        config = alembic_config()
        config.attributes["connection"] = conn
        getattr(command, fn)(config, rev)
    engine.dispose()


def _tables(url: str) -> set[str]:
    engine = build_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_intel_migrations_upgrade_rollback_upgrade_keep_existing_data(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'prod_shape.db').as_posix()}"
    _run(url, "upgrade", PROD)
    before = _tables(url)
    assert not (before & INTEL_TABLES)

    engine = build_engine(url, enforce_foreign_keys=False)
    with engine.begin() as conn:  # a row production already has, in a table this branch's code writes to (Trending)
        cols = {c["name"]: c for c in inspect(conn).get_columns("tt_topics")}
        row = {"id": 1}
        for name, c in cols.items():
            if name in row or c["nullable"] or c.get("default") is not None or c.get("server_default") is not None:
                continue
            t = str(c["type"]).upper()
            row[name] = 0 if any(k in t for k in ("INT", "FLOAT", "NUMERIC", "REAL", "BOOL")) else (
                "2026-11-25 00:00:00" if "DATE" in t or "TIME" in t else ("{}" if "JSON" in t else "x"))
        conn.execute(text(f"INSERT INTO tt_topics ({', '.join(row)}) VALUES ({', '.join(':' + k for k in row)})"), row)
    engine.dispose()

    _run(url, "upgrade", "head")
    assert INTEL_TABLES <= _tables(url)
    engine = build_engine(url, enforce_foreign_keys=False)
    with engine.begin() as conn:  # rows in the new tables must not block the rollback
        conn.execute(text("INSERT INTO ai_usage (day, at, model, purpose, status, tokens_in, tokens_out, cache_read, cost_usd) "
                          "VALUES ('2026-11-25', '2026-11-25 00:00:00', 'claude-haiku-5-5', 'test', 'settled', 1, 1, 0, 0.0)"))
    engine.dispose()

    _run(url, "downgrade", PROD)
    after = _tables(url)
    assert after == before, f"rollback left or removed tables: {sorted(after ^ before)}"
    engine = build_engine(url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == PROD
        assert conn.execute(text("SELECT count(*) FROM tt_topics")).scalar() == 1
    engine.dispose()

    _run(url, "upgrade", "head")
    engine = build_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
        assert conn.execute(text("SELECT count(*) FROM tt_topics")).scalar() == 1
    engine.dispose()
    assert diff == []
