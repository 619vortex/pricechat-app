"""Photography first (owner, 2026-11-19, second brief: "For gold news, show GOLD. For Bitcoin, show BITCOIN. For Microsoft, show
MICROSOFT. For oil, show OIL ... NOT BLOOMBERG TERMINAL SCREENSHOTS").

The library's Commons records are stubbed with their real titles, authors and licences (read 2026-11-19); no network."""
import io
import math
from datetime import datetime, timezone
from typing import Any

import pytest
from PIL import Image, ImageDraw

from app.models.newsdesk import NdEvent
from app.newsdesk import clean, editor, photography, photos, visual

AT = datetime(2026, 11, 19, 0, 1, tzinfo=timezone.utc)


def ev(headline: str, kind: str = "news", key: str = "k", **facts: Any) -> NdEvent:
    e = NdEvent(key=key, kind=kind, title=headline, headline=headline, summary="", topic="", label="", evidence="two outlets", category="news",
                sources=[{"name": "CNBC", "url": "https://www.cnbc.com/x"}], facts=facts, released_at=AT)
    e.id = 195
    return e


def rec(title: str, creator: str, licence: str, w: int = 4000, h: int = 2600) -> photos.LicensedPhoto:
    slug = title.removeprefix("File:").replace(" ", "_")
    return photos.LicensedPhoto(title=title, url=f"https://upload.wikimedia.org/thumb/{slug}/2560px-{slug}", page=f"https://commons.wikimedia.org/wiki/{title}",
                                creator=creator, provider="wikimedia", source="wikimedia", licence=licence,
                                licence_url="https://creativecommons.org/licenses/by/2.0/", width=w, height=h)


REAL = {  # the library's first files, as Commons describes them
    "gold": [rec("File:Gold nugget (Australia) 4 (16848647509).jpg", "James St. John", "CC BY 2.0", 3531, 2278),
             rec("File:Gold bar of Banque de France.jpg", "Ibex73", "CC BY 4.0", 3686, 1573)],
    "microsoft": [rec("File:Microsoft Store @ Metropolis (32458694837).jpg", "GoToVan from Vancouver, Canada", "CC BY 2.0", 5760, 3840),
                  rec("File:Microsoft Taiwan Corporation headquarters plate 20161126.jpg", "othree", "CC BY 2.0", 4608, 3072)],
    "bitcoin": [rec('File:Bitcoin "challenge coin".jpg', "Isokivi", "CC BY-SA 3.0", 2048, 1536)],
    "oil": [rec("File:LostHillsPumpjacksSunset.JPG", "Arne Hückelheim", "CC BY-SA 3.0", 3888, 2592)],
    "nvidia": [rec("File:Nvidia sign.jpg", "Will Buckner", "CC BY 2.0", 3711, 3020)],
}


