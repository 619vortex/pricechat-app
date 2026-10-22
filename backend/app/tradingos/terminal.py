"""The connected trader's terminal on the website: account overview, bot positions (with their protective orders as the
exchange sees them), the member's status per signal, and trade history. Everything is read from the database the
Telegram bot uses and from Hyperliquid itself, so the website and the bot always show the same thing.

Honesty rules: PAPER numbers are labelled simulated and never shown as an exchange balance; TESTNET / LIVE numbers come
from Hyperliquid's clearinghouse state (or are shown as unavailable / stale with their age, never invented).
"""

import threading
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.members import Member
from app.models.trading import TrAccount, TrIntent, TrOrder, TrPosition
from app.models.tradingos import TsSignal, TsSignalAction
from app.tradingos import exchange as xch
from app.tradingos import execution, market

# seconds between Hyperliquid account reads per account. The Earn terminal polls every 7 s (TERMINAL_POLL_MS in terminal.tsx), so one
# open tab costs one read per poll and a second tab or device shares it; the page shows the read's real age ("synced Xs ago").
SNAP_TTL = 6.0
STALE_AFTER = 30.0  # older than this, the numbers are flagged stale
FETCH = xch.account_snapshot  # tests replace this with a fake exchange
_snaps: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _iso(d: datetime | None) -> str | None:
    return execution._aware(d).isoformat() if d else None  # type: ignore[union-attr]


def snapshot(mode: str, master: str) -> tuple[dict[str, Any] | None, float | None, str | None]:
    """(data, fetched_at, error). A failed read returns the last good one (flagged by its age), never made-up numbers."""
    key = (mode, master.lower())
    with _lock:
        hit = _snaps.get(key)
    if hit and time.time() - hit[0] < SNAP_TTL:
        return hit[1], hit[0], None
    try:
        data = FETCH(mode, master)
        with _lock:
            _snaps[key] = (time.time(), data)
        return data, time.time(), None
    except Exception as e:  # noqa: BLE001
        err = f"Hyperliquid {'testnet' if mode == 'TESTNET' else 'mainnet'} couldn't be read ({type(e).__name__})."
        return (hit[1], hit[0], err) if hit else (None, None, err)


def _marks(coins: set[str]) -> tuple[dict[str, float], float | None, str | None]:
    """Mainnet prices for paper positions: mids (2 s cache), falling back to the mark from the markets snapshot."""
    md = market.data()
    try:
        at, mids = md.mids()
        if all(c.upper() in mids for c in coins):
            return {c: mids[c.upper()] for c in coins}, at, None
    except Exception:  # noqa: BLE001
        pass
    try:
        at, ms = md.markets()
        return {c: ms[c.upper()].mark for c in coins if c.upper() in ms}, at, None
    except market.MarketError as e:
        return {}, None, str(e)


def _orders_by_kind(db: Session, pos_ids: list[int]) -> dict[int, dict[str, TrOrder]]:
    """The latest order of each kind per position (the one that counts)."""
    out: dict[int, dict[str, TrOrder]] = {}
    if not pos_ids:
        return out
    for o in db.scalars(select(TrOrder).where(TrOrder.position_id.in_(pos_ids)).order_by(TrOrder.id)):
        out.setdefault(o.position_id, {})[o.kind] = o
    return out


def _order_state(o: TrOrder | None, mode: str, live_oids: set[int] | None, done: bool = False) -> str:
    if done:
        return "FILLED"
    if o is None:
        return "MISSING"
    if o.status == "RESTING":
        if mode == "PAPER":
            return "SIMULATED"
        if live_oids is None:
            return "UNVERIFIED"  # the exchange couldn't be read just now
        return "ACTIVE" if o.oid in live_oids else "NOT_ON_EXCHANGE"
    return {"REJECTED": "REJECTED", "ERROR": "REJECTED", "CANCELLED": "CANCELLED", "FILLED": "FILLED", "PARTIAL": "FILLED"}.get(o.status, o.status)


def _test_signals(db: Session, ids: list[int]) -> set[int]:
    """TEST signals check the pipeline: their positions are labelled TEST, never presented as a real trade idea."""
    return set(db.scalars(select(TsSignal.id).where(TsSignal.id.in_(ids), TsSignal.strategy == "test"))) if ids else set()


def _entry_size(orders: dict[str, TrOrder]) -> float:
    e = orders.get("entry")
    return e.filled_size if e else 0.0


