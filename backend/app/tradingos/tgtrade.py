"""Trading on Hyperliquid from Telegram, for Price Chat members: /market, /open, /enter, /positions, /pnl, /close, /closeall,
/sl, /tp, /orders, /cancel, /balance and /settings. bot.py routes the commands here; the trading itself is execution.py's,
with every one of its safety rules (one position per coin, deterministic order ids, fills only as the exchange reports them,
an exchange-side stop on every position or an immediate close, STOP NEW TRADES).

Rules added for Telegram:
  · who: new exposure (/open, and while `telegram_trading.gate_signal_enter` is on, its default, also /enter and a signal's
    ENTER button) only for members whose verified wallet Hyperliquid records as
    referred with Price Chat's Hyperliquid referral code (tradingbot.service.codes). Closing, moving a stop closer, cancelling a target,
    pausing and reading their own account always work, so nobody can be left unable to get out of a position;
  · where: account commands only in the member's private chat with the bot. In a group the bot says so and sends nothing
    private there;
  · how: /open, /long, /short (words in any order, see parse_open) show a compact card (price, amount, leverage, size, fees,
    liquidation, stop); a missing amount or leverage is asked for with buttons, never guessed. The card's Preview makes a
    preview (size, margin, leverage, stop, targets, liquidation estimate, fees, funding), sent only after the member presses
    Confirm within 5 minutes; single use, and never larger than what was previewed. Trading replies carry no footer or ad;
  · which network: PAPER (simulated on real prices) and TESTNET (Hyperliquid's testnet, through the member's own approved
    testnet API wallet). Manual orders (/open, /enter) on mainnet stay off until the owner sets `telegram_trading.live` (Admin → Trading OS,
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

import contextlib
import functools
import math
import re
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from app.tradingos import exchange as xch

SITE = "https://pricechat.live"
MANUAL = "MANUAL"
INTENT_TTL = timedelta(minutes=5)
MIN_NOTIONAL = 10.0  # Hyperliquid's minimum order value
LIQ_BUFFER = 0.003  # a stop must trigger at least 0.3% before the estimated liquidation price
LIMIT_MAX_AWAY = 0.05  # a limit (worst accepted fill) more than 5% from the price is refused as a probable typo
COMMANDS = {"/market", "/open", "/enter", "/closeall", "/sl", "/tp", "/orders", "/cancel", "/balance", "/settings", "/preview", "/mainnet"}
HELP = ("<b>Trading on Hyperliquid (here or in a DM)</b>\n"
        "/market BTC: price, funding, open interest, max leverage\n"
        "/long BTC 50 3x · /short ETH 25 2x · /open BTC long 50 3x (any order: a plain number is the USD amount, 3x the leverage)\n"
        "/long BTC: buttons ask for the amount and the leverage · add tp 79k sl 60k or limit 64.5k\n"
        "You get a card (price, size, fees, liquidation) → Preview → Confirm. Nothing is sent before Confirm.\n"
        "/enter: reply to a Price Chat signal to trade it\n"
        "/preview BTC long 100 5x: a mainnet order preview (read-only) · /mainnet: your mainnet readiness\n"
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
    (checked when the wallet was verified and on every recheck); a link click or anything the member says never does.
    One or two short sentences: the button under it (gate_buttons) carries the link."""

    state = _gate_state(db, member)
    if state is None:
        return None
    kind, w = state
    if kind == "no_wallet":  # Telegram is linked (this member was found by it); their Passport just has no wallet yet
        return ("No wallet is connected to your Kiwi Passport yet. Connect the wallet you use on Hyperliquid (it signs a free "
                "message, no keys are shared), then try again.")
    if kind == "pending":
        return (f"Your wallet …{w.address[-4:]} is connected, but Hyperliquid hasn't confirmed yet that it was made with our "
                "sign-up link. Recheck in your Passport, then try again.")
    return (f"Trading here is for Hyperliquid accounts made with our sign-up link. Hyperliquid's record for your wallet "
            f"…{w.address[-4:]} shows it wasn't, so new trades are off (closing and managing positions still work).")


GATE_WALLET_URL = f"{SITE}/passport#account-wallet"


def _gate_state(db: Session, member: Member) -> tuple[str, Any] | None:
    """None (may trade), or why not: no_wallet, pending (Hyperliquid hasn't confirmed the referral yet) or not_referred.
    Hyperliquid's own record decides."""
    from app.tradingbot import affiliate
    from app.tradingbot.service import codes, settings as earn_settings, wallet_of

    w = wallet_of(db, member)
    if w is None:
        return "no_wallet", None
    if (w.address or "").lower() == affiliate.WALLET.lower():  # the referral's own wallet (the owner): it can't be referred by itself
        return None
    if w.referral_status == "verified" and (w.referral_code_seen or "").upper() in codes(earn_settings(db)):
        return None
    if w.referral_status in ("pending", "unverified"):
        return "pending", w
    return "not_referred", w


def gate_buttons(db: Session, member: Member) -> list[list[dict[str, str]]]:
    """The one button under a gate reply: Connect wallet (no wallet / recheck), or Sign up on Hyperliquid (the tracked
    referral link) for a wallet Hyperliquid records as not made with it."""
    state = _gate_state(db, member)
    kind = state[0] if state else "no_wallet"
    if kind == "not_referred":
        from app.tradingos.bot import tracked_url

        url = tracked_url(db, "gate")
        rows = [[{"text": "Sign up on Hyperliquid", "url": url}]] if url else []
        return rows + [[{"text": "Connect another wallet", "url": GATE_WALLET_URL}]]
    if kind == "no_wallet":  # owner, 2026-11-10: Connect Hyperliquid (the Passport's trade steps), and the wallet row itself
        return [[{"text": "🔗 Connect Hyperliquid", "url": f"{SITE}/passport#trade-title"}],
                [{"text": "Connect wallet", "url": GATE_WALLET_URL}]]
    return [[{"text": "Recheck in Passport", "url": GATE_WALLET_URL}]]


def recheck_referral(db: Session, member: Member) -> None:
    """Asks Hyperliquid again (rate-limited per member in tradingbot.service), so a member who just joined with the code
    doesn't wait for the next scheduled check. Best effort: an outage keeps the last proved result."""
    from app.tradingbot.hyperliquid import InfoClient
    from app.tradingbot.service import AccessError, recheck

    try:
        recheck(db, member, InfoClient())
    except (AccessError, Exception):  # noqa: BLE001 - never stops the command; the gate then answers from what's on record
        db.rollback()


