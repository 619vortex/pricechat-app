"""The clean post style (app/newsdesk/clean): words, cashtags, labels, the signature, and the picture chosen per story. No network."""
import io
from datetime import datetime, timezone
from typing import Any

import pytest
from PIL import Image

from app.models.newsdesk import NdEvent
from app.newsdesk import checks, clean, network, photos, visual
from app.newsdesk.network import Copy, Link

AT = datetime(2026, 11, 12, 3, 0, tzinfo=timezone.utc)


def ev(headline: str, title: str = "", label: str = "", kind: str = "news", **facts: Any) -> NdEvent:
    e = NdEvent(key="k", kind=kind, title=title or headline, headline=headline, summary="", topic="", label=label, evidence="two outlets",
                category="news", sources=[], facts=facts, released_at=AT)
    e.id = 7
    return e


def bars(n: int = 60, start: float = 100.0) -> list[list[float]]:
    out, p = [], start
    for i in range(n):
        o, p = p, p * (1.004 if i % 3 else 0.993)
        out.append([1_762_900_000.0 + 900 * i, o, max(o, p) * 1.001, min(o, p) * 0.999, p])
    return out


def photo(w: int = 1200, h: int = 1500) -> Image.Image:
    img = Image.new("RGB", (w, h), (40, 90, 160))
    for y in range(h):
        for x in range(0, w, 40):
            img.putpixel((x, y), (200, (x * 7) % 255, (y * 3) % 255))
    return img


# ---------------------------------------------------------------- words
@pytest.mark.parametrize("text,want,tags", [
    # an index decision about the company is not established as a stock story by the sentence: no tag (owner, 2026-11-13)
    ("MSCI to remove Michael Saylor's Strategy from its indexes", "MSCI to remove Michael Saylor's Strategy from its indexes", []),
    ("Michael Saylor's Strategy shares fall 6%", "Michael Saylor's Strategy $MSTR shares fall 6%", ["$MSTR"]),
    ("Nvidia falls 4% after hours", "Nvidia $NVDA falls 4% after hours", ["$NVDA"]),
    ("Bitcoin falls 3% as XRP jumps", "Bitcoin $BTC falls 3% as $XRP jumps", ["$BTC", "$XRP"]),
    ("Mercado Bitcoin to resume withdrawals", "Mercado Bitcoin to resume withdrawals", []),  # a company's name is not the asset
    ("Binance’s $6.3B hack leaves questions", "Binance’s $6.3B hack leaves questions", []),  # never inside a possessive
    ("Strategy wins a design award", "Strategy wins a design award", []),  # "Strategy" alone is just a word
    ("Nvidia $NVDA falls again", "Nvidia $NVDA falls again", ["$NVDA"]),  # never twice
])
def test_cashtags_only_for_assets_the_sentence_names(text: str, want: str, tags: list[str]) -> None:
    assert clean.cashtag(text) == (want, tags)


def test_labels_are_the_ones_the_desk_earned() -> None:
    assert clean.prefix_for(ev("x", label="🚨 BREAKING")) == "BREAKING:"
    assert clean.prefix_for(ev("x", label="⚡ JUST IN")) == "JUST IN:"
    assert clean.prefix_for(ev("x", label="🔴 ALERT")) == "ALERT:"
    assert clean.prefix_for(ev("x")) == ""  # no label earned: no label shown
    assert clean.prefix_for(ev("x", kind="update")) == "UPDATE:"
    assert clean.prefix_for(ev("x", kind="data", kicker="MARKETS GONE MAD")) == "MARKETS GONE MAD:"


LINKS = [Link("Reuters", "https://www.reuters.com/markets/nvidia-falls"), Link("CNBC", "https://www.cnbc.com/2026/11/12/nvidia.html")]


