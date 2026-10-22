"""The SIMULATED AUTO-TRADER (owner-controlled, off by default; PAPER TRADING ONLY).

When the owner switches it on in the admin dashboard, the exchange looks alive before there are enough members:
  - each card gets a gentle "simulated price path": a mean-reverting random walk with momentum, never further than
    swing_pct from the card's REAL reference price. The simulated market maker centres its quotes on that path.
  - a separate, openly labelled system account ("SIMULATED AUTO-TRADER") takes those quotes with small market orders a
    few times a minute, so candles form. Every one of its fills is an ordinary recorded trade, shown as SIM.

What it never does: pretend to be a member (it's never ranked, never in equity boards), trade with itself, create
money (it's funded once from the treasury by a journal, like the maker), backfill history, or move anything when the
reference is stale / insufficient (the maker stops quoting then, so there's nothing to take).
"""

import math
import random
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.exchange.ledger import USDT, ExchangeError, Leg, balances, post, system
from app.models.exchange import ExMarket, ExSetting, ExTrade, PerpMarket, PerpPosition, PerpTrade

TICK_SECONDS = 15
AUTO_USDT = 1_000_000_00
INVENTORY_VALUE = 15_000_00  # spot contracts per market, at the reference when seeded
PERP_LEVERAGE = 2
PERP_POSITION_VALUE = 15_000_00
DEFAULTS: dict[str, Any] = {"enabled": False, "spot": True, "perps": True, "activity": 4.0, "swing_pct": 2.0, "max_trade_value": 300_00}
LIMITS = {"activity": (0.5, 12.0), "swing_pct": (0.3, 4.0), "max_trade_value": (10_00, 2_000_00)}


# --- settings & state ------------------------------------------------------------------------------------------------------


def _row(db: Session, key: str) -> ExSetting | None:
    return db.get(ExSetting, key)


def config(db: Session) -> dict[str, Any]:
    r = _row(db, "autotrader")
    return {**DEFAULTS, **(r.value if r else {})}