def _cfg(db: Session) -> dict[str, Any]:
    cfg = (signals.settings(db).strategies or {}).get("telegram_trading") or {}
    return cfg if isinstance(cfg, dict) else {}


def telegram_live(db: Session) -> bool:
    """Mainnet execution from Telegram: off unless the owner turns it on (strategies.telegram_trading.live, Admin → Trading
    signals → Settings → Telegram trading, behind a typed confirmation)."""
    return _cfg(db).get("live") is True


def killed(db: Session) -> bool:
    """The owner's emergency switch (strategies.telegram_trading.kill): no new Telegram trade on any network, no Confirm of a
    waiting preview, and no signed mainnet action from Telegram at all. Exchange-side stops stay; the website still closes."""
    return bool(_cfg(db).get("kill"))


def mainnet_allowed(db: Session) -> bool:
    return telegram_live(db) and not killed(db)


@contextlib.contextmanager
def telegram_surface(db: Session) -> Iterator[None]:
    """Every Telegram handler runs inside this: with mainnet execution off (or the emergency switch on), exchange.py refuses
    any signed mainnet action from this request and execution.executor hands out a key-less read-only reader instead. The
    gates below say no first, with a clear message; this is the backstop if any path is ever missed."""
    try:
        ok = mainnet_allowed(db)
    except Exception:  # noqa: BLE001 - settings unreadable: fail closed
        db.rollback()
        ok = False
    with xch.surface("telegram", ok):
        yield


KILLED = ("🛑 Telegram trading is paused by Price Chat right now (emergency stop). No new trades or confirmations; nothing was "
          "sent. Your open positions keep their exchange-side stop-loss, and you can manage them at pricechat.live/earn.")


def entry_off(db: Session) -> str | None:
    return KILLED if killed(db) else None


def gate_signal_enter(db: Session) -> bool:
    """Does the Price Chat referral gate also apply to signal entries from Telegram (a signal's ENTER button and /enter)? On unless
    the owner sets `telegram_trading.gate_signal_enter: false` (Admin → Trading OS, strategies JSON). The website's ENTER is
    never gated. Manual /open is always gated."""
    cfg = (signals.settings(db).strategies or {}).get("telegram_trading") or {}
    return bool(cfg.get("gate_signal_enter", True)) if isinstance(cfg, dict) else True


LIVE_OFF = ("Real-money (mainnet) orders from Telegram aren't switched on. Nothing was sent. Manage LIVE positions at "
            "pricechat.live/earn, or switch Telegram to test funds with /settings mode testnet.")


def live_off(db: Session, mode: str | None) -> str | None:
    """The owner's rule (2026-11-09): NO mainnet execution from Telegram until they approve live trading (telegram_trading.live).
    Every Telegram path that could reach the exchange on mainnet (open, enter, the ENTER button, confirm, close, close all,
    stop/target changes, order cancels) asks this first. PAPER and TESTNET are untouched. The emergency switch also stops
    every mainnet action from Telegram."""
    if mode == "LIVE" and killed(db):
        return KILLED
    return LIVE_OFF if mode == "LIVE" and not telegram_live(db) else None


def live_off_position(db: Session, member: Member, pos_id: int) -> str | None:
    pos = db.get(TrPosition, pos_id)
    acc = execution.account(db, member)
    return live_off(db, pos.mode if pos is not None and pos.account_id == acc.id else acc.mode)


def live_off_intent(db: Session, member: Member, token: str) -> str | None:
    it = db.scalar(select(TrIntent).where(TrIntent.token == token))
    return live_off(db, it.mode if it is not None else execution.account(db, member).mode)


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
    margin: float | None  # USD margin; None: not given yet (asked for with buttons, never guessed)
    leverage: int | None  # None: not given yet (asked for with buttons, never guessed)
    stop: float | None = None
    tp: float | None = None
    limit: float | None = None  # worst fill price for the immediate entry (None: market)


OPEN_USAGE = ("Usage: /long BTC 50 3x · /short ETH 25 2x · /open BTC long 50 3x, in any order (a plain number is the amount "
              "in USD, a number with x the leverage), then optionally tp 79k · sl 60k · limit 64.5k "
              "(limit = the worst price you accept, filled now or not at all)")
EXAMPLE = "/long BTC 50 3x"
SIDE_WORDS = {"LONG": "LONG", "BUY": "LONG", "SHORT": "SHORT", "SELL": "SHORT"}
PRICE_WORDS = {"SL": "stop", "STOP": "stop", "TP": "tp", "TARGET": "tp", "LIMIT": "limit", "LMT": "limit"}
_LEV_RE = re.compile(r"(\d+(?:\.\d+)?)X")
_COIN_RE = re.compile(r"\$?([A-Z0-9]{1,15})")


def _num(s: str) -> float | None:
    """100 · $100 · 1,250 · 79k · 1.2m (a finite amount above zero, else None)"""
    t = s.replace("$", "").replace(",", "").strip().lower()
    mult = 1.0
    if t[-1:] in ("k", "m"):
        mult, t = (1_000.0 if t[-1] == "k" else 1_000_000.0), t[:-1]
    if not re.fullmatch(r"\d+(?:\.\d+)?|\.\d+", t):  # digits only: never "inf", "nan" or "1e9"
        return None
    v = float(t) * mult
    return v if v > 0 and math.isfinite(v) else None


def _lev(s: str) -> int | None:
    t = s.lower().rstrip("x")
    return int(t) if t.isdigit() and 1 <= int(t) <= 100 else None


