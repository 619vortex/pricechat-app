"""The picture format (owner, 2026-11-09, 2026-11-10): every post is one 16:9 picture, one bold factual headline and a small source credit.
Exceptional market moves get a chart from our own prices, major breaking stories editorial artwork credited as an illustration, companies,
governments and countries a real licensed photo of the subject, the rest a branded graphic. No test reaches a real service."""

import io
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.members.referrals import program
from app.models.newsdesk import NdEvent
from app.newsdesk import engine, events, recap, sources, telegram as lane, visual

from tests.test_newsdesk_telegram import Tg as _Tg, verified


class Tg(_Tg):
    """Telegram, taking the post's JPEG picture."""

    async def send_photo(self, chat: Any, png: bytes, caption: str, **kw: Any) -> dict[str, Any]:
        assert png[:3] == bytes([0xFF, 0xD8, 0xFF]) or png[:4] == bytes([0x89]) + b"PNG"
        self.sent.append({"chat": chat, "text": caption, "card": True, "image": png, **kw})
        return self._answer()

SLOT = datetime(2026, 11, 9, 4, 0, tzinfo=timezone.utc)  # 08:00 Dubai


@dataclass
class Mkt:
    coin: str
    mark: float
    prev_day: float


@dataclass
class Candle:
    t: int
    c: float


class FakeHL:
    def __init__(self, coins: dict[str, tuple[float, float]]) -> None:
        self.coins = coins

    def market(self, coin: str) -> Mkt:
        mark, prev = self.coins[coin]
        return Mkt(coin, mark, prev)

    def candles(self, coin: str, interval: str, count: int) -> list[Candle]:
        mark, prev = self.coins[coin]
        t0 = int(SLOT.timestamp() * 1000) - 96 * 900_000
        # real prices wiggle (a straight line is refused as interpolated); the ends are exactly the 24-hour move
        wig = lambda i: mark * 0.002 * (math.sin(math.pi * i * 7 / 96) + 0.5 * math.sin(math.pi * i * 31 / 96))  # noqa: E731
        return [Candle(t0 + i * 900_000, prev + (mark - prev) * i / 96 + wig(i)) for i in range(97)]


HAMMERED = FakeHL({"BTC": (73900.0, 75900.0), "ETH": (2094.0, 2407.0), "HYPE": (72.5, 75.0), "SOL": (85.6, 88.0)})  # ETH -13.0%: exceptional
ROUTINE = FakeHL({"BTC": (73900.0, 75900.0), "ETH": (2094.0, 2240.0), "HYPE": (72.5, 75.0), "SOL": (85.6, 88.0)})  # ETH -6.5%: under its 8% line
QUIET = FakeHL({"BTC": (75100.0, 75000.0), "ETH": (2240.0, 2236.0), "HYPE": (75.0, 74.6), "SOL": (88.0, 87.9)})


@pytest.fixture(autouse=True)
def _visual(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "DEFAULT_SCHEDULE", "slots")
    monkeypatch.setattr(sources, "EXTRA_FEEDS", [])
    monkeypatch.setattr(sources, "WORLDMONITOR_FEEDS", [])
    monkeypatch.setattr(lane, "FORMAT", "visual")
    monkeypatch.setattr(engine, "X_FORMAT", "visual")
    monkeypatch.setattr(recap, "VISUAL", True)
    events._alerted.clear()
    program(db).updates_channel = "@Pricechatupdates"
    db.commit()


def jpeg_size(data: bytes) -> tuple[int, int]:
    assert data[:3] == b"\xff\xd8\xff"
    return Image.open(io.BytesIO(data)).size


async def decide(db: Session, now: datetime, market: Any) -> dict[str, Any]:
    desk = engine.settings_row(db)
    desk.state = {"last_run": {"at": now.isoformat()}}
    db.commit()

    async def agrees(*a: Any, **k: Any) -> tuple[bool, str]:
        return True, "agrees"

    return await events.decide(db, now, quotes={}, watchlist=[], categories=desk.categories or {}, desk_state=desk.state, second_opinion=agrees, liquidations=False,
                               market=market)


# --- the words ---------------------------------------------------------------------------------------------------------


