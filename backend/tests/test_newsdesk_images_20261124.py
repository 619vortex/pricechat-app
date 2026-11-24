"""Licensed image coverage pass (owner, 2026-11-24): the week's publish-worthy stories measured against the photo library, the biggest
gaps filled with verified Commons photographs, matching fixes found in production data, and Openverse failures handled quietly.

No network: library records are stubbed with the real Commons titles; VERIFIED records what each file's Commons page said when it was
checked (licence, author) so the drawn credit can be checked here too."""
import io
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from PIL import Image

from app.models.newsdesk import NdEvent
from app.newsdesk import clean, editor, photography, photos, validation

AT = datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc)

# file -> (licence, Commons Artist field), read live on 2026-11-24 and each photo looked at
VERIFIED = {
    "File:Dario Amodei at TechCrunch Disrupt 2023 07.jpg": ("CC BY 2.0", "TechCrunch"),
    "File:Dario Amodei at TechCrunch Disrupt 2023 08.jpg": ("CC BY 2.0", "TechCrunch"),
    "File:China-Russia-U.S. Shenzhen Summit 2026.jpg": ("Public domain", "White House"),
    "File:Vladimir Putin, Xi Jinping and Donald Trump at APEC China 2026.jpg": ("CC BY 4.0", "President of Russia"),
    "File:Xi–Putin–Trump summit - P20261119DT-0901 (55662934489).jpg": ("Public domain", "The White House"),
    "File:TSE Arrows 20240329.jpg": ("CC0", "Suicasmo"),
    "File:Tokyo Stock Exchange (28174991346).jpg": ("CC0", "franck injapan from Tokyo, Japan"),
    "File:Tokyo Stock Exchange (28208458775).jpg": ("CC0", "franck injapan from Tokyo, Japan"),
    "File:Strait of Hormuz (MODIS 2020-12-04).jpg": ("Public domain", "MODIS Land Rapid Response Team, NASA GSFC"),
    "File:ISS-46 Gulf of Oman with Strait of Hormuz at night.jpg": ("Public domain", "NASA/Tim Kopra"),
    "File:Strait of Hormuz and Musandam Peninsula (MODIS 2018-12-10).jpg": ("Public domain", "MODIS Land Rapid Response Team, NASA GSFC"),
    "File:Checking stock market prices (Unsplash).jpg": ("CC0", "William Iven firmbee"),
}
NEW = ("anthropic", "openai", "strategy", "hormuz", "tokyo_stocks", "markets", "trump_xi_putin")


def ev(headline: str, title: str = "") -> NdEvent:
    e = NdEvent(key="k", kind="news", title=title or headline, headline=headline, summary="", topic="", label="", evidence="two outlets",
                category="news", sources=[{"name": "Reuters", "url": "https://www.reuters.com/x"}], facts={}, released_at=AT)
    e.id = 209
    return e


def rec(title: str) -> photos.LicensedPhoto:
    lic, artist = VERIFIED.get(title, ("Public domain", "The White House"))
    slug = title.removeprefix("File:").replace(" ", "_")
    return photos.LicensedPhoto(title=title, url=f"https://upload.wikimedia.org/x/{slug}", page=f"https://commons.wikimedia.org/wiki/{slug}",
                                creator=artist, provider="wikimedia", source="wikimedia", licence=lic, licence_url="", width=3000, height=2000)


def pic() -> Image.Image:
    import random

    rnd = random.Random(3)
    img = Image.new("RGB", (3000, 2000), (12, 12, 16))
    small = Image.new("RGB", (500, 333))
    small.putdata([(rnd.randint(120, 255), rnd.randint(80, 200), rnd.randint(0, 90)) for _ in range(500 * 333)])
    img.paste(small.resize((1500, 1000)), (750, 500))
    return img


@pytest.fixture
def library(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    asked: list[str] = []

    async def records(client: Any, subject: Any) -> list[Any]:
        asked.append(subject.key)
        return [rec(t) for t in subject.files]

    async def fetch(client: Any, p: Any, min_width: int = 0, colour: bool = True) -> Image.Image:
        return pic()

    async def nothing(*a: Any, **k: Any) -> list[Any]:
        return []

    monkeypatch.setattr(photography, "_records", records)
    monkeypatch.setattr(photos, "fetch", fetch)
    monkeypatch.setattr(photos, "discover", nothing)
    return asked


# --- 1. the new subjects ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("head,key", [
    ("Anthropic shares jump 35% in their Nasdaq trading debut", "anthropic"),
    ("OpenAI weighs a share sale", "openai"),
    ("New York State Pension Fund Boosts Strategy (MSTR) Stake by More Than 10%", "strategy"),
    ("Iran and Oman hold talks on Strait of Hormuz security", "hormuz"),
    ("Nikkei falls 3% as chip stocks slide", "tokyo_stocks"),
    ("Global Stocks Set for Worst Run Since 2023 as AI Enthusiasm Fades", "markets"),
    ("Tech stock sell-off deepens in Asia", "markets"),
    ("US tech stocks plunge into bear market", "stocks"),  # Wall Street: it says US
    ("Wall St ends sharply lower on AI selloff", "stocks"),
    ("BOE to Test How Private Credit Could Amplify Shocks", "boe"),
])
def test_the_weeks_biggest_gaps_have_a_subject(head: str, key: str) -> None:
    got = photography.subject_of(head)
    assert got is not None and got.key == key


