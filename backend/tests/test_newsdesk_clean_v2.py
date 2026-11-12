"""Clean posts, second pass (owner, 2026-11-12): "the Nvidia/Jensen Huang story uses a distant conference crowd. That's a weak visual";
subject relevance, recognisable faces and composition first; no generic buildings when something better exists; collages only when
they add something; shorter captions; X posts that never exceed 280, links and cashtags counted the way X counts them."""
from datetime import datetime, timezone
from typing import Any

import pytest
from PIL import Image

from app.models.newsdesk import NdEvent
from app.newsdesk import clean, network, photos
from app.newsdesk.network import Copy, Link

AT = datetime(2026, 11, 12, 3, 0, tzinfo=timezone.utc)


def ev(headline: str) -> NdEvent:
    e = NdEvent(key="t", kind="news", title=headline, headline=headline, summary="", topic="", label="", evidence="two outlets",
                category="business", sources=[{"name": "NYT", "url": "https://www.nytimes.com/x"}], facts={})
    e.id = 1
    return e


def lp(title: str, w: int = 1600, h: int = 1000, tags: tuple[str, ...] = (), url: str = "") -> photos.LicensedPhoto:
    slug = title.replace(" ", "_")
    return photos.LicensedPhoto(title=f"File:{title}.jpg", url=url or f"https://upload.wikimedia.org/{slug}.jpg",
                                page=f"https://commons.wikimedia.org/wiki/File:{slug}.jpg", creator="Jane Doe", provider="wikimedia",
                                source="wikimedia", licence="CC BY-SA 4.0", licence_url="https://creativecommons.org/licenses/by-sa/4.0/",
                                width=w, height=h, tags=tags, year=2025)


def picture_of(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h), (40, 90, 160))
    for y in range(0, h, 3):
        for x in range(0, w, 37):
            img.putpixel((x, y), (200, (x * 7) % 255, (y * 3) % 255))
    return img


# ---------------------------------------------------------------- composition


def test_a_close_up_of_the_person_beats_a_keynote_hall_that_names_them() -> None:
    hall = lp("Jensen Huang Nvidia Keynote CES 2025", 4000, 2600)
    face = lp("Jensen Huang 2024 (cropped)", 1200, 1500)
    terms = ["Jensen Huang"]
    assert photos._score(face, terms, [], person=True) > photos._score(hall, terms, [], person=True) + 3
    assert photos.composition(hall, person=True) <= -3 and photos.composition(face, person=True) >= 3


def test_a_building_or_skyline_loses_to_the_subject_itself() -> None:
    hq = lp("Samsung Headquarters - Silicon Valley (cropped)")
    phone = lp("Samsung Galaxy S25 Ultra")
    assert photos.composition(hq) < 0 == photos.composition(phone)
    assert photos.composition(hq, sensitive=True) == 0  # a sensitive story still prefers places to people: no penalty there


def test_topic_and_product_searches_come_before_places() -> None:
    plans = clean.topic_plans("Iran opens all-women training camp amid US attack fears")
    assert plans and plans[0][0][0] == "Iranian female soldiers" and plans[0][1] == ["Iranian", "Iran"]
    women, military = plans[0][2]
    assert women.search("Iranian women soldiers") and military.search("Iranian women soldiers")
    assert not military.search("Solidarity with Assange and women in Iran")  # a protest photo never illustrates a military story
    plans = clean.topic_plans("Samsung Wallet launches USDC on Solana for U.S. Galaxy users")
    assert plans[0][0] == ["Samsung Galaxy smartphone"]
    assert clean.topic_plans("Fed holds rates steady") == []


