"""Editorial judgement across slots (owner, 2026-11-10): one story is covered once unless something materially new happens; verified
important news outranks routine price moves; a slot with nothing worthwhile and new stays empty; pictures vary and stay readable on a phone.
The cases are the channel's own failures of 2026-11-08..10. No test reaches a real service."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.members.referrals import program
from app.models.newsdesk import NdEvent
from app.newsdesk import editorial, engine, events, recap, sources, telegram as lane, visual

from tests.test_newsdesk_telegram import quote, story

SLOT = datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc)  # 04:00 Dubai


@dataclass
class Mkt:
    coin: str
    mark: float
    prev_day: float


class QuietHL:
    def market(self, coin: str) -> Mkt:
        return Mkt(coin, 100.0, 100.2)

    def candles(self, coin: str, interval: str, count: int) -> list[Any]:
        return []


@pytest.fixture(autouse=True)
def _desk(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "DEFAULT_SCHEDULE", "slots")
    monkeypatch.setattr(sources, "EXTRA_FEEDS", [])
    monkeypatch.setattr(lane, "FORMAT", "visual")
    monkeypatch.setattr(recap, "VISUAL", True)
    events._alerted.clear()
    program(db).updates_channel = "@Pricechatupdates"
    db.commit()


async def decide(db: Session, now: datetime, quotes: dict[str, Any] | None = None) -> NdEvent | None:
    desk = engine.settings_row(db)
    desk.state = {"last_run": {"at": now.isoformat()}}
    db.commit()

    async def agrees(*a: Any, **k: Any) -> tuple[bool, str]:
        return True, "agrees"

    out = await events.decide(db, now, quotes=quotes or {}, watchlist=[], categories=desk.categories or {}, desk_state=desk.state, second_opinion=agrees,
                              liquidations=False, market=QuietHL())
    return db.get(NdEvent, out["released"]) if out["released"] else None


def oil(now: datetime, pct: float) -> dict[str, sources.Quote]:
    return {"CL=F": quote("CL=F", 90.0, 90.0 * (1 + pct / 100), now, hours=24)}


def sig(subjects: list[str], typ: str = "market", direction: str = "down", magnitude: float = 0.0, cls: str = "commodity", cause: bool = False) -> dict[str, Any]:
    return {"subjects": subjects, "type": typ, "direction": direction, "magnitude": magnitude, "class": cls, "cause": cause, "event": None}


# --- the same market move -----------------------------------------------------------------------------------------------


def test_wti_down_4_4_then_6_0_four_hours_later_is_the_same_story() -> None:
    covered = [editorial.Covered(sig(["OIL"], magnitude=4.4), SLOT, 153)]
    v = editorial.judge(sig(["OIL"], magnitude=6.0), covered, SLOT + timedelta(hours=4))
    assert not v.ok and v.same_as == 153 and "not a material change" in v.reason
    # Brent and WTI are one subject: "oil"
    assert editorial.canon("BZ=F") == editorial.canon("Oil (WTI)") == editorial.canon("CL=F") == "OIL"


@pytest.mark.parametrize(("before", "after", "ok"), [
    (6.0, 7.0, False),   # same tier, x1.17
    (6.0, 8.0, False),   # a tier up but only x1.33: the window rolling, not news
    (6.0, 9.0, True),    # x1.5 and into the 7.5% tier: "extends losses to 9%"
    (5.5, 12.0, True),
])
def test_a_move_counts_again_only_when_it_escalates_a_tier_and_by_half_again(before: float, after: float, ok: bool) -> None:
    covered = [editorial.Covered(sig(["OIL"], magnitude=before), SLOT, 1)]
    assert editorial.judge(sig(["OIL"], magnitude=after), covered, SLOT + timedelta(hours=4)).ok is ok


def test_a_reversal_a_verified_cause_or_a_new_day_is_new() -> None:
    covered = [editorial.Covered(sig(["OIL"], magnitude=6.0), SLOT, 1)]
    later = SLOT + timedelta(hours=4)
    assert editorial.judge(sig(["OIL"], direction="up", magnitude=5.5), covered, later).ok  # the other way
    assert editorial.judge(sig(["OIL"], magnitude=6.2, cause=True), covered, later).ok  # "Brent falls 7% on Iran peace hopes"
    assert editorial.judge(sig(["OIL"], magnitude=6.2), covered, SLOT + timedelta(hours=25)).ok  # a day later
    assert editorial.judge(sig(["GOLD"], magnitude=6.2), covered, later).ok  # another subject


async def test_slots_never_repeat_a_move_and_a_real_escalation_goes_out_once(db: Session) -> None:
    first = await decide(db, SLOT + timedelta(minutes=1), oil(SLOT, -5.5))
    assert first is not None and first.kind == "recap" and visual.post_headline(first) == "WTI CRUDE FALLS 5.5% IN 24 HOURS"
    assert first.facts["story"]["subjects"] == ["OIL"] and first.facts["story"]["magnitude"] == 5.5  # remembered across slots and restarts
    t2 = SLOT + timedelta(hours=4, minutes=1)
    assert await decide(db, t2, oil(t2, -6.0)) is None  # the 2026-11-10 repeat: skipped, nothing posted instead
    t3 = SLOT + timedelta(hours=8, minutes=1)
    third = await decide(db, t3, oil(t3, -9.0))
    assert third is not None and visual.post_headline(third) == "WTI CRUDE TUMBLES 9.0% IN 24 HOURS"


async def test_a_post_made_before_this_change_still_counts_as_covered(db: Session) -> None:
    """Production's 00:00 Dubai WTI post has no story stamp: its fields say what it was."""
    db.add(NdEvent(key="recap:2026-11-09T20:00", kind="recap", title="OIL (WTI) SLIDES — DOWN 4.4% IN 24 HOURS", headline="OIL (WTI) SLIDES — DOWN 4.4% IN 24 HOURS",
                   topic="move", evidence="price data", status="released", slot="slot", released_at=SLOT - timedelta(hours=4) + timedelta(minutes=3),
                   facts={"recap": True, "pct": -4.4, "name": "Oil (WTI)", "recap_assets": ["Oil (WTI)"]}))
    db.commit()
    assert await decide(db, SLOT + timedelta(minutes=1), oil(SLOT, -5.0)) is None  # 4.4% then 5.0%: the same slide


