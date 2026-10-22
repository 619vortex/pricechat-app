"""What the terminal shows: tickers, book depth, trades, candles (from real paper trades only), accounts, P&L, boards.

Marks: an account's equity values contracts at the market's REFERENCE price (real-card data), not at the paper price,
so nobody can pump a thin paper book to inflate their equity or their rank. P&L uses average cost with fees included.
"""

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.exchange import specs
from app.exchange.ledger import USDT, balances, system
from app.models.exchange import ExAccount, ExEquitySnapshot, ExMarket, ExOracleSnapshot, ExOrder, ExPosition, ExTrade
from app.models.pokemon import PokemonCard

INTERVALS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14_400, "1D": 86_400, "1W": 604_800}
MAX_CANDLES = 300
MIN_TRADES_TO_RANK = 3


def _maker_id(db: Session) -> int:
    return system(db, "maker").id


def _stats_24h(db: Session, m: ExMarket, now: datetime) -> dict[str, Any]:
    since = now - timedelta(hours=24)
    row = db.execute(select(func.count(), func.coalesce(func.sum(ExTrade.qty), 0), func.coalesce(func.sum(ExTrade.qty * ExTrade.price_cents), 0),
                            func.max(ExTrade.price_cents), func.min(ExTrade.price_cents))
                     .where(ExTrade.market_id == m.id, ExTrade.at >= since)).one()
    open_px = db.scalar(select(ExTrade.price_cents).where(ExTrade.market_id == m.id, ExTrade.at < since).order_by(ExTrade.at.desc(), ExTrade.id.desc()).limit(1))
    if open_px is None:
        open_px = db.scalar(select(ExTrade.price_cents).where(ExTrade.market_id == m.id, ExTrade.at >= since).order_by(ExTrade.at, ExTrade.id).limit(1))
    last = m.last_price_cents
    return {"trades": row[0], "volume": int(row[1]), "turnover": int(row[2]), "high": row[3], "low": row[4],
            "change_pct": round((last - open_px) / open_px * 100, 2) if last and open_px else None}


def market_out(db: Session, m: ExMarket, now: datetime | None = None, card: PokemonCard | None = None) -> dict[str, Any]:
    now = now or utcnow()
    from app.members.packs import picture

    card = card or db.get(PokemonCard, m.card_id)
    ref, last = m.ref_contract_cents, m.last_price_cents
    bid, ask = _best(db, m.id, "buy"), _best(db, m.id, "sell")
    return {"symbol": m.symbol, "title": m.title, "card": {"id": card.id, "name": card.name, "set": card.set_name, "number": card.card_number,
                                                            "edition": card.edition, "year": card.year, "image": picture(card)} if card else None,
            "grade": f"{m.grading_company} {m.grade}", "auto": _auto_on(db), "status": m.status, "tradable": m.status == "active" and m.ref_status != "insufficient" and bool(ref),
            "reference": {"contract": ref, "card": ref * m.multiplier_den if ref else None, "status": m.ref_status, "confidence": m.ref_confidence,
                          "at": m.ref_at.isoformat() if m.ref_at else None},
            "last": last, "bid": bid, "ask": ask,
            "premium_pct": round((last - ref) / ref * 100, 2) if last and ref else None,
            "rules": {"multiplier": f"1/{m.multiplier_den} of the card's reference value", "tick": m.tick_cents, "min_qty": m.min_qty, "max_qty": m.max_qty,
                      "taker_fee_bps": m.taker_fee_bps, "maker_fee_bps": m.maker_fee_bps, "limit_band_pct": specs.LIMIT_BAND_PCT,
                      "market_slippage_pct": specs.MARKET_SLIPPAGE_PCT},
            "stats_24h": _stats_24h(db, m, now)}


def _best(db: Session, market_id: int, side: str) -> int | None:
    q = select(ExOrder.price_cents).where(ExOrder.market_id == market_id, ExOrder.status == "open", ExOrder.side == side)
    return db.scalar(q.order_by(ExOrder.price_cents.desc() if side == "buy" else ExOrder.price_cents.asc()).limit(1))


def markets(db: Session) -> list[dict[str, Any]]:
    ms = list(db.scalars(select(ExMarket).order_by(ExMarket.sort)))
    cards = {c.id: c for c in db.scalars(select(PokemonCard).where(PokemonCard.id.in_([m.card_id for m in ms] or [0])))}
    now = utcnow()
    return [market_out(db, m, now, cards.get(m.card_id)) for m in ms]


