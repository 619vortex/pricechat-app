"""Kiwi VPN server health and capacity: what the 5-minute sync saw, turned into uptime, latency, load and traffic figures.

The sync (service.sync_server) records one VpnServerCheck per server per run: whether its agent answered, the round trip of
the status call from the backend, and how many WireGuard peers it had and how many of them had a handshake in the last
10 minutes. Nothing about any customer is kept here: only counts. Rows older than KEEP are deleted by the sync.

Used by:
- Admin (GET /api/vpn/admin/health): per server status, uptime 24 h / 7 d, latency, live load peak, traffic projection; and
  the fleet view (healthy public servers, capacity used, one-server countries, advice).
- Members' server list (service.overview): `health` "ok" | "degraded" next to load, so Smart Connect and failover can
  prefer healthy servers. The apps ignore unknown fields, so older apps are unaffected.
- Owner alerts (app/alerts.py): fleet capacity, too few healthy servers, a server's traffic projected over its allowance.

Server-side latency here is backend -> agent, a health signal (a node whose agent takes seconds to answer is in trouble).
The latency a member feels is measured by the apps themselves (Windows 2.1 smart.ts / measure.rs, the extension).
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Integer, cast, delete, func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.vpn import VpnServer, VpnServerCheck

KEEP = timedelta(days=14)
LIVE_WINDOW = timedelta(minutes=10)  # same as service.LIVE_WINDOW: a WireGuard client that's in use re-handshakes every 2 min
HEALTHY_UPTIME = 0.98  # below this over 24 h (with enough checks to say so), a server is "degraded"
MIN_CHECKS = 12  # an hour of 5-minute checks before uptime can call a server degraded
SLOW_MS = 2500  # p95 of the agent's status call over 24 h; the client times out at 10 s
NEARLY_FULL = 0.9  # devices / capacity
FLEET_BUSY = 0.7  # devices on public servers / their capacity: time to add a server
PROJECT_AFTER_DAYS = 3  # don't project a month's traffic from its first days
GB = 1e9  # providers bill decimal units; the traffic alerts use the same


def _aware(d: datetime | None) -> datetime | None:
    return d.replace(tzinfo=timezone.utc) if d is not None and d.tzinfo is None else d


def live_peers(peers: list[dict[str, Any]], now: datetime) -> int:
    """Peers with a handshake in the last LIVE_WINDOW (latest_handshake is a Unix time, 0 = never)."""
    since = (now - LIVE_WINDOW).timestamp()
    return sum(1 for p in peers if int(p.get("latest_handshake") or 0) >= since)


def record(db: Session, server: VpnServer, *, ok: bool, ms: int | None, peers: int = 0, live: int = 0,
           error: str | None = None, now: datetime | None = None) -> VpnServerCheck:
    """One check. The caller commits."""
    first = (error or "").strip().splitlines()[0][:120] if error else None
    row = VpnServerCheck(server_id=server.id, at=now or utcnow(), ok=ok, ms=ms if ok else None, peers=peers, live=live, error=first)
    db.add(row)
    return row


def prune(db: Session, now: datetime | None = None) -> int:
    cutoff = (now or utcnow()) - KEEP
    n = db.execute(delete(VpnServerCheck).where(VpnServerCheck.at < cutoff)).rowcount or 0
    return int(n)


def _pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


def month_fraction(now: datetime) -> float:
    """How much of the calendar month (UTC) has passed, 0..1."""
    days = calendar.monthrange(now.year, now.month)[1]
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return min(1.0, max(0.0, (now - start).total_seconds() / (days * 86400)))


def projected_bytes(used: int, now: datetime) -> int | None:
    """This month's traffic at the current pace, or None in the first PROJECT_AFTER_DAYS days."""
    if now.day <= PROJECT_AFTER_DAYS:
        return None
    frac = month_fraction(now)
    return int(used / frac) if frac > 0 else None


def uptime_by_server(db: Session, since: datetime) -> dict[int, tuple[int, int]]:
    """{server_id: (checks, ok checks)} since then. One grouped query: cheap enough for the members' list."""
    rows = db.execute(select(VpnServerCheck.server_id, func.count(), func.sum(cast(VpnServerCheck.ok, Integer)))
                      .where(VpnServerCheck.at >= since).group_by(VpnServerCheck.server_id)).all()
    return {int(sid): (int(n), int(okn or 0)) for sid, n, okn in rows}


def member_health(db: Session, servers: list[VpnServer], now: datetime | None = None) -> dict[int, str]:
    """"ok" or "degraded" per server for the members' list: the last check failed, or 24 h uptime under HEALTHY_UPTIME."""
    up = uptime_by_server(db, (now or utcnow()) - timedelta(hours=24))
    out: dict[int, str] = {}
    for v in servers:
        n, okn = up.get(v.id, (0, 0))
        bad = (v.fail_streak or 0) > 0 or (n >= MIN_CHECKS and okn / n < HEALTHY_UPTIME)
        out[v.id] = "degraded" if bad else "ok"
    return out


