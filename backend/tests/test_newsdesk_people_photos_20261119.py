"""Recognisable photography of the people and companies a story is about (owner, 2026-11-19: "Binance story -> recognizable Binance imagery.
Trump/Xi/Zelensky story -> relevant real photograph of the people or event. Actual attack -> authentic event photography, never unrelated
explosions.")

Library records are stubbed with the real Commons titles (no network); every file was looked at before it went in."""
import io
import re
from datetime import datetime, timezone
from typing import Any

import pytest
from PIL import Image

from app.models.newsdesk import NdEvent
from app.newsdesk import clean, photography, photos

AT = datetime(2026, 11, 19, 9, 0, tzinfo=timezone.utc)


def ev(headline: str) -> NdEvent:
    e = NdEvent(key="k", kind="news", title=headline, headline=headline, summary="", topic="", label="", evidence="two outlets", category="news",
                sources=[{"name": "Reuters", "url": "https://www.reuters.com/x"}], facts={}, released_at=AT)
    e.id = 197
    return e


def rec(title: str, licence: str = "Public domain") -> photos.LicensedPhoto:
    slug = title.removeprefix("File:").replace(" ", "_")
    return photos.LicensedPhoto(title=title, url=f"https://upload.wikimedia.org/x/{slug}", page=f"https://commons.wikimedia.org/wiki/{slug}",
                                creator="The White House", provider="wikimedia", source="wikimedia", licence=licence, licence_url="", width=3000, height=2000)


def pic() -> Image.Image:
    import random

    rnd = random.Random(7)
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


@pytest.mark.parametrize("headline,key", [
    ("U.S., China can help end Ukraine war, Trump tells Xi before trilateral with Putin", "trump_xi"),  # Telegram 197: the two who spoke
    ("Zelensky meets Trump at the White House", "trump_zelensky"),
    ("Putin says Russia is ready for talks", "putin"),
    ("Jensen Huang agrees to face Congress over Nvidia scandal", "jensen_huang"),  # Telegram 187: named before Nvidia
    ("Musk sells $5B of Tesla stock", "musk"),
])
def test_the_people_a_headline_names(headline: str, key: str) -> None:
    got = photography.people_of(headline)
    assert got is not None and got[0].key == key


@pytest.mark.parametrize("headline", ["PDD founder Colin Huang steps down", "Super Bowl XI rerun draws record crowd", "Musk deer spotted in Nepal"])
def test_names_that_are_not_our_people(headline: str) -> None:
    assert photography.people_of(headline) is None


async def test_telegram_197_trump_xi_story_gets_trump_and_xi_with_a_caption_saying_where_and_when(library: list[str]) -> None:
    data, meta = await clean.picture(ev("U.S., China can help end Ukraine war, Trump tells Xi before trilateral with Putin"), now=AT, platform="x")
    assert meta["kind"] == "photo" and meta["library"] == "trump_xi" and Image.open(io.BytesIO(data)).size == (1600, 900)
    assert meta["credit_line"].startswith(photography.CAPTIONS[meta["title"]]) and "2026" in meta["credit_line"] or "2025" in meta["credit_line"]
    assert "Public domain" in meta["credit_line"] or "public domain" in meta["credit_line"]


async def test_telegram_196_binance_story_gets_binance_imagery_captioned_as_its_founder(library: list[str]) -> None:
    data, meta = await clean.picture(ev("Binance receives Abu Dhabi court order to 'Freeze' assets after $6.3B heist"), now=AT)
    assert meta["kind"] == "photo" and meta["library"] == "binance"
    assert meta["credit_line"].startswith("Binance founder Changpeng Zhao at Web Summit, November 2022 · ")


@pytest.mark.parametrize("headline", [
    "Saudi-led coalition intercepts Houthi missiles targeting Riyadh and south",  # Telegram 198
    "Trump orders strikes on Iranian missile sites",
    "Putin's drones kill 12 in Kyiv",
])
async def test_an_act_of_violence_never_gets_a_portrait_or_a_stand_in(library: list[str], headline: str) -> None:
    data, meta = await clean.picture(ev(headline), now=AT)
    assert meta["kind"] == "text"  # its own dated photograph (none offered here) or text: never a leader's portrait, never Riyadh's skyline
    assert not any(k in library for k in ("trump", "putin", "trump_putin"))


def test_every_people_file_has_a_caption() -> None:
    for s in (*photography.PEOPLE, *(p[0] for p in photography.PAIRS), photography.BY_KEY["binance"]):
        for f in s.files:
            assert photography.CAPTIONS.get(f), f
            assert re.search(r"\b20\d\d\b", photography.CAPTIONS[f])  # when it was taken: an archive photo never passes for today's
