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
    assert ev.facts["visual"] == {"style": "wire", "kind": "text", "layout": "text"}
    x = live["x"]
    assert await engine.follow_telegram(db, get_settings(), None, 0, BETWEEN + timedelta(minutes=1)) == "published"
    assert x.posts[0]["media"] is None and x.uploads == [] and x.posts[0]["text"].startswith("BREAKING: ")
    assert "@" not in x.posts[0]["text"] and network.x_length(x.posts[0]["text"]) <= 280
    assert db.get(NdPost, db.get(NdEvent, ev.id).x_post_id).status == "published"


async def test_a_stored_clean_choice_posts_as_wire(client: Any, db: Session, live: dict[str, Any]) -> None:  # noqa: F811
    # owner, 2026-11-16: the clean style's stock photos and headline cards are retired; an Admin setting of "clean" posts the wire way
    network.configure(db, post_style="clean")
    _both_report(live)
    await _desk_tick(db)
    tg = TextTg()
    await lane.tick(db, get_settings(), tg, BETWEEN + timedelta(seconds=30))
    ev = db.scalars(select(NdEvent).where(NdEvent.status == "released")).one()
    assert ev.facts["visual"]["style"] == "wire" and tg.sent[0]["caption"].count("\n\n") == 1  # the story, then one footer line


# ---------------------------------------------------------------- the breaking lane in the wire style
def _move(db: Session, key: str, *, ratio: float = 1.4, score: float = 76, minutes: float = 5, assets: tuple[str, ...] = ("BTC",),
          head: str = "Bitcoin falls 6.2% in 24 hours") -> NdEvent:
    ev = NdEvent(key=key, kind="market", title=head, headline=head.upper(), summary="", topic="move", evidence="price data", category="crypto",
                 virality=score, parts={}, status="candidate", first_seen=BETWEEN - timedelta(minutes=minutes), expires_at=BETWEEN + timedelta(minutes=40),
                 facts={"kind": "move", "window": "24h", "ratio": ratio, "change_pct": -6.2, "assets": list(assets), "told": head, "coin": "BTC"},
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
    assert events.release(db, BETWEEN, events.policy({"post_style": "classic"})) is None  # the classic layout: moves wait for a slot
    got = events.release(db, BETWEEN, events.policy({}))
    assert got is not None and got.id == ev.id and got.slot == "breaking" and "exceptional market move" in got.why


@pytest.mark.parametrize("why,kw", [
    ("a routine move", {"ratio": 0.8}),
    ("under the market bar", {"score": 64}),
    ("no longer happening", {"minutes": 45}),
    ("not a priority asset", {"assets": ("DOGE",), "head": "Dogecoin falls 6.2% in 24 hours"}),
])
def test_a_move_breaks_only_when_exceptional_strong_and_live(db: Session, why: str, kw: dict[str, Any]) -> None:
    _move(db, "m2", **kw)
    got = events.release(db, BETWEEN, events.policy({}))
    assert got is None, why


def test_breaking_has_a_rolling_cap_and_a_daily_one(db: Session) -> None:
    _earlier_breaking(db, [2, 4])  # two in the last six hours
    _move(db, "m3")
    assert events.release(db, BETWEEN, events.policy({})) is None
    db.query(NdEvent).delete()
    _earlier_breaking(db, [7, 12])  # two today, none in six hours: the desk's daily cap of 2 (unchanged by the wire style, 2026-11-16)
    _move(db, "m4")
    assert events.release(db, BETWEEN, events.policy({})) is None
    db.query(NdEvent).delete()
    _earlier_breaking(db, [7])  # one today, over six hours ago: room for one more
    m5 = _move(db, "m5")
    got = events.release(db, BETWEEN, events.policy({}))
    assert got is not None and got.slot == "breaking", m5.why


def test_breaking_never_crowds_another_post(db: Session) -> None:
    db.add(NdEvent(key="prev", kind="news", title="Fed holds rates", headline="FED HOLDS RATES", topic="macro", evidence="official", status="released",
                   slot="slot", released_at=BETWEEN - timedelta(minutes=60), facts={}))
    db.commit()
    _move(db, "m6")
    assert events.release(db, BETWEEN, events.policy({})) is None  # 60 minutes after the last post: under the 90-minute gap


def test_a_material_update_breaks_and_replies_to_its_original(db: Session) -> None:
    parent = NdEvent(key="p", kind="news", title="Bybaz exchange hacked for $40 million", headline="BYBAZ EXCHANGE HACKED FOR $40 MILLION", topic="hack",
                     evidence="two outlets", status="released", slot="slot", released_at=BETWEEN - timedelta(hours=3), facts={}, tg_message_id=170)
    db.add(parent)
    db.commit()
    up = _news(db, "u1", "Bybaz halts all withdrawals after $40 million hack", 60, ["CoinDesk", "The Block"], age_min=15, now=BETWEEN, topic="hack")
    up.kind, up.parent_id = "update", parent.id
    up.facts = {**up.facts, "development": "halted withdrawals"}
    db.commit()
    got = events.release(db, BETWEEN, events.policy({}))
    assert got is not None and got.id == up.id and got.slot == "breaking" and "material development" in got.why
    assert events.release(db, BETWEEN, events.policy({"post_style": "classic"})) is None


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
    assert "filler or speculation" in only.why
