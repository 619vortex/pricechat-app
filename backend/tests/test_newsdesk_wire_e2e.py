"""End to end in the wire style (owner, 2026-11-14): two outlets report a major event between slots; the desk verifies it and breaks it at
once; Telegram posts it as text only (no real picture fits: no AI image, no stand-in photo) with the BREAKING tag it earned and the sources and
@thepricechat on the last line; X posts the same words with no picture. Exceptional market moves and material updates may break too, under a
rolling cap; filler ranks lower in a slot. No approval step, no real service."""
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.models.newsdesk import NdEvent, NdPost
from app.newsdesk import engine, events, network, photos
from app.newsdesk import telegram as lane
from tests.test_newsdesk_breaking_e2e import BETWEEN, Tg, _both_report, _desk_tick, live  # noqa: F401 - the fixture
from tests.test_newsdesk_hardblock import _news


@pytest.fixture(autouse=True)
def _wire(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(network, "DEFAULT_STYLE", "wire")
    monkeypatch.setattr(events, "DEFAULT_SCHEDULE", "slots")
    monkeypatch.setattr(lane, "FORMAT", "visual")
    events._alerted.clear()

    async def nothing(*a: Any, **k: Any) -> list[Any]:
        return []

    monkeypatch.setattr(photos, "discover", nothing)


class TextTg(Tg):
    async def send_message(self, chat: Any, text: str, **kw: Any) -> dict[str, Any]:
        self.sent.append({"image": None, "caption": text, **kw})
        return {"message_id": 500 + len(self.sent), "chat": {"id": -100123, "type": "channel"}}


async def test_breaking_news_goes_out_text_only_on_telegram_and_x(client: Any, db: Session, live: dict[str, Any]) -> None:  # noqa: F811
    _both_report(live)
    await _desk_tick(db)
    ev = db.scalars(select(NdEvent).where(NdEvent.status == "released")).one()
    assert ev.slot == "breaking"
    tg = TextTg()
    sent = await lane.tick(db, get_settings(), tg, BETWEEN + timedelta(seconds=30))
    assert sent["sent"] == 501 and tg.sent[0]["image"] is None  # a text message, no picture forced
    cap = tg.sent[0]["caption"]
    assert cap.startswith("<b>BREAKING:</b> ") and "Ras Tanura" in cap
    assert cap.endswith('· <a href="https://t.me/thepricechat">@thepricechat</a>') and cap.count("\n\n") == 1  # story, then one footer line
    for poster in ("PRICE CHAT NEWS", "Verified across", "Why it matters", "AI illustration"):
        assert poster not in cap
    db.expire_all()
    ev = db.get(NdEvent, ev.id)
    assert {k: ev.facts["visual"][k] for k in ("style", "kind", "layout")} == {"style": "wire", "kind": "link_preview", "layout": "link_preview"}  # owner, 2026-11-23: the article's preview
    x = live["x"]
    desk_row = engine.settings_row(db)  # owner's switch off (2026-11-24: the branded card is the default fallback; off = the old hold)
    desk_row.state = {**(desk_row.state or {}), "card_fallback": False}
    db.commit()
    # owner, 2026-11-23: no licensed picture and no article link on X, so X waits for the owner; released as words alone, it goes
    assert await engine.follow_telegram(db, get_settings(), None, 0, BETWEEN + timedelta(minutes=1)) == "held" and x.posts == []
    from app.terminal import feed

    feed.release_held(db, ev.id, "text", BETWEEN + timedelta(minutes=1))
    assert await engine.deliver_x(db, engine.settings_row(db), get_settings(), None, 0, BETWEEN + timedelta(minutes=1, seconds=30)) == "published"
    assert x.posts[0]["media"] is None and x.uploads == [] and x.posts[0]["text"].startswith("BREAKING: ")
    assert "@" not in x.posts[0]["text"] and network.x_length(x.posts[0]["text"]) <= 280
    assert db.get(NdPost, db.get(NdEvent, ev.id).x_post_id).status == "published"


async def test_admin_can_switch_back_to_clean(client: Any, db: Session, live: dict[str, Any]) -> None:  # noqa: F811
    network.configure(db, post_style="clean")
    _both_report(live)
    await _desk_tick(db)
    tg = TextTg()
    await lane.tick(db, get_settings(), tg, BETWEEN + timedelta(seconds=30))
    cap = tg.sent[0]["caption"]
    # clean's own rules: sources · @thepricechat on one line; no real photo of this story exists in the test, so a text post (2026-11-16: no
    # stand-in photo, no headline card). No JUST IN: owner, 2026-11-18, a label only for a MAJOR consequence within 90 minutes of the report
    ev = db.scalars(select(NdEvent).where(NdEvent.status == "released")).one()
    # 2026-11-23: the editor rates a missile strike on Saudi Aramco's oil terminal MAJOR (an attack on energy), so JUST IN is the owner's rule
    # applied, not broken; the label follows the tier
    assert cap.startswith("<b>JUST IN:</b>") == (ev.facts["editor"]["tier"] == "major")
    assert cap.endswith(' · <a href="https://t.me/thepricechat">@thepricechat</a>')
    assert ev.facts["visual"]["style"] == "clean" and tg.sent[0]["image"] is None


# ---------------------------------------------------------------- the breaking lane in the wire style
def _move(db: Session, key: str, *, ratio: float = 1.4, score: float = 76, minutes: float = 5, assets: tuple[str, ...] = ("BTC",),
          head: str = "Bitcoin falls 10.5% in 24 hours", change: float = -10.5) -> NdEvent:
    ev = NdEvent(key=key, kind="market", title=head, headline=head.upper(), summary="", topic="move", evidence="price data", category="crypto",
                 virality=score, parts={}, status="candidate", first_seen=BETWEEN - timedelta(minutes=minutes), expires_at=BETWEEN + timedelta(minutes=40),
                 facts={"kind": "move", "window": "24h", "ratio": ratio, "change_pct": change, "assets": list(assets), "told": head, "coin": "BTC"},
                 sources=[{"name": "Hyperliquid", "url": "https://app.hyperliquid.xyz/trade/BTC"}])
    db.add(ev)
    db.commit()
    return ev


def _earlier_breaking(db: Session, hours: list[float]) -> None:
    for i, h in enumerate(hours):
        db.add(NdEvent(key=f"br{i}", kind="news", title=f"Missile strike {i} hits Gulf port", headline=f"MISSILE STRIKE {i} HITS GULF PORT", topic="geopolitics", evidence="two outlets",
                       status="released", slot="breaking", released_at=BETWEEN - timedelta(hours=h), facts={}))
    db.commit()


def test_an_exceptional_measured_move_breaks_in_the_wire_style_only(db: Session) -> None:
    ev = _move(db, "m1")
    assert events.release(db, BETWEEN, events.policy({"post_style": "clean"})) is None  # the older styles: moves wait for a slot
    got = events.release(db, BETWEEN, events.policy({}))
    assert got is not None and got.id == ev.id and got.slot == "breaking" and "exceptional market move" in got.why


@pytest.mark.parametrize("why,kw", [
    ("a routine move", {"ratio": 0.8}),
    ("under the market bar", {"score": 64}),
    ("no longer happening", {"minutes": 45}),
    ("not a priority asset", {"assets": ("DOGE",), "head": "Dogecoin falls 10.5% in 24 hours"}),
    ("significant, not major: it takes a slot (2026-11-24 calibration)", {"head": "Bitcoin falls 6.2% in 24 hours", "change": -6.2}),
])
def test_a_move_breaks_only_when_exceptional_strong_and_live(db: Session, why: str, kw: dict[str, Any]) -> None:
    _move(db, "m2", **kw)
    got = events.release(db, BETWEEN, events.policy({}))
    assert got is None, why


def test_a_major_move_has_no_daily_or_rolling_cap(db: Session) -> None:
    # owner, 2026-11-24: "Do not impose an arbitrary daily limit on genuinely major breaking news" (2 a day and 2 in six hours held
    # major moves before); the gap after the last post and the burst guard remain
    _earlier_breaking(db, [2, 4])  # two in the last six hours
    got = _move(db, "m3")
    rel = events.release(db, BETWEEN, events.policy({}))
    assert rel is not None and rel.id == got.id and rel.slot == "breaking", got.why
    db.query(NdEvent).delete()
    _earlier_breaking(db, [7, 12, 15])  # three today
    m4 = _move(db, "m4")
    rel = events.release(db, BETWEEN, events.policy({}))
    assert rel is not None and rel.slot == "breaking", m4.why
    db.query(NdEvent).delete()
    _earlier_breaking(db, [0.1])  # six minutes ago: the gap holds it
    _move(db, "m5")
    assert events.release(db, BETWEEN, events.policy({})) is None


def test_breaking_never_crowds_another_post(db: Session) -> None:
    db.add(NdEvent(key="prev", kind="news", title="Fed holds rates", headline="FED HOLDS RATES", topic="macro", evidence="official", status="released",
                   slot="slot", released_at=BETWEEN - timedelta(minutes=10), facts={}))
    db.commit()
    _move(db, "m6")
    # a major move keeps the desk's minimum gap after any post (20 minutes; it was the breaking lane's 90 before 2026-11-24: a major goes at once)
    assert events.release(db, BETWEEN, events.policy({})) is None  # 10 minutes after the last post


def test_a_material_update_breaks_and_replies_to_its_original(db: Session) -> None:
    parent = NdEvent(key="p", kind="news", title="Bybaz exchange hacked for $40 million", headline="BYBAZ EXCHANGE HACKED FOR $40 MILLION", topic="hack",
                     evidence="two outlets", status="released", slot="slot", released_at=BETWEEN - timedelta(hours=3), facts={}, tg_message_id=170)
    db.add(parent)
    db.commit()
    up = _news(db, "u1", "Bybaz halts all withdrawals after $40 million hack", 60, ["CoinDesk", "The Block"], age_min=15, now=BETWEEN, topic="hack")
    up.kind, up.parent_id = "update", parent.id
    up.facts = {**up.facts, "development": "halted withdrawals"}
    db.commit()
    # owner, 2026-11-23: only a verified MAJOR event breaks between slots. A material update that is not major (a withdrawal halt after a
    # $40M hack: significant) no longer breaks in the wire style: it takes the next slot, still as an update of its original
    assert events.release(db, BETWEEN, events.policy({})) is None
    assert up.facts["editor"]["tier"] != "major" and "it takes the next slot" in up.facts["breaking_miss"]
    db.commit()
    nxt = events.next_slot(BETWEEN, events.policy({})) + timedelta(minutes=1)
    got = events.release(db, nxt, events.policy({}))
    assert got is not None and got.id == up.id and got.slot == "slot" and got.kind == "update" and got.parent_id == parent.id


def test_filler_ranks_lower_and_a_surprising_story_takes_the_slot(db: Session) -> None:
    slot = BETWEEN - timedelta(hours=2) + timedelta(minutes=1)  # the 04:00 Dubai slot, just open
    _news(db, "f1", "Analysts say bitcoin price could test $100,000 next", 66, ["CoinDesk", "The Block"], age_min=30, now=slot, topic="macro")
    good = _news(db, "f2", "Bybaz halts withdrawals after $40 million hack", 62, ["CoinDesk", "The Block"], age_min=30, now=slot, topic="hack")
    got = events.release(db, slot, events.policy({}))
    assert got is not None and got.id == good.id
    db.query(NdEvent).delete()
    db.commit()
    only = _news(db, "f3", "Analysts say bitcoin price could test $100,000 next", 60, ["CoinDesk", "The Block"], age_min=30, now=slot, topic="macro")
    assert events.release(db, slot, events.policy({})) is None
    assert "a reaction piece" in only.why and only.facts["editor"]["tier"] == "noise"  # the editor's reason (2026-11-23)
