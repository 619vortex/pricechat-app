"""End to end, PRICE CHAT VIRAL ENGINE 2.0 (owner, 2026-11-11). Real desk code from feed to Telegram and X, the outside world faked:

1. a verified story goes out with its strongest factual hook, the money angle measured on our prices, the same words on both platforms,
   and the X post carries the hook so its engagement can be learned;
2. after the US close, the day's MARKETS GONE MAD (a measured anomaly) takes the slot with its data card on both platforms, once a day;
3. the emergency pause holds data posts too;
4. a live blog verified by two outlets never posts (no empty posts);
5. a story we posted develops two days later: an update goes out labelled as one; the same news again does not;
6. Admin sees the hooks and can switch data posts off."""
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.models.newsdesk import NdEvent, NdPost
from app.newsdesk import engine, events, network, sources
from app.newsdesk import telegram as lane
from tests.test_newsdesk_breaking_e2e import DW, NATIONAL, SAYS_DW, SAYS_NATIONAL, live  # noqa: F401 - the fixture
from tests.test_newsdesk_editorial import SLOT
from tests.test_newsdesk_viral import daily

AT = SLOT + timedelta(minutes=2)  # the 04:00 Dubai slot (00:02 UTC): after the US close
TITLE = "Missile strike hits Saudi Aramco oil terminal at Ras Tanura"
DW_URL = "https://www.dw.com/en/missile-strike-hits-saudi-aramco-terminal/a-79912345"
NAT_URL = "https://www.thenationalnews.com/news/gulf/2026/11/10/missile-strike-ras-tanura-terminal/"


def _quote(symbol: str, then: float, now_price: float, first: Any, end: Any) -> sources.Quote:
    bars, t = [], first - timedelta(hours=1)
    while t <= end:
        p = then if t <= first else then + (now_price - then) * (t - first).total_seconds() / (end - first).total_seconds()
        bars.append((int(t.timestamp()), p, 100.0))
        t += timedelta(minutes=5)
    return sources.Quote(symbol, now_price, end, None, None, bars)


def _report(live: dict[str, Any], at: Any, *, title: str = TITLE, extra: str = "") -> None:
    live["feeds"]["items"] = {DW["url"]: [{"title": title, "link": DW_URL + extra, "at": at - timedelta(minutes=30), "summary": SAYS_DW}],
                              NATIONAL["url"]: [{"title": title + ", officials say", "link": NAT_URL + extra, "at": at - timedelta(minutes=25), "summary": SAYS_NATIONAL}]}


async def _decide(db: Session, at: Any, quotes: dict[str, Any] | None = None, daily_data: dict[str, Any] | None = None) -> None:
    desk = engine.settings_row(db)
    await engine.collect(db, desk, None, at)
    await events.decide(db, at, quotes=quotes or {}, watchlist=[], categories=desk.categories or {}, desk_state=desk.state, liquidations=False,
                        daily=daily_data if daily_data is not None else {})


async def test_a_story_goes_out_with_its_strongest_factual_hook_on_both_platforms(client: Any, db: Session, live: dict[str, Any]) -> None:  # noqa: F811
    first = AT - timedelta(minutes=30)
    quotes = {"BZ=F": _quote("BZ=F", 80.0, 83.2, first, AT), "GC=F": _quote("GC=F", 4000.0, 4080.0, first, AT)}
    _report(live, AT)
    await _decide(db, AT, quotes)
    ev = db.scalars(select(NdEvent).where(NdEvent.status == "released")).one()
    v = ev.facts["viral"]
    by_hook = {a["hook"]: a for a in v["angles"]}
    # the money angle is our own measurement since the first report; every hook is checked; the best factual one is chosen
    assert by_hook["money"]["stakes"] == "Since the first report: Brent crude +4.0%, Gold +2.0%."
    assert all(not a["problems"] for a in v["angles"]) and v["hook"] in by_hook
    best = max(a["score"] for a in v["angles"])
    assert by_hook[v["hook"]]["score"] == best or v["experiment"]["exploring"]
    tg = live["tg"]
    sent = await lane.tick(db, get_settings(), tg, AT + timedelta(minutes=1))
    assert sent["sent"] and len(tg.sent) == 1
    cap = tg.sent[0]["caption"]
    chosen = v["copy"]
    assert chosen["headline"] in cap and "Price Chat News · Verified across 2 outlets" in cap and f'href="{DW_URL}"' in cap
    if chosen["stakes"]:
        assert chosen["stakes"] in cap
    assert await engine.follow_telegram(db, get_settings(), None, 0, AT + timedelta(minutes=1)) == "published"
    x = live["x"].posts[0]["text"]
    assert x.startswith(chosen["headline"]) and network.x_length(x) <= 280
    db.expire_all()
    ev = db.get(NdEvent, ev.id)
    post = db.get(NdPost, ev.x_post_id)
    assert post.facts["viral"]["hook"] == v["hook"] and post.facts["viral"]["format"] == ev.facts["visual"]["kind"]  # learnable


