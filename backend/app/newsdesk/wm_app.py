"""The World Monitor desktop app on the owner's PC, connected to Admin › World Monitor (owner, 2026-11-12: "I downloaded and installed
World Monitor on the machine, connect it to the admin panel").

The app (open source, worldmonitor.app) runs a local data server on the PC. The PC bridge (worker/pricechat_worker/worldmonitor.py) starts
its own headless copy of that server, reads the feeds that work without keys every few minutes (USGS earthquakes, the country instability
index, military air activity by theater, climate anomalies, FAA delays, cyber threats, macro signals), and pushes one compact snapshot
here with the PC worker's token. Nothing on the PC is reachable from outside; the bridge only calls out.

Stored as one row of ex_settings (key below; no migration), NOT in nd_settings.state, which every desk tick rewrites. Read-only for the
desk: these are signals for the owner to watch, not stories. Nothing here posts.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.exchange import ExSetting

KEY = "newsdesk.wm_app"
MAX_SIGNALS = 200
MAX_BYTES = 400_000
STALE_S = 15 * 60  # the bridge pushes every 5 minutes; three missed pushes = offline
LAYERS = ("earthquake", "military", "instability", "aviation", "climate", "cyber", "other")
SEVERITIES = ("critical", "high", "elevated", "low")


class WmError(ValueError):
    pass


def _clip(v: Any, n: int) -> str:
    return str(v if v is not None else "")[:n]


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) < 1e9 else None


def clean_signal(s: dict[str, Any]) -> dict[str, Any] | None:
    title = _clip(s.get("title"), 200).strip()
    if not title:
        return None
    url = _clip(s.get("url"), 500)
    lat, lon = _num(s.get("lat")), _num(s.get("lon"))
    return {
        "id": _clip(s.get("id"), 120) or title[:120],
        "layer": s.get("layer") if s.get("layer") in LAYERS else "other",
        "severity": s.get("severity") if s.get("severity") in SEVERITIES else "low",
        "title": title,
        "detail": _clip(s.get("detail"), 300),
        "at": _clip(s.get("at"), 40) or None,
        "url": url if url.startswith("https://") else "",
        "lat": lat if lat is not None and -90 <= lat <= 90 else None,
        "lon": lon if lon is not None and -180 <= lon <= 180 else None,
    }


def feed_health(payload: dict[str, Any], feeds: dict[str, dict[str, Any]], signals: int) -> dict[str, Any]:
    """feeds_ok / feeds_failed as the bridge counted them (1.1.0+), else counted here (older bridges: every feed not ok is failed).
    `bad`: no feed answered, or zero signals (2026-11-22: an orphaned sidecar made every feed answer 401 for hours, unnoticed)."""
    ok, failed = _num(payload.get("feeds_ok")), _num(payload.get("feeds_failed"))
    if ok is None:
        ok = sum(1 for f in feeds.values() if f["ok"])
    if failed is None:
        failed = sum(1 for f in feeds.values() if not f["ok"])
    ok_n, failed_n = max(0, int(ok)), max(0, int(failed))
    why = next((f"{k}: {f['note']}" for k, f in feeds.items() if not f["ok"] and f["note"]), "")
    return {"feeds_ok": ok_n, "feeds_failed": failed_n, "bad": ok_n == 0 or signals == 0, "bad_note": why[:200]}


def save(db: Session, worker_name: str, payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    signals = [c for c in (clean_signal(s) for s in (payload.get("signals") or [])[:MAX_SIGNALS] if isinstance(s, dict)) if c]
    feeds = {
        _clip(k, 40): {"ok": bool(v.get("ok")), "count": int(_num(v.get("count")) or 0), "note": _clip(v.get("note"), 160), "ms": int(_num(v.get("ms")) or 0)}
        for k, v in list((payload.get("feeds") or {}).items())[:40] if isinstance(v, dict)
    }
    value = {
        "received_at": now.isoformat(),
        "worker": _clip(worker_name, 80),
        "app_version": _clip(payload.get("app_version"), 20),
        "bridge_version": _clip(payload.get("bridge_version"), 20),
        "collected_at": _clip(payload.get("collected_at"), 40),
        "feeds": feeds,
        "signals": signals,
        "summary": payload.get("summary") if isinstance(payload.get("summary"), dict) else {},
    }
    value.update(feed_health(payload, feeds, len(signals)))
    if len(json.dumps(value, default=str)) > MAX_BYTES:
        raise WmError("snapshot too large")
    row = db.get(ExSetting, KEY)
    prev = dict(row.value or {}) if row is not None else {}
    value["bad_since"] = (prev.get("bad_since") or now.isoformat()) if value["bad"] else None  # how long it has been broken (monitor)
    if row is None:
        row = ExSetting(key=KEY, value=value, updated_at=now)
        db.add(row)
    else:
        row.value, row.updated_at = value, now
    db.commit()
    return {"ok": True, "signals": len(signals), "feeds": len(feeds)}


def view(db: Session, now: datetime) -> dict[str, Any]:
    row = db.get(ExSetting, KEY)
    if row is None or not row.value:
        return {"connected": False, "seen": False, "age_seconds": None, "signals": [], "feeds": {}, "summary": {},
                "note": "The World Monitor bridge on your PC hasn't reported yet."}
    v = dict(row.value)
    try:
        age = (now - datetime.fromisoformat(v["received_at"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        age = None
    v["seen"] = True
    v["age_seconds"] = age
    v["connected"] = age is not None and age < STALE_S
    order = {s: i for i, s in enumerate(SEVERITIES)}
    newest_first = sorted(v.get("signals") or [], key=lambda s: s.get("at") or "", reverse=True)
    v["signals"] = sorted(newest_first, key=lambda s: order.get(s.get("severity"), 9))  # most severe first, newest first within each
    v["note"] = "" if v["connected"] else "No report from the PC for over 15 minutes: the PC is off, or the bridge task stopped."
    return v