async def test_a_routine_move_never_takes_a_slot() -> None:
    lines = [{"tag": "$ETH", "name": "Ethereum", "price": 2094.0, "pct": -6.5, "source": "Hyperliquid"},
             {"tag": "Oil (WTI)", "name": "Oil (WTI)", "price": 86.0, "pct": -4.4, "source": "Yahoo Finance"}]
    mover, notes = recap.biggest_move(lines, [], SLOT)
    assert mover is None and any("routine" in n for n in notes)  # 2026-11-09's two Ethereum briefs and the first WTI chart


def test_small_liquidation_flushes_and_short_wiggles_are_not_exceptional() -> None:
    liq = NdEvent(key="liq", kind="market", topic="liquidations", facts={"usd": 17_000_000})
    assert editorial.exceptional(liq)[0] is False  # 148: "$17 million on Binance in an hour"
    big = NdEvent(key="liq2", kind="market", topic="liquidations", facts={"usd": 250_000_000})
    assert editorial.exceptional(big)[0] is True
    wiggle = NdEvent(key="m", kind="market", topic="move", facts={"kind": "move", "window": "1h", "ratio": 1.2, "change_pct": -3.6})
    assert editorial.exceptional(wiggle)[0] is False
    day = NdEvent(key="m2", kind="market", topic="move", facts={"kind": "move", "window": "24h", "ratio": 1.1, "change_pct": -8.8})
    assert editorial.exceptional(day)[0] is True


# --- the same news story --------------------------------------------------------------------------------------------------


async def test_a_retelling_of_a_story_already_out_is_skipped_but_a_real_development_goes(db: Session) -> None:
    for i, src in enumerate(("The Block", "Decrypt")):
        story(db, f"Bybaz exchange says hackers drained $400 million from its hot wallet{' today' if i else ''}", src, now=SLOT, hours=0.2)
    first = await decide(db, SLOT + timedelta(minutes=2))
    assert first is not None and first.topic == "hack"
    t2 = SLOT + timedelta(hours=4)
    for i, src in enumerate(("CoinDesk", "Cointelegraph")):
        story(db, f"Bybaz hackers launder $400 million through crypto mixers{' now' if i else ''}", src, now=t2, hours=0.1)
    assert await decide(db, t2 + timedelta(minutes=2)) is None  # 2026-11-09: "wash loot" at 16:00, "launder loot" at 20:00
    assert db.scalars(select(NdEvent).where(NdEvent.status == "released")).all() == [first]
    t3 = SLOT + timedelta(hours=8)
    for i, src in enumerate(("CoinDesk", "The Block")):
        story(db, f"Police arrest two over $400 million Bybaz hack{' suspects' if i else ''}", src, now=t3, hours=0.1)
    third = await decide(db, t3 + timedelta(minutes=2))
    assert third is not None and third.kind == "update" and "arrest" in third.facts["development"]


