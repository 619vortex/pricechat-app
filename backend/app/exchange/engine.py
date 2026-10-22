"""The matching engine: a price-time-priority central limit order book per market. PAPER TRADING only.

Orders: limit (rests when not fully filled) or market (IOC: fills what it can now, the rest is cancelled; it never fills
beyond MARKET_SLIPPAGE_PCT past the best opposite price at arrival). Partial fills. Cancel. Whole contracts.

Collateral is reserved BEFORE matching (no trade without collateral or inventory, no double spending):
  buy   holds price x qty + the taker fee at that price (paper USDT cents); a fill uses fill price x qty + its fee
  sell  holds the contracts (sell only what you own: no shorting, no leverage, no margin)
The unused part of a hold goes back when the order completes or is cancelled. Fees round DOWN to the cent, so a hold
always covers every fill it can have.

Priority: best price first, then the earliest order (id). A fill happens at the RESTING (maker) order's price.
Self-trade prevention: a taker never trades with its own resting order; that resting order is cancelled instead.
Settlement: one balanced journal per fill (buyer pays notional + fee, seller gets notional - fee, fees to the fees
account, contracts from seller to buyer), the trade row, average-cost P&L per position.

Concurrency: one market at a time per process (a lock per market), and the first statement of every matching
transaction bumps the market's `seq` (SQLite: takes the database write lock; PostgreSQL: locks the market row), so two
transactions can never match the same book at once. Idempotency: (account, client_id) is unique; a retry returns the
first order.
"""

import threading
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.exchange import specs
from app.exchange.ledger import USDT, ExchangeError, Leg, balances, hold, post, release, system
from app.models.exchange import ExAccount, ExMarket, ExOrder, ExPosition, ExTrade

_locks: dict[int, threading.RLock] = defaultdict(threading.RLock)
_guard = threading.Lock()


def market_lock(market_id: int) -> threading.RLock:
    with _guard:
        return _locks[market_id]


def fee(notional: int, bps: int) -> int:
    return notional * bps // 10_000  # rounds down: holds always cover fees


def _hold_for_buy(price: int, qty: int, m: ExMarket) -> int:
    n = price * qty
    return n + fee(n, max(m.taker_fee_bps, m.maker_fee_bps))


def best(db: Session, market_id: int, side: str) -> int | None:
    """Best resting price on a side ("buy" = best bid, "sell" = best ask)."""
    q = select(ExOrder.price_cents).where(ExOrder.market_id == market_id, ExOrder.status == "open", ExOrder.side == side)
    q = q.order_by(ExOrder.price_cents.desc() if side == "buy" else ExOrder.price_cents.asc()).limit(1)
    return db.scalar(q)


def _position(db: Session, account_id: int, market_id: int) -> ExPosition:
    p = db.scalar(select(ExPosition).where(ExPosition.account_id == account_id, ExPosition.market_id == market_id))
    if p is None:
        p = ExPosition(account_id=account_id, market_id=market_id, cost_cents=0, realized_cents=0)
        db.add(p)
        db.flush()
    return p


def _contracts(db: Session, account_id: int, symbol: str) -> int:
    return sum(balances(db, account_id).get(symbol, (0, 0)))


def _finish(db: Session, o: ExOrder, m: ExMarket, status: str, reason: str | None, now: datetime) -> None:
    """Close an order and give back what's left of its hold."""
    if o.held:
        release(db, o.account_id, USDT if o.side == "buy" else m.symbol, o.held)
        o.held = 0
    o.status, o.reason, o.updated_at = status, reason, now
    db.flush()


