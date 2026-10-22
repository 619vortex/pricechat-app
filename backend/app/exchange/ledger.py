"""Paper accounts and the double-entry ledger.

Every change of what an account OWNS is a journal: entries that sum to zero per asset (paper USDT comes from the
treasury and returns to it; contracts move between accounts; fees go to the fees account). Holds (reserving balance for
an open order) move value between `available` and `held` of the same account and are not ownership changes, so they
have no entries. Invariant, checked by reconcile(): for every account and asset, available + held == sum of entries.

Balances only change through guarded UPDATEs that refuse to go below zero (no negative balances, no double spending
under concurrency). Journals are unique by `ref`, so a retried posting is a no-op.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.exchange import specs
from app.models.exchange import ExAccount, ExBalance, ExEntry, ExJournal, ExMarket
from app.models.members import Member
from app.models.pokemon import PokemonCard

USDT = "USDT"


class ExchangeError(ValueError):
    """A refused action, with a plain-language message for the trader."""


@dataclass
class Leg:
    account_id: int
    asset: str
    delta: int  # change of what the account owns (available + held)
    from_held: int = 0  # how much of a negative delta comes out of `held` (the rest out of `available`)


def _balance(db: Session, account_id: int, asset: str) -> ExBalance:
    b = db.scalar(select(ExBalance).where(ExBalance.account_id == account_id, ExBalance.asset == asset))
    if b is None:
        b = ExBalance(account_id=account_id, asset=asset, available=0, held=0)
        db.add(b)
        db.flush()
    return b


def _move(db: Session, account_id: int, asset: str, d_available: int, d_held: int, allow_negative: bool = False) -> None:
    _balance(db, account_id, asset)
    stmt = update(ExBalance).where(ExBalance.account_id == account_id, ExBalance.asset == asset)
    if not allow_negative:
        stmt = stmt.where(ExBalance.available + d_available >= 0, ExBalance.held + d_held >= 0)
    res = db.execute(stmt.values(available=ExBalance.available + d_available, held=ExBalance.held + d_held))
    if res.rowcount != 1:
        raise ExchangeError("Not enough balance for that." if asset == USDT else "You don't have enough contracts for that.")


def hold(db: Session, account_id: int, asset: str, amount: int) -> None:
    """Reserve `amount` for an order (available -> held)."""
    if amount > 0:
        _move(db, account_id, asset, -amount, amount)


def release(db: Session, account_id: int, asset: str, amount: int) -> None:
    """Give back an unused reservation (held -> available)."""
    if amount > 0:
        _move(db, account_id, asset, amount, -amount)


def post(db: Session, kind: str, ref: str, memo: str, legs: list[Leg], now: datetime | None = None, treasury_may_go_negative: bool = False) -> ExJournal | None:
    """One balanced journal. Returns None if a journal with this ref already exists (idempotent retry)."""
    per_asset: dict[str, int] = defaultdict(int)
    for leg in legs:
        per_asset[leg.asset] += leg.delta
    if any(v != 0 for v in per_asset.values()):
        raise RuntimeError(f"unbalanced journal {ref}: {dict(per_asset)}")  # a programming error, never a user one
    if db.scalar(select(ExJournal.id).where(ExJournal.ref == ref)) is not None:
        return None
    j = ExJournal(kind=kind, ref=ref[:64], memo=memo[:200], at=now or utcnow())
    db.add(j)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return None
    treasury = system(db, "treasury").id  # the issuer of paper USDT and contracts: may sit below zero
    clearing = system(db, "clearing").id  # carries open perp P&L until positions close: may sit below zero
    for leg in legs:
        db.add(ExEntry(journal_id=j.id, account_id=leg.account_id, asset=leg.asset, delta=leg.delta))
        if leg.delta >= 0:
            _move(db, leg.account_id, leg.asset, leg.delta, 0, allow_negative=leg.account_id in (treasury, clearing))
        else:
            out_held = min(leg.from_held, -leg.delta)
            _move(db, leg.account_id, leg.asset, leg.delta + out_held, -out_held,
                  allow_negative=(treasury_may_go_negative and leg.account_id == treasury) or leg.account_id == clearing)
    db.flush()
    return j


# --- accounts ------------------------------------------------------------------------------------------------------------

SYSTEM = {"treasury": "Paper Treasury", "fees": "Exchange Fees", "maker": "SIMULATED MARKET MAKER", "auto": "SIMULATED AUTO-TRADER",
          "escrow": "Perp Margin Escrow", "clearing": "Perp Clearing", "insurance": "Perp Insurance Fund"}


def system(db: Session, kind: str) -> ExAccount:
    a = db.scalar(select(ExAccount).where(ExAccount.kind == kind, ExAccount.member_id.is_(None)))
    if a is None:
        a = ExAccount(kind=kind, member_id=None, name=SYSTEM[kind], created_at=utcnow())
        db.add(a)
        db.flush()
    return a


def account_for(db: Session, member: Member, create: bool = True) -> ExAccount | None:
    """The member's paper account; created on first use with START_USDT_CENTS from the treasury (one journal, once)."""
    a = db.scalar(select(ExAccount).where(ExAccount.member_id == member.id))
    if a is None and create:
        a = ExAccount(kind="user", member_id=member.id, name=member.username[:40], created_at=utcnow())
        db.add(a)
        db.flush()
        t = system(db, "treasury")
        post(db, "fund", f"fund:{a.id}", "Paper wallet: 10,000 paper USDT (no real value)",
             [Leg(t.id, USDT, -specs.START_USDT_CENTS), Leg(a.id, USDT, specs.START_USDT_CENTS)], treasury_may_go_negative=True)
        db.commit()
    return a


