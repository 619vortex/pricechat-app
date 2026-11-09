"""Trading on Hyperliquid from Telegram, for the Banter group: /market, /open, /enter, /positions, /pnl, /close, /closeall,
/sl, /tp, /orders, /cancel, /balance and /settings. bot.py routes the commands here; the trading itself is execution.py's,
with every one of its safety rules (one position per coin, deterministic order ids, fills only as the exchange reports them,
an exchange-side stop on every position or an immediate close, STOP NEW TRADES).

Rules added for Telegram:
  · who: new exposure (/open, and while `telegram_trading.gate_signal_enter` is on, its default, also /enter and a signal's
    ENTER button) only for members whose verified wallet Hyperliquid records as
    referred with the Banter group's code (tradingbot.service.codes). Closing, moving a stop closer, cancelling a target,
    pausing and reading their own account always work, so nobody can be left unable to get out of a position;
  · where: account commands only in the member's private chat with the bot. In a group the bot says so and sends nothing
    private there;
  · how: every order is a preview first (size, margin, leverage, stop, targets, liquidation estimate, fees, funding), sent
    only after the member presses Confirm within 5 minutes; single use, and never larger than what was previewed;
  · which network: PAPER (simulated on real prices) and TESTNET (Hyperliquid's testnet, through the member's own approved
    testnet API wallet). Manual orders on mainnet stay off until the owner switches on `telegram_live` (Admin → Trading OS,
    strategies JSON), and then only through execution.live_gate, the same LIVE checks as every other entry (LIVE enabled
    for the account, allowed for the member, the API wallet still listed for their current verified wallet). TESTNET orders
    are priced and sized from testnet's own market (market.for_mode). Manual trades keep every limit signal trades have
    (allowed contracts, trades a day, cooldown, Engine v2 portfolio checks) on top of the member's own sizing;
  · keys: the bot never asks for a private key or a recovery phrase. A message that looks like one is deleted at once
    (see looks_secret) and the member is told to move their funds. Wallets connect on the website, where the member's own
    wallet approves an API wallet for trading; Price Chat only places, cancels and changes orders and sets leverage with it,
    never withdrawals or transfers (see accounts.PERMISSIONS: Hyperliquid's docs state no blanket "can't withdraw" rule).

A manual trade needs a row in ts_signals (positions and intents are keyed by a signal): each gets a private anchor row with
status MANUAL and a negative number, never published, never in the signal lists, the track record or AUTO. A member's anchor
that never got a position and has no live preview is reused by their next preview, so unconfirmed previews don't pile up rows.
"""

from __future__ import annotations

import functools
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.members import Member
from app.models.trading import TrAccount, TrIntent, TrOrder, TrPosition
from app.models.tradingos import TsSignal
from app.tradingos import execution, market, risk, signals

SITE = "https://pricechat.live"
MANUAL = "MANUAL"
INTENT_TTL = timedelta(minutes=5)
MIN_NOTIONAL = 10.0  # Hyperliquid's minimum order value
LIQ_BUFFER = 0.003  # a stop must trigger at least 0.3% before the estimated liquidation price
COMMANDS = {"/market", "/open", "/enter", "/closeall", "/sl", "/tp", "/orders", "/cancel", "/balance", "/settings"}
HELP = ("<b>Trading (private chat, Banter group)</b>\n"
        "/market BTC: price, funding, open interest, max leverage\n"
        "/open BTC long 100 5x [sl 60000] [tp 70000]: $100 margin at 5x, previewed first\n"
        "/enter: reply to a Price Chat signal to trade it\n"
        "/positions · /pnl · /balance · /orders\n"
        "/sl 12 61000 · /tp 12 64000 [66000]: move a stop or targets\n"
        "/close 12 · /closeall · /cancel (previews or a target order)\n"
        "/settings: your limits and mode")


class GateError(Exception):
    pass


def esc(s: Any) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- who and where
def entry_gate(db: Session, member: Member) -> str | None:
    """Why this member may not open new positions from Telegram, or None. Hyperliquid's own referral record decides
    (checked when the wallet was verified and on every recheck); a link click or anything the member says never does."""
    from app.tradingbot.service import codes, settings as earn_settings, wallet_of

    w = wallet_of(db, member)
    accepted = codes(earn_settings(db))
    if w is None:
        return (f"Trading from Telegram is for the Banter group. Connect and verify your Hyperliquid wallet at {SITE}/earn first "
                f"(your wallet signs a message; no keys are ever shared).")
    seen = (w.referral_code_seen or "").upper()
    if w.referral_status == "verified" and seen in accepted:
        return None
    if w.referral_status in ("pending", "unverified"):
        return (f"Hyperliquid hasn't confirmed your wallet's referral code yet. Recheck it at {SITE}/earn, then try again. "
                f"Trading from Telegram is for accounts that joined with the Banter code ({accepted[0]}).")
    return (f"Trading from Telegram is only for Hyperliquid accounts that joined with the Banter group's referral code "
            f"({accepted[0]}). Hyperliquid's record for your wallet shows {seen or 'no referral code'}, so new trades are off. "
            f"Closing and managing any position you already have still works.")


def recheck_referral(db: Session, member: Member) -> None:
    """Asks Hyperliquid again (rate-limited per member in tradingbot.service), so a member who just joined with the code
    doesn't wait for the next scheduled check. Best effort: an outage keeps the last proved result."""
    from app.tradingbot.hyperliquid import InfoClient
    from app.tradingbot.service import AccessError, recheck

    try:
        recheck(db, member, InfoClient())
    except (AccessError, Exception):  # noqa: BLE001 - never stops the command; the gate then answers from what's on record
        db.rollback()


def telegram_live(db: Session) -> bool:
    """Manual mainnet orders from Telegram: off unless the owner turns them on."""
    cfg = (signals.settings(db).strategies or {}).get("telegram_trading") or {}
    return bool(cfg.get("live")) if isinstance(cfg, dict) else False


