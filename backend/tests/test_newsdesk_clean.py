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
    ("MSCI to remove Michael Saylor's Strategy from its indexes", "MSCI to remove Michael Saylor's Strategy $MSTR from its indexes", ["$MSTR"]),
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
    assert clean.chart_subject("Binance’s hack")[0] == "BNB-USD"  # a possessive still names the asset for the chart
    assert clean.chart_subject("Oil jumps after missile strike")[0] == "BZ=F"
    assert clean.chart_subject("US inflation higher than expected")[:3] == ("^GSPC", "S&P 500", "S&P 500")
    assert clean.chart_subject("Mercado Bitcoin reopens") is None
    assert clean.person_subject(ev("First day of Ukraine peace talks constructive, Zelensky says")) == "Volodymyr Zelenskyy"
    assert clean.person_subject(ev("Nvidia hires Wachtell to investigate CEO Jensen Huang's affair")) is None  # sensitive: never a face


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


async def test_a_sensitive_story_never_searches_for_the_person(found: list[dict[str, Any]]) -> None:
    await clean.picture(ev("Nvidia hires Wachtell Lipton to investigate CEO Jensen Huang's affair"), now=AT)
    assert all("Jensen Huang" not in c["terms"] for c in found) and all(c["sensitive"] for c in found)


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
