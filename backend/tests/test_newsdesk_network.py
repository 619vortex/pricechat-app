"""Price Chat News (owner, 2026-11-11): written captions, direct article links, an honest byline, one story per post, layouts that never
cover the photo's subject, the GPU PC's local caption checked again before it posts."""

from datetime import datetime, timezone
from types import SimpleNamespace as NS
from typing import Any

import pytest
from PIL import Image, ImageDraw

from app.newsdesk import chaos, checks, design, events, network, render_queue

NYT = "https://www.nytimes.com/2026/11/10/business/nvidia-wachtell-lipton-jensen-huang.html"
BBC = "https://www.bbc.co.uk/news/articles/ck1wvwwl3x0go"
ROWS = [NS(source="New York Times", link=NYT + "?smid=nytcore-ios-share", title="Nvidia Hires Wachtell Lipton to Investigate CEO Jensen Huang's Affair With Chinese Executive",
           summary="The chip giant has hired the law firm Wachtell, Lipton, Rosen & Katz to look into the relationship. Nvidia's board said it had full confidence in the review."),
        NS(source="BBC", link=BBC + "?at_medium=RSS&at_campaign=rss", title="Nvidia hires major US law firm to investigate alleged Chinese spy affair",
           summary="Nvidia has hired a major US law firm to investigate claims its boss had an affair with an alleged Chinese spy."),
        NS(source="CNBC", link="https://www.cnbc.com/2026/11/11/alibaba-pr.html", title="Alibaba announces plans to list T-Head, its chip rival", summary="")]


def event(**kw: Any) -> NS:
    base = dict(id=158, key="story:nyt-nvidia", kind="news", title=ROWS[0].title, headline="NVIDIA HIRES WACHTELL LIPTON TO INVESTIGATE C.E.O.'S AFFAIR",
                summary=ROWS[0].summary, topic="controversy", label="", evidence="two outlets", category="stocks",
                sources=[{"name": r.source, "url": r.link} for r in ROWS], facts={"outlets": ["New York Times", "BBC"]}, released_at=None, parent_id=None)
    return NS(**{**base, **kw})


def test_links_are_direct_articles_without_tracking_and_never_a_homepage_or_a_feed() -> None:
    assert network.clean_url(NYT + "?smid=x&utm_source=rss#top") == NYT
    for url in [NYT, BBC, "https://www.dw.com/en/us-russia-and-ukraine-start-talks-in-abu-dhabi/live-79906232", "https://oilprice.com/Energy/Crude-Oil/Oil-Falls.html"]:
        assert network.is_article(url), url
    for url in ["https://www.bloomberg.com/", "https://www.bloomberg.com/markets", "https://cointelegraph.com/rss", "https://rss.dw.com/xml/rss-en-all",
                "https://feeds.bloomberg.com/markets/news.rss", "https://finance.yahoo.com/quote/%5EGSPC"]:
        assert not network.is_article(url), url


def test_only_outlets_telling_the_same_story_are_linked_free_ones_first() -> None:
    links = network.links_for(event(), ROWS)
    assert [x.name for x in links] == ["New York Times", "BBC"]  # CNBC's Alibaba story is a different story
    assert links[0].url == NYT and links[1].url == BBC
    own = network.links_for(event(facts={"article_url": "https://pricechat.live/news/158-nvidia"}), ROWS)
    assert own[-1].kind == "pricechat"


def test_a_market_move_links_the_data_it_was_measured_on() -> None:
    ev = event(kind="market", evidence="price data", sources=[{"name": "Yahoo Finance", "url": "https://finance.yahoo.com/quote/CL=F"}], facts={"data_source": "Yahoo Finance"})
    links = network.links_for(ev, [])
    assert [(x.name, x.kind) for x in links] == [("Yahoo Finance", "data")]
    assert network.verification(ev) == "Measured on our price data (Yahoo Finance)"