async def test_markets_gone_mad_takes_the_slot_after_the_close_once_a_day(client: Any, db: Session, live: dict[str, Any]) -> None:  # noqa: F811
    from app.newsdesk import datapost

    data = {"BTC-USD": daily(60000, [-9.0], AT - timedelta(hours=1)), "GC=F": daily(3000, [0.2], AT - timedelta(hours=1))}
    btc = data["BTC-USD"]
    # what Yahoo's metadata says about the last value (2026-11-19): crypto never closes, so the day so far, from 00:00 UTC
    datapost._last["BTC-USD"] = datapost.Last("utc_day", btc[-1][1], btc[-2][1], AT - timedelta(hours=1), "CRYPTOCURRENCY")
    await _decide(db, AT, daily_data=data)
    ev = db.scalars(select(NdEvent).where(NdEvent.status == "released")).one()
    assert ev.kind == "data" and ev.key.startswith("mgm:") and ev.facts["kicker"] == "MARKETS GONE MAD"
    assert ev.headline == "Bitcoin's biggest one-day drop in at least two years, so far today: −9.0%" and ev.evidence == "price data"
    assert "since the 00:00 UTC daily open" in ev.facts["told"] and "closed at" not in ev.facts["told"]
    tg = live["tg"]
    assert (await lane.tick(db, get_settings(), tg, AT + timedelta(minutes=1)))["sent"]
    cap = tg.sent[0]["caption"]
    assert cap.startswith("<b>🤯 MARKETS GONE MAD</b>") and "Bitcoin's biggest one-day drop" in cap and "Yahoo Finance" in cap
    assert await engine.follow_telegram(db, get_settings(), None, 0, AT + timedelta(minutes=1)) == "published"
    assert live["x"].posts[0]["text"].startswith("MARKETS GONE MAD\n\nBitcoin's biggest one-day drop")
    db.expire_all()
    ev = db.get(NdEvent, ev.id)
    assert ev.facts["visual"]["design"] == "data" and ev.facts["visual"]["mad"] is True and live["x"].uploads[0] == tg.sent[0]["image"]
    # the next slot, same US day: no second MARKETS GONE MAD
    await _decide(db, AT + timedelta(hours=3), daily_data=data)
    assert db.scalar(select(NdEvent.id).where(NdEvent.key.like("mgm:%"), NdEvent.id != ev.id)) is None
    datapost._last.clear()


async def test_the_emergency_pause_holds_data_posts_too(client: Any, db: Session, live: dict[str, Any]) -> None:  # noqa: F811
    data = {"BTC-USD": daily(60000, [-9.0], AT - timedelta(hours=1))}
    await _decide(db, AT, daily_data=data)
    network.configure(db, emergency=True, now=AT)
    assert (await lane.tick(db, get_settings(), live["tg"], AT + timedelta(minutes=1)))["sent"] is None
    assert await engine.follow_telegram(db, get_settings(), None, 0, AT + timedelta(minutes=1)) is None
    assert live["tg"].sent == [] and live["x"].posts == []


