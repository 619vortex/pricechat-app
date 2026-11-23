"""Owner, 2026-11-23: "make sure every post has an image". Telegram 205, 207 and 208 went out as text: 205 and 208 were financial stories
filed as wars ("drone war", "kills"), 207 was sent with the Terminal's button, which had no picture step. Now: the classifier tells a
market story from a battlefield; with no licensed picture the source article's own Telegram preview is used (never downloaded and
re-uploaded); with neither, the post is held for the owner, never published text-only on its own. The Terminal shows each picture with
its source, licence and credit. No real service is called."""
import io
from datetime import datetime, timezone
from types import SimpleNamespace as N
from typing import Any

import httpx
import pytest
from PIL import Image

from app.newsdesk import editor, preview
from app.terminal import pictures

NOW = datetime(2026, 11, 23, 6, 0, tzinfo=timezone.utc)
pytestmark = pytest.mark.real_preview  # this file supplies its own article pages


def _ev(head: str) -> Any:
    return N(headline=head, title=head, kind="news", facts={}, summary="", released_at=NOW, first_seen=None, id=1)


# --- 1. the classifier ------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("head", [
    "Market rout kills 50 basis points December Fed rate hike bets",  # Telegram 208
    "Russia and Ukraine's drone war sends optical fibre prices soaring",  # Telegram 205
    "Gold rallies as war fears grow",
    "Defense stocks jump as Ukraine war drags on",
    "Oil traders price in a longer war: Brent futures surge",
])
def test_a_war_or_a_figure_of_speech_in_a_markets_story_is_not_a_battlefield(head: str) -> None:
    assert editor.brief(_ev(head)).cls != "conflict"


@pytest.mark.parametrize("head", [
    "US strikes Iranian missile site after Bahrain attacks",  # Telegram 189
    "Saudi-led coalition intercepts Houthi missiles targeting Riyadh and south",  # Telegram 198
    "Israeli air strike kills 12 in Gaza",
    "Oil prices surge after missile strike on Saudi refinery",  # a market consequence of a strike that is reported: still the strike
    "Drone attack on Moscow airport halts flights",
])
def test_a_story_that_reports_an_act_of_violence_stays_a_conflict(head: str) -> None:
    assert editor.brief(_ev(head)).cls == "conflict"


def test_a_natural_disasters_toll_is_a_disaster_not_a_war() -> None:
    assert editor.brief(_ev("Storm kills 3 in Texas")).cls == "disaster"


def test_telegram_208_now_reaches_the_federal_reserve_photo() -> None:
    from app.newsdesk import photography

    head = "Market rout kills 50 basis points December Fed rate hike bets"
    assert editor.brief(_ev(head)).cls == "news"
    lib = photography.subject_of(f"{head} {head}")
    assert lib is not None and lib.key == "fed"


# --- 2. the article preview check ---------------------------------------------------------------------------------------------------------

def _jpeg(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (40, 60, 90)).save(buf, "JPEG")
    return buf.getvalue()


def _site(pages: dict[str, tuple[int, bytes, str]]) -> httpx.MockTransport:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.headers.get("user-agent")))
        status, body, ctype = pages.get(str(req.url), (404, b"", "text/plain"))
        return httpx.Response(status, content=body, headers={"content-type": ctype})

    t = httpx.MockTransport(handler)
    t.seen = seen  # type: ignore[attr-defined]
    return t


def _page(image: str = "", site: str = "The Guardian", title: str = "Global stock markets slump") -> bytes:
    og = f'<meta property="og:image" content="{image}">' if image else ""
    return f'<html><head><meta property="og:site_name" content="{site}"><meta property="og:title" content="{title}">{og}</head><body>x</body></html>'.encode()


ART = "https://www.theguardian.com/business/2026/nov/20/global-stock-markets-slump-anthropic-ipo-ai-sell-off"
IMG = "https://i.guim.co.uk/img/media/578833ee/master/5336.jpg"


async def test_an_article_with_a_large_preview_picture_passes_and_nothing_is_kept() -> None:
    t = _site({ART: (200, _page(IMG), "text/html"), IMG: (200, _jpeg(1200, 720), "image/jpeg")})
    p = await preview.check(ART, t)
    assert p.ok and p.image_url == IMG and (p.width, p.height) == (1200, 720) and p.site == "The Guardian"
    assert all("TelegramBot" in ua for ua in t.seen)  # read the way Telegram's preview robot reads it
    meta = preview.meta_for(p, {"style": "clean"})
    assert meta["kind"] == "link_preview" and "not downloaded or re-uploaded" in meta["licence"] and meta["credit_line"] == "Picture: The Guardian (article preview)"


