"""Module 4, rule 5: the equity event calendar gate. New equity entries are blocked across earnings, exchange holidays,
trading halts, index rebalances, splits and known after-hours news.

None of these come from Hyperliquid's API, and no keyless source was verified, so the calendar is OPERATOR DATA: the
"calendar" block of equity_operator.json (FUNDARB_EQUITY_OPERATOR_PATH). It fails closed:
- no calendar, or "covers_until" missing or in the past  -> status "unknown": every new equity entry is blocked
- an event for one of the trade's markets (or "*") whose window [start - buffer, end] contains now -> "blocked"
- otherwise "clear"

  "calendar": {"covers_until": "2026-11-30", "source": "who compiled it and from where",
               "events": [{"market": "xyz:EXAMPLE" | "*", "type": "earnings", "start": "2026-10-28T20:00:00Z",
                           "end": "2026-10-29T14:30:00Z", "source": "..."}]}
Event types: earnings, holiday, halt, rebalance, split, news. A "*" holiday also marks that day as session "holiday"."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

EVENT_TYPES = {"earnings", "holiday", "halt", "rebalance", "split", "news"}


def _ts(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Calendar:
    covers_until: str | None = None
    source: str | None = None
    events: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"covers_until": self.covers_until, "source": self.source, "events": list(self.events)}

    def holidays(self) -> frozenset[date]:
        return frozenset(_ts(e["start"]).date() for e in self.events if e["type"] == "holiday" and e["market"] == "*")


def load_operator(path: Path) -> dict[str, Any]:
    """equity_operator.json -> {"calendar": Calendar, "index_baskets": {...}}. Malformed entries fail fast."""
    raw = json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}
    cal = raw.get("calendar") or {}
    events = []
    for e in cal.get("events") or []:
        if e.get("type") not in EVENT_TYPES:
            raise ValueError(f"{path}: event type {e.get('type')!r} is not one of {sorted(EVENT_TYPES)}")
        if not e.get("market") or not e.get("start") or not e.get("end"):
            raise ValueError(f"{path}: every event needs market, start and end: {e}")
        _ts(e["start"]), _ts(e["end"])
        events.append({k: e.get(k) for k in ("market", "type", "start", "end", "source")})
    if cal.get("covers_until"):
        _ts(cal["covers_until"])
    baskets = raw.get("index_baskets") or {}
    for idx, b in baskets.items():
        w = b.get("weights") or {}
        if not w or not all(isinstance(v, (int, float)) and v > 0 for v in w.values()):
            raise ValueError(f"{path}: index_baskets[{idx!r}] needs positive published weights")
    return {"calendar": Calendar(cal.get("covers_until"), cal.get("source"), tuple(events)), "index_baskets": baskets}


def calendar_status(cal: dict[str, Any], markets: list[str], now: str, buffer_hours: float) -> dict[str, Any]:
    """Pure (stored with every equity decision): {"status": clear|blocked|unknown, "reason", "events"}."""
    t = _ts(now)
    until = cal.get("covers_until")
    if not until:
        return {"status": "unknown", "reason": "event calendar not loaded (equity_operator.json calendar.covers_until): new entries blocked",
                "events": []}
    end_of_cover = _ts(until) + (timedelta(days=1) if len(until) <= 10 else timedelta(0))  # a bare date covers that whole day
    if t >= end_of_cover:
        return {"status": "unknown", "reason": f"event calendar expired (covers until {until}): new entries blocked", "events": []}
    hits = [e for e in cal.get("events", []) if (e["market"] == "*" or e["market"] in markets)
            and _ts(e["start"]) - timedelta(hours=buffer_hours) <= t <= _ts(e["end"])]
    if hits:
        return {"status": "blocked", "reason": "; ".join(f"{e['type']} {e['market']} {e['start']}..{e['end']}" for e in hits), "events": hits}
    return {"status": "clear", "reason": f"no event within {buffer_hours:g}h (calendar covers until {until})", "events": []}