# --- importance ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("title", "kind"), [
    ("After meeting Qatar PM, Trump calls Iran war 'effectively over' militarily", "geopolitics"),
    ("Saudi Arabia downs missiles fired at Riyadh after Houthi attack on pipeline", "geopolitics"),
    ("Nvidia Board Investigates Jensen Huang After Report He Was Target of Chinese Spy Effort", "corporate"),
    ("NYSE picks Avalanche to settle tokenized stocks", "structure"),
    ("Coinbase Joins Circle, Ripple With Final OCC Approval for Federal Trust Bank Charter", "regulation"),
    ("Bitcoin ETFs shed $1.3B in biggest weekly outflow since late June", "flows"),
    ("Iran war live: Iran demands US 'surrender' to its conditions", None),  # a live blog's rolling headline
    ("Doing these things now will help you save money at Christmas", None),
])
def test_what_matters_beyond_market_chaos(title: str, kind: str | None) -> None:
    s = editorial.significance(title)
    assert (s.kind if s else None) == kind


async def test_important_verified_news_takes_the_slot_over_a_routine_move(db: Session) -> None:
    titles = [("NYSE picks Avalanche to settle tokenized stocks amid Wall Street's blockchain race", "CoinDesk"),
              ("NYSE to settle tokenized stocks on Avalanche in planned 24/7 market", "The Block"),
              ("NYSE Picks Avalanche for Tokenized Stock Settlement", "The Defiant"),
              ("NYSE Taps Avalanche to Settle Its Planned Tokenized Stock Platform", "Decrypt")]
    for t, src in titles:
        story(db, t, src, now=SLOT, hours=0.3, category="crypto")
    ev = await decide(db, SLOT + timedelta(minutes=1), oil(SLOT, -3.0))
    assert ev is not None and ev.kind == "news" and ev.facts["importance"]["kind"] == "structure" and ev.evidence == "two outlets"


async def test_one_outlet_is_not_enough_for_importance(db: Session) -> None:
    story(db, "NYSE picks Avalanche to settle tokenized stocks", "CoinDesk", now=SLOT, hours=0.3)
    assert await decide(db, SLOT + timedelta(minutes=1)) is None


# --- headline and caption -------------------------------------------------------------------------------------------------


def test_one_bold_factual_headline_and_a_small_source_and_nothing_else() -> None:
    ev = NdEvent(key="k", kind="news", title="t", headline="🚨 BREAKING: Nvidia board investigates Jensen Huang after report he was the target of a Chinese spy effort "
                                                          "as shares slide in after-hours trading")
    h = visual.post_headline(ev)
    assert h == "NVIDIA BOARD INVESTIGATES JENSEN HUANG AFTER REPORT HE WAS THE TARGET OF A CHINESE SPY EFFORT"  # the trailing clause went, nothing added
    assert len(h) <= visual.MAX_HEADLINE
    assert visual.caption_html(h, "Decrypt") == f"<b>{h}</b>\n\n<i>Source: Decrypt</i>"


# --- pictures ---------------------------------------------------------------------------------------------------------------


def _ev(kind: str = "news", topic: str = "corporate", title: str = "Nvidia board investigates Jensen Huang", **facts: Any) -> NdEvent:
    e = NdEvent(key=f"k:{title}", kind=kind, title=title, headline=title.upper(), topic=topic, sources=[{"name": "Decrypt"}], facts=facts)
    e.id = 7
    return e


def test_the_picture_kind_follows_the_story() -> None:
    assert visual.kinds_for(_ev(kind="recap", topic="move", points=[[i, 1.0] for i in range(20)]))[0] == "chart"
    assert visual.kinds_for(_ev(topic="hack", title="Bybaz exchange hacked for $400 million"))[0] == "artwork"
    assert visual.kinds_for(_ev(importance={"kind": "corporate"}))[0] == "photo"
    assert visual.kinds_for(_ev(topic="geopolitics", title="Saudi Arabia downs missiles fired at Riyadh", importance={"kind": "geopolitics"}))[0] == "photo"
    assert visual.kinds_for(_ev(topic="etf", title="Bitcoin ETFs shed $1.3B in biggest weekly outflow", importance={"kind": "flows"}))[0] == "graphic"


