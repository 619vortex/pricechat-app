"""The Visual Editor and the premium templates (owner, 2026-11-19, URGENT: "PRICE CHAT'S AUTOMATED POST IMAGES ARE EMBARRASSING").

Every case here is a real post from the audit of Telegram Pricechatupdates 144-195 (scratchpad audit, 2026-11-19), with the photo's real
Wikimedia Commons record where it came from Commons. No network: photo searches, prices and the vision model are stubbed."""
import io
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from PIL import Image, ImageDraw

from app.models.newsdesk import NdEvent
from app.newsdesk import clean, datacards, editor, photos, templates, visual

AT = datetime(2026, 11, 19, 0, 1, tzinfo=timezone.utc)


def ev(headline: str, kind: str = "news", key: str = "k", **facts: Any) -> NdEvent:
    e = NdEvent(key=key, kind=kind, title=headline, headline=headline, summary="", topic="", label="", evidence="two outlets", category="news",
                sources=[{"name": "CNBC", "url": "https://www.cnbc.com/x"}], facts=facts, released_at=AT)
    e.id = 195
    return e


def commons(title: str, *tags: str, w: int = 4000, h: int = 2300) -> photos.LicensedPhoto:
    slug = title.removeprefix("File:").replace(" ", "_")
    return photos.LicensedPhoto(title=title, url=f"https://upload.wikimedia.org/{slug}", page=f"https://commons.wikimedia.org/wiki/{title}",
                                creator="Someone", provider="wikimedia", source="wikimedia", licence="CC BY-SA 4.0", width=w, height=h, tags=tags)


# the real Commons records behind the published pictures (looked up 2026-11-19)
TREEHOUSE = commons("File:A small treehouse on Microsoft campus.jpg", "Microsoft Redmond campus", "Self-published work",
                    "Treehouses in the United States", "Taken with Google Pixel 4a", w=4031, h=2358)  # post 195
CITIGROUP_CENTER = commons("File:Citigroup Center August 2024.jpg", "Citigroup Center", "Self-published work", "Midtown Manhattan in 2024")  # post 193
CITI_FIELD = commons("File:Citi Field 2026.jpg", "Citi Field", "Self-published work")
NVIDIA_AERIAL = commons("File:Nvidia campus aerial.jpg", "Self-published work", "Aerial photographs of Santa Clara County, California")  # post 190
NVIDIA_HQ = commons("File:NVIDIA Headquarters.jpg", "Taken with Canon EOS 700D", "Self-published work", "Buildings numbered 2788")  # post 178
NVIDIA_OFFICES = commons("File:2788-2888 San Tomas Expressway.jpg", "San Tomas Expressway", "Self-published work", "Nvidia offices")
KNESSET = commons("File:Knesset front side - 2022.jpg", "Knesset building", "Self-published work")  # post 180
APPLE_PARK = commons("File:Aerial view of Apple Park dllu.jpg", "Featured pictures of California", "Self-published work")  # post 188


