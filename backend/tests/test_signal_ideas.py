"""/signal track record (record.py: logging, dedupe, grading, the record line, /record) and the daily brief. No network."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingos import TsSignal, TsSignalIdea
from app.tradingos import bot, market, record, signals, weekly
from app.tradingos.market import Candle

from tests.test_bot_commands import StudyMD
from tests.test_trading_os import FakeTG, flat, uptrend_pullback


@pytest.fixture
def md(monkeypatch: pytest.MonkeyPatch) -> StudyMD:
    f = StudyMD({"BTC": uptrend_pullback(400), "ETH": uptrend_pullback(400, 50.0), "SOL": uptrend_pullback(400, 20.0), "FLAT": flat(400)})
    monkeypatch.setattr(market, "_shared", f)
    weekly._cache.clear()
    return f


def idea(db: Session, side: str = "LONG", hours_ago: float = 200, **kw: object) -> TsSignalIdea:
    """LONG: entry 99-101, stop 95 (1R = 5), target 110 (+2R). SHORT mirrored around 100."""
    lv = dict(entry_lo=99.0, entry_hi=101.0, stop=95.0, target=110.0) if side == "LONG" else dict(entry_lo=99.0, entry_hi=101.0, stop=105.0, target=90.0)
    x = TsSignalIdea(venue="hyperliquid", kind="perp", coin="BTC", tag="$BTCUSD", timeframe="1h", side=side, idea="buy a pullback to EMA20",
                     full=False, score=3.0, chat_type="group", created_at=utcnow() - timedelta(hours=hours_ago), **{"family": "pullback", **lv, **kw})
    db.add(x)
    db.commit()
    return x


def bars(x: TsSignalIdea, hl: list[tuple[float, float]], close: float | None = None) -> list[Candle]:
    """Hourly candles opening at the idea's creation, one per (high, low)."""
    t0 = int(x.created_at.timestamp() * 1000)
    return [Candle(t0 + i * 3_600_000, (h + l) / 2, h, l, close if close is not None else (h + l) / 2, 1) for i, (h, l) in enumerate(hl)]


AWAY = (104.0, 102.0)  # above the long's zone, below its target


# ---------------------------------------------------------------- logging and dedupe
def test_signal_logs_each_plan_once_an_hour_without_user_ids(db: Session, md: StudyMD) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/signal SOL", group=True, reply_to=3))
    asyncio.run(bot.handle_command(db, tg, 1, 10, "/signal SOL"))  # same plan, someone else, privately: the same record
    rows = db.scalars(select(TsSignalIdea)).all()
    assert len(rows) == 1 and rows[0].chat_type == "group" and rows[0].venue == "hyperliquid" and rows[0].status == "OPEN"
    assert rows[0].family == "full" and rows[0].full and rows[0].tag == "$SOLUSD"
    assert not any("user" in c or "member" in c for c in TsSignalIdea.__table__.columns.keys())  # no user ids, ever
    asyncio.run(bot.handle_command(db, tg, 1, 10, "/signal FLAT"))
    flat_row = db.scalars(select(TsSignalIdea).where(TsSignalIdea.coin == "FLAT")).one()
    assert flat_row.chat_type == "private" and not flat_row.full and flat_row.family in record.LABEL
    rows[0].created_at = utcnow() - timedelta(hours=2)  # an hour later the same plan is a new record
    db.commit()
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/signal SOL", group=True))
    assert len(db.scalars(select(TsSignalIdea).where(TsSignalIdea.coin == "SOL")).all()) == 2
    assert db.scalar(select(TsSignal)) is None  # still never a published signal


def test_families() -> None:
    assert record.family("buy a pullback to EMA20 in the uptrend", False) == "pullback"
    assert record.family("sell the pop above EMA20 in the downtrend", False) == "pullback"
    assert record.family("buy a break above resistance 1.2", False) == "breakout"
    assert record.family("sell the range high (resistance)", False) == "range"
    assert record.family("momentum points up", False) == "momentum"
    assert record.family("passes every rule", True) == "full"


# ---------------------------------------------------------------- grading
def test_fill_then_target(db: Session) -> None:
    x = idea(db)
    assert record.grade(x, bars(x, [AWAY, (101.5, 100.5), AWAY, (111, 103)]))
    assert x.status == "TARGET" and x.r == 2.0 and x.filled_at is not None and x.resolved_at is not None


def test_fill_then_stop_and_short_mirror(db: Session) -> None:
    x = idea(db)
    record.grade(x, bars(x, [(100.5, 99.5), AWAY, (100, 94)]))
    assert x.status == "STOP" and x.r == -1.0
    s = idea(db, "SHORT")
    record.grade(s, bars(s, [(98, 96), (100.2, 97), (99, 89)]))
    assert s.status == "TARGET" and s.r == 2.0


def test_both_levels_in_one_candle_is_a_stop(db: Session) -> None:
    x = idea(db)
    record.grade(x, bars(x, [(100.5, 99.5), (112, 94)]))
    assert x.status == "STOP" and x.r == -1.0
    y = idea(db)  # the fill candle itself reaches the stop: STOP (its target touch isn't counted, the order is unknown)
    record.grade(y, bars(y, [(112, 94), AWAY]))
    assert y.status == "STOP"


def test_expiry_unfilled_and_unresolved(db: Session) -> None:
    x = idea(db)
    record.grade(x, bars(x, [AWAY] * 23))
    assert x.status == "OPEN"  # 23 candles: still waiting
    record.grade(x, bars(x, [AWAY] * 24 + [(100, 99)]))
    assert x.status == "EXPIRED" and x.filled_at is None and x.r is None  # a fill after 24 candles doesn't count
    y = idea(db)
    record.grade(y, bars(y, [(100.5, 99.5)] + [AWAY] * 50))
    assert y.status == "FILLED" and y.resolved_at is None
    record.grade(y, bars(y, [(100.5, 99.5)] + [AWAY] * 72, close=103.0))
    assert y.status == "EXPIRED" and y.r == 0.6  # (103 - 100) / 5


