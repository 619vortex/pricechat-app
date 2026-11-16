"""Regression tests from the audit of the account's real X posts (owner, 2026-11-16: "irrelevant, boring and sometimes incorrectly combined
stories ... zero posts is better than garbage"). Every headline and tweet text here is what production published, read back from X.

Trend post 152 (X 2122186583788966104, 2026-11-16 04:34 UTC):
    The Guardian: 'It is not unusual to go 48 hours with little sleep': what it's like to cover a Cop climate summit.
    From Reuters: UN climate chief urges speed as COP31 confronts fossil fuel divide.
A journalist's diary glued to an unrelated Reuters report, because Google Trends linked both articles to the search term "cop31".

News Desk post 118 (X 2121695699708338648): "Nationwide ground stop lifted, Nvidia spy scandal, starship's longest test flight." (a newsletter
round-up); post 117: "Ukraine, Russia race to build military satellite networks as war rages on" (a feature, under a Kremlin stock photo);
post 116: "China, US discuss tariffs, farm trade ahead of Xi-Trump meeting" (talks, no outcome); posts 115 and 124: "Bitcoin vs gold / vs the
Nasdaq over the past year" (evergreen comparisons filling slots); post 123: the Bahrain attack under a headline-only card."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.models.newsdesk import NdEvent, NdPost, NdStory
from app.models.trending import TtPost
from app.newsdesk import datapost, network, quality
from app.newsdesk import engine as desk
from app.trending import engine, gates, rank, writer

NOW = datetime(2026, 11, 16, 4, 30, tzinfo=timezone.utc)
DIARY = "‘It is not unusual to go 48 hours with little sleep’: what it’s like to cover a Cop climate summit"
COP_REUTERS = "UN climate chief urges speed as COP31 confronts fossil fuel divide"
POST_152 = f"The Guardian: {DIARY}.\n\nFrom Reuters: {COP_REUTERS}."
ROUNDUP_118 = "Nationwide ground stop lifted, Nvidia spy scandal, starship’s longest test flight"
FEATURE_117 = "Ukraine, Russia race to build military satellite networks as war rages on"
TALKS_116 = "China, US discuss tariffs, farm trade ahead of Xi-Trump meeting"
OIL_167 = "Oil Prices Fall as Fears of a Global Supply Glut Return"
BAHRAIN_90 = "Bahrain intercepts Iranian aerial attacks as Tehran says it attacked US base"

# real posts that were worth it, and must stay worth it
GOOD = [
    "Iran launches drone attacks against US military base in Bahrain",
    BAHRAIN_90,
    "Israeli air strike kills senior Hamas commander in Gaza",
    "Bitcoin $BTC drops below $64,000 as bullish bets get wiped out over the weekend",
    "Suspected China spy linked to Nvidia's ex-boss left in 2025, Megaspeed says",
    OIL_167,
    "Aster CEO suspects foul play after token plunges 32% in minutes",
    "Fed holds rates steady, signals two cuts next year",
    "SEC sues Binance and CEO Changpeng Zhao",
    "Trump says US will impose 100% tariffs on Chinese imports",
    "Xi and Trump meet in Busan, agree to extend tariff truce",
    "SOL Hits Lowest Since June Ahead of London Breakpoint",  # production 11-16: a move, not a preview
    "Estonia Says Russia Behind Failed Arson Attempt on Drone-Maker",  # production 11-16: a reported event
]


def _sig(i: int, title: str, outlet: str, **extra: Any) -> rank.Sig:
    at = NOW - timedelta(minutes=40)
    return rank.Sig(id=i, source="google", title=title, outlet=outlet, reliable=True, engagement=0.0, first_seen=at, last_seen=NOW, extra=extra)


# --- 1. never two stories as one ------------------------------------------------------------------------------------------------------

def test_a_google_search_term_never_glues_unrelated_articles_into_one_topic() -> None:
    group = "google:US:cop31"
    term = rank.Sig(id=1, source="google", title="cop31", outlet="Google Trends", reliable=False, engagement=50_000.0,
                    first_seen=NOW - timedelta(minutes=40), last_seen=NOW, extra={"group": group, "query": "cop31", "approx_traffic": "50K+"})
    diary = _sig(2, DIARY, "The Guardian", group=group, kind="article", query="cop31")
    reuters = _sig(3, COP_REUTERS, "Reuters", group=group, kind="article", query="cop31")
    for c in rank.cluster([term, diary, reuters]):
        titles = {s.title for s in c.signals}
        assert not {DIARY, COP_REUTERS} <= titles, titles  # never in the same topic
        assert DIARY not in titles  # and the diary is no topic at all (a feature)


def test_the_same_event_from_two_outlets_still_groups() -> None:
    group = "google:US:oil prices"
    a = _sig(2, "Oil prices fall 3% as fears of a global supply glut return", "Reuters", group=group, kind="article", query="oil prices")
    b = _sig(3, "Oil prices fall 3% on renewed supply glut fears", "CNBC", group=group, kind="article", query="oil prices")
    assert any({a.title, b.title} <= {s.title for s in c.signals} for c in rank.cluster([a, b]))


def test_broad_topic_words_are_never_the_same_story() -> None:
    assert not quality.same_story(DIARY, COP_REUTERS)
    assert not quality.same_story("Trump says strikes on Iran will continue", "Trump signs crypto bill into law")
    assert not quality.same_story("Bitcoin drops below $64,000 as longs are wiped out", "Bitcoin ETF sees $1 billion inflows in a week")


def test_post_152_fails_the_final_check_and_the_rewrite_tells_one_story() -> None:
    # what went out: held now, for both reasons (the lead is a diary; the words come from another article)
    problems = gates.story_problems(POST_152, lead_title=DIARY, lead_summary="", measured=DIARY, outlet="The Guardian", category="world")
    assert any("not worth a post" in p for p in problems) and any("aren't in the lead story" in p for p in problems)
    post = TtPost(topic_id=1, category="world", status="queued", text=POST_152, source_text=DIARY, checks=[], metrics={},
                  sources=[{"name": "The Guardian", "title": DIARY, "url": "https://g.test/1"}, {"name": "Reuters", "title": COP_REUTERS, "url": "https://r.test/1"}])
    assert engine.final_problems(post)
    # the Reuters report on its own: talk, no outcome, and a climate summit is off this account's beat
    alone = gates.story_problems(f"{COP_REUTERS}.\n\nVia Reuters", lead_title=COP_REUTERS, lead_summary="", measured=COP_REUTERS, outlet="Reuters",
                                 category="world")
    assert any("routine statement" in p for p in alone) and any("off this account's beat" in p for p in alone)


# --- 2. only stories worth a post -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("title,why", [
    (ROUNDUP_118, "round-up"),
    (FEATURE_117, "feature"),
    (DIARY, "feature"),
    (TALKS_116, "routine statement or talks"),
    (COP_REUTERS, "routine statement or talks"),
    ("Four reasons to be hopeful about this year's COP", "feature"),
    ("Bitcoin price prediction: analysts say $100K could come next", "price prediction"),
])
def test_the_audited_weak_stories_are_refused(title: str, why: str) -> None:
    got = quality.weak_story(title)
    assert got is not None and why in got, (title, got)


@pytest.mark.parametrize("title", GOOD)
def test_real_news_still_goes_out(title: str) -> None:
    assert quality.weak_story(title) is None, title


def test_the_news_desk_holds_a_weak_story_at_the_last_moment(db: Session) -> None:
    # released before the bar existed, still queued for X: the final check holds it, the dry run included (it is never "sent")
    ev = NdEvent(key="story:118", kind="news", title=ROUNDUP_118, headline=ROUNDUP_118, summary="", topic="", category="news", sources=[], facts={},
                 status="released", slot="slot", released_at=NOW)
    db.add(ev)
    db.commit()
    post = NdPost(kind="news", category="news", priority="scheduled", status="queued", text=f"{ROUNDUP_118}.\n\nBloomberg: https://b.test/1",
                  key=f"event:{ev.id}", fingerprint="", facts={"event_id": ev.id}, sources=[], checks=[], metrics={})
    db.add(post)
    db.commit()
    s = desk.settings_row(db)
    got = desk._editorial_hold(db, post)
    assert "round-up" in got
    import asyncio

    assert asyncio.run(desk.publish(db, s, post, None, None, 0, NOW)) == "rejected"  # type: ignore[arg-type]
    assert post.status == "rejected" and post.error.startswith("Final editorial check")


def test_an_event_citing_a_different_story_is_held(db: Session) -> None:
    lead = "Iran launches drone attacks against US military base in Bahrain"
    ev = SimpleNamespace(kind="news", title=lead, summary="")
    same = SimpleNamespace(title="Iran launches drone attack on US base in Bahrain")
    other = SimpleNamespace(title="Bahrain central bank holds rates steady")
    assert quality.event_problems(ev, [same]) == []
    assert any("a different story" in p for p in quality.event_problems(ev, [same, other]))


# --- 3. the words: one headline, at most one sentence, no source introductions ---------------------------------------------------------

def test_trend_posts_90_and_167_rewritten() -> None:
    # before: "OilPrice reports: Oil Prices Fall as Fears of a Global Supply Glut Return.\n\nAlso reported by The Guardian and CNN."
    oil = writer.Brief(key="t:167", category="markets", headline=OIL_167, outlet="OilPrice", outlets=["OilPrice", "The Guardian", "CNN"],
                       others=[("The Guardian", "Oil prices fall as supply glut fears return"), ("CNN", "Oil falls on glut fears")])
    oil.source_text = f"{oil.headline} " + " ".join(writer.measured_lines(oil))
    text, angle = writer.rules(oil)
    assert text == "Oil prices fall as fears of a global supply glut return.\n\nVia OilPrice" and angle == "plain"
    # before: "CNBC reports: Bahrain intercepts ...\n\nAlso reported by The Guardian, Al Jazeera and Financial Times."
    from app.trending import sensitive

    war = writer.Brief(key="t:90", category="geopolitics", headline=BAHRAIN_90, outlet="CNBC", outlets=["CNBC", "The Guardian", "Al Jazeera"])
    war.sensitive = sensitive.classify(BAHRAIN_90)
    war.source_text = BAHRAIN_90
    text, _ = writer.rules(war)
    assert text == f"{BAHRAIN_90}.\n\nVia CNBC" and "Also reported" not in text and "reports:" not in text
    for t in (writer.rules(oil)[0], text):
        assert not any(w in t for w in ("reports:", "From ", "Also reported", "have it too", "JUST IN"))


# --- 4. pictures: real or none ---------------------------------------------------------------------------------------------------------

def test_the_three_styles_stay_distinct_and_admin_shows_what_publishes(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    # owner, 2026-11-16 (second message): "don't silently turn Clean into Wire"
    monkeypatch.setattr(network, "DEFAULT_STYLE", "clean")
    for style in ("clean", "wire", "classic"):
        assert network.post_style({"post_style": style}) == style
        assert network.configure(db, post_style=style)["post_style"] == style == network.state(db)["post_style"]
    assert network.post_style({}) == "clean"


def test_the_clean_style_skips_city_and_country_stand_in_photos() -> None:
    from app.newsdesk import clean, visual

    # Telegram 177 (Kremlin skyline on a satellite story) and 180 (the Knesset on a Gaza strike): never searched for in the clean style
    for head in ("Ukraine, Russia race to build military satellite networks as war rages on", "Israeli air strike kills senior Hamas commander in Gaza"):
        kept = [must for _, must in visual.subject_queries(head) if must not in clean.STAND_INS]
        assert kept == [], (head, kept)
    # the company or institution a story is about still has its photo
    assert "nvidia" in [m for _, m in visual.subject_queries("Nvidia hires Wachtell to investigate CEO") if m not in clean.STAND_INS]
    assert "eccles|federal reserve" in [m for _, m in visual.subject_queries("Fed holds rates") if m not in clean.STAND_INS]


def test_every_style_says_the_story_in_one_headline_and_at_most_one_sentence() -> None:
    from app.newsdesk import clean

    copy = network.Copy(headline="Bybaz exchange hacked for $40 million", short="Bybaz hacked",
                        context="Bybaz said 12,000 accounts were affected. Withdrawals stay closed until Friday. The exchange hired auditors.",
                        stakes="Traders pulled $90 million from rival venues.", question="Will Bybaz reopen?")
    links = [network.Link("CoinDesk", "https://coindesk.test/a", "article")]
    for text in (clean.x_caption(copy, links), network.x_caption(copy, links)):
        story = text.split("\n\n")
        assert "Withdrawals" not in text and "Traders pulled" not in text and "?" not in text, text
        assert story[0].startswith("Bybaz exchange hacked") and len(story) <= 3, text


def test_a_title_case_headline_is_set_in_sentence_case_without_losing_names() -> None:
    # trend post 167 replay: "Oil prices fall as fears of a global supply Glut Return"
    from app.newsdesk import wire

    wire.learn_vocabulary(["Oil slipped as traders weighed a supply glut and a possible return of OPEC barrels.",
                           "Analysts warned the glut could deepen. Investors fear a return to oversupply.",
                           "A glut of crude kept prices low, while the return of exports weighed.",
                           "Visa said it would settle in USDC. The deal with Visa expands. Payments via Visa grew. Visa rose."])
    assert wire.headline_case(OIL_167, "") == "Oil prices fall as fears of a global supply glut return"
    assert "on Visa, Anchorage Digital says" in wire.headline_case(
        "Western Union\u2019s USDPT Stablecoin Becomes a Settlement Option on Visa, Anchorage Digital Says", "")
    assert wire.headline_case("Israeli Strike on Nasser Hospital Kills at Least Six", "Strikes hit Nasser Hospital in Khan Younis.") \
        == "Israeli strike on Nasser Hospital kills at least six"


# --- 5. slots are never filled with evergreen comparisons ----------------------------------------------------------------------------

def test_a_year_long_asset_battle_never_fills_a_slot(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    battle = datapost.DataPost(kind="battle", subject="battle:BTC-USD:^IXIC:365", headline="Bitcoin vs the Nasdaq over the past year: −31.1% against +9.9%",
                               lines=[], interest=95, symbols=["BTC-USD", "^IXIC"])
    week = datapost.DataPost(kind="ranking", subject="ranking:week", headline="The week's best and worst: Tesla +8.9%, Solana −17.4%", lines=[],
                             interest=datapost.DATA_BAR + 5, symbols=["TSLA", "SOL-USD"])
    monkeypatch.setattr(datapost, "candidates", lambda data, now: [battle, week])
    made = datapost.offer(db, NOW.replace(hour=10), {"BTC-USD": [(NOW, 1.0)]}, [], 56)
    assert [e.facts["data"]["kind"] for e in made] == ["ranking"]
    monkeypatch.setattr(datapost, "candidates", lambda data, now: [battle])
    assert datapost.offer(db, NOW.replace(hour=10), {"BTC-USD": [(NOW, 1.0)]}, [], 56) == []  # nothing worth it: no post


def test_story_rows_are_loaded_for_the_identity_check(db: Session) -> None:
    lead = "Iran launches drone attacks against US military base in Bahrain"
    a = NdStory(link="https://dw.test/1", title=lead, source="DW", summary="", tier=1, category="politics")
    b = NdStory(link="https://x.test/2", title="Bahrain central bank holds rates steady", source="X", summary="", tier=1, category="economics")
    db.add_all([a, b])
    db.commit()
    ev = NdEvent(key="story:iran", kind="news", title=lead, headline=lead, summary="", topic="", category="news", sources=[], facts={"story_ids": [a.id, b.id]},
                 status="released", slot="breaking", released_at=NOW)
    db.add(ev)
    db.commit()
    post = NdPost(kind="news", category="news", priority="breaking", status="queued", text=lead, key=f"event:{ev.id}", fingerprint="",
                  facts={"event_id": ev.id}, sources=[], checks=[], metrics={})
    assert "a different story" in desk._editorial_hold(db, post)


async def test_the_bahrain_attack_goes_out_as_text_not_a_headline_card() -> None:
    # News Desk post 123 went out under the desk's own headline-only card: no photo of a public figure or the named site, so now no picture
    import httpx

    from app.newsdesk import visual

    head = "Iran launches drone attacks against US military base in Bahrain"
    ev = SimpleNamespace(id=489, kind="news", title=head, headline=head, summary="Iran targeted an air base in Bahrain.", facts={}, label="",
                         slot="slot", evidence="two outlets", topic="geopolitics", released_at=None, first_seen=None, sources=[])
    t = httpx.MockTransport(lambda req: httpx.Response(200, json={"query": {"search": []}}))
    for style, want in (("clean", "clean"), ("wire", "wire")):  # each style keeps its own name, and neither draws a headline card
        img, meta = await visual.for_event(ev, transport=t, style=network.post_style({"post_style": style}))
        assert img == b"" and meta == {"style": want, "kind": "text", "layout": "text"}, meta


@pytest.mark.parametrize("title", [
    "U.N. watchdog agency pushes for access to key Iran nuclear site",  # Telegram 174: a statement
    "SpaceX-Tesla merger talks put 30,000 bitcoin back in focus",  # Telegram 172: attention, not an event
    "Middle East latest: four Indian crew members injured in attack on Liberia-flagged oil tanker in Gulf",  # Telegram 161: a live page
    "Hot Inflation, AI Selloff & Anthropic IPO",  # Telegram 159: a newsletter round-up
    "Ukraine peace talks face an uphill battle as winter approaches",  # Telegram 157
])
def test_the_older_channel_posts_that_should_never_have_gone_out(title: str) -> None:
    assert quality.weak_story(title) is not None, title


def test_a_story_the_channel_already_delivered_never_goes_out_again(db: Session) -> None:
    # Telegram 170 and 171 (2026-11-13 00:03 and 04:01 UTC): the same CoinDesk story twice, four hours apart
    from app.models.newsdesk import NdTelegramPost

    nk = "North Korean hackers are creating memecoins to wash loot from $6.3B Binance hack"
    db.add(NdTelegramPost(story_link="event:170", title=nk, headline=nk.upper(), text=nk, source="CoinDesk", sources=[], status="delivered",
                          created_at=NOW - timedelta(hours=4)))
    db.commit()
    again = NdEvent(key="story:nk2", kind="news", title=nk, headline=nk, summary="", topic="", category="news", sources=[], facts={}, status="candidate")
    db.add(again)
    db.commit()
    assert "already posted" in (quality.already_posted(db, again, NOW) or "")
    other = NdEvent(key="story:x", kind="news", title="Bybaz exchange hacked for $40 million", headline="", summary="", topic="", category="news",
                    sources=[], facts={}, status="candidate")
    assert quality.already_posted(db, other, NOW) is None
    # a week later it may be news again (a new development comes as an update anyway)
    assert quality.already_posted(db, again, NOW + timedelta(days=8)) is None


@pytest.mark.parametrize("head,want", [
    # ordinary words lowered, with no reporting at hand (trend post 167 replay)
    ("Oil Prices Fall as Fears of a Global Supply Glut Return", "Oil prices fall as fears of a global supply glut return"),
    ("Saudi Crude Output Jumps 26% as Riyadh Reports Near Pre-War Production", "Saudi crude output jumps 26% as Riyadh reports near pre-war production"),
    ("Mercado Bitcoin Begins Post-Hack Reopening Without a Timeline for Withdrawals",
     "Mercado Bitcoin begins post-hack reopening without a timeline for withdrawals"),
    # names kept: name-only words, names that are also words after a name, brands, multi-word places, hyphenated names
    ("Israeli Strike on Nasser Hospital Kills at Least Six", "Israeli strike on Nasser Hospital kills at least six"),
    ("Western Union’s USDPT Stablecoin Becomes a Settlement Option on Visa, Anchorage Digital Says",
     "Western Union’s USDPT stablecoin becomes a settlement option on Visa, Anchorage Digital says"),
    ("Nvidia Hires Wachtell Lipton to Investigate CEO’s Affair With Chinese Executive",
     "Nvidia hires Wachtell Lipton to investigate CEO’s affair with Chinese executive"),
    ("Block Shares Jump After Square Revenue Beats", "Block shares jump after Square revenue beats"),
    ("Strategy Buys More Bitcoin as Saylor Doubles Down", "Strategy buys more Bitcoin as Saylor doubles down"),
    ("Houthis Fire Missiles at Tankers in Red Sea", "Houthis fire missiles at tankers in Red Sea"),
    ("European Union Fines Google Over Search Ranking", "European Union fines Google over search ranking"),
    ("Hamas Leader Abdel-Malek Killed in Strike", "Hamas leader Abdel-Malek killed in strike"),
])
def test_title_case_headlines_in_sentence_case_without_losing_names(head: str, want: str) -> None:
    from app.newsdesk import wire

    assert wire.headline_case(head, "") == want
    assert network.sentence_case(head, "") == want  # the clean and classic styles case headlines the same way


def test_a_word_the_feeds_mostly_capitalise_stays_a_name() -> None:
    from app.newsdesk import wire

    wire.learn_vocabulary(["The deal with Return Capital closed. Return Capital said it would buy. Analysts at Return Capital agreed."] * 3)
    assert "Return" in wire.headline_case("Oil Majors Sell Stakes to Return", "")
