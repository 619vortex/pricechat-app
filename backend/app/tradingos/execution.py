"""Trade Execution Service + Position Reconciliation: previews, confirmations, entries, exchange-side protection, the
emergency policy, closing, AUTO, and results per mode. Telegram and the website both call these functions.

Safety rules enforced here:
  · nothing trades without a preview the member confirmed (ENTER), or preapproved AUTO limits (and every risk check);
  · one position per account per signal (the duplicate guard), deterministic client order ids (idempotent retries);
  · a fill only counts when the exchange says FILLED, with its size and price;
  · no position stays open without a stop: if the stop can't be placed, the position is closed at market at once and the
    account's new trades are stopped (PROTECTION_FAILED), with an audit record;
  · STOP NEW TRADES never cancels protective orders of open positions;
  · LIVE only for eligible members (Kiwi Rank, verified wallet, Price Chat referral and minimum deposit on Hyperliquid's own
    records; or the owner's allowlist) AND that passed the pre-live checks AND who turned it on themselves;
  · no LIVE entry while Hyperliquid doesn't list the member's API wallet (revoked / expired / unreachable): new entries stop,
    open positions keep their stops.
"""

import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.members import Member
from app.models.trading import TrAccount, TrIntent, TrOrder, TrPosition
from app.models.tradingos import TsSignal, TsSignalAction
from app.tradingos import exchange as xch
from app.tradingos import market, risk, signals

logger = logging.getLogger(__name__)
INTENT_TTL = timedelta(minutes=5)
OPEN_STATES = ("OPENING", "OPEN")


class TradeError(Exception):
    """Shown to the member as is."""


def _aware(d: datetime | None) -> datetime | None:
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- accounts
def account(db: Session, member: Member) -> TrAccount:
    a = db.scalar(select(TrAccount).where(TrAccount.member_id == member.id))
    if a is None:
        from app.tradingbot.service import wallet_of

        w = wallet_of(db, member)
        a = TrAccount(member_id=member.id, mode="PAPER", master_address=w.address if w else None)
        db.add(a)
        db.flush()
    elif a.master_address is None:
        from app.tradingbot.service import wallet_of

        w = wallet_of(db, member)
        a.master_address = w.address if w else None
    return a


FRESH_FOR = timedelta(minutes=10)  # right after a signal is published, ENTER at market is accepted a full zone-width outside the zone


def fresh(sig: TsSignal) -> bool:
    return sig.published_at is not None and utcnow() - (_aware(sig.published_at) or utcnow()) <= FRESH_FOR


DEFAULT_ALLOCATION_PCT = 10.0  # TrAccount.max_allocation_pct's default: unchanged = the member hasn't chosen their own sizing yet


def allowlisted(db: Session, member: Member) -> bool:
    allow = {u.strip().lower() for u in (signals.settings(db).live_allowlist or "").split(",") if u.strip()}
    return member.username.lower() in allow


def eligibility(db: Session, member: Member) -> tuple[bool, str]:
    """Self-service LIVE: open to every member (no rank, referral or minimum deposit since 2026-10-21, the owner's call).
    The only requirement is mechanical: a verified wallet, whose signature approves the member's own API wallet.
    Returns (ok, what's missing)."""
    from app.tradingbot.service import wallet_of

    if wallet_of(db, member) is None:
        return False, "Connect and verify your wallet first."
    return True, ""


def live_allowed(db: Session, member: Member) -> bool:
    """LIVE for eligible members (see eligibility), or anyone the owner allow-listed in Admin → Trading OS."""
    return allowlisted(db, member) or eligibility(db, member)[0]


_agent_seen: dict[tuple[str, str], tuple[float, dict[str, Any] | None]] = {}


def agent_status(acc: TrAccount, max_age: float = 60.0) -> tuple[str, dict[str, Any] | None]:
    """The mainnet API wallet as Hyperliquid sees it: ACTIVE / REVOKED (not listed: revoked, replaced or expired) / NONE /
    UNKNOWN (Hyperliquid unreachable). Cached a minute per account; never involves the key."""
    import time as _t

    if not (acc.mainnet_agent and acc.mainnet_agent_key and acc.mainnet_approved_at and acc.master_address):
        return "NONE", None
    k = (acc.master_address.lower(), acc.mainnet_agent.lower())
    hit = _agent_seen.get(k)
    if hit and _t.monotonic() - hit[0] < max_age:
        return ("ACTIVE" if hit[1] else "REVOKED"), hit[1]
    try:
        rec = xch.agent_record("LIVE", acc.master_address, acc.mainnet_agent)
    except Exception:  # noqa: BLE001
        return "UNKNOWN", None
    _agent_seen[k] = (_t.monotonic(), rec)
    return ("ACTIVE" if rec else "REVOKED"), rec


def check_live_permission(db: Session, member: Member, acc: TrAccount) -> None:
    """Before any LIVE entry: the API wallet must belong to the member's current verified wallet and be listed on Hyperliquid.
    Otherwise new entries stop (closing and existing stop-loss orders keep working)."""
    from app.tradingbot.service import wallet_of

    w = wallet_of(db, member)
    if w is None or not acc.master_address or w.address.lower() != acc.master_address.lower():
        raise TradeError("Your verified wallet changed: enable bot trading again for it. Open positions keep their stop-loss.")
    st, _ = agent_status(acc)
    if st == "UNKNOWN":
        raise TradeError("Couldn't confirm Kiwi's trading permission with Hyperliquid right now. Nothing was sent; try again in a moment.")
    if st != "ACTIVE":
        signals.audit(db, f"m:{member.id}", "agent_not_listed", None, agent=acc.mainnet_agent)
        db.commit()
        raise TradeError("Kiwi's trading permission isn't active on Hyperliquid anymore (revoked or expired). New trades are blocked; "
                         "open positions keep their stop-loss. Enable bot trading again to continue.")


def executor(db: Session, acc: TrAccount, coin: str, mode: str | None = None) -> Any:
    mode = mode or acc.mode
    if mode == "PAPER":
        return xch.PaperExecutor(paper_equity(db, acc), market.data().market(coin).mark)
    key = acc.testnet_agent_key if mode == "TESTNET" else acc.mainnet_agent_key
    if not (key and acc.master_address):
        raise TradeError(f"No approved {mode.lower()} API wallet: connect it on the website first.")
    return xch.HyperliquidExecutor(mode, xch.dec(key), acc.master_address)


def ref_px(pos: TrPosition, ex: Any) -> float:
    """The price to act on: testnet has its own prices (asked from the testnet exchange); paper and live use mainnet's mark."""
    if pos.mode == "TESTNET" and hasattr(ex, "mid_px"):
        try:
            return float(ex.mid_px(pos.coin))
        except Exception:  # noqa: BLE001 - fall back to mainnet's mark
            pass
    return market.data().market(pos.coin).mark


def paper_equity(db: Session, acc: TrAccount) -> float:
    done = db.scalar(select(func.coalesce(func.sum(TrPosition.realized_pnl), 0.0)).where(
        TrPosition.account_id == acc.id, TrPosition.mode == "PAPER", TrPosition.status == "CLOSED")) or 0.0
    return acc.paper_equity + float(done)


def daily_pnl(db: Session, acc: TrAccount) -> float:
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return float(db.scalar(select(func.coalesce(func.sum(TrPosition.realized_pnl), 0.0)).where(
        TrPosition.account_id == acc.id, TrPosition.mode == acc.mode, TrPosition.closed_at >= start)) or 0.0)


STANDARD_TRADES_PER_DAY = 6  # "standard" frequency: at most this many new bot trades a day, no per-coin cooldown
ACTIVE_MIN_COOLDOWN = 15  # minutes: "active" never re-enters a coin sooner than this after a close