def parse_open(args: list[str]) -> OpenRequest | str:
    """The words after /open (or /long, /short), in any order and any case: a side (long, short, buy, sell), a coin (BTC,
    $BTC), an amount (a plain number: 50, $50, 1.5k = USD margin) and a leverage (a number with x: 3x), then optional
    "sl PRICE", "tp PRICE" and "limit PRICE" pairs. A plain number is ALWAYS the amount and "Nx" is ALWAYS the leverage, so
    nothing is guessed: two plain numbers, two coins, both sides or an unknown word get a short message saying which.
    The amount and the leverage may be missing (OpenRequest.margin / leverage None): the bot then asks for them with buttons."""
    words = [a.strip() for a in args if a.strip()]
    if not words:
        return OPEN_USAGE
    coin = side = None
    amounts: list[float] = []
    levs: list[int] = []
    prices: dict[str, float] = {}
    i = 0
    while i < len(words):
        w, u = words[i], words[i].upper()
        if u in PRICE_WORDS:
            field = PRICE_WORDS[u]
            v = _num(words[i + 1]) if i + 1 < len(words) else None
            if v is None:
                return f"{w.lower()} needs a price after it, like {w.lower()} 60000."
            if field in prices:
                return f"{w.lower()} is there twice: give one {'stop' if field == 'stop' else 'target' if field == 'tp' else 'limit'}."
            prices[field] = v
            i += 2
            continue
        lm = _LEV_RE.fullmatch(u)
        if u in SIDE_WORDS:
            if side is not None and side != SIDE_WORDS[u]:
                return "Long or short? Say one of them, not both."
            side = SIDE_WORDS[u]
        elif lm:
            if not lm.group(1).isdigit() or not 1 <= int(lm.group(1)) <= 100:
                return f"{esc(w)} isn't a leverage I can use: a whole number with x, like 3x."
            levs.append(int(lm.group(1)))
        elif _num(w) is not None:
            amounts.append(_num(w) or 0.0)
        elif (cm := _COIN_RE.fullmatch(u)) and re.search(r"[A-Z]", cm.group(1)):
            if coin is not None and coin != cm.group(1):
                return f"Two coins ({esc(coin)} and {esc(cm.group(1))}): trade one at a time, like /long {esc(coin)} 50 3x."
            coin = cm.group(1)
        else:
            return f"“{esc(w)}” isn't a coin, an amount or a leverage. Example: {EXAMPLE}"
        i += 1
    if len(levs) > 1:
        return f"Two leverages ({levs[0]}x and {levs[1]}x): give one."
    if len(amounts) > 1:
        a, b = (format(x, "g") for x in amounts[:2])
        return (f"Two amounts ({a} and {b}): a plain number is the amount in USD, and the leverage takes an x. "
                f"For ${a} at {b}x: /long {esc(coin or 'BTC')} {a} {b}x")
    if coin is None:
        return f"Which coin? For example {EXAMPLE}"
    if side is None:
        return f"Long or short? For example /long {esc(coin)} or /short {esc(coin)}"
    margin = round(amounts[0], 2) if amounts else None
    if margin is not None and margin <= 0:
        return "That amount is too small: the amount is in USD, like 50."
    return OpenRequest(coin, side, margin, levs[0] if levs else None, prices.get("stop"), prices.get("tp"), prices.get("limit"))


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
                trades_today: int = 0, max_trades_today: int = risk.HARD_MAX_TRADES_PER_DAY, cooldown_left_min: float = 0.0,
                taker_fee: float = risk.TAKER_FEE) -> Manual:
    """The member's own sizing (margin x leverage), held to their limits and the system's: leverage, margin as a share of
    equity, loss at the stop, positions, daily loss, Hyperliquid's minimum, and a stop that triggers before liquidation.
    Without a stop given, it goes where the loss reaches the member's max loss per trade (or just before liquidation)."""
    p = risk.Plan(ok=False, coin=req.coin, side=req.side)
    reasons = p.reasons
    long = req.side == "LONG"
    if req.margin is None or req.leverage is None:  # never guessed: the bot asks for the amount and the leverage first
        reasons.append("give an amount and a leverage, like 50 3x")
        return Manual(p, 0.0, 0.0)
    # a typed /open names its own leverage: that's the member's choice, held to the coin's Hyperliquid maximum and Price Chat's cap
    # (owner, 2026-11-10). Their saved max_leverage still governs signal trades; every other limit below still applies here.
    cap = min(market_max_lev, risk.HARD_MAX_LEVERAGE)
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
        why = "Price Chat's cap" if cap == risk.HARD_MAX_LEVERAGE else f"{req.coin}'s maximum on Hyperliquid"
        reasons.append(f"{req.leverage}x is over {why} of {cap}x")
    if open_positions >= min(max_positions, risk.HARD_MAX_POSITIONS):
        reasons.append(f"{open_positions} positions open already (your limit)")
    if daily_pnl <= -abs(daily_loss_limit):
        reasons.append("your daily loss limit is reached")
    max_margin = equity * max_allocation_pct / 100
    if req.margin > max_margin + 1e-9:
        reasons.append(f"${req.margin:,.2f} margin is over your limit of {max_allocation_pct:g}% of ${equity:,.2f} (${max_margin:,.2f})")
    lev = max(1, min(req.leverage, cap))
    entry = mark
    if req.limit is not None:
        if (long and req.limit < mark) or (not long and req.limit > mark):
            reasons.append(f"a {req.side.lower()} limit at {market.fmt_price(req.limit)} is {'below' if long else 'above'} the price "
                           f"({market.fmt_price(mark)}): it wouldn't fill now, and resting limit orders aren't available from Telegram yet")
        elif abs(req.limit - mark) / mark > LIMIT_MAX_AWAY:
            reasons.append(f"the limit is more than {LIMIT_MAX_AWAY:.0%} from the price ({market.fmt_price(mark)}): set it closer")
        else:
            entry = req.limit  # sized and risk-checked as if it fills at the worst price accepted
    notional = req.margin * lev
    size = risk.round_size(notional / entry, sz_decimals) if entry > 0 else 0.0
    if size * entry < MIN_NOTIONAL:
        reasons.append(f"a ${size * entry:,.2f} position is under Hyperliquid's ${MIN_NOTIONAL:.0f} minimum")
    liq = liquidation_price(req.side, entry, lev, market_max_lev)
    costs = entry * (2 * taker_fee + 2 * risk.SLIPPAGE)  # per unit: fees both ways and slippage
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
    p.max_loss_usd, p.est_fees = round(loss, 4), round(size * entry * 2 * taker_fee, 4)
    p.limit_px = req.limit if req.limit is not None and entry == req.limit else None
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
    req_d = {"coin": req.coin, "side": req.side, "margin": req.margin, "leverage": req.leverage, "stop": req.stop, "tp": req.tp,
             "limit": req.limit}
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
    lines = [mode_badge(it.mode), f"🧾 <b>{esc(p['coin'])} {p['side']}</b> · your own trade, not a signal", "",
             (f"Entry at {f(p['limit_px'])} or better (limit, filled now or not at all)" if p.get("limit_px")
              else f"Entry ≈ {f(p['entry'])} (market order)"),
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
    why = entry_off(db) or mode_gate(db, acc) or entry_gate(db, member)
    if why:
        raise execution.TradeError(why)
    if acc.paused or signals.settings(db).paused:
        raise execution.TradeError("New trades are stopped (STOP NEW TRADES). Nothing was sent.")
    execution.claim_intent(db, it)  # single use, before anything is sent (a double tap gets "Already handled")
    rq = (it.plan or {}).get("request") or {}
    req = OpenRequest(rq["coin"], rq["side"], float(rq["margin"]), int(rq["leverage"]), rq.get("stop"), rq.get("tp"), rq.get("limit"))
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


# ---------------------------------------------------------------- mainnet previews and readiness (read-only, never an order)
PREVIEW_ONLY = "🔍 <b>MAINNET PREVIEW · nothing can be sent</b>"


def _switch_line(db: Session) -> str:
    if killed(db):
        return "Telegram mainnet execution: OFF (emergency stop is on)"
    return f"Telegram mainnet execution: {'ON' if telegram_live(db) else 'OFF until Price Chat switches it on'}"


def _fees(address: str) -> dict[str, float] | None:
    try:
        return xch.user_fees("LIVE", address)
    except Exception:  # noqa: BLE001 - shown as Price Chat's estimate instead
        return None


def _mainnet_plan(db: Session, acc: TrAccount, req: OpenRequest, m: Any, equity: float, taker: float) -> Manual:
    """The order sized as it would be on mainnet (LIVE positions, LIVE day counts), never raising: refusals are on the plan."""
    live = acc.mode == "LIVE"
    live_open = int(db.scalar(select(func.count()).select_from(TrPosition).where(
        TrPosition.account_id == acc.id, TrPosition.mode == "LIVE", TrPosition.status.in_(execution.HELD_STATES))) or 0)
    per_day, cooldown = execution.style_limits(acc)
    return manual_plan(req, mark=m.mark, market_max_lev=m.max_leverage, sz_decimals=m.sz_decimals, funding_h=m.funding, equity=equity,
                       acc_max_leverage=acc.max_leverage, max_loss_usd=acc.max_loss_per_trade_usd, max_allocation_pct=acc.max_allocation_pct,
                       max_positions=acc.max_positions, open_positions=live_open, daily_pnl=execution.day_loss_basis(db, acc) if live else 0.0,
                       daily_loss_limit=acc.daily_loss_limit_usd, paused=acc.paused, global_paused=signals.settings(db).paused,
                       allowed=[c.strip().upper() for c in (acc.allowed_coins or "").split(",") if c.strip()],
                       trades_today=execution.trades_today(db, acc) if live else 0, max_trades_today=per_day,
                       cooldown_left_min=execution.cooldown_left(db, acc, req.coin, cooldown), taker_fee=taker)


def mainnet_preview(db: Session, member: Member, req: OpenRequest, ex: Any = None, fees: dict[str, float] | None = None) -> str:
    """/preview (and /open on a LIVE account while Telegram mainnet execution is off): the order exactly as it would be sized on
    mainnet for this member (their real Hyperliquid balance and positions, their limits, their own fee rate), with every reason it
    would be refused. Read-only: a key-less reader, no intent, no Confirm button, nothing signed."""
    acc = execution.account(db, member)
    db.commit()
    if not acc.master_address:
        return f"Connect your Hyperliquid wallet in your Passport first ({SITE}/passport#account-wallet): the preview reads its balance (no key needed)."
    m = market.for_mode("LIVE").market(req.coin)
    req.coin = m.coin
    ex = ex or xch.ReadOnlyExecutor("LIVE", acc.master_address)
    try:
        st = ex.state()
        equity = ex.equity()
    except Exception:  # noqa: BLE001
        return "Couldn't read your mainnet account from Hyperliquid right now. Nothing was sent; try again in a moment."
    fees = fees if fees is not None else _fees(acc.master_address)
    taker = fees["taker"] if fees else risk.TAKER_FEE
    man = _mainnet_plan(db, acc, req, m, equity, taker)
    p, f = man.plan, market.fmt_price
    refusals = list(p.reasons)
    if any(x["position"]["coin"] == req.coin and float(x["position"]["szi"]) != 0 for x in st.get("assetPositions", [])):
        refusals.append(f"you hold a {req.coin} position on Hyperliquid already (the bot never adds to one)")
    if not (acc.live_enabled_at and execution.live_allowed(db, member)):
        refusals.append(f"LIVE isn't enabled for your account (website: {SITE}/earn)")
    agent, _ = execution.agent_status(acc)
    if agent != "ACTIVE":
        refusals.append({"NONE": "no mainnet API wallet approved", "REVOKED": "your mainnet API wallet isn't listed on Hyperliquid (revoked or expired)",
                         "UNKNOWN": "couldn't confirm your API wallet with Hyperliquid"}.get(agent, f"API wallet {agent}"))
    gate = entry_gate(db, member)
    if gate:
        refusals.append("Price Chat's Hyperliquid referral isn't verified on your wallet")
    lines = [PREVIEW_ONLY, mode_badge("LIVE", not mainnet_allowed(db)), f"<b>{esc(p.coin)} {p.side}</b> · {acc.master_address[:6]}…{acc.master_address[-4:]} · balance ${equity:,.2f}", ""]
    if p.size > 0:
        lines += [(f"Entry at {f(p.limit_px)} or better (limit)" if p.limit_px else f"Entry ≈ {f(p.entry)} (market)"),
                  f"Margin ${p.margin:,.2f} · {p.leverage}x isolated → position ${p.notional:,.2f} ({p.size:g} {esc(p.coin)})",
                  f"🛑 Stop {f(p.stop)} · loss at the stop ≈ ${p.max_loss_usd:,.2f} incl. fees",
                  f"✅ TP1 {f(p.tp1)} (half) · TP2 {f(p.tp2)}",
                  f"💀 Liquidation ≈ {f(man.liq)} (estimate)",
                  f"Fees ≈ ${p.est_fees:,.2f} at your taker rate {taker * 100:.4f}%" + ("" if fees else " (Price Chat's estimate)")
                  + (f" · referral discount {fees['referral_discount'] * 100:g}%" if fees and fees.get("referral_discount") else "")
                  + f" · funding ≈ ${man.funding_h_usd:+,.4f}/h"]
    lines += ["", ("Would be refused: " + esc("; ".join(refusals)) + ".") if refusals else "Every check passes.", _switch_line(db)]
    signals.audit(db, f"m:{member.id}", "tg_mainnet_preview", None, coin=p.coin, side=p.side, margin=req.margin, leverage=req.leverage,
                  size=p.size, notional=p.notional, refused=refusals[:6], via="telegram")
    db.commit()
    return "\n".join(lines)


def signal_preview_only(db: Session, member: Member, sig: TsSignal) -> str:
    """A signal's ENTER on a LIVE account while Telegram mainnet execution is off: the same sizing as a real preview (inside
    telegram_surface the executor is the key-less reader), shown without a Confirm button and never stored as an intent."""
    try:
        plan, _ = execution._plan(db, member, sig)
    except execution.TradeError as e:
        db.rollback()
        return f"{PREVIEW_ONLY}\n#{sig.number:03d} {esc(sig.coin)} {sig.side}: would be refused: {esc(str(e)[:300])}\n{_switch_line(db)}"
    f = market.fmt_price
    signals.audit(db, f"m:{member.id}", "tg_mainnet_preview", sig, size=plan.size, notional=plan.notional, via="telegram")
    db.commit()
    return "\n".join([PREVIEW_ONLY, f"Signal #{sig.number:03d} <b>{esc(sig.coin)} {sig.side}</b> · LIVE", "",
                      f"Entry ≈ {f(plan.entry)} (market) · size {plan.size:g} · ${plan.notional:,.2f}",
                      f"Leverage {plan.leverage}x isolated · margin ${plan.margin:,.2f}", f"Stop {f(plan.stop)} · TP1 {f(plan.tp1)} · TP2 {f(plan.tp2)}",
                      f"Max planned loss ${plan.max_loss_usd:,.2f} (incl. fees)", "", _switch_line(db),
                      f"To trade it for real now, use the website: {SITE}/earn"])


def mainnet_status(db: Session, member: Member) -> str:
    """/mainnet: is this member's mainnet setup ready? Connectivity, balance, API wallet, LIVE, referral, fee rate and the
    switches. Every line is a read; nothing is signed."""
    acc = execution.account(db, member)
    db.commit()
    out = ["<b>Mainnet readiness</b> (read-only)"]
    try:
        market.for_mode("LIVE").market("BTC")
        out.append("✅ Hyperliquid mainnet reachable")
    except Exception:  # noqa: BLE001
        out.append("❌ Hyperliquid mainnet unreachable right now")
    if not acc.master_address:
        out += [f"❌ No Hyperliquid wallet connected: {SITE}/passport#account-wallet", _switch_line(db)]
        return "\n".join(out)
    a = acc.master_address
    try:
        b = xch.balances("LIVE", a)
        out.append(f"✅ Account {a[:6]}…{a[-4:]} · trading balance ${b['trading']:,.2f}" + (" (unified)" if b.get("unified") else ""))
    except Exception:  # noqa: BLE001
        out.append(f"⚠️ Account {a[:6]}…{a[-4:]} · balance unreadable right now")
    agent, rec = execution.agent_status(acc)
    out.append({"ACTIVE": "✅ API wallet approved and listed on Hyperliquid", "NONE": f"❌ No mainnet API wallet: {SITE}/earn",
                "REVOKED": "❌ API wallet not listed on Hyperliquid (revoked or expired)",
                "UNKNOWN": "⚠️ API wallet: Hyperliquid didn't answer"}.get(agent, agent)
               + (f" · valid until {datetime.fromtimestamp(int(rec['validUntil']) / 1000, tz=timezone.utc):%Y-%m-%d}" if rec and rec.get("validUntil") else ""))
    out.append("✅ LIVE enabled on the website" if acc.live_enabled_at else "❌ LIVE not enabled (website, after the pre-live checks)")
    gate = entry_gate(db, member)
    out.append("✅ Price Chat referral verified on Hyperliquid" if gate is None else "❌ Price Chat referral not verified: /settings shows why")
    fees = _fees(a)
    if fees:
        out.append(f"Fees: taker {fees['taker'] * 100:.4f}% · maker {fees['maker'] * 100:.4f}%"
                   + (f" · referral discount {fees['referral_discount'] * 100:g}%" if fees.get("referral_discount") else ""))
    out += [f"Mode in Telegram: {acc.mode}", _switch_line(db), "/preview BTC long 50 3x shows a mainnet order preview."]
    return "\n".join(out)


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
                      "", f"Trade it: /long {esc(m.coin)} 50 3x · /short {esc(m.coin)} 50 3x"])


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
        return f"No Hyperliquid account connected for {acc.mode}. Connect your wallet at {SITE}/passport#account-wallet."
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
                      f"Telegram trading: {'open (Price Chat referral verified)' if gate is None else 'not available: ' + esc(gate)}",
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

    why = entry_off(db)
    if why:
        return esc(why)
    if execution.account(db, member).mode == "LIVE" and not mainnet_allowed(db):  # mainnet off: a read-only preview, never a Confirm
        return await asyncio.to_thread(signal_preview_only, db, member, sig)
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