def gate_signal_enter(db: Session) -> bool:
    """Does the Banter referral gate also apply to signal entries from Telegram (a signal's ENTER button and /enter)? On unless
    the owner sets `telegram_trading.gate_signal_enter: false` (Admin → Trading OS, strategies JSON). The website's ENTER is
    never gated. Manual /open is always gated."""
    cfg = (signals.settings(db).strategies or {}).get("telegram_trading") or {}
    return bool(cfg.get("gate_signal_enter", True)) if isinstance(cfg, dict) else True


def mode_gate(db: Session, acc: TrAccount) -> str | None:
    if acc.mode == "LIVE" and not telegram_live(db):
        return ("Manual orders from Telegram run on PAPER and TESTNET for now: real-money (mainnet) orders from Telegram aren't "
                "switched on. Switch with /settings mode testnet or /settings mode paper. Signals you ENTER keep working as before.")
    return None


# ---------------------------------------------------------------- keys never belong in a chat
@functools.cache
def _bip39() -> frozenset[str]:
    from importlib import resources

    try:
        text = resources.files("eth_account.hdaccount").joinpath("wordlist/english.txt").read_text(encoding="utf-8")
        return frozenset(w.strip() for w in text.split() if w.strip())
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return frozenset()


_HEX64 = re.compile(r"(?<![0-9a-fA-F])(?:0x)?[0-9a-fA-F]{64}(?![0-9a-fA-F])")


def looks_secret(text: str, private: bool = True) -> bool:
    """A recovery phrase (12 or more BIP-39 words in a row), anywhere; a private key (64 hex digits) in a private chat only:
    in groups the same shape is usually a transaction hash, which people share all the time."""
    if not text:
        return False
    if private and _HEX64.search(text):
        return True
    words = _bip39()
    if not words:
        return False
    tokens = re.findall(r"[A-Za-z]+", text.lower())
    run = best = 0
    for t in tokens:
        run = run + 1 if t in words else 0
        best = max(best, run)
    return best >= 12


SECRET_WARNING = ("⚠️ That looked like a private key or a recovery phrase, so I deleted it. Never send it to anyone, Price Chat "
                  "included: we will never ask for it. If it was real, move your funds to a new wallet now.\n\n"
                  f"To trade from here, connect at {SITE}/earn: your wallet approves an API wallet for trading. Price Chat only "
                  "places, cancels and changes orders with it and never sends withdrawals or transfers. You can revoke it on "
                  "Hyperliquid at any time.")


# ---------------------------------------------------------------- /open: parsing and planning
@dataclass
class OpenRequest:
    coin: str
    side: str  # LONG | SHORT
    margin: float
    leverage: int
    stop: float | None = None
    tp: float | None = None


OPEN_USAGE = "Usage: /open BTC long 100 5x [sl 60000] [tp 70000]  (coin, long or short, margin in USD, leverage)"


def _num(s: str) -> float | None:
    try:
        v = float(s.replace("$", "").replace(",", ""))
    except ValueError:
        return None
    return v if v > 0 else None


def parse_open(args: list[str]) -> OpenRequest | str:
    """`/open BTC long 100 5x sl 60000 tp 70000`: an OpenRequest, or what's wrong with it."""
    words = [a.strip() for a in args if a.strip()]
    if len(words) < 4:
        return OPEN_USAGE
    coin = words[0].lstrip("$").upper()
    if not re.fullmatch(r"[A-Z0-9]{1,15}", coin):
        return OPEN_USAGE
    side = {"long": "LONG", "buy": "LONG", "short": "SHORT", "sell": "SHORT"}.get(words[1].lower())
    if side is None:
        return "Say long or short. " + OPEN_USAGE
    margin = _num(words[2])
    lev_s = words[3].lower().rstrip("x")
    if margin is None:
        return "The margin is an amount in USD, like 100. " + OPEN_USAGE
    if not lev_s.isdigit() or not 1 <= int(lev_s) <= 100:
        return "The leverage is a whole number, like 5x. " + OPEN_USAGE
    req = OpenRequest(coin, side, round(margin, 2), int(lev_s))
    rest = words[4:]
    while rest:
        key = rest[0].lower()
        if key not in ("sl", "stop", "tp", "target") or len(rest) < 2 or _num(rest[1]) is None:
            return "After the leverage only sl PRICE and tp PRICE are understood. " + OPEN_USAGE
        if key in ("sl", "stop"):
            req.stop = _num(rest[1])
        else:
            req.tp = _num(rest[1])
        rest = rest[2:]
    return req


def liquidation_price(side: str, entry: float, leverage: int, market_max_lev: int) -> float:
    """Hyperliquid's isolated-margin liquidation price (their docs): price - side * margin_available / size / (1 - l * side),
    with l = 1 / (2 * the market's max leverage) the maintenance rate. An estimate: funding and fees move it a little."""
    s = 1 if side == "LONG" else -1
    lev = max(1, leverage)
    mm_rate = 1 / (2 * max(1, market_max_lev))
    available = entry / lev - entry * mm_rate  # per unit of size: initial margin less maintenance
    return max(0.0, entry - s * available / (1 - mm_rate * s))


@dataclass
class Manual:
    plan: risk.Plan
    liq: float
    funding_h_usd: float