def test_the_byline_never_claims_more_than_the_desk_did() -> None:
    assert network.verification(event()) == "Verified across 3 outlets"
    assert network.verification(event(evidence="attributed", sources=[{"name": "CNBC", "url": "https://www.cnbc.com/a/b-c-d"}], facts={})) == "Reported by CNBC"
    assert network.verification(event(evidence="official", facts={})) == "From an official statement"
    assert network.is_original(event()) and not network.is_original(event(evidence="attributed"))


@pytest.mark.parametrize("title,roundup", [("HOT INFLATION, AI SELLOFF & ANTHROPIC IPO", True), ("Evening Briefing: Stocks slide as yields rise", True),
                                           ("Five Things You Need to Know to Start Your Day", True), ("Fed holds rates, signals cuts", False),
                                           ("Nvidia, AMD and Intel fall as chip curbs widen", False), ("Binance names new CFO days after hack", False)])
def test_a_round_up_of_several_stories_is_never_one_post(title: str, roundup: bool) -> None:
    assert network.is_roundup(title) is roundup
    if roundup:
        story = NS(title=title, summary="", link="https://www.bloomberg.com/news/newsletters/2026-11-10/x-y-z", category="economics")
        assert chaos.rejected(story) == "a round-up of several stories: one post is one story"


def test_the_checks_allow_only_the_storys_own_links_and_never_an_exclusive() -> None:
    text = f"Nvidia hires Wachtell Lipton\n\nNew York Times: {NYT}"
    ok = checks.check_post(text, source_text=ROWS[0].title, source_names=["New York Times"], links=[NYT])
    assert ok.ok, ok.reason
    other = checks.check_post(text + "\n\nhttps://evil.example.com/x", source_text=ROWS[0].title, source_names=["New York Times"], links=[NYT])
    assert not other.ok and "isn't one of the story's own sources" in other.reason
    assert not checks.check_post(text, source_text=ROWS[0].title, source_names=["New York Times"]).ok  # no allowed links: none at all, as before
    scoop = checks.check_post("Exclusive: Nvidia hires Wachtell Lipton (New York Times)", source_text=ROWS[0].title, source_names=["New York Times"])
    assert not scoop.ok and "exclusive" in scoop.reason


def test_x_captions_fit_280_counting_links_as_23_and_drop_the_least_needed_first() -> None:
    c = network.Copy(headline="Nvidia hires Wachtell Lipton to investigate its CEO's affair", short="Nvidia hires Wachtell to investigate its CEO",
                     context="The chip giant has hired the law firm Wachtell, Lipton, Rosen & Katz to look into the relationship. Nvidia's board said it had full confidence in the review.",
                     question="Does this change your view of the company?")
    links = network.links_for(event(), ROWS)
    text = network.x_caption(c, links)
    assert network.x_length(text) <= 280 and NYT in text and text.startswith(c.headline)
    assert "Source: Bloomberg" not in text
    plain = network.x_caption(c, links, with_links=False)
    assert "http" not in plain and "Via New York Times" in plain


async def test_the_package_is_checked_and_both_platforms_say_the_same_story() -> None:
    pkg = await network.package(event(), rows=ROWS)
    assert pkg.ok, [c for c in pkg.checks if not c["passed"]]
    assert pkg.copy.writer == "desk" and "Wachtell" in pkg.copy.headline  # the outlet's capitals, not lower-cased names
    assert NYT in pkg.x_text and f'<a href="{NYT}">New York Times</a>' in pkg.telegram_html and f'<a href="{BBC}">BBC</a>' in pkg.telegram_html
    assert "Price Chat News · Verified across 3 outlets" in pkg.telegram_html
    assert len(pkg.telegram_html) <= network.TG_CAPTION_LIMIT


