"""Vortex trade checks (/help <trade>): the parser, the FACTS maths and risk flags, the fallback when Vortex is away, the
placeholder → answer edit, expiry, late answers, sanitising, the worker API's bearer auth, daily limits, follow-ups and
/help alone. No network: a fake Hyperliquid and a fake Telegram."""

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingos import TsAssistConvo, TsAssistJob
from app.security import admin_auth
from app.tradingos import assist, bot, market, signals
from app.tradingos.market import Candle

from tests.test_trading_os import FakeMD, kiwi, uptrend_pullback

TOKEN = "vx_" + "a1b2c3d4" * 5
KNOWN = {"BTC", "ETH", "SOL", "HYPE", "DOGE", "KPEPE", "PAXG"}


def scaled(cs: list[Candle], k: float) -> list[Candle]:
    return [Candle(c.t, c.o * k, c.h * k, c.l * k, c.c * k, c.v) for c in cs]


@pytest.fixture
def md(monkeypatch: pytest.MonkeyPatch) -> FakeMD:
    base = uptrend_pullback(400)  # ends near 145
    f = FakeMD({"BTC": scaled(base, 560), "ETH": scaled(base, 22), "SOL": base})
    monkeypatch.setattr(market, "_shared", f)
    return f


@pytest.fixture
def token() -> str:
    from app.security.credentials import secret_store

    secret_store().set("vortex_assist_token", TOKEN)
    return TOKEN