def manual_plan(req: OpenRequest, *, mark: float, market_max_lev: int, sz_decimals: int, funding_h: float, equity: float,
                acc_max_leverage: int, max_loss_usd: float, max_allocation_pct: float, max_positions: int, open_positions: int,
                daily_pnl: float, daily_loss_limit: float, paused: bool, global_paused: bool, allowed: list[str] | None = None,
                trades_today: int = 0, max_trades_today: int = risk.HARD_MAX_TRADES_PER_DAY, cooldown_left_min: float = 0.0) -> Manual:
    """The member's own sizing (margin x leverage), held to their limits and the system's: leverage, margin as a share of
    equity, loss at the stop, positions, daily loss, Hyperliquid's minimum, and a stop that triggers before liquidation.
    Without a stop given, it goes where the loss reaches the member's max loss per trade (or just before liquidation)."""
    p = risk.Plan(ok=False, coin=req.coin, side=req.side)
    reasons = p.reasons
    long = req.side == "LONG"
    cap = min(acc_max_leverage, market_max_lev, risk.HARD_MAX_LEVERAGE)
    if paused:
        reasons.append("STOP NEW TRADES is on (/resume)")
    if global_paused:
        reasons.append("trading is paused for everyone right now")
    if allowed and req.coin.upper() not in allowed:  # the same limits as signal trades (risk.plan_trade)
        reasons.append(f"{req.coin} isn't in your allowed contracts")
    day_cap = min(max_trades_today, risk.HARD_MAX_TRADES_PER_DAY)
    if trades_today >= day_cap:
        reasons.append(f"{trades_today} bot trades opened today (limit {day_cap} a day)")
    if cooldown_left_min > 0:
        reasons.append(f"cooling down on {req.coin}: {cooldown_left_min:.0f} min left since your last {req.coin} trade closed")
    if req.leverage > cap:
        why = ("your limit" if cap == acc_max_leverage else "Price Chat's cap" if cap == risk.HARD_MAX_LEVERAGE else f"{req.coin}'s maximum")
        reasons.append(f"{req.leverage}x is over {why} of {cap}x (/settings leverage)")
    if open_positions >= min(max_positions, risk.HARD_MAX_POSITIONS):
        reasons.append(f"{open_positions} positions open already (your limit)")
    if daily_pnl <= -abs(daily_loss_limit):
        reasons.append("your daily loss limit is reached")
    max_margin = equity * max_allocation_pct / 100
    if req.margin > max_margin + 1e-9:
        reasons.append(f"${req.margin:,.2f} margin is over your limit of {max_allocation_pct:g}% of ${equity:,.2f} (${max_margin:,.2f})")
    lev = max(1, min(req.leverage, cap))
    entry = mark
    notional = req.margin * lev
    size = risk.round_size(notional / entry, sz_decimals) if entry > 0 else 0.0
    if size * entry < MIN_NOTIONAL:
        reasons.append(f"a ${size * entry:,.2f} position is under Hyperliquid's ${MIN_NOTIONAL:.0f} minimum")
    liq = liquidation_price(req.side, entry, lev, market_max_lev)
    costs = entry * (2 * risk.TAKER_FEE + 2 * risk.SLIPPAGE)  # per unit: fees both ways and slippage
    edge = liq * (1 + LIQ_BUFFER) if long else liq * (1 - LIQ_BUFFER)
    if req.stop is not None:
        stop = req.stop
        if (long and stop >= entry) or (not long and stop <= entry):
            reasons.append(f"a {req.side.lower()} stop must be {'below' if long else 'above'} the price ({market.fmt_price(entry)})")
        elif (long and stop <= edge) or (not long and stop >= edge):
            reasons.append(f"the stop at {market.fmt_price(stop)} is past the liquidation price (≈ {market.fmt_price(liq)}): move it closer")
    else:
        per_unit = max_loss_usd / size - costs if size > 0 else 0.0
        stop = entry - per_unit if long else entry + per_unit
        if per_unit <= 0:
            reasons.append(f"this size can't keep a loss under your ${max_loss_usd:,.2f} max per trade: use less margin or leverage")
        if (long and stop <= edge) or (not long and stop >= edge):
            stop = edge  # the max loss would be past liquidation: the stop sits just before it
    stop = risk.round_px(max(stop, 0.0), sz_decimals)
    loss = abs(entry - stop) * size + costs * size
    if size > 0 and loss > max_loss_usd + 0.01 and not any("stop" in r for r in reasons):
        reasons.append(f"the stop at {market.fmt_price(stop)} risks ${loss:,.2f}, over your ${max_loss_usd:,.2f} max per trade "
                       "(/settings maxloss): use a closer stop or less margin")
    r_dist = abs(entry - stop)
    if req.tp is not None:
        tp1 = req.tp
        if (long and tp1 <= entry) or (not long and tp1 >= entry):
            reasons.append(f"a {req.side.lower()} target must be {'above' if long else 'below'} the price ({market.fmt_price(entry)})")
    else:
        tp1 = entry + 2 * r_dist if long else entry - 2 * r_dist  # twice the risk
    tp1 = risk.round_px(max(tp1, 0.0), sz_decimals)
    tp2 = risk.round_px(max(risk.tp2_of(entry, tp1), 0.0), sz_decimals)
    p.entry, p.stop, p.tp1, p.tp2, p.size, p.leverage = entry, stop, tp1, tp2, size, lev
    p.notional, p.margin = round(size * entry, 4), round(size * entry / lev, 4)
    p.max_loss_usd, p.est_fees = round(loss, 4), round(size * entry * 2 * risk.TAKER_FEE, 4)
    p.ok = not reasons
    return Manual(p, liq, round(size * entry * funding_h, 4))


# ---------------------------------------------------------------- manual previews and confirmations
def _next_manual_number(db: Session) -> int:
    return int(db.scalar(select(func.coalesce(func.min(TsSignal.number), 0)).where(TsSignal.number < 0)) or 0) - 1


