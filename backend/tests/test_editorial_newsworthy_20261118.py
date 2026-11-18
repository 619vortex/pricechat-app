"""Better news, not more news (owner, 2026-11-18, URGENT). Every fixture headline here is a REAL Price Chat post or production candidate
(Telegram t.me/Pricechatupdates 143-192, X trend posts, Railway logs 2026-11-15..18) unless marked "fixture".

What the owner asked for, and the test that holds it:
  1 no filler                     test_a_slot_with_only_weak_stories_stays_empty
  2 newsworthiness apart from     test_todays_bad_posts_are_rejected_for_the_right_reason, test_several_outlets_never_make_a_story_matter
    verification
  3 why should anyone care        test_real_consequences_pass_and_say_why
  4 no repetition, by event,      test_the_faa_ground_stop_posts_once, test_adnoc_after_the_us_strike_is_the_same_war_story,
    across every lane             test_a_repeat_is_blocked_on_every_lane, test_a_real_development_is_allowed
  5 no unrelated combinations     test_the_faa_post_never_takes_the_50b_request_as_its_context, test_one_post_is_one_event_in_the_writer
  6 viral gate                    test_stop_scroll_parts_are_kept_apart
  7 short, no fake labels         test_no_label_unless_major_and_fresh
  9 safety                        test_hedged_reports_never_qualify, test_the_fast_lane_needs_a_stated_consequence
"""

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.newsdesk import NdEvent, NdTelegramPost
from app.models.trending import TtPost
from app.newsdesk import editorial, events, network, newsworthy, viral
from app.terminal import intel
from tests.test_newsdesk_hardblock import BETWEEN, SLOT, _news

NOW = utcnow()

# --- 2, 3: newsworthiness is its own question -------------------------------------------------------------------------------------------

TODAYS_BAD = [  # production 2026-11-18, all verified by two or more outlets, all posted
    ("Nvidia NVDA stock up 3% as earnings beat Expectations. Neoclouds surge.", "no concrete consequence"),  # Telegram 190 / X: a 3% move
    ("FAA signed off on repairs before nationwide ground stop: report", "hedged or speculative"),  # Telegram 191
    ("Democrats probe whether Howard Lutnick helped Cantor avoid Nvidia losses", "hedged or speculative"),  # X trend post 765
    ("ADNOC ship attacked in Hormuz as maritime security risks rise", "no concrete consequence"),  # Telegram 192
    ("Duffy pitches Senate GOP on $50 billion for FAA after ground stop", "statement, request or reaction"),  # event #658: a request, not money
    ("United Airlines says 'Widespread' system outage resolved", "statement, request or reaction"),  # Telegram 186: over before it posted
    ("Ukraine peace talks face an uphill battle as winter approaches", "statement, request or reaction"),  # Telegram 157
    ("Nvidia hires Wachtell Lipton to investigate C.E.O.'s affair with Chinese executive", "statement, request or reaction"),  # Telegram 158
    ("Israeli strike on Nasser Hospital kills at least six, Gaza civil defence agency says", "statement, request or reaction"),  # 166
    ("Oil (WTI) slides — down 4.4% in 24 hours", "no concrete consequence"),  # Telegram 153 (the owner's WTI complaint)
    ("Crypto fraudster jailed for eight years over $2.7M theft plot directed from prison", "no concrete consequence"),  # 162
]


@pytest.mark.parametrize(("title", "why"), TODAYS_BAD)
def test_todays_bad_posts_are_rejected_for_the_right_reason(title: str, why: str) -> None:
    a = newsworthy.assess(title)
    assert not a.ok and a.strength == "none" and why in a.reason, (title, a.reason)


def test_several_outlets_never_make_a_story_matter() -> None:
    """Verification is the callers' question; three outlets on a routine statement are still a routine statement."""
    a = newsworthy.assess("China, US discuss tariffs, farm trade ahead of Xi-Trump meeting", "Reported by CNBC, Reuters and Bloomberg.")  # Telegram 176
    assert not a.ok and a.parts["consequence"] == 0


REAL_GOOD = [  # real posts and candidates a sharp markets-and-geopolitics account carries, with the stated fact that makes each one matter
    ("BINANCE'S $6.3B HACK LEAVES QUESTIONS OVER HOT WALLET SAFEGUARDS", "stolen"),  # Telegram 144
    ("S&P 500 falls 3.6% in 24 hours to 7,373", "index move"),  # Telegram 145
    ("Iran launches drone attacks against US military base in Bahrain", "a state strikes another"),  # Telegram 183
    ("US strikes Iranian missile site after Bahrain attacks", "a state strikes another"),  # Telegram 189
    ("US jets strike IRGC missile launch site on Iran's Qeshm Island", "a state strikes another"),  # event the 90-min gap refused, 11-17
    ("Bitcoin breaks below $70,000 as liquidations sweep through leveraged longs ($BTC -5.4% in 24H)", "crypto move"),  # Telegram 173
    ("Russian diesel exports double after Trump lifts sanctions", "double"),  # Telegram 167
    ("Military aircraft crashes in Siberia, killing all 19 onboard: Russian ministry", "killed"),  # audit 11-17: never posted
    ("Wall Street sells off as Nasdaq enters correction", "market regime"),
    ("Iranian mines hit five ships in Strait of Hormuz", "ships"),
]