def book(db: Session, m: ExMarket, depth: int = 15) -> dict[str, Any]:
    mk = _maker_id(db)
    out: dict[str, Any] = {}
    for side in ("buy", "sell"):
        rows = db.execute(select(ExOrder.price_cents, func.sum(ExOrder.qty - ExOrder.filled), func.count(),
                                 func.sum(case((ExOrder.account_id == mk, ExOrder.qty - ExOrder.filled), else_=0)))
                          .where(ExOrder.market_id == m.id, ExOrder.status == "open", ExOrder.side == side)
                          .group_by(ExOrder.price_cents)
                          .order_by(ExOrder.price_cents.desc() if side == "buy" else ExOrder.price_cents.asc()).limit(depth)).all()
        out["bids" if side == "buy" else "asks"] = [{"price": p, "qty": int(q), "orders": n, "simulated_qty": int(s or 0)} for p, q, n, s in rows]
    return out


def _auto_on(db: Session) -> bool:
    from app.exchange.auto import active

    return active(db)


def trades(db: Session, m: ExMarket, limit: int = 50) -> list[dict[str, Any]]:
    sim = {_maker_id(db), system(db, "auto").id}
    rows = db.scalars(select(ExTrade).where(ExTrade.market_id == m.id).order_by(ExTrade.at.desc(), ExTrade.id.desc()).limit(min(limit, 200)))
    return [{"id": t.id, "price": t.price_cents, "qty": t.qty, "side": t.taker_side, "at": t.at.isoformat(),
             "simulated": bool(sim & {t.buyer_account_id, t.seller_account_id})} for t in rows]


def candles(db: Session, m: ExMarket, interval: str, now: datetime | None = None) -> dict[str, Any]:
    """OHLCV from executed paper trades only. Buckets without trades are simply absent (no invented candles)."""
    now = now or utcnow()
    step = INTERVALS.get(interval, 3600)
    since = now - timedelta(seconds=step * MAX_CANDLES)
    rows = db.execute(select(ExTrade.at, ExTrade.price_cents, ExTrade.qty).where(ExTrade.market_id == m.id, ExTrade.at >= since)
                      .order_by(ExTrade.at, ExTrade.id).limit(50_000)).all()
    out: list[dict[str, Any]] = []
    for at, px, q in rows:
        ts = int(at.timestamp()) // step * step
        if out and out[-1]["t"] == ts:
            c = out[-1]
            c["h"], c["l"], c["c"], c["v"] = max(c["h"], px), min(c["l"], px), px, c["v"] + q
        else:
            out.append({"t": ts, "o": px, "h": px, "l": px, "c": px, "v": q})
    refs = db.execute(select(ExOracleSnapshot.at, ExOracleSnapshot.contract_cents).where(ExOracleSnapshot.market_id == m.id, ExOracleSnapshot.at >= since,
                                                                                         ExOracleSnapshot.contract_cents.is_not(None))
                      .order_by(ExOracleSnapshot.at)).all()
    ref_line, last = [], None
    for at, c in refs:
        ts = int(at.timestamp()) // step * step
        if ts != last:
            ref_line.append({"t": ts, "v": c})
            last = ts
    return {"interval": interval, "candles": out, "reference": ref_line}


# --- accounts ----------------------------------------------------------------------------------------------------------


def equity(db: Session, account_id: int, marks: dict[str, int] | None = None) -> int:
    """Paper USDT + spot contracts at the reference + perp positions (margin + P&L at the clamped mark)."""
    from app.exchange.perps import equity_in_perps

    marks = marks if marks is not None else _marks(db)
    bal = balances(db, account_id)
    total = sum(bal.get(USDT, (0, 0)))
    for sym, (a, h) in bal.items():
        if sym != USDT:
            total += (a + h) * marks.get(sym, 0)
    return total + equity_in_perps(db, account_id)


def _marks(db: Session) -> dict[str, int]:
    return {m.symbol: (m.ref_contract_cents or m.last_price_cents or 0) for m in db.scalars(select(ExMarket))}


