"""Perpetual futures on the 10 Pokémon markets. PAPER TRADING ONLY: no real money, no ownership of any card.

  Long (buy) or short (sell), 1-10x leverage, ISOLATED margin: one position per account and perp, with its own margin.
  Index   = the spot market's real-card reference (the oracle; never moved by paper trades).
  Mark    = the perp's mid price, clamped to within MARK_BAND_PCT of the index: P&L and liquidations use the mark, so a
            thin paper book can't be pushed to liquidate others.
  Funding = every hour, rate = (mark - index) / index / 24, capped at ±FUNDING_CAP_PPM per hour: when the perp trades
            above the index, longs pay shorts (and the reverse), pulling the perp toward the real card's price.
  Margin  = opening margin = price x qty / leverage, moved into the MARGIN ESCROW account (double-entry, paper USDT).
            Maintenance margin MMR_BPS of the notional at mark. Below it, the position is LIQUIDATED: taken over by the
            INSURANCE FUND at the mark price (a recorded liquidation fill); the trader keeps what equity is left minus a
            liquidation fee (to the fund); a loss beyond the margin is covered by the fund. The fund then unwinds what it
            took over, a little at a time, into the order book.
  P&L     = realized P&L settles through the CLEARING account (every long has a short: the system is zero-sum; the
            clearing account carries the open positions' P&L until they close).
  Matching, holds, idempotency, self-trade prevention and locking work exactly like the spot engine (engine.py).
"""

import threading
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.exchange.ledger import USDT, ExchangeError, Leg, balances, hold, post, release, system
from app.models.exchange import ExAccount, ExMarket, PerpFunding, PerpLiquidation, PerpMarket, PerpOrder, PerpPosition, PerpTrade

MAX_LEVERAGE = 10
MMR_BPS = 500  # 5%
MARK_BAND_PCT = 5
FUNDING_EVERY = timedelta(hours=1)
FUNDING_CAP_PPM = 500  # 0.05% per hour
LIQ_FEE_BPS = 100  # 1% of the notional, from what's left of the margin, to the insurance fund
INSURANCE_SEED = 500_000_00
MARKET_SLIPPAGE_PCT = 5
LIMIT_BAND_PCT = 50
MAX_QTY = 1000
# the simulated market maker on perps
MAKER_LEVERAGE = 2
MAKER_POSITION_VALUE = 30_000_00  # max |position| ~ this notional
MAKER_LEVELS, MAKER_SPREAD_BPS, MAKER_STEP_BPS, MAKER_LEVEL_NOTIONAL = 3, 100, 60, 1_500_00
UNWIND_CHUNK = 10  # contracts per minute the insurance fund unwinds into the book

_locks: dict[int, threading.RLock] = defaultdict(threading.RLock)
_guard = threading.Lock()


def _lock(mid: int) -> threading.RLock:
    with _guard:
        return _locks[mid]


def fee(n: int, bps: int) -> int:
    return n * bps // 10_000


def _sign(x: int) -> int:
    return (x > 0) - (x < 0)


# --- setup ----------------------------------------------------------------------------------------------------------------


def ensure_perps(db: Session) -> list[PerpMarket]:
    have = {p.ex_market_id: p for p in db.scalars(select(PerpMarket))}
    for m in db.scalars(select(ExMarket).order_by(ExMarket.sort)):
        if m.id not in have:
            p = PerpMarket(symbol=f"{m.symbol}-PERP", ex_market_id=m.id, max_leverage=MAX_LEVERAGE, mmr_bps=MMR_BPS, taker_fee_bps=10,
                           maker_fee_bps=0, status="active", funding_ppm=0, sort=m.sort, seq=0)
            db.add(p)
            have[m.id] = p
    for k in ("escrow", "clearing", "insurance"):
        system(db, k)
    db.flush()
    t, ins = system(db, "treasury"), system(db, "insurance")
    post(db, "seed", "seed:insurance:usdt", "Perp insurance fund: paper USDT", [Leg(t.id, USDT, -INSURANCE_SEED), Leg(ins.id, USDT, INSURANCE_SEED)],
         treasury_may_go_negative=True)
    from app.exchange.maker import MAKER_USDT

    mk = system(db, "maker")  # the maker quotes perps from the same paper USDT (idempotent: posted once)
    post(db, "seed", "seed:maker:usdt", "Simulated market maker: paper USDT", [Leg(t.id, USDT, -MAKER_USDT), Leg(mk.id, USDT, MAKER_USDT)],
         treasury_may_go_negative=True)
    db.commit()
    return sorted(have.values(), key=lambda p: p.sort)


def _ex(db: Session, pm: PerpMarket) -> ExMarket:
    ex = db.get(ExMarket, pm.ex_market_id)
    assert ex is not None
    return ex


