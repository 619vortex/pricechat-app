"""Query-shape regressions for hot paths: same results, bounded number of statements (no N+1, no whole-table work)."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import event, select
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
    assert out["participation"] == {"enter": 1}
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