def style_limits(acc: TrAccount) -> tuple[int, int]:
    """(new trades allowed per UTC day, per-coin cooldown in minutes) for the member's trading frequency."""
    if acc.frequency == "active":
        return min(max(1, acc.max_trades_per_day), risk.HARD_MAX_TRADES_PER_DAY), max(ACTIVE_MIN_COOLDOWN, acc.cooldown_minutes)
    return STANDARD_TRADES_PER_DAY, 0


def trades_today(db: Session, acc: TrAccount) -> int:
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return int(db.scalar(select(func.count()).select_from(TrPosition).where(
        TrPosition.account_id == acc.id, TrPosition.mode == acc.mode, TrPosition.created_at >= start, TrPosition.status != "FAILED")) or 0)


def cooldown_left(db: Session, acc: TrAccount, coin: str, minutes: int) -> float:
    if minutes <= 0:
        return 0.0
    last = db.scalar(select(func.max(TrPosition.closed_at)).where(TrPosition.account_id == acc.id, TrPosition.coin == coin,
                                                                  TrPosition.status == "CLOSED"))
    if last is None:
        return 0.0
    return max(0.0, (_aware(last) + timedelta(minutes=minutes) - utcnow()).total_seconds() / 60)  # type: ignore[operator]


def intraday_allowed(db: Session, acc: TrAccount, sig: TsSignal) -> str | None:
    """15m (kitchen) signals trade in the member's own mode like every other signal: the PAPER-only gate and the extra opt-in
    were removed on 2026-10-21 (the owner's call). Kept as the one place to block them again."""
    return None


def wants(acc: TrAccount, sig: TsSignal) -> bool:
    """Does the member's timeframe preference include this signal? (Manual setups and pipeline tests always do.)"""
    from app.tradingos import kitchen

    if sig.strategy == kitchen.STRATEGY:
        return acc.timeframe_pref in ("15m", "both")
    if sig.strategy in ("test", "manual"):
        return True
    return acc.timeframe_pref in ("1h", "both")


def open_count(db: Session, acc: TrAccount) -> int:
    return int(db.scalar(select(func.count()).select_from(TrPosition).where(
        TrPosition.account_id == acc.id, TrPosition.status.in_(OPEN_STATES))) or 0)


# ---------------------------------------------------------------- ENTER: preview → confirm
def preview(db: Session, member: Member, sig: TsSignal, ex: Any = None) -> TrIntent:
    """The risk engine's decision for this member and signal, recorded on their Kiwi Brain timeline either way."""
    from app.tradingos import brain

    try:
        it = _preview(db, member, sig, ex)
    except TradeError as e:
        db.rollback()
        title = f"{sig.coin} #{sig.number:03d}: risk check rejected · {str(e)[:180]}"
        from app.models.tradingos import KbEvent

        last = db.scalar(select(KbEvent.title).where(KbEvent.member_id == member.id, KbEvent.signal_id == sig.id, KbEvent.kind == "risk")
                         .order_by(KbEvent.id.desc()).limit(1))
        import re

        def same(a: str | None, b: str) -> bool:  # the same reason with a newer price in it
            return a is not None and re.sub(r"\d[\d.,]*", "#", a) == re.sub(r"\d[\d.,]*", "#", b)

        if not same(last, title):  # AUTO looks again every minute while a signal waits for its zone: one line per new reason
            brain.log(db, "risk", title, member_id=member.id, signal_id=sig.id, coin=sig.coin, source="risk-engine",
                      data={"decision": "rejected", "reason": str(e)[:400]}, commit=True)
        raise
    p = it.plan
    brain.log(db, "risk", f"{sig.coin} #{sig.number:03d}: risk check approved · ${p['notional']:,.2f} at {p['leverage']}x, max loss ${p['max_loss_usd']:,.2f}",
              member_id=member.id, signal_id=sig.id, coin=sig.coin, source="risk-engine",
              data={"decision": "approved", "mode": it.mode, "plan": p}, commit=True)
    return it


def _preview(db: Session, member: Member, sig: TsSignal, ex: Any = None) -> TrIntent:
    """Size the trade for this member (their limits, their equity) and hold it for explicit confirmation."""
    acc = account(db, member)
    plan, _ = _plan(db, member, sig, ex)
    it = TrIntent(account_id=acc.id, signal_id=sig.id, token=secrets.token_hex(8), mode=acc.mode, plan=plan.as_dict(),
                  expires_at=utcnow() + INTENT_TTL)
    db.add(it)
    db.commit()
    return it


def _plan(db: Session, member: Member, sig: TsSignal, ex: Any = None) -> tuple[risk.Plan, Any]:
    """Every check and the sizing at the current mark: (plan, the executor it used). Raises TradeError when it can't enter."""
    if sig.status not in signals.OPEN:
        raise TradeError(f"Signal #{sig.number:03d} is {signals.STATUS_LABEL.get(sig.status, sig.status).lower()}.")
    acc = account(db, member)
    if acc.mode == "LIVE" and not (acc.live_enabled_at and live_allowed(db, member)):
        raise TradeError("LIVE isn't enabled for this account.")  # closing and protection still work; new entries don't
    if acc.mode == "LIVE" and ex is None:  # the real exchange path (tests pass their own fake exchange)
        check_live_permission(db, member, acc)
    blocked = intraday_allowed(db, acc, sig)
    if blocked:
        raise TradeError(blocked)
    m = market.data().market(sig.coin)
    ex = ex or executor(db, acc, sig.coin)
    # Hyperliquid nets one position per coin: never add to, or trade against, a position that's already there
    if db.scalar(select(TrPosition.id).where(TrPosition.account_id == acc.id, TrPosition.coin == sig.coin,
                                             TrPosition.status.in_(OPEN_STATES + ("PROTECTION_FAILED",))).limit(1)):
        raise TradeError(f"You already have a bot position in {sig.coin}.")
    if acc.mode != "PAPER" and any(p["position"]["coin"] == sig.coin and float(p["position"]["szi"]) != 0
                                   for p in ex.state().get("assetPositions", [])):
        raise TradeError(f"You hold a {sig.coin} position on Hyperliquid already. The bot won't touch or add to it.")
    per_day, cooldown = style_limits(acc)
    plan = risk.plan_trade(coin=sig.coin, side=sig.side, mark=m.mark, entry_zone=(sig.entry_lo, sig.entry_hi), stop=sig.stop,
                           target=sig.target, sig_lev_hi=sig.lev_hi, market_max_lev=m.max_leverage, sz_decimals=m.sz_decimals,
                           equity=ex.equity(), max_leverage=acc.max_leverage, max_loss_usd=acc.max_loss_per_trade_usd,
                           max_allocation_pct=acc.max_allocation_pct, max_positions=acc.max_positions, open_positions=open_count(db, acc),
                           daily_pnl=daily_pnl(db, acc), daily_loss_limit=acc.daily_loss_limit_usd,
                           allowed=[c.strip().upper() for c in acc.allowed_coins.split(",") if c.strip()], paused=acc.paused,
                           global_paused=signals.settings(db).paused,
                           min_bump=acc.max_allocation_pct == DEFAULT_ALLOCATION_PCT,  # default limits: the bot sizes up to the minimum
                           zone_tolerance=1.0 if fresh(sig) else 0.25,  # a fresh signal: market entry a little outside the zone is fine
                           trades_today=trades_today(db, acc), max_trades_today=per_day,
                           cooldown_left_min=cooldown_left(db, acc, sig.coin, cooldown))
    if not plan.ok:
        raise TradeError("Can't enter: " + "; ".join(plan.reasons))
    _portfolio_checks(db, acc, sig, m, plan, ex)
    return plan, ex


