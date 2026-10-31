"""FC and /signal stay separate (the owner's product rule, 2026-10-31). "FC BTC" is a market chart: the image, price, trend,
RSI / MACD / EMAs, support / resistance and the Kiwi Take, never a trade recommendation, and it never runs the signal
rules. "/signal BTC" is the trade analysis. Both end with the bot channel and the Hyperliquid referral link. Every check
reads what was actually sent to Telegram (captions and messages), not internal handler output. TEST FIXTURES only."""

import asyncio
import re
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.tradingos import bot, chart, signals
from tests.test_bot_commands import StudyMD, md  # noqa: F401 - the shared market fixture
from tests.test_trading_os import FakeTG

ANON = 1087968824  # @GroupAnonymousBot: an admin with "Remain anonymous" (ingestion passes this sender id)
# anything that belongs to a trade recommendation or the signal template
FORBIDDEN = [r"\bLONG\b", r"\bSHORT\b", r"\bentry\b", r"\bstop\b", r"\btarget", r"R/R", r"\bplan\b", r"best idea", r"\bsetup\b",
             r"\bwatch\b", r"Signal:", r"NO TRADE", r"\bWAIT\b", r"take[- ]profit", r"🎯", r"🛑", r"✅", r"🟡", r"⚡"]


@pytest.fixture
def links(db: Session) -> None:
    from app.tradingbot.service import settings as earn_settings

    earn_settings(db).referral_url = "https://app.hyperliquid.xyz/join/KIWI"
    db.commit()


def _sent_text(tg: FakeTG, since: int) -> str:
    return "\n".join(str(x[2]) for x in tg.sent[since:])


def _has_links(text: str) -> bool:
    return "@botpricechat" in text and re.search(r"4% off Hyperliquid fees: https?://\S+", text) is not None


@pytest.fixture
def no_signal_engine(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """FC must work with the whole signal pipeline unplugged; and the chart must be drawn without a plan."""
    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("FC called the signal engine")
    for name in ("analyze", "best_plan", "watch_setup"):
        monkeypatch.setattr(signals, name, boom)
    plans: list[Any] = []
    real = chart.render_study

    def spy(*a: Any, **k: Any) -> bytes:
        plans.append(k.get("plan"))
        return real(*a, **k)
    monkeypatch.setattr(chart, "render_study", spy)
    return plans


@pytest.mark.parametrize("typed", ["FC BTC", "fc eth", "Fc $SOL", "fc bitcoin", "FC SOL 1h", "fc btc rsi macd"])
def test_fc_is_the_chart_only_with_both_links(db: Session, md: StudyMD, links: None, no_signal_engine: list[Any], typed: str) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, bot.fc_command(typed) or "", group=True, reply_to=1))
    assert tg.sent and tg.sent[0][0] == "photo", tg.sent  # the chart image first
    text = _sent_text(tg, 0)
    for word in ("Price:", "Trend:", "RSI", "MACD", "EMA", "Support:", "Resistance:", "Kiwi Take:"):
        assert word in text, word
    for pat in FORBIDDEN:
        assert not re.search(pat, text, re.I), f"FC sent {pat!r}:\n{text}"
    assert _has_links(text) and "/signal " in text  # both links, and where the trade analysis lives
    assert no_signal_engine and all(p is None for p in no_signal_engine)  # nothing drawn as a plan on the image


def test_fc_from_an_anonymous_admin_is_the_same_chart(db: Session, md: StudyMD, links: None, no_signal_engine: list[Any]) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, ANON, "/fc BTC", group=True, reply_to=1, author="Price Chat"))
    text = _sent_text(tg, 0)
    assert tg.sent[0][0] == "photo" and _has_links(text) and not any(re.search(p, text, re.I) for p in FORBIDDEN)


def test_fc_respects_bot_chat_routing(db: Session, md: StudyMD, links: None, no_signal_engine: list[Any]) -> None:
    s = signals.settings(db)
    s.main_chat, s.publish_chat, s.chart_routing = "@thepricechat", "@botpricechat", "bot_chat"
    db.commit()
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -100, 9, "/fc BTC", group=True, reply_to=3, chat_username="thepricechat"))
    photos = [x for x in tg.sent if x[0] == "photo"]
    assert len(photos) == 1 and photos[0][1] == "@botpricechat"  # the chart goes to Bot Chat
    assert tg.sent[-1][1] == -100 and "Bot Chat" in tg.sent[-1][2]  # the main chat gets the one-line pointer
    bot_chat = "\n".join(str(x[2]) for x in tg.sent if x[1] == "@botpricechat")
    assert _has_links(bot_chat) and not any(re.search(p, bot_chat, re.I) for p in FORBIDDEN)
    n = len(tg.sent)
    asyncio.run(bot.handle_command(db, tg, -200, 9, "/fc BTC", group=True, reply_to=4, chat_username="botpricechat"))
    assert tg.sent[n][0] == "photo" and tg.sent[n][1] == -200  # asked in Bot Chat itself: answered there


def test_a_full_caption_still_gets_both_links() -> None:
    """A photo caption near Telegram's 1024 limit can't carry the footer: the links follow as their own message."""
    tg = FakeTG()
    footed = bot._Footed(tg, "\n\n📢 Signals: @botpricechat\n🎁 4% off Hyperliquid fees: https://x.test/r")
    caption = "\n".join(f"line {i} " + "x" * 40 for i in range(22))  # ~1000 characters
    asyncio.run(footed.send_photo(-5, b"\x89PNG\r\n\x1a\n" + b"0" * 10, caption))
    assert tg.sent[0][0] == "photo" and len(tg.sent[0][2]) <= 1024
    assert tg.sent[1][0] == "msg" and _has_links(tg.sent[1][2])


@pytest.mark.parametrize("coin", ["BTC", "ETH"])
def test_signal_is_the_trade_analysis_with_both_links(db: Session, md: StudyMD, links: None, coin: str) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, f"/signal {coin}", group=True, reply_to=1))
    text = _sent_text(tg, 0)
    assert re.search(r"\b(LONG|SHORT)\b|WAIT", text) and _has_links(text)
    if re.search(r"(LONG|SHORT) setup", text):  # a setup that passes every rule: the whole plan
        assert "Entry" in text and "Stop" in text and "Target" in text and "R/R" in text


def test_signal_without_a_setup_says_why_with_both_links(db: Session, md: StudyMD, links: None) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/signal FLAT", group=True, reply_to=1))
    text = _sent_text(tg, 0)
    assert re.search(r"NO TRADE|WAIT", text) and "Why:" in text and _has_links(text)
    assert "setup</b> · passes every rule" not in text  # never presented as a signal
    for pat in (r"\bEntry\b", r"\bStop\b", r"\bTarget\b", r"R/R", r"WATCH", "🎯", "🛑", "✅"):  # no invented prices on NO TRADE
        assert not re.search(pat, text), f"NO TRADE sent {pat!r}:\n{text}"
    assert not [x for x in tg.sent if x[0] == "photo"]  # no plan drawn either