def test_the_headline_says_exactly_what_the_data_says_and_the_verb_is_earned_by_the_severity_tier() -> None:
    assert visual.move_headline("Oil (WTI)", -6.0) == "WTI CRUDE FALLS 6.0% IN 24 HOURS"  # no "gets hammered": the number carries it
    assert visual.move_headline("Ethereum", -13.0) == "ETHEREUM TUMBLES 13.0% IN 24 HOURS"  # a tier past the 8% line
    assert visual.move_headline("Gold", 12.0) == "GOLD SOARS 12.0% IN 24 HOURS"  # three tiers for a commodity
    assert visual.move_headline("Solana", 16.0) == "SOLANA RISES 16.0% IN 24 HOURS"  # a smaller coin's line is 15%
    for h in (visual.move_headline(n, p) for n, p in (("Bitcoin", -40.0), ("S&P 500", 3.0))):
        assert not any(w in h for w in ("HAMMERED", "CRASH", "BLOODBATH", "—", "!"))
    assert visual.caption_html("A & B", "CoinDesk") == "<b>A &amp; B</b>\n\n<i>Source: CoinDesk</i>"


def test_a_news_headline_is_the_verified_one_written_short_with_no_labels() -> None:
    ev = NdEvent(key="k", kind="news", title="t", headline="⚡ JUST IN: U.S. Treasury moves to cut off MEXC over $16 billion in alleged illicit flows")
    assert visual.post_headline(ev) == "U.S. TREASURY MOVES TO CUT OFF MEXC OVER $16B IN ALLEGED ILLICIT FLOWS"


# --- the picture -------------------------------------------------------------------------------------------------------


def test_the_chart_is_a_16_by_9_picture_from_the_data() -> None:
    pts = [(1_790_000_000 + i * 900, 2240 - i * 1.5) for i in range(97)]
    assert jpeg_size(visual.chart(pts, name="Ethereum", ticker="ETH", pct=-6.5)) == (1600, 900)
    with pytest.raises(ValueError):
        visual.chart(pts[:5], name="Ethereum", ticker="ETH", pct=-6.5)


def test_photo_search_matches_the_subject() -> None:
    q = visual.subject_queries("NVIDIA INVESTIGATES CEO OVER AFFAIR WITH SUSPECTED CHINESE SPY")
    assert q[0] == ("nvidia headquarters", "nvidia") and ("Beijing CBD skyline", "beijing") in q
    # no generic stand-ins: a hack is not a photo of someone else's server room (the CERN data centre, 2026-11-09)
    assert visual.subject_queries("MERCADO BITCOIN BEGINS POST-HACK REOPENING") == []
    assert visual.subject_queries("U.S. TREASURY MOVES TO CUT OFF MEXC")[0][1] == "mexc"
    assert ("United States Treasury Building", "treasury") in visual.subject_queries("U.S. TREASURY MOVES TO CUT OFF MEXC")


def _lp(title: str, url: str, creator: str, licence: str) -> Any:
    from app.newsdesk import photos

    return photos.LicensedPhoto(title=title, url=url, page=url + "#page", creator=creator, provider="wikimedia", licence=licence, width=1920, height=1080)


def _scene(bg: tuple[int, int, int], fg: tuple[int, int, int], box: tuple[int, int, int, int]) -> Image.Image:
    """A photo-like test picture: noisy colour everywhere (a photograph's colours are spread out) and a bright subject."""
    import random

    rnd = random.Random(7)
    small = Image.new("RGB", (192, 108))
    small.putdata([tuple(max(0, min(255, c + rnd.randint(-60, 60))) for c in bg) for _ in range(192 * 108)])
    img = small.resize((1920, 1080), Image.Resampling.BILINEAR)
    ImageDraw.Draw(img).rectangle(box, fill=fg)
    return img