def _anchor(db: Session, member: Member, req: OpenRequest, plan: risk.Plan) -> TsSignal:
    """The private row a manual trade hangs on: status MANUAL, a negative number (public numbering is max + 1 of positive ones).
    The member's spare anchor (no position ever opened on it, no preview still waiting on it) is reused; a new number that
    another preview took a moment earlier is retried, never an error."""
    fields = dict(coin=req.coin, side=req.side, entry_lo=plan.entry, entry_hi=plan.entry, stop=plan.stop, target=plan.tp1,
                  lev_lo=plan.leverage, lev_hi=plan.leverage, rr=round(abs(plan.tp1 - plan.entry) / max(1e-12, abs(plan.entry - plan.stop)), 2),
                  expires_at=utcnow() + timedelta(days=1))
    spare = db.scalar(select(TsSignal).where(
        TsSignal.status == MANUAL, TsSignal.number < 0, TsSignal.approved_by == f"m:{member.id}",
        ~select(TrPosition.id).where(TrPosition.signal_id == TsSignal.id).exists(),
        ~select(TrIntent.id).where(TrIntent.signal_id == TsSignal.id, TrIntent.status == "PENDING", TrIntent.expires_at > utcnow()).exists())
        .order_by(TsSignal.id).limit(1))
    if spare is not None:
        for k, v in fields.items():
            setattr(spare, k, v)
        db.flush()
        return spare
    for _ in range(5):
        sig = TsSignal(number=_next_manual_number(db), timeframe="man", strategy="manual", status=MANUAL,
                       reason="The member's own trade from Telegram (/open)", analysis={"manual": True, "member": member.id},
                       approved_by=f"m:{member.id}", **fields)
        try:
            with db.begin_nested():
                db.add(sig)
                db.flush()
            return sig
        except IntegrityError:  # another preview took that number between the read and the insert
            continue
    raise execution.TradeError("Busy right now: send /open again.")


def _sizing(db: Session, member: Member, acc: TrAccount, req: OpenRequest, ex: Any = None) -> tuple[Manual, Any]:
    execution.live_gate(db, member, acc, ex)  # LIVE enabled, allowed, and the API wallet still listed: as for every entry
    m = market.for_mode(acc.mode).market(req.coin)  # TESTNET: testnet's own price, szDecimals and max leverage
    req.coin = m.coin
    ex = ex or execution.executor(db, acc, req.coin)
    if db.scalar(select(TrPosition.id).where(TrPosition.account_id == acc.id, TrPosition.coin == req.coin,
                                             TrPosition.status.in_(execution.HELD_STATES)).limit(1)):
        raise execution.TradeError(f"You already have a bot position in {req.coin}. Manage it with /sl, /tp or /close.")
    if acc.mode != "PAPER" and any(p["position"]["coin"] == req.coin and float(p["position"]["szi"]) != 0
                                   for p in ex.state().get("assetPositions", [])):
        raise execution.TradeError(f"You hold a {req.coin} position on Hyperliquid already. The bot won't touch or add to it.")
    per_day, cooldown = execution.style_limits(acc)
    man = manual_plan(req, mark=m.mark, market_max_lev=m.max_leverage, sz_decimals=m.sz_decimals, funding_h=m.funding, equity=ex.equity(),
                      acc_max_leverage=acc.max_leverage, max_loss_usd=acc.max_loss_per_trade_usd, max_allocation_pct=acc.max_allocation_pct,
                      max_positions=acc.max_positions, open_positions=execution.open_count(db, acc), daily_pnl=execution.day_loss_basis(db, acc),
                      daily_loss_limit=acc.daily_loss_limit_usd, paused=acc.paused, global_paused=signals.settings(db).paused,
                      allowed=[c.strip().upper() for c in (acc.allowed_coins or "").split(",") if c.strip()],
                      trades_today=execution.trades_today(db, acc), max_trades_today=per_day,
                      cooldown_left_min=execution.cooldown_left(db, acc, req.coin, cooldown))
    if not man.plan.ok:
        raise execution.TradeError("Can't open: " + "; ".join(man.plan.reasons) + ".")
    # Engine v2's portfolio checks, as for signal entries: advisory (recorded on the plan) unless the owner set "enforce"
    execution._portfolio_checks(db, acc, SimpleNamespace(coin=req.coin, analysis={}), m, man.plan, ex)  # type: ignore[arg-type]
    return man, ex


def preview_open(db: Session, member: Member, req: OpenRequest, ex: Any = None) -> TrIntent:
    acc = execution.account(db, member)
    why = mode_gate(db, acc)
    if why:
        raise execution.TradeError(why)
    man, _ = _sizing(db, member, acc, req, ex)
    sig = _anchor(db, member, req, man.plan)
    req_d = {"coin": req.coin, "side": req.side, "margin": req.margin, "leverage": req.leverage, "stop": req.stop, "tp": req.tp}
    it = TrIntent(account_id=acc.id, signal_id=sig.id, token=secrets.token_hex(8), mode=acc.mode,
                  plan=man.plan.as_dict() | {"manual": True, "liq": man.liq, "funding_h_usd": man.funding_h_usd, "request": req_d},
                  expires_at=utcnow() + INTENT_TTL)
    db.add(it)
    signals.audit(db, f"m:{member.id}", "manual_preview", sig, mode=acc.mode, plan=it.plan)
    db.commit()
    return it


MODE_NOTE = {"PAPER": "PAPER (simulated on real prices)", "TESTNET": "TESTNET (Hyperliquid testnet, test funds)", "LIVE": "LIVE · REAL MONEY"}


def preview_open_text(it: TrIntent) -> str:
    p, f = it.plan, market.fmt_price
    lines = [f"🧾 <b>{esc(MODE_NOTE.get(it.mode, it.mode))}</b>", f"<b>{esc(p['coin'])} {p['side']}</b> · your own trade, not a signal", "",
             f"Entry ≈ {f(p['entry'])} (market order)",
             f"Margin ${p['margin']:,.2f} · {p['leverage']}x isolated → position ${p['notional']:,.2f} ({p['size']:g} {esc(p['coin'])})",
             f"🛑 Stop {f(p['stop'])} · loss at the stop ≈ ${p['max_loss_usd']:,.2f} incl. fees",
             f"✅ TP1 {f(p['tp1'])} (half) · TP2 {f(p['tp2'])}",
             f"💀 Liquidation ≈ {f(p['liq'])} (estimate; the stop triggers first)",
             f"Fees ≈ ${p['est_fees']:,.2f} (open + close, {risk.TAKER_FEE * 100:.3f}% taker) · funding ≈ ${p['funding_h_usd']:+,.4f}/h", "",
             "Confirm within 5 minutes." + (" <b>REAL MONEY.</b>" if it.mode == "LIVE" else "")]
    return "\n".join(lines)