def account_view(db: Session, acct: ExAccount) -> dict[str, Any]:
    ms = {m.id: m for m in db.scalars(select(ExMarket))}
    marks = _marks(db)
    bal = balances(db, acct.id)
    usdt = bal.get(USDT, (0, 0))
    positions = []
    for p in db.scalars(select(ExPosition).where(ExPosition.account_id == acct.id)):
        m = ms.get(p.market_id)
        if m is None:
            continue
        qty = sum(bal.get(m.symbol, (0, 0)))
        if qty == 0 and p.realized_cents == 0:
            continue
        mark = marks.get(m.symbol, 0)
        value = qty * mark
        positions.append({"market": m.symbol, "title": m.title, "qty": qty, "held": bal.get(m.symbol, (0, 0))[1], "avg_cost": p.cost_cents // qty if qty else None,
                          "cost": p.cost_cents, "mark": mark, "last": m.last_price_cents, "value": value, "unrealized": value - p.cost_cents if qty else 0,
                          "realized": p.realized_cents})
    orders = db.scalars(select(ExOrder).where(ExOrder.account_id == acct.id).order_by(ExOrder.created_at.desc(), ExOrder.id.desc()).limit(100)).all()
    from app.exchange.engine import order_out

    hist = db.scalars(select(ExTrade).where(or_(ExTrade.buyer_account_id == acct.id, ExTrade.seller_account_id == acct.id))
                      .order_by(ExTrade.at.desc(), ExTrade.id.desc()).limit(200)).all()
    sym = {m.id: m.symbol for m in ms.values()}
    history = [{"id": t.id, "market": sym.get(t.market_id), "side": "buy" if t.buyer_account_id == acct.id else "sell", "price": t.price_cents, "qty": t.qty,
                "fee": t.buyer_fee_cents if t.buyer_account_id == acct.id else t.seller_fee_cents,
                "realized": t.seller_realized_cents if t.seller_account_id == acct.id else None,
                "maker": (t.taker_side == "sell") == (t.buyer_account_id == acct.id), "at": t.at.isoformat()} for t in hist]
    eq = equity(db, acct.id, marks)
    from app.exchange.perps import account_perps

    perps = account_perps(db, acct)
    return {"perps": perps, "account": {"id": acct.id, "name": acct.name}, "usdt": {"available": usdt[0], "held": usdt[1]}, "equity": eq,
            "start": specs.START_USDT_CENTS, "pnl": eq - specs.START_USDT_CENTS,
            "realized": sum(p["realized"] for p in positions) + sum(p["realized"] + p["funding"] for p in perps["positions"]),
            "unrealized": sum(p["unrealized"] for p in positions) + sum(p["unrealized"] for p in perps["positions"]),
            "positions": positions, "open_orders": [order_out(o, ms[o.market_id]) for o in orders if o.status == "open"],
            "orders": [order_out(o, ms[o.market_id]) for o in orders if o.status != "open"][:50], "history": history,
            "achievements": achievements(db, acct.id, eq, positions, history)}


ACHIEVEMENTS = [
    ("first_trade", "First trade", "Your first paper fill"),
    ("ten_trades", "Active trader", "10 fills"),
    ("hundred_trades", "Tape reader", "100 fills"),
    ("maker", "Liquidity provider", "A resting order of yours was filled"),
    ("diversified", "Collector's index", "Positions in 5 different markets"),
    ("whale", "Whale", "A single fill worth 5,000 paper USDT or more"),
    ("green", "In the green", "Equity 5% above your starting 10,000"),
]