def _best(db: Session, mid: int, side: str) -> int | None:
    q = select(PerpOrder.price_cents).where(PerpOrder.market_id == mid, PerpOrder.status == "open", PerpOrder.side == side)
    return db.scalar(q.order_by(PerpOrder.price_cents.desc() if side == "buy" else PerpOrder.price_cents.asc()).limit(1))


def mark(db: Session, pm: PerpMarket, ex: ExMarket | None = None) -> int | None:
    ex = ex or _ex(db, pm)
    idx = ex.ref_contract_cents
    bid, ask = _best(db, pm.id, "buy"), _best(db, pm.id, "sell")
    mid = (bid + ask) // 2 if bid and ask else pm.last_price_cents or idx
    if not idx:
        return mid
    band = idx * MARK_BAND_PCT // 100
    return max(idx - band, min(idx + band, mid or idx))


def _pos(db: Session, account_id: int, market_id: int) -> PerpPosition:
    p = db.scalar(select(PerpPosition).where(PerpPosition.account_id == account_id, PerpPosition.market_id == market_id))
    if p is None:
        p = PerpPosition(account_id=account_id, market_id=market_id, size=0, entry_cents=0, margin_cents=0, leverage=1, realized_cents=0,
                         funding_cents=0, liquidations=0)
        db.add(p)
        db.flush()
    return p


def liq_price(p: PerpPosition, mmr_bps: int = MMR_BPS) -> int | None:
    """The mark at which this isolated position reaches its maintenance margin."""
    if p.size == 0:
        return None
    n, mmr = abs(p.size), mmr_bps / 10_000
    if p.size > 0:  # margin + (m - e) n = mmr m n
        v = (p.entry_cents * n - p.margin_cents) / (n * (1 - mmr))
    else:  # margin + (e - m) n = mmr m n
        v = (p.entry_cents * n + p.margin_cents) / (n * (1 + mmr))
    return max(0, int(v))


# --- fills ---------------------------------------------------------------------------------------------------------------


