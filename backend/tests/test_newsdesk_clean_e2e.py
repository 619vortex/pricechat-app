"""End to end, the clean style (owner, 2026-11-12): two outlets report a story; the desk verifies it, releases it in the 04:00 Dubai slot, the
GPU PC writes the caption; Telegram posts the picture first (a licensed photo + the real price chart, or the GPU's labelled illustration when
nothing real fits) with the label the desk earned, 1-3 sentences, clickable sources and @thepricechat; X posts the same story with one link and
the very same picture. No approval step, no real service."""
import io
from datetime import timedelta
from typing import Any

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.models.newsdesk import NdEvent, NdPost, NdRenderJob
from app.newsdesk import clean, engine, events, network, photos, render_queue as rq
from app.newsdesk import telegram as lane
from tests.test_newsdesk_breaking_e2e import DW, NATIONAL, SAYS_DW, SAYS_NATIONAL, _gpu_online, live  # noqa: F401 - the fixture
from tests.test_newsdesk_clean import photo
from tests.test_newsdesk_editorial import SLOT
from tests.test_newsdesk_network_e2e import DW_URL, NAT_URL
from tests.test_newsdesk_render_queue import _picture

TITLE = "Missile strike hits Saudi Aramco oil terminal at Ras Tanura"
DRAFT = {"headline": "Missile hits Aramco's Ras Tanura, the kingdom's largest oil export terminal", "short": "Missile hits Aramco's Ras Tanura terminal",
         "context": "Saudi officials said a missile hit the Ras Tanura terminal, the kingdom's largest oil export facility.", "stakes": "", "question": ""}


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(network, "DEFAULT_STYLE", "clean")


async def _release_and_render(client: Any, db: Session, live: dict[str, Any]) -> tuple[NdEvent, Any]:  # noqa: F811
    w = _gpu_online(client, db)
    at = SLOT + timedelta(minutes=2)
    rq.claim(db, w, {"gpu": "NVIDIA GeForce RTX 5090"}, at - timedelta(seconds=20))
    live["feeds"]["items"] = {DW["url"]: [{"title": TITLE, "link": DW_URL, "at": at - timedelta(minutes=30), "summary": SAYS_DW}],
                              NATIONAL["url"]: [{"title": TITLE + ", officials say", "link": NAT_URL, "at": at - timedelta(minutes=25), "summary": SAYS_NATIONAL}]}
    desk = engine.settings_row(db)
    await engine.collect(db, desk, None, at)
    await events.decide(db, at, quotes={}, watchlist=[], categories=desk.categories or {}, desk_state=desk.state, liquidations=False)
    ev = db.scalars(select(NdEvent).where(NdEvent.status == "released")).one()
    got = rq.claim(db, w, {"gpu": "NVIDIA GeForce RTX 5090"}, at + timedelta(seconds=5))
    rq.complete(db, got["id"], w, _picture(7), {"model": "Qwen/Qwen-Image-2512", "seconds": 73.0, "ocr": {"checked": True, "found": []},
                                                "copy": {"draft": DRAFT, "ok": True, "problems": [], "model": "gpt-oss:20b", "seconds": 41.0}},
                at + timedelta(seconds=90))
    return ev, at


def _telegram_words(cap: str) -> None:
    assert "Missile hits Aramco's Ras Tanura, the kingdom's largest oil export terminal." in cap
    assert 'href="https://www.dw.com/en/missile-strike-hits-saudi-aramco-terminal/a-79912345"' in cap and f'href="{NAT_URL}"' in cap
    assert cap.endswith('<a href="https://t.me/thepricechat">@thepricechat</a>')
    for poster in ("PRICE CHAT NEWS", "Price Chat News", "Verified across", "Why it matters"):
        assert poster not in cap  # no byline, no explainer: the story, its sources, the community