def achievements(db: Session, account_id: int, eq: int, positions: list[dict[str, Any]], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    n = db.scalar(select(func.count()).select_from(ExTrade).where(or_(ExTrade.buyer_account_id == account_id, ExTrade.seller_account_id == account_id))) or 0
    got = {
        "first_trade": n >= 1, "ten_trades": n >= 10, "hundred_trades": n >= 100,
        "maker": any(h["maker"] for h in history),
        "diversified": sum(1 for p in positions if p["qty"] > 0) >= 5,
        "whale": any(h["price"] * h["qty"] >= 5_000_00 for h in history),
        "green": eq >= specs.START_USDT_CENTS * 105 // 100,
    }
    return [{"key": k, "name": nme, "how": how, "earned": got[k]} for k, nme, how in ACHIEVEMENTS]


# --- leaderboards ------------------------------------------------------------------------------------------------------


def snapshot_equity(db: Session, now: datetime | None = None) -> int:
    """Today's equity per member account (reference marks), for the 7- and 30-day returns. Idempotent per day."""
    now = now or utcnow()
    marks = _marks(db)
    n = 0
    for a in db.scalars(select(ExAccount).where(ExAccount.kind == "user")):
        row = db.scalar(select(ExEquitySnapshot).where(ExEquitySnapshot.account_id == a.id, ExEquitySnapshot.day == now.date()))
        e = equity(db, a.id, marks)
        if row is None:
            db.add(ExEquitySnapshot(account_id=a.id, day=now.date(), equity_cents=e))
        else:
            row.equity_cents = e
        n += 1
    db.commit()
    return n


def _base_equity(db: Session, a: ExAccount, day: date) -> int:
    snap = db.scalar(select(ExEquitySnapshot.equity_cents).where(ExEquitySnapshot.account_id == a.id, ExEquitySnapshot.day <= day)
                     .order_by(ExEquitySnapshot.day.desc()).limit(1))
    if snap is not None:
        return snap
    return specs.START_USDT_CENTS  # joined after that day: measured from the starting wallet


def leaderboard(db: Session, board: str = "equity", now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    from app.models.members import Member

    marks = _marks(db)
    counts = dict(db.execute(select(ExAccount.id, func.count(ExTrade.id)).join(ExTrade, or_(ExTrade.buyer_account_id == ExAccount.id,
                                                                                         ExTrade.seller_account_id == ExAccount.id))
                             .where(ExAccount.kind == "user").group_by(ExAccount.id)).all())
    eligible = [a for a in db.scalars(select(ExAccount).where(ExAccount.kind == "user", ExAccount.member_id.is_not(None)))
                if counts.get(a.id, 0) >= MIN_TRADES_TO_RANK]
    names = dict(db.execute(select(Member.id, Member.username).where(Member.id.in_([a.member_id for a in eligible] or [0]))).all())
    rows: list[dict[str, Any]] = []
    if board == "trades":
        top = db.execute(select(ExTrade, ExAccount).join(ExAccount, ExAccount.id == ExTrade.seller_account_id)
                         .where(ExAccount.kind == "user", ExAccount.member_id.is_not(None), ExTrade.seller_realized_cents > 0)
                         .order_by(ExTrade.seller_realized_cents.desc()).limit(20)).all()
        ms = {m.id: m.symbol for m in db.scalars(select(ExMarket))}
        mnames = dict(db.execute(select(Member.id, Member.username).where(Member.id.in_([a.member_id for _, a in top] or [0]))).all())
        rows = [{"username": mnames.get(a.member_id, a.name), "market": ms.get(t.market_id), "qty": t.qty, "price": t.price_cents,
                 "profit": t.seller_realized_cents, "at": t.at.isoformat()} for t, a in top]
    else:
        for a in eligible:
            eq = equity(db, a.id, marks)
            r: dict[str, Any] = {"username": names.get(a.member_id, a.name), "equity": eq, "trades": counts.get(a.id, 0)}
            if board in ("7d", "30d"):
                base = _base_equity(db, a, (now - timedelta(days=7 if board == "7d" else 30)).date())
                r["return_pct"] = round((eq - base) / base * 100, 2) if base else None
            rows.append(r)
        key = "return_pct" if board in ("7d", "30d") else "equity"
        rows.sort(key=lambda r: -(r.get(key) or 0))
        rows = rows[:50]
    for i, r in enumerate(rows):
        r["position"] = i + 1
    return {"board": board, "rows": rows, "rules": RULES}


RULES = [
    "Paper trading only: no real money, prizes or redemption.",
    "Equity = paper USDT (available + held) + every spot contract valued at its market's REAL reference price, not at the paper price, "
    "+ every perpetual position's margin and P&L at the mark (the perp price clamped to within 5% of the real reference), "
    "so a thin paper book can't be pumped to climb the board.",
    "7-day / 30-day return: today's equity against the equity recorded that many days ago (or the starting 10,000 if you joined later).",
    f"You appear after {MIN_TRADES_TO_RANK} fills. One paper account per Kiwi Passport. The simulated market maker is never ranked.",
    "Self-trading is impossible (an order never fills against your own resting order), and every fill pays the normal fee.",
    "Most profitable trades: the largest realized gains on single sells (average cost, fees included).",
]
