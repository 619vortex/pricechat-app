"""The wire style (app/newsdesk/wire, owner 2026-11-14): short factual headlines, BREAKING / JUST IN / UPDATE only when earned, a real chart
for major price moves and liquidations, text-only posts otherwise, surprising stories first, breaking between slots under a rolling cap, and
every factual gate exactly as before, with hype and superlatives held on top. No test reaches a real service."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from PIL import Image
from sqlalchemy.orm import Session

from app.models.newsdesk import NdEvent
from app.newsdesk import brief, checks, clean, events, network, photos, sources, visual, wire
from app.newsdesk.network import Copy, Link

AT = datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)
GEO = "Missile strike hits Saudi Aramco oil terminal at Ras Tanura"


@pytest.fixture(autouse=True)
def _wire(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(network, "DEFAULT_STYLE", "wire")


def ev(headline: str, *, title: str = "", label: str = "", kind: str = "news", slot: str = "slot", age: float = 20, evidence: str = "two outlets",
       topic: str = "", summary: str = "", extra: dict[str, Any] | None = None, **facts: Any) -> NdEvent:
    e = NdEvent(key=f"k:{headline}", kind=kind, title=title or headline, headline=headline, summary=summary, topic=topic, label=label, evidence=evidence,
                category="news", sources=[], slot=slot, released_at=AT, facts={"published_at": (AT - timedelta(minutes=age)).isoformat(), **facts, **(extra or {})})
    e.id = 11
    return e


# ---------------------------------------------------------------- tags
def test_breaking_is_major_market_moving_strong_and_just_happened() -> None:
    geo = {"importance": {"kind": "geopolitics"}, "topic": "geopolitics"}
    assert wire.tag_for(ev(GEO, slot="breaking", label=brief.JUST_IN, age=20, **geo)) == "BREAKING:"  # the breaking lane, 20 min
    assert wire.tag_for(ev(GEO, label=brief.BREAKING, age=40, **geo)) == "BREAKING:"  # a slot post the desk labelled BREAKING
    assert wire.tag_for(ev(GEO, label=brief.BREAKING, age=75, **geo)) == "JUST IN:"  # over an hour: fresh, no longer breaking
    assert wire.tag_for(ev(GEO, label=brief.BREAKING, age=40, evidence="attributed", **geo)) == "JUST IN:"  # one outlet: never BREAKING
    talk = "Pete Hegseth decries impeachment push"
    assert wire.tag_for(ev(talk, label=brief.BREAKING, age=20, topic="controversy")) == "JUST IN:"  # not market-moving


def test_just_in_is_fresh_but_smaller_and_old_news_carries_no_tag() -> None:
    assert wire.tag_for(ev("Deutsche Bank opens bitcoin custody to clients", label=brief.JUST_IN, age=45)) == "JUST IN:"
    assert wire.tag_for(ev("Bybaz halts withdrawals after hack", label=brief.ALERT, age=60)) == "JUST IN:"  # no ALERT tag in this style
    assert wire.tag_for(ev("Deutsche Bank opens bitcoin custody to clients", label=brief.JUST_IN, age=95)) == ""
    assert wire.tag_for(ev("Deutsche Bank opens bitcoin custody to clients", label="", age=10)) == ""  # no label earned: no tag


def test_update_data_and_market_check_tags() -> None:
    assert wire.tag_for(ev("Bybaz reopens withdrawals", kind="update", age=300)) == "UPDATE:"
    assert wire.tag_for(ev("Tesla vs Nvidia over 30 days", kind="data", label=brief.JUST_IN, kicker="MARKETS GONE MAD")) == ""
    assert wire.tag_for(ev("Market check", kind="recap", label=brief.JUST_IN)) == ""


def test_an_exceptional_measured_move_breaks_and_a_routine_one_does_not() -> None:
    big = ev("Bitcoin falls 6.2% in 24 hours", kind="market", label=brief.BREAKING, evidence="price data", age=0, topic="move",
             extra={"kind": "move", "window": "24h", "ratio": 1.4, "change_pct": -6.2})
    assert wire.tag_for(big) == "BREAKING:"
    small = ev("Bitcoin falls 1.1% in an hour", kind="market", label=brief.BREAKING, evidence="price data", age=0, topic="move",
               extra={"kind": "move", "window": "1h", "ratio": 1.1, "change_pct": -1.1})
    assert wire.tag_for(small) == "JUST IN:"
    flush = ev("$140M of longs liquidated in an hour", kind="market", label=brief.BREAKING, evidence="exchange data", age=0, topic="liquidations", usd=140e6)
    assert wire.tag_for(flush) == "BREAKING:"


# ---------------------------------------------------------------- words
LINKS = [Link("CNBC", "https://www.cnbc.com/2026/11/14/china-us-tariffs-xi-trump-meeting.html"), Link("Reuters", "https://www.reuters.com/world/china/talks-2026-11-14/")]


def test_the_caption_is_tag_story_then_one_quiet_line_of_sources_and_the_community() -> None:
    c = Copy(headline="China, US discuss tariffs, farm trade ahead of Xi-Trump meeting", short="",
             context="Beijing has repeatedly urged Washington to implement the $60B tariff-reduction deal. Officials met in Kuala Lumpur.",
             stakes="Soybean futures rose.", question="Is this priced in?")
    html, plain, checked = wire.telegram_caption(c, LINKS, tag="JUST IN:", flag="🇨🇳")
    assert html == ("<b>JUST IN:</b> 🇨🇳 China, US discuss tariffs, farm trade ahead of Xi-Trump meeting. Beijing has repeatedly urged Washington to "
                    "implement the $60B tariff-reduction deal.\n\n"
                    '🔗 <a href="https://www.cnbc.com/2026/11/14/china-us-tariffs-xi-trump-meeting.html">CNBC</a> · '
                    '<a href="https://www.reuters.com/world/china/talks-2026-11-14/">Reuters</a> · <a href="https://t.me/thepricechat">@thepricechat</a>')
    assert plain.endswith("Source: CNBC · Reuters · @thepricechat") and "@" not in checked and checked.endswith("Source: CNBC · Reuters")
    assert "Is this priced in?" not in html and "Soybean" not in html  # one or two sentences: no question, no third sentence


def test_a_second_sentence_only_when_it_adds_a_figure_or_the_headline_is_very_short() -> None:
    c = Copy(headline="Deutsche Bank's bitcoin and ether custody is open to clients, bank says", short="",
             context="The service went live for institutional clients in Germany. It was first announced last year.")
    assert wire.story_sentences(c) == ["Deutsche Bank's bitcoin and ether custody is open to clients, bank says."]
    c.context = "The bank said 12 clients signed up in the first week."
    assert len(wire.story_sentences(c)) == 2
    assert wire.story_sentences(Copy(headline="Bybaz halts withdrawals", short="", context="The exchange said it was investigating."))[1] == \
        "The exchange said it was investigating."


def test_a_long_headline_loses_its_trailing_clause_never_gains_a_word() -> None:
    long = ("Bitcoin breaks below $70,000 as liquidations sweep through leveraged longs across every major exchange after a weekend of thin "
            "trading and heavy selling")
    head = wire.story_sentences(Copy(headline=long, short=""))[0]
    assert len(head) <= wire.HEAD_MAX + 1 and set(head.rstrip(".").split()) <= set(long.split())


def test_the_x_post_is_the_same_words_and_one_link() -> None:
    c = Copy(headline="China, US discuss tariffs, farm trade ahead of Xi-Trump meeting", short="",
             context="Beijing has repeatedly urged Washington to implement the $60B tariff-reduction deal.")
    x = wire.x_caption(c, LINKS, tag="JUST IN:", flag="🇨🇳")
    assert x.startswith("JUST IN: 🇨🇳 China, US discuss tariffs") and x.endswith("CNBC: https://www.cnbc.com/2026/11/14/china-us-tariffs-xi-trump-meeting.html")
    assert "@" not in x and network.x_length(x) <= 280
    assert wire.x_caption(c, LINKS, with_links=False).endswith("Via CNBC · Reuters")


def test_text_only_with_no_sources_still_signs() -> None:
    html, plain, checked = wire.telegram_caption(Copy(headline="Bitcoin vs gold over the past year: −31.2% against −7.4%", short=""), [])
    assert html.endswith('<a href="https://t.me/thepricechat">@thepricechat</a>') and plain.endswith("@thepricechat") and "@" not in checked


# ---------------------------------------------------------------- the wording gate (on top of every existing gate)
SRC = "Bitcoin fell 5.4% to $69,800 as more than $1 billion of leveraged longs were liquidated in 12 hours, CoinDesk reported."


@pytest.mark.parametrize("story,problem", [
    ("Bitcoin suffers massive drop as longs are liquidated.", "hype"),  # the sources never said massive
    ("Bitcoin sees the biggest liquidation wave in history.", "superlative"),
    ("Bitcoin crashes as longs are liquidated.", "loaded move word"),  # a crash word without a figure
    ("Bitcoin explodes lower, 5.4% down.", "hype"),  # a figure, but the sources never said explodes
])
def test_hype_superlatives_and_loaded_words_the_sources_dont_support_are_held(story: str, problem: str) -> None:
    assert any(problem in p for p in checks.wording_problems(story, SRC))


def test_the_sources_own_words_with_their_figures_pass() -> None:
    assert checks.wording_problems("Bitcoin fell 5.4% to $69,800 as more than $1 billion of longs were liquidated in 12 hours.", SRC) == []
    said = "Gold hit a record high of $4,380 an ounce, a massive 40% gain this year, Reuters reported."
    assert checks.wording_problems("Gold hits a record high of $4,380, a massive 40% gain this year.", said) == []
    assert checks.wording_problems("Gold hits a massive high.", said)  # the outlet's word, but the sentence carries no figure
    assert checks.wording_problems("Every exchange halted.", "However, trading resumed.") == []  # 'ever' is not 'every'


def test_the_final_gate_is_unchanged_for_the_other_styles_and_stricter_for_wire() -> None:
    caption = "JUST IN: Bitcoin suffers massive drop as longs are liquidated.\n\nSource: CoinDesk · @thepricechat"
    assert checks.final_gate(caption, SRC) == []  # clean / classic: the same gate as before, nothing added
    assert checks.final_gate(caption, SRC, wire=True) == ["hype the figures don't support: massive"]


@pytest.mark.parametrize("rewrite,held", [
    ("JUST IN: Bitcoin plunges 9% below $65,000 as $3 billion in longs are liquidated.", "figures no source gave"),  # invented figures
    ("JUST IN: Binance $BNB users hit as bitcoin falls 5.4% to $69,800.", "tickers the sources don't establish: $BNB"),  # an invented ticker
    ("BREAKING: Bitcoin posts its biggest drop ever, down 5.4% to $69,800.", "a superlative no source uses"),  # an unsupported superlative
    ("JUST IN: Bitcoin falls 5.4% after Michael Saylor sold, CoinDesk reported.", "people no source names"),  # a name nobody reported
])
def test_a_punchy_rewrite_with_an_unsupported_figure_ticker_superlative_or_name_is_still_blocked(rewrite: str, held: str) -> None:
    caption = f"{rewrite}\n\nSource: CoinDesk · @thepricechat"
    assert any(held in p for p in checks.final_gate(caption, SRC, "", wire=True)), checks.final_gate(caption, SRC, "", wire=True)


def test_a_chart_the_sources_dont_establish_is_still_blocked() -> None:
    caption = "JUST IN: Binance users report delays.\n\nSource: CoinDesk · @thepricechat"
    assert checks.final_gate(caption, "Binance users reported withdrawal delays, CoinDesk said.", "BNB-USD", wire=True) == \
        ["a $BNB price chart the sources don't establish"]


# ---------------------------------------------------------------- the model writes; the gates decide
STORY = SimpleNamespace(source="CoinDesk", link="https://www.coindesk.com/markets/2026/11/13/bitcoin-breaks-below-70000-as-liquidations-sweep",
                        title="Bitcoin breaks below $70,000 as liquidations sweep through leveraged longs", summary=SRC)
STORY2 = SimpleNamespace(source="The Block", link="https://www.theblock.co/news/markets/2026-11-13-bitcoin-drops-below-70000-438600",
                         title="Bitcoin drops below $70,000 as $1 billion of longs are liquidated", summary="Bitcoin dropped below $70,000 on Thursday.")


def _model(draft: dict[str, str]) -> httpx.MockTransport:
    return httpx.MockTransport(lambda r: httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps(draft)}]}))


@pytest.fixture
def with_key(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    from app.security import credentials

    monkeypatch.setattr(credentials, "resolve", lambda name, settings=None: "key" if name == "anthropic_api_key" else None)
    return SimpleNamespace(newsdesk_model="claude-test")


def _news_event() -> NdEvent:
    e = ev(STORY.title, label=brief.JUST_IN, age=30, summary=SRC, told=STORY.title)
    e.sources = [{"name": "CoinDesk", "url": STORY.link, "title": STORY.title}, {"name": "The Block", "url": STORY2.link, "title": STORY2.title}]
    return e


@pytest.mark.parametrize("draft,never", [
    ({"headline": "Bitcoin plunges 9% as $3 billion in longs are wiped out", "context": SRC}, "9%"),  # an invented figure
    ({"headline": "Bitcoin's biggest liquidation ever wipes out longs below $70,000", "context": ""}, "biggest"),  # an unsupported superlative
    ({"headline": "Massive selloff takes Bitcoin below $70,000", "context": ""}, "Massive"),  # hype the figures don't carry
    ({"headline": "Bitcoin breaks below $70,000 as Binance BNB longs are liquidated", "context": ""}, "$BNB"),  # a ticker nobody reported
])
async def test_the_models_punchy_draft_is_refused_and_the_sources_words_go_out(with_key: SimpleNamespace, draft: dict[str, str], never: str) -> None:
    pkg = await network.package(_news_event(), settings=with_key, rows=[STORY, STORY2], transport=_model({"short": "", "stakes": "", "question": "", **draft}),
                                style="wire")
    assert pkg.ok, pkg.checks
    assert never not in pkg.telegram_plain and never not in pkg.x_text and pkg.copy.writer != "model"
    assert pkg.telegram_plain.startswith("JUST IN: Bitcoin $BTC breaks below $70,000 as liquidations sweep through leveraged longs.")
    assert checks.final_gate(pkg.telegram_plain, network.source_text_of(_news_event(), [STORY, STORY2]), wire=True) == []


async def test_a_clean_model_draft_goes_out_in_the_wire_shape(with_key: SimpleNamespace) -> None:
    draft = {"headline": "Bitcoin breaks below $70,000 as $1 billion of longs are liquidated", "short": "", "context": "", "stakes": "", "question": ""}
    pkg = await network.package(_news_event(), settings=with_key, rows=[STORY, STORY2], transport=_model(draft), style="wire")
    assert pkg.ok and pkg.copy.writer == "model"
    # the sources say "more than $1 billion": the model's bare "$1 billion" gets the sources' own qualifier back (checks.restore_qualifiers)
    assert pkg.telegram_html.startswith("<b>JUST IN:</b> Bitcoin $BTC breaks below $70,000 as more than $1B of longs are liquidated.")
    assert pkg.telegram_html.endswith('· <a href="https://t.me/thepricechat">@thepricechat</a>')
    assert any(c["check"] == "wording" and c["passed"] for c in pkg.checks)


# ---------------------------------------------------------------- pictures
def bars(n: int = 60) -> list[list[float]]:
    return [[1_762_900_000.0 + 900 * i, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i] for i in range(n)]


async def test_a_major_price_story_gets_the_real_chart(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    async def fetch(client: Any, symbol: str, end: Any = None) -> list[list[float]]:
        asked.append(symbol)
        return bars()

    monkeypatch.setattr(clean, "fetch_bars", fetch)
    img, meta = await wire.picture(ev("Bitcoin breaks below $70,000 as liquidations sweep through leveraged longs"), now=AT)
    assert asked == ["BTC-USD"] and meta["layout"] == "chart" and meta["symbol"] == "BTC-USD" and meta["style"] == "wire" and img


@pytest.mark.parametrize("headline", [
    "Saudi crude output jumps 26% as Riyadh reports near pre-war production",  # a 26% that isn't a price
    "SpaceX-Tesla merger talks put 30,000 bitcoin back in focus",  # names an asset, reports no move
    "Deutsche Bank's bitcoin and ether custody is open to clients",
])
async def test_no_chart_beside_a_story_that_isnt_about_that_price(headline: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fetch(client: Any, symbol: str, end: Any = None) -> list[list[float]]:
        raise AssertionError("no chart for this story")

    async def none(*a: Any, **k: Any) -> list[Any]:
        return []

    monkeypatch.setattr(clean, "fetch_bars", fetch)
    monkeypatch.setattr(photos, "discover", none)
    img, meta = await wire.picture(ev(headline), now=AT)
    assert img == b"" and meta == {"style": "wire", "kind": "text", "layout": "text"}


async def test_a_measured_reaction_earns_the_chart(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fetch(client: Any, symbol: str, end: Any = None) -> list[list[float]]:
        return bars()

    monkeypatch.setattr(clean, "fetch_bars", fetch)
    e = ev("SpaceX-Tesla merger talks put Tesla shares in focus", viral={"reactions": [{"symbol": "TSLA"}]})
    _, meta = await wire.picture(e, now=AT)
    assert meta["layout"] == "chart" and meta["symbol"] == "TSLA"


async def test_a_public_figure_story_gets_their_licensed_photo_and_nothing_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    async def discover(client: Any, queries: list[str], terms: list[str], **kw: Any) -> list[Any]:
        seen.append(terms)
        return [photos.LicensedPhoto(title="File:Jerome Powell.jpg", url="https://upload.wikimedia.org/powell.jpg", page="https://commons.wikimedia.org/wiki/File:Powell.jpg",
                                     creator="Federal Reserve", provider="wikimedia", licence="Public domain", source="wikimedia", width=1600, height=1200)]

    async def fetch(client: Any, p: Any, min_width: int = 0) -> Image.Image:
        return Image.new("RGB", (1600, 1200), (90, 90, 90))

    monkeypatch.setattr(photos, "discover", discover)
    monkeypatch.setattr(photos, "fetch", fetch)
    img, meta = await wire.picture(ev("Powell says the Fed will hold rates through December"), now=AT)
    assert seen == [["Jerome Powell"]] and meta["layout"] == "photo" and meta["person"] == "Jerome Powell" and img
    seen.clear()
    img, meta = await wire.picture(ev("Iran opens training camp for women in readiness for war"), now=AT)
    assert seen == [] and img == b"" and meta["kind"] == "text"  # no person: no stand-in place or scene, a text post


async def test_a_measured_move_is_its_verified_chart_and_never_an_illustration(monkeypatch: pytest.MonkeyPatch) -> None:
    async def checked(e: Any, chosen: Any, market: Any, now: Any = None) -> tuple[bytes, dict[str, Any]]:
        return b"x", {"number_check": {"ok": True}}

    monkeypatch.setattr(visual, "_chart_for", checked)
    pts = [[1_762_900_000 + 900 * i, 100.0 - i * 0.1] for i in range(40)]
    e = ev("Bitcoin falls 6.2% in 24 hours", kind="market", points=pts, name="Bitcoin", window="24h", data_source="Hyperliquid")
    img, meta = await wire.picture(e, now=AT)
    assert meta["layout"] == "chart" and meta["number_check"] == {"ok": True} and "photo" not in meta and img


async def test_x_redraws_what_telegram_showed_including_no_picture() -> None:
    e = ev("Iran opens training camp for women in readiness for war", visual={"style": "wire", "kind": "text", "layout": "text"})
    assert await visual.for_event(e) == (b"", {"style": "wire", "kind": "text", "layout": "text"})
    e = ev("Bitcoin breaks below $70,000", visual={"style": "wire", "kind": "chart", "layout": "chart", "bars": bars(), "label": "$BTC · 15m · Yahoo Finance"})
    img, meta = await visual.for_event(e)
    assert img and meta["layout"] == "chart" and Image.open(__import__("io").BytesIO(img)).size == (1600, 900)


# ---------------------------------------------------------------- ranking
def test_surprising_consequential_stories_rank_first_and_filler_ranks_lower() -> None:
    assert wire.surprise_bonus(ev("Bybaz halts withdrawals after $40 million hack")) == wire.SURPRISE_MAX
    assert wire.surprise_bonus(ev("Deutsche Bank opens custody to clients")) == 0
    assert wire.filler(ev("Bitcoin price prediction: analysts say $100K could come next")) is not None
    assert wire.filler(ev("Iran launches missiles at Israel")) is None  # a launch of missiles is news, not a product launch
    assert wire.filler(ev("Bitcoin could hit $100K", kind="market")) is None  # our own measured posts are never filler


def test_the_policy_follows_the_style(db: Session) -> None:
    # owner, 2026-11-16: the wire style is the standard and a stored "clean" posts as wire; the daily breaking cap stays the desk's own 2
    p = events.policy({})
    assert p.wire and p.breaking_per_day == 2 and p.breaking_per_6h == 2 and p.breaking_gap == timedelta(minutes=90)
    assert events.policy({"post_style": "clean"}).wire
    q = events.policy({"post_style": "classic"})
    assert not q.wire and q.breaking_per_day == 2 and q.breaking_per_6h == 0
    assert network.state(db)["post_style"] == "wire"
    assert network.configure(db, post_style="clean")["post_style"] == "wire"
    assert network.configure(db, post_style="classic")["post_style"] == "classic"
    assert network.configure(db, post_style="wire")["post_style"] == "wire"


# ---------------------------------------------------------------- the channel's own cases (2026-11-11..14)
@pytest.mark.parametrize("head,reporting,want", [
    ("Saudi Crude Output Jumps 26% as Riyadh Reports Near Pre-War Production",
     "Saudi Arabia told OPEC it raised crude production in October, back near pre-war levels.",
     "Saudi crude output jumps 26% as Riyadh reports near pre-war production"),  # a capital the reporting never repeats keeps its capital
    ("Russian Diesel Tanker Makes U-Turn After Heading to U.S.", "A tanker carrying Russian diesel made a U-turn off New York.",
     "Russian diesel tanker makes U-turn after heading to U.S."),  # the reporting's own spelling, never "u-Turn"
    ("Bitcoin breaks below $70,000 as liquidations sweep", "", "Bitcoin breaks below $70,000 as liquidations sweep"),  # already sentence case
])
def test_title_case_becomes_sentence_case_without_losing_a_name(head: str, reporting: str, want: str) -> None:
    assert wire.headline_case(head, reporting) == want


def test_one_cashtag_per_asset_per_post() -> None:
    c = Copy(headline="SpaceX-Tesla merger talks put 30,000 bitcoin back in focus", short="",
             context="A deal could be announced as soon as December, putting $2.1 billion of bitcoin under one roof.")
    _, plain = wire.lead(c)
    assert plain.count("$BTC") == 1 and plain.startswith("SpaceX-Tesla merger talks put 30,000 bitcoin $BTC back in focus.")


def test_a_flag_only_when_the_headline_leads_with_one_country() -> None:
    assert wire.flag_for(ev("China, US discuss tariffs, farm trade ahead of Xi-Trump meeting"), "JUST IN:") == ""
    assert wire.flag_for(ev("Japan raises rates for the first time since 2007"), "JUST IN:") == "🇯🇵"
    assert wire.flag_for(ev("Japan raises rates for the first time since 2007"), "") == ""  # no tag, no flag


@pytest.mark.parametrize("headline,symbol", [
    ("Oil prices jump 5% after missile strike on Ras Tanura", "BZ=F"),
    ("Gold hits record high above $4,000", "GC=F"),
    ("Saudi crude output jumps 26% as Riyadh reports near pre-war production", None),  # output, not the oil price
    ("Fed cuts rates by 0.25%, the first cut this year", None),  # macro: the S&P 500 stand-in only on a measured reaction
])
def test_commodity_and_macro_charts_need_their_own_price_moving(headline: str, symbol: str | None) -> None:
    got = wire.price_story(ev(headline))
    assert (got[0] if got else None) == symbol


async def test_a_photo_only_when_the_person_leads_the_story(monkeypatch: pytest.MonkeyPatch) -> None:
    async def discover(*a: Any, **k: Any) -> list[Any]:
        raise AssertionError("Xi is mentioned, not the subject: no photo search")

    monkeypatch.setattr(photos, "discover", discover)
    img, meta = await wire.picture(ev("China, US discuss tariffs, farm trade ahead of Xi-Trump meeting"), now=AT)
    assert img == b"" and meta["kind"] == "text"
