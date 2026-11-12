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
    """Owner's rule, 2026-11-07: "FC BTC" is the chart, the timeframe buttons, the referral link and the Price Chat bot link.
    Nothing else: no readout, no notes, no signal."""
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, bot.fc_command(typed) or "", group=True, reply_to=1))
    assert len(tg.sent) == 1 and tg.sent[0][0] == "photo", tg.sent  # one message: the chart
    text = _sent_text(tg, 0)
    for word in ("Price:", "Trend:", "RSI", "MACD", "EMA", "Support:", "Resistance:", "Kiwi Take:", "/signal", "Signals"):
        assert word not in text, word
    for pat in FORBIDDEN:
        assert not re.search(pat, text, re.I), f"FC sent {pat!r}:\n{text}"
    assert _has_links(text) and text.startswith("📊 <b>$")  # the title: market | timeframe | exchange
    btns = tg.sent[0][3]
    assert [b["callback_data"].split(":")[1] for row in btns for b in row] == ["1m", "5m", "15m", "1h", "4h", "1d", "1w"]
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
def test_signal_is_the_card_without_the_promotional_footer(db: Session, md: StudyMD, links: None, coin: str) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, f"/signal {coin}", group=True, reply_to=1))
    text = _sent_text(tg, 0)
    assert re.search(r"\b(LONG|SHORT)\b|WAIT", text)
    assert not _has_links(text) and "@botpricechat" not in text and "4% off" not in text  # owner 2026-11-12: no promotional clutter
    if re.search(r"<b>(LONG|SHORT) ", text):  # a setup that passes every rule: the whole plan
        assert all(w in text for w in ("Entry", "SL", "TP1", "TP2", "Invalid if", "Not financial advice"))


def test_signal_without_a_setup_says_what_each_side_needs(db: Session, md: StudyMD, links: None) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/signal FLAT", group=True, reply_to=1))
    text = _sent_text(tg, 0)
    assert "WAIT" in text and ("LONG when" in text or "SHORT when" in text) and not _has_links(text)
    for pat in (r"\bEntry\b", r"\bSL\b", r"\bTP1\b", r"R/R", r"WATCH", "🎯", "🛑", "✅"):  # no invented prices on a WAIT
        assert not re.search(pat, text), f"NO TRADE sent {pat!r}:\n{text}"
    assert not [x for x in tg.sent if x[0] == "photo"]  # no plan drawn either


class EditTG(FakeTG):
    """FakeTG that also records a chart replaced in place (editMessageMedia)."""

    async def edit_photo(self, chat_id: Any, message_id: int, png: bytes, caption: str, buttons: Any = None, html: bool = False) -> None:
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        self.sent.append(("edit_photo", message_id, caption, buttons))


def _marked(buttons: Any) -> list[str]:
    return [b["callback_data"].split(":")[1] for row in buttons for b in row if b["text"].startswith("•")]


@pytest.mark.parametrize("typed,tf", [("FC BTC", "4h"), ("FC BTC 1m", "1m"), ("FC BTC 5m", "5m"), ("FC BTC 15m", "15m"), ("FC BTC 15MIN", "15m"),
                                      ("fc btc 15min", "15m"), ("FC BTC 1h", "1h"), ("FC BTC 1HR", "1h"), ("FC BTC 4h", "4h"), ("FC BTC 4H", "4h"),
                                      ("FC BTC 1d", "1d"), ("FC BTC 1DAY", "1d"), ("FC BTC daily", "1d"), ("FC BTC 1w", "1w"), ("FC BTC 1W", "1w"),
                                      ("FC BTC weekly", "1w"), ("FC BTC 5MINS", "5m"), ("FC BTC 1MIN", "1m")])
def test_fc_every_timeframe(db: Session, md: StudyMD, links: None, no_signal_engine: list[Any], typed: str, tf: str) -> None:
    cmd = bot.fc_command(typed)
    assert cmd is not None, typed
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, cmd, group=True, reply_to=1))
    assert len(tg.sent) == 1 and tg.sent[0][0] == "photo", tg.sent
    caption = tg.sent[0][2]
    from app.tradingos.readout import tf_label
    assert f" | {tf_label(tf)} | " in caption and _has_links(caption)
    assert _marked(tg.sent[0][3]) == [tf]
    assert not any(re.search(p, caption, re.I) for p in FORBIDDEN)


@pytest.mark.parametrize("coin", ["BTC", "ETH", "SOL"])
def test_fc_timeframe_buttons_redraw_the_chart_without_signals(db: Session, md: StudyMD, links: None, no_signal_engine: list[Any], coin: str) -> None:
    tg = EditTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, f"/fc {coin}", group=True, reply_to=1))
    btns = tg.sent[0][3]
    message = {"chat": {"id": -5}, "message_id": 101, "reply_markup": {"inline_keyboard": btns}}
    for tf in ("1m", "5m", "15m", "1h", "1d", "1w"):
        data = next(b["callback_data"] for row in message["reply_markup"]["inline_keyboard"] for b in row if b["callback_data"].split(":")[1] == tf)
        n = len(tg.sent)
        asyncio.run(bot.handle_callback(db, tg, {"id": "q", "from": {"id": 9}, "data": data, "message": message}))
        edits = [x for x in tg.sent[n:] if x[0] == "edit_photo"]
        assert len(edits) == 1, tg.sent[n:]
        caption, new_btns = edits[0][2], edits[0][3]
        from app.tradingos.readout import tf_label
        assert caption.startswith(f"📊 <b>${coin}") and f" | {tf_label(tf)} | " in caption and _has_links(caption)
        for word in ("Price:", "Trend:", "RSI", "Kiwi Take:", "/signal"):
            assert word not in caption
        assert not any(re.search(p, caption, re.I) for p in FORBIDDEN)
        assert _marked(new_btns) == [tf]
        message = {**message, "reply_markup": {"inline_keyboard": new_btns}}
    n = len(tg.sent)  # the timeframe on show: nothing redrawn
    asyncio.run(bot.handle_callback(db, tg, {"id": "q", "from": {"id": 9}, "data": f"fc:1w:{coin}", "message": message}))
    assert not [x for x in tg.sent[n:] if x[0] == "edit_photo"] and "Already" in tg.sent[-1][2]
    assert all(p is None for p in no_signal_engine)


def test_fc_bad_button_data_is_refused(db: Session, md: StudyMD) -> None:
    tg = EditTG()
    msg = {"chat": {"id": -5}, "message_id": 101}
    for data in ("fc:2h:BTC", "fc:1h:", "fc:1h"):
        asyncio.run(bot.handle_callback(db, tg, {"id": "q", "from": {"id": 9}, "data": data, "message": msg}))
    assert not [x for x in tg.sent if x[0] == "edit_photo"] and all(x[0] == "answer" for x in tg.sent)


def test_signal_still_has_the_signal_and_its_usual_footer(db: Session, md: StudyMD, links: None) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/signal BTC 15MIN", group=True, reply_to=1))
    text = _sent_text(tg, 0)
    assert re.search(r"\b(LONG|SHORT)\b|WAIT", text) and "· 15m ·" in text and "Signals: @botpricechat" not in text  # the card alone
