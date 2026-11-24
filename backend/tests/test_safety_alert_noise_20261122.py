"""The safety module's owner alerts and automatic stop (2026-11-22): production held the same PIEVERSE liquidation figure on every 5-minute pass
("Binance + OKX: $3.4M in an hour is 370 times PIEVERSE's typical recorded hour") and tripped "News Desk SAFETY STOP" although market posts were
already paused by default. Now: one owner alert per (kind, subject) per hour, every anomaly still recorded; the stop trips (and is told) only
while market posts are on. No test reaches a real service."""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.newsdesk import engine, events, safety

NOW = datetime(2026, 11, 22, 9, 0, tzinfo=timezone.utc)
WHY = "Binance + OKX: $3.4M in an hour is 370 times PIEVERSE's typical recorded hour"


@pytest.fixture
def told(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    async def notify(db_: Session, key: str, text: str, **kw: Any) -> bool:
        out.append((key, text))
        return True

    import app.alerts

    monkeypatch.setattr(app.alerts, "notify_owner", notify)
    safety._pending.clear()
    return out


def desk(db: Session, state: dict[str, Any]) -> Any:
    row = engine.settings_row(db)
    row.state = state
    db.commit()
    return row


def held(told: list[tuple[str, str]]) -> list[str]:
    return [k for k, _ in told if k.startswith("newsdesk:safety:") and k != "newsdesk:safety:stop"]


async def hold_every_five_minutes(db: Session, passes: int, start: datetime = NOW, subject: str = "PIEVERSE") -> None:
    for i in range(passes):
        safety.note("implausible_liquidations", subject, WHY, total=3_400_000.0)
        assert await safety.settle(db, start + timedelta(minutes=5 * i)) == 1


async def test_the_same_figure_every_five_minutes_is_told_once_an_hour_and_recorded_every_time(db: Session, told: list[tuple[str, str]],
                                                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "MARKET_PAUSE_DEFAULT", True)
    row = desk(db, {})  # market_pause unset: paused by default
    await hold_every_five_minutes(db, 12)  # 09:00 .. 09:55
    assert held(told) == ["newsdesk:safety:implausible_liquidations:PIEVERSE"]
    db.refresh(row)
    assert len(row.state["anomalies"]) == 12 and all(a["subject"] == "PIEVERSE" for a in row.state["anomalies"])  # Admin sees every one
    assert "PIEVERSE" in told[0][1] and "Nothing was published" in told[0][1]
    await hold_every_five_minutes(db, 1, NOW + timedelta(minutes=60))  # an hour after the first alert: told again
    assert len(held(told)) == 2


async def test_another_subject_or_kind_is_told_on_its_own(db: Session, told: list[tuple[str, str]]) -> None:
    desk(db, {})
    await hold_every_five_minutes(db, 1)
    await hold_every_five_minutes(db, 1, NOW + timedelta(minutes=5), subject="DOGE")
    safety.note("implausible_move", "PIEVERSE", "a broken bar", change=-99.0)
    await safety.settle(db, NOW + timedelta(minutes=10))
    assert held(told) == ["newsdesk:safety:implausible_liquidations:PIEVERSE", "newsdesk:safety:implausible_liquidations:DOGE",
                          "newsdesk:safety:implausible_move:PIEVERSE"]


async def test_market_posts_paused_by_default_never_trip_the_stop(db: Session, told: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "MARKET_PAUSE_DEFAULT", True)  # production's value (the test suite runs unpaused: conftest)
    row = desk(db, {})
    assert events.market_paused(db)
    await hold_every_five_minutes(db, 6)
    db.refresh(row)
    assert "newsdesk:safety:stop" not in [k for k, _ in told] and not any("SAFETY STOP" in t for _, t in told)
    assert "market_pause" not in row.state and "market_pause_reason" not in row.state  # still unset: the default, not an automatic stop
    assert events.market_paused(db)


async def test_market_posts_paused_by_the_owner_never_trip_the_stop(db: Session, told: list[tuple[str, str]]) -> None:
    row = desk(db, {"market_pause": True})
    await hold_every_five_minutes(db, 6)
    db.refresh(row)
    assert not any("SAFETY STOP" in t for _, t in told)
    assert row.state["market_pause"] is True and "market_pause_reason" not in row.state  # the owner's own pause, untouched


async def bad_prices(db: Session, subjects: list[str], start: datetime = NOW) -> None:
    for i, sym in enumerate(subjects):
        safety.note("implausible_move", sym, "a broken bar", change=-99.0)
        assert await safety.settle(db, start + timedelta(minutes=5 * i)) == 1


async def test_market_posts_resumed_still_trip_the_stop_on_bad_price_data_and_tell_the_owner(db: Session, told: list[tuple[str, str]]) -> None:
    row = desk(db, {"market_pause": False})  # the owner resumed market posts
    assert not events.market_paused(db)
    await bad_prices(db, ["^GSPC", "^IXIC", "^DJI"])  # three different index figures that can't be believed
    db.refresh(row)
    # 2026-11-24: scoped to the class (indices); the owner's switch, crypto, stocks and commodities stay on
    assert row.state["market_pause"] is False and row.state["class_pause"]["index"]["reason"].startswith("automatic: 3 implausible")
    assert not events.market_paused(db) and set(row.state["class_pause"]) == {"index"}
    assert [k for k, _ in told].count("newsdesk:safety:stop") == 1 and any("SAFETY STOP" in t for _, t in told)
    from app.models.newsdesk import NdEvent

    idx = NdEvent(key="i", kind="market", title="t", headline="h", topic="move", facts={"symbol": "^GSPC", "two_sources": True})
    gold = NdEvent(key="g", kind="market", title="t", headline="h", topic="move", facts={"symbol": "GC=F", "two_sources": True})
    assert "index market posts are paused" in events.held_market(db, idx) and events.held_market(db, gold) == ""
    await bad_prices(db, ["^RUT", "^VIX", "^FTSE"], NOW + timedelta(minutes=15))  # indices already paused: no second stop
    assert [k for k, _ in told].count("newsdesk:safety:stop") == 1


async def test_one_bad_figure_per_class_pauses_nothing_but_a_broad_failure_pauses_every_class_involved(db: Session, told: list[tuple[str, str]]) -> None:
    row = desk(db, {"market_pause": False})
    await bad_prices(db, ["^GSPC", "GC=F", "BTC-USD"])  # one problem in each of three classes: each figure is held, nothing is paused
    db.refresh(row)
    assert not row.state.get("class_pause") and "newsdesk:safety:stop" not in [k for k, _ in told]
    await bad_prices(db, ["SI=F", "ETH-USD", "NVDA"], NOW + timedelta(minutes=10))  # six across classes within the hour: the feed is suspect
    db.refresh(row)
    assert set(row.state["class_pause"]) == {"index", "commodity", "crypto", "stock"} and row.state["market_pause"] is False


async def test_one_figure_reread_every_pass_is_one_anomaly_and_trips_nothing(db: Session, told: list[tuple[str, str]]) -> None:
    # production 2026-11-22 09:35 UTC: the same PIEVERSE figure, re-read three times, paused every market post (owner, 2026-11-23: count
    # distinct problems)
    row = desk(db, {"market_pause": False})
    await hold_every_five_minutes(db, 3)
    db.refresh(row)
    assert row.state["market_pause"] is False and not row.state.get("liq_pause")
    assert held(told) == ["newsdesk:safety:implausible_liquidations:PIEVERSE"]  # still recorded and told once


async def test_bad_liquidation_data_pauses_liquidation_posts_never_price_news(db: Session, told: list[tuple[str, str]]) -> None:
    # owner, 2026-11-23: "A liquidation-data problem must never silence independently verified market news"
    row = desk(db, {"market_pause": False})
    for i, coin in enumerate(["PIEVERSE", "DOGE", "ZEC"]):
        await hold_every_five_minutes(db, 1, NOW + timedelta(minutes=5 * i), subject=coin)
    db.refresh(row)
    assert row.state["liq_pause"] is True and row.state["liq_pause_reason"].startswith("automatic: 3 implausible liquidation")
    assert row.state["market_pause"] is False and not events.market_paused(db) and events.liquidations_paused(db)
    assert [k for k, _ in told].count("newsdesk:safety:liq-stop") == 1 and "newsdesk:safety:stop" not in [k for k, _ in told]
    from app.models.newsdesk import NdEvent

    liq = NdEvent(key="l", kind="market", title="t", headline="h", topic="liquidations", facts={"assets": ["LIQ:BTC"]})
    move = NdEvent(key="m", kind="market", title="t", headline="h", topic="move", facts={"symbol": "^GSPC", "two_sources": True})
    assert "liquidation posts are paused" in events.held_market(db, liq) and events.held_market(db, move) == ""


async def test_with_an_unpaused_default_an_unset_switch_counts_as_on(db: Session, told: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "MARKET_PAUSE_DEFAULT", False)  # the same rule as events.market_paused, whatever the default
    row = desk(db, {})
    await bad_prices(db, ["BTC-USD", "ETH-USD", "SOL-USD"])
    db.refresh(row)
    assert set(row.state["class_pause"]) == {"crypto"} and [k for k, _ in told].count("newsdesk:safety:stop") == 1


async def test_the_old_automatic_market_pause_moves_to_the_scope_its_anomalies_justify(db: Session) -> None:
    # production 2026-11-22 09:35 UTC: "automatic: 3 implausible or contradicted figures" from PIEVERSE liquidation readings held every
    # market post until 11-24 (the 11-23 US rally never went out). On the first pass it becomes a liquidation pause only.
    at = (NOW - timedelta(days=2)).isoformat()
    liq = [{"kind": "implausible_liquidations", "subject": "PIEVERSE", "why": "w", "at": at}] * 3
    row = desk(db, {"market_pause": True, "market_pause_reason": "automatic: 3 implausible or contradicted figures within 60 minutes",
                    "market_pause_at": at, "anomalies": liq})
    assert safety.migrate_pause(db, NOW) == "liquidation posts"
    db.refresh(row)
    assert row.state["market_pause"] is False and row.state["liq_pause"] is True and not row.state.get("class_pause")
    assert row.state["pause_migrated"]["from"].startswith("automatic") and not events.market_paused(db)
    assert safety.migrate_pause(db, NOW) == ""  # once


async def test_the_owners_own_market_pause_is_never_moved(db: Session) -> None:
    row = desk(db, {"market_pause": True, "market_pause_reason": "paused by the owner"})
    assert safety.migrate_pause(db, NOW) == ""
    db.refresh(row)
    assert row.state["market_pause"] is True and events.market_paused(db)


async def test_a_price_triggered_automatic_pause_is_never_migrated(db: Session) -> None:
    # owner, 2026-11-24: "Ensure only liquidation-triggered automatic pauses migrate to liquidation scope"
    at = (NOW - timedelta(hours=5)).isoformat()
    mixed = [{"kind": "implausible_liquidations", "subject": "DOGE", "why": "w", "at": at},
             {"kind": "second_source", "subject": "HG=F", "why": "w", "at": at}, {"kind": "implausible_move", "subject": "^GSPC", "why": "w", "at": at}]
    row = desk(db, {"market_pause": True, "market_pause_reason": "automatic: 3 implausible or contradicted figures within 60 minutes",
                    "market_pause_at": at, "anomalies": mixed})
    assert safety.migrate_pause(db, NOW) == ""
    db.refresh(row)
    assert row.state["market_pause"] is True and not row.state.get("liq_pause") and events.market_paused(db)


async def test_an_automatic_pause_with_no_record_stays_paused(db: Session) -> None:
    row = desk(db, {"market_pause": True, "market_pause_reason": "automatic: 3 implausible or contradicted figures within 60 minutes",
                    "market_pause_at": (NOW - timedelta(days=3)).isoformat(), "anomalies": []})
    assert safety.migrate_pause(db, NOW) == ""
    db.refresh(row)
    assert row.state["market_pause"] is True and events.market_paused(db)


async def test_productions_verified_liquidation_trip_migrates_even_after_its_rows_rolled_off(db: Session) -> None:
    # the 11-23 backup showed seven PIEVERSE liquidation readings around 2026-11-22 09:35:38 UTC and no price anomaly; by deploy time the
    # desk's last-50 anomaly list holds only later figures (NVDA, XAG...), far from the trip
    later = [{"kind": "implausible_liquidations", "subject": "NVDA", "why": "w", "at": "2026-11-23T14:10:00+00:00"}] * 50
    row = desk(db, {"market_pause": True, "market_pause_reason": "automatic: 3 implausible or contradicted figures within 60 minutes",
                    "market_pause_at": "2026-11-22T09:35:38.393492+00:00", "anomalies": later})
    assert safety.migrate_pause(db, NOW + timedelta(days=3)) == "liquidation posts"
    db.refresh(row)
    assert row.state["market_pause"] is False and row.state["liq_pause"] is True
    assert row.state["pause_migrated"]["evidence"].startswith("verified liquidation-only")


async def test_the_emergency_stop_and_a_timed_pause_are_never_touched(db: Session) -> None:
    # owner, 2026-11-24: "Preserve all manually activated emergency stops"
    from app.models.newsdesk import NdSettings

    at = (NOW - timedelta(days=2)).isoformat()
    liq = [{"kind": "implausible_liquidations", "subject": "PIEVERSE", "why": "w", "at": at}] * 3
    row = desk(db, {"market_pause": True, "market_pause_reason": "automatic: 3 implausible or contradicted figures within 60 minutes",
                    "market_pause_at": at, "anomalies": liq})
    s = db.get(NdSettings, 1)
    s.kill_switch, s.paused_until, s.pause_reason = True, NOW + timedelta(hours=6), "owner: stop everything"
    db.commit()
    assert safety.migrate_pause(db, NOW) == "liquidation posts"
    db.refresh(s)
    assert s.kill_switch is True and s.pause_reason == "owner: stop everything" and s.paused_until is not None
    assert s.state["liq_pause"] is True  # only the automatic, liquidation-only market pause moved