def _portfolio_checks(db: Session, acc: TrAccount, sig: TsSignal, m: Any, plan: risk.Plan, ex: Any) -> None:
    """Engine v2's portfolio checks on the sized plan (risk.portfolio_checks). Advisory by default: recorded on the plan (and
    so on the member's Kiwi Brain) without changing what trades; with the owner's "enforce" a failed check refuses the entry."""
    s = signals.settings(db)
    cfg = risk.v2_settings(s.strategies)
    if "max_spread_bps" not in ((s.strategies or {}).get("risk_v2") or {}):
        cfg["max_spread_bps"] = s.max_spread_bps  # the scanner's own spread limit unless the owner set one for entries
    rows = db.scalars(select(TrPosition).where(TrPosition.account_id == acc.id, TrPosition.status.in_(OPEN_STATES)))
    opened = [{"side": p.side, "notional": abs(p.size * (p.entry_px or 0)), "margin": abs(p.size * (p.entry_px or 0)) / max(1, p.leverage)}
              for p in rows]
    try:
        levels = market.data().levels(sig.coin, 5)
    except Exception:  # noqa: BLE001 - no book: the slippage check reports "unknown"
        levels = None
    week_pnl = float(db.scalar(select(func.coalesce(func.sum(TrPosition.realized_pnl), 0.0)).where(
        TrPosition.account_id == acc.id, TrPosition.mode == acc.mode, TrPosition.closed_at >= utcnow() - timedelta(days=7))) or 0.0)
    hold = (sig.analysis or {}).get("max_hold_min") if isinstance(sig.analysis, dict) else None
    plan.checks = risk.portfolio_checks(cfg, side=plan.side, entry=plan.entry, stop=plan.stop, notional=plan.notional, margin=plan.margin,
                                        equity=ex.equity(), open_positions=opened, spread_bps=m.spread_bps, levels=levels,
                                        funding_h=m.funding, hold_h=round(hold / 60, 2) if hold else 24.0, week_pnl=week_pnl)
    plan.risk_mode = cfg["mode"]
    failed = [c for c in plan.checks if c["status"] == "fail"]
    if failed and cfg["mode"] == "enforce":
        raise TradeError("Can't enter: " + "; ".join(f"{c['label'].lower()}: {c['detail']}" for c in failed))


def preview_text(sig: TsSignal, it: TrIntent) -> str:
    p = it.plan
    f = market.fmt_price
    return "\n".join([f"{it.mode} · Signal #{sig.number:03d} {sig.coin} {sig.side}", "",
                      f"Entry ≈ {f(p['entry'])} (market)", f"Size {p['size']} {sig.coin} · ${p['notional']:,.2f}",
                      f"Leverage {p['leverage']}x isolated · margin ${p['margin']:,.2f}", f"Stop {f(p['stop'])}",
                      f"TP1 {f(p['tp1'])} (half) · TP2 {f(p['tp2'])}", f"Max planned loss ${p['max_loss_usd']:,.2f} (incl. fees)",
                      *([p["note"]] if p.get("note") else []),
                      *[f"⚠️ {c['label']}: {c['detail']}" for c in p.get("checks") or [] if c["status"] == "fail"], "",
                      "Confirm within 5 minutes." + (" REAL MONEY." if it.mode == "LIVE" else "")])


def confirm(db: Session, member: Member, token: str, ex: Any = None) -> TrPosition:
    it = db.scalar(select(TrIntent).where(TrIntent.token == token))
    acc = account(db, member)
    if it is None or it.account_id != acc.id:
        raise TradeError("That confirmation isn't yours or doesn't exist.")
    if it.status != "PENDING":
        raise TradeError("Already handled.")
    if _aware(it.expires_at) < utcnow():
        it.status = "EXPIRED"
        db.commit()
        raise TradeError("The preview expired. Press ENTER again for a fresh one.")
    if it.mode != acc.mode:
        raise TradeError("Your trading mode changed since the preview. Press ENTER again.")
    if acc.mode == "LIVE" and not (acc.live_enabled_at and live_allowed(db, member)):
        raise TradeError("LIVE isn't enabled for this account.")
    if acc.paused or signals.settings(db).paused:
        raise TradeError("New trades are stopped (STOP NEW TRADES). Nothing was sent.")
    sig = db.get(TsSignal, it.signal_id)
    assert sig is not None
    _still_allowed(db, acc, sig)
    it.status = "CONFIRMED"  # single use, before anything is sent
    db.commit()
    # the preview's price can be 5 minutes old: size and price the IOC entry at the current mark (same checks as the preview)
    try:
        plan, ex = _plan(db, member, sig, ex)
    except TradeError as e:
        raise TradeError(f"{str(e).rstrip('.')}. Nothing was sent.") from e
    seen = float((it.plan or {}).get("size") or 0)
    if seen and plan.size > seen:  # never more than the size the member confirmed (a smaller one keeps the risk limits)
        k = seen / plan.size
        plan.size, plan.notional, plan.margin = seen, plan.notional * k, plan.margin * k
        plan.max_loss_usd, plan.est_fees = plan.max_loss_usd * k, plan.est_fees * k
    return open_position(db, acc, sig, plan, "enter", ex)


def _still_allowed(db: Session, acc: TrAccount, sig: TsSignal) -> None:
    """What may have changed between a preview and its confirmation (up to 5 minutes, possibly several previews at once): the
    signal closed, another position opened in the same coin, the position limit or the daily loss limit was reached."""
    if sig.status not in signals.OPEN:
        raise TradeError(f"Signal #{sig.number:03d} is {signals.STATUS_LABEL.get(sig.status, sig.status).lower()} now. Nothing was sent.")
    if db.scalar(select(TrPosition.id).where(TrPosition.account_id == acc.id, TrPosition.coin == sig.coin,
                                             TrPosition.status.in_(OPEN_STATES + ("PROTECTION_FAILED",))).limit(1)):
        raise TradeError(f"You already have a bot position in {sig.coin}. Nothing was sent.")
    cap = min(acc.max_positions, risk.HARD_MAX_POSITIONS)
    if open_count(db, acc) >= cap:
        raise TradeError(f"You have {cap} bot positions open already (your limit). Nothing was sent.")
    if daily_pnl(db, acc) <= -abs(acc.daily_loss_limit_usd):
        raise TradeError("Your daily loss limit was reached since the preview. Nothing was sent.")


def cancel_intent(db: Session, member: Member, token: str) -> None:
    it = db.scalar(select(TrIntent).where(TrIntent.token == token))
    if it is not None and it.account_id == account(db, member).id and it.status == "PENDING":
        it.status = "CANCELLED"
        db.commit()


# ---------------------------------------------------------------- entering and protecting
def _member_of(db: Session, pos: TrPosition) -> int | None:
    acc = db.get(TrAccount, pos.account_id)
    return acc.member_id if acc else None


