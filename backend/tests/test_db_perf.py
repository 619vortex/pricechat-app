"""Query-shape regressions for hot paths: same results, bounded number of statements (no N+1, no whole-table work)."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members.catalog import ensure_catalog
from app.members import service as members
from app.models.members import Member
from app.models.pokemon import PokemonCard, PokemonPriceSnapshot
from app.models.portfolio import PokemonHolding, PokemonPortfolio
from app.models.tradingos import TsSignal, TsSignalAction
from app.pokemon import catalog, history
from app.pokemon import service as pokemon


@contextmanager
def statements(db: Session) -> Iterator[list[str]]:
    sql: list[str] = []

    def on(_conn, _cursor, statement, *_a):  # type: ignore[no-untyped-def]
        sql.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", on)
    try:
        yield sql
    finally:
        event.remove(engine, "before_cursor_execute", on)


def _member(db: Session, name: str) -> Member:
    ensure_catalog(db)
    m = members.create_member(db)
    m.username, m.username_key = name, name.lower()
    db.commit()
    return m


def test_explore_reads_ranks_once_for_the_whole_list(db: Session) -> None:
    from app.portfolio import service as portfolios

    catalog.ensure_catalog(db)
    card = db.scalars(select(PokemonCard)).first()
    assert card is not None
    for i in range(4):
        m = _member(db, f"collector{i}")
        p = PokemonPortfolio(member_id=m.id, visibility="public", last_activity_at=utcnow() - timedelta(minutes=i))
        db.add(p)
        db.flush()
        db.add(PokemonHolding(portfolio_id=p.id, card_id=card.id, kind="raw", quantity=i + 1))
    db.commit()
    with statements(db) as sql:
        out = portfolios.explore(db, "cards")
    assert [r["username"] for r in out["rows"]] == ["collector3", "collector2", "collector1", "collector0"]
    assert all(r["rank"]["key"] for r in out["rows"])
    assert sum("FROM ranks" in q for q in sql) == 1


def test_signal_out_reads_participation_once(db: Session) -> None:
    from app.api.routes.tradingos import sig_out

    s = TsSignal(number=1, coin="SOL", side="LONG", timeframe="1h", strategy="t", entry_lo=99, entry_hi=101, stop=95, target=110,
                 lev_lo=3, lev_hi=5, rr=2, status="ACTIVE", expires_at=utcnow() + timedelta(hours=1), published_at=utcnow())
    db.add(s)
    db.flush()
    db.add(TsSignalAction(signal_id=s.id, who="tg:1", action="enter", verified=True))
    db.commit()
    with statements(db) as sql:
        out = sig_out(db, s)
    assert out["participation"] == {"enter": 1, "live": 0, "practice": 0}
    assert sum("FROM ts_signal_actions" in q for q in sql) == 1


def test_ensure_built_checks_for_any_row_without_counting(db: Session) -> None:
    with statements(db) as sql:
        history.ensure_built(db)
    assert sql and not any("count(" in q.lower() for q in sql)


def test_price_snapshot_only_groups_recent_snapshots(db: Session) -> None:
    """An old snapshot never blocks a new one, a recent one does; the newest-per-card read is bounded to the gap window."""
    catalog.ensure_catalog(db)
    c = db.scalar(select(PokemonCard).where(PokemonCard.name == "Venusaur", PokemonCard.set_name == "Base Set",
                                            PokemonCard.edition == "1st Edition"))
    assert c is not None
    pokemon.import_sales(db, [{"card_id": c.id, "grading_company": "PSA", "grade": "10", "price": 100, "source": "ebay",
                               "sale_date": (utcnow() - timedelta(days=2)).isoformat(), "source_listing_id": "a"}], verified=True)
    assert pokemon.snapshot(db, utcnow() - timedelta(days=1)) == 1
    with statements(db) as sql:
        assert pokemon.snapshot(db) == 1  # yesterday's doesn't block today's
    grouped = [q for q in sql if "FROM pokemon_price_snapshots" in q and "GROUP BY" in q]
    assert len(grouped) == 1 and "taken_at >" in grouped[0]
    assert pokemon.snapshot(db, utcnow() + timedelta(minutes=10)) == 0  # 10 minutes later: blocked
    assert db.query(PokemonPriceSnapshot).count() == 2


def test_engine_pool_fits_the_request_threads_and_jobs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.pool import QueuePool

    from app.db.session import POOL_OVERFLOW, POOL_SIZE, build_engine

    e = build_engine(f"sqlite:///{(tmp_path / 'p.db').as_posix()}")
    assert isinstance(e.pool, QueuePool) and e.pool.size() == POOL_SIZE and e.pool._max_overflow == POOL_OVERFLOW
    assert POOL_SIZE + POOL_OVERFLOW >= 40  # anyio's default worker threads for sync routes
    e.dispose()
    small = build_engine(f"sqlite:///{(tmp_path / 'q.db').as_posix()}", pool_size=2)  # callers still decide
    assert small.pool.size() == 2
    small.dispose()


def _verify_member(db: Session, name: str, tg_user: int, src_chat: int) -> Member:
    from app.members import service as member_service
    from app.models.telegram import TelegramMessage, TelegramSource

    m = _member(db, name)
    ident = member_service.link_identity(db, m, "telegram", str(tg_user), name, name)
    ident.verified_at = utcnow() - timedelta(days=5)
    src = TelegramSource(chat_id=src_chat, display_name="Price Chat Community", enabled=True)
    db.add(src)
    db.flush()
    texts = [(f"distinct thought number {i} about the market", timedelta(days=2, seconds=-10 * i)) for i in range(60)]
    texts += [(f"today's thought number {i} about the market", timedelta(minutes=30 - i)) for i in range(10)]
    texts += [(f"a fresh live thought number {i} right now", timedelta(seconds=60 - i)) for i in range(3)]  # live: never skipped
    for n, (text, ago) in enumerate(texts):
        db.add(TelegramMessage(source_id=src.id, chat_id=src_chat, message_id=100 + n, author_id=tg_user, author_name=name,
                               sent_at=utcnow() - ago, text=text))
    db.commit()
    return m


def _ledger_of(db: Session, m: Member) -> tuple[list[tuple[str, str, int]], tuple[int, int, int, int | None, int]]:
    from app.models.members import MemberBadge, MemberNotification, ReputationEvent

    rows = sorted((e.event_type, e.day, e.xp) for e in db.scalars(select(ReputationEvent).where(ReputationEvent.member_id == m.id)))
    db.refresh(m)
    notes = db.scalar(select(func.count()).select_from(MemberNotification).where(MemberNotification.member_id == m.id))
    badges = db.scalar(select(func.count()).select_from(MemberBadge).where(MemberBadge.member_id == m.id))
    return rows, (m.xp_total, m.rank_score, m.legacy_points, m.rank_level, notes * 1000 + badges)


def test_verify_job_awards_in_a_batch_same_results_far_fewer_queries(db: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """members.verify re-reads 14 days of Telegram every run. In a ledger batch each award costs a handful of statements
    (not ~27), and past activity already over a daily cap is skipped rather than refused again on every run, with exactly
    the ledger, totals, rank, notifications and badges of unbatched awards."""
    from contextlib import nullcontext

    from app.members import verified
    from app.members import xp as ledger
    from app.models.members import LinkedIdentity

    ledger.reset_for_tests()

    def run() -> tuple[int, int]:
        with statements(db) as sql:
            a = verified.telegram_participation(db)["awarded"] + verified.telegram_message_xp(db)["awarded"]
        return a, len(sql)

    with monkeypatch.context() as mp:  # the old way: no batch
        mp.setattr(verified.ledger, "batch", lambda _db: nullcontext())
        plain = _verify_member(db, "plainmember", 9001, -100901)
        awarded_plain, sql_plain = run()
        _, rerun_plain = run()
    expected = _ledger_of(db, plain)
    for i in db.scalars(select(LinkedIdentity).where(LinkedIdentity.member_id == plain.id)):
        i.status = "revoked"
    db.commit()

    batched = _verify_member(db, "batchmember", 9002, -100902)
    awarded, sql_first = run()
    _, rerun = run()
    assert awarded == awarded_plain > 70
    assert _ledger_of(db, batched) == expected
    assert sql_first * 3 < sql_plain and sql_first < 8 * awarded  # was ~27 statements per award
    assert rerun * 5 < rerun_plain and rerun <= 30  # capped past activity is not retried every run


def test_file_sqlite_runs_in_wal_with_normal_sync(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Readers never wait for the single writer (WAL), and a commit doesn't fsync while holding the write lock."""
    from sqlalchemy import text

    from app.db.session import build_engine

    e = build_engine(f"sqlite:///{(tmp_path / 'w.db').as_posix()}")
    with e.connect() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert c.execute(text("PRAGMA synchronous")).scalar() == 1  # NORMAL
        assert c.execute(text("PRAGMA busy_timeout")).scalar() == 15000
    e.dispose()