# ---------------------------------------------------------------- the trading card (/open, /long, /short) and its buttons
AMOUNT_CHOICES = (10, 25, 50, 100)  # USD margin buttons, only those within the member's limit
LEVERAGE_CHOICES = (2, 5, 10, 20)  # only up to the coin's Hyperliquid maximum and Price Chat's cap (risk.HARD_MAX_LEVERAGE)
CONNECT_URL = f"{SITE}/passport#trade-title"  # the Passport's "Trade on Hyperliquid" steps: sign up, API key, Telegram
CONNECT_BUTTONS = [[{"text": "🔗 Connect Hyperliquid", "url": CONNECT_URL}]]


def mode_badge(mode: str, read_only: bool = False) -> str:
    """The account's network, plainly, on every card and preview (owner, 2026-11-10)."""
    if mode == "PAPER":
        return "📝 <b>PAPER</b> · simulated, never sent to Hyperliquid"
    if mode == "TESTNET":
        return "🧪 <b>TESTNET</b> · Hyperliquid testnet, test funds"
    if read_only:
        return "💰 <b>LIVE</b> · real money · preview only: Telegram mainnet execution is OFF"
    return "💰 <b>LIVE · REAL MONEY</b>"


def api_missing(acc: TrAccount, read_only: bool) -> str | None:
    """No Hyperliquid account / API wallet for the account's network: a short line (sent with CONNECT_BUTTONS), or None."""
    if acc.mode == "PAPER":
        return None
    if not acc.master_address:
        return "No Hyperliquid account is connected yet. Connect it in your Passport, then try again."
    if acc.mode == "TESTNET" and not acc.testnet_agent_key:
        return "No Hyperliquid testnet API wallet is connected yet. Connect it in your Passport, then try again."
    if acc.mode == "LIVE" and not read_only and not acc.mainnet_agent_key:
        return "No Hyperliquid API wallet is connected yet. Connect it in your Passport, then try again."
    return None
