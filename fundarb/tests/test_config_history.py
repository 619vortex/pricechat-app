from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from fundarb.config import Settings
from fundarb.history import QuoteHistory, QuoteStore
from fundarb.models import FundingRate, OrderBook, VenueQuote

T0 = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def test_defaults_need_no_secret_and_execution_is_off() -> None:
    s = Settings()
    assert not s.live_trading and s.live_confirm_token is None and not s.live_armed
    assert s.hl_account_address is None and s.backpack_api_key is None


def test_malformed_settings_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVE_TRADING", "true")
    with pytest.raises(ValidationError, match="LIVE_CONFIRM_TOKEN"):
        Settings()
    monkeypatch.setenv("LIVE_CONFIRM_TOKEN", "  ")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("LIVE_CONFIRM_TOKEN", "yes-i-mean-it")
    assert Settings().live_armed
    monkeypatch.delenv("LIVE_TRADING")
    monkeypatch.setenv("FUNDARB_HL_ACCOUNT_ADDRESS", "0x123")
    with pytest.raises(ValidationError, match="40-hex"):
        Settings()
    monkeypatch.setenv("FUNDARB_HL_ACCOUNT_ADDRESS", "0x" + "Ab" * 20)
    assert Settings().hl_account_address == "0x" + "ab" * 20
    monkeypatch.setenv("FUNDARB_BACKPACK_API_KEY", "k")
    with pytest.raises(ValidationError, match="set together"):
        Settings()


def quote(ts: datetime, venue: str = "hyperliquid", rate: float = 0.0000125) -> VenueQuote:
    b = OrderBook.build(venue, "BTC-PERP", [(99.9, 5), (98, 50)], [(100.1, 5), (102, 50)], ts)
    return VenueQuote.build(venue, "BTC-PERP", FundingRate(rate, 1, "derived: spacing", "predicted", ts), 100, 100.2, b, (1.5, 4.5),
                            "api: userFees", ts)


def test_history_keeps_seven_days_in_order() -> None:
    h = QuoteHistory(days=7)
    for d in range(10):
        h.add(quote(T0 + timedelta(days=d)))
    s = h.series("hyperliquid", "BTC-PERP")
    assert len(s) == 8 and s[0].timestamp_utc == T0 + timedelta(days=2) and h.latest("hyperliquid", "BTC-PERP") == s[-1]
    with pytest.raises(ValueError, match="time order"):
        h.add(quote(T0))
    assert h.keys() == [("hyperliquid", "BTC-PERP")]


def test_sqlite_round_trip_keeps_every_field(tmp_path: Path) -> None:
    store = QuoteStore(tmp_path / "q.sqlite3")
    a, b = quote(T0), replace(quote(T0 + timedelta(hours=1), venue="orderly", rate=0.0001), maker_bps=None, taker_bps=None,
                              fee_source="unavailable: needs auth", depth_complete=False)
    assert store.save([a, b]) == 2
    assert store.load() == [a, b]
    assert store.load(venue="orderly") == [b] and store.load(since=T0 + timedelta(minutes=30)) == [b]