def confirm_open(db: Session, member: Member, token: str, ex: Any = None) -> TrPosition:
    """The confirmed manual trade, re-sized at the current price with the same checks, never bigger than previewed."""
    it = db.scalar(select(TrIntent).where(TrIntent.token == token))
    acc = execution.account(db, member)
    if it is None or it.account_id != acc.id or not (it.plan or {}).get("manual"):
        raise execution.TradeError("That confirmation isn't yours or doesn't exist.")
    if it.status != "PENDING":
        raise execution.TradeError("Already handled.")
    if execution._aware(it.expires_at) < utcnow():
        it.status = "EXPIRED"
        db.commit()
        raise execution.TradeError("The preview expired. Send /open again for a fresh one.")
    if it.mode != acc.mode:
        raise execution.TradeError("Your trading mode changed since the preview. Send /open again.")
    why = mode_gate(db, acc) or entry_gate(db, member)
    if why:
        raise execution.TradeError(why)
    if acc.paused or signals.settings(db).paused:
        raise execution.TradeError("New trades are stopped (STOP NEW TRADES). Nothing was sent.")
    it.status = "CONFIRMED"  # single use, before anything is sent
    db.commit()
    rq = (it.plan or {}).get("request") or {}
    req = OpenRequest(rq["coin"], rq["side"], float(rq["margin"]), int(rq["leverage"]), rq.get("stop"), rq.get("tp"))
    try:
        man, ex = _sizing(db, member, acc, req, ex)
    except execution.TradeError as e:
        raise execution.TradeError(f"{str(e).rstrip('.')}. Nothing was sent.") from e
    plan = man.plan
    seen = float((it.plan or {}).get("size") or 0)
    if seen and plan.size > seen:
        k = seen / plan.size
        plan.size, plan.notional, plan.margin, plan.max_loss_usd, plan.est_fees = (seen, plan.notional * k, plan.margin * k,
                                                                                  plan.max_loss_usd * k, plan.est_fees * k)
    sig = db.get(TsSignal, it.signal_id)
    assert sig is not None
    sig.entry_lo = sig.entry_hi = plan.entry
    sig.stop, sig.target = plan.stop, plan.tp1
    db.commit()
    pos = execution.open_position(db, acc, sig, plan, "manual", ex)
    signals.audit(db, f"m:{member.id}", "manual_open", sig, position=pos.id, status=pos.status, mode=pos.mode)
    db.commit()
    return pos


def cancel_previews(db: Session, member: Member) -> int:
    acc = execution.account(db, member)
    rows = db.scalars(select(TrIntent).where(TrIntent.account_id == acc.id, TrIntent.status == "PENDING")).all()
    for it in rows:
        it.status = "CANCELLED"
    db.commit()
    return len(rows)


# ---------------------------------------------------------------- reading
def market_text(coin: str) -> str:
    m = market.data().market(coin)
    f = market.fmt_price
    oi_usd = m.open_interest * m.mark
    spread = f"{m.spread_bps:.1f} bps" if m.spread_bps is not None else "n/a"
    return "\n".join([f"<b>{esc(m.coin)}</b> perp on Hyperliquid", f"Price {f(m.mark)} ({m.change_24h:+.2f}% 24h)",
                      f"Funding {m.funding * 100:+.4f}%/h ({m.funding * 24 * 365 * 100:+.1f}% a year)",
                      f"Open interest ${oi_usd:,.0f} · 24h volume ${m.day_volume:,.0f}",
                      f"Max leverage {m.max_leverage}x (Price Chat caps it at {risk.HARD_MAX_LEVERAGE}x) · spread {spread}",
                      "", f"Trade it privately: /open {esc(m.coin)} long 50 3x"])


def bot_positions_text(db: Session, acc: TrAccount) -> str:
    rows = db.scalars(select(TrPosition).where(TrPosition.account_id == acc.id, TrPosition.status.in_(execution.HELD_STATES),
                                               TrPosition.size > 0).order_by(TrPosition.id)).all()
    if not rows:
        return f"No open bot positions ({acc.mode})."
    out, f = [f"<b>Bot positions · {acc.mode}</b>"], market.fmt_price
    for p in rows:
        try:
            pm = market.for_mode(p.mode).market(p.coin)  # a testnet position at testnet's price
            mark, mm = pm.mark, pm.max_leverage
        except market.MarketError:
            mark, mm = 0.0, 0
        long = p.side == "LONG"
        upnl = ((mark - (p.entry_px or 0)) * p.size * (1 if long else -1)) if mark else 0.0
        liq = liquidation_price(p.side, p.entry_px or 0, p.leverage, mm) if mm and p.entry_px else 0.0
        out.append(f"#{p.id} <b>{esc(p.coin)}</b> {p.side} {p.size:g} @ {f(p.entry_px or 0)} · {p.leverage}x · uPnL ${upnl:+,.2f}"
                   + (f" · liq ≈ {f(liq)}" if liq else "") + f"\n   🛑 {f(p.stop)} · ✅ {f(p.tp1)} / {f(p.tp2)}"
                   + (" · ⚠️ PROTECTION FAILED" if p.status == "PROTECTION_FAILED" else ""))
    return "\n".join(out)