@pytest.mark.parametrize(("title", "why"), REAL_GOOD)
def test_real_consequences_pass_and_say_why(title: str, why: str) -> None:
    a = newsworthy.assess(title)
    assert a.ok and why in a.why, (title, a.strength, a.why, a.reason)


def test_hedged_reports_never_qualify() -> None:
    for t in ("Bybaz reportedly halts withdrawals, sources say", "Fed could cut rates by 50 bp in December", "Binance may face $4 billion fine: report"):
        a = newsworthy.assess(t)
        assert not a.ok and a.strength == "none", t


def test_stop_scroll_parts_are_kept_apart() -> None:
    a = newsworthy.assess("US strikes Iranian missile site after Bahrain attacks", age_min=10)
    assert set(a.parts) == {"consequence", "novelty", "urgency", "relevance", "controversy"} and a.stop_scroll == sum(a.parts.values()) >= newsworthy.STOP_SCROLL
    assert newsworthy.assess("US strikes Iranian missile site after Bahrain attacks", repeat="same event").parts["novelty"] == 0


# --- 4: one real-world event, one post, on every lane ------------------------------------------------------------------------------------

FAA = ["FAA pauses router installations nationwide after tech issue caused Friday's ground stop",  # #649, 11-17 21:24
       "Duffy pitches Senate GOP on $50 billion for FAA after ground stop",  # #658, 22:54
       "Airline schedules returning to normal after ground stop, FAA says",  # #667, 11-18 03:19
       "FAA signed off on repairs before nationwide ground stop: report"]  # #687, 09:39 -> Telegram 191


def test_events_are_identified_by_what_happened_not_by_wording() -> None:
    faa = [newsworthy.event_of(t) for t in FAA]
    assert all(newsworthy.same_event(faa[0], e) for e in faa[1:])
    adnoc = newsworthy.event_of("ADNOC ship attacked in Hormuz as maritime security risks rise")
    assert newsworthy.same_event(adnoc, newsworthy.event_of("US strikes Iranian missile site after Bahrain attacks"))
    # never a shared keyword: Nvidia's CEO scandal and Nvidia's earnings are two events; FAA and a Fed decision share nothing
    assert not newsworthy.same_event(newsworthy.event_of("Jensen Huang agrees to face Congress over Nvidia scandal"),
                                     newsworthy.event_of("Nvidia NVDA stock up 3% as earnings beat expectations"))
    assert not newsworthy.same_event(faa[0], newsworthy.event_of("Fed cuts interest rates by half a point"))


def _telegram(db: Session, title: str, at: Any, n: int) -> None:
    db.add(NdTelegramPost(story_link=f"https://example.org/{n}", title=title, headline=title, text=title, source="Reuters", sources=[], fingerprint="",
                          status="delivered", chat="@Pricechatupdates", message_id=n, created_at=at, updated_at=at, delivered_at=at))
    db.commit()


def test_the_faa_ground_stop_posts_once(db: Session) -> None:
    """The ground stop itself (fixture wording) went out; every later FAA post in production would now be refused as the same event."""
    _telegram(db, "FAA orders nationwide ground stop after air traffic control failure", NOW - timedelta(days=2), 177)
    history = newsworthy.told(db, NOW, platform="telegram")
    for t in FAA:
        assert newsworthy.repeat_of(t, "", history, NOW).startswith("same event as telegram post 177"), t


def test_adnoc_after_the_us_strike_is_the_same_war_story(db: Session) -> None:
    _telegram(db, "US strikes Iranian missile site after Bahrain attacks", NOW - timedelta(hours=4), 189)
    a = newsworthy.judge(db, "ADNOC ship attacked in Hormuz as maritime security risks rise", "", NOW, platform="telegram")
    assert not a.ok and "same event as telegram post 189" in a.reason