def test_only_closed_candles_after_the_idea_count(db: Session) -> None:
    x = idea(db, hours_ago=0.5)
    t = int((x.created_at - timedelta(hours=1)).timestamp() * 1000)
    before = Candle(t, 100, 112, 94, 100, 1)  # before it was shown
    forming = Candle(t + 3_600_000 + 1, 100, 100.5, 99.5, 100, 1)  # still open
    assert not record.grade(x, [before, forming]) and x.status == "OPEN"


def test_grade_all_reads_candles_once_per_market(db: Session) -> None:
    x, y = idea(db), idea(db, "SHORT")
    calls: list[tuple[str, str, int]] = []

    class MD:
        def candles(self, coin: str, tf: str, n: int) -> list[Candle]:
            calls.append((coin, tf, n))
            return bars(x, [(100.5, 99.5), (111, 100)])

    out = record.grade_all(db, MD())  # type: ignore[arg-type]
    assert len(calls) == 1 and calls[0][2] >= 200 and out == {"open": 2, "changed": 2, "errors": 0}
    assert (x.status, y.status) == ("TARGET", "STOP")


# ---------------------------------------------------------------- the record line and /record
def graded(db: Session, fam: str, wins: int, losses: int) -> None:
    for i in range(wins + losses):
        x = idea(db, family=fam)
        x.status, x.r = ("TARGET", 2.0) if i < wins else ("STOP", -1.0)
        x.filled_at = x.resolved_at = utcnow() - timedelta(hours=i)
    db.commit()


def test_record_line_by_family_then_overall(db: Session) -> None:
    assert record.record_line(db, "pullback") == ""
    graded(db, "range", 3, 2)
    graded(db, "pullback", 3, 4)
    assert record.record_line(db, "pullback") == "📈 Track record: /signal ideas 50% hit target (last 12 graded)"  # 7 < 10: overall
    graded(db, "pullback", 3, 0)
    assert record.record_line(db, "pullback") == "📈 Track record: pullback ideas 60% hit target (last 10 graded)"
    x = idea(db)  # never filled: not in the hit rate
    x.status = "EXPIRED"
    db.commit()
    assert "last 10 graded" in record.record_line(db, "pullback")


def test_signal_reply_carries_the_record_line(db: Session, md: StudyMD) -> None:
    graded(db, "full", 7, 3)
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/signal SOL", group=True))
    assert "📈 Track record: full setups 70% hit target (last 10 graded)" in tg.sent[-1][2]


def test_record_command(db: Session, md: StudyMD) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/record", group=True))
    assert "Nothing graded yet" in tg.sent[-1][2]
    graded(db, "pullback", 3, 1)
    x = idea(db)
    x.status = "EXPIRED"  # unfilled
    db.commit()
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/record", group=True))
    text = tg.sent[-1][2]
    assert "track record" in text and "<code>pullback    5   80%  75%  +1.25</code>" in text and "<code>all" in text
    assert "/record" in bot.HELP


# ---------------------------------------------------------------- the daily brief
def at(h: int, day: int = 22) -> datetime:
    return datetime(2026, 10, day, h, 30, tzinfo=timezone.utc)


def test_daily_brief_once_a_day(db: Session, md: StudyMD) -> None:
    s = signals.settings(db)
    s.publish_chat = "@botpricechat"
    db.commit()
    tg = FakeTG()
    assert asyncio.run(bot.daily_brief(db, tg, md, at(7))) == "waiting for 08:00 UTC" and not tg.sent
    assert asyncio.run(bot.daily_brief(db, tg, md, at(8))) == "sent"
    assert [k for k, *_ in tg.sent] == ["msg", "photo"] and tg.sent[0][1] == "@botpricechat"
    assert "Daily brief" in tg.sent[0][2] and "market overview" in tg.sent[0][2]
    assert "Most promising now" in tg.sent[1][2] and "Entry" in tg.sent[1][2]
    assert db.scalar(select(TsSignalIdea).where(TsSignalIdea.chat_type == "brief")) is not None  # its plan is tracked too
    db.expire_all()  # a restart: the date is in the database
    assert asyncio.run(bot.daily_brief(db, tg, md, at(9))) == "already sent today" and len(tg.sent) == 2
    assert asyncio.run(bot.daily_brief(db, tg, md, at(8, 23))) == "sent" and len(tg.sent) == 4
    s = signals.settings(db)
    s.brief_enabled = False
    db.commit()
    assert asyncio.run(bot.daily_brief(db, tg, md, at(8, 24))) == "off"
    s.brief_enabled, s.publish_chat = True, ""
    db.commit()
    assert asyncio.run(bot.daily_brief(db, tg, md, at(8, 24))) == "off" and len(tg.sent) == 4


def test_daily_brief_retries_when_nothing_went_out(db: Session, md: StudyMD) -> None:
    signals.settings(db).publish_chat = "@botpricechat"
    db.commit()

    class Down(FakeTG):
        async def send_message(self, *a: object, **kw: object) -> dict:  # type: ignore[override]
            raise RuntimeError("telegram down")

    with pytest.raises(RuntimeError):
        asyncio.run(bot.daily_brief(db, Down(), md, at(8)))
    assert signals.settings(db).brief_sent_on is None
    tg = FakeTG()
    assert asyncio.run(bot.daily_brief(db, tg, md, at(9))) == "sent"