def _shortfall(db: Session, pm: PerpMarket, o: PerpOrder, q: int, price: int, d: int, fee_cents: int) -> int:
    """How much paper USDT this side would still miss for the fill (0 = it can fund it). Changes nothing."""
    pos = _pos(db, o.account_id, pm.id)
    s = pos.size
    closing = min(q, abs(s)) if s * d < 0 else 0
    opening = q - closing
    lev = pos.leverage if (s != 0 and closing == 0) else o.leverage
    back = 0
    if closing:
        back = max(0, pos.margin_cents * closing // abs(s) + closing * (price - pos.entry_cents) * _sign(s))
    need = (price * opening // lev if opening else 0) + fee_cents
    return max(0, need - o.held - back - balances(db, o.account_id).get(USDT, (0, 0))[0])


def _settle_side(db: Session, pm: PerpMarket, o: PerpOrder, q: int, price: int, d: int, fee_cents: int, now: datetime,
                 fee_to: int | None = None) -> tuple[list[Leg], int, dict[str, Any]]:
    """The legs and position change for one side of a fill (d = +1 buy, -1 sell). Raises before anything changes if the
    order can't fund its opening margin."""
    pos = _pos(db, o.account_id, pm.id)
    s = pos.size
    closing = min(q, abs(s)) if s * d < 0 else 0
    opening = q - closing
    lev = pos.leverage if (s != 0 and closing == 0) else o.leverage
    realized = closing * (price - pos.entry_cents) * _sign(s) if closing else 0
    released = pos.margin_cents * closing // abs(s) if closing else 0
    open_margin = price * opening // lev if opening else 0
    need = open_margin + fee_cents
    from_held = min(o.held, need)  # the rest comes from available (checked by _shortfall before any side changed)
    esc, clr, ins = system(db, "escrow").id, system(db, "clearing").id, system(db, "insurance").id
    legs: list[Leg] = []
    covered = 0
    if closing:
        back = released + realized
        legs += [Leg(esc, USDT, -released), Leg(clr, USDT, -realized)]
        if back >= 0:
            legs.append(Leg(o.account_id, USDT, back))
        else:  # the loss is bigger than this margin: the insurance fund covers the rest
            legs.append(Leg(ins, USDT, back))
            covered = -back
    if open_margin:
        legs += [Leg(o.account_id, USDT, -open_margin, from_held=min(from_held, open_margin)), Leg(esc, USDT, open_margin)]
    if fee_cents:
        legs += [Leg(o.account_id, USDT, -fee_cents, from_held=max(0, from_held - open_margin)), Leg(fee_to or system(db, "fees").id, USDT, fee_cents)]
    o.held -= from_held
    # the position
    if closing:
        pos.margin_cents -= released
        pos.realized_cents += realized
        pos.size += d * closing
        if pos.size == 0:
            pos.entry_cents, pos.margin_cents = 0, pos.margin_cents if pos.margin_cents > 0 else 0
    if opening:
        if pos.size == 0:
            pos.entry_cents, pos.leverage = price, lev
        else:
            pos.entry_cents = (pos.entry_cents * abs(pos.size) + price * opening) // (abs(pos.size) + opening)
        pos.size += d * opening
        pos.margin_cents += open_margin
    pos.updated_at = now
    return legs, realized, {"covered": covered, "released": released}


def _fill(db: Session, pm: PerpMarket, taker: PerpOrder, maker: PerpOrder, q: int, now: datetime, kind: str = "trade",
          liq_fee_side: str | None = None, liq_fee: int = 0) -> PerpTrade:
    price = maker.price_cents
    n = price * q
    buy, sell = (taker, maker) if taker.side == "buy" else (maker, taker)
    ins = system(db, "insurance").id
    bf = liq_fee if liq_fee_side == "buy" else (0 if kind == "liquidation" else fee(n, pm.taker_fee_bps if buy is taker else pm.maker_fee_bps))
    sf = liq_fee if liq_fee_side == "sell" else (0 if kind == "liquidation" else fee(n, pm.taker_fee_bps if sell is taker else pm.maker_fee_bps))
    bl, br, _ = _settle_side(db, pm, buy, q, price, +1, bf, now, fee_to=ins if liq_fee_side == "buy" else None)
    sl, sr, _ = _settle_side(db, pm, sell, q, price, -1, sf, now, fee_to=ins if liq_fee_side == "sell" else None)
    t = PerpTrade(market_id=pm.id, price_cents=price, qty=q, buyer_account_id=buy.account_id, seller_account_id=sell.account_id, taker_side=taker.side,
                  buyer_fee_cents=bf, seller_fee_cents=sf, buyer_realized_cents=br, seller_realized_cents=sr, kind=kind, at=now)
    db.add(t)
    db.flush()
    post(db, "perp", f"perp:{t.id}", f"{pm.symbol} {q} @ {price / 100:.2f}", bl + sl, now)
    for o in (buy, sell):
        o.filled += q
        o.updated_at = now
    pm.last_price_cents = price
    if maker.filled >= maker.qty:
        _finish(db, maker, "filled", None, now)
    db.flush()
    return t


def _finish(db: Session, o: PerpOrder, status: str, reason: str | None, now: datetime) -> None:
    if o.held:
        release(db, o.account_id, USDT, o.held)
        o.held = 0
    o.status, o.reason, o.updated_at = status, reason, now
    db.flush()


# --- orders ----------------------------------------------------------------------------------------------------------------


def place(db: Session, account: ExAccount, pm: PerpMarket, side: str, type_: str, qty: int, price_cents: int | None, leverage: int, client_id: str,
          reduce_only: bool = False, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    if side not in ("buy", "sell") or type_ not in ("limit", "market"):
        raise ExchangeError("Unknown order type.")
    if not client_id or len(client_id) > 40:
        raise ExchangeError("Missing order id.")
    with _lock(pm.id):
        db.commit()
        db.execute(update(PerpMarket).where(PerpMarket.id == pm.id).values(seq=PerpMarket.seq + 1))
        db.refresh(pm)
        dup = db.scalar(select(PerpOrder).where(PerpOrder.account_id == account.id, PerpOrder.client_id == client_id))
        if dup is not None:
            db.commit()
            return {"order": order_out(dup, pm), "fills": [], "duplicate": True}
        try:
            out = _place_locked(db, account, pm, side, type_, qty, price_cents, leverage, client_id, reduce_only, now)
            liquidate(db, pm, now)
        except ExchangeError:
            db.rollback()
            raise
        db.commit()
        return out


def _place_locked(db: Session, account: ExAccount, pm: PerpMarket, side: str, type_: str, qty: int, price_cents: int | None, leverage: int,
                  client_id: str, reduce_only: bool, now: datetime) -> dict[str, Any]:
    ex = _ex(db, pm)
    if pm.status != "active" or ex.status != "active":
        raise ExchangeError("This market is paused by the exchange.")
    idx = ex.ref_contract_cents
    if not idx:
        raise ExchangeError("Trading is halted: no reliable reference price for this card.")
    if ex.ref_status == "insufficient" and not reduce_only:
        raise ExchangeError("Only closing orders right now: there isn't enough reliable price evidence for this card.")
    if not 1 <= qty <= MAX_QTY:
        raise ExchangeError(f"Quantity must be a whole number from 1 to {MAX_QTY}.")
    if not 1 <= leverage <= pm.max_leverage:
        raise ExchangeError(f"Leverage is 1x to {pm.max_leverage}x.")
    pos = _pos(db, account.id, pm.id)
    d = 1 if side == "buy" else -1
    closable = abs(pos.size) if pos.size * d < 0 else 0
    if reduce_only:
        if closable == 0:
            raise ExchangeError("Nothing to close on that side.")
        qty = min(qty, closable)
    if type_ == "limit":
        band = idx * LIMIT_BAND_PCT // 100
        if price_cents is None or not idx - band <= price_cents <= idx + band:
            raise ExchangeError(f"Limit price must be within {LIMIT_BAND_PCT}% of the index ({(idx - band) / 100:,.2f} – {(idx + band) / 100:,.2f}).")
        limit = price_cents
    else:
        opp = _best(db, pm.id, "sell" if side == "buy" else "buy")
        if opp is None:
            raise ExchangeError("No " + ("sellers" if side == "buy" else "buyers") + " right now. Place a limit order instead.")
        limit = opp * (100 + MARKET_SLIPPAGE_PCT) // 100 if side == "buy" else max(1, opp * (100 - MARKET_SLIPPAGE_PCT) // 100)
    # reserve the margin for the part that opens (or adds to) a position, and the fee for all of it
    worst = limit if side == "buy" else max(limit, _best(db, pm.id, "buy") or limit)
    opening = max(0, qty - closable)
    lev = pos.leverage if pos.size * d > 0 else leverage
    need = worst * opening // lev + fee(worst * qty, max(pm.taker_fee_bps, pm.maker_fee_bps))
    have = balances(db, account.id).get(USDT, (0, 0))[0]
    if have < need:
        raise ExchangeError(f"Not enough paper USDT: this order needs {need / 100:,.2f} of margin and fees at {lev}x.")
    o = PerpOrder(market_id=pm.id, account_id=account.id, client_id=client_id, side=side, type=type_, price_cents=limit, qty=qty, filled=0,
                  leverage=lev, reduce_only=reduce_only, status="open", held=0, created_at=now, updated_at=now)
    db.add(o)
    db.flush()
    hold(db, account.id, USDT, need)
    o.held = need
    fills: list[PerpTrade] = []
    opp_side = "sell" if side == "buy" else "buy"
    while o.filled < o.qty:
        q = select(PerpOrder).where(PerpOrder.market_id == pm.id, PerpOrder.status == "open", PerpOrder.side == opp_side, PerpOrder.id != o.id)
        q = q.where(PerpOrder.price_cents <= limit) if side == "buy" else q.where(PerpOrder.price_cents >= limit)
        mk = db.scalar(q.order_by(PerpOrder.price_cents.asc() if side == "buy" else PerpOrder.price_cents.desc(), PerpOrder.id.asc()).limit(1))
        if mk is None:
            break
        if mk.account_id == account.id:
            _finish(db, mk, "cancelled", "self-trade prevention", now)
            continue
        q_fill = min(o.qty - o.filled, mk.qty - mk.filled)
        n = mk.price_cents * q_fill
        if _shortfall(db, pm, mk, q_fill, mk.price_cents, -d, fee(n, pm.maker_fee_bps)):
            _finish(db, mk, "cancelled", "not enough margin any more", now)  # a resting order that can't fund itself goes
            continue
        if _shortfall(db, pm, o, q_fill, mk.price_cents, d, fee(n, pm.taker_fee_bps)):
            break  # the taker can't fund more: the rest is cancelled (market) or rests (limit, with its hold)
        fills.append(_fill(db, pm, o, mk, q_fill, now))
    if o.filled >= o.qty:
        _finish(db, o, "filled", None, now)
    elif type_ == "market":
        _finish(db, o, "cancelled", f"filled {o.filled} of {o.qty}; the rest found no liquidity within the price protection" if o.filled
                else "no liquidity within the price protection", now)
    db.flush()
    return {"order": order_out(o, pm), "fills": [{"price": t.price_cents, "qty": t.qty} for t in fills], "duplicate": False}


def cancel(db: Session, account: ExAccount, order_id: int, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    o = db.get(PerpOrder, order_id)
    if o is None or o.account_id != account.id:
        raise ExchangeError("That order isn't yours.")
    pm = db.get(PerpMarket, o.market_id)
    assert pm is not None
    with _lock(pm.id):
        db.commit()
        db.execute(update(PerpMarket).where(PerpMarket.id == pm.id).values(seq=PerpMarket.seq + 1))
        db.refresh(o)
        if o.status != "open":
            db.commit()
            raise ExchangeError("That order is no longer open.")
        _finish(db, o, "cancelled", "cancelled by you", now)
        db.commit()
    return {"order": order_out(o, pm)}


def close_position(db: Session, account: ExAccount, pm: PerpMarket, client_id: str) -> dict[str, Any]:
    """Close the whole position at market (a reduce-only market order)."""
    pos = db.scalar(select(PerpPosition).where(PerpPosition.account_id == account.id, PerpPosition.market_id == pm.id))
    if pos is None or pos.size == 0:
        raise ExchangeError("No open position.")
    return place(db, account, pm, "sell" if pos.size > 0 else "buy", "market", abs(pos.size), None, pos.leverage, client_id, reduce_only=True)


def _cancel_all(db: Session, account_id: int, pm: PerpMarket, reason: str, now: datetime) -> int:
    n = 0
    for o in db.scalars(select(PerpOrder).where(PerpOrder.market_id == pm.id, PerpOrder.account_id == account_id, PerpOrder.status == "open")):
        _finish(db, o, "cancelled", reason, now)
        n += 1
    return n


# --- liquidation, funding, insurance unwind ---------------------------------------------------------------------------------


def liquidate(db: Session, pm: PerpMarket, now: datetime | None = None) -> list[dict[str, Any]]:
    """Every position below its maintenance margin at the mark is taken over by the insurance fund at the mark.
    The caller holds the market lock and commits."""
    now = now or utcnow()
    ex = _ex(db, pm)
    mk = mark(db, pm, ex)
    if not mk:
        return []
    ins = system(db, "insurance")
    out = []
    for p in db.scalars(select(PerpPosition).where(PerpPosition.market_id == pm.id, PerpPosition.size != 0, PerpPosition.account_id != ins.id)).all():
        n = abs(p.size)
        equity = p.margin_cents + (mk - p.entry_cents) * p.size
        if equity >= mk * n * pm.mmr_bps // 10_000:
            continue
        _cancel_all(db, p.account_id, pm, "liquidation", now)  # its open orders go first (their holds come back)
        liq_fee = max(0, min(fee(mk * n, LIQ_FEE_BPS), equity))
        pos_before, margin_before = p.size, p.margin_cents
        t = _takeover(db, pm, p, mk, now, liq_fee, "liquidation")
        p.liquidations += 1
        db.add(PerpLiquidation(account_id=p.account_id, market_id=pm.id, at=now, size=pos_before, mark_cents=mk,
                               margin_lost_cents=margin_before - max(0, equity - liq_fee), insurance_cents=max(0, -equity)))
        out.append({"account": p.account_id, "size": pos_before, "mark": mk, "trade": t.id})
    db.flush()
    return out


def _takeover(db: Session, pm: PerpMarket, p: PerpPosition, mk: int, now: datetime, liq_fee: int, kind: str) -> PerpTrade:
    """The insurance fund takes over a whole position at the mark (a recorded fill; the fund posts full 1x margin)."""
    ins = system(db, "insurance")
    n = abs(p.size)
    side = "sell" if p.size > 0 else "buy"
    tag = f"{kind[:4]}:{p.id}:{int(now.timestamp() * 1000)}"
    user_o = PerpOrder(market_id=pm.id, account_id=p.account_id, client_id=tag[:40], side=side, type="market", price_cents=mk, qty=n, filled=0,
                       leverage=p.leverage, reduce_only=True, status="open", held=0, reason=kind, created_at=now, updated_at=now)
    ins_o = PerpOrder(market_id=pm.id, account_id=ins.id, client_id=(tag + "i")[:40], side="buy" if side == "sell" else "sell", type="limit",
                      price_cents=mk, qty=n, filled=0, leverage=1, reduce_only=False, status="open", held=0, reason=f"takes over ({kind})",
                      created_at=now, updated_at=now)
    db.add_all([user_o, ins_o])
    db.flush()
    hold(db, ins.id, USDT, mk * n)
    ins_o.held = mk * n
    t = _fill(db, pm, user_o, ins_o, n, now, kind="liquidation", liq_fee_side=side, liq_fee=liq_fee)
    _finish(db, user_o, "filled", kind, now)
    _finish(db, ins_o, "filled", f"takes over ({kind})", now)
    return t


def force_close(db: Session, account_id: int, reason: str, now: datetime | None = None) -> int:
    """Admin reset / account removal: cancel the account's perp orders and hand every position to the insurance fund at
    the mark, with no fee. Returns how many positions were closed."""
    now = now or utcnow()
    n = 0
    for pm in db.scalars(select(PerpMarket)).all():
        with _lock(pm.id):
            db.commit()
            db.execute(update(PerpMarket).where(PerpMarket.id == pm.id).values(seq=PerpMarket.seq + 1))
            _cancel_all(db, account_id, pm, reason, now)
            p = db.scalar(select(PerpPosition).where(PerpPosition.account_id == account_id, PerpPosition.market_id == pm.id))
            mk = mark(db, pm)
            if p is not None and p.size != 0 and mk:
                _takeover(db, pm, p, mk, now, 0, reason)
                n += 1
            db.commit()
    return n


def fund(db: Session, pm: PerpMarket, now: datetime | None = None, force: bool = False) -> dict[str, Any] | None:
    """Hourly funding between longs and shorts through the clearing account. Returns None when it isn't time yet."""
    now = now or utcnow()
    if not force and pm.last_funding_at and now - pm.last_funding_at < FUNDING_EVERY:
        return None
    with _lock(pm.id):
        db.commit()
        db.execute(update(PerpMarket).where(PerpMarket.id == pm.id).values(seq=PerpMarket.seq + 1))
        db.refresh(pm)
        ex = _ex(db, pm)
        idx, mk = ex.ref_contract_cents, mark(db, pm, ex)
        if not idx or not mk or ex.ref_status == "insufficient":
            pm.last_funding_at = now
            db.commit()
            return {"symbol": pm.symbol, "rate_ppm": 0, "skipped": "no reliable index"}
        rate = max(-FUNDING_CAP_PPM, min(FUNDING_CAP_PPM, round((mk - idx) / idx * 1_000_000 / 24)))
        esc, clr = system(db, "escrow").id, system(db, "clearing").id
        legs: list[Leg] = []
        paid = 0
        for p in db.scalars(select(PerpPosition).where(PerpPosition.market_id == pm.id, PerpPosition.size != 0)).all():
            amt = abs(p.size) * idx * abs(rate) // 1_000_000
            if not amt:
                continue
            pays = (p.size > 0) == (rate > 0)  # longs pay when the perp is above the index; shorts when below
            if pays:
                amt = min(amt, p.margin_cents)
                legs += [Leg(esc, USDT, -amt), Leg(clr, USDT, amt)]
                p.margin_cents -= amt
                p.funding_cents -= amt
                paid += amt
            else:
                legs += [Leg(clr, USDT, -amt), Leg(esc, USDT, amt)]
                p.margin_cents += amt
                p.funding_cents += amt
        if legs:
            post(db, "funding", f"funding:{pm.id}:{int(now.timestamp())}", f"{pm.symbol} funding {rate / 10_000:.4f}%", legs, now)
        db.add(PerpFunding(market_id=pm.id, at=now, rate_ppm=rate, index_cents=idx, mark_cents=mk, paid_cents=paid))
        pm.funding_ppm, pm.last_funding_at = rate, now
        liquidate(db, pm, now)
        db.commit()
        return {"symbol": pm.symbol, "rate_ppm": rate, "paid": paid}


def unwind_insurance(db: Session, pm: PerpMarket) -> dict[str, Any] | None:
    """The insurance fund reduces what it took over, UNWIND_CHUNK contracts a minute, into the book (reduce-only IOC)."""
    ins = system(db, "insurance")
    p = db.scalar(select(PerpPosition).where(PerpPosition.account_id == ins.id, PerpPosition.market_id == pm.id))
    if p is None or p.size == 0:
        return None
    try:
        r = place(db, ins, pm, "sell" if p.size > 0 else "buy", "market", min(UNWIND_CHUNK, abs(p.size)), None, 1,
                  f"unwind:{pm.id}:{int(utcnow().timestamp())}", reduce_only=True)
        return {"symbol": pm.symbol, "filled": sum(f["qty"] for f in r["fills"])}
    except ExchangeError:
        return None


# --- the simulated market maker on perps -------------------------------------------------------------------------------------


def requote(db: Session, pm: PerpMarket, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    mk_acct = system(db, "maker")
    with _lock(pm.id):
        db.commit()
        db.execute(update(PerpMarket).where(PerpMarket.id == pm.id).values(seq=PerpMarket.seq + 1))
        db.refresh(pm)
        pulled = _cancel_all(db, mk_acct.id, pm, "re-quote", now)
        ex = _ex(db, pm)
        idx = ex.ref_contract_cents
        if pm.status != "active" or ex.status != "active" or ex.ref_status != "ok" or not idx:
            db.commit()
            return {"market": pm.symbol, "quoting": False, "pulled": pulled}
        from app.exchange.auto import center

        idx = center(db, ex.symbol, idx, "perps")  # quotes centre on the index, or the simulated path (auto-trader on)
        pos = _pos(db, mk_acct.id, pm.id).size
        limit = max(1, MAKER_POSITION_VALUE // idx)
        skew_bps = max(-1.0, min(1.0, pos / limit)) * MAKER_SPREAD_BPS / 2
        size = max(1, min(50, MAKER_LEVEL_NOTIONAL // idx))
        room_long, room_short = max(0, limit - pos), max(0, limit + pos)
        placed = 0
        for i in range(MAKER_LEVELS):
            off = MAKER_SPREAD_BPS / 2 + i * MAKER_STEP_BPS
            for side, price, room in (("buy", int(idx * (1 - (off + skew_bps) / 10_000)), room_long),
                                      ("sell", int(idx * (1 + (off - skew_bps) / 10_000)) + 1, room_short)):
                qty = min(size, room)
                if qty <= 0:
                    continue
                try:
                    _place_locked(db, mk_acct, pm, side, "limit", qty, price, MAKER_LEVERAGE, f"pmm:{pm.id}:{pm.seq}:{side[0]}{i}", False, now)
                except ExchangeError:
                    continue
                placed += 1
                if side == "buy":
                    room_long -= qty
                else:
                    room_short -= qty
        liquidate(db, pm, now)
        db.commit()
        return {"market": pm.symbol, "quoting": True, "pulled": pulled, "placed": placed, "position": pos}


def tick(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """The minute job: funding when due, the maker's quotes, the insurance fund's unwind, a liquidation sweep."""
    now = now or utcnow()
    out: dict[str, Any] = {}
    for pm in ensure_perps(db):
        f = fund(db, pm, now)
        q = requote(db, pm, now)
        u = unwind_insurance(db, pm)
        out[pm.symbol] = {"funding": f, "placed": q.get("placed"), "unwound": u}
    return out


# --- views ------------------------------------------------------------------------------------------------------------------


def order_out(o: PerpOrder, pm: PerpMarket) -> dict[str, Any]:
    return {"id": o.id, "market": pm.symbol, "side": o.side, "type": o.type, "price": o.price_cents, "qty": o.qty, "filled": o.filled, "leverage": o.leverage,
            "reduce_only": o.reduce_only, "status": o.status, "reason": o.reason, "created_at": o.created_at.isoformat()}


def market_out(db: Session, pm: PerpMarket, now: datetime | None = None) -> dict[str, Any]:
    from app.exchange import views

    now = now or utcnow()
    ex = _ex(db, pm)
    spot = views.market_out(db, ex, now)
    mk, idx = mark(db, pm, ex), ex.ref_contract_cents
    since = now - timedelta(hours=24)
    row = db.execute(select(func.count(), func.coalesce(func.sum(PerpTrade.qty), 0), func.max(PerpTrade.price_cents), func.min(PerpTrade.price_cents))
                     .where(PerpTrade.market_id == pm.id, PerpTrade.at >= since)).one()
    first = db.scalar(select(PerpTrade.price_cents).where(PerpTrade.market_id == pm.id, PerpTrade.at < since).order_by(PerpTrade.at.desc()).limit(1)) \
        or db.scalar(select(PerpTrade.price_cents).where(PerpTrade.market_id == pm.id, PerpTrade.at >= since).order_by(PerpTrade.at).limit(1))
    oi = db.scalar(select(func.coalesce(func.sum(PerpPosition.size), 0)).where(PerpPosition.market_id == pm.id, PerpPosition.size > 0)) or 0
    predicted = max(-FUNDING_CAP_PPM, min(FUNDING_CAP_PPM, round((mk - idx) / idx * 1_000_000 / 24))) if mk and idx else 0
    nxt = (pm.last_funding_at + FUNDING_EVERY) if pm.last_funding_at else now
    return {"symbol": pm.symbol, "spot": ex.symbol, "title": ex.title, "card": spot["card"], "grade": spot["grade"],
            "tradable": spot["tradable"], "auto": spot["auto"], "reference": spot["reference"], "index": idx, "mark": mk, "last": pm.last_price_cents,
            "bid": _best(db, pm.id, "buy"), "ask": _best(db, pm.id, "sell"),
            "premium_pct": round((mk - idx) / idx * 100, 2) if mk and idx else None,
            "funding": {"last_ppm": pm.funding_ppm, "predicted_ppm": predicted, "next_at": nxt.isoformat(), "every_minutes": 60, "cap_ppm": FUNDING_CAP_PPM},
            "open_interest": int(oi), "max_leverage": pm.max_leverage, "mmr_bps": pm.mmr_bps, "taker_fee_bps": pm.taker_fee_bps, "maker_fee_bps": pm.maker_fee_bps,
            "stats_24h": {"trades": row[0], "volume": int(row[1]), "high": row[2], "low": row[3],
                          "change_pct": round((pm.last_price_cents - first) / first * 100, 2) if pm.last_price_cents and first else None}}


def markets(db: Session) -> list[dict[str, Any]]:
    now = utcnow()
    return [market_out(db, pm, now) for pm in db.scalars(select(PerpMarket).order_by(PerpMarket.sort))]


def book(db: Session, pm: PerpMarket, depth: int = 15) -> dict[str, Any]:
    from sqlalchemy import case

    mk = system(db, "maker").id
    out: dict[str, Any] = {}
    for side in ("buy", "sell"):
        rows = db.execute(select(PerpOrder.price_cents, func.sum(PerpOrder.qty - PerpOrder.filled), func.count(),
                                 func.sum(case((PerpOrder.account_id == mk, PerpOrder.qty - PerpOrder.filled), else_=0)))
                          .where(PerpOrder.market_id == pm.id, PerpOrder.status == "open", PerpOrder.side == side).group_by(PerpOrder.price_cents)
                          .order_by(PerpOrder.price_cents.desc() if side == "buy" else PerpOrder.price_cents.asc()).limit(depth)).all()
        out["bids" if side == "buy" else "asks"] = [{"price": p, "qty": int(q), "orders": n, "simulated_qty": int(s or 0)} for p, q, n, s in rows]
    return out


def trades(db: Session, pm: PerpMarket, limit: int = 50) -> list[dict[str, Any]]:
    sysids = {system(db, "maker").id, system(db, "insurance").id, system(db, "auto").id}
    rows = db.scalars(select(PerpTrade).where(PerpTrade.market_id == pm.id).order_by(PerpTrade.at.desc(), PerpTrade.id.desc()).limit(min(limit, 200)))
    return [{"id": t.id, "price": t.price_cents, "qty": t.qty, "side": t.taker_side, "at": t.at.isoformat(), "kind": t.kind,
             "simulated": bool(sysids & {t.buyer_account_id, t.seller_account_id})} for t in rows]


def candles(db: Session, pm: PerpMarket, interval: str, now: datetime | None = None) -> dict[str, Any]:
    from app.exchange.views import INTERVALS, MAX_CANDLES

    now = now or utcnow()
    step = INTERVALS.get(interval, 3600)
    since = now - timedelta(seconds=step * MAX_CANDLES)
    rows = db.execute(select(PerpTrade.at, PerpTrade.price_cents, PerpTrade.qty).where(PerpTrade.market_id == pm.id, PerpTrade.at >= since)
                      .order_by(PerpTrade.at, PerpTrade.id).limit(50_000)).all()
    out: list[dict[str, Any]] = []
    for at, px, q in rows:
        ts = int(at.timestamp()) // step * step
        if out and out[-1]["t"] == ts:
            c = out[-1]
            c["h"], c["l"], c["c"], c["v"] = max(c["h"], px), min(c["l"], px), px, c["v"] + q
        else:
            out.append({"t": ts, "o": px, "h": px, "l": px, "c": px, "v": q})
    from app.exchange.views import candles as spot_candles

    return {"interval": interval, "candles": out, "reference": spot_candles(db, _ex(db, pm), interval, now)["reference"]}


def account_perps(db: Session, acct: ExAccount) -> dict[str, Any]:
    pms = {p.id: p for p in db.scalars(select(PerpMarket))}
    positions = []
    for p in db.scalars(select(PerpPosition).where(PerpPosition.account_id == acct.id)):
        pm = pms.get(p.market_id)
        if pm is None or (p.size == 0 and p.realized_cents == 0 and p.funding_cents == 0):
            continue
        mk = mark(db, pm) or 0
        upnl = (mk - p.entry_cents) * p.size if p.size else 0
        positions.append({"market": pm.symbol, "side": "long" if p.size > 0 else "short" if p.size < 0 else "flat", "size": p.size, "entry": p.entry_cents,
                          "mark": mk, "liq_price": liq_price(p, pm.mmr_bps), "margin": p.margin_cents, "leverage": p.leverage, "unrealized": upnl,
                          "roe_pct": round(upnl / p.margin_cents * 100, 2) if p.margin_cents and p.size else None, "realized": p.realized_cents,
                          "funding": p.funding_cents, "liquidations": p.liquidations, "value": p.margin_cents + upnl if p.size else 0})
    orders = db.scalars(select(PerpOrder).where(PerpOrder.account_id == acct.id).order_by(PerpOrder.created_at.desc(), PerpOrder.id.desc()).limit(100)).all()
    hist = db.scalars(select(PerpTrade).where(or_(PerpTrade.buyer_account_id == acct.id, PerpTrade.seller_account_id == acct.id))
                      .order_by(PerpTrade.at.desc(), PerpTrade.id.desc()).limit(200)).all()
    return {"positions": positions,
            "open_orders": [order_out(o, pms[o.market_id]) for o in orders if o.status == "open"],
            "orders": [order_out(o, pms[o.market_id]) for o in orders if o.status != "open"][:50],
            "history": [{"id": t.id, "market": pms[t.market_id].symbol, "side": "buy" if t.buyer_account_id == acct.id else "sell", "price": t.price_cents,
                         "qty": t.qty, "kind": t.kind, "fee": t.buyer_fee_cents if t.buyer_account_id == acct.id else t.seller_fee_cents,
                         "realized": t.buyer_realized_cents if t.buyer_account_id == acct.id else t.seller_realized_cents, "at": t.at.isoformat()} for t in hist],
            "equity_in_perps": sum(p["value"] for p in positions)}


def equity_in_perps(db: Session, account_id: int) -> int:
    total = 0
    for p in db.scalars(select(PerpPosition).where(PerpPosition.account_id == account_id, PerpPosition.size != 0)):
        pm = db.get(PerpMarket, p.market_id)
        mk = mark(db, pm) if pm else 0
        total += p.margin_cents + ((mk or 0) - p.entry_cents) * p.size
    return total


def check_invariants(db: Session) -> dict[str, Any]:
    """Margin escrow == every position's margin; long and short sizes match per market."""
    esc = sum(balances(db, system(db, "escrow").id).get(USDT, (0, 0)))
    margins = db.scalar(select(func.coalesce(func.sum(PerpPosition.margin_cents), 0))) or 0
    unbalanced = [pm.symbol for pm in db.scalars(select(PerpMarket))
                  if (db.scalar(select(func.coalesce(func.sum(PerpPosition.size), 0)).where(PerpPosition.market_id == pm.id)) or 0) != 0]
    return {"ok": esc == margins and not unbalanced, "escrow": esc, "margins": int(margins), "unbalanced_markets": unbalanced}