CARD_PREFIXES = ("oa:", "op:", "ox:")  # oa: a choice (amount / leverage) · op: Preview · ox: Cancel; bot.py guards and rate-limits them
CARD_INVALID = "That button isn't valid. Nothing was sent: send the command again."
CARD_STALE = "That card is out of date (over 5 minutes). Nothing was sent: send the command again."


@dataclass
class Limits:
    coin: str
    mark: float
    equity: float
    max_margin: float
    cap: int
    cap_why: str


def _limits(db: Session, acc: TrAccount, coin: str) -> Limits:
    """The live price on the account's network and what this member may use: margin up to max allocation of equity,
    leverage up to min(their limit, the market's, Price Chat's cap). Read-only (inside Telegram a mainnet executor is the
    key-less reader)."""
    m = market.for_mode(acc.mode).market(coin)
    try:
        equity = float(execution.executor(db, acc, m.coin).equity())
    except execution.TradeError:
        raise
    except Exception as e:  # noqa: BLE001
        raise execution.TradeError("Couldn't read your balance from Hyperliquid right now. Nothing was sent; try again in a moment.") from e
    cap = min(m.max_leverage, risk.HARD_MAX_LEVERAGE)  # a typed /open's leverage is the member's choice (as manual_plan)
    why = "Price Chat's cap" if cap == risk.HARD_MAX_LEVERAGE else f"{m.coin}'s maximum on Hyperliquid"
    return Limits(m.coin, m.mark, equity, round(equity * acc.max_allocation_pct / 100, 2), max(1, cap), why)