# ---------------------------------------------------------------- the person's own portrait, and the layouts


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {"discover": []}

    async def portraits(client: Any, name: str) -> list[photos.LicensedPhoto]:
        seen["wikidata"] = name
        return [lp("Jen-Hsun Huang 2025", 1200, 1600)]

    async def discover(client: Any, queries: list[str], terms: list[str], **kw: Any) -> list[photos.LicensedPhoto]:
        seen["discover"].append((terms, kw))
        if terms == ["Jensen Huang"]:
            return [lp("Jensen Huang Nvidia Keynote CES 2025", 4000, 2600)]
        if terms == ["nvidia"]:
            return [lp("Nvidia headquarters Santa Clara", 2400, 1600)]
        return []

    async def fetch(client: Any, p: photos.LicensedPhoto, min_width: int = 0) -> Image.Image:
        return picture_of(p.width // 4, p.height // 4)

    monkeypatch.setattr(photos, "wikidata_portraits", portraits)
    monkeypatch.setattr(photos, "discover", discover)
    monkeypatch.setattr(photos, "fetch", fetch)
    return seen


async def test_the_person_gets_their_portrait_beside_their_company(stubs: dict[str, Any]) -> None:
    _, meta = await clean.picture(ev("Nvidia hires Wachtell Lipton to investigate CEO Jensen Huang's affair with Chinese executive"), now=AT)
    assert stubs["wikidata"] == "Jensen Huang"
    assert meta["title"] == "File:Jen-Hsun Huang 2025.jpg" and meta["person"] == "Jensen Huang"  # not the keynote hall
    assert meta["layout"] == "duo" and meta["context_photo"]["title"] == "File:Nvidia headquarters Santa Clara.jpg"
    company = [kw for terms, kw in stubs["discover"] if terms == ["nvidia"]]
    assert company and company[0]["sensitive"] is True  # an affair story: the company side shows a place, never people


async def test_a_politician_alone_is_shown_whole_not_cropped(stubs: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    async def portraits(client: Any, name: str) -> list[photos.LicensedPhoto]:
        return [lp("Volodymyr Zelensky Official portrait", 1200, 1600)]

    monkeypatch.setattr(photos, "wikidata_portraits", portraits)
    data, meta = await clean.picture(ev("First day of Ukraine peace talks constructive, Zelensky says"), now=AT)
    assert meta["layout"] == "portrait" and "Official portrait" in meta["title"]
    assert Image.open(__import__("io").BytesIO(data)).size == (clean.W, clean.H)


async def test_x_redraws_the_new_layouts(stubs: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    async def download(url: str, transport: Any = None) -> Image.Image:
        return picture_of(400, 500) if "Jen-Hsun" in url else picture_of(600, 400)

    monkeypatch.setattr(clean, "download", download)
    e = ev("Nvidia hires Wachtell Lipton to investigate CEO Jensen Huang's affair with Chinese executive")
    _, meta = await clean.picture(e, now=AT)
    again = await clean.redraw(e, meta)
    assert again is not None and Image.open(__import__("io").BytesIO(again[0])).size == (clean.CW, clean.CH)


# ---------------------------------------------------------------- words


LINK = [Link("New York Times", "https://www.nytimes.com/2026/11/10/business/nvidia-wachtell-lipton-jensen-huang.html")]


def test_x_counts_like_x() -> None:
    assert network.x_length("Read https://www.nytimes.com/a/very/long/path/that/goes/on/and/on") == 5 + 23
    assert network.x_length("more at pricechat.live") == 8 + 23  # a bare domain is a link on X too
    assert network.x_length("🚨 BREAKING") == 11 and network.x_length("👍🏽") == 2 and network.x_length("日本") == 4
    assert network.x_length("$NVDA") == 5


@pytest.mark.parametrize("headline", [
    "Nvidia hires Wachtell Lipton to investigate CEO Jensen Huang's affair with Chinese executive",
    "Some very long headline " * 20,
    "日本銀行が金利を引き上げ、円相場が急騰し、日経平均株価は大幅に下落した。" * 4,
])
def test_an_x_post_never_exceeds_the_limit_and_keeps_its_link(headline: str) -> None:
    c = Copy(headline=headline, short="", context="Wachtell partner Joon H. Kim, a former acting U.S. attorney, is leading the inquiry.", stakes="", question="")
    text = clean.x_caption(c, LINK, prefix="JUST IN:")
    assert network.x_length(text) <= clean.X_SAFE <= 280
    assert text.endswith(LINK[0].url)
    assert "  " not in text and not text.split("\n\n")[0].endswith(" …")


def test_the_x_post_is_the_hook_and_the_link() -> None:
    c = Copy(headline="Nvidia hires Wachtell Lipton to investigate CEO Jensen Huang's affair with Chinese executive", short="",
             context="Wachtell partner Joon H. Kim, a former acting U.S. attorney, is leading the inquiry into the chip maker's chief executive.",
             stakes="", question="")
    text = clean.x_caption(c, LINK)
    assert text.split("\n\n")[0] == "Nvidia $NVDA hires Wachtell Lipton to investigate CEO Jensen Huang's affair with Chinese executive."
    assert "Joon H. Kim" not in text  # a long hook goes alone


def test_vague_attribution_is_trimmed_named_sources_kept() -> None:
    assert clean._trim_attribution("Experts say blockchain records show repeated wallet drains.") == "Blockchain records show repeated wallet drains."
    assert clean._trim_attribution("Elliptic said the funds moved through memecoins.") == "Elliptic said the funds moved through memecoins."


def test_x_adds_a_second_sentence_only_for_the_missing_number() -> None:
    c = Copy(headline="US inflation higher than expected in October", short="",
             context="Prices rose by 0.4% over the month, driven by increases in shelter, food and energy costs.", stakes="", question="")
    assert "0.4%" in clean.x_caption(c, LINK)
    c = Copy(headline="First day of Ukraine peace talks constructive, Zelensky says", short="",
             context="Delegations from Kyiv, Washington and Moscow are in the United Arab Emirates for the first trilateral talks.", stakes="", question="")
    assert "Delegations" not in clean.x_caption(c, LINK)