def set_config(db: Session, changes: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    cfg = config(db)
    for k, v in changes.items():
        if k not in DEFAULTS or v is None:
            continue
        if k in LIMITS:
            lo, hi = LIMITS[k]
            v = max(lo, min(hi, float(v)))
            if k == "max_trade_value":
                v = int(v)
        else:
            v = bool(v)
        cfg[k] = v
    r = _row(db, "autotrader")
    if r is None:
        db.add(ExSetting(key="autotrader", value=cfg, updated_at=now or utcnow()))
    else:
        r.value, r.updated_at = dict(cfg), now or utcnow()
    db.commit()
    return cfg


def _state(db: Session) -> dict[str, Any]:
    r = _row(db, "autotrader_state")
    return dict(r.value) if r else {}


def _save_state(db: Session, st: dict[str, Any], now: datetime) -> None:
    r = _row(db, "autotrader_state")
    if r is None:
        db.add(ExSetting(key="autotrader_state", value=st, updated_at=now))
    else:
        r.value, r.updated_at = st, now


def center(db: Session, symbol: str, ref: int, kind: str = "spot") -> int:
    """Where the simulated maker centres its quotes: the real reference, or (auto-trader on) the simulated path."""
    cfg = config(db)
    if not ref or not cfg["enabled"] or not cfg[kind]:
        return ref
    off = (_state(db).get("paths", {}).get(symbol) or {}).get("off", 0.0)
    return max(1, int(round(ref * (1 + off / 10_000))))


def active(db: Session) -> bool:
    return bool(config(db)["enabled"])


def _step(p: dict[str, float], swing_bps: float, rng: random.Random) -> dict[str, float]:
    """One 15-second step: momentum that fades, a pull back to the reference, noise; never past the swing."""
    off, mom = p.get("off", 0.0), p.get("mom", 0.0)
    mom = mom * 0.9 + rng.gauss(0, swing_bps * 0.025)
    off = off + mom - 0.03 * off + rng.gauss(0, swing_bps * 0.05)
    if abs(off) > swing_bps:
        off, mom = math.copysign(swing_bps, off), -mom * 0.5
    return {"off": round(off, 2), "mom": round(mom, 2)}


def _seed(db: Session, now: datetime) -> None:
    a, t = system(db, "auto"), system(db, "treasury")
    post(db, "seed", "seed:auto:usdt", "Simulated auto-trader: paper USDT", [Leg(t.id, USDT, -AUTO_USDT), Leg(a.id, USDT, AUTO_USDT)], now,
         treasury_may_go_negative=True)
    for m in db.scalars(select(ExMarket)):
        if m.ref_contract_cents:
            qty = max(1, INVENTORY_VALUE // m.ref_contract_cents)
            post(db, "seed", f"seed:auto:{m.symbol}", f"Simulated auto-trader: starting {m.symbol} inventory",
                 [Leg(t.id, m.symbol, -qty), Leg(a.id, m.symbol, qty)], now, treasury_may_go_negative=True)


def _poisson(lam: float, rng: random.Random) -> int:
    n, p, L = 0, 1.0, math.exp(-lam)
    while True:
        p *= rng.random()
        if p <= L:
            return min(n, 6)
        n += 1


# --- the tick ---------------------------------------------------------------------------------------------------------------


def tick(db: Session, now: datetime | None = None, rng: random.Random | None = None) -> dict[str, Any]:
    """Every 15 s while on: move each card's path, re-centre the maker, take a few of its quotes."""
    from app.exchange import engine, maker, perps

    now = now or utcnow()
    cfg = config(db)
    if not cfg["enabled"]:
        return {"enabled": False}
    rng = rng or random.Random()
    bot = system(db, "auto")
    _seed(db, now)
    db.commit()
    st = _state(db)
    paths = st.setdefault("paths", {})
    swing = cfg["swing_pct"] * 100
    lam = cfg["activity"] * TICK_SECONDS / 60
    out: dict[str, Any] = {}
    for m in db.scalars(select(ExMarket).order_by(ExMarket.sort)).all():
        paths[m.symbol] = _step(paths.get(m.symbol, {}), swing, rng)
    st["at"] = now.isoformat()
    _save_state(db, st, now)
    db.commit()
    for m in db.scalars(select(ExMarket).order_by(ExMarket.sort)).all():
        ref = m.ref_contract_cents
        if not ref or m.status != "active" or m.ref_status != "ok":
            continue
        p = paths[m.symbol]
        lean = 0.5 + 0.35 * math.tanh(p["mom"] / max(1.0, swing * 0.05))  # buys when the path rises, sells when it falls
        max_q = max(1, cfg["max_trade_value"] // ref)
        done = {"spot": 0, "perp": 0}
        if cfg["spot"]:
            maker.requote(db, m, now)
            tgt = max(1, INVENTORY_VALUE // ref)
            for i in range(_poisson(lam, rng)):
                inv = sum(balances(db, bot.id).get(m.symbol, (0, 0)))
                bias = lean + (0.25 if inv < tgt * 0.4 else -0.25 if inv > tgt * 1.6 else 0)
                side = "buy" if rng.random() < bias else "sell"
                try:
                    r = engine.place(db, bot, m, side, "market", rng.randint(1, max_q), None, f"auto:{m.id}:{int(now.timestamp())}:{i}", now)
                    done["spot"] += sum(f["qty"] for f in r["fills"])
                except ExchangeError:
                    db.rollback()
        pm = db.scalar(select(PerpMarket).where(PerpMarket.ex_market_id == m.id))
        if cfg["perps"] and pm is not None and pm.status == "active":
            perps.requote(db, pm, now)
            limit = max(1, PERP_POSITION_VALUE // ref)
            for i in range(_poisson(lam, rng)):
                pos = db.scalar(select(PerpPosition.size).where(PerpPosition.account_id == bot.id, PerpPosition.market_id == pm.id)) or 0
                bias = lean + (0.3 if pos < -limit * 0.7 else -0.3 if pos > limit * 0.7 else 0)
                side = "buy" if rng.random() < bias else "sell"
                q = rng.randint(1, max_q)
                if (side == "buy" and pos + q > limit) or (side == "sell" and pos - q < -limit):
                    side = "sell" if side == "buy" else "buy"
                try:
                    r = perps.place(db, bot, pm, side, "market", q, None, PERP_LEVERAGE, f"auto:p{pm.id}:{int(now.timestamp())}:{i}", now=now)
                    done["perp"] += sum(f["qty"] for f in r["fills"])
                except ExchangeError:
                    db.rollback()
        out[m.symbol] = {"path_bps": round(p["off"]), **done}
    return {"enabled": True, "markets": out}


# --- the owner's view ---------------------------------------------------------------------------------------------------------


def status(db: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    cfg = config(db)
    st = _state(db)
    bot = system(db, "auto")
    bal = balances(db, bot.id)
    since = now - timedelta(hours=1)
    spot_n = db.scalar(select(func.count()).select_from(ExTrade).where(ExTrade.at >= since,
                                                                       or_(ExTrade.buyer_account_id == bot.id, ExTrade.seller_account_id == bot.id))) or 0
    perp_n = db.scalar(select(func.count()).select_from(PerpTrade).where(PerpTrade.at >= since,
                                                                         or_(PerpTrade.buyer_account_id == bot.id, PerpTrade.seller_account_id == bot.id))) or 0
    pms = {p.ex_market_id: p for p in db.scalars(select(PerpMarket))}
    markets = []
    for m in db.scalars(select(ExMarket).order_by(ExMarket.sort)):
        off = (st.get("paths", {}).get(m.symbol) or {}).get("off", 0.0)
        pm = pms.get(m.id)
        pos = db.scalar(select(PerpPosition.size).where(PerpPosition.account_id == bot.id, PerpPosition.market_id == pm.id)) if pm else 0
        markets.append({"symbol": m.symbol, "reference": m.ref_contract_cents, "ref_status": m.ref_status, "path_bps": round(off),
                        "center": center(db, m.symbol, m.ref_contract_cents or 0), "last": m.last_price_cents,
                        "inventory": sum(bal.get(m.symbol, (0, 0))), "perp_position": int(pos or 0)})
    return {"config": cfg, "limits": LIMITS, "last_tick": st.get("at"), "tick_seconds": TICK_SECONDS, "usdt": sum(bal.get(USDT, (0, 0))),
            "trades_last_hour": {"spot": spot_n, "perps": perp_n}, "markets": markets}
