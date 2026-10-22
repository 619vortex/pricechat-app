"""Exchange administration (owner only): overview, pause / resume, oracle and market-maker runs, demo-balance resets.
Every action is written to the exchange audit log."""

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.exchange import maker, oracle, specs
from app.exchange.engine import cancel_all, market_lock
from app.exchange.ledger import USDT, ExchangeError, Leg, balances, post, reconcile, system
from app.models.exchange import ExAccount, ExAudit, ExMarket, ExOracleSnapshot, ExPosition


def audit(db: Session, actor: str, action: str, target: str = "", details: dict[str, Any] | None = None) -> None:
    db.add(ExAudit(actor=actor, action=action, target=target[:80], details=details or {}, at=utcnow()))


def overview(db: Session) -> dict[str, Any]:
    mk = system(db, "maker")
    mb = balances(db, mk.id)
    out = []
    for m in db.scalars(select(ExMarket).order_by(ExMarket.sort)):
        snap = db.scalar(select(ExOracleSnapshot).where(ExOracleSnapshot.market_id == m.id).order_by(ExOracleSnapshot.at.desc()).limit(1))
        out.append({"symbol": m.symbol, "title": m.title, "status": m.status, "reference": m.ref_contract_cents, "ref_status": m.ref_status,
                    "confidence": m.ref_confidence, "last": m.last_price_cents,
                    "maker_inventory": sum(mb.get(m.symbol, (0, 0))), "maker_target": maker.target(db, m),
                    "oracle": {"at": snap.at.isoformat(), "review": snap.review, "raw": snap.raw_cents, "card": snap.card_cents,
                               "provenance": snap.provenance} if snap else None})
    audits = db.scalars(select(ExAudit).order_by(ExAudit.at.desc()).limit(50)).all()
    from app.exchange import perps

    return {"markets": out, "maker_usdt": mb.get(USDT, (0, 0)), "reconcile": reconcile(db), "perps": perps.check_invariants(db),
            "insurance_usdt": balances(db, system(db, "insurance").id).get(USDT, (0, 0)),
            "audit": [{"actor": a.actor, "action": a.action, "target": a.target, "details": a.details, "at": a.at.isoformat()} for a in audits]}


def set_status(db: Session, symbol: str, status: str, actor: str) -> dict[str, Any]:
    m = db.scalar(select(ExMarket).where(ExMarket.symbol == symbol))
    if m is None or status not in ("active", "paused"):
        raise ExchangeError("Unknown market or status.")
    m.status = status
    audit(db, actor, f"market.{status}", symbol)
    db.commit()
    maker.requote(db, m)  # pulls the maker's quotes when paused
    return {"symbol": symbol, "status": status}


def reset_account(db: Session, account_id: int, actor: str, now: datetime | None = None) -> dict[str, Any]:
    """Reset a member's demo balances: cancel open orders, everything back to the treasury, a fresh 10,000 paper USDT.
    One balanced journal, audited."""
    now = now or utcnow()
    a = db.get(ExAccount, account_id)
    if a is None or a.kind != "user":
        raise ExchangeError("Unknown member account.")
    for m in db.scalars(select(ExMarket)):
        with market_lock(m.id):
            db.commit()
            db.execute(update(ExMarket).where(ExMarket.id == m.id).values(seq=ExMarket.seq + 1))
            cancel_all(db, a.id, m, "account reset by the exchange", now)
            db.commit()
    from app.exchange import perps

    perps.force_close(db, a.id, "account reset", now)  # perp positions go to the insurance fund at the mark first
    t = system(db, "treasury")
    legs: list[Leg] = []
    for asset, (avail, held) in balances(db, a.id).items():
        if avail + held:
            legs += [Leg(a.id, asset, -(avail + held)), Leg(t.id, asset, avail + held)]
    legs += [Leg(t.id, USDT, -specs.START_USDT_CENTS), Leg(a.id, USDT, specs.START_USDT_CENTS)]
    n = len(db.scalars(select(ExAudit.id).where(ExAudit.action == "account.reset", ExAudit.target == str(a.id))).all())
    post(db, "reset", f"reset:{a.id}:{n + 1}", "Demo balances reset by the exchange", legs, now, treasury_may_go_negative=True)
    db.execute(update(ExPosition).where(ExPosition.account_id == a.id).values(cost_cents=0, realized_cents=0))
    audit(db, actor, "account.reset", str(a.id), {"name": a.name})
    db.commit()
    return {"account": a.id, "usdt": specs.START_USDT_CENTS}


def run_oracle(db: Session, actor: str) -> dict[str, Any]:
    out = oracle.refresh_all(db)
    audit(db, actor, "oracle.refresh", "all", out)
    db.commit()
    return out