def terminal(db: Session, member: Member) -> dict[str, Any]:
    acc = execution.account(db, member)
    db.commit()
    rows = db.scalars(select(TrPosition).where(TrPosition.account_id == acc.id, TrPosition.status.in_(("OPENING", "OPEN", "PROTECTION_FAILED")))
                      .order_by(TrPosition.id)).all()
    open_rows = [p for p in rows if p.status != "OPENING" and p.size > 0]
    by_kind = _orders_by_kind(db, [p.id for p in rows])
    tests = _test_signals(db, [p.signal_id for p in rows])
    mode = acc.mode
    sync: dict[str, Any] = {"source": "paper" if mode == "PAPER" else "hyperliquid", "at": None, "age_s": None, "stale": False, "error": None}
    overview: dict[str, Any] = {"mode": mode, "auto": acc.auto_enabled, "auto_until": _iso(acc.auto_until), "paused": acc.paused,
                                "realized_today": round(execution.daily_pnl(db, acc), 2), "simulated": mode == "PAPER"}
    positions: list[dict[str, Any]] = []
    others: list[dict[str, Any]] = []
    ex_pos: dict[str, dict[str, Any]] = {}
    live_oids: set[int] | None = None

    if mode == "PAPER":
        marks, at, err = _marks({p.coin for p in open_rows})
        sync.update(at=at, error=err)
        upnl = margin = exposure = 0.0
        for p in open_rows:
            mk = marks.get(p.coin)
            sign = 1 if p.side == "LONG" else -1
            u = (mk - (p.entry_px or mk)) * p.size * sign if mk else None
            mg = (p.entry_px or 0) * p.size / max(p.leverage, 1)
            ex_pos[p.coin] = {"mark": mk, "upnl": u, "margin": mg, "value": p.size * mk if mk else None, "liq": None, "funding": None,
                              "leverage": p.leverage, "size": p.size}
            upnl += u or 0.0
            margin += mg
            exposure += p.size * (mk or p.entry_px or 0)
        eq = execution.paper_equity(db, acc) + upnl
        overview.update(equity=round(eq, 2), available=round(eq - margin, 2), margin_used=round(margin, 2), unrealized=round(upnl, 2),
                        exposure=round(exposure, 2))
    elif not acc.master_address:
        sync["error"] = "No verified wallet: connect it in Kiwi Passport."
    else:
        data, at, err = snapshot(mode, acc.master_address)
        sync.update(at=at, error=err)
        if data is not None:
            st = data["state"]
            ms = st.get("marginSummary") or {}
            live_oids = {int(o["oid"]) for o in data.get("orders", []) if o.get("oid") is not None} if not err else None
            bot_coins = {p.coin for p in open_rows if p.mode == mode}
            upnl = 0.0
            for ap in st.get("assetPositions", []):
                q = ap.get("position") or {}
                szi = _f(q.get("szi"))
                if szi == 0:
                    continue
                value = _f(q.get("positionValue"))
                row = {"coin": q.get("coin"), "side": "LONG" if szi > 0 else "SHORT", "size": abs(szi), "entry": _f(q.get("entryPx")),
                       "mark": value / abs(szi) if szi else None, "value": value, "upnl": _f(q.get("unrealizedPnl")),
                       "margin": _f(q.get("marginUsed")), "liq": _f(q.get("liquidationPx")) or None,
                       "leverage": (q.get("leverage") or {}).get("value"), "funding": _f((q.get("cumFunding") or {}).get("sinceOpen"))}
                upnl += row["upnl"]
                if row["coin"] in bot_coins:
                    ex_pos[row["coin"]] = row
                else:
                    others.append(row)  # the member's own position: shown, never touched
            eq_, av_ = _f(ms.get("accountValue")), _f(st.get("withdrawable"))
            if data.get("unified"):  # unified account: Spot USDC is the collateral (the Perps value can read $0)
                eq_ = max(eq_, _f(data.get("spot_usdc")))
                av_ = max(av_, _f(data.get("spot_usdc")) - _f(data.get("spot_hold")))
            overview.update(equity=round(eq_, 2), available=round(av_, 2), margin_used=_f(ms.get("totalMarginUsed")),
                            unrealized=round(upnl, 2), exposure=_f(ms.get("totalNtlPos")))
        else:
            overview.update(equity=None, available=None, margin_used=None, unrealized=None, exposure=None)
    if sync["at"]:
        sync["age_s"] = round(time.time() - sync["at"], 1)
        sync["stale"] = sync["age_s"] > STALE_AFTER or bool(sync["error"])
        sync["at"] = datetime.fromtimestamp(sync["at"], tz=timezone.utc).isoformat()

    for p in rows:
        orders = by_kind.get(p.id, {})
        x = ex_pos.get(p.coin, {}) if p.mode == mode else {}
        mg = x.get("margin")
        u = x.get("upnl")
        sl, t1, t2 = (_order_state(orders.get("sl"), p.mode, live_oids), _order_state(orders.get("tp1"), p.mode, live_oids, p.tp1_done),
                      _order_state(orders.get("tp2"), p.mode, live_oids))
        warnings = []
        if p.status == "PROTECTION_FAILED":
            warnings.append("No confirmed stop-loss: close this position.")
        elif p.status == "OPEN" and sl not in ("ACTIVE", "SIMULATED", "UNVERIFIED"):
            warnings.append(f"Stop-loss is {sl.replace('_', ' ').lower()}. Check it on Hyperliquid or close the position.")
        for k, s in (("TP1", t1), ("TP2", t2)):
            if s in ("REJECTED", "MISSING", "NOT_ON_EXCHANGE") and p.status == "OPEN":
                warnings.append(f"{k} is {s.replace('_', ' ').lower()} (the stop-loss still protects the position).")
        if p.mode != "PAPER" and p.mode == mode and x and abs(x["size"] - p.size) > 1e-9:
            warnings.append("The exchange shows a different size: it's being reconciled.")
        if p.mode != "PAPER" and p.mode == mode and not x and live_oids is not None and p.status == "OPEN":
            warnings.append("Hyperliquid shows no position: it's being reconciled.")
        if x.get("funding") is not None and p.mode != "PAPER":
            p.funding = x["funding"]  # remembered for the history after the close
        positions.append({
            "id": p.id, "signal_id": p.signal_id, "mode": p.mode, "coin": p.coin, "side": p.side, "status": p.status, "via": p.via,
            "size": p.size, "size_usd": x.get("value") if x.get("value") is not None else None, "entry_px": p.entry_px, "mark": x.get("mark"),
            "leverage": x.get("leverage") or p.leverage, "margin": mg, "liq_px": x.get("liq"), "upnl": u,
            "upnl_pct": round(u / mg * 100, 2) if u is not None and mg else None,  # return on the margin used by this position
            "stop": p.stop, "tp1": p.tp1, "tp2": p.tp2, "tp1_done": p.tp1_done, "sl_status": sl, "tp1_status": t1, "tp2_status": t2,
            "fees": round(p.fees, 4), "funding": x.get("funding"), "realized": round(p.realized_pnl, 4), "opened_at": _iso(p.created_at),
            "tp1_at": _iso(orders["tp1"].updated_at) if p.tp1_done and orders.get("tp1") is not None else None,
            "warnings": warnings, "error": p.error, "test": p.signal_id in tests})
    db.commit()
    return {"overview": overview, "sync": sync, "positions": positions, "other_positions": others,
            "history": history(db, acc), "signals": signal_status(db, acc, member)}


