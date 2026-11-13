"""News Desk photographs (app/newsdesk/photos.py): licence allow/deny, relevance (subject named, no logos, no other people, sensitive
stories show places only), provider failure isolation, dedupe across providers, the used-URL skip, the subject-aware crop and where the
headline may sit, and the credit line. All HTTP is mocked (httpx.MockTransport); no network."""

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageOps

from app.newsdesk import photos as P


def _photo(title: str = "File:Binance headquarters Dubai.jpg", **kw: Any) -> P.LicensedPhoto:
    base: dict[str, Any] = dict(url="https://upload.wikimedia.org/x/1920px-a.jpg", page="https://commons.wikimedia.org/wiki/File:A.jpg",
                                creator="Jane Doe", provider="wikimedia", source="wikimedia", licence="CC BY 4.0",
                                licence_url="https://creativecommons.org/licenses/by/4.0", width=4000, height=2250, year=2023,
                                mime="image/jpeg")
    base.update(kw)
    return P.LicensedPhoto(title=title, **base)


# --- licence ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name,url,want", [
    ("CC0", "", "CC0"),
    ("CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/", "CC0"),
    ("Public domain", "", "public domain"),
    ("Public Domain Mark", "https://creativecommons.org/publicdomain/mark/1.0/", "public domain"),
    ("CC BY 2.0", "", "CC BY 2.0"),
    ("CC BY 2.5", "", "CC BY 2.5"),
    ("CC BY-SA 3.0", "", "CC BY-SA 3.0"),
    ("CC BY-SA 4.0", "", "CC BY-SA 4.0"),
    ("cc-by-sa-4.0", "", "CC BY-SA 4.0"),
    ("CC BY 2.0 fr", "", "CC BY 2.0"),
    ("CC BY", "https://creativecommons.org/licenses/by/4.0/", "CC BY 4.0"),  # version from the licence URL
    ("Pexels License", "", "Pexels License"),
    ("Unsplash License", "", "Unsplash License"),
    ("CC BY-NC 2.0", "", None),
    ("CC BY-NC-SA 4.0", "", None),
    ("CC BY-ND 4.0", "", None),
    ("CC BY-NC-ND 3.0", "", None),
    ("CC BY 4.0", "https://creativecommons.org/licenses/by-nc/4.0/", None),  # the URL contradicts the name
    ("CC BY 1.0", "", None),
    ("CC BY", "", None),  # no version: cannot be verified
    ("All rights reserved", "", None),
    ("GFDL", "", None),
    ("Fair use", "", None),
    ("Non-free media", "", None),
    ("Attribution", "", None),
    ("", "", None),
    ("unknown", "", None),
])
def test_licence_allow_and_deny(name: str, url: str, want: str | None) -> None:
    assert P.canonical_licence(name, url) == want
    assert (P.verify_licence(_photo(licence=name, licence_url=url)) is None) == (want is not None)


def test_licence_needs_an_auditable_page_and_no_trademark() -> None:
    assert "source page" in (P.verify_licence(_photo(page="")) or "")
    assert "trademark" in (P.verify_licence(_photo(restrictions="trademarked")) or "")
    p = _photo(licence="CC BY-SA 3.0", licence_url="")
    assert P.verify_licence(p) is None and p.licence_url == "https://creativecommons.org/licenses/by-sa/3.0/"


def test_meta_records_everything_needed_to_audit_the_credit() -> None:
    m = _photo().to_meta()
    for k in ("url", "page", "title", "creator", "provider", "licence", "licence_url", "retrieved_at", "width", "height"):
        assert m[k] not in (None, ""), k
    assert m["licence"] == "CC BY 4.0"
    assert datetime.fromisoformat(m["retrieved_at"]).tzinfo is not None


# --- credit -------------------------------------------------------------------------------------------------------------


def test_credit_lines() -> None:
    assert _photo(provider="openverse", source="flickr", licence="CC BY 2.0").credit == "Photo: Jane Doe / Flickr (CC BY 2.0)"
    assert _photo(creator="", licence="Public domain").credit == "Photo: Wikimedia Commons (public domain)"
    assert _photo(creator="<a href='x'>Unknown author</a>", licence="CC0").credit == "Photo: Wikimedia Commons (CC0)"
    assert _photo(creator="U.S. Navy photo by Petty Officer 2nd Class John Smith", licence="Public domain").credit == \
        "Photo: U.S. Navy / Wikimedia Commons (public domain)"
    assert _photo(provider="pexels", source="pexels", creator="Ann Lee", licence="Pexels License").credit == \
        "Photo: Ann Lee / Pexels (Pexels License)"
    long = _photo(creator="A very long photographer name that goes on and on forever").credit
    assert "…" in long and len(long) < 80