def _order(db: Session, pos: TrPosition, kind: str, is_buy: bool, size: float, price: float, reduce: bool, res: xch.Result, cloid: str) -> TrOrder:
    o = TrOrder(position_id=pos.id, cloid=cloid, kind=kind, is_buy=is_buy, size=size, price=price, reduce_only=reduce,
                status=res.status, oid=res.oid, filled_size=res.filled, avg_px=res.avg_px, response=res.raw, error=res.error)
    db.add(o)
    db.flush()
    from app.tradingos import brain

    mid, venue = _member_of(db, pos), ("paper" if pos.mode == "PAPER" else f"hyperliquid:exchange {pos.mode.lower()}")
    label = {"entry": "entry", "sl": "stop-loss", "tp1": "TP1", "tp2": "TP2", "close": "close", "emergency": "emergency close"}.get(kind, kind)
    brain.log(db, "order", f"{pos.coin} {label} order sent: {'buy' if is_buy else 'sell'} {size:g} @ {price:g}"
              + (" (reduce-only)" if reduce else ""), member_id=mid, signal_id=pos.signal_id, coin=pos.coin, source=venue,
              data={"position": pos.id, "kind": kind, "side": "buy" if is_buy else "sell", "size": size, "price": price, "reduce_only": reduce,
                    "client_order_id": cloid, "mode": pos.mode})
    brain.log(db, "exchange", f"{pos.coin} {label}: {res.status}" + (f" · filled {res.filled:g} @ {res.avg_px:g}" if res.filled and res.avg_px else "")
              + (f" · {res.error}" if res.error else ""), member_id=mid, signal_id=pos.signal_id, coin=pos.coin, source=venue,
              data={"position": pos.id, "kind": kind, "status": res.status, "order_id": res.oid, "filled": res.filled, "avg_px": res.avg_px,
                    "error": res.error, "mode": pos.mode})
    return o


def open_position(db: Session, acc: TrAccount, sig: TsSignal, plan: risk.Plan, via: str, ex: Any = None) -> TrPosition:
    long = sig.side == "LONG"
    m = market.data().market(sig.coin)  # before any row: a 429 or a missing API-wallet key leaves no ghost OPENING row
    ex = ex or executor(db, acc, sig.coin)
    # an earlier attempt at this signal that never filled (FAILED, no entry price) is retried in place, not a second row
    pos = db.scalar(select(TrPosition).where(TrPosition.account_id == acc.id, TrPosition.signal_id == sig.id, TrPosition.status == "FAILED",
                                             TrPosition.entry_px.is_(None), TrPosition.size <= 0).with_for_update())
    attempt = 0
    if pos is None:
        pos = TrPosition(account_id=acc.id, signal_id=sig.id, mode=acc.mode, coin=sig.coin, side=sig.side, status="OPENING",
                         leverage=plan.leverage, stop=plan.stop, tp1=plan.tp1, tp2=plan.tp2, max_loss_usd=plan.max_loss_usd, via=via)
        db.add(pos)
    else:
        attempt = 1 + int(db.scalar(select(func.count()).select_from(TrOrder).where(TrOrder.position_id == pos.id, TrOrder.kind == "entry")) or 0)
        # claimed atomically: two confirms racing for the same FAILED row can't both send an entry
        if db.execute(update(TrPosition).where(TrPosition.id == pos.id, TrPosition.status == "FAILED")
                      .values(status="OPENING")).rowcount != 1:
            db.rollback()
            raise TradeError("You're already in this signal.")
        db.refresh(pos)
        pos.mode, pos.error, pos.leverage, pos.via = acc.mode, None, plan.leverage, via
        pos.stop, pos.tp1, pos.tp2, pos.max_loss_usd, pos.fees = plan.stop, plan.tp1, plan.tp2, plan.max_loss_usd, 0.0
    try:
        db.commit()
    except IntegrityError as e:  # this account already has a position for this signal: never twice
        db.rollback()
        raise TradeError("You're already in this signal.") from e
    try:
        ex.set_leverage(sig.coin, plan.leverage)
        limit_px = risk.round_px(plan.entry * (1 + 2 * risk.SLIPPAGE if long else 1 - 2 * risk.SLIPPAGE), m.sz_decimals)
        # a fresh client order id per attempt (Hyperliquid refuses a reused one); the first attempt keeps the original id
        cl = xch.cloid_for(acc.id, sig.id, "entry", *([f"a{attempt}"] if attempt else []))
        res = ex.market(sig.coin, long, plan.size, limit_px, cl)
        _order(db, pos, "entry", long, plan.size, limit_px, False, res, cl)
    except Exception as e:  # noqa: BLE001 - the order may or may not have reached the exchange
        err = f"{type(e).__name__}: {str(e)[:200]}"
        if acc.mode != "PAPER" and _adopt(db, acc, pos, ex, plan, m.sz_decimals, err):
            _record_participation(db, acc, sig, via)
            db.commit()
            return pos
        pos.status, pos.error = "FAILED", err
        signals.audit(db, f"account:{acc.id}", "entry_error", sig, error=pos.error, mode=acc.mode)
        db.commit()
        raise TradeError(f"The order didn't go through: {pos.error}"
                         + ("" if acc.mode == "PAPER" else ". Check app.hyperliquid.xyz → Positions to be sure.")) from e
    if res.status not in ("FILLED", "PARTIAL") or res.filled <= 0:
        pos.status, pos.error = "FAILED", res.error or res.status
        signals.audit(db, f"account:{acc.id}", "entry_not_filled", sig, status=res.status, error=res.error, mode=acc.mode)
        db.commit()
        raise TradeError(f"Not filled ({res.error or res.status}). No position was opened.")
    pos.size, pos.entry_px = res.filled, res.avg_px
    pos.fees = round((res.avg_px or plan.entry) * res.filled * risk.TAKER_FEE, 6)
    from app.tradingos import brain

    brain.log(db, "position", f"{pos.coin} {pos.side} opened: {res.filled:g} @ {res.avg_px:g} ({pos.mode})", member_id=acc.member_id,
              signal_id=sig.id, coin=pos.coin, source="paper" if pos.mode == "PAPER" else f"hyperliquid:exchange {pos.mode.lower()}",
              data={"position": pos.id, "mode": pos.mode, "via": via, "size": res.filled, "entry": res.avg_px, "leverage": plan.leverage,
                    "stop": plan.stop, "tp1": plan.tp1, "tp2": plan.tp2, "max_loss_usd": plan.max_loss_usd})
    db.commit()  # the fill (size, price, order ids) is on record before protection is tried: a crash there can't lose it
    protect(db, acc, pos, ex, m.sz_decimals)
    _record_participation(db, acc, sig, via)
    db.commit()
    return pos


def _adopt(db: Session, acc: TrAccount, pos: TrPosition, ex: Any, plan: risk.Plan, sz_decimals: int, err: str) -> bool:
    """The entry raised (a timeout, a dropped connection) after it may have reached Hyperliquid. If the exchange now holds a
    position in this coin on the signal's side (the preview checked there was none), it's this entry: book what's there and
    protect it at once, rather than leave a real position without a stop while Price Chat says "failed"."""
    try:
        held = next((p["position"] for p in ex.state().get("assetPositions", []) if p["position"]["coin"] == pos.coin
                     and float(p["position"]["szi"]) != 0), None)
    except Exception:  # noqa: BLE001 - can't tell: reported as failed, with the advice to check Hyperliquid
        return False
    if held is None or (float(held["szi"]) > 0) != (pos.side == "LONG"):
        return False
    pos.size, pos.entry_px = abs(float(held["szi"])), float(held.get("entryPx") or plan.entry)
    pos.fees = round(pos.entry_px * pos.size * risk.TAKER_FEE, 6)
    signals.audit(db, f"account:{acc.id}", "entry_adopted", db.get(TsSignal, pos.signal_id), error=err, size=pos.size, mode=acc.mode)
    db.commit()  # on record before protection is tried
    protect(db, acc, pos, ex, sz_decimals)
    return True