def pic(w: int = 3000, h: int = 2000, dark: bool = True) -> Image.Image:
    import random

    rnd = random.Random(w + h)
    img = Image.new("RGB", (w, h), (10, 10, 12) if dark else (230, 225, 215))
    small = Image.new("RGB", (w // 6, h // 6))
    small.putdata([(rnd.randint(150, 255), rnd.randint(100, 200), rnd.randint(0, 60)) for _ in range(small.width * small.height)])
    img.paste(small.resize((w // 2, h // 2)), (w // 4, h // 4))
    return img


@pytest.fixture
def library(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    fetched: list[str] = []

    async def records(client: Any, subject: Any) -> list[Any]:
        return list(REAL.get(subject.key, []))

    async def fetch(client: Any, p: Any, min_width: int = 0) -> Image.Image:
        fetched.append(p.title)
        return pic()

    async def no_search(*a: Any, **k: Any) -> list[Any]:
        raise AssertionError("the library had the subject: no search")

    monkeypatch.setattr(photography, "_records", records)
    monkeypatch.setattr(photos, "fetch", fetch)
    monkeypatch.setattr(photos, "discover", no_search)
    return fetched


def gold_post() -> dict[str, Any]:
    closes = [[1_783_000_000 + 86400 * i, 3900 * (1 + 0.01 * math.sin(i))] for i in range(130)]
    return {"kind": "anomaly", "subject": "anomaly:GC=F", "headline": "Gold's biggest one-day drop since March 2026: −3.6%", "lines": [], "interest": 80,
            "why": {}, "series": {"GC=F": closes}, "bars": [], "symbols": ["GC=F"], "as_of": AT.isoformat(), "table": []}


# ------------------------------------------------------------------------------------------------ the two posts the owner showed
@pytest.mark.parametrize("platform,size", [("telegram", (1280, 1024)), ("x", (1600, 900))])
async def test_post_194_gold_news_shows_gold(library: list[str], platform: str, size: tuple[int, int]) -> None:
    e = ev(gold_post()["headline"], kind="data", data=gold_post())
    data, meta = await visual.for_event(e, style="clean", now=AT, platform=platform)
    assert meta["kind"] == "photo" and meta["library"] == "gold" and meta["title"] == "File:Gold nugget (Australia) 4 (16848647509).jpg"
    assert Image.open(io.BytesIO(data)).size == size and "template" not in meta  # a photograph, no chart, no giant percentage
    assert meta["credit_line"] == "Photo: James St. John / Wikimedia Commons (CC BY 2.0)" and meta["editor"]["ok"] is True


async def test_post_195_microsoft_news_shows_microsoft_not_a_treehouse(library: list[str]) -> None:
    e = ev("Microsoft had rallied back to record Highs. Then came the election and Outlook outage.")
    data, meta = await visual.for_event(e, style="clean", now=AT)
    assert meta["kind"] == "photo" and meta["title"] == "File:Microsoft Store @ Metropolis (32458694837).jpg" and "treehouse" not in data.decode("latin-1").lower()
    e.facts = {**e.facts, "visual": meta}
    xdata, xmeta = await visual.for_event(e, now=AT, platform="x")  # X: the same photograph in its own 16:9 frame
    assert Image.open(io.BytesIO(xdata)).size == (1600, 900) and xmeta["title"] == meta["title"]


@pytest.mark.parametrize("headline,key", [
    ("Bitcoin breaks below $70,000 as liquidations sweep through leveraged longs ($BTC -5.4% in 24H)", "bitcoin"),  # post 173: bare candles
    ("Saudi crude output jumps 26% as Riyadh reports near pre-war production", "oil"),  # post 163: the Riyadh skyline
    ("Nvidia stock up 3% as earnings beat expectations", "nvidia"),  # post 190: campus aerial + candles
    ("SpaceX-Tesla merger talks put 30,000 bitcoin back in focus", None),  # Tesla is named first: no Bitcoin stand-in (no Tesla records here)
])
async def test_the_subject_the_headline_names_first_is_the_photograph(library: list[str], monkeypatch: pytest.MonkeyPatch, headline: str, key: str | None) -> None:
    if key is None:
        async def none(*a: Any, **k: Any) -> list[Any]:
            return []

        monkeypatch.setattr(photos, "discover", none)
    data, meta = await clean.picture(ev(headline), now=AT)
    if key is None:
        assert meta["kind"] == "text"  # Tesla's own photo, or nothing: never Bitcoin's
    else:
        assert meta["kind"] == "photo" and meta["library"] == key


async def test_a_market_move_keeps_its_numbers_check_and_shows_the_asset(library: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    pts = [[1_762_900_000 + 900 * i, 7600 - 3 * i] for i in range(97)]
    seen: list[str] = []

    async def checked(e: Any, chosen: Any, market: Any, now: Any) -> Any:
        seen.append("checked")
        return b"", {"number_check": {"ok": True}}

    monkeypatch.setattr(visual, "_chart_for", checked)
    e = ev("Gold falls 3.8% in 24 hours", kind="market", points=pts, name="Gold", coin="GOLD", window="24h", data_source="Yahoo Finance")
    data, meta = await clean.picture(e, now=AT)
    assert seen == ["checked"] and meta["kind"] == "photo" and meta["library"] == "gold"

    async def refused(e: Any, chosen: Any, market: Any, now: Any) -> Any:
        raise visual.NumbersRefused("the headline says 3.8%, the data 2.1%")

    monkeypatch.setattr(visual, "_chart_for", refused)
    with pytest.raises(visual.NumbersRefused):  # a wrong figure still stops the post: a pretty photo never hides it
        await clean.picture(e, now=AT)


async def test_a_comparison_keeps_its_chart(library: list[str]) -> None:
    tsla = [[86400 * i, 100.0 + 3.4 * i / 21 + (0.4 if i % 3 else -0.3)] for i in range(21)]
    nvda = [[86400 * i, 100.0 - 23.2 * i / 21 + (0.5 if i % 2 else -0.4)] for i in range(21)]
    tsla[-1][1], nvda[-1][1] = 103.4, 76.8
    post = {"kind": "battle", "subject": "battle", "headline": "Tesla vs Nvidia over the past 30 days: +3.4% against −23.2%", "lines": [], "interest": 60,
            "why": {}, "series": {"TSLA": tsla, "NVDA": nvda}, "bars": [], "symbols": ["TSLA", "NVDA"], "as_of": AT.isoformat(), "table": []}
    _, meta = await clean.picture(ev(post["headline"], kind="data", data=post), now=AT)
    assert meta["layout"] == "battle" and library == []  # the story is the comparison: the chart is the picture


async def test_the_library_rotates_so_gold_is_not_the_same_nugget_every_day(library: list[str]) -> None:
    first = REAL["gold"][0].url
    _, meta = await clean.picture(ev("Gold hits a record high"), now=AT, records=[[first, "2026-11-18T20:00:00+00:00", "Gold's biggest drop", "k194"]])
    assert meta["title"] == "File:Gold bar of Banque de France.jpg"


async def test_a_war_story_never_gets_a_subject_photo_as_a_stand_in(library: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    async def none(*a: Any, **k: Any) -> list[Any]:
        return []

    monkeypatch.setattr(photos, "discover", none)
    data, meta = await clean.picture(ev("Missile strike hits Saudi Aramco oil terminal at Ras Tanura"), now=AT)
    assert data == b"" and meta["kind"] == "text" and library == []  # no pumpjack sunset standing in for an attack


async def test_without_a_licensed_photo_the_post_is_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def none(*a: Any, **k: Any) -> list[Any]:
        return []

    monkeypatch.setattr(photos, "discover", none)
    data, meta = await clean.picture(ev("Gold's biggest one-day drop since March 2026", kind="data", data=gold_post()), now=AT)
    assert data == b"" and meta["kind"] == "text"  # the conftest's library is empty: never a chart instead


# ------------------------------------------------------------------------------------------------ rules for every photograph
def test_a_watermarked_or_logo_restricted_file_never_enters() -> None:
    page = {"title": "File:Gold bars stock photo.jpg", "imageinfo": [{"url": "https://u/x.jpg", "width": 3000, "height": 2000, "extmetadata": {
        "LicenseShortName": {"value": "CC BY 2.0"}, "Categories": {"value": "Gold bars|Images with watermarks"}, "Artist": {"value": "X"}}}]}
    assert photos.record_from_commons(page) is None
    ok = editor.vision_verdict({"shows_subject": True, "recognisable": True, "generic": False, "misleading": False, "text_heavy": False,
                                "watermark": True, "what": "gold bars with a stock watermark"}, editor.Fit(True, "strong", "sign", ""))
    assert ok == (False, "vision: watermarked (gold bars with a stock watermark)")


def test_the_crop_keeps_the_aspect_without_stretching() -> None:
    src = Image.new("RGB", (4000, 1500), (10, 10, 10))
    ImageDraw.Draw(src).ellipse((2600, 400, 3300, 1100), fill=(230, 180, 40))  # the subject off-centre
    for platform, size in (("telegram", (1280, 1024)), ("x", (1600, 900))):
        out, box = photography.frame(src, platform)
        assert out.size == size and box[0] < box[2]
        # the subject (the bright disc) is inside the frame: a crop that keeps it, never a squeeze
        px = [out.getpixel((x, size[1] // 2)) for x in range(0, size[0], 20)]
        assert any(p[0] > 150 for p in px)


def test_the_most_striking_relevant_photo_wins() -> None:
    assert photography.aesthetic(pic(dark=True)) > photography.aesthetic(pic(dark=False))


@pytest.mark.parametrize("headline,key", [("Gold hits a record", "gold"), ("Goldman Sachs cuts jobs", None), ("Asian stocks slide", None),
                                          ("S&P 500 falls 2%", "stocks"), ("Microsoft Outlook down for thousands", "microsoft"),
                                          ("Silver lining for retailers", None), ("Ethereum upgrade goes live", "ether"),
                                          ("Fed holds rates steady", "fed"), ("Investors fed up with volatility", None),  # "fed up" is not the Fed
                                          ("Amazon rainforest fires spread", None), ("Amazon cuts 14,000 jobs", "amazon"),
                                          ("Trump announces new tariffs on Canada", "shipping"), ("Nasdaq falls 3%", "nasdaq"),
                                          ("ECB cuts rates for a third time", "ecb"), ("Natural gas prices spike in Europe", "natgas"),
                                          ("Russian diesel exports double", "oil"), ("Texas power grid strains under heat", "power")])
def test_the_subject_is_read_from_the_headline(headline: str, key: str | None) -> None:
    s = photography.subject_of(headline)
    assert (s.key if s else None) == key


def test_every_library_file_is_a_commons_file_title() -> None:
    for s in photography.LIBRARY:
        assert s.files and all(f.startswith("File:") and f.rsplit(".", 1)[-1].lower() in ("jpg", "jpeg", "png") for f in s.files)


async def test_the_production_self_test_reports_whether_vision_works(library: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.security import credentials

    class S:
        newsdesk_model = "claude-haiku-5-5"

    out = await editor.self_test()  # the test settings: no Anthropic key
    assert out["state"] == "unavailable" and editor.VISION_STATUS["state"] == "unavailable"  # no key: said plainly, library or text only

    monkeypatch.setattr(credentials, "resolve", lambda name, settings=None: "k")

    async def vision(img: Any, story: str, expect: str, **kw: Any) -> dict[str, Any]:
        gold = expect.startswith("gold")
        return {"shows_subject": gold, "recognisable": gold, "generic": not gold, "misleading": False, "text_heavy": False, "watermark": False,
                "what": "a gold nugget" if gold else "a treehouse in a forest"}

    monkeypatch.setattr(editor, "vision", vision)
    buf = io.BytesIO()
    pic().save(buf, "JPEG")

    def commons(request: httpx.Request) -> httpx.Response:
        if "api.php" in str(request.url):
            return httpx.Response(200, json={"query": {"pages": {"1": {"title": editor.NEGATIVE, "imageinfo": [{"thumburl": "https://upload.wikimedia.org/t.jpg"}]}}}})
        return httpx.Response(200, content=buf.getvalue())

    out = await editor.self_test(S(), transport=httpx.MockTransport(commons))
    assert out == {**out, "state": "working", "gold_ok": True, "treehouse_refused": True, "treehouse": "a treehouse in a forest"}