def _fill(db: Session, m: ExMarket, taker: ExOrder, maker: ExOrder, qty: int, now: datetime) -> ExTrade:
    price = maker.price_cents
    notional = price * qty
    buy, sell = (taker, maker) if taker.side == "buy" else (maker, taker)
    buy_fee = fee(notional, m.taker_fee_bps if buy is taker else m.maker_fee_bps)
    sell_fee = fee(notional, m.taker_fee_bps if sell is taker else m.maker_fee_bps)
    # realized P&L for the seller (average cost, fees included), computed before the contracts move
    sp = _position(db, sell.account_id, m.id)
    held_before = _contracts(db, sell.account_id, m.symbol)
    cost_out = sp.cost_cents * qty // held_before if held_before else 0
    realized = notional - sell_fee - cost_out
    t = ExTrade(market_id=m.id, price_cents=price, qty=qty, buy_order_id=buy.id, sell_order_id=sell.id, buyer_account_id=buy.account_id,
                seller_account_id=sell.account_id, taker_side=taker.side, buyer_fee_cents=buy_fee, seller_fee_cents=sell_fee,
                seller_realized_cents=realized, at=now)
    db.add(t)
    db.flush()
    fees_acct = system(db, "fees").id
    post(db, "trade", f"trade:{t.id}", f"{m.symbol} {qty} @ {price / 100:.2f}", [
        Leg(buy.account_id, USDT, -(notional + buy_fee), from_held=notional + buy_fee),
        Leg(sell.account_id, USDT, notional - sell_fee),
        Leg(fees_acct, USDT, buy_fee + sell_fee),
        Leg(sell.account_id, m.symbol, -qty, from_held=qty),
        Leg(buy.account_id, m.symbol, qty),
    ], now=now)
    buy.held -= notional + buy_fee
    sell.held -= qty
    for o in (buy, sell):
        o.filled += qty
        o.updated_at = now
    bp = _position(db, buy.account_id, m.id)
    bp.cost_cents += notional + buy_fee
    sp.cost_cents -= cost_out
    sp.realized_cents += realized
    m.last_price_cents = price
    if maker.filled >= maker.qty:
        _finish(db, maker, m, "filled", None, now)
    db.flush()  # autoflush is off: the next book query must see this fill
    return t


def place(db: Session, account: ExAccount, m: ExMarket, side: str, type_: str, qty: int, price_cents: int | None, client_id: str,
          now: datetime | None = None) -> dict[str, Any]:
    """Place one order and match it. Returns the order and its fills. Raises ExchangeError (nothing changed) if refused."""
    now = now or utcnow()
    if side not in ("buy", "sell") or type_ not in ("limit", "market"):
        raise ExchangeError("Unknown order type.")
    if not client_id or len(client_id) > 40:
        raise ExchangeError("Missing order id.")
    with market_lock(m.id):
        db.commit()  # a fresh transaction whose FIRST statement is the write lock below
        db.execute(update(ExMarket).where(ExMarket.id == m.id).values(seq=ExMarket.seq + 1))
        db.refresh(m)
        dup = db.scalar(select(ExOrder).where(ExOrder.account_id == account.id, ExOrder.client_id == client_id))
        if dup is not None:
            db.commit()
            return {"order": order_out(dup, m), "fills": [], "duplicate": True}
        try:
            out = _place_locked(db, account, m, side, type_, qty, price_cents, client_id, now)
        except ExchangeError:
            db.rollback()
            raise
        db.commit()
        return out


