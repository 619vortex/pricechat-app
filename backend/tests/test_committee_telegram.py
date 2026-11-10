"""Kiwi AI Investment Committee in Telegram (no network, no real bot): /committee, /debate, /research, /risk <coin> and
/committee status; the placeholder edited into the answer by the watcher; the compact summary (DOGE WAIT with both
scenarios and the engine's reasons, a missing-news note, the OKX informational label); the buttons (each view, Back, Full
Report privately in the main chat, Chart through /chart, Refresh cached vs stale and the daily limit); message lengths;
HTML escaping of model text; group vs private routing; and the deterministic writers' text on real evidence.

TEST FIXTURES: tests/committee_live/*_evidence.json are evidence packs from a live smoke run (rules backend, 2026-10-23,
trimmed: 4 headlines, short summaries), run here through the real committee pipeline on the rules backend."""

import asyncio
import copy
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.committee import grounding, orchestrator, roles
from app.committee import telegram_view as tv
from app.committee.llm import Backend
from app.config.settings import get_settings
from app.models.committee import CmRun
from app.models.tradingos import TsSettings
from app.telegram.errors import TelegramRequestError
from app.tradingos import bot, committeecmd, venues
from tests.committee_fixtures import FakeLLM, no_news
from tests.test_trading_os import FakeTG

LIVE = Path(__file__).parent / "committee_live"
GROUP, MAIN, USER, OTHER = -1001, -1002, 501, 502


def live(name: str) -> dict[str, Any]:
    return json.loads((LIVE / f"{name}_evidence.json").read_text(encoding="utf-8"))


PACKS = {"BTC": "btc", "DOGE": "doge", "TAO": "tao", "TAO OKX": "tao"}


def run_of(db: Session, ev: dict[str, Any], backend: Backend | None = None) -> dict[str, Any]:
    r = asyncio.run(orchestrator.run_committee(db, ev["asset"]["symbol"], ev["timeframe"], requested_by={"admin": True}, evidence=ev,
                                               backend=backend or Backend(), use_cache=False))
    return r.result


class TG(FakeTG):
    """TEST FIXTURE: the tradingos FakeTG plus edits, inline buttons, reply ids and refused private messages."""

    def __init__(self, fail_dm: bool = False, fail_edit: bool = False) -> None:
        super().__init__()
        self.n, self.fail_dm, self.fail_edit = 0, fail_dm, fail_edit

    async def send_message(self, chat_id: Any, text: str, buttons: Any = None, html: bool = False, reply_to: Any = None) -> dict[str, Any]:
        if self.fail_dm and isinstance(chat_id, int) and chat_id > 0:
            raise TelegramRequestError("Forbidden: bot can't initiate conversation with a user")
        self.n += 1
        self.sent.append({"kind": "send", "chat": chat_id, "text": text, "buttons": buttons, "html": html, "reply_to": reply_to, "id": self.n})
        return {"message_id": self.n, "chat": {"id": chat_id, "type": "supergroup" if isinstance(chat_id, int) and chat_id < 0 else "private"}}

    async def edit_message_text(self, chat_id: Any, message_id: int, text: str, html: bool = False, buttons: Any = None) -> dict[str, Any]:
        if self.fail_edit:
            raise TelegramRequestError("Bad Request: message to edit not found")
        self.sent.append({"kind": "edit", "chat": chat_id, "id": message_id, "text": text, "buttons": buttons, "html": html})
        return {"message_id": message_id}

    async def answer_callback(self, cid: str, text: str, alert: bool = False) -> None:
        self.sent.append({"kind": "answer", "text": text, "alert": alert})

    async def send_photo(self, chat_id: Any, png: bytes, caption: str, buttons: Any = None, reply_to: Any = None, html: bool = False) -> dict[str, Any]:
        self.n += 1
        self.sent.append({"kind": "photo", "chat": chat_id, "text": caption, "reply_to": reply_to, "id": self.n})
        return {"message_id": self.n}

    async def aclose(self) -> None:
        return None

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.sent if m["kind"] == kind]