def protect(db: Session, acc: TrAccount, pos: TrPosition, ex: Any, sz_decimals: int) -> None:
    """Exchange-side stop for the whole filled size, TP1 for half, TP2 for the rest. If the stop can't be placed: close now."""
    long = pos.side == "LONG"
    sl_cl = xch.cloid_for(acc.id, pos.signal_id, "sl")
    try:
        sl = ex.trigger(pos.coin, not long, pos.size, risk.round_px(pos.stop, sz_decimals), "sl", sl_cl)
    except Exception as e:  # noqa: BLE001 - a timeout / HTTP error: an unconfirmed stop is an unplaced stop
        sl = xch.Result("ERROR", error=f"{type(e).__name__}: {str(e)[:200]}")
    _order(db, pos, "sl", not long, pos.size, pos.stop, True, sl, sl_cl)
    if sl.status != "RESTING":
        emergency(db, acc, pos, ex, sz_decimals, f"stop-loss not placed: {sl.error or sl.status}")
        return
    half = risk.round_size(pos.size / 2, sz_decimals)
    rest = risk.round_size(pos.size - half, sz_decimals)
    for kind, size, px in (("tp1", half, pos.tp1), ("tp2", rest, pos.tp2)):
        if size <= 0:
            continue
        cl = xch.cloid_for(acc.id, pos.signal_id, kind)
        try:
            r = ex.trigger(pos.coin, not long, size, risk.round_px(px, sz_decimals), "tp", cl)
        except Exception as e:  # noqa: BLE001
            r = xch.Result("ERROR", error=f"{type(e).__name__}: {str(e)[:200]}")
        _order(db, pos, kind, not long, size, px, True, r, cl)  # a missing TP leaves the stop in place: logged, not fatal
    pos.status = "OPEN"


def emergency(db: Session, acc: TrAccount, pos: TrPosition, ex: Any, sz_decimals: int, why: str) -> None:
    """The tested emergency policy: no unprotected position. Close it at market (reduce-only) and stop new entries."""
    long = pos.side == "LONG"
    cl = xch.cloid_for(acc.id, pos.signal_id, "emergency")
    try:
        try:
            mark = ref_px(pos, ex)
        except Exception:  # noqa: BLE001 - no price right now: still close, priced off the entry (or the stop)
            mark = pos.entry_px or pos.stop
        px = risk.round_px(mark * (1 - 3 * risk.SLIPPAGE if long else 1 + 3 * risk.SLIPPAGE), sz_decimals)
        res = ex.market(pos.coin, not long, pos.size, px, cl, reduce_only=True)
        _order(db, pos, "close", not long, pos.size, px, True, res, cl)
        if res.status in ("FILLED", "PARTIAL"):
            _settle(pos, res.avg_px or mark, "emergency_close", res.filled)
    except Exception as e:  # noqa: BLE001
        why += f"; emergency close failed too: {type(e).__name__}"
    pos.status = "PROTECTION_FAILED" if pos.status != "CLOSED" else "CLOSED"
    pos.error = why
    acc.paused, acc.paused_reason = True, f"Protection failed on {pos.coin}: {why}"
    signals.audit(db, f"account:{acc.id}", "protection_failed", db.get(TsSignal, pos.signal_id), error=why, mode=acc.mode)


def _settle(pos: TrPosition, px: float, reason: str, size: float | None = None) -> None:
    """Book a (partial) close at `px` with estimated taker fees (paper; live/testnet are corrected from exchange fills)."""
    size = pos.size if size is None else size
    sign = 1 if pos.side == "LONG" else -1
    pnl = (px - (pos.entry_px or px)) * size * sign - px * size * risk.TAKER_FEE
    pos.realized_pnl = round(pos.realized_pnl + pnl, 6)
    pos.fees = round(pos.fees + px * size * risk.TAKER_FEE, 6)
    pos.size = round(pos.size - size, 10)
    if pos.size <= 1e-12:
        pos.status, pos.exit_px, pos.closed_at, pos.close_reason = "CLOSED", px, utcnow(), reason
        from sqlalchemy.orm import object_session

        from app.tradingos import brain

        db = object_session(pos)
        if db is not None:
            brain.log(db, "position", f"{pos.coin} {pos.side} closed ({reason}) @ {px:g} · net ${pos.realized_pnl:+,.2f}", member_id=_member_of(db, pos),
                      signal_id=pos.signal_id, coin=pos.coin, source="paper" if pos.mode == "PAPER" else f"hyperliquid:exchange {pos.mode.lower()}",
                      data={"position": pos.id, "mode": pos.mode, "reason": reason, "exit": px, "net_pnl": pos.realized_pnl, "fees": pos.fees})


def _record_participation(db: Session, acc: TrAccount, sig: TsSignal, via: str) -> None:
    """Count a confirmed entry as a participating Kiwi (only after a fill)."""
    try:
        with db.begin_nested():  # a savepoint: an earlier answer to this signal must not undo the position
            db.add(TsSignalAction(signal_id=sig.id, who=f"m:{acc.member_id}", member_id=acc.member_id, action="enter", verified=True))
    except IntegrityError:
        pass


# ---------------------------------------------------------------- closing, adjusting, tracking, reconciling
CLOSE_FRACTIONS = (0.25, 0.5, 1.0)
CLOSABLE = ("OPEN", "PROTECTION_FAILED")
EXCHANGE_DOWN = ("Your stop-loss stays on the exchange. If you need to act now, manage the position directly on Hyperliquid: "
                 "app.hyperliquid.xyz → Positions → Close.")


def _mine(db: Session, member: Member, pos_id: int) -> tuple[TrAccount, TrPosition]:
    """The member's own position, row-locked (a close from Telegram and one from the website can't both run)."""
    acc = account(db, member)
    pos = db.scalar(select(TrPosition).where(TrPosition.id == pos_id).with_for_update())
    if pos is None or pos.account_id != acc.id or pos.status not in CLOSABLE or pos.size <= 0:
        raise TradeError("No such open position of yours.")
    return acc, pos


CLOSE_LEASE = "closing:"  # in close_reason while a close is under way (an open position has none): "closing:<unix time>"
CLOSE_LEASE_SECONDS = 180  # a close cut off midway (a crash) frees the position after this long


def closing(pos: TrPosition) -> bool:
    return (pos.close_reason or "").startswith(CLOSE_LEASE)


def _claim_close(db: Session, pos: TrPosition) -> str:
    """Claim the position for one close (a close from Telegram and one from the website can't both run) with one atomic
    UPDATE, committed at once: the exchange calls that follow never run inside a write transaction (on SQLite that would
    hold the database's only write lock for every other writer)."""
    now = int(time.time())
    lease, stale = f"{CLOSE_LEASE}{now}", f"{CLOSE_LEASE}{now - CLOSE_LEASE_SECONDS}"
    won = db.execute(update(TrPosition).where(
        TrPosition.id == pos.id, TrPosition.status.in_(CLOSABLE), TrPosition.size > 0,
        or_(TrPosition.close_reason.is_(None), ~TrPosition.close_reason.like(f"{CLOSE_LEASE}%"), TrPosition.close_reason < stale),
    ).values(close_reason=lease).execution_options(synchronize_session=False)).rowcount
    db.commit()
    if won != 1:
        raise TradeError("This position is already being closed.")
    db.refresh(pos)  # its size/status as of the claim (another close may have just finished)
    return lease


def _release_close(db: Session, pos: TrPosition, lease: str) -> None:
    """Drop the claim (unless the close booked its own close reason)."""
    db.execute(update(TrPosition).where(TrPosition.id == pos.id, TrPosition.close_reason == lease)
               .values(close_reason=None).execution_options(synchronize_session=False))
    db.commit()
    db.refresh(pos)


def _seq(db: Session, pos: TrPosition) -> int:
    return int(db.scalar(select(func.count()).select_from(TrOrder).where(TrOrder.position_id == pos.id)) or 0)


def _resting(db: Session, pos: TrPosition, *kinds: str) -> list[TrOrder]:
    return list(db.scalars(select(TrOrder).where(TrOrder.position_id == pos.id, TrOrder.status == "RESTING", TrOrder.kind.in_(kinds))
                           .order_by(TrOrder.id)))