@pytest.mark.parametrize("pages,why", [
    ({ART: (200, _page(""), "text/html")}, "offers no preview picture"),  # FT: a paywall shows a robot nothing
    ({ART: (403, b"blocked", "text/html")}, "answered 403"),  # CoinDesk: robots refused
    ({ART: (200, _page("https://example.com/static/logo-share.png"), "text/html")}, "logo or a placeholder"),
    ({ART: (200, _page(IMG), "text/html"), IMG: (200, _jpeg(320, 180), "image/jpeg")}, "is small"),
    ({ART: (200, _page(IMG), "text/html"), IMG: (404, b"", "text/plain")}, "answered 404"),
])
async def test_an_article_without_a_usable_preview_is_refused_with_its_reason(pages: dict[str, Any], why: str) -> None:
    p = await preview.check(ART, _site(pages))
    assert not p.ok and why in p.why


async def test_the_first_working_preview_among_the_sources_wins() -> None:
    ft = "https://www.ft.com/content/339c3a72"
    t = _site({ft: (200, _page(""), "text/html"), ART: (200, _page(IMG), "text/html"), IMG: (200, _jpeg(1600, 900), "image/jpeg")})
    got, looks = await preview.first_working([ft, ART], t)
    assert got is not None and got.url == ART and [x.ok for x in looks] == [False, True]


# --- 3. what the Terminal's picture panel shows ---------------------------------------------------------------------------------------------

def test_the_panel_names_a_library_photo_its_page_author_licence_and_credit() -> None:
    meta = {"kind": "photo", "url": "https://upload.wikimedia.org/a.jpg", "page": "https://commons.wikimedia.org/wiki/File:Eccles.jpg", "title": "File:Eccles.jpg",
            "library": "fed", "licence": "CC BY-SA 4.0", "credit_line": "Photo: AgnosticPreachersKid, CC BY-SA 4.0",
            "photo": {"source": "wikimedia", "creator": "AgnosticPreachersKid", "licence_url": "https://creativecommons.org/licenses/by-sa/4.0", "width": 4000, "height": 2600},
            "editor": {"why": "the editor's approved photo of the subject", "depiction": "strong"}}
    out = pictures.out(_jpeg(64, 40), meta)
    assert out["image"].startswith("data:image/jpeg;base64,") and not out["held"]
    assert out["source"] == "Editor's photo library (fed), from Wikimedia Commons"
    assert out["author"] == "AgnosticPreachersKid" and out["licence"] == "CC BY-SA 4.0" and out["licence_url"].endswith("by-sa/4.0")
    assert out["credit"].startswith("Photo: AgnosticPreachersKid") and out["size"] == [4000, 2600] and out["depiction"] == "strong"


def test_the_panel_shows_an_article_preview_as_the_publishers_own_and_a_held_post_with_its_reasons() -> None:
    p = preview.Preview(ART, True, "", IMG, 1200, 720, "The Guardian", "Global stock markets slump")
    out = pictures.out(b"", preview.meta_for(p, {}))
    assert out["kind"] == "link_preview" and not out["held"] and out["image"] == "" and out["preview_image"] == IMG
    assert out["source"].startswith("Article preview from The Guardian") and "re-uploaded" in out["source"]
    held = pictures.out(b"", {"kind": "held", "why": "no dated photograph of this conflict",
                              "previews": [{"url": "https://www.ft.com/x", "site": "ft.com", "ok": False, "why": "the article offers no preview picture"}]})
    assert held["held"] and held["source"] == "No picture: held for your review" and held["previews"][0]["why"].startswith("the article offers")


# --- 4. Telegram never gets a text-only post on its own -------------------------------------------------------------------------------------

class _Tg:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_photo(self, chat: Any, image: bytes, caption: str, **kw: Any) -> dict[str, Any]:
        self.sent.append({"photo": True, "caption": caption, **kw})
        return {"message_id": 700 + len(self.sent), "chat": {"id": -100123}}

    async def send_message(self, chat: Any, text: str, **kw: Any) -> dict[str, Any]:
        self.sent.append({"photo": False, "caption": text, **kw})
        return {"message_id": 700 + len(self.sent), "chat": {"id": -100123}}


def _message(**kw: Any) -> dict[str, Any]:
    base = {"link": "event:42", "title": "t", "headline": "Market rout kills Fed hike bets", "plain": "words", "html": "words", "source": "CoinDesk",
            "sources": [], "fingerprint": "fp", "writer": "network-x", "card": None, "preview": "", "text_ok": False}
    return {**base, **kw}


