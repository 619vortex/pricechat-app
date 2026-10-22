"""The SIMULATED MARKET MAKER: an openly labelled system account that keeps real, resting bid and ask orders around each
market's reference price, so members can trade before there are enough of them. It is not a person and never pretends
to be one; its orders and fills are ordinary book orders and trades, shown as "Simulated market maker".

Rules:
  - funded once from the treasury (a journal): MAKER_USDT paper USDT, plus an inventory per market worth about
    INVENTORY_VALUE at the reference when it was seeded. It can't create money: every quote is held from its own
    balances like anyone's, so it can't buy without paper USDT or sell what it doesn't own.
  - quotes LEVELS bids and asks: the spread SPREAD_BPS around the reference, LEVEL_STEP_BPS between levels, each level
    about LEVEL_NOTIONAL (bounded to MIN_SIZE..MAX_SIZE contracts).
  - inventory skew: holding more than its target, it quotes lower (to sell); less, higher (to buy back). No bids at
    MAX_INVENTORY_X x the target; no asks with nothing left.
  - it re-quotes after trades and every minute, and pulls ALL its quotes when the reference isn't "ok" (stale or
    insufficient evidence) or the market is paused. It never trades at prices away from the reference to "guarantee"
    fills, never prints trades by itself, and never trades with itself.
  - as members bring their own depth near the reference, it quotes one level instead of three.
"""

import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.exchange.engine import _place_locked, cancel_all, market_lock
from app.exchange.ledger import USDT, ExchangeError, Leg, balances, post, system
from app.models.exchange import ExMarket, ExOrder

MAKER_USDT = 1_000_000_00  # 1,000,000 paper USDT
INVENTORY_VALUE = 30_000_00  # per market, at the reference when seeded
SPREAD_BPS = 100  # 1.00% from best bid to best ask
LEVEL_STEP_BPS = 60
LEVELS = 3
LEVEL_NOTIONAL = 1_500_00
MIN_SIZE, MAX_SIZE = 1, 50
MAX_INVENTORY_X = 2
USER_DEPTH_BAND_BPS = 200  # members' own orders within 2% of the reference count as real liquidity