def test_every_new_photo_is_verified_captioned_and_credited_within_the_corner() -> None:
    for key in NEW:
        s = photography.BY_KEY[key]
        assert s.files, key
        for f in s.files:
            assert photography.CAPTIONS.get(f), f  # who or what, where and when
            assert any(y in photography.CAPTIONS[f] for y in ("2015", "2016", "2018", "2020", "2022", "2023", "2024", "2025", "2026")), f
            p = rec(f)
            if f in VERIFIED:
                assert photos.verify_licence(p) is None, f
            named = photography.CREDITS.get(f)
            if named:
                p.creator = named
            line = clean.photo_credit({"credit": p.credit, "licence": p.licence_name, "who": photography.CAPTIONS[f]})
            assert len(line) <= validation.CREDIT_MAX and clean.drawable(line), line


def test_a_market_wide_picture_never_comes_before_anything_more_specific() -> None:
    assert photography.subject_of("Stocks slip as investors mull Nvidia earnings").key == "nvidia"
    assert photography.subject_of("Asian stocks plunge as Anthropic fails to dispel AI fears").key == "markets"  # not the CEO's portrait
    assert photography.subject_of("Gold and stocks slump").key == "gold"
    assert photography.BY_KEY["markets"].fallback and not photography.BY_KEY["stocks"].fallback


async def test_a_person_named_later_still_comes_before_the_market_wide_picture(library: list[str]) -> None:
    _, meta = await clean.picture(ev("Stocks plunge after Trump threatens tax on data centers built abroad"), now=AT)
    assert meta["kind"] == "photo" and meta["library"] == "trump"


async def test_a_company_shown_by_its_chief_is_never_pictured_on_a_story_of_violence(library: list[str]) -> None:
    _, meta = await clean.picture(ev("Gunman shot outside Anthropic office, police say"), now=AT)
    assert meta.get("library") != "anthropic"


# --- 2. matching fixes found in the week's data ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("headline,title,key", [
    # 16 summit stories got the Trump-Putin pair from Alaska 2025: "XI" in capitals was invisible
    ("TRUMP TO MEET XI, PUTIN IN CHINA AFTER US MIDTERM LOSS", "Trump to meet Xi, Putin in China after US midterm loss", "trump_xi_putin"),
    ("TRUMP HOLDS NEWS CONFERENCE AFTER SUMMIT WITH PUTIN, XI", "", "trump_xi_putin"),
    ("Here’s What Trump, Putin and Xi Said at Unprecedented Sitdown", "", "trump_xi_putin"),
    # Telegram 197 stays the two who spoke
    ("U.S., CHINA CAN HELP END UKRAINE WAR, TRUMP TELLS XI BEFORE TRILATERAL WITH PUTIN",
     "U.S., China can help end Ukraine war, Trump tells Xi before trilateral with Putin", "trump_xi"),
    ("'IT'S MY FRIEND': MOMENT TRUMP GREETS PUTIN AT CHINA SUMMIT", "", "trump_putin"),
])
def test_the_leaders_a_story_names_together(headline: str, title: str, key: str) -> None:
    got = photography.people_of(photography.story_text(headline, title))
    assert got is not None and got[0].key == key


def test_a_headline_in_capitals_is_read_after_the_sources_title() -> None:
    assert photography.story_text("TRUMP TO MEET XI", "Trump to meet Xi") == "Trump to meet Xi TRUMP TO MEET XI"
    assert photography.story_text("Trump to meet Xi", "Trump to meet Xi") == "Trump to meet Xi"
    assert photography.story_text("Bitcoin falls 3%", "BTC drops") == "Bitcoin falls 3% BTC drops"
    assert photography.people_of("OGC Nice predicted XI v PSG") is None  # a team sheet, not the Chinese leader


@pytest.mark.parametrize("head", ["Circle and Trump-linked crypto firm hit with court freeze order", "Earnings trump forecasts at Gap",
                                  "Trump-backed stablecoin grows"])
def test_trump_only_as_the_man(head: str) -> None:
    got = photography.people_of(head)
    assert got is None or got[0].key != "trump"