def _cancel(ex: Any, pos: TrPosition, orders: list[TrOrder]) -> list[str]:
    """Cancel this position's own orders (never anything else). Returns the ones the exchange wouldn't cancel."""
    failed = []
    for o in orders:
        try:
            ex.cancel(pos.coin, o.oid)
            o.status = "CANCELLED"
        except Exception as e:  # noqa: BLE001 - it may have triggered or be gone already; reconciliation re-checks
            failed.append(f"{o.kind}: {type(e).__name__}")
    return failed


def close_position(db: Session, member: Member, pos_id: int, ex: Any = None, fraction: float = 1.0) -> TrPosition:
    """Reduce-only market close of 25 / 50 / 100% of a bot position. Only what the exchange reports as filled is booked;
    the protective orders are resized to what's left (a stop is never removed before the close is confirmed)."""
    if fraction not in CLOSE_FRACTIONS:
        raise TradeError("Close 25%, 50% or 100%.")
    acc, pos = _mine(db, member, pos_id)
    if pos.status == "PROTECTION_FAILED" and fraction < 1:
        raise TradeError("This position has no confirmed stop-loss: close 100%.")
    lease = _claim_close(db, pos)
    try:
        return _close(db, member, acc, pos, ex, fraction)
    except BaseException:
        db.rollback()  # what was booked is committed; anything half-done is not
        raise
    finally:
        _release_close(db, pos, lease)


def _close(db: Session, member: Member, acc: TrAccount, pos: TrPosition, ex: Any, fraction: float) -> TrPosition:
    """close_position, once the position is claimed. Every exchange call runs outside a write transaction: each answer is
    committed before the next call."""
    m = market.data().market(pos.coin)
    try:
        ex = ex or executor(db, acc, pos.coin, pos.mode)
        mark = ref_px(pos, ex)
    except (xch.ExchangeError, market.MarketError) as e:
        raise TradeError(f"The exchange can't be reached ({e}). Nothing was sent. {EXCHANGE_DOWN}") from e
    long = pos.side == "LONG"
    size = pos.size if fraction >= 1 else risk.round_size(pos.size * fraction, m.sz_decimals)
    if fraction < 1 and (size <= 0 or size * mark < risk.MIN_NOTIONAL or (pos.size - size) * mark < risk.MIN_NOTIONAL):
        raise TradeError(f"A {fraction:.0%} close would make an order under Hyperliquid's ${risk.MIN_NOTIONAL:g} minimum. Close 100% instead.")
    px = risk.round_px(mark * (1 - 3 * risk.SLIPPAGE if long else 1 + 3 * risk.SLIPPAGE), m.sz_decimals)
    cl = xch.cloid_for(acc.id, pos.signal_id, "close", pos.id, _seq(db, pos))
    try:
        res = ex.market(pos.coin, not long, size, px, cl, reduce_only=True)  # reduce-only: can never open a reverse position
    except Exception as e:  # noqa: BLE001
        signals.audit(db, f"m:{member.id}", "close_error", db.get(TsSignal, pos.signal_id), position=pos.id, error=f"{type(e).__name__}: {e}"[:300])
        db.commit()
        raise TradeError(f"Hyperliquid didn't answer the close ({type(e).__name__}). {EXCHANGE_DOWN}") from e
    _order(db, pos, "close", not long, size, px, True, res, cl)
    if res.status not in ("FILLED", "PARTIAL") or res.filled <= 0:
        signals.audit(db, f"m:{member.id}", "close_not_filled", db.get(TsSignal, pos.signal_id), position=pos.id, status=res.status, error=res.error)
        db.commit()
        if pos.mode != "PAPER" and _gone(ex, pos.coin):  # its stop or a target already closed it on Hyperliquid
            reconcile(db, acc, ex, pos.mode)
            if pos.status == "CLOSED":
                return pos
        raise TradeError(f"Close not filled ({res.error or res.status}). The position and its stop-loss are unchanged.")
    _settle(pos, res.avg_px or mark, "manual_close", res.filled)
    db.commit()  # the fill is booked before the stop and targets are touched
    if pos.status == "CLOSED":
        left = _cancel(ex, pos, _resting(db, pos, "sl", "tp1", "tp2"))
        if left:
            pos.error = "Closed; some leftover orders couldn't be cancelled (reduce-only, they can't open a position): " + ", ".join(left)
        if pos.mode != "PAPER":
            try:
                _book_from_fills(db, pos, ex)
            except Exception:  # noqa: BLE001 - the estimate stays until the exchange's fills can be read
                pass
    elif pos.status == "OPEN":
        reprotect(db, acc, pos, ex, m.sz_decimals)
    signals.audit(db, f"m:{member.id}", "position_close", db.get(TsSignal, pos.signal_id), position=pos.id, fraction=fraction,
                  filled=res.filled, avg_px=res.avg_px, remaining=pos.size, mode=pos.mode)
    db.commit()
    return pos


def _gone(ex: Any, coin: str) -> bool:
    try:
        return not any(p["position"]["coin"] == coin and float(p["position"]["szi"]) != 0 for p in ex.state().get("assetPositions", []))
    except Exception:  # noqa: BLE001 - unknown: assume it's still there
        return False


def close_all(db: Session, member: Member, ex_for: Any = None) -> dict[str, Any]:
    """Emergency: close every bot position of this member (reduce-only), one by one, reporting each exchange answer."""
    acc = account(db, member)
    ids = list(db.scalars(select(TrPosition.id).where(TrPosition.account_id == acc.id, TrPosition.status.in_(CLOSABLE), TrPosition.size > 0)))
    out: dict[str, Any] = {"requested": len(ids), "closed": [], "partial": [], "failed": []}
    for pid in ids:
        pos = db.get(TrPosition, pid)
        assert pos is not None
        coin = pos.coin
        try:
            p = close_position(db, member, pid, ex_for(pos) if ex_for else None)
            (out["closed"] if p.status == "CLOSED" else out["partial"]).append({"id": pid, "coin": coin, "remaining": p.size})
        except (TradeError, market.MarketError) as e:
            db.rollback()
            out["failed"].append({"id": pid, "coin": coin, "error": str(e)})
    signals.audit(db, f"m:{member.id}", "close_all", None, requested=len(ids), closed=len(out["closed"]), partial=len(out["partial"]),
                  failed=len(out["failed"]))
    db.commit()
    return out


def reprotect(db: Session, acc: TrAccount, pos: TrPosition, ex: Any, sz_decimals: int) -> None:
    """Resize the stop and targets to what's left. The new stop goes in first; the old one is only cancelled once the new
    one rests (until then the old reduce-only stop still covers the smaller position)."""
    long = pos.side == "LONG"
    old_sl = _resting(db, pos, "sl")
    cl = xch.cloid_for(acc.id, pos.signal_id, "sl", pos.id, _seq(db, pos))
    sl = ex.trigger(pos.coin, not long, pos.size, risk.round_px(pos.stop, sz_decimals), "sl", cl)
    _order(db, pos, "sl", not long, pos.size, pos.stop, True, sl, cl)
    db.commit()  # each order is on record before the next exchange call (and no write lock is held during it)
    if sl.status == "RESTING":
        _cancel(ex, pos, old_sl)
    else:
        pos.error = f"Resized stop-loss refused ({sl.error or sl.status}); the previous stop-loss stays in place."
    _cancel(ex, pos, _resting(db, pos, "tp1", "tp2"))
    half = 0.0 if pos.tp1_done else risk.round_size(pos.size / 2, sz_decimals)
    for kind, size, px in (("tp1", half, pos.tp1), ("tp2", risk.round_size(pos.size - half, sz_decimals), pos.tp2)):
        if size <= 0:
            continue
        cl = xch.cloid_for(acc.id, pos.signal_id, kind, pos.id, _seq(db, pos))
        _order(db, pos, kind, not long, size, px, True, ex.trigger(pos.coin, not long, size, risk.round_px(px, sz_decimals), "tp", cl), cl)
        db.commit()