async def test_a_live_blog_is_never_posted(client: Any, db: Session, live: dict[str, Any]) -> None:  # noqa: F811
    _report(live, AT, title="Live updates: missile strike hits Saudi Aramco terminal")
    await _decide(db, AT)
    assert db.scalar(select(NdEvent.id).where(NdEvent.kind == "news")) is None
    await lane.tick(db, get_settings(), live["tg"], AT + timedelta(minutes=1))
    assert live["tg"].sent == [] and live["x"].posts == []


async def test_a_story_is_followed_for_days_and_only_new_facts_post(client: Any, db: Session, live: dict[str, Any]) -> None:  # noqa: F811
    _report(live, AT)
    await _decide(db, AT)
    assert (await lane.tick(db, get_settings(), live["tg"], AT + timedelta(minutes=1)))["sent"]
    later = AT + timedelta(days=2)  # two days on: the 04:00 Dubai slot again
    new = "Saudi Aramco shuts down Ras Tanura terminal after missile strike"  # a material development (the desk's own rule)
    live["feeds"]["items"] = {DW["url"]: [{"title": new, "link": DW_URL + "-claim", "at": later - timedelta(minutes=30), "summary": "Aramco shut the terminal after the strike, the company said."}],
                              NATIONAL["url"]: [{"title": new + ", group says", "link": NAT_URL + "claim", "at": later - timedelta(minutes=25),
                                                 "summary": "Saudi Aramco shut down the Ras Tanura terminal."}]}
    await _decide(db, later)
    up = db.scalars(select(NdEvent).where(NdEvent.kind == "update")).one()
    assert up.status == "released" and up.facts["development"].startswith("new: ") and up.facts["kicker"] == "UPDATE · first reported 10 Nov"
    assert (await lane.tick(db, get_settings(), live["tg"], later + timedelta(minutes=1)))["sent"]
    assert live["tg"].sent[-1]["caption"].startswith("<b>🔄 UPDATE · first reported 10 Nov</b>")
    assert await engine.follow_telegram(db, get_settings(), None, 0, later + timedelta(minutes=1)) == "published"
    assert live["x"].posts[-1]["text"].startswith("UPDATE · first reported 10 Nov\n\nSaudi Aramco shuts down")
    # the same development told again by another outlet: nothing new, no post
    live["feeds"]["items"] = {DW["url"]: [{"title": new + " again", "link": DW_URL + "-claim-2", "at": later + timedelta(hours=3), "summary": "Aramco shut the terminal."}]}
    await _decide(db, later + timedelta(hours=4))
    assert len(list(db.scalars(select(NdEvent).where(NdEvent.kind == "update")))) == 1


async def test_admin_sees_the_hooks_and_can_switch_data_posts_off(client: Any, db: Session, live: dict[str, Any]) -> None:  # noqa: F811
    first = AT - timedelta(minutes=30)
    _report(live, AT)
    await _decide(db, AT, {"BZ=F": _quote("BZ=F", 80.0, 83.2, first, AT)})
    with patch("app.api.routes.newsdesk.utcnow", return_value=AT):
        got = client.get("/api/x/newsdesk/viral").json()
    story = got["stories"][0]
    assert story["hook"] and {a["hook"] for a in story["angles"]} >= {"money", "shock"} and set(got["hooks"]) == {"shock", "money", "controversy", "winners_losers", "contradiction"}
    assert got["engagement"]["learning"] is False and "Telegram" in got["engagement"]["telegram"]
    r = client.put("/api/x/newsdesk/network", json={"data_posts": False, "experiments": False})
    assert r.status_code == 200 and r.json()["data_posts"] is False and r.json()["experiments"] is False
    data = {"BTC-USD": daily(60000, [-9.0], AT + timedelta(hours=3))}
    await _decide(db, AT + timedelta(hours=4), daily_data=data)
    assert db.scalar(select(NdEvent.id).where(NdEvent.kind == "data")) is None