# --- relevance ----------------------------------------------------------------------------------------------------------


def test_subject_must_be_named_as_a_whole_word() -> None:
    assert P.relevant(_photo("File:Binance headquarters Dubai.jpg"), ["Binance"]) is None
    assert P.relevant(_photo("File:Data centre at CERN.jpg"), ["Binance"]) is not None  # the 2026-11-09 stand-in
    assert P.relevant(_photo("File:Binancefoo tower.jpg"), ["Binance"]) is not None  # not a whole word
    assert P.relevant(_photo("File:DSC 4410.jpg", tags=("Binance offices",)), ["Binance"]) is None  # a human tag/category names it
    assert "generic scene" in P.relevant(_photo("File:Skyline.jpg", tags=("Binance offices",)), ["Binance"])  # 2026-11-13: a skyline isn't the story
    assert P.relevant(_photo("File:Strait_of_Hormuz_from_space.jpg"), ["Strait of Hormuz"]) is None  # underscores are spaces


def test_description_counts_only_its_first_sentence() -> None:
    beach = _photo("File:Khalig-fars-Persian-Gulf 05.jpg", description="The Persian Gulf is a sea in Western Asia. It is an extension of the "
                   "Indian Ocean through the Strait of Hormuz.")
    assert P.relevant(beach, ["Strait of Hormuz"]) is not None
    ship = _photo("File:IMG 0042.jpg", description="A tanker in the Strait of Hormuz, 2023.")
    assert P.relevant(ship, ["Strait of Hormuz"]) is None
    assert P.relevant(ship, ["Strait of Hormuz"], story_terms=["Iran"]) is not None  # description-only + story terms: must share one


@pytest.mark.parametrize("title", ["File:Binance logo.png", "File:Binance logo 2023.jpg", "File:Map of Binance offices.jpg",
                                   "File:Binance app screenshot.jpg", "File:Binance price chart.jpg", "File:Binance tower illustration.jpg",
                                   "File:Binance HQ 3D render.jpg", "File:Binance t-shirt and bag - 2022-07-09.jpg",
                                   "File:Binance building under construction.jpg", "File:Binance black and white.jpg"])
def test_not_news_photographs_are_rejected(title: str) -> None:
    assert P.relevant(_photo(title), ["Binance"]) is not None


def test_description_saying_map_is_rejected() -> None:
    assert P.relevant(_photo("File:Federal Reserve Banks.jpg", description="A map of the twelve federal reserve districts."),
                      ["Federal Reserve"]) is not None


def test_size_shape_age_and_file_type() -> None:
    assert "small" in (P.relevant(_photo(width=1200, height=675), ["Binance"]) or "")
    assert "portrait" in (P.relevant(_photo(width=2000, height=3000), ["Binance"]) or "")
    assert "portrait" in (P.relevant(_photo(width=3000, height=3000), ["Binance"]) or "")
    assert "unknown" in (P.relevant(_photo(width=0, height=0), ["Binance"]) or "")
    assert "old" in (P.relevant(_photo(year=1987), ["Binance"]) or "")
    assert "old" in (P.relevant(_photo("File:Binance building 1962.jpg", year=None), ["Binance"]) or "")
    assert P.relevant(_photo(mime="image/svg+xml"), ["Binance"]) is not None


def test_a_photo_of_another_person_is_rejected() -> None:
    assert "another person" in (P.relevant(_photo("File:Jerome Powell at the Federal Reserve.jpg"), ["Federal Reserve"]) or "")
    assert "another person" in (P.relevant(_photo("File:Jensen Huang Nvidia Keynote CES 2025.jpg"), ["Nvidia"]) or "")
    nga = _photo("File:Director Lê Nguyễn Thiên Nga and NVIDIA Founder and CEO Jensen Huang.jpg")
    assert "another person" in (P.relevant(nga, ["Nvidia"]) or "")
    # the person IS the subject: fine
    assert P.relevant(_photo("File:Jerome Powell at the Federal Reserve.jpg"), ["Federal Reserve", "Jerome Powell"]) is None
    # a building named after someone is a building
    assert P.relevant(_photo("File:Marriner S. Eccles Federal Reserve Board Building.jpg"), ["Federal Reserve", "Eccles Building"]) is None
    assert P.relevant(_photo("File:USS Dwight D. Eisenhower in the Strait of Hormuz.jpg"), ["Strait of Hormuz"]) is None