def server_rows(db: Session, now: datetime | None = None) -> list[dict[str, Any]]:
    """Every server's health and capacity figures for Admin."""
    from app.vpn.service import device_counts, server_traffic

    now = now or utcnow()
    since7, since24 = now - timedelta(days=7), now - timedelta(hours=24)
    checks: dict[int, list[VpnServerCheck]] = {}
    for c in db.scalars(select(VpnServerCheck).where(VpnServerCheck.at >= since7).order_by(VpnServerCheck.at)):
        checks.setdefault(c.server_id, []).append(c)
    counts = device_counts(db)
    traffic = server_traffic(db, now)
    rows = []
    for v in db.scalars(select(VpnServer).order_by(VpnServer.id)):
        cs = checks.get(v.id, [])
        day = [c for c in cs if _aware(c.at) >= since24]
        up24 = (sum(c.ok for c in day) / len(day)) if day else None
        up7 = (sum(c.ok for c in cs) / len(cs)) if cs else None
        ms24 = [c.ms for c in day if c.ok and c.ms is not None]
        p95 = _pct(ms24, 0.95)
        devices = counts.get(v.id, 0)
        load = round(devices / v.capacity, 4) if v.capacity > 0 else 1.0
        live_now = cs[-1].live if cs else 0
        live_peak = max((c.live for c in cs), default=0)
        used = traffic.get(v.id, 0)
        proj = projected_bytes(used, now)
        incl = v.traffic_included_gb * GB if v.traffic_included_gb > 0 else None
        reasons: list[str] = []
        warnings: list[str] = []
        if (v.fail_streak or 0) > 0:
            reasons.append(f"the last {v.fail_streak} check(s) failed")
        if up24 is not None and len(day) >= MIN_CHECKS and up24 < HEALTHY_UPTIME:
            reasons.append(f"uptime {up24 * 100:.1f}% in 24 h")
        if p95 is not None and p95 > SLOW_MS:
            reasons.append(f"agent slow: p95 {p95} ms")
        if load >= NEARLY_FULL:
            reasons.append(f"nearly full: {devices} of {v.capacity} devices")
        if incl and proj is not None and proj >= incl:
            warnings.append(f"traffic on pace for {proj / GB:.0f} GB of {v.traffic_included_gb} GB included: the rest is billed")
        if incl is None:
            warnings.append("no traffic allowance set: no bandwidth alerts")
        if not v.enabled:
            status = "disabled"
        elif not v.online:
            status = "offline"
        else:
            status = "degraded" if reasons else "healthy"
        rows.append({
            "id": v.id, "name": v.name, "region": v.region, "country_code": v.country_code, "enabled": v.enabled,
            "testers_only": v.testers_only, "online": v.online, "status": status, "reasons": reasons, "warnings": warnings,
            "checks_24h": len(day), "uptime_24h": None if up24 is None else round(up24, 4), "uptime_7d": None if up7 is None else round(up7, 4),
            "ms_last": cs[-1].ms if cs else None, "ms_p50_24h": _pct(ms24, 0.5), "ms_p95_24h": p95,
            "devices": devices, "capacity": v.capacity, "load": load, "live_now": live_now, "live_peak_7d": live_peak,
            "live_load_peak_7d": round(live_peak / v.capacity, 4) if v.capacity > 0 else 1.0,
            "traffic_month_gb": round(used / GB, 1), "traffic_included_gb": v.traffic_included_gb,
            "traffic_projected_gb": None if proj is None else round(proj / GB, 1),
            "traffic_projected_share": None if (proj is None or not incl) else round(proj / incl, 3),
            "last_seen": v.last_seen.isoformat() if v.last_seen else None, "last_error": v.last_error,
        })
    return rows


def fleet(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The whole fleet, from server_rows: what members can use (enabled, public), and what to do next."""
    public = [r for r in rows if r["enabled"] and not r["testers_only"]]
    up = [r for r in public if r["status"] in ("healthy", "degraded")]
    healthy = [r for r in public if r["status"] == "healthy"]
    capacity = sum(r["capacity"] for r in up)
    devices = sum(r["devices"] for r in up)
    util = round(devices / capacity, 4) if capacity else None
    countries: dict[str, int] = {}
    for r in up:
        cc = (r["country_code"] or "?").upper()
        countries[cc] = countries.get(cc, 0) + 1
    advice: list[str] = []
    if public and len(healthy) < 2:
        advice.append(f"only {len(healthy)} healthy public server(s): one more failure leaves members without a fallback")
    if util is not None and util >= FLEET_BUSY:
        advice.append(f"devices fill {util * 100:.0f}% of public capacity: add a server or raise capacities")
    for r in public:
        if r["traffic_projected_share"] is not None and r["traffic_projected_share"] >= 1:
            advice.append(f"{r['name']}: traffic on pace for {r['traffic_projected_gb']:.0f} GB of {r['traffic_included_gb']} GB included")
    return {
        "public_servers": len(public), "public_up": len(up), "public_healthy": len(healthy),
        "testers_only": sum(1 for r in rows if r["enabled"] and r["testers_only"]),
        "capacity": capacity, "devices": devices, "utilization": util,
        "live_now": sum(r["live_now"] for r in up), "live_peak_7d_sum": sum(r["live_peak_7d"] for r in up),
        "countries": countries, "single_server_countries": sorted(cc for cc, n in countries.items() if n == 1),
        "advice": advice,
    }


def report(db: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    rows = server_rows(db, now)
    return {"generated_at": now.isoformat(), "servers": rows, "fleet": fleet(rows),
            "limits": {"healthy_uptime": HEALTHY_UPTIME, "slow_ms": SLOW_MS, "nearly_full": NEARLY_FULL, "fleet_busy": FLEET_BUSY,
                       "keep_days": KEEP.days}}