class TG:
    """TEST FIXTURE: a fake Telegram client that records sends and edits."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []

    async def send_message(self, chat_id: Any, text: str, buttons: Any = None, html: bool = False, reply_to: Any = None) -> dict[str, Any]:
        self.sent.append({"chat": chat_id, "text": text, "reply_to": reply_to})
        return {"message_id": 1000 + len(self.sent), "chat": {"id": chat_id, "type": "private"}}

    async def send_photo(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return {"message_id": 1}

    async def edit_message_text(self, chat_id: Any, message_id: int, text: str, html: bool = False) -> dict[str, Any]:
        self.edits.append({"chat": chat_id, "id": message_id, "text": text})
        return {"message_id": message_id}

    async def aclose(self) -> None:
        pass


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def jobs(db: Session) -> list[TsAssistJob]:
    db.expire_all()
    return list(db.scalars(select(TsAssistJob).order_by(TsAssistJob.id)))


def ask(db: Session, tg: TG, text: str, user: int = 42, chat: int = 42, msg: int = 7) -> None:
    run(bot.handle_command(db, tg, chat, user, text, group=chat < 0, reply_to=msg))


def online(db: Session) -> None:
    assist.mark_seen(db)
    db.commit()


# ---------------------------------------------------------------- the parser
@pytest.mark.parametrize("text,want", [
    ("i long btc at 81k", dict(side="long", coin="BTC", entry=81000)),
    ("short eth 3200 10x sl 3350 tp 3000", dict(side="short", coin="ETH", entry=3200, leverage=10, stop=3350, target=3000)),
    ("im in sol long from 140 with $500 5x", dict(side="long", coin="SOL", entry=140, leverage=5, margin_usd=500, size_usd=2500)),
    ("long $BTC 81,000 x20 stop 78k target 90k", dict(coin="BTC", entry=81000, leverage=20, stop=78000, target=90000)),
    ("bought eth @ 3.2k", dict(side="long", coin="ETH", entry=3200)),
    ("short btcusdt from 95000 sl 97k tp 90k 3x size $2000", dict(side="short", coin="BTC", entry=95000, leverage=3, size_usd=2000, margin_usd=None)),
    ("long doge", dict(side="long", coin="DOGE", entry=None)),
    ("I'm long Bitcoin from 80.5k, stop loss 78000", dict(side="long", coin="BTC", entry=80500, stop=78000)),
    ("sold hype at 40 lev 3 tp 35", dict(side="short", coin="HYPE", entry=40, leverage=3, target=35)),
    ("long gold 3300 2x", dict(coin="PAXG", entry=3300, leverage=2)),
    ("long pepe 0.0123 sl 0.011", dict(coin="KPEPE", entry=0.0123, stop=0.011)),
    ("btc long 81k 10x 1000 usd margin", dict(coin="BTC", entry=81000, leverage=10, margin_usd=1000, size_usd=10000)),
    ("long sol 150 $300 notional", dict(coin="SOL", entry=150, size_usd=300, margin_usd=None)),
    ("shorting ETH-PERP entry: 3300 sl: 3400", dict(side="short", coin="ETH", entry=3300, stop=3400)),
    ("long eth tp 4000 sl 3000", dict(coin="ETH", entry=None, stop=3000, target=4000)),
    ("BTC long at 1.2m", dict(coin="BTC", entry=1_200_000)),
])
def test_parser_reads_the_usual_phrasings(text: str, want: dict[str, Any]) -> None:
    tr = assist.parse(text, KNOWN)
    for k, v in want.items():
        assert getattr(tr, k) == (pytest.approx(v) if isinstance(v, float) else v), (text, k, tr)


@pytest.mark.parametrize("text,code,word", [("what do you think", "unparseable", ""), ("btc 81k", "unparseable", ""),
                                            ("long foo at 5", "unknown_coin", "FOO"), ("i short wagmicoin 3x", "unknown_coin", "WAGMICOIN"),
                                            ("", "unparseable", "")])
def test_parser_refuses_what_it_cant_read(text: str, code: str, word: str) -> None:
    with pytest.raises(assist.ParseError) as e:
        assist.parse(text, KNOWN)
    assert (e.value.code, e.value.word) == (code, word)


# ---------------------------------------------------------------- FACTS
def test_facts_maths_for_a_long(md: FakeMD) -> None:
    f = assist.build_facts(md, assist.Trade("long", "SOL", entry=100, leverage=10, size_usd=1000, stop=95, target=110))
    mmr = 1 / 40  # max leverage 20 in the fake: maintenance = half the initial margin at max leverage
    assert f["liq_price"] == pytest.approx(100 * (1 - (0.1 - mmr) / (1 - mmr)), rel=1e-5)
    assert f["rr"] == 2.0 and f["stop_distance_pct"] == 5.0 and f["risk_usd"] == 50.0
    price = md.market("SOL").mark
    assert f["pnl_pct"] == pytest.approx((price - 100) / 100 * 100, abs=0.01) and f["pnl_pct"] > 0
    assert f["liq_distance_pct"] == pytest.approx((price - f["liq_price"]) / price * 100, abs=0.01)
    assert f["entry_source"] == "user" and f["venue"] == "hyperliquid" and f["funding_8h_pct"] == pytest.approx(0.008)
    assert f["funding_daily_cost_usd"] == pytest.approx(1000 * 0.00001 * 24)  # longs pay positive funding
    assert set(f) >= {"coin", "side", "entry", "price", "leverage", "size_usd", "stop", "target", "liq_price", "atr_pct", "trend", "rsi",
                      "support", "resistance", "change_24h_pct", "suggested_stop", "suggested_stop_reason", "system_view", "risk_score",
                      "risk_flags", "as_of"}
    assert f["suggested_stop"] < f["entry"] and 1 <= f["risk_score"] <= 10


def test_facts_maths_for_a_short(md: FakeMD) -> None:
    price = md.market("SOL").mark
    f = assist.build_facts(md, assist.Trade("short", "SOL", entry=price * 1.1, leverage=5, stop=price * 1.2, target=price))
    assert f["pnl_pct"] > 0  # the price fell below the short's entry: a profit
    assert f["liq_price"] > f["entry"] and f["liq_distance_pct"] > 0
    assert f["rr"] == pytest.approx(0.1 / 0.1, abs=0.01)
    assert f["funding_daily_cost_usd"] is None and f["risk_usd"] is None  # no size given
    assert f["suggested_stop"] > f["entry"]


def test_missing_entry_is_the_market_price_and_k_shorthand_is_rescaled(md: FakeMD) -> None:
    f = assist.build_facts(md, assist.Trade("long", "BTC"))
    assert f["entry_source"] == "market" and f["entry"] == f["price"] and f["pnl_pct"] == 0
    g = assist.build_facts(md, assist.Trade("long", "BTC", entry=81, stop=78))  # "btc long 81 sl 78" means thousands
    assert g["entry"] == 81000 and g["stop"] == 78000


def test_risk_flags_follow_the_rules(md: FakeMD) -> None:
    calm = assist.build_facts(md, assist.Trade("long", "SOL", entry=md.market("SOL").mark, leverage=2, stop=md.market("SOL").mark * 0.95,
                                               target=md.market("SOL").mark * 1.15))
    wild = assist.build_facts(md, assist.Trade("long", "SOL", entry=md.market("SOL").mark, leverage=50))
    assert "no stop-loss" in wild["risk_flags"] and "high leverage vs volatility" in wild["risk_flags"]
    assert "leverage above the venue max (20x)" in wild["risk_flags"]
    assert "no stop-loss" not in calm["risk_flags"] and wild["risk_score"] > calm["risk_score"]
    f = {"side": "long", "entry": 100.0, "price": 101.0, "leverage": None, "atr_pct": 2.0, "stop": 90.0, "stop_distance_pct": 10.0,
         "liq_price": None, "liq_distance_pct": None, "trend": "down", "funding_8h_pct": 0.05, "support": [90.0], "resistance": [],
         "rsi": 75.0, "rr": 0.5, "system_view": {"agrees": False}, "max_leverage": 20}
    score, flags = assist.risk_of(f)
    assert {"against the trend", "funding against you", "entry far above support", "reward smaller than the risk",
            "the bot's own read is the other side"} <= set(flags) and any("overbought" in x for x in flags)
    assert score == 3 + 5 + 1 - 1  # five +1 rules, RSI +1, no leverage -1
    wrong = assist.risk_of({**f, "stop": 105.0, "stop_distance_pct": -5.0})[1]
    assert "stop on the wrong side of the entry" in wrong
    beyond = assist.risk_of({**f, "leverage": 10, "liq_price": 92.0, "liq_distance_pct": 9.0, "stop": 91.0})[1]
    assert "stop beyond liquidation" in beyond


def test_fallback_text_stands_on_its_own(md: FakeMD) -> None:
    f = assist.build_facts(md, assist.Trade("long", "BTC", entry=81000, leverage=10, margin_usd=500, size_usd=5000, stop=78000, target=90000))
    t = assist.render_fallback(f)
    for needle in ("Trade check · $BTC long", "Risk ", "/10", "liquidation ≈", "Stop 78,000.0", "R/R 1:", "lost at the stop", "Trend ",
                   "Safer", "Invalidation", "Not financial advice"):
        assert needle in t, needle
    assert "<script" not in t and t.count("<b>") == t.count("</b>")


# ---------------------------------------------------------------- the bot: fallback, placeholder, answer, expiry
def test_vortex_away_means_the_fallback_at_once(db: Session, md: FakeMD, token: str) -> None:
    tg = TG()
    ask(db, tg, "/help i long btc at 81k")
    assert len(tg.sent) == 1 and "Trade check · $BTC long" in tg.sent[0]["text"] and tg.sent[0]["reply_to"] == 7
    (j,) = jobs(db)
    assert j.status == "fallback" and j.kind == "assess" and j.message_id == 1001 and j.answer


def test_unparseable_and_unknown_coin_get_a_hint_and_no_job(db: Session, md: FakeMD, token: str) -> None:
    tg = TG()
    ask(db, tg, "/help what should i do")
    ask(db, tg, "/help long foo at 5")
    assert "Trade check" in tg.sent[0]["text"] and "/help long BTC" in tg.sent[0]["text"]
    assert "can't find FOO" in tg.sent[1]["text"] and jobs(db) == []


def test_help_alone_is_unchanged(db: Session, md: FakeMD, token: str) -> None:
    tg = TG()
    ask(db, tg, "/help")
    assert bot.HELP in tg.sent[0]["text"] and "Trade check" not in tg.sent[0]["text"] and jobs(db) == []


def test_switched_off_help_with_words_is_plain_help(db: Session, md: FakeMD) -> None:
    tg = TG()  # no token set: off by default
    assert assist.config(db)["enabled"] is False
    ask(db, tg, "/help long btc 81k")
    assert bot.HELP in tg.sent[0]["text"] and jobs(db) == []


def api(client: TestClient, path: str, body: dict[str, Any] | None = None, tok: str | None = TOKEN) -> Any:
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    return client.post(f"/api/vortex/{path}", json=body or {}, headers=headers)


@pytest.fixture
def tgfake(monkeypatch: pytest.MonkeyPatch) -> TG:
    from app.api.routes import vortex

    tg = TG()
    monkeypatch.setattr(vortex, "_tg", lambda runtime: tg)
    return tg


def test_placeholder_is_edited_into_vortexs_answer(client: TestClient, db: Session, md: FakeMD, token: str, tgfake: TG) -> None:
    online(db)
    tg = TG()
    ask(db, tg, "/help short eth 3200 10x sl 3350 tp 3000", chat=-100, msg=9)
    assert tg.sent[0]["text"] == assist.PLACEHOLDER and tg.sent[0]["reply_to"] == 9
    got = api(client, "claim", {"max": 3, "wait": 0}).json()["jobs"]
    assert len(got) == 1 and got[0]["kind"] == "assess" and got[0]["facts"]["coin"] == "ETH" and got[0]["history"] == []
    assert got[0]["question"] == "short eth 3200 10x sl 3350 tp 3000" and got[0]["lease_seconds"] == 45 and got[0]["lang_hint"] == "en"
    assert api(client, "claim", {"wait": 0}).json()["jobs"] == []  # leased
    r = api(client, f"jobs/{got[0]['id']}/answer", {"text": "Risk 7/10: tight liq.\n- stop is fine"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    (e,) = tgfake.edits
    assert e["id"] == 1001 and e["chat"] == -100 and "Risk 7/10" in e["text"] and e["text"].endswith(f"<i>{assist.FOOTER}</i>")
    (j,) = jobs(db)
    assert j.status == "answered" and j.answer.startswith("Risk 7/10")
    assert api(client, f"jobs/{got[0]['id']}/answer", {"text": "again"}).status_code == 409
    assert api(client, "jobs/j_nope/answer", {"text": "x"}).status_code == 404


def test_answer_text_must_fit(client: TestClient, db: Session, md: FakeMD, token: str, tgfake: TG) -> None:
    online(db)
    ask(db, TG(), "/help long btc")
    jid = jobs(db)[0].public_id
    assert api(client, f"jobs/{jid}/answer", {"text": "   "}).status_code == 400
    assert api(client, f"jobs/{jid}/answer", {"text": "x" * 1801}).status_code == 400
    assert api(client, f"jobs/{jid}/answer", {"text": "https://evil.example.com"}).status_code == 400  # nothing left once stripped


def test_expiry_turns_the_placeholder_into_the_fallback(db: Session, md: FakeMD, token: str) -> None:
    online(db)
    ask(db, TG(), "/help long btc at 81k 10x")
    tg = TG()
    assert run(assist.expire(db, tg, utcnow())) == 0  # not yet
    assert run(assist.expire(db, tg, utcnow() + timedelta(seconds=61))) == 1
    (j,) = jobs(db)
    assert j.status == "expired" and tg.edits[0]["text"] == j.fallback and "Trade check · $BTC long" in j.fallback


def test_late_answer_is_refused_and_the_fallback_goes_out(client: TestClient, db: Session, md: FakeMD, token: str, tgfake: TG) -> None:
    online(db)
    ask(db, TG(), "/help long btc at 81k")
    (j,) = jobs(db)
    j.created_at = utcnow() - timedelta(seconds=70)
    db.commit()
    r = api(client, f"jobs/{j.public_id}/answer", {"text": "Too late"})
    assert r.status_code == 409
    assert jobs(db)[0].status == "expired" and "Trade check" in tgfake.edits[0]["text"]
    assert api(client, "claim", {"wait": 0}).json()["jobs"] == []  # expired jobs are never handed out


def test_fail_sends_the_fallback_now(client: TestClient, db: Session, md: FakeMD, token: str, tgfake: TG) -> None:
    online(db)
    ask(db, TG(), "/help long sol 140")
    jid = jobs(db)[0].public_id
    assert api(client, f"jobs/{jid}/fail", {"reason": "llm down"}).json() == {"ok": True}
    assert jobs(db)[0].status == "failed" and "Trade check · $SOL long" in tgfake.edits[0]["text"]
    assert api(client, f"jobs/{jid}/fail", {"reason": "again"}).status_code == 409


def test_an_expired_lease_is_handed_out_again(db: Session, md: FakeMD, token: str) -> None:
    online(db)
    ask(db, TG(), "/help long btc")
    now = utcnow()
    assert len(assist.claim(db, 3, now)) == 1 and assist.claim(db, 3, now + timedelta(seconds=10)) == []
    again = assist.claim(db, 3, now + timedelta(seconds=46))
    assert len(again) == 1 and jobs(db)[0].claims == 2


# ---------------------------------------------------------------- sanitising
def test_sanitize_strips_markup_links_and_mentions() -> None:
    raw = ("<b>Risk 8/10</b> <a href='https://x.io'>click</a>\nSee https://evil.com/x, www.scam.io and t.me/joinme or "
           "pump.fun/coin, DM @scammer99\x07\n\n\n\n\nBye a < b & c")
    t = assist.sanitize(raw)
    for bad in ("<b>", "<a", "https", "www.", "t.me", "pump.fun", "@scammer99", "\x07", "\n\n\n"):
        assert bad not in t, bad
    assert t.startswith("Risk 8/10") and "[link removed]" in t and t.endswith("Bye a < b & c")
    h = assist.answer_html(t, {"coin": "BTC", "side": "long"})
    assert "a &lt; b &amp; c" in h and h.endswith("<i>Not financial advice · Vortex 🌀</i>")
    assert len(assist.sanitize("x" * 5000)) == assist.MAX_ANSWER


# ---------------------------------------------------------------- the worker API's auth
def test_worker_routes_need_the_bearer(client: TestClient, db: Session, token: str) -> None:
    assert admin_auth.is_public("POST", "/api/vortex/claim") and admin_auth.is_public("GET", "/api/vortex/health")
    assert not admin_auth.is_public("PUT", "/api/tradingos/admin/vortex-token")
    assert api(client, "claim", {"wait": 0}, tok=None).status_code == 401
    assert api(client, "claim", {"wait": 0}, tok="wrong" * 8).status_code == 401
    client.cookies.set(admin_auth.COOKIE, "an-owner-session")  # the admin login is no way in
    assert client.get("/api/vortex/health").status_code == 401
    client.cookies.clear()
    r = client.get("/api/vortex/health", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200 and r.json() == {"ok": True, "pending": 0}


def test_failed_tokens_are_rate_limited(client: TestClient, token: str) -> None:
    codes = [api(client, "claim", {"wait": 0}, tok="bad-token-guess").status_code for _ in range(8)]
    assert codes[:5] == [401] * 5 and codes[-1] == 429


def test_no_token_set_means_nobody_gets_in(client: TestClient) -> None:
    assert api(client, "claim", {"wait": 0}, tok="anything-at-all-here").status_code == 401


def test_claim_marks_vortex_seen(client: TestClient, db: Session, token: str) -> None:
    assert not assist.online(db)
    assert api(client, "claim", {"wait": 0}).json() == {"jobs": []}
    db.expire_all()
    assert assist.online(db) and assist.config(db)["last_seen"]


def test_claim_long_polls_without_a_session(client: TestClient, db: Session, md: FakeMD, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.routes import vortex

    looks: list[bool] = []
    real = vortex._claim_once
    monkeypatch.setattr(vortex, "POLL_EVERY", 0.05)

    def counting(n: int, seen: bool) -> list[dict[str, Any]]:
        looks.append(seen)
        return real(n, seen)
    monkeypatch.setattr(vortex, "_claim_once", counting)
    assert api(client, "claim", {"wait": 1}).json() == {"jobs": []}
    assert len(looks) > 5 and looks[0] is True and not any(looks[1:])


# ---------------------------------------------------------------- limits
def test_daily_limit_with_a_rank_boost(db: Session, md: FakeMD, token: str) -> None:
    tg = TG()
    for _ in range(3):
        ask(db, tg, "/help long btc", user=50, chat=50)
    ask(db, tg, "/help long btc", user=50, chat=50)
    assert "used today's 3 trade checks" in tg.sent[-1]["text"] and "Rank 2+" in tg.sent[-1]["text"] and len(jobs(db)) == 3
    kiwi(db, 2, "60")
    for _ in range(4):
        ask(db, tg, "/help long eth", user=60, chat=60)
    assert len(jobs(db)) == 7 and "used today" not in tg.sent[-1]["text"]
    s = signals.settings(db)
    s.strategies = {**s.strategies, "vortex_assist": {"daily_ranked": 4, "min_rank": 2}}
    db.commit()
    ask(db, tg, "/help long eth", user=60, chat=60)
    assert "used today's 4 trade checks" in tg.sent[-1]["text"] and "Rank 2+" not in tg.sent[-1]["text"]


# ---------------------------------------------------------------- follow-ups
def test_followups_carry_the_history_within_the_window_and_cap(db: Session, md: FakeMD, token: str) -> None:
    online(db)
    tg = TG()
    ask(db, tg, "/help long btc at 81k 10x", user=70, chat=-5, msg=11)
    (first,) = assist.claim(db, 3)
    run(assist.answer(db, tg, jobs(db)[0], "Risk 6/10. Liq is close."))
    answer_msg = jobs(db)[0].message_id
    assert assist.is_reply_to_check(db, -5, answer_msg) and not assist.is_reply_to_check(db, -5, 99999)
    assert run(assist.followup(db, tg, -5, 71, "and me?", 12, answer_msg)) == "ignored"  # someone else
    assert run(assist.followup(db, tg, -5, 70, "what if I use 5x?", 13, answer_msg)) == "queued"
    assert tg.sent[-1]["text"] == assist.THINKING and tg.sent[-1]["reply_to"] == 13
    (fu,) = assist.claim(db, 3)
    assert fu["kind"] == "followup" and fu["question"] == "what if I use 5x?"
    assert fu["history"] == [{"role": "user", "text": "long btc at 81k 10x"}, {"role": "vortex", "text": "Risk 6/10. Liq is close."}]
    convo = db.scalar(select(TsAssistConvo))
    convo.followups = assist.MAX_FOLLOWUPS
    db.commit()
    assert run(assist.followup(db, tg, -5, 70, "one more", 14, answer_msg)) == "cap" and "6 follow-ups" in tg.sent[-1]["text"]
    convo.followups, convo.created_at = 0, utcnow() - timedelta(minutes=31)
    db.commit()
    assert run(assist.followup(db, tg, -5, 70, "still there?", 15, answer_msg)) == "ignored"


def test_followup_history_is_capped_at_six_turns(db: Session, md: FakeMD, token: str) -> None:
    online(db)
    tg = TG()
    ask(db, tg, "/help long sol 140", user=80, chat=80)
    for i in range(4):
        last = jobs(db)[-1]
        assist.claim(db, 3)
        run(assist.answer(db, tg, last, f"answer {i}"))
        assert run(assist.followup(db, tg, 80, 80, f"question {i}", 100 + i, jobs(db)[-1].message_id)) == "queued"
    (fu,) = assist.claim(db, 3)
    assert len(fu["history"]) == assist.MAX_HISTORY and fu["history"][-1] == {"role": "vortex", "text": "answer 3"}


def test_followup_while_vortex_is_away_says_so(db: Session, md: FakeMD, token: str) -> None:
    tg = TG()
    ask(db, tg, "/help long btc", user=90, chat=90)  # answered by the fallback (Vortex unseen)
    assert run(assist.followup(db, tg, 90, 90, "and now?", 5, jobs(db)[0].message_id)) == "offline"
    assert "offline" in tg.sent[-1]["text"] and len(jobs(db)) == 1


# ---------------------------------------------------------------- owner settings
def test_owner_settings_and_write_only_token(client: TestClient, db: Session) -> None:
    ov = client.get("/api/tradingos/admin/overview").json()["vortex_assist"]
    assert ov["enabled"] is False and ov["token_set"] is False and ov["daily_public"] == 3 and ov["daily_ranked"] == 10
    assert client.put("/api/tradingos/admin/vortex-token", json={"token": "short"}).status_code == 422
    ov = client.put("/api/tradingos/admin/vortex-token", json={"token": TOKEN}).json()["vortex_assist"]
    assert ov["token_set"] is True and ov["enabled"] is True and TOKEN not in str(ov)
    ov = client.put("/api/tradingos/admin/settings", json={"vortex_on": False, "vortex_daily_public": 5, "vortex_min_rank": 3}).json()["vortex_assist"]
    assert (ov["enabled"], ov["daily_public"], ov["min_rank"]) == (False, 5, 3)
    ov = client.delete("/api/tradingos/admin/vortex-token").json()["vortex_assist"]
    assert ov["token_set"] is False


async def test_ingestion_routes_only_replies_to_check_messages(db: Session, fake_telegram: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.telegram.client import TelegramBotClient
    from app.telegram.ingestion import TelegramIngestor
    from app.telegram.state import TelegramRuntime
    from tests.fakes import TOKEN as BOT_TOKEN

    convo = TsAssistConvo(chat_id=-100222, tg_user_id=42, coin="BTC", side="long", trade={}, facts={}, followups=0, created_at=utcnow())
    db.add(convo)
    db.flush()
    db.add(TsAssistJob(public_id="j_test", convo_id=convo.id, kind="assess", status="answered", tg_user_id=42, chat_id=-100222,
                       message_id=500, question="long btc", facts={}, fallback="", claims=0, created_at=utcnow()))
    db.commit()
    seen: list[tuple[Any, ...]] = []

    async def record(db_: Any, client: Any, *args: Any) -> str:
        seen.append(args)
        return "queued"
    monkeypatch.setattr(assist, "followup", record)
    fake_telegram.message(-100222, "what about 5x?", user_id=42, reply_to=500)
    fake_telegram.message(-100222, "unrelated reply", user_id=42, reply_to=499)
    fake_telegram.message(-100222, "plain chat", user_id=42)
    await TelegramIngestor(db, TelegramBotClient(BOT_TOKEN, "https://api.telegram.test", transport=fake_telegram.transport),
                           TelegramRuntime()).sync()
    assert seen == [(-100222, 42, "what about 5x?", 1, 500)]
