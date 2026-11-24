"""The branded Price Chat news card as the last picture (owner, 2026-11-24): a post with no licensed picture and no article preview (or,
on X, no link card) goes out with the card instead of waiting; the owner's switch (`card_fallback: false`) restores the hold."""
import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.models.newsdesk import NdEvent
from app.newsdesk import engine, events, network, newscard, photos
from app.newsdesk import telegram as lane
from tests.test_newsdesk_breaking_e2e import BETWEEN, _both_report, _desk_tick, live  # noqa: F401 - the fixture
from tests.test_newsdesk_wire_e2e import TextTg

NOW = datetime(2026, 11, 24, 16, 0, tzinfo=timezone.utc)


def _ev(**kw: Any) -> SimpleNamespace:
    base = {"id": 7, "kind": "news", "headline": "ZELENSKY CONFIRMS UKRAINE WAS BEHIND DRONE ATTACK ON RUSSIAN CARGO SHIP", "title": "",
            "summary": "", "label": "", "key": "k", "facts": {"published_at": NOW.isoformat()}, "evidence": "two outlets",
            "sources": [{"name": "Moscow Times", "url": "https://www.themoscowtimes.com/x"}, {"name": "CNA", "url": "https://www.channelnewsasia.com/y"}],
            "first_seen": NOW, "created_at": NOW, "category": "politics", "virality": 70.0, "topic": "conflict", "parts": {}}
    return SimpleNamespace(**{**base, **kw})


def test_the_card_is_drawn_from_the_post_s_own_words() -> None:
    png = newscard.render(_ev(), NOW)
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"
    assert Image.open(io.BytesIO(png)).size == (1200, 675)


def test_a_market_card_carries_the_measured_figure() -> None:
    png = newscard.render(_ev(kind="market", headline="BITCOIN FALLS 5.0% IN AN HOUR", facts={"stat": "-5.0%", "spark": [60, 59, 58, 57],
                                                                                              "as_of": NOW.isoformat(), "symbol": "BTC-USD"}), NOW)
    assert png


def test_no_headline_no_card_and_the_switch_turns_it_off() -> None:
    assert newscard.render(_ev(headline="", title=""), NOW) is None
    assert newscard.enabled({}) and newscard.enabled({"card_fallback": True}) and not newscard.enabled({"card_fallback": False})


@pytest.fixture()
def _wire(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(network, "DEFAULT_STYLE", "wire")
    monkeypatch.setattr(events, "DEFAULT_SCHEDULE", "slots")
    monkeypatch.setattr(lane, "FORMAT", "visual")
    events._alerted.clear()

    async def nothing(*a: Any, **k: Any) -> list[Any]:
        return []

    monkeypatch.setattr(photos, "discover", nothing)


async def test_x_posts_the_branded_card_instead_of_holding(client: Any, db: Session, live: dict[str, Any], _wire: None) -> None:  # noqa: F811
    _both_report(live)
    await _desk_tick(db)
    ev = db.scalars(select(NdEvent).where(NdEvent.status == "released")).one()
    await lane.tick(db, get_settings(), TextTg(), BETWEEN + timedelta(seconds=30))
    x = live["x"]
    # no licensed picture and no article link on X: the card, not a hold (owner, 2026-11-24)
    assert await engine.follow_telegram(db, get_settings(), None, 0, BETWEEN + timedelta(minutes=1)) == "published"
    assert len(x.uploads) == 1 and x.posts[0]["media"]
    db.expire_all()
    assert db.get(NdEvent, ev.id).facts["visual"]["kind"] == "branded card"
