"""The shared desk (app/newsdesk/events): what it scores, what it releases and when, how stories develop, and that X and Telegram each deliver
the same decision on their own. No test reaches a real service."""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.members.referrals import program
from app.models.newsdesk import NdEvent, NdPost, NdStory
from app.newsdesk import card, checks, engagement, engine, events, headline, moves, sources, telegram as lane, virality
from app.newsdesk.brief import ALERT, BREAKING, UPDATE

from tests.test_newsdesk_telegram import Tg, quote, story, verified

T0 = datetime(2026, 11, 2, 6, 5, tzinfo=timezone.utc)  # five minutes into the 06:00 window


@pytest.fixture(autouse=True)
def channel(db: Session) -> None:
    program(db).updates_channel = "@Pricechatupdates"
    db.commit()


async def agrees(item: dict[str, Any], price: float, market: Any = None) -> tuple[bool, str]:
    return True, f"Hyperliquid {price:,.2f} vs Yahoo {price:,.2f}: 0.00% apart"


async def decide(db: Session, now: datetime, quotes: dict[str, Any] | None = None, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    desk = engine.settings_row(db)
    desk.state = {"last_run": {"at": now.isoformat()}, **({"policy": plan} if plan else {})}
    db.commit()
    return await events.decide(db, now, quotes=quotes or {}, watchlist=desk.watchlist if quotes else [], categories=desk.categories or {}, desk_state=desk.state, second_opinion=agrees, liquidations=False)


def released(db: Session) -> list[NdEvent]:
    db.expire_all()
    return list(db.scalars(select(NdEvent).where(NdEvent.status == "released").order_by(NdEvent.released_at)))


def hack(db: Session, name: str, now: datetime, amount: str = "$40 million", hours: float = 0.2) -> None:
    verified(db, f"{name} exchange says hackers drained {amount} from its hot wallet", now=now, hours=hours)


# --- scoring ---------------------------------------------------------------------------------------------------------


def test_a_big_verified_hack_outscores_a_small_one_and_every_point_is_explained() -> None:
    big = virality.news(title="Bybaz says hackers drained $1.5 billion", summary="", evidence="two outlets", outlets=3, outlets_3h=3, age_minutes=10, alleged=False, tickers=["ETH"])
    small = virality.news(title="Bybaz says hackers drained $4 million", summary="", evidence="attributed", outlets=0, outlets_3h=0, age_minutes=10, alleged=False, tickers=[])
    assert big.total >= 85 > 60 > small.total
    assert "severity 38" in big.explain() and "magnitude 25" in big.explain()


def test_a_deal_s_size_isn_t_chaos_but_a_loss_is() -> None:
    deal = virality.news(title="Riot shares surge on $28B AI lease", summary="", evidence="attributed", outlets=0, outlets_3h=0, age_minutes=10, alleged=False, tickers=[])
    loss = virality.news(title="Riot loses $28B in exchange collapse", summary="", evidence="attributed", outlets=0, outlets_3h=0, age_minutes=10, alleged=False, tickers=[])
    assert deal.parts["magnitude"] == 0 and loss.parts["magnitude"] == 25


def test_an_allegation_scores_less_and_age_fades_a_story() -> None:
    kw = dict(title="SEC sues Acme over $100 million fraud", summary="", evidence="official", outlets=0, outlets_3h=0, tickers=[])
    fresh = virality.news(**kw, age_minutes=10, alleged=False)
    assert virality.news(**kw, age_minutes=10, alleged=True).total == fresh.total - 6
    assert virality.news(**kw, age_minutes=360, alleged=False).total < fresh.total * 0.7


def test_a_move_scores_by_how_far_past_its_line_it_went() -> None:
    small = virality.market(kind="move", group="crypto_major", ratio=1.0, two_sources=True, symbol_key="BTC")
    crash = virality.market(kind="move", group="crypto_major", ratio=3.2, two_sources=True, symbol_key="BTC")
    assert small.total < 60 and crash.total >= 80


def test_engagement_nudges_the_score_only_with_enough_measured_posts(db: Session) -> None:
    now = T0
    def post(topic: str, likes: int, i: int) -> NdPost:
        return NdPost(kind="news", category="crypto", status="published", text=f"t{i}", key=f"k{i}", facts={"topic": topic}, sources=[], checks=[], published_at=now - timedelta(days=1),
                      metrics={"impression_count": 1000, "like_count": likes})
    for i in range(4):
        db.add(post("hack", 60, i))
    db.commit()
    assert engagement.adjustments(db, now) == {}  # too few to mean anything
    for i in range(4, 12):
        db.add(post("move" if i % 2 else "hack", 10 if i % 2 else 60, i))
    db.commit()
    adj = engagement.adjustments(db, now)
    assert adj["hack"] > 0 > adj["move"] and all(-8 <= v <= 8 for v in adj.values())


# --- headlines -------------------------------------------------------------------------------------------------------


def test_a_headline_gains_our_measured_figure_and_loses_only_a_trailing_clause() -> None:
    assert headline.news("Bitcoin slides as ETF outflows mount", tag="BTC -6.2% IN 24H") == "BITCOIN SLIDES AS ETF OUTFLOWS MOUNT (BTC -6.2% IN 24H)"
    assert headline.news("Bitcoin falls 6% as ETF outflows mount", tag="BTC -6.2% IN 24H") == "BITCOIN FALLS 6% AS ETF OUTFLOWS MOUNT"  # it already gives a figure
    assert headline.shorten("BITCOIN TUMBLES 8% AS TRADERS DUMP RISK ASSETS AHEAD OF THE FED", 30) == "BITCOIN TUMBLES 8%"
    assert headline.shorten("TOKEN FALLS AS", 5) == "TOKEN FALLS AS"  # never cut to nothing


def test_market_words_are_earned_and_a_market_wide_move_reads_right() -> None:
    btc = next(w for w in sources.DEFAULT_WATCHLIST if w["symbol"] == "BTC-USD")
    now = T0
    m = moves.Move("move", btc, 60000, now, "1h", -14.3, 4.8)
    assert headline.market(m) == "BITCOIN CRASHES 14.3% IN AN HOUR TO $60,000"
    wide = moves.Move("wide", {"symbol": "US-STOCKS", "name": "US stocks", "group": "index"}, 1, now, "24h", -5.0, 2.1, members=[("Nasdaq", -5.6), ("S&P 500", -3.5)])
    assert headline.market(wide) == "US STOCKS PLUNGE: NASDAQ -5.6%, S&P 500 -3.5% IN 24 HOURS"


def test_a_market_wide_move_counts_only_members_that_really_moved() -> None:
    now = T0
    wl = [w for w in sources.DEFAULT_WATCHLIST if w["symbol"] in ("^GSPC", "^IXIC", "^DJI")]
    # two hours of bars: a US index's hour is measured between bar ENDS (moves.price_near), so the bar ending an hour ago must exist
    q = {"^IXIC": quote("^IXIC", 103, 97, now, hours=2), "^GSPC": quote("^GSPC", 101.4, 98.6, now, hours=2), "^DJI": quote("^DJI", 100.01, 99.99, now, hours=2)}
    found = moves.detect(q, wl, now)
    wide = next(m for m in found if m.kind == "wide")
    assert [n for n, _ in wide.members] == ["Nasdaq", "S&P 500"]  # the Dow's -0.0% isn't part of a sell-off


def test_a_question_headline_is_advice_not_news(db: Session) -> None:
    from app.newsdesk import chaos

    s = story(db, "Worried about crypto hacks? Buy bitcoin proxies instead, say Wall Street firms", "CNBC", tier=1)
    assert chaos.rejected(s) == "rumour, opinion or explainer"


def test_the_card_draws_the_figure_and_our_prices() -> None:
    from app.newsdesk import brief

    b = brief.Brief(88, 88, BREAKING, "move", ["BTC"], "", True, False, 3, [], True)
    png = card.render("BITCOIN CRASHES 14.3% IN AN HOUR TO $60,000", b, ["Yahoo Finance"], T0, stat="-14.3%", spark=[70000 - i * 200 for i in range(50)])
    plain = card.render("BITCOIN CRASHES 14.3% IN AN HOUR TO $60,000", b, ["Yahoo Finance"], T0)
    assert png.startswith(b"\x89PNG") and plain.startswith(b"\x89PNG") and png != plain


# --- the plan ----------------------------------------------------------------------------------------------------------


async def test_a_quiet_day_posts_nothing_rather_than_fill_the_plan(db: Session) -> None:
    verified(db, "Bybaz exchange says withdrawals resumed after maintenance", now=T0, hours=0.1)
    story(db, "Acme fund manager says markets look fragile", "BBC", tier=1, now=T0, hours=0.1)
    for minutes in range(0, 360, 15):
        await decide(db, T0 + timedelta(minutes=minutes))
    assert released(db) == []


async def test_one_regular_post_per_window_three_hours_apart_and_four_a_day_at_most(db: Session) -> None:
    names = ["Bybaz", "Qorvex", "Lumio", "Vexa", "Zelto", "Okra", "Pyra", "Tundra"]
    for i, name in enumerate(names):
        hack(db, name, T0 + timedelta(hours=i * 3), "$300 million", hours=0)
    for minutes in range(0, 24 * 60, 10):
        await decide(db, T0 + timedelta(minutes=minutes))
    out = released(db)
    times = [e.released_at for e in out]
    assert len(out) == 4 and all(e.slot == "regular" for e in out)
    assert len({events.window_start(t, 6) for t in times}) == 4  # spread across the day
    assert all(b - a >= timedelta(hours=3) for a, b in zip(times, times[1:]))


async def test_a_three_a_day_plan_posts_three(db: Session) -> None:
    for i, name in enumerate(["Bybaz", "Qorvex", "Lumio", "Vexa", "Zelto", "Okra", "Pyra", "Tundra"]):  # a story every three hours, all four windows
        hack(db, name, T0 + timedelta(hours=i * 3), "$300 million", hours=0)
    for minutes in range(0, 24 * 60, 10):
        await decide(db, T0 + timedelta(minutes=minutes), plan={"regular_per_day": 3, "topic_max": 50})
    assert len(released(db)) == 3


async def test_a_fair_story_waits_for_late_in_the_window_and_a_strong_one_goes_at_once(db: Session) -> None:
    hack(db, "Bybaz", T0, hours=0)  # a $40m exchange hack from two specialist outlets: a fair story, around 63
    await decide(db, T0)
    ev = db.scalars(select(NdEvent)).one()
    assert ev.status == "candidate" and "under the bar" in ev.why and 56 <= ev.virality < 64
    hack(db, "Qorvex", T0 + timedelta(minutes=10), "$600 million", hours=0)
    await decide(db, T0 + timedelta(minutes=10))
    assert [e.headline for e in released(db)] == ["QORVEX EXCHANGE SAYS HACKERS DRAINED $600 MILLION FROM ITS HOT WALLET"]


async def test_an_extraordinary_event_goes_out_past_the_plan_within_its_own_limits(db: Session) -> None:
    for i, name in enumerate(["Bybaz", "Qorvex", "Lumio", "Vexa", "Zelto"]):
        story(db, f"{name} exchange collapses after hackers drain $2 billion", "The Block", now=T0 + timedelta(minutes=i * 50), hours=0)
        story(db, f"{name} exchange collapses after hackers drain $2 billion, users locked out", "Decrypt", now=T0 + timedelta(minutes=i * 50), hours=0)
        story(db, f"{name} collapse: hackers drain $2 billion from the exchange", "CNBC", tier=1, now=T0 + timedelta(minutes=i * 50), hours=0)
    for minutes in range(0, 300, 5):
        await decide(db, T0 + timedelta(minutes=minutes))
    out = released(db)
    assert [e.slot for e in out] == ["regular", "extra", "extra", "extra"]  # three extraordinary posts a day, at most
    assert all(e.label == BREAKING for e in out)
    assert all(b.released_at - a.released_at >= timedelta(minutes=45) for a, b in zip(out, out[1:]))


async def test_extraordinary_needs_strong_evidence(db: Session) -> None:
    hack(db, "Bybaz", T0, "$300 million", hours=0)  # takes the window's regular slot
    await decide(db, T0)
    story(db, "Binance says hackers drained $2 billion in ether from its hot wallet", "The Block", now=T0 + timedelta(hours=1), hours=0)
    await decide(db, T0 + timedelta(hours=1))
    ev = db.scalars(select(NdEvent).where(NdEvent.title.like("Binance%"))).one()
    assert ev.status == "candidate" and ev.evidence == "attributed" and ev.virality >= 80  # high, but one outlet: it waits for a regular slot


async def test_the_plan_can_be_changed_from_the_desk_settings(db: Session) -> None:
    p = events.policy({"policy": {"regular_per_day": 3, "regular_bar": 65, "min_gap_minutes": 30, "window_hours": 5, "bogus": 1, "ceiling": "lots"}})
    assert (p.regular_per_day, p.regular_bar, p.min_gap, p.window_hours, p.ceiling) == (3, 65, timedelta(minutes=30), 6, 7)


# --- one asset, one story ------------------------------------------------------------------------------------------------


def crash(now: datetime, start: float = 70000, end: float = 60000, hours: int = 1) -> dict[str, sources.Quote]:
    return {"BTC-USD": quote("BTC-USD", start, end, now, hours=hours)}


async def test_the_same_move_isn_t_reposted_as_its_figure_rolls_on_but_an_escalation_is_an_update(db: Session) -> None:
    btc = [w for w in sources.DEFAULT_WATCHLIST if w["symbol"] == "BTC-USD"]
    desk = engine.settings_row(db)
    desk.watchlist = btc
    db.commit()
    easy = {"opening_bar": 56}
    await decide(db, T0, crash(T0, 70000, 66800), easy)  # -4.6% in an hour: 1.5 times the line
    await decide(db, T0 + timedelta(hours=7), crash(T0 + timedelta(hours=7), 70000, 66500), easy)  # about the same, seven hours on
    assert len(released(db)) == 1
    later = T0 + timedelta(hours=8)
    await decide(db, later, crash(later, 70000, 57000), easy)  # -18.6%: far worse
    out = released(db)
    assert len(out) == 2 and out[1].kind == "update" and out[1].parent_id == out[0].id and out[1].label == UPDATE


async def test_a_story_s_developments_become_at_most_two_updates(db: Session) -> None:
    hack(db, "Bybaz", T0, "$300 million", hours=0)
    await decide(db, T0)
    steps = [("Bybaz suspends withdrawals after the $300 million hack", 4), ("Bybaz hack losses rise to $500 million", 8), ("Bybaz founder arrested after the hack", 12),
             ("Bybaz hack: hackers drained $300 million, exchange says again", 16)]
    for title, h in steps:
        at = T0 + timedelta(hours=h)
        story(db, title, "The Block", summary="", now=at, hours=0)
        story(db, title + " today", "Decrypt", summary="", now=at, hours=0)
        for minutes in range(0, 240, 20):
            await decide(db, at + timedelta(minutes=minutes), plan={"opening_bar": 0, "regular_bar": 0, "window_hours": 0, "regular_gap_minutes": 0, "regular_per_day": 50, "topic_max": 50})
    out = released(db)
    assert [e.kind for e in out] == ["news", "update", "update"] and all(e.parent_id == out[0].id for e in out[1:])


async def test_an_executive_leaving_after_a_hack_is_an_update_as_big_as_the_hack(db: Session) -> None:
    # 2026-11-07: "Binance CFO steps down nine days after record hack" scored 50 (no figure of its own) and never went out
    hack(db, "Bybaz", T0, "$1.5 billion", hours=0)
    await decide(db, T0)
    later = T0 + timedelta(hours=10, minutes=30)  # late in the 12:00 window, the bar at its lowest
    story(db, "Bybaz CFO steps down days after record hack; co-CEO says transition was planned", "The Block", summary="", now=later, hours=0.1)
    await decide(db, later)
    out = released(db)
    assert [e.kind for e in out] == ["news", "update"]
    upd = out[1]
    assert upd.parent_id == out[0].id and upd.parts["magnitude"] == out[0].parts["magnitude"] and upd.virality >= events.POLICY.regular_bar
    assert (upd.facts or {}).get("development", "").startswith("new: step")


def test_regulators_cutting_off_an_exchange_and_index_corrections_are_market_chaos() -> None:
    from app.newsdesk import chaos

    def row(title: str, category: str) -> NdStory:
        return NdStory(link="https://x.test/1", title=title, summary="", source="The Block", tier=2, category=category)

    # 2026-11-06: three outlets carried this and every one was turned down as "not about a market" or "not market chaos"
    assert chaos.rejected(row("US Treasury cuts off MEXC over alleged $16B in illicit transactions", "economics")) is None
    assert chaos.rejected(row("Treasury Cuts Crypto Exchange MEXC Off From US Financial System", "economics")) is None
    assert chaos.topic_of("Nasdaq drops into correction territory - here's what typically happens next")[1] == "move"
    assert chaos.topic_of("AI stocks climb after trillions wiped from market values")[1] == "move"
    # still not news: an exchange's routine business, and politics with no market in it
    assert chaos.rejected(row("MEXC lists three new tokens for spot trading", "crypto")) is not None
    assert chaos.rejected(row("Mayor steps down after council vote", "politics")) is not None
    assert virality.news(title="US Treasury cuts off MEXC over alleged $16B in illicit transactions", summary="", evidence="two outlets", outlets=2, outlets_3h=1,
                         age_minutes=10, alleged=True, tickers=[]).total >= events.POLICY.regular_bar


# --- delivery ----------------------------------------------------------------------------------------------------------


async def test_x_and_telegram_deliver_the_same_event_and_one_failing_doesn_t_touch_the_other(db: Session) -> None:
    from app.telegram.errors import TelegramUnavailableError

    hack(db, "Bybaz", T0, "$300 million", hours=0)
    await decide(db, T0)
    desk = engine.settings_row(db)
    desk.kill_switch = True  # X stopped: Telegram goes on
    db.commit()
    tg = Tg()
    await lane.tick(db, get_settings(), tg, T0, quotes={})
    ev = released(db)[0]
    assert ev.tg_status == "delivered" and ev.x_status == "pending" and len(tg.sent) == 1
    events.pending(db, "x", T0 + timedelta(hours=3))
    db.refresh(ev)
    assert ev.x_status == "missed" and ev.tg_status == "delivered"  # recorded, per platform

    hack(db, "Qorvex", T0 + timedelta(hours=6), "$300 million", hours=0)
    desk.kill_switch = False
    db.commit()
    await decide(db, T0 + timedelta(hours=6))
    tg2 = Tg(fail=TelegramUnavailableError("Telegram did not respond in time"))
    await lane.tick(db, get_settings(), tg2, T0 + timedelta(hours=6), quotes={})
    await engine.deliver_x(db, desk, get_settings(), None, 0, T0 + timedelta(hours=6))
    ev2 = released(db)[1]
    assert ev2.tg_status == "uncertain" and ev2.x_status == "dry_run"  # Telegram may have sent it, so it's never resent; X went ahead


async def test_both_platforms_must_be_able_to_carry_an_event_before_it_is_released(db: Session) -> None:
    story(db, "Bybaz exchange hacked for $300 million, details at bybaz.com", "The Block", now=T0, hours=0)
    story(db, "Bybaz exchange hacked for $300 million, details at bybaz.com today", "Decrypt", now=T0, hours=0)
    await decide(db, T0)
    ev = db.scalars(select(NdEvent)).one()
    assert ev.status == "rejected" and released(db) == [] and checks.URLISH.search(ev.headline)