def balance_text(db: Session, acc: TrAccount) -> str:
    if acc.mode == "PAPER":
        return f"<b>PAPER balance</b> (simulated): ${execution.paper_equity(db, acc):,.2f}\nOpen positions: {execution.open_count(db, acc)}"
    if not acc.master_address:
        return f"No Hyperliquid account connected for {acc.mode}. Connect at {SITE}/earn."
    from app.tradingos import exchange as xch

    try:
        b = xch.balances(acc.mode, acc.master_address)
    except Exception:  # noqa: BLE001
        return f"Couldn't read your {acc.mode} balance from Hyperliquid right now. Try again in a moment."
    return "\n".join([f"<b>{acc.mode} balance</b> · {esc(acc.master_address[:6])}…{esc(acc.master_address[-4:])}",
                      f"Perps account value ${b['perps']:,.2f}", f"Spot USDC ${b['spot_usdc']:,.2f}",
                      f"Available to trade ${b['trading']:,.2f}" + (" (unified account)" if b.get("unified") else ""),
                      f"Open bot positions: {execution.open_count(db, acc)}"])


def orders_text(db: Session, acc: TrAccount) -> str:
    rows = db.execute(select(TrOrder, TrPosition).join(TrPosition, TrOrder.position_id == TrPosition.id)
                      .where(TrPosition.account_id == acc.id, TrPosition.status.in_(execution.HELD_STATES), TrOrder.status == "RESTING")
                      .order_by(TrOrder.id)).all()
    pending = db.scalars(select(TrIntent).where(TrIntent.account_id == acc.id, TrIntent.status == "PENDING",
                                                TrIntent.expires_at > utcnow())).all()
    if not rows and not pending:
        return f"No open orders ({acc.mode})."
    label = {"sl": "🛑 stop-loss", "tp1": "✅ TP1", "tp2": "✅ TP2"}
    out = [f"<b>Open orders · {acc.mode}</b>"]
    for o, p in rows:
        out.append(f"order {o.id}: {label.get(o.kind, o.kind)} {esc(p.coin)} {'buy' if o.is_buy else 'sell'} {o.size:g} @ "
                   f"{market.fmt_price(o.price)} · position #{p.id}")
    if pending:
        out.append(f"{len(pending)} preview(s) waiting for your Confirm: /cancel drops them")
    out.append("/cancel ORDER cancels a target · stops can only be moved (/sl) or closed (/close)")
    return "\n".join(out)


def cancel_order(db: Session, member: Member, order_id: int, ex: Any = None) -> str:
    """Cancels a take-profit order. Never a stop-loss: every position keeps one (move it with /sl, or /close)."""
    acc = execution.account(db, member)
    row = db.execute(select(TrOrder, TrPosition).join(TrPosition, TrOrder.position_id == TrPosition.id)
                     .where(TrOrder.id == order_id, TrPosition.account_id == acc.id)).first()
    if row is None:
        raise execution.TradeError("No such order of yours. /orders lists them.")
    o, p = row
    if o.status != "RESTING":
        raise execution.TradeError(f"Order {o.id} is {o.status.lower()}, not open.")
    if o.kind == "sl":
        raise execution.TradeError("A stop-loss can't be cancelled: every position keeps one. Move it with /sl, or close with /close.")
    if p.mode == "PAPER":
        raise execution.TradeError("Paper targets are simulated: move them with /tp instead.")
    ex = ex or execution.executor(db, acc, p.coin, p.mode)
    try:
        ex.cancel(p.coin, o.oid)
    except Exception as e:  # noqa: BLE001
        raise execution.TradeError(f"Hyperliquid didn't cancel it ({type(e).__name__}). Nothing changed.") from e
    o.status = "CANCELLED"
    signals.audit(db, f"m:{member.id}", "order_cancelled", db.get(TsSignal, p.signal_id), order=o.id, kind=o.kind, position=p.id)
    db.commit()
    return f"Cancelled order {o.id} ({o.kind.upper()} on {p.coin} #{p.id}). The stop-loss stays."


# ---------------------------------------------------------------- settings
SETTING_KEYS = {"leverage": ("max_leverage", int), "maxloss": ("max_loss_per_trade_usd", float), "margin": ("max_allocation_pct", float),
                "positions": ("max_positions", int), "dailyloss": ("daily_loss_limit_usd", float)}


def settings_text(db: Session, member: Member, acc: TrAccount) -> str:
    gate = entry_gate(db, member)
    return "\n".join([f"<b>Your trading settings</b> · mode {acc.mode}",
                      f"Max leverage {acc.max_leverage}x (cap {risk.HARD_MAX_LEVERAGE}x) · /settings leverage 3",
                      f"Max loss per trade ${acc.max_loss_per_trade_usd:,.2f} · /settings maxloss 10",
                      f"Max margin per trade {acc.max_allocation_pct:g}% of equity · /settings margin 10",
                      f"Max open positions {acc.max_positions} · /settings positions 2",
                      f"Daily loss limit ${acc.daily_loss_limit_usd:,.2f} · /settings dailyloss 20",
                      "Mode: /settings mode paper · /settings mode testnet (live is switched on at the website)",
                      f"New trades: {'STOPPED (/resume)' if acc.paused else 'on (/pause)'}",
                      f"Telegram trading: {'open (Banter referral verified)' if gate is None else 'not available: ' + esc(gate)}",
                      f"Mainnet orders from /open: {'on' if telegram_live(db) else 'off (owner approval)'}"])


