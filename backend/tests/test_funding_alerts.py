"""Earn › Funding Arb alerts: threshold net of estimated fees, per-coin cooldown kept in the database, and nothing posted
without a publish chat. A fake Telegram client and a hand-made board: nothing here reads a venue or trades."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.arbitrage import funding_alerts as fa
from app.models.arbitrage import ArbFundingAlert
from app.tradingos import signals

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


class Client:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[Any, str]] = []
        self.fail = fail

    async def send_message(self, chat: Any, text: str, **kw: Any) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append((chat, text))
        return {}


def leg(venue: str, apr: float) -> dict[str, Any]:
    return {"venue": venue, "rate_raw": apr / 100 / 8760, "interval_hours": 1.0, "apr_pct": apr, "hourly": apr / 100 / 8760}


def board(*rows: tuple[str, str, float, str, float]) -> dict[str, Any]:
    """rows: (symbol, short venue, short APR, long venue, long APR)."""
    return {"rows": [{"symbol": s, "legs": [leg(sv, sa), leg(lv, la)], "short": sv, "long": lv, "gross_spread_apr_pct": sa - la}
                     for s, sv, sa, lv, la in rows]}


@pytest.fixture(autouse=True)
def alerts_on(db: Session) -> None:
    """Off by default (owner, 2026-10-23): these tests turn them on at the old 30% line."""
    s = signals.settings(db)
    s.strategies = {**s.strategies, "funding_alerts": {"on": True, "min_net_apr": 30}}
    db.commit()


def run(db: Session, b: dict[str, Any], now: datetime = NOW, client: Client | None = None) -> tuple[dict[str, Any], Client]:
    c = client or Client()
    return asyncio.run(fa.run(db, now, c, board=b)), c


def net(n: float) -> dict[str, Any]:
    """A SOL board whose Lighter-long / Hyperliquid-short spread nets exactly n% APR."""
    return board(("SOL", "hyperliquid", n + fa.fee_apr("lighter", "hyperliquid"), "lighter", 0.0))


def test_fee_maths_round_trip_both_legs_over_a_week() -> None:
    # Hyperliquid 4.5 + Orderly 5.0 bps, in and out: 19 bps per week, annualized
    assert fa.fee_apr("hyperliquid", "orderly") == pytest.approx(0.19 * 8760 / 168)
    assert fa.fee_apr("newvenue", "lighter") == pytest.approx(2 * (5.0 + 2.0) / 100 * 8760 / 168)


def test_threshold_is_net_of_fees(db: Session) -> None:
    fees = fa.fee_apr("lighter", "hyperliquid")
    b = board(("BTC", "hyperliquid", 30 + fees + 0.5, "lighter", 0.0),  # net 30.5: over
              ("ETH", "hyperliquid", 30 + fees - 0.5, "lighter", 0.0))  # gross over 30, net under
    out, c = run(db, b)
    assert out["posted"] == ["BTC 30.5%"] and len(c.sent) == 1
    chat, text = c.sent[0]
    assert chat == "@botpricechat"
    assert "BTC" in text and "Long Lighter" in text and "Short Hyperliquid" in text and "30.5% APR" in text
    assert "https://pricechat.live/earn/funding" in text and "Not financial advice" in text
    row = db.query(ArbFundingAlert).one()
    assert (row.symbol, row.long_venue, row.short_venue, row.net_apr_pct) == ("BTC", "lighter", "hyperliquid", 30.5)


def test_off_by_default(db: Session) -> None:
    s = signals.settings(db)
    s.strategies = {k: v for k, v in s.strategies.items() if k != "funding_alerts"}
    db.commit()
    assert fa.config(db) == {"on": False, "min_net_apr": 50.0}
    out, c = run(db, net(200))
    assert "off" in out["skipped"] and not c.sent


def test_cooldown_24h_unless_the_spread_doubles(db: Session) -> None:
    assert run(db, net(40))[0]["posted"] == ["SOL 40.0%"]
    assert run(db, net(70), NOW + timedelta(hours=1))[0]["posted"] == []  # +75%: still cooling down
    assert run(db, net(80), NOW + timedelta(hours=2))[0]["posted"] == ["SOL 80.0%"]  # doubled: posted again
    assert run(db, net(150), NOW + timedelta(hours=3))[0]["posted"] == []  # measured from the new post
    assert run(db, net(35), NOW + timedelta(hours=14))[0]["posted"] == []  # 12 h is no longer enough
    assert run(db, net(35), NOW + timedelta(hours=26, minutes=1))[0]["posted"] == ["SOL 35.0%"]  # 24 h since: due again


def test_nothing_posts_without_a_publish_chat_or_when_off(db: Session) -> None:
    b = board(("BTC", "hyperliquid", 200.0, "lighter", 0.0))
    signals.settings(db).publish_chat = "  "
    db.commit()
    out, c = run(db, b)
    assert "publish chat" in out["skipped"] and not c.sent
    s = signals.settings(db)
    s.publish_chat, s.strategies = "@botpricechat", {**s.strategies, "funding_alerts": {"on": False, "min_net_apr": 30}}
    db.commit()
    out, c = run(db, b)
    assert "off" in out["skipped"] and not c.sent and db.query(ArbFundingAlert).count() == 0


def test_threshold_setting_failed_sends_and_the_per_run_cap(db: Session) -> None:
    s = signals.settings(db)
    s.strategies = {**s.strategies, "funding_alerts": {"on": True, "min_net_apr": 100}}
    db.commit()
    b = board(*[(f"C{i}", "hyperliquid", 150.0 + i, "lighter", 0.0) for i in range(5)], ("LOW", "hyperliquid", 90.0, "lighter", 0.0))
    out, _ = run(db, b, client=Client(fail=True))
    assert out["posted"] == [] and db.query(ArbFundingAlert).count() == 0  # not recorded: retried next run
    out, _ = run(db, b)
    assert out["above_threshold"] == 5 and [p.split()[0] for p in out["posted"]] == ["C4"]  # the widest only


def test_at_most_four_posts_a_day(db: Session) -> None:
    b = board(*[(f"C{i}", "hyperliquid", 150.0 + i, "lighter", 0.0) for i in range(8)])
    posted = [p for k in range(8) for p in run(db, b, NOW + timedelta(minutes=10 * k))[0]["posted"]]
    assert [p.split()[0] for p in posted] == ["C7", "C6", "C5", "C4"]
    assert run(db, b, NOW + timedelta(hours=24, minutes=1))[0]["posted"] == ["C7 150.2%"]  # a day on (net of fees): the oldest is due again