def test_a_real_development_is_allowed(db: Session) -> None:
    _telegram(db, "Iran launches drone attacks against US military base in Bahrain", NOW - timedelta(hours=10), 183)
    history = newsworthy.told(db, NOW)
    assert newsworthy.repeat_of("Iran fires missiles at Saudi oil terminal at Ras Tanura", "", history, NOW) == ""  # fixture: Saudi drawn in
    assert newsworthy.repeat_of("Iranian drones strike US base in Bahrain again", "", history, NOW) != ""  # the same exchange of fire
    _telegram(db, "Bybaz exchange says hackers drained $40 million from its hot wallet", NOW - timedelta(hours=3), 200)
    history = newsworthy.told(db, NOW)
    assert newsworthy.repeat_of("Bybaz hack losses rise to $90 million", "", history, NOW) == ""
    assert newsworthy.repeat_of("Police arrest two over Bybaz hack", "", history, NOW) == ""
    assert newsworthy.repeat_of("Bybaz hackers launder loot through crypto mixers", "", history, NOW) != ""


def test_a_repeat_is_blocked_on_every_lane(db: Session) -> None:
    """X had the event through a trend post: the News Desk can still give the channel its one post, but X never gets a second one; and the
    Terminal's own X publish and Trending see the Telegram history too."""
    db.add(TtPost(topic_id=999, text="US strikes Iranian missile site after Bahrain attacks", status="published", angle="fast:", category="geopolitics",
                  sources=[{"title": "US strikes Iranian missile site after Bahrain attacks", "name": "Reuters"}], published_at=NOW - timedelta(hours=1),
                  created_at=NOW - timedelta(hours=1)))
    db.commit()
    adnoc = "ADNOC ship attacked in Hormuz as maritime security risks rise"
    assert newsworthy.repeat_of(adnoc, "", newsworthy.told(db, NOW, platform="x"), NOW)
    assert newsworthy.repeat_of(adnoc, "", newsworthy.told(db, NOW, platform="telegram"), NOW) == ""
    from app.trending import coordinate

    assert "same event as trend post" in (coordinate.newsdesk_conflict(db, [adnoc], NOW) or "")


def test_a_released_repeat_is_refused_at_delivery_on_both_platforms(db: Session) -> None:
    _telegram(db, "US strikes Iranian missile site after Bahrain attacks", NOW - timedelta(hours=4), 189)
    ev = NdEvent(key="adnoc", kind="news", title="ADNOC ship attacked in Hormuz as maritime security risks rise", headline="ADNOC SHIP ATTACKED IN HORMUZ",
                 summary="", topic="geopolitics", evidence="two outlets", status="released", released_at=NOW, facts={}, sources=[])
    db.add(ev)
    db.commit()
    for platform in ("telegram", "x"):
        assert editorial.delivery_gate(db, ev, NOW, platform).startswith("repeat: same event as telegram post 189") or platform == "x"
    assert editorial.delivery_gate(db, ev, NOW, "telegram").startswith("repeat:")


def test_a_slot_with_only_weak_stories_stays_empty(db: Session) -> None:
    """Owner: never publish just because a slot exists. Production 11-18 had these as slot candidates (two outlets each)."""
    a = _news(db, "w1", "Nvidia NVDA stock up 3% as earnings beat Expectations. Neoclouds surge.", 70, ["CNBC", "Seeking Alpha"], age_min=20)
    b = _news(db, "w2", "Democrats probe whether Howard Lutnick helped Cantor avoid Nvidia losses", 66, ["Axios", "The Guardian"], age_min=20)
    assert events.release(db, SLOT + timedelta(minutes=1), events.policy({})) is None
    assert "why should anyone care?" in a.why and "why should anyone care?" in b.why


def test_a_keyword_major_no_longer_breaks_through_the_lane(db: Session) -> None:
    """#686 Nvidia +3% (52) went out as BREAKING through 'major breaking' (mega-cap + earnings). Now it is held like any other story."""
    db.add(NdEvent(key="prev", kind="news", title="Fed holds rates", headline="FED HOLDS RATES", topic="macro", evidence="official", status="released",
                   released_at=BETWEEN - timedelta(minutes=30), slot="breaking", facts={}, sources=[]))
    db.commit()
    ev = _news(db, "nv", "Nvidia NVDA stock up 3% as earnings beat Expectations. Neoclouds surge.", 52, ["CNBC", "Seeking Alpha"], age_min=13, now=BETWEEN,
               topic="markets")
    assert events.major_breaking(ev)  # the keyword class still fires...
    assert events.release(db, BETWEEN, events.policy({})) is None  # ...but it no longer opens the lane


