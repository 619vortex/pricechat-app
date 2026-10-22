"""The terminal's Hyperliquid account read is shared for SNAP_TTL seconds (TEST FIXTURES only: a fake exchange, no network)."""
from typing import Any

import pytest

from app.tradingos import terminal


def test_one_read_per_poll_shared_by_a_second_tab_and_age_is_real(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[str] = []
    now = [1000.0]
    monkeypatch.setattr(terminal, "FETCH", lambda mode, master: reads.append(master) or {"n": len(reads)})
    monkeypatch.setattr(terminal.time, "time", lambda: now[0])
    terminal._snaps.clear()
    assert 5.0 < terminal.SNAP_TTL < 7.0  # shorter than the page's 7 s poll: each poll of one tab gets a fresh read
    d1, at1, _ = terminal.snapshot("LIVE", "0xAbC")
    now[0] += 2.0  # a second tab polls in between
    d2, at2, _ = terminal.snapshot("LIVE", "0xabc")
    assert reads == ["0xAbC"] and d2 == d1 and at2 == at1 == 1000.0  # shared, with the time it was really read
    now[0] += 5.0  # the first tab's next poll (7 s later)
    d3, at3, _ = terminal.snapshot("LIVE", "0xabc")
    assert len(reads) == 2 and at3 == 1007.0 and d3 == {"n": 2}
    terminal._snaps.clear()


def test_snapshot_returns_last_good_read_with_its_age_when_hyperliquid_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [2000.0]
    monkeypatch.setattr(terminal.time, "time", lambda: now[0])
    terminal._snaps.clear()
    monkeypatch.setattr(terminal, "FETCH", lambda mode, master: {"ok": 1})
    terminal.snapshot("LIVE", "0x1")

    def boom(mode: str, master: str) -> Any:
        raise TimeoutError

    monkeypatch.setattr(terminal, "FETCH", boom)
    now[0] += 30.0
    data, at, err = terminal.snapshot("LIVE", "0x1")
    assert data == {"ok": 1} and at == 2000.0 and err and "couldn't be read" in err
    terminal._snaps.clear()