RIGHTS = {"chat_id": -100123, "bot": "@Cryptopricechatbot"}


async def test_a_post_with_no_picture_no_preview_and_no_owner_choice_is_never_sent(db: Any) -> None:
    from sqlalchemy import select

    from app.models.newsdesk import NdTelegramPost
    from app.newsdesk import telegram as lane

    tg = _Tg()
    got = await lane.send(db, lane.settings_row(db), tg, _message(), "@Pricechatupdates", RIGHTS, NOW)
    assert got is None and tg.sent == [] and db.scalars(select(NdTelegramPost)).first() is None
    assert not lane._paused(lane.settings_row(db), NOW)  # held, not a Telegram problem: the lane keeps running


async def test_the_article_preview_goes_as_telegrams_own_preview_above_the_words(db: Any) -> None:
    from app.newsdesk import telegram as lane

    tg = _Tg()
    post = await lane.send(db, lane.settings_row(db), tg, _message(preview=ART), "@Pricechatupdates", RIGHTS, NOW)
    assert post is not None and post.status == "delivered"
    assert tg.sent == [{"photo": False, "caption": "words", "html": True, "silent": True, "preview_url": ART}]


async def test_the_owners_choice_sends_the_words_alone(db: Any) -> None:
    from app.newsdesk import telegram as lane

    tg = _Tg()
    post = await lane.send(db, lane.settings_row(db), tg, _message(text_ok=True), "@Pricechatupdates", RIGHTS, NOW)
    assert post is not None and tg.sent and "preview_url" not in tg.sent[0] and not tg.sent[0]["photo"]


def test_the_client_asks_telegram_for_a_large_preview_above_the_text() -> None:
    from app.telegram.client import TelegramBotClient

    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1, "chat": {"id": 5}}})

    async def go() -> None:
        c = TelegramBotClient("123:abc", transport=httpx.MockTransport(handler))
        await c.send_message(5, "hello", preview_url=ART)
        await c.send_message(5, "plain")
        await c.aclose()

    import asyncio

    asyncio.run(go())
    assert seen[0]["link_preview_options"] == {"url": ART, "prefer_large_media": True, "show_above_text": True} and "disable_web_page_preview" not in seen[0]
    assert seen[1]["disable_web_page_preview"] is True and "link_preview_options" not in seen[1]


# --- 5. the owner's decision on a held post --------------------------------------------------------------------------------------------------

def _held(db: Any) -> Any:
    from app.models.newsdesk import NdEvent

    ev = NdEvent(key="k208", kind="news", title="Market rout kills Fed hike bets", headline="Market rout kills Fed hike bets", status="released",
                 released_at=NOW, tg_status="held", x_status="held", tg_note="no licensed picture", facts={"visual": {"kind": "held"}})
    db.add(ev)
    db.commit()
    return ev


@pytest.mark.parametrize("action,status,text_ok", [("retry", "queued", False), ("text", "queued", True), ("skip", "skipped", False)])
def test_a_held_post_is_released_only_by_the_owners_decision(db: Any, action: str, status: str, text_ok: bool) -> None:
    from app.terminal import feed

    ev = _held(db)
    out = feed.release_held(db, ev.id, action, NOW)
    db.refresh(ev)
    assert out["platforms"] == ["telegram", "x"] and ev.tg_status == status and ev.x_status == status
    assert bool((ev.facts or {}).get("text_ok")) is text_ok
    if action == "retry":
        assert "visual" not in ev.facts  # looked for again from scratch
    with pytest.raises(ValueError, match="not held"):
        feed.release_held(db, ev.id, action, NOW)


# --- 6. the Terminal's Publish to Telegram button carries a picture ----------------------------------------------------------------------------

def _no_picture(monkeypatch: pytest.MonkeyPatch, image: bytes = b"", meta: dict[str, Any] | None = None) -> None:
    async def for_topic(db: Any, t: Any, now: Any, **_: Any) -> tuple[bytes, dict[str, Any]]:
        return image, (meta if meta is not None else {"kind": "held", "why": "no dated photograph of this conflict"})

    monkeypatch.setattr(pictures, "for_topic", for_topic)