async def test_a_city_stand_in_is_never_its_photo_and_it_gets_no_chart_it_didnt_earn(client: Any, db: Session, live: dict[str, Any],  # noqa: F811
                                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    async def discover(client_: Any, queries: list[str], terms: list[str], **kw: Any) -> list[Any]:
        seen.append(terms)
        if not any(w in " ".join(terms).lower() for w in ("ras tanura", "aramco")):
            return []  # a city stand-in (Riyadh's skyline) is never searched for (owner, 2026-11-16)
        return [photos.LicensedPhoto(url="https://upload.wikimedia.org/ras-tanura.jpg", page="https://commons.wikimedia.org/wiki/File:Ras_Tanura.jpg",
                                     title="File:Aramco oil terminal, Ras Tanura, Saudi Arabia.jpg", creator="Jane Doe", licence="CC BY-SA 4.0", provider="wikimedia", source="wikimedia",
                                     licence_url="https://creativecommons.org/licenses/by-sa/4.0/", width=1600, height=1200)]

    async def fetch(client_: Any, p: Any, min_width: int = 0) -> Image.Image:
        return photo(1600, 1200)

    async def oil(client_: Any, symbol: str, end: Any = None) -> list[list[float]]:
        raise AssertionError("a missile strike story isn't about a price and no reaction was measured: no chart beside it")

    async def download(url: str, transport: Any = None) -> Image.Image:
        assert url == "https://upload.wikimedia.org/ras-tanura.jpg"
        return photo(1600, 1200)

    monkeypatch.setattr(photos, "discover", discover)
    monkeypatch.setattr(photos, "fetch", fetch)
    monkeypatch.setattr(clean, "fetch_bars", oil)
    monkeypatch.setattr(clean, "download", download)
    ev, at = await _release_and_render(client, db, live)
    # 2026-11-16: this used to post Riyadh's skyline for a strike on Ras Tanura (a city stand-in). The named site's photo needs a place the
    # story names to check it against (wire.site_photo_fits) and "Saudi" is a nationality: nothing real fits, so a text post on both
    tg = live["tg"]
    texts: list[str] = []

    async def send_message(chat: Any, text: str, **kw: Any) -> dict[str, Any]:
        texts.append(text)
        return {"message_id": 900, "chat": {"id": -100123, "type": "channel"}}

    tg.send_message = send_message
    sent = await lane.tick(db, get_settings(), tg, at + timedelta(minutes=2))
    assert sent["sent"] and tg.sent == [] and len(texts) == 1
    _telegram_words(texts[0])
    db.expire_all()
    ev = db.get(NdEvent, ev.id)
    assert ev.facts["visual"] == {"style": "clean", "kind": "text", "layout": "text"}
    assert not any("riyadh" in " ".join(t).lower() for t in seen)  # the stand-in is never even searched for
    x = live["x"]
    assert await engine.follow_telegram(db, get_settings(), None, 0, at + timedelta(minutes=2)) == "published"
    text = x.posts[0]["text"]
    assert "Missile hits Aramco's Ras Tanura" in text and "https://www.dw.com/en/missile-strike-hits-saudi-aramco-terminal/a-79912345" in text
    assert network.x_length(text) <= 280 and "@" not in text
    assert x.uploads == [] and x.posts[0]["media"] is None  # text on X too
    post = db.get(NdPost, db.get(NdEvent, ev.id).x_post_id)
    assert post.status == "published"


async def test_nothing_real_fits_a_text_post_goes_out(client: Any, db: Session, live: dict[str, Any]) -> None:  # noqa: F811
    # owner, 2026-11-16: no illustration standing in for a photo, no headline-only card: text only, on both platforms
    ev, at = await _release_and_render(client, db, live)  # no licensed photo found, no prices
    tg = live["tg"]
    texts: list[str] = []

    async def send_message(chat: Any, text: str, **kw: Any) -> dict[str, Any]:
        texts.append(text)
        return {"message_id": 900, "chat": {"id": -100123, "type": "channel"}}

    tg.send_message = send_message
    sent = await lane.tick(db, get_settings(), tg, at + timedelta(minutes=2))
    assert sent["sent"] and tg.sent == [] and len(texts) == 1
    _telegram_words(texts[0])
    db.expire_all()
    assert db.get(NdEvent, ev.id).facts["visual"] == {"style": "clean", "kind": "text", "layout": "text"}
    assert await engine.follow_telegram(db, get_settings(), None, 0, at + timedelta(minutes=2)) == "published"
    assert live["x"].uploads == [] and live["x"].posts[0]["media"] is None


async def test_admin_can_go_back_to_the_classic_layout(client: Any, db: Session, live: dict[str, Any]) -> None:  # noqa: F811
    network.configure(db, post_style="classic")
    ev, at = await _release_and_render(client, db, live)
    tg = live["tg"]
    await lane.tick(db, get_settings(), tg, at + timedelta(minutes=2))
    assert "Price Chat News · Verified across 2 outlets" in tg.sent[0]["caption"] and "@thepricechat" not in tg.sent[0]["caption"]


async def test_emergency_pause_stops_the_clean_posts_too(client: Any, db: Session, live: dict[str, Any]) -> None:  # noqa: F811
    network.configure(db, emergency=True)
    ev, at = await _release_and_render(client, db, live)
    await lane.tick(db, get_settings(), live["tg"], at + timedelta(minutes=2))
    assert not live["tg"].sent