def test_a_draft_with_an_invented_figure_or_name_or_hype_is_refused() -> None:
    src = network.source_text_of(event(), ROWS)
    good = network.Copy(headline="Nvidia hires Wachtell Lipton to investigate CEO affair", short="Nvidia hires Wachtell to probe CEO",
                        context="The chip giant hired Wachtell, Lipton, Rosen & Katz to look into the relationship.")
    assert network.verify_copy(good, src) == []
    assert "figures" in network.verify_copy(network.Copy(**{**good.out(), "context": "Nvidia shares fell 9% on the news."}), src)[0]
    assert "names" in network.verify_copy(network.Copy(**{**good.out(), "stakes": "Goldman Sachs downgraded the stock."}), src)[0]
    assert any("hype" in p for p in network.verify_copy(network.Copy(**{**good.out(), "headline": "EXCLUSIVE: Nvidia hires Wachtell"}), src))


async def test_the_pcs_local_caption_is_used_when_it_passes_and_ignored_when_it_doesnt() -> None:
    ok = event(facts={"outlets": ["New York Times", "BBC"], "local_copy": {"headline": "Nvidia brings in Wachtell Lipton to investigate its CEO",
                                                                           "short": "Nvidia brings in Wachtell", "context": "Nvidia's board said it had full confidence in the review.",
                                                                           "stakes": "", "question": "Does this change your view of the company?"}})
    c = await network.write(None, ok, ROWS)
    assert c.writer == "local" and c.headline.startswith("Nvidia brings in Wachtell")
    bad = event(facts={"outlets": ["New York Times"], "local_copy": {"headline": "Nvidia stock crashes 12% on affair probe", "short": "", "context": "", "stakes": "", "question": ""}})
    c = await network.write(None, bad, ROWS)
    assert c.writer == "desk" and c.rejected and c.rejected[0].startswith("local: figures")


def test_the_render_job_asks_the_pc_for_a_caption_and_keeps_only_a_fact_checked_one(db: Any) -> None:
    from app.models.brand_manager import BmWorker
    from app.models.newsdesk import NdEvent

    ev = NdEvent(key="story:x", kind="news", title=ROWS[0].title, headline="NVIDIA HIRES WACHTELL", topic="controversy", sources=[], facts={})
    db.add(ev)
    db.flush()
    now = datetime(2026, 11, 11, 7, 35, tzinfo=timezone.utc)
    job = render_queue.enqueue(db, ev, now, priority=0)
    assert job.payload["copy_request"]["system"].startswith(network.MODEL_PROMPT) and ROWS[0].title in job.payload["copy_request"]["user"]
    breaking = NdEvent(key="story:y", kind="news", title="Strike", headline="STRIKE", topic="war", sources=[], facts={})
    db.add(breaking)
    db.flush()
    assert "copy_request" not in render_queue.enqueue(db, breaking, now, priority=10).payload  # breaking can't wait for the local model
    w = BmWorker(name="pc", token_hash="x" * 64)
    job.worker_id, job.status = None, "claimed"
    render_queue.keep_copy(db, job, {"copy": {"draft": {"headline": "Nvidia brings in Wachtell"}, "ok": False}}, now)
    assert "local_copy" not in (db.get(NdEvent, ev.id).facts or {})  # the PC's own fact check failed it
    render_queue.keep_copy(db, job, {"copy": {"draft": {"headline": "Nvidia brings in Wachtell", "short": "Wachtell"}, "ok": True, "model": "gpt-oss:20b"}}, now)
    assert db.get(NdEvent, ev.id).facts["local_copy"]["headline"] == "Nvidia brings in Wachtell"
    del w


def _photo() -> Image.Image:
    img = Image.new("RGB", (1800, 1200), (60, 90, 130))
    ImageDraw.Draw(img).rectangle((1100, 300, 1600, 1100), fill=(220, 190, 80))
    return img


