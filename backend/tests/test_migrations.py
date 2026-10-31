import json
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from app.db.base import Base
from app.db.migrate import alembic_config, upgrade_database
from app.db.session import build_engine


def test_migrations_match_models(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    upgrade_database(url)
    engine = build_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    engine.dispose()
    assert diff == []


def test_phase1_database_upgrades_and_keeps_data(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'phase1.db').as_posix()}"
    engine = build_engine(url, enforce_foreign_keys=False)
    with engine.begin() as conn:
        config = alembic_config()
        config.attributes["connection"] = conn
        command.upgrade(config, "0001")
        conn.execute(text("DROP TABLE alembic_version"))  # Phase 1 used create_all(): no version table
        now = "2026-01-01 00:00:00"
        conn.execute(
            text(
                "INSERT INTO agent_configs (id, slug, name, identity, brand_profile, goals, status, created_at, updated_at) "
                "VALUES (1, 'price-chat', 'Price Chat', 'id', :brand, :goals, 'active', :now, :now)"
            ),
            {
                "brand": json.dumps({"brand_name": "Price Chat", "voice": "Calm", "topics": "a, b", "avoid": "c"}),
                "goals": json.dumps(["Goal A", "Goal B"]),
                "now": now,
            },
        )
        conn.execute(
            text("INSERT INTO memories (agent_id, kind, content, source, importance, extra, created_at, updated_at) "
                 "VALUES (1, 'insight', 'members like AMAs', 'dashboard', 0.5, '{}', :now, :now)"),
            {"now": now},
        )
    engine.dispose()

    upgrade_database(url)

    check = create_engine(url)
    with check.connect() as conn:
        head = ScriptDirectory.from_config(alembic_config()).get_current_head()
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == head
        brand = conn.execute(text("SELECT brand_name, tone, topics_of_interest, topics_to_avoid FROM brand_profiles")).one()
        assert brand[0] == "Price Chat" and brand[1] == "Calm"
        assert json.loads(brand[2]) == ["a", "b"] and json.loads(brand[3]) == ["c"]
        goals = conn.execute(text("SELECT name FROM agent_goals ORDER BY id")).scalars().all()
        assert goals == ["Goal A", "Goal B"]
        memory = conn.execute(text("SELECT kind, layer, source_type, tags FROM memories")).one()
        assert tuple(memory) == ("community_insight", "community", "manual", "[]")
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
    check.dispose()


def test_community_rename_moves_saved_settings_to_thepricechat(tmp_path: Path) -> None:
    """0071: every saved @cryptopricechat (the community group's old name) becomes @thepricechat; other values stay."""
    from sqlalchemy import inspect

    url = f"sqlite:///{(tmp_path / 'rename.db').as_posix()}"
    engine = build_engine(url, enforce_foreign_keys=False)

    def insert(conn, table: str, values: dict) -> None:  # type: ignore[no-untyped-def]
        row = dict(values)
        for c in inspect(conn).get_columns(table):  # fill the other required columns with something valid
            if c["name"] in row or c["nullable"] or c.get("default") is not None or c.get("autoincrement") is True:
                continue
            t = str(c["type"]).upper()
            row[c["name"]] = 0 if any(k in t for k in ("INT", "BOOL", "FLOAT", "NUMERIC")) else "{}" if "JSON" in t else \
                "2026-01-01 00:00:00" if "DATE" in t or "TIME" in t else "x"
        cols = ", ".join(row)
        conn.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({', '.join(':' + k for k in row)})"), row)

    with engine.begin() as conn:
        config = alembic_config()
        config.attributes["connection"] = conn
        command.upgrade(config, "0070")
        insert(conn, "ts_settings", {"id": 1, "main_chat": "@CryptoPriceChat"})
        insert(conn, "wk_settings", {"id": 1, "community_chat": "@cryptopricechat"})
        insert(conn, "member_program_settings", {"id": 1, "crew_chat": "@someothergroup"})
        insert(conn, "telegram_sources", {"id": 1, "chat_id": -100, "username": "Cryptopricechat"})
        insert(conn, "website_settings", {"id": 1, "links": json.dumps({"telegram": "https://t.me/cryptopricechat", "x": "https://x.com/pricechat"})})
        command.upgrade(config, "0071")
        assert conn.execute(text("SELECT main_chat FROM ts_settings")).scalar() == "@thepricechat"
        assert conn.execute(text("SELECT community_chat FROM wk_settings")).scalar() == "@thepricechat"
        assert conn.execute(text("SELECT crew_chat FROM member_program_settings")).scalar() == "@someothergroup"  # owner's own choice kept
        assert conn.execute(text("SELECT username FROM telegram_sources")).scalar() == "thepricechat"
        links = conn.execute(text("SELECT links FROM website_settings")).scalar()
        assert json.loads(links) == {"telegram": "https://t.me/thepricechat", "x": "https://x.com/pricechat"}
        insert(conn, "wk_settings", {"id": 2})
        assert conn.execute(text("SELECT community_chat FROM wk_settings WHERE id = 2")).scalar() == "@thepricechat"  # new default
    engine.dispose()