def test_consecutive_pictures_vary() -> None:
    company = _ev(importance={"kind": "corporate"})
    assert visual.plan(company, [])["kind"] == "photo"
    two_photos = [{"kind": "photo", "palette": "violet", "layout": "lower", "motif": "beams"}] * 2
    p = visual.plan(company, two_photos)
    assert p["kind"] == "graphic" and p["palette"] != "violet" and p["layout"] != "lower" and p["motif"] != "beams"
    chart = _ev(kind="recap", topic="move", points=[[i, 1.0] for i in range(20)])
    assert visual.plan(chart, [{"kind": "chart", "tone": "ink"}] * 2)["kind"] == "chart"  # accuracy first: a chart is never swapped
    assert visual.plan(chart, [{"kind": "chart", "tone": "ink"}] * 2)["tone"] != "ink"


@pytest.mark.parametrize("palette", list(visual.PALETTES))
def test_headlines_are_big_and_high_contrast_on_a_phone(palette: str) -> None:
    long = "NORTH KOREAN HACKERS ARE CREATING MEMECOINS TO WASH LOOT FROM $6.3B BINANCE HACK"
    for layout in visual.LAYOUTS:
        _, meta = visual.graphic(long, palette=palette, layout=layout, seed=3)
        assert meta["type_px"] >= visual.MIN_TYPE and meta["lines"] <= 4 and meta["contrast"] >= 4.5
        left, top, right, bottom = meta["box"]
        assert left >= visual.MARGIN - 1 and right <= visual.W - visual.MARGIN + 1 and bottom <= visual.H - 60  # safe margins
    data, meta = visual.artwork(long, motif="fracture", palette=palette, layout="lower", seed=5)
    assert meta["contrast"] >= 4.5 and data[:3] == b"\xff\xd8\xff"


def test_a_photo_with_its_headline_is_readable_even_on_a_bright_picture() -> None:
    white = Image.new("RGB", (1920, 1080), (250, 250, 250))
    data, meta = visual.photo(white, "Photo: X, Wikimedia Commons (CC BY 4.0)", headline="TRUMP CALLS IRAN WAR 'EFFECTIVELY OVER'", layout="center")
    assert meta["contrast"] >= 4.5 and meta["type_px"] >= visual.MIN_TYPE


def test_an_update_needs_a_material_development_not_a_reworded_one() -> None:
    parent = NdEvent(key="story:a", kind="news", topic="hack", title="North Korean hackers are creating memecoins to wash loot from $6.3 billion Binance hack",
                     headline="X", facts={})
    parent.id = 151
    covered = [editorial.Covered(editorial.signature(parent), SLOT, 151)]
    reworded = NdEvent(key="update:b", kind="update", topic="hack", title="Binance hack launderers mint Elon Musk and animal meme coins to move stolen crypto",
                       headline="Y", facts={"development": "new: launder"}, status="candidate")
    assert events._repeat(reworded, covered, SLOT + timedelta(hours=4)) and reworded.status == "superseded" and "same story as #151" in reworded.why
    arrest = NdEvent(key="update:c", kind="update", topic="hack", title="Police arrest two over Binance hack", headline="Z", facts={"development": "new: arrest"},
                     status="candidate")
    assert not events._repeat(arrest, covered, SLOT + timedelta(hours=4))


def test_a_strong_story_keeps_its_strength_while_it_waits_for_the_next_slot_but_not_longer() -> None:
    p = events.policy({})
    ev = NdEvent(key="story:x", kind="news", topic="geopolitics", virality=49.0, facts={"peak": 62.0, "published_at": (SLOT - timedelta(hours=2)).isoformat()})
    assert events.held_score(ev, SLOT, p) == 62.0  # 22:10's story is still strong at the 00:00 slot
    ev.facts = {**ev.facts, "published_at": (SLOT - timedelta(hours=5)).isoformat()}
    assert events.held_score(ev, SLOT, p) == 49.0  # it had a whole slot interval: now freshness counts
    move = NdEvent(key="move:x", kind="market", virality=58.0, facts={"peak": 70.0})
    assert events.held_score(move, SLOT, p) == 58.0  # a price move is only ever as good as now