def adjust_protection(db: Session, member: Member, pos_id: int, stop: float | None = None, tp1: float | None = None,
                      tp2: float | None = None, dry_run: bool = False, ex: Any = None) -> dict[str, Any]:
    """Move the stop-loss or a take-profit. Validated here, whatever the browser sent: the stop can only move TOWARDS the
    price (less risk, never more), nothing may trigger at once, and size and leverage never change."""
    acc, pos = _mine(db, member, pos_id)
    if pos.status != "OPEN":
        raise TradeError("This position has no confirmed stop-loss: close it instead.")
    if closing(pos) and not dry_run:
        raise TradeError("This position is being closed right now.")
    long = pos.side == "LONG"
    m = market.data().market(pos.coin)
    ex = ex or executor(db, acc, pos.coin, pos.mode)
    mark = ref_px(pos, ex)
    f = market.fmt_price
    changes: list[str] = []
    if stop is not None:
        stop = risk.round_px(stop, m.sz_decimals)
        if (long and stop >= mark) or (not long and stop <= mark):
            raise TradeError(f"A stop at {f(stop)} would trigger at once (price {f(mark)}). Use CLOSE POSITION instead.")
        if (long and stop < pos.stop - 1e-12) or (not long and stop > pos.stop + 1e-12):
            raise TradeError(f"Moving the stop from {f(pos.stop)} to {f(stop)} would increase your risk. It can only be tightened here.")
        if stop != pos.stop:
            changes.append(f"Stop-loss {f(pos.stop)} → {f(stop)}")
    for kind, val in (("tp1", tp1), ("tp2", tp2)):
        if val is None:
            continue
        if kind == "tp1" and pos.tp1_done:
            raise TradeError("TP1 was already taken.")
        val = risk.round_px(val, m.sz_decimals)
        if (long and val <= mark) or (not long and val >= mark):
            raise TradeError(f"{kind.upper()} at {f(val)} is on the wrong side of the price ({f(mark)}).")
        if val != getattr(pos, kind):
            changes.append(f"{kind.upper()} {f(getattr(pos, kind))} → {f(val)}")
        if kind == "tp1":
            tp1 = val
        else:
            tp2 = val
    n1, n2 = tp1 if tp1 is not None else pos.tp1, tp2 if tp2 is not None else pos.tp2
    if not pos.tp1_done and ((long and n1 > n2) or (not long and n1 < n2)):
        raise TradeError("TP1 must come before TP2.")
    if not changes:
        raise TradeError("Nothing to change.")
    new_stop = stop if stop is not None else pos.stop
    risk_after = round(abs((pos.entry_px or mark) - new_stop) * pos.size + new_stop * pos.size * risk.TAKER_FEE, 2)
    summary = {"position": pos.id, "coin": pos.coin, "side": pos.side, "size": pos.size, "leverage": pos.leverage, "changes": changes,
               "loss_at_stop_usd": risk_after, "mark": mark}
    if dry_run:
        return summary | {"dry_run": True}
    sig = db.get(TsSignal, pos.signal_id)
    done: list[str] = []
    try:
        if stop is not None and stop != pos.stop:
            old = _resting(db, pos, "sl")
            cl = xch.cloid_for(acc.id, pos.signal_id, "sl", pos.id, _seq(db, pos))
            r = ex.trigger(pos.coin, not long, pos.size, stop, "sl", cl)
            _order(db, pos, "sl", not long, pos.size, stop, True, r, cl)
            db.commit()  # on record before the next exchange call, which then runs without holding the write lock
            if r.status != "RESTING":
                raise TradeError(f"Hyperliquid refused the new stop ({r.error or r.status}). Your previous stop-loss is unchanged.")
            _cancel(ex, pos, old)
            pos.stop = stop
            done.append("stop")
        for kind, val in (("tp1", tp1), ("tp2", tp2)):
            if val is None or val == getattr(pos, kind):
                continue
            old = _resting(db, pos, kind)
            size = old[-1].size if old else (risk.round_size(pos.size / 2, m.sz_decimals) if kind == "tp1"
                                             else risk.round_size(pos.size - sum(o.size for o in _resting(db, pos, "tp1")), m.sz_decimals))
            cl = xch.cloid_for(acc.id, pos.signal_id, kind, pos.id, _seq(db, pos))
            r = ex.trigger(pos.coin, not long, size, val, "tp", cl)
            _order(db, pos, kind, not long, size, val, True, r, cl)
            db.commit()
            if r.status != "RESTING":
                raise TradeError(f"Hyperliquid refused the new {kind.upper()} ({r.error or r.status}). The previous one is unchanged.")
            _cancel(ex, pos, old)
            setattr(pos, kind, val)
            done.append(kind)
    except TradeError:
        signals.audit(db, f"m:{member.id}", "protection_change_refused", sig, position=pos.id, changes=changes, done=done)
        db.commit()
        raise
    except Exception as e:  # noqa: BLE001
        signals.audit(db, f"m:{member.id}", "protection_change_error", sig, position=pos.id, error=f"{type(e).__name__}"[:100], done=done)
        db.commit()
        raise TradeError(f"Hyperliquid didn't answer ({type(e).__name__}). {EXCHANGE_DOWN}") from e
    signals.audit(db, f"m:{member.id}", "protection_changed", sig, position=pos.id, changes=changes, mode=pos.mode)
    db.commit()
    return summary | {"dry_run": False, "done": done}


def track_paper(db: Session) -> int:
    """Paper positions: stop / TP1 / TP2 on real 1-minute candles (stop first within a candle: the conservative case)."""
    changed = 0
    for pos in db.scalars(select(TrPosition).where(TrPosition.mode == "PAPER", TrPosition.status == "OPEN")):
        try:
            cs = market.data().candles(pos.coin, "1m", 15)
        except market.MarketError:
            continue
        long = pos.side == "LONG"
        for c in cs:
            if c.t / 1000 < (_aware(pos.created_at) or utcnow()).timestamp():
                continue
            if (long and c.l <= pos.stop) or (not long and c.h >= pos.stop):
                _settle(pos, pos.stop * (1 - risk.SLIPPAGE if long else 1 + risk.SLIPPAGE), "stop")
            elif not pos.tp1_done and ((long and c.h >= pos.tp1) or (not long and c.l <= pos.tp1)):
                half = round(pos.size / 2, 10)
                _settle(pos, pos.tp1, "tp1", half)
                pos.tp1_done = True
            elif pos.tp1_done and ((long and c.h >= pos.tp2) or (not long and c.l <= pos.tp2)):
                _settle(pos, pos.tp2, "tp2")
            if pos.status == "CLOSED":
                break
        changed += 1
    db.commit()
    return changed


RECONCILED = ("OPEN", "PROTECTION_FAILED")


def reconcile(db: Session, acc: TrAccount, ex: Any = None, mode: str | None = None) -> int:
    """Testnet / live: positions the exchange no longer holds are closed with P&L from the exchange's own fills. Every network
    with open positions is checked, whatever mode the account is in now (a mode change never orphans a position)."""
    modes = [mode] if mode else sorted({m for (m,) in db.execute(select(TrPosition.mode).where(
        TrPosition.account_id == acc.id, TrPosition.status.in_(RECONCILED), TrPosition.mode != "PAPER")).all()})
    return sum(_reconcile(db, acc, m, ex or executor(db, acc, "BTC", m)) for m in modes if m != "PAPER")