def test_perf_indexes_upgrade_and_downgrade(tmp_path: Path) -> None:
    """0090: every hot-query index is created (and the models declare the same, see test_migrations_match_models); a
    downgrade removes them and restores the narrower ex_orders / perp_orders account index."""
    from sqlalchemy import inspect

    from app.models.perf_indexes import PERF_INDEXES

    url = f"sqlite:///{(tmp_path / 'perf.db').as_posix()}"
    upgrade_database(url)
    engine = build_engine(url, enforce_foreign_keys=False)

    def indexes(conn, table: str) -> dict[str, list[str]]:  # type: ignore[no-untyped-def]
        return {i["name"]: i["column_names"] for i in inspect(conn).get_indexes(table)}

    with engine.begin() as conn:
        for ix in PERF_INDEXES:
            assert indexes(conn, ix.table.name)[ix.name] == [c.name for c in ix.columns]
        assert indexes(conn, "ex_orders")["ix_ex_orders_account"] == ["account_id", "status", "updated_at"]
        config = alembic_config()
        config.attributes["connection"] = conn
        command.downgrade(config, "0089_workers_checks")
        assert "ix_telegram_messages_source_sent" not in indexes(conn, "telegram_messages")
        assert indexes(conn, "perp_orders")["ix_perp_orders_account"] == ["account_id", "status"]
        command.upgrade(config, "head")
        assert "ix_telegram_messages_source_sent" in indexes(conn, "telegram_messages")
    engine.dispose()


def test_no_migration_switches_auto_trading_on() -> None:
    """AUTO trading is the member's own choice: a deploy (its migrations) must never turn it on for an existing account."""
    import re

    versions = Path(alembic_config().get_main_option("script_location") or "migrations") / "versions"
    files = sorted(versions.glob("*.py")) or sorted((Path(__file__).parents[1] / "migrations" / "versions").glob("*.py"))
    assert files
    for f in files:
        src = f.read_text(encoding="utf-8")
        assert not re.search(r"auto_enabled\s*=\s*(1|true)", src, re.I), f"{f.name} sets auto_enabled on"


def test_upgrading_keeps_every_accounts_auto_setting(tmp_path: Path) -> None:
    """Accounts as they are in production before this deploy (AUTO off, AUTO on with an end date, paused): upgrading to head
    leaves AUTO, its end date and STOP NEW TRADES exactly as they were."""
    url = f"sqlite:///{(tmp_path / 'auto.db').as_posix()}"
    engine = build_engine(url, enforce_foreign_keys=False)
    with engine.begin() as conn:
        config = alembic_config()
        config.attributes["connection"] = conn
        command.upgrade(config, "0094_kalshi_links")
        now = "2026-10-30 12:00:00"
        rows = [  # id, mode, live_enabled_at, agent approved, auto_enabled, auto_until, paused
            (1, "LIVE", now, True, 0, None, 0),  # connected LIVE, AUTO off
            (2, "LIVE", now, True, 0, None, 1),  # connected LIVE, paused
            (3, "LIVE", now, True, 1, "2026-11-01 00:00:00", 0),  # AUTO on until a date
            (4, "PAPER", None, False, 0, None, 0),
        ]
        for i, mode, live_at, approved, auto, until, paused in rows:
            conn.execute(text(
                "INSERT INTO tr_accounts (id, member_id, mode, master_address, mainnet_agent, mainnet_agent_key, mainnet_approved_at, "
                "live_checks, live_enabled_at, auto_enabled, auto_until, paused, created_at, updated_at) "
                "VALUES (:i, :i, :mode, '0xabc', :agent, :key, :appr, '{}', :live_at, :auto, :until, :paused, :now, :now)"),
                {"i": i, "mode": mode, "agent": "0xdef" if approved else None, "key": "sealed" if approved else None,
                 "appr": now if approved else None, "live_at": live_at, "auto": auto, "until": until, "paused": paused, "now": now})
    engine.dispose()

    upgrade_database(url)

    check = create_engine(url)
    with check.connect() as conn:
        got = conn.execute(text("SELECT id, auto_enabled, auto_until, paused FROM tr_accounts ORDER BY id")).all()
    check.dispose()
    assert [(r[0], bool(r[1]), r[2] is not None, bool(r[3])) for r in got] == [
        (1, False, False, False), (2, False, False, True), (3, True, True, False), (4, False, False, False)]