def _card_data(prefix: str, req: OpenRequest) -> str:
    """A card button's callback data: what the member chose so far, never an account or an order. Everything is checked
    again on the press (_decode_card, then the same sizing and gates as a typed command); a trade still needs Confirm."""
    bits = [prefix.rstrip(":"), _stamp(), req.coin, "L" if req.side == "LONG" else "S",
            _px(req.margin) if req.margin is not None else "_", str(req.leverage) if req.leverage is not None else "_"]
    bits += [k + _px(v) for k, v in (("s", req.stop), ("t", req.tp), ("l", req.limit)) if v is not None]
    return ":".join(bits)


def _decode_card(data: str) -> OpenRequest | str:
    parts = data.split(":")
    if len(parts) < 6 or len(data.encode()) > 64:
        return CARD_INVALID
    _, stamp, coin, side, amt, lev, *rest = parts
    if not _fresh(stamp):
        return CARD_STALE
    if not re.fullmatch(r"[A-Za-z0-9]{1,15}", coin) or side not in ("L", "S"):  # Hyperliquid's own names (kPEPE)
        return CARD_INVALID
    margin = None if amt == "_" else _num(amt)
    leverage = None if lev == "_" else (int(lev) if lev.isdigit() else 0)
    if (amt != "_" and margin is None) or (leverage is not None and not 1 <= leverage <= risk.HARD_MAX_LEVERAGE):
        return CARD_INVALID  # a forged amount or a leverage over the system cap never gets further
    req = OpenRequest(coin, "LONG" if side == "L" else "SHORT", round(margin, 2) if margin is not None else None, leverage)
    for r in rest:
        v = _num(r[1:]) if r[:1] in ("s", "t", "l") else None
        if v is None:
            return CARD_INVALID
        setattr(req, {"s": "stop", "t": "tp", "l": "limit"}[r[0]], v)
    return req


async def _send(client: Any, chat_id: Any, text: str, buttons: Any, reply_to: int | None) -> None:
    """In a group every card replies to the member's own command (reply_to), so bot._not_yours knows whose buttons they are."""
    if reply_to:
        await client.send_message(chat_id, text, buttons, html=True, reply_to=reply_to)
    else:
        await client.send_message(chat_id, text, buttons, html=True)


def _head(req: OpenRequest, mode: str, mark: float, read_only: bool = False) -> str:
    return (mode_badge(mode, read_only) + "\n"
            + f"{'🟢' if req.side == 'LONG' else '🔴'} <b>{esc(req.coin)} {req.side}</b> · price {market.fmt_price(mark)}")


def _cancel_button(req: OpenRequest) -> dict[str, str]:
    return {"text": "Cancel", "callback_data": "ox:" + req.coin}


def ask_card(req: OpenRequest, lim: Limits, mode: str, note: str = "", read_only: bool = False) -> tuple[str, list[list[dict[str, str]]] | None]:
    """The amount or the leverage is missing: a short card asking for it with buttons (only choices within the limits)."""
    lines = [_head(req, mode, lim.mark, read_only)] + ([esc(note)] if note else [])
    if req.margin is None:
        amts = [a for a in AMOUNT_CHOICES if a <= lim.max_margin + 1e-9 and a * (req.leverage or lim.cap) >= MIN_NOTIONAL]
        if not amts and lim.max_margin >= 1 and int(lim.max_margin) * (req.leverage or lim.cap) >= MIN_NOTIONAL:
            amts = [int(lim.max_margin)]
        if not amts:
            lines.append(f"Your margin limit is ${lim.max_margin:,.2f}: under Hyperliquid's ${MIN_NOTIONAL:.0f} minimum position. "
                         "Add funds or raise it with /settings margin.")
            return "\n".join(lines), None
        lines.append(f"How much margin? Up to ${lim.max_margin:,.2f}" + (f" · leverage {req.leverage}x" if req.leverage else " · then the leverage"))
        row = [{"text": f"${a:g}", "callback_data": _card_data("oa", OpenRequest(req.coin, req.side, float(a), req.leverage, req.stop,
                                                                                    req.tp, req.limit))} for a in amts]
    else:
        levs = sorted({x for x in LEVERAGE_CHOICES if x <= lim.cap} | {lim.cap})
        levs = [x for x in levs if req.margin * x >= MIN_NOTIONAL]
        if not levs:
            lines.append(f"${req.margin:,.2f} at up to {lim.cap}x is under Hyperliquid's ${MIN_NOTIONAL:.0f} minimum position: use a bigger amount.")
            return "\n".join(lines), None
        lines.append(f"Amount ${req.margin:,.2f} · which leverage? Up to {lim.cap}x")
        row = [{"text": f"{x}x", "callback_data": _card_data("oa", OpenRequest(req.coin, req.side, req.margin, x, req.stop, req.tp,
                                                                                  req.limit))} for x in levs]
    if any(len(b["callback_data"].encode()) > 64 for b in row):
        return "Those prices are too long for a button. Nothing was sent: use fewer digits.", None
    return "\n".join(lines), [row, [_cancel_button(req)]]