def test_another_huang_is_not_jensen_huang() -> None:
    assert photography.people_of("Alice Huang in touch with Chinese spy agency during affair with Nvidia CEO") is None
    assert photography.people_of("Nvidia boss Huang says demand is strong")[0].key == "jensen_huang"
    assert photography.people_of("Jensen Huang agrees to face Congress over Nvidia scandal")[0].key == "jensen_huang"


@pytest.mark.parametrize("head", ["Malaysia Palm Oil Industry Says CPO Tax-Waiver Window Has Passed", "Olive oil prices ease in Spain"])
def test_palm_or_olive_oil_is_not_crude(head: str) -> None:
    got = photography.subject_of(head)
    assert got is None or got.key != "oil"


@pytest.mark.parametrize("symbol,key", [("LIQ:BTCUSD_PERP", "bitcoin"), ("BTCUSD_270326", "bitcoin"), ("ETHUSDT", "ether"), ("$MSTR", "strategy"),
                                        ("BTC-USD", "bitcoin"), ("XYZ", "")])
def test_a_derivatives_contract_shows_its_coin_not_the_exchange(symbol: str, key: str) -> None:
    # "BTCUSD_PERP LONGS LIQUIDATED: $10.1 BILLION ON BINANCE" showed Binance's founder
    assert photography.symbol_key(symbol) == key


@pytest.mark.parametrize("head,conflict", [
    ("Oil gains on Iran war worries and short-covering ahead of Thanksgiving holiday", False),
    ("Iran war set to hike 2027 UK energy bills to four-year high", False),
    ("Russian missile hits oil depot in Odesa", True),  # the act itself
    ("Ukrainian Drones Kill 1, Strike Oil Refinery in Perm Region", True),
])
def test_a_wars_market_consequence_is_a_market_story(head: str, conflict: bool) -> None:
    assert editor.is_conflict(head) is conflict


# --- 3. no searches that can only be refused; Openverse failures stay quiet ---------------------------------------------------------------

async def test_with_the_vision_check_off_nothing_is_searched() -> None:
    calls: list[str] = []

    def handle(r: httpx.Request) -> httpx.Response:
        calls.append(str(r.url))
        return httpx.Response(500)

    got = await clean.find_photo(ev("Russian warship struck by suspected Ukrainian drones at Libya naval base"), used=set(),
                                 transport=httpx.MockTransport(handle), panel=False)
    assert got is None and calls == []  # production's "ukraine damage"/"ukraine attack" Openverse 504s were such searches


@pytest.mark.parametrize("status,headers,minutes", [(504, {}, 15), (403, {}, 360), (429, {"retry-after": "120"}, 2), (502, {}, 15)])
async def test_openverse_failing_upstream_is_left_alone_for_a_while(status: int, headers: dict[str, str], minutes: int) -> None:
    calls: list[str] = []

    def handle(r: httpx.Request) -> httpx.Response:
        calls.append(r.url.host)
        return httpx.Response(status, headers=headers, text="<html>Just a moment...</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
        assert await photos.search_openverse(c, "ukraine damage") == []
        assert photos.openverse_paused() == f"HTTP {status}"
        assert await photos.search_openverse(c, "ukraine attack") == []
    assert calls == ["api.openverse.org"]  # asked once, then paused
    import time

    left = (photos._openverse["until"] - time.time()) / 60
    assert minutes - 1 <= left <= minutes + 0.1


async def test_openverse_timeout_pauses_and_a_bad_request_still_surfaces() -> None:
    def slow(r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=r)

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as c:
        assert await photos.search_openverse(c, "gold") == []
    assert photos.openverse_paused() == "ReadTimeout"
    photos._openverse.update(until=0.0, why="")

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(400))) as c:
        with pytest.raises(httpx.HTTPStatusError):  # ours to fix: discovery logs it (photos._safe), nothing is paused
            await photos.search_openverse(c, "gold")
    assert photos.openverse_paused() == ""


# --- 4. the audit reads the same words the picture was chosen from -----------------------------------------------------------------------

async def test_the_summit_photo_passes_the_published_picture_audit(library: list[str]) -> None:
    e = ev("TRUMP HOLDS NEWS CONFERENCE AFTER SUMMIT WITH PUTIN, XI", "Trump holds news conference after summit with Putin, Xi")
    data, meta = await clean.picture(e, now=AT, platform="x")
    assert meta["library"] == "trump_xi_putin" and Image.open(io.BytesIO(data)).size == (1600, 900)
    assert meta["who"] and "2026" in meta["credit_line"]
    text = photography.story_text(e.headline, e.title)
    assert not [p for p in validation.audit_image(text, data, meta, "x") if "off-subject" in p or "portrait" in p]