def seed(db: Session, m: ExMarket, now: datetime | None = None) -> None:
    """Fund the maker once (paper USDT) and give it its starting inventory in this market (both from the treasury)."""
    mk, t = system(db, "maker"), system(db, "treasury")
    post(db, "seed", "seed:maker:usdt", "Simulated market maker: paper USDT", [Leg(t.id, USDT, -MAKER_USDT), Leg(mk.id, USDT, MAKER_USDT)],
         now, treasury_may_go_negative=True)
    if m.ref_contract_cents:
        qty = max(MIN_SIZE, INVENTORY_VALUE // m.ref_contract_cents)
        post(db, "seed", f"seed:maker:{m.symbol}", f"Simulated market maker: starting {m.symbol} inventory",
             [Leg(t.id, m.symbol, -qty), Leg(mk.id, m.symbol, qty)], now, treasury_may_go_negative=True)


def target(db: Session, m: ExMarket) -> int:
    from app.models.exchange import ExEntry, ExJournal

    q = db.scalar(select(ExEntry.delta).join(ExJournal, ExJournal.id == ExEntry.journal_id)
                  .where(ExJournal.ref == f"seed:maker:{m.symbol}", ExEntry.account_id == system(db, "maker").id))
    return int(q or 0)


def requote(db: Session, m: ExMarket, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    mk = system(db, "maker")
    with market_lock(m.id):
        db.commit()
        db.execute(update(ExMarket).where(ExMarket.id == m.id).values(seq=ExMarket.seq + 1))
        db.refresh(m)
        seed(db, m, now)
        pulled = cancel_all(db, mk.id, m, "re-quote", now)
        if m.status != "active" or m.ref_status != "ok" or not m.ref_contract_cents:
            db.commit()
            return {"market": m.symbol, "quoting": False, "pulled": pulled, "why": f"reference {m.ref_status}" if m.status == "active" else "paused"}
        from app.exchange.auto import center

        ref = center(db, m.symbol, m.ref_contract_cents)  # the reference, or the owner's simulated path (auto-trader on)
        bal = balances(db, mk.id)
        inv = sum(bal.get(m.symbol, (0, 0)))
        tgt = max(1, target(db, m))
        skew_bps = max(-1.0, min(1.0, (inv - tgt) / tgt)) * SPREAD_BPS / 2  # long -> quote lower
        size = max(MIN_SIZE, min(MAX_SIZE, LEVEL_NOTIONAL // ref))
        band = ref * USER_DEPTH_BAND_BPS // 10_000
        user_depth = db.scalar(select(func.coalesce(func.sum(ExOrder.qty - ExOrder.filled), 0)).where(
            ExOrder.market_id == m.id, ExOrder.status == "open", ExOrder.account_id != mk.id,
            ExOrder.price_cents.between(ref - band, ref + band))) or 0
        levels = 1 if user_depth >= size * 3 else LEVELS
        placed, room_bid = [], max(0, tgt * MAX_INVENTORY_X - inv)
        sellable = bal.get(m.symbol, (0, 0))[0]
        for i in range(levels):
            off = SPREAD_BPS / 2 + i * LEVEL_STEP_BPS
            bid = int(ref * (1 - (off + skew_bps) / 10_000))
            ask = int(ref * (1 + (off - skew_bps) / 10_000)) + 1
            for side, price, qty in (("buy", bid, min(size, room_bid)), ("sell", ask, min(size, sellable))):
                if qty <= 0 or price <= 0:
                    continue
                try:
                    r = _place_locked(db, mk, m, side, "limit", qty, price, f"mm:{m.id}:{m.seq}:{side[0]}{i}", now)
                except ExchangeError:
                    continue  # out of paper USDT or inventory: it simply quotes less
                placed.append((side, price, qty))
                if side == "buy":
                    room_bid -= qty
                else:
                    sellable -= qty
        db.commit()
        return {"market": m.symbol, "quoting": True, "pulled": pulled, "placed": len(placed), "inventory": inv, "target": tgt}


def requote_all(db: Session, now: datetime | None = None) -> dict[str, Any]:
    return {m.symbol: requote(db, m, now) for m in db.scalars(select(ExMarket).order_by(ExMarket.sort))}


# --- housekeeping -----------------------------------------------------------------------------------------------------

PRUNE_AFTER = timedelta(hours=1)
PRUNE_BATCH = 2000


def prune(db: Session, now: datetime | None = None, budget_seconds: float = 4.0) -> int:
    """Delete system accounts' (maker / auto-trader) quotes that were cancelled without a single fill, an hour on.

    Every re-quote cancels and replaces the maker's quotes, so these pile up fast (the auto-trader re-quotes every 15 s)
    and filled the database volume once. They hold nothing (the hold is released on cancel) and no trade points at them
    (filled = 0), so removing them changes no balance, trade, chart or ledger. Members' orders are never touched.
    Small committed batches within a time budget: freed pages are reused by SQLite, so the file stops growing."""
    from app.models.exchange import ExAccount, PerpOrder

    now = now or utcnow()
    cutoff = now - PRUNE_AFTER
    bots = select(ExAccount.id).where(ExAccount.member_id.is_(None)).scalar_subquery()
    started, total = time.monotonic(), 0
    for model in (ExOrder, PerpOrder):
        while time.monotonic() - started < budget_seconds:
            ids = select(model.id).where(model.account_id.in_(bots), model.status == "cancelled", model.filled == 0,
                                         model.updated_at < cutoff).limit(PRUNE_BATCH).scalar_subquery()
            n = db.execute(delete(model).where(model.id.in_(ids))).rowcount or 0
            db.commit()
            total += n
            if n < PRUNE_BATCH:
                break
    return total