def test_the_telegram_caption_is_label_story_sources_and_the_community() -> None:
    c = Copy(headline="Nvidia falls 4% after hours on weaker data-centre guidance", short="Nvidia falls 4%",
             context="Nvidia fell 4% after hours on weaker data-centre guidance. Revenue guidance came in at $54 billion, below the $56 billion analysts expected.",
             stakes="Chipmakers fell with it in late trading.", question="Is the AI trade cooling?")
    html, plain, checked = clean.telegram_caption(c, LINKS, prefix="JUST IN:")
    assert html.startswith("<b>JUST IN:</b> Nvidia $NVDA falls 4% after hours on weaker data-centre guidance. Revenue guidance came in at $54 billion")
    assert "Chipmakers fell with it" in html and html.count("Nvidia fell 4%") == 0  # the re-telling of the headline is dropped
    assert "Is the AI trade cooling?" not in html  # no question, no "why it matters", no byline: 1-3 sentences
    assert '🔗 <a href="https://www.reuters.com/markets/nvidia-falls">Reuters</a> · <a href="https://www.cnbc.com/2026/11/12/nvidia.html">CNBC</a>' in html
    assert html.endswith('<a href="https://t.me/thepricechat">@thepricechat</a>') and plain.endswith("@thepricechat")
    assert "@" not in checked and "Source: Reuters · CNBC" in checked  # the checks read the story, the signature is ours
    assert "PRICE CHAT NEWS" not in html.upper() and "Verified across" not in html


def test_the_x_post_is_the_hook_and_one_link() -> None:
    c = Copy(headline="Nvidia falls 4% after hours on weaker data-centre guidance", short="",
             context="Revenue guidance came in at $54 billion, below the $56 billion analysts expected.")
    x = clean.x_caption(c, LINKS, prefix="JUST IN:")
    assert x.startswith("JUST IN: Nvidia $NVDA falls 4% after hours") and x.endswith("Reuters: https://www.reuters.com/markets/nvidia-falls")
    assert network.x_length(x) <= 280 and "@thepricechat" not in x and "cnbc.com" not in x
    assert clean.x_caption(c, LINKS, with_links=False).endswith("Via Reuters · CNBC")  # links off on X: names only


def test_long_stories_fit_a_photo_caption() -> None:
    c = Copy(headline="A " * 200 + "headline", short="", context=("A long sentence about figures, again and again. " * 40))
    html, _, _ = clean.telegram_caption(c, LINKS * 3)
    assert len(html) <= 1024 or html.count(".") <= 2


def test_title_case_headlines_read_as_sentences_and_names_stay() -> None:
    c = Copy(headline="Nvidia Hires Wachtell Lipton to Investigate CEO Jensen Huang’s Affair With Chinese Executive", short="",
             context="Wachtell partner Joon H. Kim is leading the inquiry into Jensen Huang.")
    first = clean.sentences_of(c, "Nvidia has hired the law firm Wachtell Lipton.")[0]
    assert first == "Nvidia hires Wachtell Lipton to investigate CEO Jensen Huang’s affair with Chinese executive."


def test_the_clean_caption_passes_the_posting_checks() -> None:
    c = Copy(headline="Nvidia falls 4% after hours", short="", context="Revenue guidance came in at $54 billion.")
    _, _, checked = clean.telegram_caption(c, LINKS, prefix="JUST IN:")
    r = checks.check_post(checked, source_text="Nvidia falls 4% after hours. Revenue guidance came in at $54 billion.", source_names=["Reuters"],
                          links=[x.url for x in LINKS], max_len=1024)
    assert r.ok, r.reason


# ---------------------------------------------------------------- pictures
def test_collage_photo_and_chart_side_by_side_without_a_headline_on_it() -> None:
    data, meta = clean.collage(photo(), "Photo: Jane Doe / Wikimedia Commons, CC BY-SA 4.0", bars(), "$NVDA · 15m · Yahoo Finance")
    img = Image.open(io.BytesIO(data))
    assert img.size == (1600, 1000) and meta["layout"] == "collage"
    seam = img.crop((795, 0, 805, 1000)).convert("L")
    assert max(seam.getdata()) < 20  # the black seam between the two panels