def test_market_lists_value_every_listing_with_one_pricer(db: Session) -> None:
    """/members/market/home and the listing pages value all their listings with one Pricer (prices read once per grade),
    not one Pricer (~3 queries) per listing; each estimate is exactly the single-item one."""
    from app.market import service as market
    from app.models.market import MarketItem, MarketListing

    catalog.ensure_catalog(db)
    cards = db.scalars(select(PokemonCard).order_by(PokemonCard.id).limit(6)).all()
    seller = _member(db, "marketseller")
    pokemon.import_sales(db, [{"card_id": c.id, "grading_company": "PSA", "grade": "10", "price": 100 + 10 * n, "source": "ebay",
                               "sale_date": (utcnow() - timedelta(days=3)).isoformat(), "source_listing_id": f"s{n}"}
                              for n, c in enumerate(cards[:3])], verified=True)
    n = 0
    for c in cards:
        for comp, grade in (("PSA", "10"), ("PSA", "9"), ("BGS", "9.5"), ("RAW", "NM")):
            n += 1
            item = MarketItem(card_id=c.id, grading_company=comp, grade=grade, cert_number=f"{n:08d}", cert_key=f"{comp}:{n}",
                              owner_member_id=seller.id, condition_low=7.0 if comp == "RAW" else None,
                              condition_high=8.5 if comp == "RAW" else None)
            db.add(item)
            db.flush()
            db.add(MarketListing(item_id=item.id, seller_id=seller.id, status="active", price_cents=10_000 + n, listed_at=utcnow()))
    db.commit()
    rows = market._rows(db, market._base().where(MarketListing.status == "active"))
    assert len(rows) == 24
    with statements(db) as sql:
        out = market._out_many(db, rows, None)
    assert [x["estimate"]["value"] for x in out] == [market.item_estimate(db, i)["value"] for _, i, _ in rows]
    assert [x["estimate"] for x in out] == [{**{k: market.item_estimate(db, i).get(k) for k in ("value", "low", "high", "source", "confidence",
                                                                                                    "note", "as_of")}, "currency": "USD"}
                                            for _, i, _ in rows]
    assert any(x["estimate"]["source"] == "sales" for x in out)
    assert len(sql) <= 6, len(sql)  # photos + provider prices + one sales read per graded grade (was ~3 per listing)
    with statements(db) as sql:
        market.home(db, None, __import__("app.config.settings", fromlist=["get_settings"]).get_settings())
    assert len(sql) < 40, len(sql)