def photo_like(w: int = 1920, h: int = 1080, seed: int = 3) -> Image.Image:
    import random

    rnd = random.Random(seed)
    small = Image.new("RGB", (w // 3, h // 3))
    lum = [rnd.randint(15, 240) for _ in range(small.width * small.height)]  # light and dark areas, tinted: a photograph's spread
    small.putdata([(v, max(0, min(255, v + rnd.randint(-40, 40))), max(0, min(255, v + rnd.randint(-60, 20)))) for v in lum])
    return small.resize((w, h), Image.Resampling.BILINEAR)


# ------------------------------------------------------------------------------------------------ the photo's subject, not its name
def test_post_195_the_microsoft_treehouse_is_a_generic_scene_not_microsoft() -> None:
    b = editor.brief(ev("Microsoft had rallied back to record Highs. Then came the election and Outlook outage."))
    fit = editor.depiction(TREEHOUSE.title, TREEHOUSE.tags, "", "microsoft", b)
    assert not fit.ok and fit.depiction == "generic" and fit.role == "treehouse"
    # the old rule passed it: the name was in the title, and that was all it asked
    assert photos.relevant(TREEHOUSE, ["Microsoft"]) is None


@pytest.mark.parametrize("record,subject,headline", [
    (CITIGROUP_CENTER, "citigroup", "Citigroup and Circle to help World Bank study stablecoin payouts"),  # post 193: an office tower
    (NVIDIA_AERIAL, "nvidia", "Nvidia stock up 3% as earnings beat expectations"),  # post 190: sprawl from the air
    (NVIDIA_OFFICES, "nvidia", "Nvidia hires Wachtell Lipton to investigate CEO"),  # an address, the brand only in a category
])
def test_a_building_or_campus_without_visible_branding_needs_the_vision_check(record: Any, subject: str, headline: str) -> None:
    fit = editor.depiction(record.title, record.tags, "", subject, editor.brief(ev(headline)))
    assert not fit.ok and fit.depiction in ("weak", "generic")  # an address alone ("San Tomas Expressway") reads as a street scene
    if fit.depiction == "generic":
        return
    ok, why = editor.vision_verdict(None, fit)  # no model available: refused, never waved through
    assert not ok and "no vision check" in why
    yes = {"shows_subject": True, "recognisable": True, "generic": False, "misleading": False, "text_heavy": False, "what": "Nvidia sign on a building"}
    assert editor.vision_verdict(yes, fit)[0] is True
    assert editor.vision_verdict({**yes, "generic": True, "what": "an office park"}, fit)[0] is False
    assert editor.vision_verdict({**yes, "recognisable": False}, fit)[0] is False


def test_a_sponsor_named_stadium_is_not_the_bank() -> None:
    fit = editor.depiction(CITI_FIELD.title, CITI_FIELD.tags, "", "citi", editor.brief(ev("Citi to cut 2,000 jobs")))
    assert not fit.ok and fit.depiction == "generic"


@pytest.mark.parametrize("record,subject,role", [
    (NVIDIA_HQ, "nvidia", "headquarters"),  # post 178/182: the sign on its headquarters
    (APPLE_PARK, "apple", "headquarters"),  # Apple Park is a headquarters, not a park
    (commons("File:Tesla store, Main Street, Palo Alto.jpg"), "tesla", "sign"),  # a street in an address doesn't make it a street scene
    (commons("File:Treasury Building, Washington DC.jpg"), "treasury", "institution"),  # the acting institution's landmark
])
def test_the_subjects_own_sign_headquarters_or_landmark_is_a_strong_picture(record: Any, subject: str, role: str) -> None:
    fit = editor.depiction(record.title, record.tags, "", subject, editor.brief(ev(f"{subject.title()} news today")))
    assert fit.ok and fit.depiction == "strong" and fit.role == role


def test_a_microsoft_cafeteria_or_lobby_is_still_generic_even_at_headquarters() -> None:
    fit = editor.depiction("File:Microsoft headquarters cafeteria.jpg", (), "", "microsoft", editor.brief(ev("Microsoft cuts jobs")))
    assert not fit.ok and fit.depiction == "generic"


# ------------------------------------------------------------------------------------------------ wars and disasters
@pytest.mark.parametrize("headline", [
    "Israeli air strike kills senior Hamas commander in Gaza",  # post 180 carried the Knesset building
    "JUST IN: Israeli strike on Nasser Hospital kills at least six, Gaza civil defence agency says",  # post 166: Hebrew room signage
    "Iran launches drone attacks against US military base in Bahrain",
])
def test_a_war_story_gets_only_a_dated_photograph_of_the_event(headline: str) -> None:
    b = editor.brief(ev(headline))
    assert b.cls == "conflict"
    assert not editor.depiction(KNESSET.title, KNESSET.tags, "", "knesset", b).ok
    assert not editor.depiction("File:Gaza City skyline 2019.jpg", (), "", "gaza", b).ok  # a place, before the war, is not the strike
    b.places = ["Gaza"]
    event = editor.depiction("File:Damage in Gaza after Israeli strike, November 2026.jpg", (), "", "gaza", b)
    assert event.ok and event.depiction == "event"


async def test_a_war_story_without_an_event_photo_goes_as_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def discover(client: Any, queries: list[str], terms: list[str], **kw: Any) -> list[Any]:
        return [KNESSET]

    async def fetch(client: Any, p: Any, min_width: int = 0) -> Image.Image:
        return photo_like()

    monkeypatch.setattr(photos, "discover", discover)
    monkeypatch.setattr(photos, "fetch", fetch)
    data, meta = await clean.picture(ev("Israeli air strike kills senior Hamas commander in Gaza"), now=AT)
    assert data == b"" and meta == {"style": "clean", "kind": "text", "layout": "text"}


# ------------------------------------------------------------------------------------------------ the final gate on every lane
@pytest.mark.parametrize("meta", [
    {"kind": "cinematic", "ai_illustration": True, "layout": "overlay"},  # posts 161/162/164: AI tanker, AI server rooms
    {"kind": "photo", "layout": "ai", "style": "clean"},  # posts 169/170/171: clean style's labelled AI picture
    {"kind": "artwork", "layout": "lower"},  # post 159: the INFLATION typography card
    {"kind": "card", "design": "card"},  # post 183: a headline-only card
])
def test_no_ai_illustration_or_headline_card_stands_in_for_news(meta: dict[str, Any]) -> None:
    image, out = editor.review(ev("Deutsche Bank's Bitcoin and Ether custody is open to clients"), b"\xff\xd8\xff fake", meta)
    assert image == b"" and out["kind"] == "text" and out["editor"]["ok"] is False


def test_a_stored_photo_chosen_before_the_editor_is_judged_again_before_x_posts_it() -> None:
    img = io.BytesIO()
    photo_like(1600, 900).save(img, "JPEG")
    meta = {"kind": "photo", "layout": "photo", "style": "clean", "url": TREEHOUSE.url, "title": TREEHOUSE.title, "photo": {"tags": list(TREEHOUSE.tags)}}
    image, out = editor.review(ev("Microsoft had rallied back to record Highs. Then came the election and Outlook outage."), img.getvalue(), meta)
    assert image == b"" and "treehouse" in out["editor"]["why"]
    meta = {"kind": "photo", "layout": "photo", "style": "clean", "url": NVIDIA_HQ.url, "title": NVIDIA_HQ.title, "photo": {"tags": list(NVIDIA_HQ.tags)}}
    image, out = editor.review(ev("Nvidia opens a new research lab in Taipei"), img.getvalue(), meta)
    assert image and out["editor"]["ok"] is True


# ------------------------------------------------------------------------------------------------ pixels
def test_a_screenshot_a_blur_and_a_washed_out_picture_are_refused() -> None:
    shot = photo_like()  # post 167: a photo behind a large white tweet card
    ImageDraw.Draw(shot).rounded_rectangle((900, 250, 1880, 900), radius=30, fill=(255, 255, 255))
    assert "screenshot" in (editor.pixel_problem(shot) or "")
    from PIL import ImageFilter

    blur = photo_like().filter(ImageFilter.GaussianBlur(4))  # an out-of-focus or upscaled thumbnail
    assert "blurry" in (editor.pixel_problem(blur) or "")
    dark = Image.new("RGB", (1920, 1080), (8, 8, 10))  # a sharp subject on a large black background is not blurry
    dark.paste(photo_like(500, 400), (700, 340))
    assert editor.pixel_problem(dark, ui=False) is None
    assert editor.pixel_problem(photo_like()) is None


# ------------------------------------------------------------------------------------------------ one photograph, one story
def test_a_photo_never_illustrates_an_unrelated_story_again() -> None:
    url = NVIDIA_HQ.url
    used = [[url, (AT - timedelta(days=40)).isoformat(), "Nvidia hires Wachtell Lipton to investigate CEO's affair", "k-affair"]]
    assert editor.reuse_problem(url, ev("Nvidia stock up 3% as earnings beat expectations", key="k-earn"), used, AT)
    assert editor.reuse_problem(url, ev("Nvidia hires Wachtell Lipton to investigate CEO's affair", key="k-affair"), used, AT) is None  # same story
    assert editor.reuse_problem(url, ev("Nvidia earnings", key="x"), [[url, (AT - timedelta(days=400)).isoformat()]], AT) is None  # over a year ago
    assert editor.reuse_problem(url, ev("Nvidia earnings", key="x"), [[url, (AT - timedelta(days=3)).isoformat()]], AT)  # an old record: unknown story


# ------------------------------------------------------------------------------------------------ the gold card (post 194)
def gold_closes(last_pct: float = -3.6) -> list[list[float]]:
    """130 real-looking daily closes (they wiggle), the last day's change exactly `last_pct`."""
    t0, out, p = 1_783_000_000, [], 3900.0
    for i in range(129):
        p *= 1 + 0.011 * math.sin(i * 1.7) + 0.004 * math.cos(i * 0.37)
        out.append([t0 + 86400 * i, round(p, 2)])
    out.append([t0 + 86400 * 129, round(out[-1][1] * (1 + last_pct / 100), 4)])
    return out


def gold_post(headline_pct: str = "−3.6%") -> dict[str, Any]:
    return {"kind": "anomaly", "subject": "anomaly:GC=F", "headline": f"Gold's biggest one-day drop since March 2026: {headline_pct}",
            "lines": ["A typical day over the last 90 moved it 1.4%: today was 2.6 times that."], "interest": 80, "why": {},
            "series": {"GC=F": gold_closes()}, "bars": [], "symbols": ["GC=F"], "as_of": AT.isoformat(), "table": []}


@pytest.mark.parametrize("fmt,size", [("tg", (1280, 1280)), ("x", (1600, 900))])
def test_post_194_gold_is_a_huge_red_figure_over_its_real_daily_moves(fmt: str, size: tuple[int, int]) -> None:
    name, args = clean.data_template(gold_post(), AT, False)
    assert name == "anomaly" and args["live"] is True  # measured before the close: labelled with its time, not "closed at"
    data, meta = clean.render(name, args, fmt)
    img = Image.open(io.BytesIO(data)).convert("RGB")
    assert img.size == size and meta["template"] == "anomaly" and round(meta["pct"], 1) == -3.6 and meta["bars"] == 60
    assert templates.readable(meta) is None and meta["phone_hero_px"] >= 56  # the old card's whole headline was 14 px on a phone
    reds = sum(1 for px in img.resize((160, 160)).getdata() if px[0] > 200 and px[1] < 90 and px[2] < 90)
    assert reds > 300  # a red figure that fills a good part of the picture: not a pale thin line


def test_the_gold_card_refuses_a_figure_its_closes_dont_show() -> None:
    name, args = clean.data_template(gold_post("−4.6%"), AT, False)
    with pytest.raises(templates.TemplateRefused):
        clean.render(name, args, "tg")
    data, meta = datacards.data_card(gold_post("−4.6%"), when=AT)  # every lane's data card: text only, never a wrong picture
    assert data == b"" and meta["kind"] == "text"


# ------------------------------------------------------------------------------------------------ the Microsoft card (post 195)
def msft_daily() -> list[tuple[datetime, float]]:
    """MSFT's real daily closes from Yahoo Finance, 15 Oct - 18 Nov 2026 (the 4 Nov record 555.80, then 508.29), after ten earlier sessions."""
    real = [("10-15", 539.44), ("10-16", 529.05), ("10-19", 533.52), ("10-20", 531.87), ("10-21", 532.95), ("10-22", 521.08), ("10-23", 524.51),
            ("10-26", 532.6), ("10-27", 531.33), ("10-28", 531.01), ("10-29", 530.14), ("10-30", 521.91), ("11-02", 507.12), ("11-03", 520.99),
            ("11-04", 555.8), ("11-05", 535.93), ("11-06", 530.61), ("11-09", 545.18), ("11-10", 533.16), ("11-11", 521.13), ("11-12", 523.0),
            ("11-13", 523.88), ("11-16", 517.99), ("11-17", 506.24), ("11-18", 508.29)]
    early = [(datetime(2026, 10, 1 + i, 14, 30, tzinfo=timezone.utc), 528.0 + 2 * math.sin(i)) for i in range(10)]
    return early + [(datetime.fromisoformat(f"2026-{d}T14:30:00+00:00"), c) for d, c in real]


async def test_post_195_without_prices_goes_as_text_never_the_treehouse(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.newsdesk import datapost

    async def discover(client: Any, queries: list[str], terms: list[str], **kw: Any) -> list[Any]:
        return [TREEHOUSE]

    async def nothing(*a: Any, **k: Any) -> list[Any]:
        return []

    monkeypatch.setattr(photos, "discover", discover)
    monkeypatch.setattr(clean, "fetch_bars", nothing)
    monkeypatch.setattr(datapost, "fetch_daily", nothing)
    data, meta = await clean.picture(ev("Microsoft had rallied back to record Highs. Then came the election and Outlook outage."), now=AT)
    assert data == b"" and meta["kind"] == "text"


async def test_a_live_outage_gets_the_outage_card_with_the_service_named(monkeypatch: pytest.MonkeyPatch) -> None:
    async def nothing(*a: Any, **k: Any) -> list[Any]:
        return []

    monkeypatch.setattr(clean, "fetch_bars", nothing)
    monkeypatch.setattr(photos, "discover", nothing)
    e = ev("Microsoft Outlook and Teams down for thousands of users in a global outage")
    assert editor.brief(e).cls == "outage"
    data, meta = await visual.for_event(e, style="clean", now=AT)
    assert meta["template"] == "outage" and meta["status"] == "OUTAGE" and meta["template_args"]["services"] == ["Outlook", "Teams"]
    assert meta["stock"] is None  # no prices read: no stock chip
    assert templates.readable(meta) is None


# ------------------------------------------------------------------------------------------------ market moves and price stories
def candles(pct: float, n: int = 96) -> list[list[float]]:
    t0, out, p = 1_795_000_000.0, [], 100.0
    step = (1 + pct / 100) ** (1 / n)
    for i in range(n):
        o = p
        p = p * step * (1 + 0.0015 * math.sin(i * 1.3))
        out.append([t0 + 900 * i, o, max(o, p) * 1.001, min(o, p) * 0.999, p])
    return out


def test_the_move_card_draws_only_the_figure_its_own_points_show() -> None:
    pts = [(1_795_000_000 + 900 * i, 100 - 0.05 * i + 0.1 * math.sin(i)) for i in range(97)]
    real = (pts[-1][1] / pts[0][1] - 1) * 100
    data, meta = templates.move_card("x", name="Oil (WTI)", ticker="CL", pct=real, points=pts, window="Last 24 hours", source="Yahoo Finance", when=AT)
    assert meta["up"] is False and templates.readable(meta) is None
    with pytest.raises(templates.TemplateRefused):
        templates.move_card("x", name="Oil (WTI)", ticker="CL", pct=real - 1.0, points=pts, window="24h", source="Yahoo", when=AT)


@pytest.mark.parametrize("fmt", ["tg", "x"])
def test_every_template_reads_on_a_phone(fmt: str) -> None:
    pts = [(1_795_000_000 + 86400 * i, 100 + i + 3 * math.sin(i)) for i in range(40)]
    a = [(t, 100 + 0.2 * i + math.sin(i)) for i, (t, _) in enumerate(pts)]
    b = [(t, 100 - 0.6 * i + math.sin(i * 0.7)) for i, (t, _) in enumerate(pts)]
    pa, pb = (a[-1][1] / a[0][1] - 1) * 100, (b[-1][1] / b[0][1] - 1) * 100
    made = [
        templates.move_card(fmt, name="Ether", ticker="ETH", pct=(pts[-1][1] / pts[0][1] - 1) * 100, points=pts, window="Last 24 hours", source="Hyperliquid", when=AT),
        templates.anomaly_card(fmt, name="Gold", symbol="GC=F", closes=gold_closes(), headline_pct=-3.6, since="since March 2026", when=AT),
        templates.battle_card(fmt, names=("Tesla", "Nvidia"), pcts=(pa, pb), series=[a, b], period="past 30 days", when=AT),
        templates.then_now_card(fmt, name="Bitcoin", symbol="BTC-USD", then=pts[0][1], now_price=pts[-1][1], series=pts, when=AT),
        templates.ranking_card(fmt, title="The week's winners and losers", rows=[("Silver", 6.1), ("Gold", 2.0), ("Bitcoin", -4.2), ("Ether", -9.8)], when=AT),
        templates.outage_card(fmt, company="JPMorgan Chase", services=[], status="outage", sector="banking outage", when=AT, source="CNBC",
                              stock=("JPM", -1.2, "24H")),
    ]
    for data, meta in made:
        assert templates.readable(meta) is None, meta
        assert Image.open(io.BytesIO(data)).size == templates.FORMATS[fmt]


def test_a_battle_card_refuses_returns_its_lines_dont_show() -> None:
    a = [(1_795_000_000 + 86400 * i, 100 + 0.1 * i + math.sin(i)) for i in range(30)]
    with pytest.raises(templates.TemplateRefused):
        templates.battle_card("x", names=("Tesla", "Nvidia"), pcts=(30.0, -23.2), series=[a, a], period="past 30 days", when=AT)


# ------------------------------------------------------------------------------------------------ the vision check
async def test_the_vision_check_sends_the_pixels_and_reads_the_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.security import credentials

    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["model"] = body["model"]
        seen["kinds"] = [c["type"] for c in body["messages"][0]["content"]]
        verdict = {"shows_subject": False, "recognisable": False, "generic": True, "misleading": False, "text_heavy": False, "what": "a treehouse in a forest"}
        return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps(verdict)}]})

    monkeypatch.setattr(credentials, "resolve", lambda name, settings=None: "test-key")

    class S:
        newsdesk_model = "claude-haiku-5-5"

    v = await editor.vision(photo_like(), "Microsoft had rallied back to record highs", "Microsoft: its branding", settings=S(),
                            transport=httpx.MockTransport(handler))
    assert seen == {"model": "claude-haiku-5-5", "kinds": ["image", "text"]} and v["generic"] is True
    fit = editor.depiction(NVIDIA_HQ.title, NVIDIA_HQ.tags, "", "nvidia", editor.brief(ev("Nvidia news")))
    assert editor.vision_verdict(v, fit) == (False, "vision: generic picture (a treehouse in a forest)")  # a strong record can still be vetoed


async def test_no_key_means_no_vision_call_and_the_rules_stand() -> None:
    class S:
        newsdesk_model = "claude-haiku-5-5"

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no key: the model is never called")

    assert await editor.vision(photo_like(), "x", "y", settings=S(), transport=httpx.MockTransport(handler)) is None


@pytest.mark.parametrize("headline,cls", [
    ("Saudi Crude Output Jumps 26% as Riyadh Reports Near Pre-War Production", "news"),  # post 163: "pre-war" is not a war story
    ("China, US discuss tariffs as the trade war cools", "news"),
    ("Ukraine, Russia race to build military satellite networks as war rages on", "conflict"),  # post 177
    ("Apple addresses outages affecting Apple Pay, iCloud and apps", "outage"),  # post 188
    ("Microsoft had rallied back to record Highs. Then came the election and Outlook outage.", "corporate"),  # post 195: past context
])
def test_the_story_class_decides_what_may_illustrate_it(headline: str, cls: str) -> None:
    assert editor.brief(ev(headline)).cls == cls