def test_a_photo_of_a_different_incident_is_rejected() -> None:
    crash = _photo("File:USS Porter damaged in collision with oil tanker.jpg")
    assert "incident" in (P.relevant(crash, ["oil tanker"]) or "")
    assert P.relevant(crash, ["oil tanker"], story_terms=["collision"]) is None  # the story is about that kind of incident


# --- sensitive stories --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("headline,want", [
    ("Nvidia CEO accused in affair scandal", True), ("Exchange hacked for $400 million", True), ("Founder arrested in Dubai", True),
    ("SEC lawsuit against Binance", True), ("Three dead in strike on tanker", True), ("Fraud charges for fund manager", True),
    ("Fed holds rates steady", False), ("Nvidia reports record revenue", False), ("Oil tankers return to Hormuz", False),
])
def test_is_sensitive(headline: str, want: bool) -> None:
    assert P.is_sensitive(headline) is want
    assert bool(P.SENSITIVE.search(headline)) is want


def test_sensitive_stories_show_places_never_people() -> None:
    nga = _photo("File:Director Lê Nguyễn Thiên Nga and NVIDIA Founder and CEO Jensen Huang.jpg")
    assert "Thiên" in (P.relevant(nga, ["Nvidia", "Jensen Huang"]) or "")  # she is not the subject, in any story
    assert P.relevant(nga, ["Nvidia", "Jensen Huang"], sensitive=True) is not None
    him = _photo("File:Jensen Huang at Nvidia.jpg")
    assert P.relevant(him, ["Nvidia", "Jensen Huang"]) is None  # an ordinary story about him may show him...
    assert "Jensen Huang" in (P.relevant(him, ["Nvidia", "Jensen Huang"], sensitive=True) or "")  # ...an affair story shows nobody
    assert P.relevant(_photo("File:Federal Reserve.jpg"), ["Federal Reserve"], sensitive=True) is None  # an institution is not a person
    for title in ("File:Nvidia staff at the annual event.jpg", "File:Nvidia board meeting.jpg", "File:Crowd outside Nvidia.jpg",
                  "File:Satya Nadella visits Nvidia.jpg", "File:Nvidia Lisa Su.jpg", "File:Portrait at Nvidia.jpg"):
        assert P.relevant(_photo(title), ["Nvidia"], sensitive=True) is not None, title
    assert P.relevant(_photo("File:Nvidia campus.jpg", description="A woman walks past the entrance."), ["Nvidia"], sensitive=True)
    for title in ("File:NVIDIA Headquarters.jpg", "File:Nvidia HQ Santa Clara building exterior.jpg",
                  "File:Marriner S. Eccles Federal Reserve Board Building.jpg"):
        assert P.relevant(_photo(title, tags=("Buildings and structures in Santa Clara",)), ["Nvidia", "Federal Reserve"],
                          sensitive=True) is None, title


async def test_discover_sensitive_prefers_places() -> None:
    pages = [_commons_page("NVIDIA Headquarters.jpg", 1, year="2018"),
             _commons_page("Director Lê Nguyễn Thiên Nga and NVIDIA Founder and CEO Jensen Huang.jpg", 2, year="2024"),
             _commons_page("Nvidia GPU board.jpg", 3, year="2025")]
    async with httpx.AsyncClient(transport=_transport(pages)) as c:
        normal = await P.discover(c, ["Nvidia"], ["Nvidia"])
        sens = await P.discover(c, ["Nvidia"], ["Nvidia"], sensitive=True)
    assert all("Jensen" not in p.title for p in normal)  # names another person even in an ordinary story
    assert [p.title for p in sens][0] == "File:NVIDIA Headquarters.jpg"
    assert all("Jensen" not in p.title for p in sens)


# --- providers and discovery --------------------------------------------------------------------------------------------