def card_text(req: OpenRequest, man: Manual, mode: str, mark: float, read_only: bool) -> str:
    """The compact card once coin, side, amount and leverage are known. Estimates: Preview re-sizes at the price then."""
    p, f = man.plan, market.fmt_price
    stop = f"🛑 Stop {f(p.stop)}" + (" (yours)" if req.stop is not None else f" (default: ≈ ${p.max_loss_usd:,.2f} max loss)")
    tp = f"🎯 TP {f(p.tp1)}" + (" (yours)" if req.tp is not None else "")
    lines = [_head(req, mode, mark, read_only),
             f"Amount ${req.margin:,.2f} · {p.leverage}x → position ${p.notional:,.2f} ({p.size:g} {esc(p.coin)})"
             + (f" · limit {f(req.limit)}" if req.limit else ""),
             f"Fees ≈ ${p.est_fees:,.2f} (open + close) · liquidation ≈ {f(man.liq)}",
             f"{stop} · {tp}"]
    if read_only:
        lines.append("Preview is read-only: nothing can be sent from Telegram.")
    else:
        lines.append("Preview shows the order to confirm. Nothing is sent yet.")
    return "\n".join(lines)


def _card_plan(db: Session, member: Member, acc: TrAccount, req: OpenRequest, read_only: bool) -> Manual:
    """Sizing for the card: the same as a preview's (every limit, raising with the reasons), or on a LIVE account with
    Telegram mainnet off the read-only mainnet sizing (its refusals show on Preview)."""
    if read_only:
        m = market.for_mode("LIVE").market(req.coin)
        req.coin = m.coin
        equity = float(execution.executor(db, acc, m.coin).equity())
        fees = _fees(acc.master_address) if acc.master_address else None
        return _mainnet_plan(db, acc, req, m, equity, fees["taker"] if fees else risk.TAKER_FEE)
    man, _ = _sizing(db, member, acc, req)
    return man


async def open_card(db: Session, client: Any, chat_id: Any, member: Member, req: OpenRequest, reply_to: int | None = None) -> str | None:
    """/open, /long, /short and the card's choice buttons: the gates, then a card asking for what's missing, or the full card
    with Preview / Cancel. Returns a plain reply, or None when a card was sent."""
    import asyncio

    acc = execution.account(db, member)
    db.commit()
    if killed(db):
        return esc(KILLED)
    read_only = acc.mode == "LIVE" and not mainnet_allowed(db)
    why = None if read_only else mode_gate(db, acc)
    if why:
        return esc(why)
    why = await gate_for_entry(db, member)
    if why:
        await _send(client, chat_id, esc(why), gate_buttons(db, member), reply_to)
        return None
    why = api_missing(acc, read_only)
    if why:
        await _send(client, chat_id, f"{mode_badge(acc.mode, read_only)}\n{esc(why)}", CONNECT_BUTTONS, reply_to)
        return None
    try:
        lim = await asyncio.to_thread(_limits, db, acc, req.coin)
    except (execution.TradeError, market.MarketError) as e:  # an unknown coin says so plainly
        db.rollback()
        return esc(str(e))
    req.coin = lim.coin
    notes = []
    if req.leverage is not None and req.leverage > lim.cap:
        notes.append(f"{req.leverage}x is over {lim.cap_why} of {lim.cap}x.")
        req.leverage = None
    if req.margin is not None and req.margin > lim.max_margin + 1e-9:
        notes.append(f"${req.margin:,.2f} margin is over your limit of {acc.max_allocation_pct:g}% of ${lim.equity:,.2f} (${lim.max_margin:,.2f}).")
        req.margin = None
    if req.margin is None or req.leverage is None:
        text, buttons = ask_card(req, lim, acc.mode, " ".join(notes), read_only)
        await _send(client, chat_id, text, buttons, reply_to)
        return None
    try:
        man = await asyncio.to_thread(_card_plan, db, member, acc, req, read_only)
    except (execution.TradeError, market.MarketError) as e:
        db.rollback()
        return esc(str(e))
    db.commit()
    data = _card_data("op", req)
    if len(data.encode()) > 64:
        return "Those prices are too long for a button. Nothing was sent: use fewer digits."
    buttons = [[{"text": "👁 Preview", "callback_data": data}, _cancel_button(req)]]
    await _send(client, chat_id, card_text(req, man, acc.mode, lim.mark, read_only), buttons, reply_to)
    return None


async def card_callback(db: Session, client: Any, member: Member, chat_id: Any, reply_to: int | None, data: str) -> str | None:
    """A press on a card: a choice (oa:) builds the next card, Preview (op:) makes the existing preview with Confirm / Cancel
    (or, on a LIVE account with Telegram mainnet off, the read-only mainnet preview), Cancel (ox:) drops the card. Every
    value in the data is checked again here and by the same gates and sizing as a typed command."""
    import asyncio

    if data.startswith("ox:"):
        return "Cancelled. Nothing was sent."
    req = _decode_card(data)
    if isinstance(req, str):
        return req
    if data.startswith("oa:"):
        return await open_card(db, client, chat_id, member, req, reply_to)
    if not data.startswith("op:") or req.margin is None or req.leverage is None:
        return CARD_INVALID
    acc = execution.account(db, member)
    db.commit()
    if killed(db):
        return esc(KILLED)
    read_only = acc.mode == "LIVE" and not mainnet_allowed(db)
    why = api_missing(acc, read_only)
    if why:
        await _send(client, chat_id, f"{mode_badge(acc.mode, read_only)}\n{esc(why)}", CONNECT_BUTTONS, reply_to)
        return None
    if read_only:  # no intent, no Confirm, nothing signed
        try:
            return await asyncio.to_thread(mainnet_preview, db, member, req)
        except (execution.TradeError, market.MarketError) as e:
            return esc(str(e))
    why = mode_gate(db, acc)
    if why:
        return esc(why)
    why = await gate_for_entry(db, member)
    if why:
        await _send(client, chat_id, esc(why), gate_buttons(db, member), reply_to)
        return None
    try:
        it = await asyncio.to_thread(preview_open, db, member, req)
    except (execution.TradeError, market.MarketError) as e:
        db.rollback()
        return esc(str(e))
    await _send(client, chat_id, preview_open_text(it), _confirm_buttons("mo", "mn", it.token), reply_to)
    return None


