"""The wire style, revised before deploy (owner, 2026-11-14):
1. Title Case headlines in sentence case without an ordinary word keeping its capital ("..., Bank says"), and no qualifier lost ("at least six").
2. Sources grouped into one story only on same-event evidence (post 166: an unrelated Al Jazeera story attached on "Israeli").
3. Clean stays the default and byte-for-byte what production (1374981) writes; Wire is opt-in through the Admin switch.
4. Through the real lane: BREAKING, JUST IN, an UPDATE replying to the original message, a verified chart, a text-only post.
5. Selection: real news with "could" / "set to" isn't filler; previews, price targets, routine earnings and listicles are.
6. A real picture when it adds something: the public figure, the named site of a major incident, a chart whose candles agree.
7. Every factual gate still holds a punchier rewrite: an added figure, a dropped qualifier, a name, a superlative, a ticker.
No test reaches a real service."""
import io
import json
import math
import re
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.models.newsdesk import NdEvent
from app.newsdesk import brief, checks, clean, engine, events, network, photos, wire
from app.newsdesk import telegram as lane
from tests import newsdesk_style_cases as cases
from tests.test_newsdesk_breaking_e2e import BETWEEN, DW, NATIONAL, _desk_tick, _report, live  # noqa: F401 - the fixture
from tests.test_newsdesk_wire_e2e import TextTg

NOW = BETWEEN + timedelta(seconds=30)
REAL_FETCH_BARS = clean.fetch_bars  # tests/conftest.py stubs Yahoo out; these tests serve its real responses from a fixture instead
HERE = Path(__file__).parent


@pytest.fixture(autouse=True)
def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "DEFAULT_SCHEDULE", "slots")
    monkeypatch.setattr(lane, "FORMAT", "visual")
    events._alerted.clear()

    async def nothing(*a: Any, **k: Any) -> list[Any]:
        return []

    monkeypatch.setattr(photos, "discover", nothing)


# ================================================================ 3. clean is the default and unchanged
def _real_default() -> str:
    m = re.search(r'^DEFAULT_STYLE = "(\w+)"', Path(network.__file__).read_text(encoding="utf-8"), re.M)
    assert m, "network.DEFAULT_STYLE is set in one place"
    return m.group(1)


