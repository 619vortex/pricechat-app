"""Data retention (system.retention): old rows go, recent and in-use rows stay, at most BATCH per table a run."""

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import retention
from app.brand_manager import brands
from app.brand_manager.queue import BOOTSTRAP_LOG
from app.db.base import utcnow
from app.models.activity import ActivityLog
from app.models.brand_manager import BmEvent, BmMetric
from app.models.enums import ActivityLevel
from app.models.tradingbot import TbReferralClick
from app.models.tradingos import TsPriceAlert, TsSignalIdea


def _count(db: Session, model, *cond) -> int:  # type: ignore[no-untyped-def]
    return int(db.scalar(select(func.count(model.id)).where(*cond)) or 0)


def _idea(status: str, at, **kw) -> TsSignalIdea:  # type: ignore[no-untyped-def]
    return TsSignalIdea(venue="hyperliquid", kind="perp", coin="BTC", tag="$BTC", timeframe="1h", side="LONG", entry_lo=1, entry_hi=2,
                        stop=0.5, target=3, family="pullback", chat_type="group", status=status, created_at=at, updated_at=at, **kw)


def _alert(status: str, at) -> TsPriceAlert:  # type: ignore[no-untyped-def]
    return TsPriceAlert(telegram_user_id=1, coin="BTC", kind="above", price=1, last_price=1, status=status, expires_at=at,
                        created_at=at, updated_at=at)


def test_old_rows_go_recent_and_in_use_rows_stay(db: Session) -> None:
    now = utcnow()
    old, new = now - timedelta(days=500), now - timedelta(days=1)
    b = brands.ensure_default(db)
    a = brands.automation(db, b)
    a.started_at = now - timedelta(days=300)
    db.add_all([BmEvent(brand_id=b.id, at=old, message="old"), BmEvent(brand_id=b.id, at=new, message="new"),
                BmEvent(brand_id=b.id, at=old, message=BOOTSTRAP_LOG)])
    first = BmMetric(brand_id=b.id, at=now - timedelta(days=400), followers=10)  # each brand's first reading: kept
    mid = BmMetric(brand_id=b.id, at=now - timedelta(days=350), followers=11)  # older than 180 d, not a baseline: goes
    since = BmMetric(brand_id=b.id, at=now - timedelta(days=299), followers=12)  # first since AUTO started: kept
    db.add_all([first, mid, since, BmMetric(brand_id=b.id, at=new, followers=20)])
    db.add_all([TbReferralClick(at=old, placement="card", source="web"), TbReferralClick(at=new, placement="card", source="web")])
    db.add_all([_idea("SKIPPED", old), _idea("SKIPPED", new), _idea("OPEN", old), _idea("FILLED", old),
                _idea("TARGET", old, filled_at=old, resolved_at=old, r=2.0)])
    db.add_all([_alert("fired", old), _alert("expired", old), _alert("fired", new), _alert("active", old)])
    db.add_all([ActivityLog(event="x", message="old", created_at=old), ActivityLog(event="x", message="new", created_at=new),
                ActivityLog(event="safety", message="audit", level=ActivityLevel.AUDIT, created_at=old),
                ActivityLog(event="ai_inference", message="last ok", created_at=old)])
    db.commit()
    ids = (first.id, mid.id, since.id)

    out = retention.run(db, now)
    new_tables = ("member_sessions", "member_notifications", "pokemon_scans", "pokemon_sync_logs", "ex_oracle_snapshots", "usage_counters")
    assert out == {"bm_events": 1, "bm_metrics": 1, "tb_referral_clicks": 1, "ts_signal_ideas": 1, "ts_price_alerts": 2, "activity_logs": 1,
                   **{t: 0 for t in new_tables}}
    assert {e.message for e in db.scalars(select(BmEvent))} == {"new", BOOTSTRAP_LOG}
    assert set(db.scalars(select(BmMetric.id).where(BmMetric.id.in_(ids)))) == {ids[0], ids[2]}
    assert _count(db, TbReferralClick) == 1
    assert sorted(db.scalars(select(TsSignalIdea.status))) == ["FILLED", "OPEN", "SKIPPED", "TARGET"]
    assert sorted(db.scalars(select(TsPriceAlert.status))) == ["active", "fired"]
    assert {x.message for x in db.scalars(select(ActivityLog).where(ActivityLog.created_at < now - timedelta(days=90)))} == {"audit", "last ok"}
    assert set(retention.run(db, now).values()) == {0}  # nothing left to do