def test_member_missions_read_progress_and_rules_once(db: Session) -> None:
    """The missions list reads the member's progress rows and the XP rules once, not once per mission."""
    from app.members import missions
    from app.models.members import MemberMission, Mission

    m = _member(db, "missionfan")
    now = utcnow()
    for i in range(12):
        db.add(Mission(key=f"perf-{i}", kind="manual", cadence=("daily", "weekly", "once")[i % 3], title=f"Mission {i}", description="d",
                       platform="website", xp=5, verification="verified", status="active", priority=i))
    db.commit()
    ms = db.scalars(select(Mission).where(Mission.key.like("perf-%")).order_by(Mission.id)).all()
    for mi in ms[:6]:
        db.add(MemberMission(member_id=m.id, mission_id=mi.id, period=missions.period_for(mi.cadence, now),
                             status="completed" if mi.priority % 2 else "pending", progress=1))
    db.commit()
    with statements(db) as sql:
        out = {x["key"]: x for x in missions.for_member(db, m)}
    assert [out[f"perf-{i}"]["status"] for i in range(12)] == ["pending", "completed"] * 3 + ["available"] * 6
    assert len([x for x in out.values()]) >= 12
    assert sum("FROM member_missions" in q for q in sql) <= 3, sum("FROM member_missions" in q for q in sql)
    assert sum("FROM xp_rules" in q for q in sql) <= 2


def test_auto_checks_earlier_intents_and_positions_once_per_signal(db: Session) -> None:
    from app.models.trading import TrIntent
    from app.tradingos import execution

    sig = TsSignal(number=7, coin="SOL", side="LONG", timeframe="1h", strategy="t", entry_lo=99, entry_hi=101, stop=95, target=110,
                   lev_lo=3, lev_hi=5, rr=2, status="ACTIVE", expires_at=utcnow() + timedelta(hours=1), published_at=utcnow())
    db.add(sig)
    db.flush()
    for i in range(6):
        acc = execution.account(db, _member(db, f"auto{i}"))
        acc.auto_enabled = True
        db.add(TrIntent(account_id=acc.id, signal_id=sig.id, token=f"tok{i}", mode=acc.mode, plan={"skipped": "x"}, status="SKIPPED",
                        expires_at=utcnow()))
    db.commit()
    with statements(db) as sql:
        assert execution.run_auto(db, sig) == []  # each already considered
    assert sum("FROM tr_intents" in q for q in sql) == 1 and sum("FROM tr_positions" in q for q in sql) == 1