async def command(db: Session, client: Any, chat_id: int, member: Member, cmd: str, args: list[str]) -> str | None:
    """One trading command in the member's private chat. Returns the reply (None when a message with buttons was sent)."""
    import asyncio

    acc = execution.account(db, member)
    db.commit()
    if cmd in ("/open", "/enter") and killed(db):  # the emergency stop: no new trade on any network
        return esc(KILLED)
    if cmd in ("/closeall", "/sl", "/tp") or (cmd == "/cancel" and args):
        why = live_off(db, acc.mode)
        if why:
            return esc(why)
    if cmd == "/preview":
        req = parse_open(args)
        if isinstance(req, str) or req.margin is None or req.leverage is None:
            first = req if isinstance(req, str) and req != OPEN_USAGE else "A mainnet preview needs the coin, the side, the amount and the leverage."
            return f"{first} Usage: /preview BTC long 100 5x"
        try:
            return await asyncio.to_thread(mainnet_preview, db, member, req)
        except (execution.TradeError, market.MarketError) as e:
            return esc(str(e))
    if cmd == "/mainnet":
        return await asyncio.to_thread(mainnet_status, db, member)
    if cmd == "/market":
        if not args:
            return "Usage: /market BTC"
        try:
            return market_text(args[0].lstrip("$"))
        except market.MarketError as e:
            return esc(str(e))
    if cmd == "/open":  # also /long, /short, /buy, /sell (bot.py): the trading card, then Preview, then Confirm
        req = parse_open(args)
        if isinstance(req, str):
            return req  # parse_open escapes what the member typed
        return await open_card(db, client, chat_id, member, req)
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
    if kind in ("mo", "ca", "pr") and arg != "no":
        acc = execution.account(db, member)
        why = live_off_intent(db, member, arg) if kind == "mo" else live_off(db, acc.mode)
        if kind == "pr":
            bits = arg.split(":")
            why = live_off_position(db, member, int(bits[1])) if len(bits) > 1 and bits[1].isdigit() else why
        if why:
            return esc(why)
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


# ---------------------------------------------------------------- the owner's switches and monitoring (Admin → Trading signals)
LIVE_PHRASE = "ENABLE TELEGRAM MAINNET"
TG_ACTIONS = ("manual_preview", "manual_open", "tg_mainnet_preview", "tg_signal_enter", "settings_telegram", "order_cancelled",
              "telegram_trading_switch")
FAIL_ACTIONS = ("entry_error", "entry_not_filled", "agent_not_listed", "protection_failed")


class SwitchError(Exception):
    pass


def set_switches(db: Session, actor: str, kill: bool | None = None, live: bool | None = None, phrase: str = "") -> dict[str, Any]:
    """The emergency stop takes effect at once, either way. Mainnet execution from Telegram turns on only with the typed phrase
    and while the emergency stop is off; turning it off needs nothing. Every change is in the audit log."""
    s = signals.settings(db)
    cfg = dict(_cfg(db))
    if live is True:
        if (phrase or "").strip().upper() != LIVE_PHRASE:
            raise SwitchError(f'Type "{LIVE_PHRASE}" to switch on real-money orders from Telegram.')
        if kill if kill is not None else cfg.get("kill"):
            raise SwitchError("Turn the emergency stop off first.")
    if kill is not None:
        cfg["kill"] = bool(kill)
        if kill:
            cfg["killed_at"] = utcnow().isoformat()
    if live is not None:
        cfg["live"] = bool(live)
    s.strategies = {**(s.strategies or {}), "telegram_trading": cfg}
    signals.audit(db, actor, "telegram_trading_switch", None, **{k: v for k, v in (("kill", kill), ("live", live)) if v is not None})
    if kill:  # waiting /open previews are dropped (a signal preview's Confirm is refused while the stop is on)
        for it in db.scalars(select(TrIntent).where(TrIntent.status == "PENDING")):
            if (it.plan or {}).get("manual"):
                it.status = "CANCELLED"
    db.commit()
    return admin_status(db)


def admin_status(db: Session, hours: int = 24) -> dict[str, Any]:
    """What the owner watches: the switches, the referral codes, Telegram activity and failures in the last `hours`, open
    Telegram (manual) positions per network, and the latest Telegram trading events. Nothing secret is in any of it."""
    from app.models.tradingos import TsAudit
    from app.tradingbot.service import codes, settings as earn_settings

    since = utcnow() - timedelta(hours=hours)
    es = earn_settings(db)

    def count(actions: tuple[str, ...]) -> dict[str, int]:
        rows = db.execute(select(TsAudit.action, func.count()).where(TsAudit.action.in_(actions), TsAudit.created_at >= since)
                          .group_by(TsAudit.action)).all()
        return {a: int(n) for a, n in rows}

    open_manual = db.execute(select(TrPosition.mode, func.count()).where(TrPosition.via == "manual", TrPosition.status.in_(execution.HELD_STATES))
                             .group_by(TrPosition.mode)).all()
    recent = db.scalars(select(TsAudit).where(TsAudit.action.in_(TG_ACTIONS + FAIL_ACTIONS)).order_by(TsAudit.id.desc()).limit(50)).all()
    cfg = _cfg(db)
    return {"switches": {"live": telegram_live(db), "kill": killed(db), "killed_at": cfg.get("killed_at"),
                         "gate_signal_enter": gate_signal_enter(db), "global_paused": signals.settings(db).paused,
                         "mainnet_execution": mainnet_allowed(db), "live_phrase": LIVE_PHRASE},
            "referral": {"codes": codes(es), "url": es.referral_url},
            "window_hours": hours, "activity": count(TG_ACTIONS), "failures": count(FAIL_ACTIONS),
            "open_positions": {m: int(n) for m, n in open_manual},
            "pending_previews": int(db.scalar(select(func.count()).select_from(TrIntent).where(
                TrIntent.status == "PENDING", TrIntent.expires_at > utcnow())) or 0),
            "recent": [{"at": a.created_at.isoformat(), "actor": a.actor, "action": a.action, "details": a.details} for a in recent]}
