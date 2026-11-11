"""End to end, Price Chat News (owner, 2026-11-11): two outlets report a story; the desk verifies it, releases it in the 04:00 Dubai slot and
queues a Qwen job that also asks the GPU PC's local model for the caption; the PC returns the picture and a fact-checked caption; Telegram
posts the picture with the caption, the byline and clickable article links; X posts the same caption with the article link and the same
picture. No approval step, no real service."""
import io
from datetime import timedelta
from typing import Any

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.models.newsdesk import NdEvent, NdPost, NdRenderJob
from app.newsdesk import engine, events, network, render_queue as rq
from app.newsdesk import telegram as lane
from tests.test_newsdesk_breaking_e2e import DW, NATIONAL, SAYS_DW, SAYS_NATIONAL, _gpu_online, live  # noqa: F401 - the fixture
from tests.test_newsdesk_editorial import SLOT
from tests.test_newsdesk_render_queue import _picture

DW_URL = "https://www.dw.com/en/missile-strike-hits-saudi-aramco-terminal/a-79912345?maca=en-rss-en-all-1573-xml"
NAT_URL = "https://www.thenationalnews.com/news/gulf/2026/11/11/missile-strike-ras-tanura-terminal/"


async def test_a_slot_story_posts_the_pcs_caption_with_article_links_on_telegram_and_x(client: Any, db: Session, live: dict[str, Any]) -> None:  # noqa: F811
    w = _gpu_online(client, db)
    at = SLOT + timedelta(minutes=2)
    rq.claim(db, w, {"gpu": "NVIDIA GeForce RTX 5090"}, at - timedelta(seconds=20))
    title = "Missile strike hits Saudi Aramco oil terminal at Ras Tanura"
    live["feeds"]["items"] = {DW["url"]: [{"title": title, "link": DW_URL, "at": at - timedelta(minutes=30), "summary": SAYS_DW}],
                              NATIONAL["url"]: [{"title": title + ", officials say", "link": NAT_URL, "at": at - timedelta(minutes=25), "summary": SAYS_NATIONAL}]}
    desk = engine.settings_row(db)
    await engine.collect(db, desk, None, at)
    await events.decide(db, at, quotes={}, watchlist=[], categories=desk.categories or {}, desk_state=desk.state, liquidations=False)
    ev = db.scalars(select(NdEvent).where(NdEvent.status == "released")).one()
    assert ev.slot == "slot"
    # the GPU job carries the caption request: the PC's local model writes the words with the picture
    job = db.scalars(select(NdRenderJob).where(NdRenderJob.event_id == ev.id)).one()
    assert job.payload["model"] == "qwen" and title in job.payload["copy_request"]["user"]
    got = rq.claim(db, w, {"gpu": "NVIDIA GeForce RTX 5090"}, at + timedelta(seconds=5))
    assert got["copy_request"]["system"] == network.MODEL_PROMPT
    draft = {"headline": "Missile hits Aramco's Ras Tanura, the kingdom's largest oil export terminal", "short": "Missile hits Aramco's Ras Tanura terminal",
             "context": "Saudi officials said a missile hit the Ras Tanura terminal, the kingdom's largest oil export facility.", "stakes": "",
             "question": "Are markets pricing this in yet?"}
    done = rq.complete(db, got["id"], w, _picture(7), {"model": "Qwen/Qwen-Image-2512", "seconds": 73.0, "ocr": {"checked": True, "found": []},
                                                       "copy": {"draft": draft, "ok": True, "problems": [], "model": "gpt-oss:20b", "seconds": 41.0}},
                       at + timedelta(seconds=90))
    assert done["ok"] is True
    # Telegram: the AI illustration (labelled), the PC's caption, the byline, both outlets' articles as links
    tg = live["tg"]
    sent = await lane.tick(db, get_settings(), tg, at + timedelta(minutes=2))
    assert sent["sent"] and len(tg.sent) == 1
    cap = tg.sent[0]["caption"]
    assert cap.startswith("<b>Missile hits Aramco's Ras Tanura, the kingdom's largest oil export terminal</b>")
    assert 'href="https://www.dw.com/en/missile-strike-hits-saudi-aramco-terminal/a-79912345"' in cap and f'href="{NAT_URL}"' in cap  # tracking dropped
    assert "Price Chat News · Verified across 2 outlets" in cap and "Are markets pricing this in yet?" in cap
    assert Image.open(io.BytesIO(tg.sent[0]["image"])).size == (1600, 900)
    db.expire_all()
    ev = db.get(NdEvent, ev.id)
    assert ev.facts["package"]["copy"]["writer"] == "local" and ev.facts["visual"]["ai_illustration"] is True
    # X: the same words and the article link, the same picture, posted automatically
    x = live["x"]
    assert await engine.deliver_x(db, engine.settings_row(db), get_settings(), None, 0, at + timedelta(minutes=3)) == "published"
    text = x.posts[0]["text"]
    assert text.startswith("Missile hits Aramco's Ras Tanura") and "https://www.dw.com/en/missile-strike-hits-saudi-aramco-terminal/a-79912345" in text
    assert network.x_length(text) <= 280 and "Source: DW" not in text and x.uploads[0] == tg.sent[0]["image"]
    db.expire_all()
    post = db.get(NdPost, db.get(NdEvent, ev.id).x_post_id)
    assert post.status == "published" and any(c["check"] == "no_link" and c["passed"] for c in post.checks)


async def test_a_pc_caption_with_an_invented_figure_never_posts(client: Any, db: Session, live: dict[str, Any]) -> None:  # noqa: F811
    w = _gpu_online(client, db)
    at = SLOT + timedelta(minutes=2)
    title = "Missile strike hits Saudi Aramco oil terminal at Ras Tanura"
    live["feeds"]["items"] = {DW["url"]: [{"title": title, "link": DW_URL, "at": at - timedelta(minutes=30), "summary": SAYS_DW}],
                              NATIONAL["url"]: [{"title": title + ", officials say", "link": NAT_URL, "at": at - timedelta(minutes=25), "summary": SAYS_NATIONAL}]}
    desk = engine.settings_row(db)
    await engine.collect(db, desk, None, at)
    await events.decide(db, at, quotes={}, watchlist=[], categories=desk.categories or {}, desk_state=desk.state, liquidations=False)
    got = rq.claim(db, w, {"gpu": "NVIDIA GeForce RTX 5090"}, at + timedelta(seconds=5))
    bad = {"headline": "Oil jumps 9% after missile hits Aramco terminal", "short": "", "context": "", "stakes": "", "question": ""}
    rq.complete(db, got["id"], w, _picture(8), {"model": "Qwen/Qwen-Image-2512", "seconds": 70.0, "copy": {"draft": bad, "ok": True}}, at + timedelta(seconds=90))
    tg = live["tg"]
    await lane.tick(db, get_settings(), tg, at + timedelta(minutes=2))
    assert "9%" not in tg.sent[0]["caption"]  # the desk's own words went instead
    db.expire_all()
    pkg = db.scalars(select(NdEvent).where(NdEvent.status == "released")).one().facts["package"]
    assert pkg["copy"]["writer"] == "desk" and pkg["copy"]["rejected"][0].startswith("local: figures")