@pytest.fixture
def tg(monkeypatch: pytest.MonkeyPatch) -> TG:
    t = TG()
    monkeypatch.setattr(committeecmd, "CLIENT_FACTORY", lambda: t)
    monkeypatch.setattr(committeecmd, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(committeecmd, "RUNTIME", None)
    return t


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Rules backend, the live evidence packs by symbol, and a market resolver without network."""
    s = get_settings()
    monkeypatch.setattr(s, "committee_backend", "rules")
    asked: list[str] = []

    async def evidence(db: Any, symbol: str, timeframe: str, as_of: Any, mode: str) -> dict[str, Any]:
        asked.append(f"{symbol} {timeframe}")
        if symbol not in PACKS:
            raise orchestrator.CommitteeError("unknown_symbol", f"'{symbol}' isn't a market Price Chat can read.")
        return live(PACKS[symbol])

    async def resolve(words: list[str]) -> Any:
        w = " ".join(words).upper().lstrip("$")
        if w == "NOPE":
            raise venues.NotFound("NOPE isn't listed on Hyperliquid, Binance, Bybit or OKX.")
        if w == "PEPE":
            opt = SimpleNamespace(pool_name="PEPE/SOL", base="PEPE", label="DEX · Solana", liquidity=1.2e6, token_address="So1aNaAddr123", symbol="PEPE")
            raise venues.Ambiguous("PEPE matches 2 tokens: which one?", [opt, SimpleNamespace(**{**opt.__dict__, "token_address": "0xEthAddr"})])
        return SimpleNamespace(tag=w.split()[0])

    monkeypatch.setattr(orchestrator, "_evidence", evidence)
    monkeypatch.setattr(committeecmd, "resolve", resolve)
    return asked


def say(db: Session, tg: TG, text: str, chat: int = GROUP, user: int = USER, reply_to: int | None = 77, username: str | None = None) -> None:
    async def go() -> None:
        await bot.handle_command(db, tg, chat, user, text, group=chat < 0, reply_to=reply_to, chat_username=username)
        await committeecmd.settle()
        for rid in list(orchestrator._TASKS):
            await orchestrator.wait(rid)
        await committeecmd.settle()

    asyncio.run(go())
    db.expire_all()


def press(db: Session, tg: TG, data: str, chat: int = GROUP, mid: int = 1, user: int = OTHER, username: str | None = None) -> None:
    kind = "supergroup" if chat < 0 else "private"
    cq = {"id": "cb1", "data": data, "from": {"id": user}, "message": {"message_id": mid, "chat": {"id": chat, "type": kind, "username": username}}}

    async def go() -> None:
        await bot.handle_callback(db, tg, cq)
        await committeecmd.settle()
        for rid in list(orchestrator._TASKS):
            await orchestrator.wait(rid)
        await committeecmd.settle()

    asyncio.run(go())
    db.expire_all()


def ids_in(buttons: list[list[dict[str, str]]]) -> list[str]:
    return [b["callback_data"] for row in buttons for b in row if "callback_data" in b]


# ---------------------------------------------------------------- parsing
@pytest.mark.parametrize("args,want", [(["doge", "4h"], (["doge"], "4h")), (["DOGE", "4H"], (["DOGE"], "4h")), (["BTC"], (["BTC"], "1h")),
                                       (["4h", "$sol"], (["$sol"], "4h")), (["TAO", "okx", "15m"], (["TAO", "okx"], "15m")),
                                       (["eth", "1D"], (["eth"], "1d"))])
def test_parse_accepts_coin_and_timeframe_in_any_case(args: list[str], want: tuple[list[str], str]) -> None:
    words, tf, err = committeecmd.parse(args)
    assert err is None and (words, tf) == want


@pytest.mark.parametrize("args,needle", [(["BTC", "5m"], "isn't supported"), (["BTC", "1h", "4h"], "One timeframe"), (["4h"], "Usage"),
                                         (["%%%"], "can't read")])
def test_parse_refuses_other_timeframes_and_junk(args: list[str], needle: str) -> None:
    _, _, err = committeecmd.parse(args)
    assert err and needle in err


def test_committee_without_arguments_is_the_usage(db: Session, tg: TG, world: list[str]) -> None:
    say(db, tg, "/committee")
    assert "Usage: /committee BTC" in tg.of("send")[0]["text"] and world == []


# ---------------------------------------------------------------- placeholder → edited summary
def test_committee_doge_placeholder_is_edited_into_the_compact_summary(db: Session, tg: TG, world: list[str]) -> None:
    say(db, tg, "/committee doge 4h")
    assert world == ["DOGE 4h"]
    ph = tg.of("send")[0]
    assert ph["chat"] == GROUP and ph["reply_to"] == 77 and "Kiwi Committee is meeting on <b>DOGE 4h</b>" in ph["text"]
    assert "@botpricechat" not in ph["text"]  # the footer only goes under the finished answer
    ed = tg.of("edit")
    assert len(ed) == 1 and ed[0]["id"] == ph["id"] and ed[0]["chat"] == GROUP
    text = ed[0]["text"]
    body = text.split(bot.CHANNEL_FOOTER)[0]
    assert len(body) <= 1300 and len(text) <= 4096 and text.endswith(bot.footer(db))
    assert body.startswith("🥝 <b>KIWI AI INVESTMENT COMMITTEE</b> · <b>DOGE 4H</b> · Hyperliquid perp")
    assert "Price 0.07948 · 03:36 UTC" in body
    assert "Technical: Bearish · Fundamental: Neutral · News: Neutral · Sentiment: Neutral" in body
    assert "<b>Verdict: WAIT</b> (Underweight)" in body
    assert "Long if: 4h close &gt; 0.08011 · stop 0.07823 · T 0.08231 / 0.08379" in body  # 0.083795 at 4 significant digits
    assert "Short if: 4h close &lt; 0.07932 · stop 0.08264 · T 0.07142 / 0.06954" in body
    why = next(x for x in body.split("\n") if x.startswith("No trade approved because"))
    # a reason both scenarios share is said once; the verdict line already says no trade was proposed
    assert why == ("No trade approved because: Both setups blocked: 24h volume $13M below the $50M minimum · "
                   "long only: not triggered (price 0.07948 outside its entry zone)")
    assert len(why) <= tv.WHY_MAX and "no trade proposed" not in body and body.count("24h volume $13M") == 1
    assert "Risk: thin liquidity: 24h volume $12.76M" in body
    assert "Bull: Thin bull case" in body and "Bear: 1 of 4 sections leans bearish (technical)" in body
    assert "Research only — nothing was traded. Not financial advice." in body
    for verbose in ("pivot high", "kept from", "most touches", "x ATR14(4h)"):
        assert verbose not in body  # the scenarios' rule strings belong to the full report
    assert not any(w in body.lower() for w in ("probability", "chance", "odds"))
    row = db.query(CmRun).one()
    btns = ed[0]["buttons"]
    assert ids_in(btns) == [f"cm:{row.public_id}:{v}" for v in ("full", "bull", "bear", "risk", "chart", "refresh")]
    assert btns[-1] == [{"text": "🌐 Web report", "url": f"{committeecmd.report_url(row.public_id)}"}]
    assert committeecmd.report_url(row.public_id).endswith(f"/earn/committee?run={row.public_id}")
    assert (row.chat_id, row.message_id) == (GROUP, ph["id"])  # the placeholder is on the row (recovery edits it)
    assert all(len(d.encode()) <= 64 for d in ids_in(btns))
    assert row.requested_by_tg == USER and orchestrator.used_today(db, {"tg_user_id": USER}) == 1


def test_a_cached_run_is_answered_at_once_and_not_counted(db: Session, tg: TG, world: list[str]) -> None:
    say(db, tg, "/committee DOGE 4H")
    tg.sent.clear()
    say(db, tg, "/committee doge 4h", user=OTHER)
    assert world == ["DOGE 4h"]  # no second run
    sent = tg.of("send")
    assert len(sent) == 1 and not tg.of("edit") and "<b>Verdict: WAIT</b>" in sent[0]["text"] and sent[0]["reply_to"] == 77
    assert orchestrator.used_today(db, {"tg_user_id": OTHER}) == 0 and db.query(CmRun).count() == 1


def test_debate_research_and_risk_render_their_views(db: Session, tg: TG, world: list[str]) -> None:
    say(db, tg, "/debate btc")
    d = tg.of("edit")[-1]
    assert "BULL vs BEAR" in d["text"] and "🐂 <b>Bull:</b>" in d["text"] and "🐻 <b>Bear:</b>" in d["text"] and "Research manager: Hold" in d["text"]
    assert ids_in(d["buttons"])[0].endswith(":sum")  # Back
    tg.sent.clear()
    say(db, tg, "/research btc")  # the cached run: answered at once, one or two messages
    parts = tg.of("send")
    assert 1 <= len(parts) <= 2 and "FULL REPORT" in parts[0]["text"] and all(len(p["text"]) <= 4096 for p in parts)
    assert "pivot" in "".join(p["text"] for p in parts)  # the rule strings live here
    tg.sent.clear()
    say(db, tg, "/risk BTC")
    r = tg.of("send")[0]["text"]
    assert "RISK REVIEW" in r and "Kiwi risk engine: NOT APPLICABLE" in r and "Conservative (support)" in r and "Passed" in r
    assert world == ["BTC 1h"]


def test_failed_run_edits_the_placeholder_into_the_reason(db: Session, tg: TG, world: list[str]) -> None:
    say(db, tg, "/committee ZZZ")
    ed = tg.of("edit")
    assert len(ed) == 1 and "couldn't finish <b>ZZZ 1h</b>" in ed[0]["text"] and "isn&#x27;t" not in ed[0]["text"]
    assert "Usage: /committee BTC" in ed[0]["text"] and ed[0]["buttons"] is None


def test_unknown_coin_and_ambiguous_dex_ticker_answer_before_any_run(db: Session, tg: TG, world: list[str]) -> None:
    say(db, tg, "/committee nope")
    t = tg.of("send")[0]["text"]
    assert "NOPE isn't listed" in t and "Usage: /committee BTC" in t
    say(db, tg, "/committee pepe")
    t = tg.of("send")[1]["text"]
    assert "PEPE matches 2 tokens" in t and "<code>So1aNaAddr123</code>" in t and "<code>0xEthAddr</code>" in t
    assert "/committee So1aNaAddr123 1h" in t
    assert world == [] and db.query(CmRun).count() == 0 and orchestrator.used_today(db, {"tg_user_id": USER}) == 0


def test_daily_limit_is_answered_with_the_reset_time(db: Session, tg: TG, world: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "committee_daily_limit", 1)
    say(db, tg, "/committee btc")
    say(db, tg, "/committee doge 4h")
    t = tg.of("send")[-1]["text"]
    assert "Daily limit reached (1 committee runs a day)" in t and "00:00 UTC" in t and "/passport" in t
    assert world == ["BTC 1h"]


def test_rate_limit_per_telegram_user(db: Session, tg: TG, world: list[str]) -> None:
    from app.security.rate_limit import LIMITS

    for _ in range(int(LIMITS["committee"].burst)):
        assert LIMITS["committee"].hit(f"tg:{USER}") is None
    say(db, tg, "/committee btc")
    assert "Try again in" in tg.of("send")[0]["text"] and world == []


def test_watcher_sends_anew_when_the_placeholder_is_gone(db: Session, tg: TG, world: list[str]) -> None:
    tg.fail_edit = True  # e.g. the main-chat tidy-up deleted it
    say(db, tg, "/committee btc")
    sends = tg.of("send")
    assert len(sends) == 2 and "Verdict: WAIT" in sends[1]["text"] and sends[1]["reply_to"] == 77 and sends[1]["buttons"]


# ---------------------------------------------------------------- the compact view
def test_missing_news_is_reported_missing(db: Session) -> None:
    ev = live("btc")
    ev["sections"]["news"] = no_news()["sections"]["news"]
    text = tv.compact(run_of(db, ev))
    assert "News: missing" in text and "data missing: news" in text and len(text) <= 1300


def test_okx_market_is_labelled_informational(db: Session) -> None:
    text = tv.compact(run_of(db, live("tao")))
    assert "· <b>TAO 1H</b> · OKX spot" in text
    assert "Market: OKX spot TAO-USDT · informational only, no execution" in text
    assert "No trade approved because" not in text  # the Market line already says it: said once, not twice
    assert "News: no signal" in text  # the feeds answered, nothing about TAO: not "missing"
    assert "Price 261.8 · 03:36 UTC" in text and "Long if: 1h close &gt; 261.9 · stop 259 · T 266.4 / 269.5" in text


def test_model_text_is_html_escaped(db: Session) -> None:
    llm_ = FakeLLM({"bull": {"thesis": "Bulls <b>pump</b> & <script>x</script> it.", "points": [{"text": "a <i>b</i>", "refs": ["T1"]}]},
                    "risk panel": {"aggressive": {"stance": "support", "concerns": ["<a href='x'>link</a>"]},
                                   "neutral": {"stance": "support", "concerns": []},
                                   "conservative": {"stance": "reduce", "concerns": ["size <small> & tight"]}, "summary": "ok"}})
    res = run_of(db, live("doge"), llm_)
    assert res["debate"]["bull"]["author"].startswith("llm:")
    for text in (tv.compact(res), tv.side_view(res, "bull"), tv.risk_view(res), tv.debate_view(res), *tv.full_report(res)):
        assert "<script>" not in text and "<b>pump</b>" not in text and "<a href" not in text and "<small>" not in text
    assert "Bulls &lt;b&gt;pump&lt;/b&gt; &amp; &lt;script&gt;x&lt;/script&gt; it." in tv.compact(res)
    assert "size &lt;small&gt; &amp; tight" in tv.compact(res)


def test_message_length_limits_hold_even_for_huge_texts(db: Session) -> None:
    runs = [run_of(db, live(n)) for n in ("btc", "doge", "tao")]
    big = copy.deepcopy(runs[1])
    long = "word " * 2000
    big["debate"]["bull"]["thesis"] = big["debate"]["bear"]["thesis"] = long
    big["debate"]["bull"]["points"] = [{"text": long, "refs": ["T1"]}] * 10
    big["verdict"]["summary"] = long
    big["execution"]["reasons"] = [long] * 10
    for a in big["analysts"].values():
        a["summary"] = long
    big["risk_committee"]["conservative"]["concerns"] = [long] * 3
    for r in runs + [big]:
        assert len(tv.compact(r)) <= 1300
        for name in ("bull", "bear", "debate", "risk", "sum"):
            assert len(tv.view(r, name)) <= 4096
        parts = tv.full_report(r)
        assert 1 <= len(parts) <= 2 and all(len(p) <= 4096 for p in parts)


def test_prices_are_formatted_by_magnitude() -> None:
    assert [tv.px(v) for v in (81851.5, 81992.0, 261.8, 2.7934, 0.079475, 0.0000123456, 123456.0)] == [
        "81,851.5", "81,992", "261.8", "2.793", "0.07948", "0.00001235", "123,456"]


# ---------------------------------------------------------------- buttons
def _first_run(db: Session, tg: TG, text: str = "/committee doge 4h", chat: int = GROUP) -> tuple[str, int]:
    say(db, tg, text, chat=chat, reply_to=None if chat > 0 else 77)
    row = db.query(CmRun).order_by(CmRun.id.desc()).first()
    return row.public_id, tg.of("send")[0]["id"]


def test_each_view_edits_the_same_message_with_a_back_button(db: Session, tg: TG, world: list[str]) -> None:
    rid, mid = _first_run(db, tg, chat=USER)  # a private chat: the buttons switch the message in place
    for view, needle in (("bull", "BULL CASE"), ("bear", "BEAR CASE"), ("risk", "RISK REVIEW"), ("sum", "KIWI AI INVESTMENT COMMITTEE")):
        tg.sent.clear()
        press(db, tg, f"cm:{rid}:{view}", chat=USER, mid=mid, user=USER)
        ed = tg.of("edit")
        assert len(ed) == 1 and ed[0]["id"] == mid and ed[0]["chat"] == USER and needle in ed[0]["text"] and len(ed[0]["text"]) <= 4096
        assert not tg.of("send")
        assert tg.of("answer")[0]["text"] == ""
        data = ids_in(ed[0]["buttons"])
        assert (data[0] == f"cm:{rid}:sum") == (view != "sum")  # Back on every view, not on the summary
    tg.sent.clear()
    press(db, tg, f"cm:{rid}:full", chat=USER, mid=mid, user=USER)  # private: the report replaces the message
    ed, more = tg.of("edit"), tg.of("send")
    assert "FULL REPORT" in ed[0]["text"] and len(more) <= 1 and all(len(m["text"]) <= 4096 for m in ed + more)


def test_full_report_in_the_main_chat_goes_privately(db: Session, tg: TG, world: list[str]) -> None:
    db.add(TsSettings(main_chat=str(MAIN)))
    db.commit()
    say(db, tg, "/research doge 4h", chat=MAIN)
    edits = tg.of("edit")
    assert "KIWI AI INVESTMENT COMMITTEE" in edits[0]["text"] and "FULL REPORT" not in edits[0]["text"]  # the chat gets the summary
    dms = [m for m in tg.of("send") if m["chat"] == USER]
    assert dms and "FULL REPORT" in dms[0]["text"]
    rid = db.query(CmRun).one().public_id
    tg.sent.clear()
    press(db, tg, f"cm:{rid}:full", chat=MAIN, mid=edits[0]["id"])
    assert [m["chat"] for m in tg.of("send")][0] == OTHER and tg.of("answer")[0]["text"] == "Full Report sent to you privately."
    assert not tg.of("edit")
    tg.sent.clear()
    tg.fail_dm = True
    press(db, tg, f"cm:{rid}:full", chat=MAIN, mid=edits[0]["id"])
    ans = tg.of("answer")[0]
    assert "press Start" in ans["text"] and ans["alert"] is True


def test_chart_button_reuses_the_chart_command(db: Session, tg: TG, world: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Any, list[str], Any]] = []

    async def chart(db_: Any, client: Any, chat_id: Any, md: Any, args: list[str], reply_to: Any, note: Any = None) -> None:
        seen.append((chat_id, args, reply_to))
        return None

    monkeypatch.setattr(bot, "_chart", chart)
    monkeypatch.setattr("app.tradingos.market.data", lambda: None)
    rid, mid = _first_run(db, tg)
    press(db, tg, f"cm:{rid}:chart", mid=mid)
    assert seen == [(GROUP, ["DOGE", "4h"], mid)] and tg.of("answer")[0]["text"] == "Drawing the chart…"
    tg.sent.clear()
    say(db, tg, "/committee TAO okx")
    rid = db.query(CmRun).order_by(CmRun.id.desc()).first().public_id
    press(db, tg, f"cm:{rid}:chart", mid=5)
    assert seen[-1] == (GROUP, ["TAO", "okx", "spot", "1h"], 5)


def test_refresh_fresh_run_is_not_rerun(db: Session, tg: TG, world: list[str]) -> None:
    rid, mid = _first_run(db, tg)
    tg.sent.clear()
    press(db, tg, f"cm:{rid}:refresh", mid=mid)
    ans = tg.of("answer")[0]["text"]
    assert ans.startswith("Fresh: updated ") and " UTC" in ans and world == ["DOGE 4h"] and not tg.of("edit")


def test_refresh_a_stale_run_starts_a_new_one_counted_for_the_presser(db: Session, tg: TG, world: list[str]) -> None:
    rid, mid = _first_run(db, tg)
    row = db.query(CmRun).one()
    row.created_at = row.created_at - timedelta(minutes=20)
    db.commit()
    tg.sent.clear()
    press(db, tg, f"cm:{rid}:refresh", mid=mid, user=OTHER)
    assert world == ["DOGE 4h", "DOGE 4h"] and db.query(CmRun).count() == 2
    new = db.query(CmRun).order_by(CmRun.id.desc()).first()
    assert new.public_id != rid and new.requested_by_tg == OTHER
    edits = tg.of("edit")
    assert "meeting on <b>DOGE 4h</b>" in edits[0]["text"] and edits[0]["id"] == mid  # the same message, placeholder first
    assert "Verdict: WAIT" in edits[-1]["text"] and ids_in(edits[-1]["buttons"])[0] == f"cm:{new.public_id}:full"
    assert orchestrator.used_today(db, {"tg_user_id": OTHER}) == 1


def test_refresh_respects_the_daily_limit(db: Session, tg: TG, world: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    rid, mid = _first_run(db, tg)
    row = db.query(CmRun).one()
    row.created_at = row.created_at - timedelta(minutes=20)
    db.commit()
    monkeypatch.setattr(get_settings(), "committee_daily_limit", 1)
    tg.sent.clear()
    press(db, tg, f"cm:{rid}:refresh", mid=mid, user=USER)  # USER already used today's one run
    ans = tg.of("answer")[0]
    assert "Daily limit reached" in ans["text"] and ans["alert"] is True and db.query(CmRun).count() == 1


def test_unknown_run_or_button(db: Session, tg: TG, world: list[str]) -> None:
    press(db, tg, "cm:cm_doesnotexist:full")
    assert tg.of("answer")[0]["text"] == "This committee run isn't available any more."


# ---------------------------------------------------------------- routing and status
def test_group_and_private_routing(db: Session, tg: TG, world: list[str]) -> None:
    say(db, tg, "/connect")  # wallet linking stays private in groups (trading commands answer there since 2026-11-10)
    assert "Account commands work privately" in tg.of("send")[0]["text"] and world == []
    tg.sent.clear()
    say(db, tg, "/risk BTC")  # the committee's risk review is public
    assert "meeting on <b>BTC 1h</b>" in tg.of("send")[0]["text"] and "RISK REVIEW" in tg.of("edit")[0]["text"]
    tg.sent.clear()
    say(db, tg, "/committee btc", chat=USER, reply_to=None)  # private chats too (cached)
    assert tg.of("send")[0]["chat"] == USER and "Verdict: WAIT" in tg.of("send")[0]["text"]
    tg.sent.clear()
    say(db, tg, "/risk", chat=USER, reply_to=None)  # private /risk alone: the trading account's limits, as before
    assert "Link your Kiwi Passport first" in tg.of("send")[0]["text"]


def test_committee_status_reuses_the_status_route(db: Session, tg: TG, world: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from app.committee import evidence

    async def providers(http: Any = None) -> dict[str, Any]:
        return {"hyperliquid": {"ok": True, "age_s": 12.0, "detail": "ok"}, "news": {"ok": False, "age_s": None, "detail": "timeout"}}

    monkeypatch.setattr(evidence, "provider_status", providers)
    say(db, tg, "/committee status")
    t = tg.of("send")[0]["text"]
    assert "Kiwi Committee status" in t and "backend now: <b>rules</b>" in t and "✅ rules" in t and "❌ vortex" in t
    assert "✅ hyperliquid 12 s" in t and "❌ news never" in t and "Budget today: 0/300 model calls" in t
    assert "Your runs today: 0 of 3" in t and world == []


def test_help_and_menus_list_the_new_commands() -> None:
    for c in ("/committee BTC 4h", "/debate ETH", "/research SOL", "/risk BTC", "/committee status"):
        assert c in bot.HELP
    for c in ("/signal DOGE", "/chart BTC 1h", "/vpn", "/alert BTC 85000"):  # nothing removed
        assert c in bot.HELP
    names = [c for c, _ in bot.MENU_PRIVATE]
    assert {"committee", "debate", "research", "risk", "signal", "vpn", "help"} <= set(names)
    assert {"committee", "debate", "research", "risk", "signal", "help"} <= {c for c, _ in bot.MENU_GROUP}
    assert all(len(d) <= 256 for _, d in bot.MENU_PRIVATE + bot.MENU_GROUP)


# ---------------------------------------------------------------- the rules writers on real evidence
def test_rules_writers_state_the_decisive_facts_with_numbers(db: Session) -> None:
    doge = run_of(db, live("doge"))
    t = doge["analysts"]["technical"]["summary"]
    assert t.startswith("Bearish (score -2 of ±2): 4h trend down") and "RSI 40.1" in t and "MACD histogram -0.0003062 (below zero)" in t
    assert "above support 0.079318" in t and "under resistance 0.080107" in t and "Rule signal WAIT (24h volume $13M below $50M)" in t
    f = doge["analysts"]["fundamental"]["summary"]
    assert "#12 by market cap ($12.43B)" in f and "24h volume $12.76M, open interest $88.93M on Hyperliquid (max leverage 10x)" in f
    assert "2 headlines about DOGE in 48 h" in doge["analysts"]["news"]["summary"]
    s = doge["analysts"]["sentiment"]["summary"]
    assert "funding +0.0003096%/h (longs pay)" in s and "Fear & Greed 50 (Neutral), 63 a week ago" in s and "Section partial" in s
    bull, bear = doge["debate"]["bull"], doge["debate"]["bear"]
    assert bull["thesis"].startswith("Thin bull case: no section leans bullish; its best facts: 4h structure HH/HL")
    assert "The long setup needs a 4h close above 0.080107 (0.8% away), stop 0.0782308 (2.3%, 1.4 x ATR14), T1 0.082313 (R/R 1.2)." in bull["thesis"]
    assert bear["thesis"].startswith("1 of 4 sections leans bearish (technical); best facts: 4h trend down; 4h MACD histogram -0.0003062")
    assert bull["points"] and bear["points"] and bull["rebuttal"].startswith("The bear case rests on technical; against it:")
    rk = doge["risk_committee"]
    assert rk["conservative"]["concerns"][0] == "thin liquidity: 24h volume $12.76M, slippage risk"
    assert any("short stop 0.0826422 is 4.2% from the trigger (2.5 x ATR14 4h)" in c for c in rk["aggressive"]["concerns"])
    assert any("ATR14 1.67% of price per 4h candle, 5.32% a day" in c for c in rk["neutral"]["concerns"])
    assert doge["trader"]["rationale"].endswith("wait for a 4h close below 0.079318.")
    btc = run_of(db, live("btc"))
    assert "tight" in btc["risk_committee"]["conservative"]["concerns"][0]
    assert "Watch: long on a 1h close above 81,992 or short on a 1h close below 81,657." in btc["trader"]["rationale"]


def test_rules_text_is_grounded_and_has_no_probabilities(db: Session) -> None:
    """Every number the rules writers print is the evidence's (or a labelled derivation the grounder knows)."""
    for name in ("btc", "doge", "tao"):
        ev = live(name)
        res = run_of(db, ev)
        g = grounding.Grounder(ev, roles.scenarios_view(ev), roles.plan_from(ev, "LONG"), roles.plan_from(ev, "SHORT"), roles.derived(ev))
        texts = {**{f"{k} analyst": v for k, v in res["analysts"].items()}, "bull": res["debate"]["bull"], "bear": res["debate"]["bear"],
                 "manager": res["debate"]["manager"], "trader": {"rationale": res["trader"]["rationale"]}, "risk": res["risk_committee"],
                 "verdict": {k: res["verdict"][k] for k in ("summary", "what_changes_view")}}
        for role, out in texts.items():
            assert grounding.check(role, out, g) is None, (name, role)


# ---------------------------------------------------------------- follow-ups: address case, market cache key, recovery, single full report, prompts
def test_contract_addresses_keep_their_case() -> None:
    sol = "So11111111111111111111111111111111111111112"
    assert orchestrator._norm(f"${sol} 1h".replace(" 1h", ""), "1h") == (sol, "1h")
    assert orchestrator._norm("0xAbCdEf0123456789aBcDeF0123456789AbCdEf01", "4h")[0] == "0xAbCdEf0123456789aBcDeF0123456789AbCdEf01"
    assert orchestrator._norm("$tao okx", "1h") == ("TAO OKX", "1h") and orchestrator._norm("btcusdt", "1h")[0] == "BTCUSDT"


def test_cache_is_keyed_by_the_resolved_market(db: Session, tg: TG, world: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    markets = {"TAO": ("okx", "spot", "TAO-USDT"), "TAO OKX": ("okx", "spot", "TAO-USDT"), "TAO HL": ("hyperliquid", "perp", "TAO")}

    async def resolve(words: list[str]) -> Any:
        v, k, m = markets[" ".join(words).upper()]
        return SimpleNamespace(tag="TAO", venue=v, kind=k, symbol=m)

    monkeypatch.setattr(committeecmd, "resolve", resolve)
    say(db, tg, "/committee tao okx")
    say(db, tg, "/committee TAO", user=OTHER)  # the same market: the same run, not counted
    assert world == ["TAO OKX 1h"] and db.query(CmRun).count() == 1
    assert orchestrator.used_today(db, {"tg_user_id": OTHER}) == 0
    row = db.query(CmRun).one()
    assert (row.venue, row.market_id) == ("okx", "TAO-USDT")
    with pytest.raises(orchestrator.CommitteeError):  # another market never shares it (here: no evidence pack for it)
        asyncio.run(orchestrator.run_committee(db, "TAO HL", "1h", requested_by={"admin": True}))
    assert orchestrator.cached_run(db, "TAO HL", "1h", market={"venue": "hyperliquid", "market_id": "TAO"}) is None
    assert orchestrator.cached_run(db, "X", "4h", market={"venue": "okx", "market_id": "TAO-USDT"}) is None  # other timeframe


def test_stuck_runs_are_failed_refunded_and_their_placeholder_edited(db: Session, tg: TG, world: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db.base import utcnow
    from app.models.committee import CmUsage

    now = utcnow()
    old = CmRun(public_id="cm_stuck0000000001", symbol="DOGE", timeframe="4h", mode="live", depth="full", status="running",
                requested_by_tg=USER, chat_id=GROUP, message_id=42, created_at=now - timedelta(minutes=11), cost_usd=0.0, calls=0)
    prev = CmRun(public_id="cm_stuck0000000002", symbol="BTC", timeframe="1h", mode="live", depth="full", status="queued",
                 created_at=orchestrator.STARTED - timedelta(seconds=5), cost_usd=0.0, calls=0)
    fresh = CmRun(public_id="cm_fresh0000000003", symbol="ETH", timeframe="1h", mode="live", depth="full", status="running",
                  created_at=now, cost_usd=0.0, calls=0)
    db.add_all([old, prev, fresh])
    db.add(CmUsage(day=orchestrator.llm.day_of(now), key=f"tg:{USER}", tg_user_id=USER, count=2, calls=0, tokens_in=0, tokens_out=0, cost_usd=0.0))
    db.commit()
    assert {r.public_id for r in orchestrator.stuck(db)} == {old.public_id, prev.public_id}
    assert asyncio.run(committeecmd.recover(db, tg)) == 2
    db.expire_all()
    got = {r.public_id: (r.status, r.error) for r in db.query(CmRun)}
    assert got[old.public_id] == ("failed", "interrupted by a restart") and got[prev.public_id][0] == "failed"
    assert got[fresh.public_id] == ("running", None)
    ed = tg.of("edit")
    assert len(ed) == 1 and ed[0]["chat"] == GROUP and ed[0]["id"] == 42 and "was interrupted" in ed[0]["text"] and "Send the command again" in ed[0]["text"]
    assert orchestrator.used_today(db, {"tg_user_id": USER}) == 1  # given back
    assert asyncio.run(committeecmd.recover(db, tg)) == 0


def test_the_job_runs_the_recovery(db: Session, client: Any) -> None:
    from app.db.base import utcnow

    db.add(CmRun(public_id="cm_stuck0000000009", symbol="BTC", timeframe="1h", mode="live", depth="full", status="running",
                 created_at=utcnow() - timedelta(minutes=30), cost_usd=0.0, calls=0))
    db.commit()
    from app.jobs import build_jobs

    spec = next(j for j in build_jobs(client.app.state.runtime) if j.name == "committee.llm_expire")
    out = asyncio.run(spec.func(SimpleNamespace(db=db, now=utcnow(), trigger="manual", state={})))
    assert out["interrupted"] == 1
    db.expire_all()
    assert db.query(CmRun).one().status == "failed"


def test_full_report_button_is_one_message_however_often_it_is_pressed(db: Session, tg: TG, world: list[str]) -> None:
    rid, mid = _first_run(db, tg, chat=USER)
    tg.sent.clear()
    for _ in range(3):
        press(db, tg, f"cm:{rid}:full", chat=USER, mid=mid, user=USER)
    assert not tg.of("send") and len(tg.of("edit")) == 3
    assert all(e["id"] == mid and len(e["text"]) <= 4096 and "FULL REPORT" in e["text"] for e in tg.of("edit"))


def test_prompts_carry_the_derived_distances() -> None:
    from app.committee import prompts

    ev = live("doge")
    assert prompts.PROMPT_VERSION == "cm-2"
    d = roles.derived(ev)
    for p in (prompts.analyst_prompt(ev, "technical"), prompts.risk_prompt(ev, {}, {"action": "WAIT"}),
              prompts.debate_prompt(ev, {}, "bull")):
        assert '"derived":' in p and '"stop_atr":' + str(d["long"]["stop_atr"]) in p.replace(" ", "")
    assert '"derived"' not in prompts.analyst_prompt(ev, "news")


# ---------------------------------------------------------------- group buttons: answered privately
def test_group_buttons_answer_the_presser_privately_and_keep_the_summary(db: Session, tg: TG, world: list[str]) -> None:
    rid, mid = _first_run(db, tg)  # a group (not the main chat)
    for view, needle, label in (("bull", "BULL CASE", "Bull Case"), ("bear", "BEAR CASE", "Bear Case"), ("risk", "RISK REVIEW", "Risk Review"),
                                ("full", "FULL REPORT", "Full Report")):
        tg.sent.clear()
        press(db, tg, f"cm:{rid}:{view}", mid=mid, user=OTHER)
        assert not tg.of("edit")  # the shared message stays on the summary for everyone
        dms = tg.of("send")
        assert dms and all(m["chat"] == OTHER for m in dms) and needle in dms[0]["text"] and all(len(m["text"]) <= 4096 for m in dms)
        assert tg.of("answer")[0]["text"] == f"{label} sent to you privately." and tg.of("answer")[0]["alert"] is False
        if view != "full":  # the private copy has the buttons: pressed there, they edit that message in place
            assert ids_in(dms[0]["buttons"])[0] == f"cm:{rid}:sum"
    tg.sent.clear()
    press(db, tg, f"cm:{rid}:sum", mid=mid)  # Back / summary in the group: the summary, edited in place
    assert "KIWI AI INVESTMENT COMMITTEE" in tg.of("edit")[0]["text"] and not tg.of("send")


def test_group_button_without_a_private_chat_asks_to_press_start(db: Session, tg: TG, world: list[str]) -> None:
    rid, mid = _first_run(db, tg)
    tg.sent.clear()
    tg.fail_dm = True
    press(db, tg, f"cm:{rid}:bear", mid=mid, user=OTHER)
    ans = tg.of("answer")[0]
    assert ans["alert"] is True and "press Start" in ans["text"] and "Bear Case" in ans["text"]
    assert not tg.of("edit") and not tg.of("send")


# ---------------------------------------------------------------- "no trade approved because": each reason once
def _with_reasons(reasons: list[str], action: str = "WAIT", checks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    r = json.loads((Path(__file__).parent / "committee_runs" / "doge.json").read_text(encoding="utf-8"))
    r["verdict"]["action"] = action
    r["execution"] = {"status": "NOT_APPLICABLE" if action == "WAIT" else "REJECTED", "reasons": reasons, "checks": checks or [],
                      "engine": "kiwi-risk", "note": "Research only — nothing was traded."}
    return r


LIQ = ["liquidity: 24h volume $12M below the $50M minimum",
       "liquidity: thin order book ($80k within 0.5% on the thinner side, minimum $100k)"]


def test_reasons_shared_by_both_scenarios_are_said_once() -> None:
    run = _with_reasons(["no trade proposed", "long scenario now: " + "; ".join(LIQ), "short scenario now: " + "; ".join(LIQ)])
    line = tv.why_line(run)
    assert line == ("No trade approved because: Both setups blocked: 24h volume $12M below the $50M minimum; "
                    "order book $80k within 0.5% (min $100k)")
    assert len(line) <= tv.WHY_MAX
    body = tv.compact(run)
    assert body.count("24h volume $12M") == 1 and "no trade proposed" not in body and "scenario now" not in body
    risk = tv.risk_view(run)  # Risk Review: once too, in the engine's full wording
    assert risk.count("24h volume $12M below") == 1 and "Both setups blocked: liquidity: 24h volume $12M below the $50M minimum; liquidity: thin order book ($80k" in risk


def test_scenario_only_reasons_keep_their_side_and_the_line_stays_short() -> None:
    many = [f"check {i}: a fairly long explanation of why this particular check failed for the setup" for i in range(6)]
    run = _with_reasons(["no trade proposed", "long scenario now: " + "; ".join(LIQ[:1] + many),
                         "short scenario now: " + "; ".join(LIQ[:1] + ["price 0.0795 is outside the entry zone"])])
    line = tv.why_line(run)
    assert line.startswith("No trade approved because: Both setups blocked: 24h volume $12M below the $50M minimum · long only: check 0")
    assert len(line) <= tv.WHY_MAX and "more in Risk Review" in line
    g = tv.reason_groups(run["execution"])
    assert g["both"] == [LIQ[0]] and g["short"] == ["not triggered yet (price 0.0795 is outside its entry zone)"] and len(g["long"]) == 6
    risk = tv.risk_view(run)
    assert "· Long only: check 0" in risk and "· Short only: not triggered yet (price 0.0795 is outside its entry zone)" in risk


def test_a_scenario_the_engine_would_pass_is_said_plainly() -> None:
    run = _with_reasons(["no trade proposed", "long scenario now: price 0.079475 is outside the entry zone",
                         "short scenario now: the risk engine would approve it"])
    assert tv.why_line(run) == ("No trade approved because: long: not triggered (price 0.07948 outside its entry zone) · "
                                "short: would pass the risk checks; the verdict is still WAIT")
    assert tv.why_line(_with_reasons(["no trade proposed"])) is None  # nothing to add to "Verdict: WAIT"
    between = _with_reasons(["no trade proposed", "long scenario now: price 2406.8 is outside the entry zone",
                             "short scenario now: price 2406.8 is outside the entry zone; round-trip check failed"])
    assert tv.why_line(between) == ("No trade approved because: neither setup has triggered yet (price 2,406.8) · "
                                    "short: round-trip check failed")
    assert "· no trade proposed" in tv.risk_view(_with_reasons(["no trade proposed"]))  # Risk Review still says why


def test_rejected_trade_keeps_its_reasons_and_failed_checks_merge() -> None:
    checks = [{"name": f"{s} scenario · 24h volume at least the minimum", "ok": False, "value": "$12M", "limit": "$50M"} for s in ("long", "short")]
    run = _with_reasons(LIQ, "LONG", checks)
    assert tv.why_line(run).startswith("⛔ No trade approved because: 24h volume $12M below the $50M minimum; order book $80k")
    wait = _with_reasons(["no trade proposed", "long scenario now: " + LIQ[0], "short scenario now: " + LIQ[0]], checks=checks)
    risk = tv.risk_view(wait)
    assert "❌ both setups · 24h volume at least the minimum: $12M" in risk and risk.count("24h volume at least the minimum") == 1


def test_html_report_says_shared_reasons_once() -> None:
    from app.committee import report

    run = _with_reasons(["no trade proposed", "long scenario now: " + "; ".join(LIQ), "short scenario now: " + "; ".join(LIQ)])
    html_ = report.render(run)
    assert html_.count("24h volume $12M below the $50M minimum") == 1 and "Both setups blocked" in html_