def test_wire_is_the_default_and_clean_is_retired(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    # owner, 2026-11-16: "one strong factual headline and at most one useful supporting sentence ... real licensed photos or meaningful
    # charts; otherwise publish text-only". Production reads the real default (tests/conftest.py runs the older tests in classic).
    assert _real_default() == "wire" and network.CLEAN_RETIRED
    monkeypatch.setattr(network, "DEFAULT_STYLE", _real_default())
    assert network.post_style({}) == network.post_style(None) == "wire" and network.post_style({"post_style": "bogus"}) == "wire"
    assert network.post_style({"post_style": "clean"}) == "wire" and network.post_style({"post_style": "classic"}) == "classic"
    assert network.state(db)["post_style"] == "wire"
    p = events.policy({})
    assert p.wire and p.breaking_per_day == 2  # the daily breaking cap as before
    assert network.configure(db, post_style="clean")["post_style"] == "wire"
    assert events.policy(engine.settings_row(db).state).wire


@pytest.mark.parametrize("case", cases.CASES, ids=[c["name"] for c in cases.CASES])
async def test_clean_output_is_byte_for_byte_what_production_writes(case: dict[str, Any]) -> None:
    # written by 1374981's code, regenerated 2026-11-15 for the owner's caption rules (sources and @thepricechat on one last line, each
    # cashtag once and one at most): every difference against the 1374981 golden was read; the words and the checks' verdicts are unchanged
    golden = json.loads((HERE / "newsdesk_clean_golden_20261115.json").read_text(encoding="utf-8"))[case["name"]]
    ev, rows = cases.build(case, NdEvent)
    pkg = await network.package(ev, settings=None, rows=rows, x_links=True, style="clean")
    assert {"telegram_html": pkg.telegram_html, "telegram_plain": pkg.telegram_plain, "x_text": pkg.x_text, "ok": pkg.ok} == golden


async def test_with_no_style_chosen_the_lane_posts_wire(client: Any, db: Session, live: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setattr(network, "DEFAULT_STYLE", _real_default())
    _released(db, "c1", "Deutsche Bank's Bitcoin and Ether Custody Is Open to Clients, Bank Says", cases.CASES[0]["rows"][0][3],
              [("Unchained", cases.CASES[0]["rows"][0][1])], label="")
    tg = TextTg()
    await lane.tick(db, get_settings(), tg, NOW)
    # no public figure and no named site: no stock photo of a bank's headquarters and no headline card, a text post
    assert tg.sent[0]["image"] is None and db.scalars(select(NdEvent)).one().facts["visual"] == {"style": "wire", "kind": "text", "layout": "text"}


# ================================================================ 1. casing and qualifiers
CUSTODY_SAID = " ".join(r[3] for r in cases.CASES[0]["rows"])


@pytest.mark.parametrize("head,reporting,want", [
    ("Deutsche Bank's Bitcoin and Ether Custody Is Open to Clients, Bank Says", CUSTODY_SAID,
     "Deutsche Bank's Bitcoin and Ether custody is open to clients, bank says"),  # the live bug: "Bank says"
    ("Deutsche Bank's Bitcoin and Ether Custody Is Open to Clients, Bank Says", "",
     "Deutsche Bank's Bitcoin and Ether custody is open to clients, bank says"),  # nothing to read against: the name still ends in Bank
    ("Western Union's USDPT Stablecoin Becomes a Settlement Option on Visa, Anchorage Digital Says",
     "Anchorage Digital said Visa partners can now pay their VisaNet balances in USDPT, the Western Union token its bank mints. Visa has not "
     "put out its own statement.", "Western Union's USDPT stablecoin becomes a settlement option on Visa, Anchorage Digital says"),
    ("Israeli Army Kills Palestinian Footballer Mahmoud Kamal Abdel-Malek in Gaza", "",
     "Israeli army kills Palestinian footballer Mahmoud Kamal Abdel-Malek in Gaza"),  # a name the reporting never writes stays a name
    ("Fed Holds Rates Steady as Powell Signals Patience", "", "Fed holds rates steady as Powell signals patience"),
    ("Nvidia Hires Wachtell Lipton to Investigate CEO Affair", "The chip maker said the review would be independent.",
     "Nvidia hires Wachtell Lipton to investigate CEO affair"),
    ("Bitcoin Breaks Below $70,000 as Liquidations Sweep Through Leveraged Longs", "", "Bitcoin breaks below $70,000 as liquidations sweep through leveraged longs"),
    ("SpaceX, Tesla Merger Talks Progress, Bloomberg News Reports", "", "SpaceX, Tesla merger talks progress, Bloomberg News reports"),
    ("Russian Diesel Tanker Makes U-Turn After Heading to U.S.", "A tanker carrying Russian diesel made a U-turn off New York.",
     "Russian diesel tanker makes U-turn after heading to U.S."),
])
def test_title_case_headlines_keep_names_and_lose_ordinary_capitals(head: str, reporting: str, want: str) -> None:
    assert wire.headline_case(head, reporting) == want


SIX_SOURCES = ("Israeli strike on Gaza hospital kills six Palestinians, health ministry says. Witnesses say a missile hit a building "
               "containing the surgical wards. Israeli strike on Nasser Hospital kills at least six, Gaza civil defence agency says.")


@pytest.mark.parametrize("text,source,want", [
    ("Israeli strike on Gaza hospital kills six Palestinians.", SIX_SOURCES, "Israeli strike on Gaza hospital kills at least six Palestinians."),
    ("Up to $3 billion of longs liquidated.", "Up to $3 billion of longs were liquidated, CoinGlass said.", "Up to $3 billion of longs liquidated."),
    ("Exchange loses $40 million in hack.", "The exchange lost an estimated $40 million, it said.", "Exchange loses an estimated $40 million in hack."),
    ("Tesla holds 30,000 bitcoin.", "Talks about 30,000 bitcoin.", "Tesla holds 30,000 bitcoin."),  # "talks about": a preposition
    ("A 25% stake was sold.", "Tariffs of up to 25% on steel.", "A 25% stake was sold."),  # the same figure counting something else
])
def test_a_qualifier_the_sources_attach_to_a_figure_is_put_back(text: str, source: str, want: str) -> None:
    assert checks.restore_qualifiers(text, source) == want
    assert checks.qualifier_problems(want, source) == []


def test_a_hedge_on_the_claim_is_never_moved_but_its_loss_is_held() -> None:
    src = "Rebels reportedly killed 20 soldiers, local media said."
    assert checks.restore_qualifiers("Rebels kill 20 soldiers.", src) == "Rebels kill 20 soldiers."
    assert checks.qualifier_problems("Rebels kill 20 soldiers.", src)
    assert checks.qualifier_problems("Rebels reportedly kill 20 soldiers, local media said.", src) == []


def test_the_final_gate_holds_a_figure_stated_as_exact_that_the_sources_qualify() -> None:
    caption = "JUST IN: Israeli strike on Gaza hospital kills six Palestinians, health ministry says.\n\nSource: BBC · @thepricechat"
    assert checks.final_gate(caption, SIX_SOURCES, wire=True) == [
        "a figure stated as exact that the sources qualify: six (the sources say at least six)"]
    assert checks.final_gate(caption.replace("kills six", "kills at least six"), SIX_SOURCES, wire=True) == []
    assert checks.final_gate(caption, SIX_SOURCES) == []  # clean and classic: production's gate, unchanged


BBC = SimpleNamespace(source="BBC", link="https://www.bbc.co.uk/news/articles/ckzd9p7e4eqeo",
                      title="Israeli strike on Gaza hospital kills six Palestinians, health ministry says",
                      summary="Witnesses say a missile hit a building containing the surgical wards and intensive care units at Nasser hospital, the "
                              "largest in southern Gaza. Israeli strike on Nasser Hospital kills at least six, Gaza civil defence agency says.")
ALJ = SimpleNamespace(source="Al Jazeera", link="https://www.aljazeera.com/sports/2026/11/11/israeli-army-kills-palestinian-footballer-mahmoud-kamal-abdel-malek-in-gaza",
                      title="Israeli army kills Palestinian footballer Mahmoud Kamal Abdel-Malek in Gaza",
                      summary="Abdel-Malek, the captain and goalkeeper of Gaza Sports Club, recently played in the Thousand Martyrs Cup final.")


def _post_166() -> NdEvent:
    e = NdEvent(key="ex:166", kind="news", title=BBC.title, headline=BBC.title, summary=BBC.summary, topic="geopolitics", label=brief.JUST_IN,
                evidence="two outlets", category="news", slot="slot", released_at=NOW, first_seen=NOW - timedelta(minutes=40),
                sources=[{"name": r.source, "url": r.link, "title": r.title} for r in (BBC, ALJ)], facts={"told": BBC.title})
    e.id = 166
    return e


async def test_post_166_keeps_at_least_six_and_drops_the_unrelated_story() -> None:
    pkg = await network.package(_post_166(), settings=None, rows=[BBC, ALJ], x_links=True, style="wire")
    assert pkg.ok, [c for c in pkg.checks if not c["passed"]]
    assert pkg.telegram_plain.startswith("JUST IN: Israeli strike on Gaza hospital kills at least six Palestinians, health ministry says.")
    assert "Al Jazeera" not in pkg.telegram_html and "aljazeera" not in pkg.x_text and [x.name for x in pkg.links] == ["BBC"]


# ================================================================ 2. same-event grouping
@pytest.mark.parametrize("a,b", [
    (BBC.title, ALJ.title),  # post 166: "Israeli" and "Gaza" in common, two different events
    ("Israeli strike kills six in Gaza", "Israeli strike kills 20 in Lebanon"),
    ("Bybaz exchange hacked for $40 million", "Qorvex exchange hacked for $40 million"),
    ("Iran launches missiles at Israel", "Iran opens training camp for women in readiness for war"),
    ("Deutsche Bank opens bitcoin custody to clients", "Deutsche Bank fined $10 million over money laundering checks"),
    ("Bitcoin breaks below $69,000", "Bitcoin climbs above $69,000"),  # the same level, the other way
    ("Bitcoin falls below $70,000", "Gold falls below $4,000"),
])
def test_stories_that_only_share_generic_words_are_not_one_event(a: str, b: str) -> None:
    assert not checks.same_event(a, b) and not network.related(a, b)


@pytest.mark.parametrize("a,b", [
    (BBC.title, "Israeli strike on Nasser Hospital kills at least six, Gaza civil defence agency says"),
    ("Missile strike hits Saudi Aramco oil terminal at Ras Tanura", "Missile strike hits Saudi Aramco's Ras Tanura oil terminal, officials say"),
    ("Deutsche Bank's Bitcoin and Ether Custody Is Open to Clients, Bank Says", "Deutsche Bank goes live with digital asset custody service, signs asset management deal"),
    ("Western Union's USDPT Stablecoin Becomes a Settlement Option on Visa, Anchorage Digital Says", "Visa adds Western Union"),
    ("Crypto fraudster jailed for eight years over $2.7 million theft plot directed from prison", "Prisoner Jailed for 8 Years Over $2.7M Crypto Theft Plot Run From His Cell"),
    ("Bitcoin Breaks Below $69,000 as Selloff Sets New Lows", "Bitcoin tumbles below $69,000 as Nvidia turmoil adds to Fed-driven market jitters"),  # one level
    ("Bitcoin dips below $70,000 as Dollar Index hits 19-month high", "Bitcoin slides below $70K amid thin liquidity, ETF outflows"),  # $70K is $70,000
    ("U.S. seizes $1.2 billion in cryptocurrency stolen from Binance", "DOJ moves to seize $1.2B from Binance hack, disrupts FaceStealer scam"),  # one amount
])
def test_real_coverage_of_one_event_still_groups(a: str, b: str) -> None:
    assert checks.same_event(a, b)


def test_the_importance_path_corroborates_only_the_same_event() -> None:
    from app.newsdesk import editorial

    at = NOW.replace(tzinfo=None)
    nasser = "Israeli strike on Nasser Hospital kills at least six, Gaza civil defence agency says"
    rows = [SimpleNamespace(id=i, source=s, title=t, published_at=at) for i, (s, t) in enumerate([("BBC", BBC.title), ("Al Jazeera", ALJ.title), ("DW", nasser)])]
    assert [o.source for o in editorial.corroborating(rows[0], rows)] == ["DW"]  # post 166: ISRAEL in common was enough before
    nyse = [SimpleNamespace(id=0, source="CoinDesk", title="NYSE picks Avalanche for tokenized stock settlement", published_at=at),
            SimpleNamespace(id=1, source="The Block", title="NYSE to settle tokenized stocks on Avalanche", published_at=at)]
    assert [o.source for o in editorial.corroborating(nyse[0], nyse)] == ["The Block"]  # one event, worded differently: still corroborated


def test_the_event_merge_and_the_corroboration_use_same_event_evidence() -> None:
    held = NdEvent(key="story:bbc", kind="news", title=BBC.title, topic="geopolitics", sources=[{"name": "BBC", "url": BBC.link}])
    footballer = SimpleNamespace(link=ALJ.link, title=ALJ.title, fingerprint=checks.fingerprint(ALJ.title))
    assert not events._same(held, footballer, {BBC.link})
    nasser = "Israeli strike on Nasser Hospital kills at least six, Gaza civil defence agency says"
    assert events._same(held, SimpleNamespace(link="https://x.example/n", title=nasser, fingerprint=checks.fingerprint(nasser)), {BBC.link})
    at = NOW
    rows = [SimpleNamespace(id=i, source=s, title=t, fingerprint=checks.fingerprint(t), published_at=at - timedelta(hours=h))
            for i, (s, t, h) in enumerate([("BBC", BBC.title, 0), ("Al Jazeera", ALJ.title, 0), ("DW", nasser, 1), ("Reuters", nasser, 72)])]
    assert [o.source for o in engine.corroboration(rows[0], rows)] == ["DW"]  # not the footballer; not a telling three days apart


async def test_through_the_desk_a_shared_country_never_merges_two_stories(client: Any, db: Session, live: dict[str, Any]) -> None:  # noqa: F811
    live["feeds"]["items"] = {
        DW["url"]: [_report(DW, "Israeli strike on Nasser Hospital kills at least six, Gaza civil defence agency says", 20,
                            "An Israeli strike hit Nasser Hospital in Khan Younis, killing at least six people, Gaza's civil defence agency said.")],
        NATIONAL["url"]: [_report(NATIONAL, ALJ.title, 12, "Israeli forces killed Mahmoud Kamal Abdel-Malek, the captain of Gaza Sports Club, officials said.")],
    }
    await _desk_tick(db)
    evs = list(db.scalars(select(NdEvent)))  # (alone, neither has a second outlet any more: neither may count the other as one)
    assert all({s["name"] for s in ev.sources or []} != {"DW", "The National"} for ev in evs), [(e.title, e.sources) for e in evs]


async def test_through_the_desk_two_tellings_of_one_event_still_group(client: Any, db: Session, live: dict[str, Any]) -> None:  # noqa: F811
    live["feeds"]["items"] = {
        DW["url"]: [_report(DW, "Missile strike hits Saudi Aramco oil terminal at Ras Tanura", 20, "A missile strike hit Saudi Aramco's Ras Tanura oil export terminal, Saudi officials said.")],
        NATIONAL["url"]: [_report(NATIONAL, "Missile strike hits Saudi Aramco's Ras Tanura oil terminal, officials say", 12,
                                  "Saudi officials said a missile hit the Ras Tanura terminal, the kingdom's largest oil export facility.")],
    }
    await _desk_tick(db)
    ev = db.scalars(select(NdEvent)).one()
    assert {s["name"] for s in ev.sources} >= {"DW", "The National"} and ev.evidence == "two outlets"


# ================================================================ 4. through the real lane (Wire chosen in Admin)
def _released(db: Session, key: str, title: str, summary: str, srcs: list[tuple[str, str]], *, label: str = "", age: float = 30, kind: str = "news",
              slot: str = "slot", evidence: str = "two outlets", topic: str = "", parent_id: int | None = None, extra: dict[str, Any] | None = None,
              **facts: Any) -> NdEvent:
    facts.update(extra or {})
    ev = NdEvent(key=key, kind=kind, title=title, headline=title, summary=summary, topic=topic, label=label, evidence=evidence, category="news",
                 virality=72, parts={}, status="released", slot=slot, released_at=NOW - timedelta(minutes=1), first_seen=NOW - timedelta(minutes=age),
                 expires_at=NOW + timedelta(hours=2), parent_id=parent_id,
                 facts={"told": f"{title} {summary}", "published_at": (NOW - timedelta(minutes=age)).isoformat(), **facts},
                 sources=[{"name": n, "url": u, "title": title} for n, u in srcs])
    db.add(ev)
    db.commit()
    return ev


@pytest.fixture
def wired(db: Session, live: dict[str, Any]) -> dict[str, Any]:  # noqa: F811
    network.configure(db, post_style="wire")  # the owner's choice in Admin
    return live


async def test_talks_ahead_of_a_meeting_never_go_out(client: Any, db: Session, wired: dict[str, Any]) -> None:
    # News Desk post 116 (X 2121519072441467219, 2026-11-14): "JUST IN: China, US discuss tariffs, farm trade ahead of Xi-Trump meeting"
    # was talks before a meeting, no outcome. The final editorial check holds it on both platforms (owner, 2026-11-16).
    rows = cases.CASES[1]["rows"][0]
    _released(db, "j0", rows[2], rows[3], [(rows[0], rows[1])], label=brief.JUST_IN, age=6, evidence="attributed")
    tg = TextTg()
    await lane.tick(db, get_settings(), tg, NOW)
    assert tg.sent == []
    assert "routine statement or talks with no outcome" in (db.scalars(select(NdEvent)).one().tg_note or "")


async def test_just_in_goes_out_as_one_short_text_post(client: Any, db: Session, wired: dict[str, Any]) -> None:
    rows = cases.CASES[2]["rows"][0]
    _released(db, "j1", rows[2], rows[3], [(rows[0], rows[1])], label=brief.JUST_IN, age=6, evidence="attributed")
    tg = TextTg()
    sent = await lane.tick(db, get_settings(), tg, NOW)
    assert sent["sent"] == 501 and tg.sent[0]["image"] is None
    assert tg.sent[0]["caption"] == (
        "<b>JUST IN:</b> Bitcoin $BTC breaks below $70,000 as liquidations sweep through leveraged longs. More than $1 billion in crypto "
        "positions were forced shut in 12 hours as bitcoin broke below $70,000.\n\n"
        '🔗 <a href="https://unchainedcrypto.com/bitcoin-breaks-below-70000-as-liquidations-sweep-through-leveraged-longs/">Unchained</a> · <a href="https://t.me/thepricechat">@thepricechat</a>')


async def test_an_update_replies_to_the_original_telegram_message(client: Any, db: Session, wired: dict[str, Any]) -> None:
    parent = _released(db, "p1", "Bybaz exchange hacked for $40 million", "Bybaz said hackers drained $40 million from a hot wallet.",
                       [("CoinDesk", "https://www.coindesk.com/business/2026/11/14/bybaz-hacked")], topic="hack")
    parent.released_at, parent.tg_status, parent.x_status, parent.tg_message_id = NOW - timedelta(hours=3), "delivered", "delivered", 170
    db.commit()
    _released(db, "u1", "Bybaz halts all withdrawals after $40 million hack", "Bybaz halted all withdrawals while it investigates, the exchange said.",
              [("The Block", "https://www.theblock.co/news/2026-11-14-bybaz-halts-withdrawals")], kind="update", parent_id=parent.id, topic="hack",
              development="halted withdrawals", kicker="UPDATE · first reported 14 Nov")
    tg = TextTg()
    await lane.tick(db, get_settings(), tg, NOW)
    assert len(tg.sent) == 1 and tg.sent[0]["reply_to"] == 170  # threaded under the original post
    assert tg.sent[0]["caption"].startswith("<b>UPDATE:</b> Bybaz halts all withdrawals after $40M hack.")


async def test_a_story_with_no_real_picture_goes_text_only(client: Any, db: Session, wired: dict[str, Any]) -> None:
    r = cases.CASES[0]["rows"]
    _released(db, "t1", r[0][2], " ".join(x[3] for x in r), [(x[0], x[1]) for x in r], age=480)
    tg = TextTg()
    await lane.tick(db, get_settings(), tg, NOW)
    cap = tg.sent[0]["caption"]
    assert tg.sent[0]["image"] is None and cap.startswith("Deutsche Bank's Bitcoin $BTC and Ether $ETH custody is open to clients, bank says.\n\n🔗 ")
    assert db.scalars(select(NdEvent)).one().facts["visual"] == {"style": "wire", "kind": "text", "layout": "text"}


def _points(start: float, end: float, n: int = 97) -> list[list[float]]:
    t0 = (NOW - timedelta(minutes=2)).timestamp() - 900 * (n - 1)
    pts = [[int(t0 + 900 * i), start + (end - start) * i / (n - 1) + (0 if i in (0, n - 1) else 120 * math.sin(i * 1.3))] for i in range(n)]
    return pts


async def test_a_breaking_measured_move_goes_with_its_verified_chart(client: Any, db: Session, wired: dict[str, Any]) -> None:
    pts = _points(100_000.0, 93_800.0)  # -6.2% over 24 hours, the very series the chart draws
    _released(db, "m1", "Bitcoin falls 6.2% in 24 hours", "", [("Hyperliquid", "https://app.hyperliquid.xyz/trade/BTC")], kind="market", slot="breaking",
              label=brief.BREAKING, evidence="price data", topic="move", age=5, points=pts, window="24h", ratio=1.4, change_pct=-6.2, assets=["BTC"],
              coin="BTC", name="Bitcoin", data_source="Hyperliquid", extra={"kind": "move"}, as_of=(NOW - timedelta(minutes=1)).isoformat())
    tg = TextTg()
    sent = await lane.tick(db, get_settings(), tg, NOW)
    assert sent["sent"] == 501, sent
    assert tg.sent[0]["caption"].startswith("<b>BREAKING:</b> Bitcoin $BTC falls 6.2% in 24 hours.")
    assert Image.open(io.BytesIO(tg.sent[0]["image"])).size == (1600, 900)
    seen = db.scalars(select(NdEvent)).one().facts["visual"]
    assert seen["layout"] == "chart" and seen["number_check"]["ok"] and abs(seen["number_check"]["series_pct"] + 6.2) < 0.1, seen["number_check"]


@pytest.fixture
def yahoo_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clean, "fetch_bars", REAL_FETCH_BARS)  # the real request and parsing, against the fixture transport


def _yahoo(start: float, end: float, low: float | None = None) -> httpx.MockTransport:
    n = 96
    t0 = int((NOW - timedelta(minutes=16)).timestamp()) - 900 * (n - 1)
    closes = [start + (end - start) * i / (n - 1) + (0 if i in (0, n - 1) else 60 * math.sin(i)) for i in range(n)]
    opens = [start] + closes[:-1]
    body = {"chart": {"result": [{"timestamp": [t0 + 900 * i for i in range(n)], "indicators": {"quote": [{
        "open": opens, "close": closes, "high": [max(o, c) + 40 for o, c in zip(opens, closes)],
        "low": [min(o, c) - 40 if low is None or i != n - 2 else low for i, (o, c) in enumerate(zip(opens, closes))]}]}}]}}

    def handle(r: httpx.Request) -> httpx.Response:
        if r.url.host == "query1.finance.yahoo.com" and "BTC-USD" in r.url.path:
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    return httpx.MockTransport(handle)


BTC70 = cases.CASES[2]["rows"][0]


async def test_a_price_story_goes_with_the_real_chart_when_the_candles_agree(client: Any, db: Session, wired: dict[str, Any], yahoo_parsing: None) -> None:
    _released(db, "c1", BTC70[2], BTC70[3], [(BTC70[0], BTC70[1])], label=brief.JUST_IN, age=37, evidence="attributed")
    tg = TextTg()
    await lane.tick(db, get_settings(), tg, NOW, writer_transport=_yahoo(72_400.0, 69_600.0))
    assert tg.sent[0]["image"] is not None and tg.sent[0]["caption"].startswith("<b>JUST IN:</b> Bitcoin $BTC breaks below $70,000 as liquidations sweep")
    meta = db.scalars(select(NdEvent)).one().facts["visual"]
    assert meta["layout"] == "chart" and meta["symbol"] == "BTC-USD" and meta["bars"][-1][4] < 70_000


async def test_no_chart_when_the_candles_dont_show_what_the_headline_says(client: Any, db: Session, wired: dict[str, Any], yahoo_parsing: None) -> None:
    _released(db, "c2", BTC70[2], BTC70[3], [(BTC70[0], BTC70[1])], label=brief.JUST_IN, age=37, evidence="attributed")
    tg = TextTg()
    await lane.tick(db, get_settings(), tg, NOW, writer_transport=_yahoo(71_800.0, 72_600.0))  # never below $70,000 in these candles
    assert tg.sent[0]["image"] is None and db.scalars(select(NdEvent)).one().facts["visual"]["layout"] == "text"


@pytest.mark.parametrize("text,start,end,ok", [
    ("Bitcoin breaks below $70,000", 72_000, 69_500, True),
    ("Bitcoin breaks below $70,000", 72_000, 71_000, False),
    ("Gold jumps 3% to a record high", 4_000, 4_124, True),
    ("Gold jumps 3% to a record high", 4_124, 4_000, False),  # the candles fall
    ("S&P 500 falls 3.2%", 6_800, 6_790, False),  # a 0.15% day is not a 3.2% fall
    ("Bitcoin liquidations sweep through leveraged longs", 70_000, 68_000, True),
])
def test_the_chart_must_show_the_stated_move(text: str, start: float, end: float, ok: bool) -> None:
    n = 60
    closes = [start + (end - start) * i / (n - 1) for i in range(n)]
    bars = [[float(i), (closes[i - 1] if i else start), max(closes[i - 1] if i else start, c) + 1, min(closes[i - 1] if i else start, c) - 1, c] for i, c in enumerate(closes)]
    assert (wire.chart_matches(text, bars) == "") is ok, wire.chart_matches(text, bars)


# ================================================================ 5. selection
@pytest.mark.parametrize("title", [
    "Bitcoin price prediction: BTC could reach $150,000 by year-end",
    "Analysts say ether could rally next week",
    "XRP set to explode as whales accumulate",
    "Nvidia earnings preview: what to expect",
    "5 altcoins to watch this week",
    "Tesla reports quarterly results in line with expectations",
    "Apple could unveil new AI features, report says",
    "Saudi Arabia weighs oil output increase",
    "Visa partners with fintech startup on payments",
])
def test_filler_is_held_back(title: str) -> None:
    assert wire.filler(SimpleNamespace(kind="news", title=title)) is not None


@pytest.mark.parametrize("title", [
    "Fed set to cut rates by 25 basis points after inflation cools",
    "Binance could face $4 billion fine as DOJ files charges",
    "Oil jumps 5% as traders say supply could tighten",
    "SEC approves spot ether ETFs; trading set to begin Tuesday",
    "Nvidia shares tumble 8% after earnings miss",
    "Bybaz halts withdrawals after $40 million hack",
    "Visa adds Western Union's USDPT stablecoin to its settlement network",
    "China, US discuss tariffs, farm trade ahead of Xi-Trump meeting",
    "Trump says he could fire Powell, markets slide",
])
def test_real_news_with_could_or_set_to_is_not_filler(title: str) -> None:
    assert wire.filler(SimpleNamespace(kind="news", title=title)) is None


def test_surprising_market_moving_news_outranks_routine_news() -> None:
    ranks = {t: wire.surprise_bonus(SimpleNamespace(title=t)) for t in (
        "Bybaz halts withdrawals after $40 million hack", "Oil plunges 7% after OPEC+ surprise output hike", "Deutsche Bank opens custody to clients")}
    assert ranks["Bybaz halts withdrawals after $40 million hack"] == ranks["Oil plunges 7% after OPEC+ surprise output hike"] == wire.SURPRISE_MAX
    assert ranks["Deutsche Bank opens custody to clients"] == 0


# ================================================================ 6. real pictures when they add something
def _photo(title: str, description: str = "") -> photos.LicensedPhoto:
    return photos.LicensedPhoto(title=title, url=f"https://upload.wikimedia.org/{abs(hash(title))}.jpg", page="https://commons.wikimedia.org/wiki/File:x.jpg",
                                creator="Example Photographer", provider="wikimedia", licence="CC BY-SA 4.0", source="wikimedia", width=1800, height=1200,
                                description=description)


async def test_a_major_incident_at_a_named_site_gets_that_sites_credited_photo(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[list[str], bool]] = []

    async def discover(client: Any, queries: list[str], terms: list[str], **kw: Any) -> list[Any]:
        asked.append((terms, kw.get("sensitive")))
        return [_photo("File:Gamal Abdel Nasser Hospital 1.jpg", "Gamal Abdel Nasser Hospital in Alexandria, Egypt"),  # real Commons result, wrong site
                _photo("File:Nasser Hospital entrance.jpg", "The entrance of Nasser Hospital, Khan Younis, Gaza Strip")]

    async def fetch(client: Any, p: Any, min_width: int = 0) -> Image.Image:
        return Image.new("RGB", (1800, 1200), (90, 90, 90))

    monkeypatch.setattr(photos, "discover", discover)
    monkeypatch.setattr(photos, "fetch", fetch)
    e = _post_166()
    e.title = e.headline = "Israeli strike on Nasser Hospital kills at least six, Gaza civil defence agency says"
    img, meta = await wire.picture(e, now=NOW)
    assert asked == [(["Nasser"], True)]  # the site by name, and never people on a story of violence
    assert meta["layout"] == "photo" and meta["site"] == "Nasser hospital" and "Example Photographer" in meta["credit_line"] and img
    assert meta["title"] == "File:Nasser Hospital entrance.jpg"  # never the Egyptian hospital that shares the name


@pytest.mark.parametrize("title,description,ok", [
    ("File:Nasser Hospital entrance.jpg", "Nasser Hospital, Khan Younis, Gaza", True),
    ("File:Gamal Abdel Nasser Hospital 1.jpg", "Gamal Abdel Nasser Hospital in Alexandria, Egypt", False),  # a longer name, another country
    ("File:Nasser Hospital.jpg", "A hospital building", False),  # no place to check it against the story's
    ("File:Al Nasser ICU 2005.jpeg", "Missiles were deployed during this attack", False),  # not the site's name
])
def test_a_site_photo_must_show_that_site_in_that_place(title: str, description: str, ok: bool) -> None:
    assert wire.site_photo_fits(_photo(title, description), "Nasser", "hospital", {"gaza"}) is ok


@pytest.mark.parametrize("title,ok", [
    ("File:Donald Trump official portrait.jpg", True),
    ("File:Donald Trump swearing in ceremony.jpg", True),
    ("File:Donald Trump Facebook Twitter post 19679418.jpg", False),  # real Commons result: a tweet laid over a rally photo
    ("File:Trump quote poster.png", False),
])
def test_a_person_photo_is_a_plain_photograph_not_a_post_or_a_graphic(title: str, ok: bool) -> None:
    assert wire.plain_photo(_photo(title)) is ok


@pytest.mark.parametrize("title", [
    BBC.title,  # "Gaza hospital": a place, not a named site
    "Iran opens training camp for women in readiness for war",  # no incident at a site
    "Deutsche Bank goes live with digital asset custody service",
])
def test_no_stand_in_photo_without_a_named_site_or_person(title: str) -> None:
    assert wire.site_subject(SimpleNamespace(headline=title, title=title, facts={})) is None


def test_an_index_move_gets_the_index_chart_and_a_macro_story_does_not() -> None:
    assert wire.price_story(SimpleNamespace(headline="S&P 500 falls 3.2% as tech stocks slide", title="", facts={}))[0] == "^GSPC"
    assert wire.price_story(SimpleNamespace(headline="Nasdaq jumps 2.4% after Nvidia results", title="", facts={}))[0] == "^IXIC"
    assert wire.price_story(SimpleNamespace(headline="Fed cuts rates by 0.25%, the first cut this year", title="", facts={})) is None
    assert wire.price_story(SimpleNamespace(headline="Nasdaq-listed miner raises $40 million", title="", facts={})) is None


# ================================================================ 7. a punchier rewrite is still held
SRC = ("Bitcoin fell 5.4% to $69,800 as more than $1 billion of leveraged longs were liquidated in 12 hours, CoinDesk reported. "
       "At least six exchanges paused withdrawals.")


@pytest.mark.parametrize("rewrite,held", [
    ("BREAKING: Bitcoin plunges 9% as $1 billion of longs are wiped out.", "figures no source gave: 9"),
    ("JUST IN: Six exchanges paused withdrawals as bitcoin fell 5.4%.", "a figure stated as exact that the sources qualify: Six"),
    ("JUST IN: $1 billion of longs liquidated as bitcoin fell 5.4%.", "a figure stated as exact that the sources qualify: $1 billion"),
    ("JUST IN: Bitcoin fell 5.4% after Michael Saylor sold, CoinDesk reported.", "people no source names: Michael Saylor"),
    ("BREAKING: Bitcoin suffers its worst liquidation ever, down 5.4%.", "a superlative no source uses"),
    ("JUST IN: Binance $BNB users hit as bitcoin fell 5.4% to $69,800.", "tickers the sources don't establish: $BNB"),
])
def test_a_punchier_rewrite_is_still_held_by_the_final_gate(rewrite: str, held: str) -> None:
    got = checks.final_gate(f"{rewrite}\n\nSource: CoinDesk · @thepricechat", SRC, wire=True)
    assert any(held in p for p in got), got


def _model(draft: dict[str, str]) -> httpx.MockTransport:
    return httpx.MockTransport(lambda r: httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps(draft)}]}))


async def test_a_model_draft_that_drops_a_qualifier_goes_out_with_it_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.security import credentials

    monkeypatch.setattr(credentials, "resolve", lambda name, settings=None: "key" if name == "anthropic_api_key" else None)
    draft = {"headline": "Israeli strike on Gaza hospital kills six Palestinians", "short": "", "context": "", "stakes": "", "question": ""}
    pkg = await network.package(_post_166(), settings=SimpleNamespace(newsdesk_model="claude-test"), rows=[BBC], transport=_model(draft), style="wire")
    assert pkg.ok and pkg.copy.writer == "model"
    assert pkg.telegram_plain.startswith("JUST IN: Israeli strike on Gaza hospital kills at least six Palestinians.")
    assert checks.final_gate(pkg.telegram_plain, network.source_text_of(_post_166(), [BBC]), wire=True) == []