def _reconcile(db: Session, acc: TrAccount, mode: str, ex: Any) -> int:
    st = ex.state()
    held = {p["position"]["coin"]: float(p["position"]["szi"]) for p in st.get("assetPositions", [])}
    n = 0
    for pos in db.scalars(select(TrPosition).where(TrPosition.account_id == acc.id, TrPosition.status.in_(RECONCILED), TrPosition.mode == mode)):
        now_held = abs(held.get(pos.coin, 0.0))
        if now_held > 0:
            if now_held + 1e-12 < pos.size and pos.status == "OPEN":  # TP1 (or part of the position) was taken on the exchange
                pos.size, pos.tp1_done = now_held, True
            continue
        _book_from_fills(db, pos, ex)
        pos.status, pos.closed_at, pos.size = "CLOSED", utcnow(), 0.0
        pos.close_reason = "exchange"
        from app.tradingos import brain

        brain.log(db, "position", f"{pos.coin} {pos.side} closed on Hyperliquid (stop / take-profit / manual) · net ${pos.realized_pnl:+,.2f}",
                  member_id=acc.member_id, signal_id=pos.signal_id, coin=pos.coin, source=f"hyperliquid:info {mode.lower()} fills",
                  data={"position": pos.id, "mode": pos.mode, "reason": "exchange", "exit": pos.exit_px, "net_pnl": pos.realized_pnl, "fees": pos.fees})
        for o in db.scalars(select(TrOrder).where(TrOrder.position_id == pos.id, TrOrder.status == "RESTING")):
            try:  # leftover take-profit / stop orders of this position only
                ex.cancel(pos.coin, o.oid)
            except Exception:  # noqa: BLE001 - already gone on the exchange
                pass
            o.status = "CANCELLED"
        n += 1
    db.commit()
    return n


def _book_from_fills(db: Session, pos: TrPosition, ex: Any) -> None:
    """Net P&L, fees and exit price from the exchange's own fills of this position's orders (replacing the estimates)."""
    oids = {o.oid for o in db.scalars(select(TrOrder).where(TrOrder.position_id == pos.id)) if o.oid}
    fills = [f for f in ex.fills_since(int((_aware(pos.created_at) or utcnow()).timestamp() * 1000)) if f.get("oid") in oids]
    if not fills:
        return
    closed_pnl = sum(float(f.get("closedPnl") or 0) for f in fills)
    fees = sum(float(f.get("fee") or 0) for f in fills)
    pos.realized_pnl, pos.fees = round(closed_pnl - fees, 6), round(fees, 6)
    exits = [f for f in fills if f.get("dir", "").startswith("Close")]
    if exits:
        pos.exit_px = float(exits[-1]["px"])


# ---------------------------------------------------------------- AUTO
def _auto_skip(db: Session, acc: TrAccount, member: Member, sig: TsSignal, reason: str, kind: str, source: str = "auto",
               reasons: list[str] | None = None) -> None:
    """One recorded skip: the intent (so the signal isn't considered again), the audit line and the member's Kiwi Brain feed."""
    from app.tradingos import brain

    db.add(TrIntent(account_id=acc.id, signal_id=sig.id, token=secrets.token_hex(8), mode=acc.mode, plan={"skipped": reason[:300]},
                    status="SKIPPED", expires_at=utcnow()))
    signals.audit(db, f"account:{acc.id}", "auto_skipped", sig, reason=f"{kind}: {reason}"[:300])
    brain.log(db, "risk", f"{sig.coin} #{sig.number:03d}: AUTO skipped · {reason[:160]}", member_id=member.id, signal_id=sig.id, coin=sig.coin,
              source=source, data={"decision": "skipped", "kind": kind, "reasons": reasons or [reason]})
    db.commit()


def run_auto(db: Session, sig: TsSignal) -> list[int]:
    """Members with AUTO on (and not expired) enter a newly published signal within their preapproved limits."""
    out = []
    now = utcnow()
    for acc in db.scalars(select(TrAccount).where(TrAccount.auto_enabled.is_(True), TrAccount.paused.is_(False))):
        if acc.auto_until and _aware(acc.auto_until) < now:
            acc.auto_enabled = False
            continue
        member = db.get(Member, acc.member_id)
        if member is None or (sig.strategy == "test" and acc.mode != "PAPER"):
            continue  # test signals never trade real or testnet money
        if db.scalar(select(TrIntent.id).where(TrIntent.account_id == acc.id, TrIntent.signal_id == sig.id).limit(1)) or \
                db.scalar(select(TrPosition.id).where(TrPosition.account_id == acc.id, TrPosition.signal_id == sig.id)):
            continue  # each signal is considered once per account (entered, previewed, or skipped)
        if not wants(acc, sig):  # recorded, so a member with AUTO on always sees why a signal wasn't taken
            labels = {"1h": "1h signals only", "15m": "15m signals only", "both": "1h and 15m signals"}
            _auto_skip(db, acc, member, sig, f"a {sig.timeframe} signal; your Trading style takes {labels.get(acc.timeframe_pref, acc.timeframe_pref)} "
                                             "(Earn → Trading style)", "timeframe")
            continue
        blocked = intraday_allowed(db, acc, sig)
        if blocked:
            _auto_skip(db, acc, member, sig, blocked, "intraday")
            continue
        from app.tradingos import brainx

        why = brainx.config_allows(brainx.current_config(db, member)["config"], sig)
        if why:  # the member's own Kiwi config filters it out (filters only ever remove trades)
            _auto_skip(db, acc, member, sig, "Your Kiwi config: " + "; ".join(why), "config", source="teach-your-kiwi", reasons=why)
            continue
        try:
            it = preview(db, member, sig)
            it.status = "CONFIRMED"  # preapproved by the AUTO settings the member confirmed
            db.commit()
            open_position(db, acc, sig, risk.Plan(**it.plan), "auto")
            out.append(acc.id)
        except (TradeError, market.MarketError, xch.ExchangeError) as e:
            db.rollback()
            if isinstance(e, market.MarketError) or "outside the entry zone" in str(e):
                continue  # passing conditions: looked at again next minute
            db.add(TrIntent(account_id=acc.id, signal_id=sig.id, token=secrets.token_hex(8), mode=acc.mode, plan={"skipped": str(e)[:300]},
                            status="SKIPPED", expires_at=utcnow()))
            signals.audit(db, f"account:{acc.id}", "auto_skipped", sig, reason=str(e)[:300])
            db.commit()
        except Exception:  # noqa: BLE001 - one account's SDK / network surprise must not skip the other members for this signal
            db.rollback()
            logger.exception("AUTO entry failed for account %s on signal %s", acc.id, sig.id)
    return out


# ---------------------------------------------------------------- results (per mode, never mixed)
def results(db: Session, acc: TrAccount | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for mode in ("PAPER", "TESTNET", "LIVE"):
        q = select(TrPosition).where(TrPosition.mode == mode, TrPosition.status == "CLOSED")
        if acc is not None:
            q = q.where(TrPosition.account_id == acc.id)
        rows = db.scalars(q).all()
        pnl = [p.realized_pnl for p in rows]
        wins, losses = [x for x in pnl if x > 0], [x for x in pnl if x <= 0]
        out[mode] = {"trades": len(rows), "net": round(sum(pnl), 2), "fees": round(sum(p.fees for p in rows), 4),
                     "win_rate": round(len(wins) / len(rows) * 100, 1) if rows else None,
                     "avg_win": round(sum(wins) / len(wins), 2) if wins else None, "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
                     "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else None}
    return out