def test_ai_illustration_says_so_on_the_picture() -> None:
    data, meta = clean.ai_picture(photo(1920, 1080), "Qwen-Image")
    assert Image.open(io.BytesIO(data)).size == (1600, 900) and meta["layout"] == "ai"


def test_photo_credit_carries_the_licence() -> None:
    assert clean.photo_credit({"credit": "Jane Doe / Wikimedia Commons", "licence": "CC BY-SA 4.0"}) == "Photo: Jane Doe / Wikimedia Commons, CC BY-SA 4.0"
    assert clean.photo_credit({"credit": "Photo: NASA", "licence": "Public domain"}) == "Photo: NASA, Public domain"


def test_chart_subject_and_people() -> None:
    assert clean.chart_subject("Nvidia falls 4%")[:2] == ("NVDA", "$NVDA")
    assert clean.chart_subject("Binance’s hack") is None  # an exchange's hack is not a BNB price story: no BNB chart (2026-11-13)
    assert clean.chart_subject("Nvidia’s shares fall 4%")[0] == "NVDA"  # a possessive still names the asset when its shares are the story
    assert clean.chart_subject("Oil jumps after missile strike")[0] == "BZ=F"
    assert clean.chart_subject("US inflation higher than expected")[:3] == ("^GSPC", "S&P 500", "S&P 500")
    assert clean.chart_subject("Mercado Bitcoin reopens") is None
    assert clean.person_subject(ev("First day of Ukraine peace talks constructive, Zelensky says")) == "Volodymyr Zelenskyy"
    # owner, 2026-11-12: a controversy may show the public figure it is about; never a private person, never on violence or death
    assert clean.person_subject(ev("Nvidia hires Wachtell to investigate CEO Jensen Huang's affair")) == "Jensen Huang"
    assert clean.person_subject(ev("Trump sued by New York over business records")) == "Donald Trump"
    assert clean.person_subject(ev("John Smith charged with fraud in Ohio")) is None  # not a public figure
    assert clean.person_subject(ev("Putin says the war will continue after the attack")) is None  # violence: no face