@pytest.mark.parametrize("layout", ["band", "side", "overlay"])
def test_every_layout_is_16_by_9_and_keeps_the_type_off_the_subject(layout: str) -> None:
    box = (1100 / 1800, 300 / 1200, 1600 / 1800, 1100 / 1200)
    data, meta = design.render(_photo(), subject=box, headline="Nvidia hires Wachtell Lipton to investigate its CEO", kicker="Business", section="business",
                               credit="Photo: Coolcaesar / Wikimedia Commons (CC BY-SA 4.0)", when=datetime(2026, 11, 10, 20, 1, tzinfo=timezone.utc),
                               line="Verified across 3 outlets", layout=layout)
    import io

    assert Image.open(io.BytesIO(data)).size == (1600, 900)
    assert meta["covered"] <= design.MAX_COVER
    if layout == "side":
        assert meta["layout"] == "side-left"  # the subject sits right: the panel goes left
    if meta["subject"] is not None:
        assert 0 <= meta["subject"][0] < meta["subject"][2] <= 1


def test_an_overlay_that_would_cover_the_subject_becomes_a_band() -> None:
    data, meta = design.render(_photo(), subject=(0.05, 0.05, 0.95, 0.95), headline="A headline", kicker="News", section="news", credit="c", when=None, layout="overlay")
    assert meta["format"] == "band"


def test_an_ai_background_is_labelled_on_the_picture_and_in_the_credit() -> None:
    data, meta = design.render(_photo(), subject=None, headline="Oil tanker traffic halts in the strait", kicker="Geopolitics", section="geopolitics", credit="",
                               when=None, illustration="Qwen-Image", layout="band")
    assert meta["format"] == "band"
    # the label is drawn: the top-right corner holds the white AI ILLUSTRATION tag
    import io

    img = Image.open(io.BytesIO(data)).convert("L")
    assert max(img.crop((1400, 60, 1530, 90)).getdata()) > 230


def test_discovery_points_reward_wider_confirmation_not_the_two_outlet_floor() -> None:
    assert events.discovery_points("two outlets", "CoinDesk", {"The Block"}) == 0.0
    assert events.discovery_points("two outlets", "Reuters", {"BBC", "CNBC"}) == 3.0
    assert events.discovery_points("two outlets", "DW", {"BBC"}) == 2.0  # DW comes from the World Monitor catalog
    assert events.discovery_points("attributed", "CNBC", set()) == 0.0


@pytest.mark.parametrize("title", ["Missile strike hits Saudi Aramco export terminal", "Drone hits tanker in the Strait of Hormuz", "Attack shuts Red Sea shipping lane"])
def test_the_market_tie_matches_real_market_infrastructure(title: str) -> None:
    from app.newsdesk import editorial

    # 87c01bb shipped this pattern with backspace characters where \b belonged: it matched nothing
    assert editorial.MARKET_TIE.search(title)
    assert not editorial.MARKET_TIE.search("Prime minister meets union leaders over pensions")


def test_admin_emergency_pause_stops_both_platforms_and_x_links_can_be_turned_off(client: Any, db: Any) -> None:
    from app.db.base import utcnow
    from app.newsdesk import engine
    from app.newsdesk import telegram as lane

    r = client.get("/api/x/newsdesk/network")
    assert r.status_code == 200 and r.json()["x_links"] is True and r.json()["x_cost_per_post_usd"] == 0.20 and r.json()["emergency"] is False
    r = client.put("/api/x/newsdesk/network", json={"emergency": True, "x_links": False})
    assert r.json()["emergency"] is True and r.json()["x_links"] is False and r.json()["x_cost_per_post_usd"] == 0.015
    db.expire_all()
    now = utcnow()
    assert engine.blocked_reason(engine.settings_row(db), now) == network.EMERGENCY
    assert lane._paused(lane.settings_row(db), now) == network.EMERGENCY
    r = client.put("/api/x/newsdesk/network", json={"emergency": False})
    db.expire_all()
    assert r.json()["emergency"] is False and engine.blocked_reason(engine.settings_row(db), now) is None and lane._paused(lane.settings_row(db), now) is None