async def test_a_news_picture_is_a_real_licensed_photo_of_the_subject_never_reused_or_black_and_white(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.newsdesk import photos

    colour = _scene((180, 60, 40), (30, 120, 200), (1100, 300, 1600, 900))
    grey = Image.new("RGB", (1920, 1080), (120, 120, 120))
    asked: dict[str, Any] = {}

    async def discover(client: Any, queries: list[str], terms: list[str], *, used: Any = (), sensitive: bool = False, **_: Any) -> list[Any]:
        asked.update(queries=queries, terms=terms, used=set(used), sensitive=sensitive)
        return [p for p in [_lp("File:Treasury old.jpg", "https://img/old.jpg", "B", "Public domain"),
                            _lp("File:Treasury Building (1).jpg", "https://img/t1.jpg", "C", "CC BY-SA 3.0"),
                            _lp("File:Treasury Building (2).jpg", "https://img/t2.jpg", "D", "CC BY-SA 3.0")] if p.url not in used]

    async def fetch(client: Any, url: str) -> Image.Image:
        return grey if url.endswith("old.jpg") else colour

    monkeypatch.setattr(photos, "discover", discover)
    monkeypatch.setattr(visual, "fetch_image", fetch)
    ev = NdEvent(key="k", kind="news", title="Treasury cuts off MEXC", headline="U.S. TREASURY MOVES TO CUT OFF MEXC", topic="controversy", sources=[{"name": "CoinDesk"}], facts={})
    data, meta = await visual._for_event(ev, used={"https://img/t1.jpg"})  # the selection itself
    assert jpeg_size(data) == (1600, 900) and meta["url"] == "https://img/t2.jpg" and meta["kind"] == "photo"
    published, why = await visual.for_event(ev, used={"https://img/t1.jpg"})  # what goes out: no vision check here, so text
    assert published == b"" and "not visually verified" in why["editor"]["why"]
    assert asked["sensitive"] is True and "https://img/t1.jpg" in asked["used"]  # a sanctions story: no people in the photo
    assert meta["credit"] == "Photo: D / Wikimedia Commons (CC BY-SA 3.0)" and meta["photo"]["licence"] == "CC BY-SA 3.0" and meta["photo"]["page"]
    assert meta["design"] in ("band", "side", "overlay") and meta["covered"] <= 0.08  # the headline never sits on the subject
    ev.facts = {"visual": meta}  # X uses the same picture as Telegram
    _, again = await visual._for_event(ev)
    assert again["url"] == "https://img/t2.jpg" and again["design"] == meta["design"]


async def test_a_major_breaking_story_gets_no_artwork_standing_in_for_a_photo(monkeypatch: pytest.MonkeyPatch) -> None:
    async def never(client: Any, query: str, limit: int = 12) -> list[visual.Photo]:
        raise AssertionError("a hack is never illustrated with a stock photo")

    monkeypatch.setattr(visual, "find_photos", never)
    ev = NdEvent(key="k", kind="news", title="Bybaz hacked", headline="BYBAZ EXCHANGE SAYS HACKERS DRAINED $40 MILLION", topic="hack", sources=[{"name": "The Block"}],
                 facts={}, first_seen=SLOT, virality=70, label="")
    data, meta = await visual.for_event(ev)
    # owner, 2026-11-19: no generic AI headline card stands in for news: text only
    assert data == b"" and meta["kind"] == "text" and meta["editor"]["was"] == "artwork"


# --- the slots, end to end ---------------------------------------------------------------------------------------------


async def test_a_slot_without_a_story_posts_the_biggest_real_move_as_a_chart_on_telegram_and_x(db: Session) -> None:
    now = SLOT + timedelta(minutes=1)
    await decide(db, now, HAMMERED)
    ev = db.scalar(select(NdEvent).where(NdEvent.status == "released"))
    assert ev is not None and ev.kind == "recap" and visual.post_headline(ev) == "ETHEREUM TUMBLES 13.0% IN 24 HOURS"
    tg = Tg()
    out = await lane.tick(db, get_settings(), tg, now, quotes={})
    sent = tg.sent[0]
    assert out["sent"] == 144 and sent["card"] is True
    assert sent["text"] == "<b>ETHEREUM TUMBLES 13.0% IN 24 HOURS</b>\n\n<i>Source: Hyperliquid</i>"  # nothing else
    desk = engine.settings_row(db)
    await engine.deliver_x(db, desk, get_settings(), None, 0, now)
    db.refresh(ev)
    post = db.get(__import__("app.models.newsdesk", fromlist=["NdPost"]).NdPost, ev.x_post_id)
    assert ev.x_status == "dry_run" and post.text == "ETHEREUM TUMBLES 13.0% IN 24 HOURS\n\nSource: Hyperliquid"


async def test_a_quiet_market_and_no_story_means_no_post_rather_than_filler(db: Session) -> None:
    await decide(db, SLOT + timedelta(minutes=1), QUIET)
    assert db.scalar(select(NdEvent).where(NdEvent.status == "released")) is None


async def test_a_routine_move_is_not_news_and_the_slot_stays_empty(db: Session) -> None:
    await decide(db, SLOT + timedelta(minutes=1), ROUTINE)  # Ethereum -6.5% in 24 h: posted twice on 2026-11-09 as a "brief"; under its 8% line
    assert db.scalar(select(NdEvent).where(NdEvent.status == "released")) is None


async def test_a_verified_story_goes_out_as_picture_headline_and_source_and_nothing_else(db: Session) -> None:
    verified(db, "Bybaz exchange says hackers drained $400 million from its hot wallet", now=SLOT, hours=0.2)
    now = SLOT + timedelta(minutes=2)
    await decide(db, now, QUIET)
    tg = Tg()
    await lane.tick(db, get_settings(), tg, now, quotes={})
    sent = tg.sent[0]
    # Price Chat News (2026-11-11): a written caption with context, the byline, the verification line and the outlets' own article links
    assert sent["card"] is False and sent["text"].startswith("<b>Bybaz exchange says hackers drained $400M from its hot wallet</b>")
    assert "Price Chat News" in sent["text"] and "Verified across 2 outlets" in sent["text"] and '<a href="https://' in sent["text"]
    assert "PRICE ALERT" not in sent["text"] and "#" not in sent["text"] and "exclusive" not in sent["text"].lower()
    seen = lane.settings_row(db).state["recent_visuals"]
    assert seen[-1]["kind"] == "text"  # no real photo of the exchange: clean text, not a typographic headline card (owner, 2026-11-19)


async def test_a_company_story_gets_a_real_licensed_photo_of_the_company(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.newsdesk import photos

    async def discover(client: Any, queries: list[str], terms: list[str], **_: Any) -> list[Any]:
        assert "nvidia" in terms
        return [_lp("File:NVIDIA Headquarters.jpg", "https://img/nv.jpg", "Coolcaesar", "CC BY-SA 4.0")]

    async def fetch(client: Any, url: str) -> Image.Image:
        return _scene((40, 90, 160), (230, 200, 60), (200, 300, 700, 800))

    monkeypatch.setattr(photos, "discover", discover)
    monkeypatch.setattr(visual, "fetch_image", fetch)
    ev = NdEvent(key="k2", kind="news", title="Nvidia board investigates Jensen Huang", headline="NVIDIA BOARD INVESTIGATES JENSEN HUANG", topic="corporate",
                 sources=[{"name": "Decrypt"}], facts={"importance": {"kind": "corporate"}})
    data, meta = await visual._for_event(ev)
    assert meta["kind"] == "photo" and meta["url"] == "https://img/nv.jpg" and meta["credit"] == "Photo: Coolcaesar / Wikimedia Commons (CC BY-SA 4.0)"
    assert jpeg_size(data) == (1600, 900) and meta["headline_px"] >= 44


# --- X image upload ----------------------------------------------------------------------------------------------------


async def test_x_uploads_the_picture_and_attaches_it_to_the_post(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.connectors.x.client import XClient
    from app.security.credentials import XCredentials

    monkeypatch.setattr("app.social.guard.require_permit", lambda *a, **k: None)
    seen: list[tuple[str, bytes]] = []

    def x(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.content))
        if request.url.path == "/2/media/upload":
            return httpx.Response(200, json={"data": {"id": "1880", "media_key": "3_1880"}})
        return httpx.Response(201, json={"data": {"id": "999", "text": "t"}})

    c = XClient(XCredentials("b", "k", "s", "t", "ts"), "https://api.x.test", transport=httpx.MockTransport(x))
    media = await c.upload_image(b"\xff\xd8\xff" + b"0" * 100, permit=None)  # type: ignore[arg-type]
    await c.create_post("HEADLINE\n\nSource: CoinDesk", permit=None, media_ids=[media])  # type: ignore[arg-type]
    assert media == "1880" and seen[0][0] == "/2/media/upload" and b"tweet_image" in seen[0][1]
    assert b'"media": {"media_ids": ["1880"]}' in seen[1][1] or b'"media":{"media_ids":["1880"]}' in seen[1][1]