def _commons_page(name: str, i: int, *, licence: str = "CC BY-SA 4.0", url: str = "https://creativecommons.org/licenses/by-sa/4.0",
                  width: int = 4000, height: int = 2400, year: str = "2023", artist: str = "<a href='u'>Jane Doe</a>") -> dict[str, Any]:
    fname = name.replace(" ", "_")
    return {"pageid": i, "index": i, "title": f"File:{name}", "imageinfo": [{
        "url": f"https://upload.wikimedia.org/wikipedia/commons/a/ab/{fname}",
        "thumburl": f"https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/{fname}/1920px-{fname}",
        "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{fname}", "width": width, "height": height, "mime": "image/jpeg",
        "extmetadata": {"LicenseShortName": {"value": licence}, "LicenseUrl": {"value": url}, "Artist": {"value": artist},
                        "ImageDescription": {"value": ""}, "Categories": {"value": "Self-published work"},
                        "DateTimeOriginal": {"value": f"{year}-05-01 10:00:00"}}}]}


def _ov(title: str, *, source: str = "flickr", lic: str = "by", ver: str = "2.0", url: str = "", page: str = "",
        width: int = 4000, height: int = 2250, tags: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"id": title, "title": title, "url": url or f"https://live.staticflickr.com/1/{title.replace(' ', '_')}.jpg",
            "foreign_landing_url": page or f"https://www.flickr.com/photos/x/{abs(hash(title))}", "creator": "Sam Roe",
            "license": lic, "license_version": ver, "license_url": f"https://creativecommons.org/licenses/{lic}/{ver}/",
            "provider": source, "source": source, "width": width, "height": height, "filetype": "jpg",
            "tags": [{"name": t, "accuracy": None} for t in tags] + [{"name": "logo", "accuracy": 0.9}]}  # machine tags are ignored


