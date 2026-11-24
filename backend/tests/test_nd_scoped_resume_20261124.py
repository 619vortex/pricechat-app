"""The owner's resume of one scope counts that scope afresh only (2026-11-24 review of the Admin liquidation-resume control): resuming
liquidation posts used to reset the one shared breaker count, wiping a price problem half-way to its own stop."""
from datetime import datetime, timedelta, timezone

from app.newsdesk import safety

NOW = datetime(2026, 11, 24, 18, 0, tzinfo=timezone.utc)


def anomaly(kind: str, subject: str, minutes_ago: int) -> dict:
    return {"kind": kind, "subject": subject, "why": "x", "at": (NOW - timedelta(minutes=minutes_ago)).isoformat()}


def test_resuming_liquidations_keeps_the_price_count() -> None:
    state = {"anomalies": [anomaly("implausible_move", "BTC-USD", 30), anomaly("second_source", "ETH-USD", 20),
                           anomaly("implausible_liquidations", "PIEVERSE", 25)],
             safety.reset_key("liq"): (NOW - timedelta(minutes=10)).isoformat()}
    assert len(safety.recent(state, NOW, "crypto")) == 3  # the crypto count is untouched by the liquidation resume
    assert [a["kind"] for a in safety.recent(state, NOW, "liq")] == []  # liquidations count afresh


def test_the_full_resume_still_resets_everything() -> None:
    state = {"anomalies": [anomaly("implausible_move", "BTC-USD", 30)], "breaker_reset_at": (NOW - timedelta(minutes=5)).isoformat()}
    assert safety.recent(state, NOW) == [] and safety.recent(state, NOW, "crypto") == [] and safety.recent(state, NOW, "liq") == []


def test_resuming_one_class_keeps_the_others() -> None:
    state = {"anomalies": [anomaly("implausible_move", "BTC-USD", 30), anomaly("implausible_move", "GC=F", 30)],
             safety.reset_key("crypto"): (NOW - timedelta(minutes=10)).isoformat()}
    assert safety.recent(state, NOW, "crypto") == [] and len(safety.recent(state, NOW, "commodity")) == 2