@pytest.fixture
def found(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def discover(client: Any, queries: list[str], terms: list[str], **kw: Any) -> list[Any]:
        calls.append({"queries": queries, "terms": terms, **kw})
        return [photos.LicensedPhoto(url="https://upload.wikimedia.org/x.jpg", page="https://commons.wikimedia.org/wiki/File:X.jpg", title="File:X.jpg",
                                     creator="Jane Doe", licence="CC BY-SA 4.0", licence_url="https://creativecommons.org/licenses/by-sa/4.0/",
                                     provider="wikimedia", source="wikimedia", width=1200, height=1500)]

    async def fetch(client: Any, p: Any, min_width: int = 0) -> Image.Image:
        return photo()

    monkeypatch.setattr(photos, "discover", discover)
    monkeypatch.setattr(photos, "fetch", fetch)
    return calls


async def test_a_story_naming_an_asset_with_a_subject_photo_is_a_collage(found: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_bars(client: Any, symbol: str, end: Any = None) -> list[list[float]]:
        assert symbol == "NVDA"
        return bars()

    monkeypatch.setattr(clean, "fetch_bars", fake_bars)
    data, meta = await clean.picture(ev("Nvidia falls 4% after Jensen Huang's keynote"), now=AT)
    assert meta["layout"] == "collage" and meta["style"] == "clean" and meta["symbol"] == "NVDA" and len(meta["bars"]) == 60
    assert found[0]["terms"] == ["Jensen Huang"] and found[0]["panel"] is True  # the person the story is about, in a portrait panel
    assert meta["licence"] and "CC BY-SA" in meta["credit_line"]


async def test_x_redraws_exactly_what_telegram_showed(found: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_bars(client: Any, symbol: str, end: Any = None) -> list[list[float]]:
        return bars()

    async def download(url: str, transport: Any = None) -> Image.Image:
        return photo()

    monkeypatch.setattr(clean, "fetch_bars", fake_bars)
    monkeypatch.setattr(clean, "download", download)
    e = ev("Nvidia falls 4% after Jensen Huang's keynote")
    data, meta = await clean.picture(e, now=AT)
    e.facts = {**e.facts, "visual": meta}
    again, meta2 = await visual.for_event(e)  # X: no database, no style setting: the stored style decides
    assert again == data and meta2 is meta or meta2 == meta


async def test_a_controversy_shows_its_public_figure_only_in_a_neutral_photo(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def shot(title: str) -> Any:
        return photos.LicensedPhoto(url=f"https://upload.wikimedia.org/{title}.jpg", page=f"https://commons.wikimedia.org/wiki/File:{title}.jpg",
                                    title=f"File:{title}.jpg", creator="Jane Doe", licence="CC BY 2.0", provider="wikimedia", source="wikimedia",
                                    width=1200, height=1500)

    async def discover(client: Any, queries: list[str], terms: list[str], **kw: Any) -> list[Any]:
        calls.append({"terms": terms, **kw})
        if terms == ["Jensen Huang"]:  # an arrest-style or courtroom photo is never used, even licensed
            return [shot("Jensen Huang leaving court"), shot("Jensen Huang at Computex 2025")]
        return []

    async def fetch(client: Any, p: Any, min_width: int = 0) -> Image.Image:
        return photo()

    monkeypatch.setattr(photos, "discover", discover)
    monkeypatch.setattr(photos, "fetch", fetch)
    _, meta = await clean.picture(ev("Nvidia hires Wachtell Lipton to investigate CEO Jensen Huang's affair"), now=AT)
    assert calls[0]["terms"] == ["Jensen Huang"] and calls[0]["sensitive"] is False  # the subject's own search
    assert meta["title"] == "File:Jensen Huang at Computex 2025.jpg" and meta["layout"] == "photo"  # neutral, not the courtroom one
    calls.clear()
    await clean.picture(ev("John Smith charged with fraud at Ohio exchange"), now=AT)
    assert all(c["sensitive"] for c in calls) and not any(c["terms"] == ["John Smith"] for c in calls)  # a private person: never


async def test_no_photo_but_prices_is_the_chart(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_bars(client: Any, symbol: str, end: Any = None) -> list[list[float]]:
        return bars()

    monkeypatch.setattr(clean, "fetch_bars", fake_bars)
    data, meta = await clean.picture(ev("Bitcoin falls 3% after a Mercado Bitcoin outage"), now=AT)
    assert meta["layout"] == "chart" and meta["symbol"] == "BTC-USD" and Image.open(io.BytesIO(data)).size == (1600, 900)


async def test_nothing_real_uses_the_gpu_illustration_labelled(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.newsdesk import render_queue

    monkeypatch.setattr(render_queue, "background_for", lambda db, eid: (photo(1920, 1080), {"model": "Qwen/Qwen-Image-2512", "job": 3}))
    data, meta = await clean.picture(ev("Mercado Bitcoin reopens after hack"), now=AT, db=object())
    assert meta["layout"] == "ai" and meta["ai_illustration"] is True and meta["render_job"] == 3


async def test_a_data_post_keeps_its_comparison_card() -> None:
    from app.newsdesk import datapost

    post = datapost.DataPost(kind="battle", subject="TSLA:NVDA:30", headline="Tesla vs Nvidia over the past 30 days: +3.4% against −23.2%",
                             lines=["Tesla +3.4%", "Nvidia −23.2%"], interest=62, why={}, series={"TSLA": [[0, 100.0], [1, 103.4]], "NVDA": [[0, 100.0], [1, 76.8]]},
                             bars=[], symbols=["TSLA", "NVDA"], as_of=AT.isoformat())
    data, meta = await clean.picture(ev(post.headline, kind="data", data=post.out()), now=AT)
    assert meta["layout"] == "data" and meta["style"] == "clean"


def test_style_setting_defaults_to_clean_and_can_go_back(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(network, "DEFAULT_STYLE", "clean")
    monkeypatch.setattr(network, "CLEAN_RETIRED", False)  # the retired clean style, tested as it was (Admin can no longer pick it)
    assert network.state(db)["post_style"] == "clean"
    assert network.configure(db, post_style="classic")["post_style"] == "classic"
    assert network.configure(db, post_style="poster")["post_style"] == "classic"  # only the two known styles
    assert network.configure(db, post_style="clean")["post_style"] == "clean"


def test_a_chart_only_beside_a_story_about_that_price_or_a_measured_reaction() -> None:
    assert clean.charted_story(ev("Nvidia falls 4% after hours"), "NVDA")
    assert not clean.charted_story(ev("Samsung Wallet launches USDC on Solana"), "SOL-USD")  # a chart there would imply the story moved SOL
    assert clean.charted_story(ev("Samsung Wallet launches USDC on Solana", viral={"reactions": [{"symbol": "SOL-USD", "pct": -3.1}]}), "SOL-USD")
    assert not clean.charted_story(ev("US consumer prices rose 0.4% in October"), "^GSPC")  # consumer prices are not the S&P 500
    assert clean.charted_story(ev("US consumer prices rose 0.4%", viral={"reactions": [{"symbol": "^GSPC", "pct": -1.2}]}), "^GSPC")


async def test_a_product_story_shows_the_photo_alone(found: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_bars(client: Any, symbol: str, end: Any = None) -> list[list[float]]:
        raise AssertionError("no prices are read for a story that isn't about a price")

    monkeypatch.setattr(clean, "fetch_bars", fake_bars)
    _, meta = await clean.picture(ev("Nvidia opens a new campus in Taipei"), now=AT)
    assert meta["layout"] == "photo" and "bars" not in meta


def test_a_credit_the_font_cannot_draw_moves_to_the_caption() -> None:
    meta = {"credit": "Photo: 颐园居 / Wikimedia Commons (CC BY-SA 4.0)", "licence": "CC BY-SA 4.0", "page": "https://commons.wikimedia.org/wiki/File:NYSE.jpg",
            "photo": {"source": "wikimedia"}}
    assert clean.image_credit(meta) == "Photo: Wikimedia Commons, CC BY-SA 4.0"
    html, plain = clean.with_credit('Story.\n\n<a href="https://t.me/thepricechat">@thepricechat</a>', "Story.\n\n@thepricechat", meta)
    assert '📷 <a href="https://commons.wikimedia.org/wiki/File:NYSE.jpg">Photo: 颐园居 / Wikimedia Commons (CC BY-SA 4.0)</a>' in html
    assert html.endswith("@thepricechat</a>") and plain.endswith("@thepricechat")
    latin = {"credit": "Photo: Jane Doe / Wikimedia Commons (CC BY-SA 4.0)", "licence": "CC BY-SA 4.0"}
    assert clean.image_credit(latin) == latin["credit"] and clean.with_credit("x", "y", latin) == ("x", "y")


async def test_a_market_move_gets_its_markets_photo_beside_the_verified_chart(found: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    async def checked(e: Any, chosen: Any, market: Any, now: Any) -> Any:
        return b"", {"number_check": {"ok": True}}

    monkeypatch.setattr(visual, "_chart_for", checked)  # the series check itself is visual.verify_series's own tests
    pts = [[1_762_900_000 + 900 * i, 7600 - 3 * i] for i in range(97)]
    e = ev("S&P 500 falls 3.8% in 24 hours", kind="market", points=pts, name="S&P 500", window="24h", data_source="Yahoo Finance")
    data, meta = await clean.picture(e, now=AT)
    assert meta["layout"] == "collage" and meta["number_check"] == {"ok": True} and len(meta["bars"]) == 96
    assert found[0]["terms"] == ["stock exchange"]  # the New York Stock Exchange for an S&P 500 move


# ---------------------------------------------------------------- tickers only where the sentence establishes the asset (2026-11-13)
# Published 2026-11-13: "...$1.2B of North Korea's stolen Binance $BNB crypto". Binance being involved never means BNB was involved.
@pytest.mark.parametrize("text", [
    "US seizes $1.2B of North Korea's stolen Binance crypto",
    "North Korean hackers launder loot from the $6.3B Binance hack through memecoins",
    "Binance CFO steps down after record hack",
    "Binance fined $4.3B by US regulators",
    "Coinbase suffers a six-hour outage as traders pile in",
    "Coinbase sued by the SEC over staking",
    "Hackers drain $40M from Hyperliquid vault",
    "Nvidia hires Wachtell to investigate CEO Jensen Huang's affair",
    "Tesla recalls 200,000 cars over steering fault",
    "Apple unveils a new iPhone",
    "Kraken and Binance freeze accounts tied to the exploit",
])
def test_a_company_or_exchange_name_alone_never_brings_its_ticker(text: str) -> None:
    out, tags = clean.cashtag(text)
    assert (out, tags) == (text, [])
    assert clean.chart_subject(text) is None


@pytest.mark.parametrize("text,want,tags", [
    ("BNB falls 5% after the Binance hack", "$BNB falls 5% after the Binance hack", ["$BNB"]),  # the token itself is the subject
    ("Binance's BNB token hits a record", "Binance's $BNB token hits a record", ["$BNB"]),
    ("Coinbase shares fall 8% after the outage", "Coinbase $COIN shares fall 8% after the outage", ["$COIN"]),
    ("Coinbase falls 8% after hours", "Coinbase $COIN falls 8% after hours", ["$COIN"]),
    ("Hyperliquid's HYPE token jumps 12%", "Hyperliquid's $HYPE token jumps 12%", ["$HYPE"]),
    ("Hackers steal $40M in Ether from Bybit", "Hackers steal $40M in Ether $ETH from Bybit", ["$ETH"]),  # the stolen asset is named
    ("Exchange hack: $30M of Solana drained", "Exchange hack: $30M of Solana $SOL drained", ["$SOL"]),
    ("Shares of Tesla slide after the recall", "Shares of Tesla $TSLA slide after the recall", ["$TSLA"]),
])
def test_tickers_only_where_the_sentence_establishes_the_asset(text: str, want: str, tags: list[str]) -> None:
    assert clean.cashtag(text) == (want, tags)


def test_the_published_binance_story_caption_carries_no_bnb() -> None:
    c = Copy(headline="US seizes $1.2B of North Korea's stolen Binance crypto", short="US seizes $1.2B",
             context="Prosecutors said the funds were stolen in the Binance hack.", stakes="", question="")
    html, plain, _ = clean.telegram_caption(c, LINKS, prefix="BREAKING:")
    assert "$BNB" not in html and "$BNB" not in plain and "BNB" not in plain
    assert "$BNB" not in clean.x_caption(c, LINKS, prefix="BREAKING:")


def test_classic_brief_tickers_follow_the_same_rule() -> None:
    from app.newsdesk import brief

    assert brief.tickers("US seizes $1.2B of North Korea's stolen Binance crypto") == []
    assert brief.tickers("Coinbase sued by the SEC over staking") == []
    assert brief.tickers("Hackers drain $40M from Hyperliquid vault") == []
    assert brief.tickers("Coinbase shares fall 8% after the outage") == ["COIN"]
    assert brief.tickers("BNB falls 5% after the Binance hack") == ["BNB"]
    assert brief.tickers("Hackers steal $40M in Ether from Bybit") == ["ETH"]


# ---------------------------------------------------------------- the final-output gate (owner, 2026-11-13)
def test_final_gate_holds_a_caption_whose_ticker_the_sources_dont_establish() -> None:
    from app.newsdesk import checks

    src = "The US Justice Department seized $1.2 billion stolen by North Korean hackers in the Binance hack."
    assert checks.final_gate("BREAKING: US seizes $1.2B of North Korea's stolen Binance $BNB crypto", src) == [
        "tickers the sources don't establish: $BNB"]
    assert checks.final_gate("BREAKING: US seizes $1.2B of North Korea's stolen Binance crypto", src) == []
    assert checks.final_gate("BREAKING: US seizes $1.2B of North Korea's stolen Binance crypto", src, "BNB-USD") == [
        "a $BNB price chart the sources don't establish"]
    # established by the sources: allowed
    assert checks.final_gate("$BNB falls 5% after the hack", "BNB fell 5% after the Binance hack", "BNB-USD") == []
    assert checks.final_gate("Hackers took $40M in Ether $ETH", "Hackers stole $40 million in Ether from the exchange") == []
    assert checks.final_gate("Coinbase $COIN shares fall 8%", "Coinbase shares fell 8% on Thursday") == []
    assert checks.final_gate("Coinbase $COIN hit by outage", "Coinbase was down for six hours") == ["tickers the sources don't establish: $COIN"]


def test_check_post_fails_an_unsupported_ticker_and_dollar_amounts_are_not_tickers() -> None:
    from app.newsdesk import checks

    src = "Reuters: The US seized $1.2 billion stolen in the Binance hack."
    bad = checks.check_post("US seizes $1.2B stolen in the Binance $BNB hack. Reuters", source_text=src, source_names=["Reuters"])
    assert not bad.ok and any(c["check"] == "tickers" and not c["passed"] for c in bad.checks)
    good = checks.check_post("US seizes $1.2B stolen in the Binance hack. Reuters", source_text=src, source_names=["Reuters"])
    assert next(c for c in good.checks if c["check"] == "tickers")["passed"]


def test_final_gate_checks_figures_people_accusations_and_quotes() -> None:
    from app.newsdesk import checks

    src = ("The US Justice Department said on Thursday it seized $1.2 billion in cryptocurrency stolen by North Korean hackers in the "
           "Binance hack. \"We will follow the money wherever it goes,\" Attorney General Pam Bondi said.")
    good = ("BREAKING: US seizes $1.2B of North Korea's stolen Binance crypto. \"We will follow the money wherever it goes,\" Pam Bondi said."
            "\n\n🔗 Reuters\n\nPhoto: Jane Doe / Wikimedia Commons, CC BY-SA 4.0\n\n@thepricechat")
    assert checks.final_gate(good, src) == []  # the credit's "4.0" and the photographer are not claims
    assert checks.final_gate("US seizes $2.4B of North Korea's stolen crypto", src) == ["figures no source gave: 2.4"]
    assert checks.final_gate("US seizes $1.2B stolen by hackers, Elon Musk says", src) == ["people no source names: Elon Musk"]
    assert checks.final_gate("Binance charged with laundering $1.2B for North Korea", src) == [
        "accusations or legal actions no source makes: launder, charge"]
    assert checks.final_gate("\"Binance helped North Korea hide the money,\" officials said", src) == [
        "quotations not in the sources: Binance helped North Korea hide the money,"]


def test_final_gate_story_excludes_sources_credit_and_signature() -> None:
    from app.newsdesk import checks

    cap = "Nvidia shares fall 4%.\n\nSource: Reuters · CNBC\n\nPhoto: John Smith / Getty Images 2024\n\n@thepricechat"
    assert checks.story_of(cap) == "Nvidia shares fall 4%."