def history(db: Session, acc: TrAccount, limit: int = 100) -> list[dict[str, Any]]:
    rows = db.scalars(select(TrPosition).where(TrPosition.account_id == acc.id, TrPosition.status == "CLOSED")
                      .order_by(TrPosition.closed_at.desc(), TrPosition.id.desc()).limit(limit)).all()
    by_kind = _orders_by_kind(db, [p.id for p in rows])
    tests = _test_signals(db, [p.signal_id for p in rows])
    out = []
    for p in rows:
        size = _entry_size(by_kind.get(p.id, {}))
        opened, closed = execution._aware(p.created_at), execution._aware(p.closed_at)
        out.append({"id": p.id, "signal_id": p.signal_id, "test": p.signal_id in tests, "mode": p.mode, "coin": p.coin, "side": p.side, "entry_px": p.entry_px, "exit_px": p.exit_px,
                    "size": size, "size_usd": round(size * (p.entry_px or 0), 2), "leverage": p.leverage, "net_pnl": round(p.realized_pnl, 4),
                    "fees": round(p.fees, 4), "funding": (round(p.funding, 4) if p.funding else None) if p.mode != "PAPER" else None,
                    "duration_s": int((closed - opened).total_seconds()) if opened and closed else None, "reason": p.close_reason,
                    "opened_at": _iso(p.created_at), "closed_at": _iso(p.closed_at), "via": p.via})
    return out


def signal_status(db: Session, acc: TrAccount, member: Member, limit: int = 30) -> dict[int, dict[str, Any]]:
    """This member's own status per signal. An ACTIVE signal says nothing about whether they're in it: this does."""
    out: dict[int, dict[str, Any]] = {}
    now = utcnow()
    for a in db.scalars(select(TsSignalAction).where(TsSignalAction.member_id == member.id, TsSignalAction.action == "reject")):
        out[a.signal_id] = {"status": "REJECTED"}
    for it in db.scalars(select(TrIntent).where(TrIntent.account_id == acc.id, TrIntent.status == "PENDING")):
        if execution._aware(it.expires_at) > now:  # type: ignore[operator]
            out[it.signal_id] = {"status": "ORDER_PENDING", "detail": "preview waiting for your confirmation"}
    for p in db.scalars(select(TrPosition).where(TrPosition.account_id == acc.id).order_by(TrPosition.id.desc()).limit(200)):
        st = {"OPENING": "ORDER_PENDING", "OPEN": "POSITION_OPEN", "PROTECTION_FAILED": "POSITION_OPEN", "CLOSED": "POSITION_CLOSED"}.get(p.status)
        if st:
            out[p.signal_id] = {"status": st, "position_id": p.id, "mode": p.mode}
        elif p.signal_id not in out:
            out[p.signal_id] = {"status": "NOT_ENTERED", "detail": f"order not filled ({p.mode.lower()})"}
    return out