def resolve_card(db: Session, s: specs.Spec) -> PokemonCard | None:
    return db.scalar(select(PokemonCard).where(PokemonCard.name == s.name, PokemonCard.set_name == s.set_name, PokemonCard.edition == s.edition,
                                               PokemonCard.card_number == s.number, PokemonCard.language == s.language).order_by(PokemonCard.id).limit(1))


def ensure_markets(db: Session) -> list[ExMarket]:
    """Create the 10 markets (idempotent) from the specs, each tied to its exact catalogue card."""
    have = {m.symbol: m for m in db.scalars(select(ExMarket))}
    for i, s in enumerate(specs.MARKETS):
        if s.symbol in have:
            continue
        card = resolve_card(db, s)
        if card is None:
            continue  # not in this catalogue (tests, fresh installs): never guessed
        m = ExMarket(symbol=s.symbol, card_id=card.id, grading_company=s.grading_company, grade=s.grade, title=s.title,
                     multiplier_den=specs.MULTIPLIER_DEN, tick_cents=specs.TICK_CENTS, min_qty=specs.MIN_QTY, max_qty=specs.MAX_QTY,
                     taker_fee_bps=specs.TAKER_FEE_BPS, maker_fee_bps=specs.MAKER_FEE_BPS, status="active", sort=i, ref_status="insufficient", seq=0)
        db.add(m)
        have[s.symbol] = m
    system(db, "treasury"), system(db, "fees"), system(db, "maker")
    db.commit()
    return sorted(have.values(), key=lambda m: m.sort)


def balances(db: Session, account_id: int) -> dict[str, tuple[int, int]]:
    """Fresh from the database (columns, not cached objects: balances change through guarded UPDATEs)."""
    rows = db.execute(select(ExBalance.asset, ExBalance.available, ExBalance.held).where(ExBalance.account_id == account_id))
    return {a: (int(av), int(h)) for a, av, h in rows}


def reconcile(db: Session) -> dict[str, Any]:
    """Every journal sums to zero per asset, and every balance equals its entries. Returns any problems found."""
    bad_journals = db.execute(select(ExEntry.journal_id, ExEntry.asset, func.sum(ExEntry.delta)).group_by(ExEntry.journal_id, ExEntry.asset)
                              .having(func.sum(ExEntry.delta) != 0)).all()
    sums = {(a, s): v for a, s, v in db.execute(select(ExEntry.account_id, ExEntry.asset, func.sum(ExEntry.delta)).group_by(ExEntry.account_id, ExEntry.asset))}
    mismatches = []
    treasury = system(db, "treasury").id
    for acct, asset, av, h in db.execute(select(ExBalance.account_id, ExBalance.asset, ExBalance.available, ExBalance.held)):
        if av + h != sums.get((acct, asset), 0) or (av < 0 and acct not in (treasury, system(db, "clearing").id)) or h < 0:
            mismatches.append({"account": acct, "asset": asset, "balance": av + h, "ledger": sums.get((acct, asset), 0)})
    totals = {s: v for s, v in db.execute(select(ExEntry.asset, func.sum(ExEntry.delta)).group_by(ExEntry.asset))}
    return {"ok": not bad_journals and not mismatches and all(v == 0 for v in totals.values()),
            "unbalanced_journals": [list(r) for r in bad_journals], "balance_mismatches": mismatches, "asset_totals": totals}


def detach_member(db: Session, member_id: int) -> None:
    """Before a member is deleted: their open orders are cancelled (holds released) and their paper account is kept,
    anonymous, so the double-entry history (and every counterparty's) stays whole. Never ranked again."""
    from sqlalchemy import update as _update

    from app.exchange.engine import cancel_all, market_lock

    a = db.scalar(select(ExAccount).where(ExAccount.member_id == member_id))
    if a is None:
        return
    for m in db.scalars(select(ExMarket)):
        with market_lock(m.id):
            db.commit()
            db.execute(_update(ExMarket).where(ExMarket.id == m.id).values(seq=ExMarket.seq + 1))
            cancel_all(db, a.id, m, "member account deleted")
            db.commit()
    a.member_id, a.name = None, "former member"
    db.commit()