def change_setting(db: Session, member: Member, words: list[str]) -> str:
    from app.tradingos import accounts

    if len(words) != 2:
        return "Usage: /settings leverage 3 · maxloss 10 · margin 10 · positions 2 · dailyloss 20 · mode paper|testnet"
    key, val = words[0].lower(), words[1].lower()
    acc = execution.account(db, member)
    try:
        if key == "mode":
            mode = {"paper": "PAPER", "testnet": "TESTNET"}.get(val)
            if mode is None:
                return "Telegram switches between paper and testnet. Live is switched on at the website, with its checks."
            accounts.update(db, member, {"mode": mode})
            return f"Mode is now {mode}." + (" Orders go to Hyperliquid's testnet with test funds." if mode == "TESTNET" else "")
        if key not in SETTING_KEYS:
            return "Unknown setting. Send /settings to see them."
        field, kind = SETTING_KEYS[key]
        try:
            number = kind(val.rstrip("x%").lstrip("$"))
        except ValueError:
            return f"{key} needs a number."
        if field == "max_leverage" and number > risk.HARD_MAX_LEVERAGE:
            return f"Leverage is capped at {risk.HARD_MAX_LEVERAGE}x."
        accounts.update(db, member, {field: number})
    except accounts.AccountError as e:
        db.rollback()
        return esc(str(e))
    db.refresh(acc)
    signals.audit(db, f"m:{member.id}", "settings_telegram", None, key=key, value=val)
    db.commit()
    return "Saved.\n\n" + settings_text(db, member, acc)


# ---------------------------------------------------------------- /enter
_SIG_NO = re.compile(r"#\s?(\d{1,6})\b")


def signal_from(args: list[str], replied_text: str = "") -> int | None:
    """The signal number from "/enter 12", "/enter #012", or the card being replied to ("🟢 $SOL LONG · #012")."""
    for a in args:
        m = re.fullmatch(r"#?(\d{1,6})", a.strip())
        if m:
            return int(m.group(1))
    m = _SIG_NO.search(replied_text or "")
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- the commands (private chat), called by bot.py
def _confirm_buttons(prefix_yes: str, prefix_no: str, token: str) -> list[list[dict[str, str]]]:
    return [[{"text": "✅ Confirm", "callback_data": f"{prefix_yes}:{token}"}, {"text": "Cancel", "callback_data": f"{prefix_no}:{token}"}]]


def _only_position(db: Session, acc: TrAccount) -> int | None:
    ids = list(db.scalars(select(TrPosition.id).where(TrPosition.account_id == acc.id, TrPosition.status.in_(execution.CLOSABLE),
                                                      TrPosition.size > 0)))
    return ids[0] if len(ids) == 1 else None


async def gate_for_entry(db: Session, member: Member) -> str | None:
    """entry_gate, asking Hyperliquid again first when the record doesn't show the code yet."""
    import asyncio

    why = entry_gate(db, member)
    if why is None:
        return None
    await asyncio.to_thread(recheck_referral, db, member)
    return entry_gate(db, member)


async def send_signal_preview(db: Session, client: Any, chat_id: int, member: Member, sig: TsSignal) -> str | None:
    """/enter: the signal's private preview with Confirm / Cancel, exactly as its ENTER button sends it."""
    import asyncio

    why = await gate_for_entry(db, member) if gate_signal_enter(db) else None
    if why:
        return esc(why)
    try:
        it = await asyncio.to_thread(execution.preview, db, member, sig)
    except execution.TradeError as e:
        return f"#{sig.number:03d} not entered: {esc(str(e)[:300])}"
    await client.send_message(chat_id, esc(execution.preview_text(sig, it)), _confirm_buttons("cf", "cx", it.token), html=True)
    return None


def _sl_tp_args(nums: list[str], only: int | None, open_ids: set[int] | None = None) -> tuple[int | None, list[float]]:
    """"/sl 12 61000" -> (12, [61000]); "/sl 61000" -> (the only open position, [61000]). The first number is a position only
    when it is one of the member's open positions (or written #12): "/tp 64000 66000" is two targets, not position 64000."""
    vals = [float(_num(x) or 0) for x in nums]
    first = nums[0].lstrip("#") if nums else ""
    if len(nums) >= 2 and first.isdigit() and (nums[0].startswith("#") or int(first) in (open_ids or set())):
        return int(first), vals[1:]
    return only, vals


BUTTON_TTL_SECONDS = 300  # Confirm buttons that change positions work for 5 minutes, like previews


def _stamp() -> str:
    return format(int(utcnow().timestamp()), "x")


def _fresh(stamp: str) -> bool:
    try:
        return 0 <= utcnow().timestamp() - int(stamp, 16) <= BUTTON_TTL_SECONDS
    except ValueError:
        return False


def _px(v: float) -> str:
    """A price for a button exactly as typed (no rounding to 6 digits: 104123.5 stays 104123.5)."""
    return format(v, ".12g")