def _transport(pages: list[dict[str, Any]], ov: list[dict[str, Any]] | None = None, *, ov_status: int = 200, calls: list[str] | None = None,
               pexels: list[dict[str, Any]] | None = None) -> httpx.MockTransport:
    def handle(r: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(f"{r.url.host}{r.url.path}")
        assert "@" not in r.headers.get("user-agent", "")  # never a personal email in requests
        if r.url.host == "commons.wikimedia.org":
            return httpx.Response(200, json={"query": {"pages": {str(p["pageid"]): p for p in pages}}})
        if r.url.host == "api.openverse.org":
            q = dict(r.url.params)
            assert q["license_type"] == "commercial" and set(q["license"].split(",")) == {"by", "by-sa", "cc0", "pdm"}
            assert q["category"] == "photograph" and q["aspect_ratio"] == "wide" and q["size"] == "large"
            if ov_status != 200:
                return httpx.Response(ov_status, text="<html>Just a moment...</html>")
            return httpx.Response(200, json={"result_count": len(ov or []), "results": ov or []})
        if r.url.host == "api.pexels.com":
            assert r.headers.get("authorization") == "test-key"
            return httpx.Response(200, json={"photos": pexels or []})
        return httpx.Response(404)
    return httpx.MockTransport(handle)


async def test_discover_filters_ranks_and_reads_commons_metadata() -> None:
    pages = [_commons_page("Binance headquarters Dubai.jpg", 1),
             _commons_page("Binance logo.jpg", 2),
             _commons_page("Binance office NC.jpg", 3, licence="CC BY-NC 2.0", url="https://creativecommons.org/licenses/by-nc/2.0"),
             _commons_page("Server room at CERN.jpg", 4),
             _commons_page("Binance booth small.jpg", 5, width=1000, height=600)]
    async with httpx.AsyncClient(transport=_transport(pages)) as c:
        got = await P.discover(c, ["Binance"], ["Binance"])
    assert [p.title for p in got] == ["File:Binance headquarters Dubai.jpg"]
    p = got[0]
    assert p.creator == "Jane Doe" and p.licence_name == "CC BY-SA 4.0" and p.year == 2023 and p.tags == ("Self-published work",)
    assert p.url.endswith("1920px-Binance_headquarters_Dubai.jpg") and p.page.startswith("https://commons.wikimedia.org/wiki/File:")
    assert p.credit == "Photo: Jane Doe / Wikimedia Commons (CC BY-SA 4.0)"


async def test_one_dead_provider_never_breaks_discovery() -> None:
    pages = [_commons_page("Binance headquarters Dubai.jpg", 1)]
    async with httpx.AsyncClient(transport=_transport(pages, ov_status=403)) as c:  # Openverse's Cloudflare challenge
        assert len(await P.discover(c, ["Binance"], ["Binance"])) == 1

    def boom(r: httpx.Request) -> httpx.Response:
        if r.url.host == "commons.wikimedia.org":
            raise httpx.ConnectError("down", request=r)
        return httpx.Response(200, json={"results": [_ov("Binance headquarters Paris")]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as c:
        got = await P.discover(c, ["Binance"], ["Binance"])
    assert [p.provider for p in got] == ["openverse"] and got[0].credit == "Photo: Sam Roe / Flickr (CC BY 2.0)"

    def junk(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")
    async with httpx.AsyncClient(transport=httpx.MockTransport(junk)) as c:
        assert await P.discover(c, ["Binance"], ["Binance"]) == []


async def test_openverse_licences_and_tags() -> None:
    ov = [_ov("Binance tower at night", tags=("binance",)), _ov("Binance tower NC", lic="by-nc"), _ov("Binance tower ND", lic="by-nd"),
          _ov("Binance plaza", lic="cc0", ver="1.0"), _ov("Binance square", lic="pdm", ver="1.0"), _ov("Binance tall", width=1500, height=2500)]
    async with httpx.AsyncClient(transport=_transport([], ov)) as c:
        got = await P.discover(c, ["Binance"], ["Binance"])
    assert sorted(p.title for p in got) == ["Binance plaza", "Binance square", "Binance tower at night"]
    assert {p.licence_name for p in got} == {"CC BY 2.0", "CC0", "public domain"}
    assert all("logo" not in p.tags for p in got)  # the machine-generated tag was dropped (else "logo" would have rejected it)


async def test_dedupe_across_providers_and_numbered_series() -> None:
    pages = [_commons_page("Binance headquarters Dubai.jpg", 1), _commons_page("Binance headquarters Dubai 2023-05-01 10-00-01.jpg", 2),
             _commons_page("Binance headquarters Dubai 2023-05-01 10-00-02.jpg", 3)]
    same = _ov("Binance headquarters Dubai", source="wikimedia", lic="by-sa", ver="4.0",
               url="https://upload.wikimedia.org/wikipedia/commons/a/ab/Binance_headquarters_Dubai.jpg",
               page="https://commons.wikimedia.org/wiki/File:Binance_headquarters_Dubai.jpg")
    async with httpx.AsyncClient(transport=_transport(pages, [same])) as c:
        got = await P.discover(c, ["Binance", "Binance headquarters"], ["Binance"])
    assert len(got) == 1 and got[0].provider == "wikimedia"  # the same file via Openverse and a second query collapses to one


async def test_used_urls_are_skipped() -> None:
    pages = [_commons_page("Binance headquarters Dubai.jpg", 1), _commons_page("Binance office Paris.jpg", 2)]
    used_page = "https://commons.wikimedia.org/wiki/File:Binance_headquarters_Dubai.jpg"
    async with httpx.AsyncClient(transport=_transport(pages)) as c:
        got = await P.discover(c, ["Binance"], ["Binance"], used={used_page})
    assert [p.title for p in got] == ["File:Binance office Paris.jpg"]
    now = datetime.now(timezone.utc)
    recs = [("a", now - timedelta(days=3)), ("b", (now - timedelta(days=20)).isoformat()), ("c", "garbage"), ("d", None)]
    assert P.used_urls(recs, now=now) == {"a"}


async def test_pexels_only_with_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    pex = [{"id": 1, "width": 5000, "height": 3000, "url": "https://www.pexels.com/photo/binance-sign-1/", "photographer": "Ann Lee",
            "alt": "Binance office sign at night",
            "src": {"original": "https://images.pexels.com/1.jpeg", "large2x": "https://images.pexels.com/1-l.jpeg"}}]
    calls: list[str] = []
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    async with httpx.AsyncClient(transport=_transport([], calls=calls, pexels=pex)) as c:
        assert await P.discover(c, ["Binance"], ["Binance"]) == []
    assert not any("pexels" in x for x in calls)
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    async with httpx.AsyncClient(transport=_transport([], calls=calls, pexels=pex)) as c:
        got = await P.discover(c, ["Binance"], ["Binance"])
    assert [p.provider for p in got] == ["pexels"]
    assert got[0].licence_url == P.PEXELS_LICENCE_URL and got[0].credit == "Photo: Ann Lee / Pexels (Pexels License)"


# --- pixels -------------------------------------------------------------------------------------------------------------


def _scene(w: int, h: int, box: tuple[int, int, int, int], seed: int = 1) -> Image.Image:
    rng = np.random.default_rng(seed)
    base = np.zeros((h, w, 3)) + np.array([24, 34, 48])
    base += rng.normal(0, 6, (h, w, 3))
    img = Image.fromarray(base.clip(0, 255).astype("uint8"))
    d = ImageDraw.Draw(img)
    d.rectangle(box, fill=(250, 210, 60))
    d.ellipse((box[0] + 20, box[1] + 20, box[0] + 80, box[1] + 80), fill=(200, 30, 40))
    return img


def test_smart_crop_keeps_the_subject_in_a_wide_picture() -> None:
    img = _scene(3200, 1000, (2550, 350, 2950, 700))  # subject far right; a centre crop would cut it off
    out, box = P.smart_crop(img)
    assert out.size == (1600, 900)
    assert 0 < box[0] < box[2] <= 1 and box[2] - box[0] < 0.6
    arr = np.asarray(out)
    yellow = (arr[..., 0] > 200) & (arr[..., 1] > 170) & (arr[..., 2] < 120)
    assert yellow.sum() > 0.9 * (400 * 0.9) * (350 * 0.9)  # (scaled) subject fully inside
    xs = np.where(yellow.any(axis=0))[0] / 1600
    assert box[0] - 0.05 <= xs.min() and xs.max() <= box[2] + 0.05  # the reported box is where the subject is


def test_smart_crop_keeps_the_subject_in_a_tall_picture() -> None:
    img = _scene(1800, 1800, (700, 1450, 1100, 1750))  # subject near the bottom
    out, box = P.smart_crop(img)
    arr = np.asarray(out)
    yellow = (arr[..., 0] > 200) & (arr[..., 1] > 170) & (arr[..., 2] < 120)
    assert yellow.sum() > 0.9 * (400 * 0.889) * (300 * 0.889)
    assert box[1] > 0.4  # the subject sits low in the crop


def test_subject_box_finds_the_subject() -> None:
    x0, y0, x1, y1 = P.subject_box(_scene(1600, 900, (200, 300, 600, 600)))
    assert x0 < 200 / 1600 + 0.03 and x1 > 600 / 1600 - 0.03 and x1 < 0.6
    assert y0 < 300 / 900 + 0.05 and y1 > 600 / 900 - 0.05


def test_text_zone_avoids_the_subject() -> None:
    assert P.text_zone((0.6, 0.15, 0.95, 0.85)) == "left"
    assert P.text_zone((0.05, 0.15, 0.4, 0.85)) == "right"
    assert P.text_zone((0.1, 0.05, 0.9, 0.45)) == "lower"
    assert P.text_zone((0.1, 0.6, 0.9, 0.95)) == "upper"
    img = _scene(3200, 1000, (2550, 350, 2950, 700))
    _, box = P.smart_crop(img)
    zone = P.text_zone(box)
    assert zone == "left"
    assert P.covers(box, P.zone_rect(zone, box)) < 0.1


def test_covers() -> None:
    box = (0.5, 0.5, 1.0, 1.0)
    assert P.covers(box, (0.0, 0.0, 0.5, 1.0)) == 0
    assert P.covers(box, (0.0, 0.75, 1.0, 1.0)) == pytest.approx(0.5)
    assert P.covers(box, (0.0, 0.0, 1.0, 1.0)) == 1
    assert P.covers((0.2, 0.2, 0.2, 0.4), (0, 0, 1, 1)) == 0  # an empty subject


def test_colour_and_graphics() -> None:
    photo = _scene(800, 450, (300, 100, 500, 300), seed=3)
    grey = ImageOps.grayscale(photo).convert("RGB")
    sepia = ImageOps.colorize(ImageOps.grayscale(photo), (40, 20, 5), (250, 230, 190))
    assert P.is_colour(photo) and not P.is_colour(grey) and not P.is_colour(sepia)
    flat = Image.new("RGB", (800, 450), (255, 255, 255))
    ImageDraw.Draw(flat).rectangle((100, 100, 400, 300), fill=(0, 80, 200))
    assert P.looks_like_graphic(flat)
    rng = np.random.default_rng(0)
    textured = Image.fromarray((rng.random((450, 800, 3)) * 255).astype("uint8"))
    assert not P.looks_like_graphic(textured)