def test_batch_limit_is_honoured(db: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(retention, "BATCH", 3)
    old = utcnow() - timedelta(days=500)
    db.add_all([TbReferralClick(at=old, placement="card", source="web") for _ in range(7)])
    db.commit()
    assert [retention.run(db, utcnow())["tb_referral_clicks"] for _ in range(4)] == [3, 3, 1, 0]


def test_sessions_notifications_scans_logs_oracle_and_usage(db: Session) -> None:
    """Expired sessions, read notifications, old scans and run logs, thinned oracle history and old usage tallies go; what
    the product still reads stays (unread notifications, each provider's newest log, a day's last oracle evaluation, review flags)."""
    from app.exchange.ledger import ensure_markets
    from app.members import service as members
    from app.members.catalog import ensure_catalog
    from app.models.exchange import ExOracleSnapshot
    from app.models.members import MemberNotification, MemberSession
    from app.models.pokemon import PokemonSyncLog
    from app.models.portfolio import PokemonScan
    from app.models.social_intel import UsageCounter

    now = utcnow().replace(hour=12)
    old, new = now - timedelta(days=500), now - timedelta(days=1)
    ensure_catalog(db)
    m = members.create_member(db)
    db.flush()
    db.add_all([MemberSession(member_id=m.id, token_hash="a" * 64, expires_at=now - timedelta(days=8)),
                MemberSession(member_id=m.id, token_hash="b" * 64, expires_at=now - timedelta(days=2)),  # expired recently: kept a while
                MemberSession(member_id=m.id, token_hash="c" * 64, expires_at=now + timedelta(days=20))])
    db.add_all([MemberNotification(member_id=m.id, kind="xp", title="old read", created_at=old, read_at=old),
                MemberNotification(member_id=m.id, kind="xp", title="old unread", created_at=old),
                MemberNotification(member_id=m.id, kind="xp", title="new read", created_at=new, read_at=new)])
    db.add_all([PokemonScan(member_id=m.id, at=old, image_sha256="x", status="ok"), PokemonScan(member_id=m.id, at=new, image_sha256="y", status="ok")])
    db.add_all([PokemonSyncLog(provider="scrydex", started_at=old - timedelta(days=1), status="ok"),
                PokemonSyncLog(provider="psa", started_at=old, status="ok"),  # psa's newest: kept however old
                PokemonSyncLog(provider="scrydex", started_at=old, status="ok"), PokemonSyncLog(provider="scrydex", started_at=new, status="ok")])
    from app.pokemon import catalog as cards

    cards.ensure_catalog(db)
    mk = ensure_markets(db)[0]
    day = old.replace(hour=0, minute=0, second=0, microsecond=0)
    snaps = [ExOracleSnapshot(market_id=mk.id, at=day + timedelta(hours=h), status="ok", review=(h == 3), provenance={}) for h in (1, 2, 3, 4)]
    db.add_all(snaps + [ExOracleSnapshot(market_id=mk.id, at=new, status="ok", provenance={}),
                        ExOracleSnapshot(market_id=mk.id, at=new + timedelta(minutes=15), status="ok", provenance={})])
    db.add_all([UsageCounter(day=old.strftime("%Y-%m-%d"), kind="model", key="k"), UsageCounter(day=new.strftime("%Y-%m-%d"), kind="model", key="k")])
    db.commit()

    out = retention.run(db, now)
    assert {k: out[k] for k in ("member_sessions", "member_notifications", "pokemon_scans", "pokemon_sync_logs", "ex_oracle_snapshots",
                                "usage_counters")} == {"member_sessions": 1, "member_notifications": 1, "pokemon_scans": 1,
                                                       "pokemon_sync_logs": 2, "ex_oracle_snapshots": 2, "usage_counters": 1}
    assert sorted(db.scalars(select(MemberSession.token_hash))) == ["b" * 64, "c" * 64]
    assert {"new read", "old unread"} <= set(db.scalars(select(MemberNotification.title))) and _count(db, MemberNotification, MemberNotification.title == "old read") == 0
    assert _count(db, PokemonScan) == 1
    assert sorted((p, s.date() == new.date()) for p, s in db.execute(select(PokemonSyncLog.provider, PokemonSyncLog.started_at))) ==         [("psa", False), ("scrydex", True)]
    kept = set(db.scalars(select(ExOracleSnapshot.id).where(ExOracleSnapshot.at < now - timedelta(days=90))))
    assert kept == {snaps[2].id, snaps[3].id}  # the review-flagged one and the day's last
    assert _count(db, ExOracleSnapshot, ExOracleSnapshot.at >= now - timedelta(days=90)) == 2  # recent history untouched
    assert _count(db, UsageCounter) == 1
    assert set(retention.run(db, now).values()) == {0}
