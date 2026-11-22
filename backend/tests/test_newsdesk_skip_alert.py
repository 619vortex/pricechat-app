"""Owner alert when News Desk slots keep going empty (2026-11-22: production skipped every slot from 11-21 12:00 UTC into 11-22 and nobody was
told). SKIP_ALERT_AFTER skipped slots in a row send ONE alert with the top refusal reasons and the best waiting candidate; no repeat until a slot
is filled or SKIP_ALERT_AFTER more are skipped; a filled slot after an alerted run sends one "recovered" message. No test reaches a real service."""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.newsdesk import NdEvent
from app.newsdesk import engine, events

SLOT = datetime(2026, 11, 9, 4, 0, tzinfo=timezone.utc)  # 08:00 Dubai
H = timedelta(hours=1)


@pytest.fixture(autouse=True)
def _slots(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "DEFAULT_SCHEDULE", "slots")
    events._alerted.clear()
    row = engine.settings_row(db)
    row.state = {"slots_since": (SLOT - timedelta(days=2)).isoformat()}
    db.commit()


@pytest.fixture
def told(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    async def notify(db: Session, key: str, text: str, **kw: Any) -> bool:
        out.append((key, text))
        return True

    monkeypatch.setattr("app.alerts.notify_owner", notify)
    return out


def skip_alerts(told: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [t for t in told if t[0].startswith("newsdesk:skipped:")]


def candidate(db: Session, key: str, title: str, score: float, why: str) -> NdEvent:
    e = NdEvent(key=key, kind="news", title=title, headline=title, evidence="attributed", virality=score, status="candidate", why=why, first_seen=utcnow())
    db.add(e)
    db.commit()
    return e


async def skip(db: Session, slot: datetime) -> None:
    """The desk looks at the slot, releases nothing (note_skip), and the slot alerts run once the slot's catch-up window is over."""
    p = events.policy({})
    events.note_skip(db, slot + timedelta(minutes=5), p)
    await events.alert_slots(db, slot + 2 * H, p)


async def test_one_skip_alerts_nobody_two_alert_once_with_reasons_and_a_third_does_not_repeat(db: Session, told: list[tuple[str, str]]) -> None:
    candidate(db, "a", "Pieverse listing rumour", 48, "scores 48, under the bar of 56")
    candidate(db, "b", "Small cap rally", 41, "scores 41, under the bar of 56")
    candidate(db, "c", "Senator criticises bill", 52, "not worth a post: routine political controversy")
    candidate(db, "d", "Coin up 3%", 30, "a routine market move, not exceptional: 3% in a day")

    await skip(db, SLOT)
    assert skip_alerts(told) == []
    assert engine.settings_row(db).state["skip_run"]["count"] == 1

    await skip(db, SLOT + 4 * H)
    alerts = skip_alerts(told)
    assert len(alerts) == 1
    key, text = alerts[0]
    assert key == f"newsdesk:skipped:{SLOT.isoformat()}:1"
    assert "2 slots in a row went empty" in text and "08:00 Dubai" in text and "12:00 Dubai" in text
    assert "scores 48, under the bar of 56 (4x)" in text  # the two slots' "under the bar" refusals are one reason, whatever the numbers
    assert "routine political controversy (2x)" in text and "a routine market move" in text
    assert text.count("\n- ") == 3  # the top three reasons only
    assert "Best waiting: #" in text and "Senator criticises bill" in text  # the highest-scoring waiting candidate

    await events.alert_slots(db, SLOT + 6 * H + timedelta(minutes=5), events.policy({}))  # every later pass: nothing new
    events._alerted.clear()  # even after a restart (the run is kept in the desk's state)
    await events.alert_slots(db, SLOT + 6 * H + timedelta(minutes=10), events.policy({}))
    await skip(db, SLOT + 8 * H)
    assert len(skip_alerts(told)) == 1
    assert engine.settings_row(db).state["skip_run"]["count"] == 3

    await skip(db, SLOT + 12 * H)  # SKIP_ALERT_AFTER more: told again
    alerts = skip_alerts(told)
    assert len(alerts) == 2 and alerts[1][0] == f"newsdesk:skipped:{SLOT.isoformat()}:2" and "4 slots in a row" in alerts[1][1]


async def test_a_filled_slot_ends_the_run_with_one_recovered_message_and_counting_starts_again(db: Session, told: list[tuple[str, str]]) -> None:
    candidate(db, "a", "Pieverse listing rumour", 48, "scores 48, under the bar of 56")
    await skip(db, SLOT)
    await skip(db, SLOT + 4 * H)
    assert len(skip_alerts(told)) == 1

    filled = SLOT + 8 * H
    db.add(NdEvent(key="out", kind="news", title="Fed cuts rates", headline="FED CUTS RATES", evidence="official", virality=72, status="released",
                   slot="slot", released_at=filled + timedelta(minutes=1)))
    db.commit()
    await events.alert_slots(db, filled + timedelta(minutes=10), events.policy({}))
    alerts = skip_alerts(told)
    assert len(alerts) == 2 and alerts[1][0] == f"newsdesk:skipped:{SLOT.isoformat()}:recovered"
    assert "recovered" in alerts[1][1] and "FED CUTS RATES" in alerts[1][1] and "after 2 empty slots" in alerts[1][1]
    assert engine.settings_row(db).state["skip_run"].get("count") is None

    await events.alert_slots(db, filled + 2 * H, events.policy({}))
    await skip(db, SLOT + 12 * H)  # one skip after the fill: a new run of one, nobody told
    assert len(skip_alerts(told)) == 2
    await skip(db, SLOT + 16 * H)  # a second: a new alert for the new run
    alerts = skip_alerts(told)
    assert len(alerts) == 3 and alerts[2][0] == f"newsdesk:skipped:{(SLOT + 12 * H).isoformat()}:1"


async def test_a_run_that_was_never_alerted_ends_quietly_and_a_missed_slot_breaks_the_run(db: Session, told: list[tuple[str, str]]) -> None:
    await skip(db, SLOT)
    db.add(NdEvent(key="out", kind="news", title="Fed cuts rates", headline="FED CUTS RATES", evidence="official", virality=72, status="released",
                   slot="slot", released_at=SLOT + 4 * H + timedelta(minutes=1)))
    db.commit()
    await events.alert_slots(db, SLOT + 6 * H, events.policy({}))
    assert skip_alerts(told) == []  # one skip, then a fill: no alert, no "recovered"

    await skip(db, SLOT + 8 * H)
    await events.alert_slots(db, SLOT + 14 * H, events.policy({}))  # the 20:00 Dubai slot: nothing at all, not even a skip = missed
    assert any(k == f"newsdesk:slot:{(SLOT + 12 * H).isoformat()}" for k, _ in told)  # its own missed-slot alert
    await skip(db, SLOT + 16 * H)
    assert skip_alerts(told) == []  # skip, missed, skip: never two in a row


async def test_no_candidates_waiting_still_says_so(db: Session, told: list[tuple[str, str]]) -> None:
    await skip(db, SLOT)
    await skip(db, SLOT + 4 * H)
    (_, text), = skip_alerts(told)
    assert "none recorded" in text and "Best waiting: none" in text