async def test_the_terminal_button_refuses_a_text_only_send_unless_the_owner_chooses_it(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config.settings import get_settings
    from app.terminal import feed
    from tests.test_newsdesk_telegram import Tg
    from tests.test_terminal2_20261118 import verified_topic

    t = await verified_topic(db)
    _no_picture(monkeypatch)
    tg = Tg()
    with pytest.raises(ValueError, match="Send without picture"):
        await feed.publish_telegram(db, get_settings(), t, None, client=tg)
    assert tg.sent == []
    out = await feed.publish_telegram(db, get_settings(), t, None, client=tg, text_ok=True)
    assert out["ok"] and len(tg.sent) == 1 and tg.sent[0]["card"] is False and "preview_url" not in tg.sent[0]
    assert t.parts["telegram"]["picture"]["kind"] == "text"


async def test_the_terminal_button_sends_the_licensed_photo_with_its_credit(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config.settings import get_settings
    from app.terminal import feed
    from tests.test_newsdesk_telegram import Tg
    from tests.test_terminal2_20261118 import verified_topic

    t = await verified_topic(db)
    meta = {"style": "clean", "kind": "photo", "url": "https://upload.wikimedia.org/kyiv.jpg", "page": "https://commons.wikimedia.org/wiki/File:Kyiv.jpg",
            "licence": "CC BY 4.0", "credit_line": "Photo: A. Author, CC BY 4.0", "photo": {"source": "wikimedia"}}
    png = io.BytesIO()
    Image.new("RGB", (1280, 1024), (20, 30, 40)).save(png, "PNG")
    _no_picture(monkeypatch, png.getvalue(), meta)
    tg = Tg()
    out = await feed.publish_telegram(db, get_settings(), t, None, client=tg)
    assert out["ok"] and tg.sent[0]["card"] is True and out["picture"]["kind"] == "photo" and out["picture"]["licence"] == "CC BY 4.0"
    from app.newsdesk import telegram as lane

    assert any(r[0] == meta["url"] for r in lane.settings_row(db).state["used_photos"])  # one photograph, one story


async def test_the_terminal_button_uses_the_articles_preview_when_no_licensed_photo_passes(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config.settings import get_settings
    from app.terminal import feed
    from tests.test_newsdesk_telegram import Tg
    from tests.test_terminal2_20261118 import verified_topic

    t = await verified_topic(db)
    p = preview.Preview(ART, True, "", IMG, 1200, 720, "Reuters", "Russia launches missile strikes on Kyiv")
    _no_picture(monkeypatch, b"", preview.meta_for(p, {}))
    tg = Tg()
    out = await feed.publish_telegram(db, get_settings(), t, None, client=tg)
    assert out["ok"] and tg.sent[0]["card"] is False and tg.sent[0]["preview_url"] == ART and out["picture"]["kind"] == "link_preview"


# --- 7. the library's new subjects (owner, 2026-11-23: "expand the approved image library using properly licensed sources") ---------------

@pytest.mark.parametrize("head,key", [
    ("OPEC+ agrees surprise output cut; oil jumps 6%", "opec"),  # OPEC's own building before a generic oil picture
    ("Oil prices fall as US crude stocks rise", "oil"),
    ("SEC sues Bybaz exchange over unregistered securities", "sec"),
    ("IMF cuts its global growth forecast", "imf"),
    ("Bank of Japan raises rates to 1%", "boj"),
    ("BoE holds rates at 4%", "boe"),
    ("Meta shares slide after earnings", "meta"),
    ("White House weighs a crypto executive order", "white_house"),
    ("US Senate passes the stablecoin bill", "capitol"),
    ("World Bank approves a $2B loan to Egypt", "world_bank"),
    ("Treasury Department sanctions a crypto mixer", "treasury"),
])
def test_a_headline_naming_an_institution_gets_its_own_building(head: str, key: str) -> None:
    from app.newsdesk import photography

    got = photography.subject_of(head)
    assert got is not None and got.key == key


@pytest.mark.parametrize("head", ["Meta-analysis shows coffee helps", "Second quarter GDP beats", "Kenya's Senate votes on a tax bill", "Treasury yields climb"])
def test_lookalike_words_never_pull_an_institutions_photo(head: str) -> None:
    from app.newsdesk import photography

    assert photography.subject_of(head) is None


@pytest.mark.parametrize("head,key", [("Warsh signals a rate pause", "warsh"), ("Lagarde says the ECB is ready to act", "lagarde"),
                                      ("Erdoğan says Turkey will cut energy imports", "erdogan"), ("Sam Altman says OpenAI will raise again", "altman"),
                                      ("Saylor's Strategy buys more bitcoin", "saylor"), ("Bessent rules out a tariff cut", "bessent")])
def test_a_headline_naming_a_person_gets_their_dated_captioned_photo(head: str, key: str) -> None:
    from app.newsdesk import photography

    got = photography.people_of(head)
    assert got is not None and got[0].key == key
    assert all(photography.CAPTIONS.get(f) for f in got[0].files)  # who, where and when: an archive photo never passes for today's event