async def command(db: Session, client: Any, chat_id: int, member: Member, cmd: str, args: list[str]) -> str | None:
    """One trading command in the member's private chat. Returns the reply (None when a message with buttons was sent)."""
    import asyncio

    acc = execution.account(db, member)
    db.commit()
    if cmd == "/market":
        if not args:
            return "Usage: /market BTC"
        try:
            return market_text(args[0].lstrip("$"))
        except market.MarketError as e:
            return esc(str(e))
    if cmd == "/open":
        req = parse_open(args)
        if isinstance(req, str):
            return esc(req)
        why = mode_gate(db, acc) or await gate_for_entry(db, member)
        if why:
            return esc(why)
        try:
            it = await asyncio.to_thread(preview_open, db, member, req)
        except (execution.TradeError, market.MarketError) as e:  # an unknown coin says so plainly
            return esc(str(e))
        await client.send_message(chat_id, preview_open_text(it), _confirm_buttons("mo", "mn", it.token), html=True)
        return None
    if cmd == "/enter":
        number = signal_from(args)
        if number is None:
            return "Reply to a Price Chat signal with /enter, or send /enter 12 for signal #012."
        sig = db.scalar(select(TsSignal).where(TsSignal.number == number, TsSignal.number > 0))
        if sig is None or sig.published_at is None:
            return f"There's no published signal #{number:03d}."
        return await send_signal_preview(db, client, chat_id, member, sig)
    if cmd == "/balance":
        return await asyncio.to_thread(balance_text, db, acc)
    if cmd == "/orders":
        return orders_text(db, acc)
    if cmd == "/cancel":
        if not args:
            n = cancel_previews(db, member)
            return f"Dropped {n} preview(s). Nothing was traded." if n else "No previews waiting. /cancel ORDER cancels a target order (see /orders)."
        if not args[0].isdigit():
            return "Usage: /cancel (drops your previews) or /cancel ORDER (a target order from /orders)"
        try:
            return esc(await asyncio.to_thread(cancel_order, db, member, int(args[0])))
        except execution.TradeError as e:
            return esc(str(e))
    if cmd == "/closeall":
        n = len(list(db.scalars(select(TrPosition.id).where(TrPosition.account_id == acc.id, TrPosition.status.in_(execution.CLOSABLE),
                                                            TrPosition.size > 0))))
        if not n:
            return "No open bot positions."
        await client.send_message(chat_id, f"Close all {n} bot position(s) at market ({acc.mode})? Reduce-only: nothing else on your account is touched.",
                                  [[{"text": f"✅ Close all {n}", "callback_data": f"ca:all:{_stamp()}"}, {"text": "Cancel", "callback_data": "ca:no"}]], html=True)
        return None
    if cmd in ("/sl", "/tp"):
        nums = [a for a in args if _num(a) is not None]
        if not nums:
            return "Usage: /sl 12 61000 (position, new stop) · /tp 12 64000 [66000] (position, TP1 [TP2])"
        open_ids = set(db.scalars(select(TrPosition.id).where(TrPosition.account_id == acc.id, TrPosition.status.in_(execution.CLOSABLE))))
        pos_id, vals = _sl_tp_args(nums, _only_position(db, acc), open_ids)
        if pos_id is None or not vals:
            return "Which position? /sl 12 61000 (/positions shows the numbers)"
        kw: dict[str, float] = {"stop": vals[0]} if cmd == "/sl" else {"tp1": vals[0], **({"tp2": vals[1]} if len(vals) > 1 else {})}
        try:
            s = await asyncio.to_thread(functools.partial(execution.adjust_protection, db, member, pos_id, dry_run=True, **kw))
        except execution.TradeError as e:
            return esc(str(e))
        except market.MarketError:
            return "Couldn't read the market right now. Nothing changed."
        data = "pr:" + ":".join([_stamp(), str(pos_id), "s" if cmd == "/sl" else "t", *[_px(v) for v in vals]])
        if len(data) > 64:
            return "Those prices are too long. Nothing changed."
        text = "\n".join([f"<b>Change #{pos_id} {esc(s['coin'])} {s['side']}</b> ({acc.mode})", *map(esc, s["changes"]),
                          f"Loss at the stop after this ≈ ${s['loss_at_stop_usd']:,.2f}"])
        await client.send_message(chat_id, text, [[{"text": "✅ Confirm", "callback_data": data}, {"text": "Cancel", "callback_data": "pr:no"}]], html=True)
        return None
    if cmd == "/settings":
        if args:
            return await asyncio.to_thread(change_setting, db, member, args)
        return settings_text(db, member, acc)
    return None


async def callback(db: Session, client: Any, member: Member, uid: int, data: str) -> str:
    """The Confirm / Cancel buttons of the commands above. Returns the message for the member."""
    import asyncio

    kind, _, arg = data.partition(":")
    if kind == "mn":
        execution.cancel_intent(db, member, arg)
        return "Cancelled. Nothing was traded."
    if kind == "mo":
        try:
            pos = await asyncio.to_thread(confirm_open, db, member, arg)
        except execution.TradeError as e:
            return esc(str(e))
        except market.MarketError:
            return "Couldn't read Hyperliquid right now. Nothing was sent."
        f = market.fmt_price
        if pos.status == "OPEN":
            return (f"<b>{pos.mode} · filled</b> #{pos.id} {esc(pos.coin)} {pos.side} {pos.size:g} @ {f(pos.entry_px or 0)} · {pos.leverage}x isolated\n"
                    f"🛑 Stop {f(pos.stop)} · ✅ TP1 {f(pos.tp1)} (half) · TP2 {f(pos.tp2)}\n/positions · /sl · /tp · /close {pos.id}")
        return f"<b>{pos.mode} · {pos.status}</b> {esc(pos.error or '')}\nNew trades were stopped for safety. See /positions."
    if kind == "ca":
        what, _, stamp = arg.partition(":")
        if what != "all":
            return "Nothing was closed."
        if not _fresh(stamp):
            return "That button is out of date (over 5 minutes). Nothing was closed: send /closeall again."
        out = await asyncio.to_thread(execution.close_all, db, member)
        lines = [f"Closed {len(out['closed'])} of {out['requested']}."]
        lines += [f"#{x['id']} {esc(x['coin'])}: {esc(x['error'][:160])}" for x in out["failed"]]
        lines += [f"#{x['id']} {esc(x['coin'])}: partly closed, {x['remaining']:g} left" for x in out["partial"]]
        return "\n".join(lines)
    if kind == "pr":
        if arg == "no":
            return "Nothing changed."
        bits = arg.split(":")
        if not bits or not _fresh(bits[0]):
            return "That button is out of date (over 5 minutes). Nothing changed: send the command again."
        try:
            pos_id, which, vals = int(bits[1]), bits[2], [float(x) for x in bits[3:]]
        except (ValueError, IndexError):
            return "That button is out of date. Nothing changed."
        if not vals:
            return "That button is out of date. Nothing changed."
        kw: dict[str, float] = {"stop": vals[0]} if which == "s" else {"tp1": vals[0], **({"tp2": vals[1]} if len(vals) > 1 else {})}
        try:
            s = await asyncio.to_thread(functools.partial(execution.adjust_protection, db, member, pos_id, **kw))
        except execution.TradeError as e:
            return esc(str(e))
        except market.MarketError:
            return "Couldn't read the market right now. Nothing changed."
        return f"Done: {esc('; '.join(s['changes']))}. Loss at the stop ≈ ${s['loss_at_stop_usd']:,.2f}."
    return "That button is out of date."