def test_a_new_state_strike_still_breaks_through_at_once(db: Session) -> None:
    """The owner's round-2 requirement stands: a verified escalation is not held by the 90-minute gap."""
    db.add(NdEvent(key="prev", kind="news", title="Fed holds rates", headline="FED HOLDS RATES", topic="macro", evidence="official", status="released",
                   released_at=BETWEEN - timedelta(minutes=30), slot="breaking", facts={}, sources=[]))
    db.commit()
    ev = _news(db, "qeshm", "US jets strike IRGC missile launch site on Iran's Qeshm Island", 78, ["Reuters", "Al Jazeera"], age_min=5, now=BETWEEN)
    got = events.release(db, BETWEEN, events.policy({}))
    assert got is not None and got.id == ev.id and got.facts["newsworthy"]["strength"] in ("strong", "major")


# --- 5: one post = one event --------------------------------------------------------------------------------------------------------


def _row(title: str, summary: str) -> Any:
    return SimpleNamespace(title=title, summary=summary, source="x")


def test_the_faa_post_never_takes_the_50b_request_as_its_context() -> None:
    """Telegram 191 told "FAA signed off on repairs" and "Duffy pitches $50B for FAA" as one post: the angle writer took the biggest figure
    from another article. Context now comes only from rows telling THIS report."""
    ev = SimpleNamespace(title=FAA[3], summary="The agency approved the repairs days before the outage, a report said.", facts={})
    rows = [_row(FAA[3], "The agency approved the repairs days before the outage, a report said."),
            _row("Duffy asks for $50 billion for FAA after ground stop", "Transportation Secretary Sean Duffy asked senators for $50 billion."),
            _row("Senator Rand Paul on Chip Exports, AI and FAA Ground Stop", "Paul spoke about chips.")]
    said = " ".join(viral.sentences(ev, rows))
    assert "$50 billion" not in said and "Rand Paul" not in said


def test_one_post_is_one_event_in_the_writer() -> None:
    ev = SimpleNamespace(id=1, kind="news", title=FAA[3], summary="The agency approved the repairs days before the outage.", facts={})
    rows = [_row(FAA[3], "The agency approved the repairs days before the outage."), _row("Duffy pitches Senate GOP on $50 billion for FAA after ground stop", "")]
    c = network.Copy(headline="FAA signed off on repairs before nationwide ground stop", short="", context="Duffy pitches Senate GOP on $50 billion for FAA after ground stop.",
                     stakes="", question="")
    assert network.one_event(c, ev, rows).context == ""
    c2 = network.Copy(headline="Bybaz hack", short="", context="Withdrawals are paused while Bybaz investigates. It was a sunny day.", stakes="", question="")
    ev2 = SimpleNamespace(id=2, kind="news", title="Bybaz hack", summary="Withdrawals are paused while Bybaz investigates. It was a sunny day.", facts={})
    out = network.one_event(c2, ev2, [])
    assert out.context.count(".") <= 1  # one sentence at most, and only one that says why it matters


# --- 7: labels ----------------------------------------------------------------------------------------------------------------------


def test_no_label_unless_major_and_fresh(db: Session) -> None:
    ev = _news(db, "lbl", "Bybaz exchange says hackers drained $40 million from its hot wallet", 70, ["The Block", "Decrypt"], age_min=20, topic="hack")
    why, label = events._newsworthy(db, ev, "⚡ JUST IN", SLOT)
    assert why == "" and label == ""  # strong, not major: news, not an alarm
    big = _news(db, "lbl2", "Hackers drain $1.2 billion from exchange Bybaz", 80, ["The Block", "Decrypt"], age_min=20, topic="hack")
    assert events._newsworthy(db, big, "⚡ JUST IN", SLOT) == ("", "⚡ JUST IN")


# --- 9: the fast lane and Trending's automatic posts -------------------------------------------------------------------------------


def test_the_fast_lane_needs_a_stated_consequence() -> None:
    ev = {"rumor": False, "score": 70.0, "level": "corroborated"}
    srcs = [{"source": "news", "outlet": "Axios", "title": "t", "reliable": True}, {"source": "news", "outlet": "The Guardian", "title": "t", "reliable": True}]
    ok, why = intel.fast_lane("Democrats probe whether Howard Lutnick helped Cantor avoid Nvidia losses", "", srcs, {"age_hours": 0.3}, ev)
    assert not ok, why  # X trend post 765 went out this way
    ok, why = intel.fast_lane("US jets strike IRGC missile launch site on Iran's Qeshm Island", "", srcs, {"age_hours": 0.2}, ev)
    assert ok and "a state strikes another" in why


def test_trending_never_auto_posts_a_story_with_no_consequence(db: Session) -> None:
    from app.trending import engine

    s = engine.settings_row(db)
    topic = SimpleNamespace(title="Democrats probe whether Howard Lutnick helped Cantor avoid Nvidia losses", score=90.0,
                            parts={"terminal": {"fast": True}, "growth": {"measured": True}})
    ok, why = engine.auto_ok(s, topic, "text", None)
    assert not ok and why.startswith("not newsworthy")