def _place_locked(db: Session, account: ExAccount, m: ExMarket, side: str, type_: str, qty: int, price_cents: int | None,
                  client_id: str, now: datetime) -> dict[str, Any]:
    if m.status != "active":
        raise ExchangeError("This market is paused by the exchange.")
    if m.ref_status == "insufficient" or not m.ref_contract_cents:
        raise ExchangeError("Trading is halted: there isn't enough reliable price evidence for this card right now.")
    if not isinstance(qty, int) or not m.min_qty <= qty <= m.max_qty:
        raise ExchangeError(f"Quantity must be a whole number from {m.min_qty} to {m.max_qty}.")
    ref = m.ref_contract_cents
    if type_ == "limit":
        if price_cents is None or price_cents <= 0 or price_cents % m.tick_cents:
            raise ExchangeError("Set a valid limit price.")
        band = ref * specs.LIMIT_BAND_PCT // 100
        if not ref - band <= price_cents <= ref + band:
            raise ExchangeError(f"Limit price must be within {specs.LIMIT_BAND_PCT}% of the reference ({(ref - band) / 100:,.2f} – {(ref + band) / 100:,.2f}).")
        limit = price_cents
    else:
        opp = best(db, m.id, "sell" if side == "buy" else "buy")
        if opp is None:
            raise ExchangeError("No " + ("sellers" if side == "buy" else "buyers") + " right now. Place a limit order instead.")
        limit = opp * (100 + specs.MARKET_SLIPPAGE_PCT) // 100 if side == "buy" else max(1, opp * (100 - specs.MARKET_SLIPPAGE_PCT) // 100)

    need = _hold_for_buy(limit, qty, m) if side == "buy" else qty
    asset = USDT if side == "buy" else m.symbol
    have = balances(db, account.id).get(asset, (0, 0))[0]
    if have < need:  # checked before anything is written (the guarded hold below is the second line of defence)
        raise ExchangeError(f"Not enough paper USDT: this order needs {need / 100:,.2f} including the fee." if side == "buy"
                            else f"You can sell up to {have} {m.symbol} (you can only sell contracts you own).")
    o = ExOrder(market_id=m.id, account_id=account.id, client_id=client_id, side=side, type=type_, price_cents=limit, qty=qty, filled=0,
                status="open", held=0, created_at=now, updated_at=now)
    db.add(o)
    db.flush()
    hold(db, account.id, asset, need)
    o.held = need

    fills: list[ExTrade] = []
    opp_side = "sell" if side == "buy" else "buy"
    while o.filled < o.qty:
        q = select(ExOrder).where(ExOrder.market_id == m.id, ExOrder.status == "open", ExOrder.side == opp_side, ExOrder.id != o.id)
        q = q.where(ExOrder.price_cents <= limit) if side == "buy" else q.where(ExOrder.price_cents >= limit)
        q = q.order_by(ExOrder.price_cents.asc() if side == "buy" else ExOrder.price_cents.desc(), ExOrder.id.asc()).limit(1)
        maker = db.scalar(q)
        if maker is None:
            break
        if maker.account_id == account.id:  # self-trade prevention: the resting order is cancelled, never traded against
            _finish(db, maker, m, "cancelled", "self-trade prevention", now)
            db.flush()
            continue
        fills.append(_fill(db, m, o, maker, min(o.qty - o.filled, maker.qty - maker.filled), now))

    if o.filled >= o.qty:
        _finish(db, o, m, "filled", None, now)
    elif type_ == "market":
        _finish(db, o, m, "cancelled", f"filled {o.filled} of {o.qty}; the rest found no liquidity within the price protection" if o.filled
                else "no liquidity within the price protection", now)
    elif side == "buy":  # a resting buy keeps exactly what its remainder needs
        extra = o.held - _hold_for_buy(limit, o.qty - o.filled, m)
        if extra > 0:
            release(db, account.id, USDT, extra)
            o.held -= extra
    db.flush()
    return {"order": order_out(o, m), "fills": [trade_out(t) for t in fills], "duplicate": False}


def cancel(db: Session, account: ExAccount, order_id: int, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    o = db.get(ExOrder, order_id)
    if o is None or o.account_id != account.id:
        raise ExchangeError("That order isn't yours.")
    m = db.get(ExMarket, o.market_id)
    assert m is not None
    with market_lock(m.id):
        db.commit()
        db.execute(update(ExMarket).where(ExMarket.id == m.id).values(seq=ExMarket.seq + 1))
        db.refresh(o)
        if o.status != "open":
            db.commit()
            raise ExchangeError("That order is no longer open.")
        _finish(db, o, m, "cancelled", "cancelled by you", now)
        db.commit()
    return {"order": order_out(o, m)}


def cancel_all(db: Session, account_id: int, m: ExMarket, reason: str, now: datetime | None = None) -> int:
    """Cancel every open order of one account in one market (the market maker's re-quote; admin resets). Caller holds the lock."""
    now = now or utcnow()
    n = 0
    for o in db.scalars(select(ExOrder).where(ExOrder.market_id == m.id, ExOrder.account_id == account_id, ExOrder.status == "open")):
        _finish(db, o, m, "cancelled", reason, now)
        n += 1
    return n


def order_out(o: ExOrder, m: ExMarket) -> dict[str, Any]:
    return {"id": o.id, "market": m.symbol, "side": o.side, "type": o.type, "price": o.price_cents, "qty": o.qty, "filled": o.filled,
            "status": o.status, "reason": o.reason, "client_id": o.client_id, "created_at": o.created_at.isoformat()}


def trade_out(t: ExTrade) -> dict[str, Any]:
    return {"id": t.id, "price": t.price_cents, "qty": t.qty, "side": t.taker_side, "at": t.at.isoformat()}
