"""Members' trading accounts on the website: mode, limits, AUTO, STOP NEW TRADES, authorizing a Hyperliquid API wallet
with their own wallet's signature, and the pre-live checklist.

Mode rules: PAPER always; TESTNET once a testnet API wallet is approved; LIVE only for allow-listed members (Admin → Trading
OS → LIVE allowlist), with an approved mainnet API wallet, every pre-live check passed within the last hour, and the
member's explicit "ENABLE LIVE" confirmation. Nothing here ever places an order.
"""

import time
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.members import Member
from app.models.trading import TrAccount, TrPosition
from app.tradingos import exchange as xch
from app.tradingos import execution, risk, signals

CHECKS_VALID = timedelta(hours=1)
INFO_ONLY = ("balance", "sizing")  # pre-live checks shown to the member but not required to connect
LIMIT_CAPS = {"max_leverage": (1, risk.HARD_MAX_LEVERAGE), "max_loss_per_trade_usd": (1.0, 500.0), "max_allocation_pct": (1.0, 50.0),
              "max_positions": (1, risk.HARD_MAX_POSITIONS), "daily_loss_limit_usd": (1.0, 2000.0)}
LIVE_ROLLOUT_MAX_LEVERAGE = 3

AUTO_MAX_HOURS = 24 * 180  # AUTO runs for a set time (up to 180 days) or, with auto_hours = 0, until the member turns it off


class AccountError(Exception):
    pass


def _iso(d: Any) -> str | None:
    return execution._aware(d).isoformat() if d else None


def pos_out(p: TrPosition) -> dict[str, Any]:
    return {"id": p.id, "signal_id": p.signal_id, "mode": p.mode, "coin": p.coin, "side": p.side, "status": p.status, "size": p.size,
            "entry_px": p.entry_px, "leverage": p.leverage, "stop": p.stop, "tp1": p.tp1, "tp2": p.tp2, "tp1_done": p.tp1_done,
            "max_loss_usd": p.max_loss_usd, "via": p.via, "exit_px": p.exit_px, "realized_pnl": round(p.realized_pnl, 4), "fees": round(p.fees, 4),
            "close_reason": None if execution.closing(p) else p.close_reason, "error": p.error, "opened_at": _iso(p.created_at), "closed_at": _iso(p.closed_at)}


def state(db: Session, member: Member) -> dict[str, Any]:
    acc = execution.account(db, member)
    db.commit()
    rows = db.scalars(select(TrPosition).where(TrPosition.account_id == acc.id).order_by(TrPosition.id.desc()).limit(30)).all()
    return {"mode": acc.mode, "master_address": acc.master_address, "paused": acc.paused, "paused_reason": acc.paused_reason,
            "limits": {k: getattr(acc, k) for k in LIMIT_CAPS} | {"allowed_coins": acc.allowed_coins}, "caps": LIMIT_CAPS,
            "auto": {"enabled": acc.auto_enabled, "until": _iso(acc.auto_until)},
            "agents": {"TESTNET": {"address": acc.testnet_agent, "approved": bool(acc.testnet_approved_at)},
                       "LIVE": {"address": acc.mainnet_agent, "approved": bool(acc.mainnet_approved_at)}},
            "live": {"allowed": execution.live_allowed(db, member), "enabled": bool(acc.live_enabled_at), "checks": acc.live_checks or {}},
            "style": style_out(db, acc),
            "paper_equity": round(execution.paper_equity(db, acc), 2), "daily_pnl": round(execution.daily_pnl(db, acc), 2),
            "positions": [pos_out(p) for p in rows], "results": execution.results(db, acc)}


# ---------------------------------------------------------------- settings
TIMEFRAMES = ("1h", "15m", "both")
FREQUENCIES = ("standard", "active")
INTRADAY_LIVE_PHRASE = "ENABLE 15M"


def style_out(db: Session, acc: TrAccount) -> dict[str, Any]:
    """The member's trading style and what it means right now (the limits the risk engine applies)."""
    from app.tradingos import kitchen

    per_day, cooldown = execution.style_limits(acc)
    return {"timeframe": acc.timeframe_pref, "frequency": acc.frequency, "max_trades_per_day": acc.max_trades_per_day,
            "cooldown_minutes": acc.cooldown_minutes, "effective": {"trades_per_day": per_day, "cooldown_minutes": cooldown},
            "trades_today": execution.trades_today(db, acc), "intraday_live": bool(acc.intraday_live_at),
            "intraday_live_available": kitchen.live_enabled(signals.settings(db)), "intraday_live_phrase": INTRADAY_LIVE_PHRASE,
            "caps": {"max_trades_per_day": [1, risk.HARD_MAX_TRADES_PER_DAY], "cooldown_minutes": [execution.ACTIVE_MIN_COOLDOWN, 240]}}


def _style(db: Session, member: Member, acc: TrAccount, v: dict[str, Any]) -> None:
    changed = {}
    if "timeframe" in v:
        if v["timeframe"] not in TIMEFRAMES:
            raise AccountError("Timeframe must be 1h, 15m or both.")
        acc.timeframe_pref = changed["timeframe"] = v["timeframe"]
    if "frequency" in v:
        if v["frequency"] not in FREQUENCIES:
            raise AccountError("Frequency must be standard or active.")
        acc.frequency = changed["frequency"] = v["frequency"]
    if "max_trades_per_day" in v:
        if not 1 <= int(v["max_trades_per_day"]) <= risk.HARD_MAX_TRADES_PER_DAY:
            raise AccountError(f"Trades per day must be between 1 and {risk.HARD_MAX_TRADES_PER_DAY}.")
        acc.max_trades_per_day = changed["max_trades_per_day"] = int(v["max_trades_per_day"])
    if "cooldown_minutes" in v:
        if not execution.ACTIVE_MIN_COOLDOWN <= int(v["cooldown_minutes"]) <= 240:
            raise AccountError(f"Cooldown must be between {execution.ACTIVE_MIN_COOLDOWN} and 240 minutes.")
        acc.cooldown_minutes = changed["cooldown_minutes"] = int(v["cooldown_minutes"])
    if "intraday_live" in v:
        if v["intraday_live"]:
            if (v.get("intraday_phrase") or "").strip().upper() != INTRADAY_LIVE_PHRASE:
                raise AccountError(f'Type "{INTRADAY_LIVE_PHRASE}" to let 15m signals trade on TESTNET / LIVE.')
            if not acc.intraday_live_at:
                acc.intraday_live_at = utcnow()
        else:
            acc.intraday_live_at = None
        changed["intraday_live"] = bool(acc.intraday_live_at)
    if changed:
        signals.audit(db, f"m:{member.id}", "trading_style", None, mode=acc.mode, **changed)


def update(db: Session, member: Member, v: dict[str, Any]) -> None:
    acc = execution.account(db, member)
    _style(db, member, acc, v)
    for k, (lo, hi) in LIMIT_CAPS.items():
        if k in v:
            x = v[k]
            if not lo <= x <= hi:
                raise AccountError(f"{k.replace('_', ' ')} must be between {lo:g} and {hi:g}.")
            setattr(acc, k, x)
    if acc.mode == "LIVE" and acc.max_leverage > LIVE_ROLLOUT_MAX_LEVERAGE:
        raise AccountError(f"LIVE is capped at {LIVE_ROLLOUT_MAX_LEVERAGE}x for the initial rollout.")
    if "allowed_coins" in v:
        acc.allowed_coins = ",".join(c.strip().upper() for c in v["allowed_coins"].split(",") if c.strip())[:500]
    if "paused" in v:
        if not v["paused"] and acc.paused_reason and acc.paused_reason.startswith("Protection failed") and not v.get("ack_protection"):
            raise AccountError(f"New trades were stopped by a safety check: {acc.paused_reason}. Tick that you reviewed it first.")
        acc.paused, acc.paused_reason = bool(v["paused"]), ("STOP NEW TRADES (website)" if v["paused"] else None)
        signals.audit(db, f"m:{member.id}", "stop_new_trades" if v["paused"] else "resume_new_trades", None, via="website")
    if "auto_enabled" in v:
        if v["auto_enabled"]:
            hours = int(v["auto_hours"]) if v.get("auto_hours") is not None else 24
            if hours != 0 and not 1 <= hours <= AUTO_MAX_HOURS:  # 0 = until the member turns it off
                raise AccountError("AUTO lasts 1 hour to 180 days, or until you turn it off.")
            acc.auto_enabled, acc.auto_until = True, (utcnow() + timedelta(hours=hours) if hours else None)
        else:
            acc.auto_enabled, acc.auto_until = False, None
        signals.audit(db, f"m:{member.id}", "auto_on" if acc.auto_enabled else "auto_off", None, mode=acc.mode,
                      until=_iso(acc.auto_until))
    if "mode" in v and v["mode"] != acc.mode:
        set_mode(db, member, acc, v["mode"])
    db.commit()


def set_mode(db: Session, member: Member, acc: TrAccount, mode: str) -> None:
    if execution.open_count(db, acc):
        raise AccountError("Close your open bot positions before switching mode.")
    if mode == "TESTNET" and not acc.testnet_approved_at:
        raise AccountError("Approve a testnet API wallet first.")
    if mode == "LIVE":
        if not execution.live_allowed(db, member):
            raise AccountError("LIVE trading isn't available for this account.")
        if not acc.live_enabled_at:
            raise AccountError("Pass the pre-live checks and enable LIVE first.")
        if acc.max_leverage > LIVE_ROLLOUT_MAX_LEVERAGE:
            acc.max_leverage = LIVE_ROLLOUT_MAX_LEVERAGE
    acc.mode = mode
    acc.auto_enabled, acc.auto_until = False, None  # a new mode never inherits AUTO: it's switched on again on purpose
    signals.audit(db, f"m:{member.id}", "mode", None, mode=mode)


# ---------------------------------------------------------------- API wallet authorization
def _open_on(db: Session, acc: TrAccount, network: str) -> int:
    from sqlalchemy import func

    return int(db.scalar(select(func.count()).select_from(TrPosition).where(
        TrPosition.account_id == acc.id, TrPosition.mode == network, TrPosition.status.in_(execution.RECONCILED + ("OPENING",)))) or 0)


def _master(db: Session, member: Member) -> str:
    from app.tradingbot.service import wallet_of

    w = wallet_of(db, member)
    if w is None:
        raise AccountError("Connect and verify your wallet in Kiwi Passport first.")
    return w.address.lower()


def agent_start(db: Session, member: Member, network: str, chain_id_hex: str) -> dict[str, Any]:
    """Create an API wallet (its key never leaves the server) and return the ApproveAgent message for the member to sign."""
    if network == "LIVE" and not execution.live_allowed(db, member):
        raise AccountError(execution.eligibility(db, member)[1] or "Bot trading isn't available for this account yet.")
    acc = execution.account(db, member)
    if _open_on(db, acc, network):  # the new key isn't approved yet: those positions couldn't be closed from here until it is
        raise AccountError(f"Close your open {network.lower()} bot positions before creating a new API wallet.")
    acc.master_address = _master(db, member)
    key, addr = xch.new_agent()
    if network == "TESTNET":
        acc.testnet_agent, acc.testnet_agent_key, acc.testnet_approved_at = addr, xch.enc(key), None
    else:
        acc.mainnet_agent, acc.mainnet_agent_key, acc.mainnet_approved_at = addr, xch.enc(key), None
        acc.live_enabled_at = None
        if acc.mode == "LIVE":
            acc.mode, acc.auto_enabled, acc.auto_until = "PAPER", False, None
    nonce = int(time.time() * 1000)
    signals.audit(db, f"m:{member.id}", "agent_created", None, network=network, agent=addr)
    db.commit()
    return {"typed_data": xch.approve_typed_data(network, addr, nonce, chain_id_hex), "agent": addr, "nonce": nonce}


def agent_finish(db: Session, member: Member, network: str, nonce: int, chain_id_hex: str, signature: str,
                 transport: Any = None) -> dict[str, Any]:
    acc = execution.account(db, member)
    agent = acc.testnet_agent if network == "TESTNET" else acc.mainnet_agent
    if not agent:
        raise AccountError("Start the authorization again.")
    if abs(time.time() * 1000 - nonce) > 10 * 60 * 1000:
        raise AccountError("That signature request expired. Start again.")
    typed = xch.approve_typed_data(network, agent, nonce, chain_id_hex)
    master = _master(db, member)
    try:
        signer = xch.signer_of(typed, signature)
    except Exception as e:  # noqa: BLE001
        raise AccountError("That signature couldn't be read.") from e
    if signer != master:
        raise AccountError("The signature isn't from your verified wallet. Sign with the wallet connected in Kiwi Passport.")
    resp = xch.submit_approval(network, typed, signature, transport)
    if not isinstance(resp, dict) or resp.get("status") != "ok":
        signals.audit(db, f"m:{member.id}", "agent_rejected", None, network=network, response=str(resp)[:300])
        db.commit()
        msg = str(resp.get("response") if isinstance(resp, dict) else resp)[:200]
        raise AccountError(f"Hyperliquid didn't accept the approval: {msg}"
                           + (" (a Hyperliquid account needs a deposit before it can approve an API wallet)" if "deposit" in msg.lower() else ""))
    if not xch.agent_listed(network, master, agent, transport):
        raise AccountError("Hyperliquid accepted it but doesn't list the API wallet yet. Try 'Check again' in a minute.")
    if network == "TESTNET":
        acc.testnet_approved_at = utcnow()
    else:
        acc.mainnet_approved_at = utcnow()
    signals.audit(db, f"m:{member.id}", "agent_approved", None, network=network, agent=agent)
    db.commit()
    return {"approved": True, "agent": agent}


def revoke_local(db: Session, member: Member, network: str) -> None:
    """Forget the API wallet here (the member can also remove it on Hyperliquid → API)."""
    acc = execution.account(db, member)
    if _open_on(db, acc, network):  # without the key those positions couldn't be closed or reconciled
        raise AccountError("Close your open bot positions on this network first.")
    if network == "TESTNET":
        acc.testnet_agent = acc.testnet_agent_key = acc.testnet_approved_at = None
    else:
        acc.mainnet_agent = acc.mainnet_agent_key = acc.mainnet_approved_at = acc.live_enabled_at = None
    if acc.mode == network:
        acc.mode, acc.auto_enabled = "PAPER", False
    signals.audit(db, f"m:{member.id}", "agent_removed", None, network=network)
    db.commit()


# ---------------------------------------------------------------- the pre-live checklist
def live_checks(db: Session, member: Member, ex: Any = None) -> dict[str, Any]:
    """Every check reads real state; none of them places an order. Stored with a timestamp (valid for an hour)."""
    acc = execution.account(db, member)
    out: dict[str, dict[str, Any]] = {}

    def put(key: str, ok: bool, detail: str) -> None:
        out[key] = {"ok": bool(ok), "detail": detail}

    ok, why = execution.eligibility(db, member)
    listed = execution.allowlisted(db, member)
    put("eligible", ok or listed, "verified wallet confirmed" if ok
        else "on the owner's allowlist" if listed else why)
    try:
        master = _master(db, member)
        put("wallet_ownership", acc.master_address == master, "your verified wallet signed the authorization")
    except AccountError as e:
        put("wallet_ownership", False, str(e))
    put("authorization", bool(acc.mainnet_approved_at and acc.mainnet_agent_key), "mainnet API wallet approved" if acc.mainnet_approved_at
        else "approve a mainnet API wallet")
    equity = 0.0
    if acc.mainnet_agent_key and acc.master_address:
        try:
            ex = ex or xch.HyperliquidExecutor("LIVE", xch.dec(acc.mainnet_agent_key), acc.master_address)
            equity = ex.equity()
            put("connectivity", True, "Hyperliquid mainnet reachable")
            listed = xch.agent_listed("LIVE", acc.master_address, acc.mainnet_agent or "")
            out["authorization"] = {"ok": out["authorization"]["ok"] and listed,
                                    "detail": "API wallet listed on Hyperliquid" if listed else "Hyperliquid doesn't list the API wallet"}
        except Exception as e:  # noqa: BLE001
            put("connectivity", False, f"couldn't read Hyperliquid: {type(e).__name__}")
    else:
        put("connectivity", False, "needs the mainnet API wallet")
    put("balance", equity >= 10, f"trading balance ${equity:,.2f}" + ("" if equity >= 10 else
        " (at least $10 needed: deposit USDC; on a classic account, move it from Spot to Perps)"))
    # sizing: a typical BTC-like trade (2% stop) at these limits must be valid and within the max loss
    probe = risk.plan_trade(coin="BTC", side="LONG", mark=100.0, entry_zone=(99.5, 100.5), stop=98.0, target=104.0, sig_lev_hi=3,
                            market_max_lev=40, sz_decimals=4, equity=equity, max_leverage=acc.max_leverage,
                            max_loss_usd=acc.max_loss_per_trade_usd, max_allocation_pct=acc.max_allocation_pct, max_positions=acc.max_positions,
                            open_positions=0, daily_pnl=0.0, daily_loss_limit=acc.daily_loss_limit_usd, allowed=[], paused=False, global_paused=False)
    put("sizing", probe.ok and probe.max_loss_usd <= acc.max_loss_per_trade_usd + 0.01,
        f"example trade: ${probe.notional:,.2f} notional, max loss ${probe.max_loss_usd:,.2f}" if probe.ok else "; ".join(probe.reasons))
    # (No testnet trade is required any more: every LIVE entry places its exchange-side stop at once, and if Hyperliquid refuses
    # it the position is closed at market and new trades stop: see execution.protect / emergency.)
    put("risk_limits", acc.max_leverage <= LIVE_ROLLOUT_MAX_LEVERAGE and acc.max_positions <= 2 and acc.daily_loss_limit_usd > 0,
        f"{acc.max_leverage}x max, {acc.max_positions} positions, daily loss limit ${acc.daily_loss_limit_usd:,.0f}")
    stopped = risk.plan_trade(coin="BTC", side="LONG", mark=100.0, entry_zone=(99.5, 100.5), stop=98.0, target=104.0, sig_lev_hi=3,
                              market_max_lev=40, sz_decimals=4, equity=1000, max_leverage=3, max_loss_usd=5, max_allocation_pct=10, max_positions=2,
                              open_positions=0, daily_pnl=0, daily_loss_limit=15, allowed=[], paused=True, global_paused=False)
    put("emergency_stop", not stopped.ok, "STOP NEW TRADES blocks entries (stops stay in place)")
    # balance and sizing are shown, not required: an empty account can connect (and see $0); every entry is still sized and refused
    # by risk.plan_trade when the money isn't there, so nothing can trade without funds
    acc.live_checks = {"at": utcnow().isoformat(), "items": out, "passed": all(c["ok"] for k, c in out.items() if k not in INFO_ONLY)}
    signals.audit(db, f"m:{member.id}", "live_checks", None, passed=acc.live_checks["passed"],
                  failed=[k for k, c in out.items() if not c["ok"]])
    db.commit()
    return acc.live_checks


def enable_live(db: Session, member: Member, phrase: str) -> None:
    acc = execution.account(db, member)
    if phrase.strip().upper() != "ENABLE LIVE":
        raise AccountError('Type "ENABLE LIVE" to confirm.')
    if not execution.live_allowed(db, member):
        raise AccountError("LIVE trading isn't available for this account.")
    ch = acc.live_checks or {}
    from datetime import datetime

    at = datetime.fromisoformat(ch["at"]) if ch.get("at") else None
    if not ch.get("passed") or at is None or utcnow() - execution._aware(at) > CHECKS_VALID:  # type: ignore[operator]
        raise AccountError("Run the pre-live checks (all must pass, within the last hour).")
    acc.live_enabled_at = utcnow()
    acc.max_leverage = min(acc.max_leverage, LIVE_ROLLOUT_MAX_LEVERAGE)
    signals.audit(db, f"m:{member.id}", "live_enabled", None)
    set_mode(db, member, acc, "LIVE")
    db.commit()


def disable_live(db: Session, member: Member) -> None:
    acc = execution.account(db, member)
    acc.live_enabled_at = None
    if acc.mode == "LIVE":
        if execution.open_count(db, acc):
            acc.paused, acc.paused_reason = True, "LIVE disabled: open positions keep their stops; no new trades"
        else:
            acc.mode = "PAPER"
        acc.auto_enabled, acc.auto_until = False, None
    signals.audit(db, f"m:{member.id}", "live_disabled", None)
    db.commit()


# ---------------------------------------------------------------- "Connect Hyperliquid" (website): one status for the 4 steps
# What an approved API wallet does here. Hyperliquid's docs (API → Nonces and API wallets; Exchange endpoint) say API wallets
# sign actions on behalf of the account; they do NOT state a blanket "can never withdraw" rule (some asset moves are agent-
# signable), so we don't claim one. What we do control: Price Chat's code only places, cancels and changes orders and sets
# leverage; it never sends withdrawals or transfers. Losses from trading are possible: limits are enforced here.
PERMISSIONS = ["Place, cancel and change orders on your Hyperliquid account", "Set leverage for the trades it opens",
               "Price Chat never sends withdrawals or transfers with it. You can revoke it on Hyperliquid at any time",
               "Trades can lose money: your limits are enforced by Price Chat, not by Hyperliquid"]


def _referral_state(w: Any) -> str:
    if w is None or w.referral_status in ("pending", "unverified") or w.checked_at is None:
        return "PENDING"
    if w.referral_status == "verified":
        return "VERIFIED"
    return "EXISTING_ACCOUNT" if w.referral_code_seen else "NOT_VERIFIED"


_money: dict[str, tuple[float, dict[str, float] | None]] = {}


def _balances(address: str) -> dict[str, float] | None:
    """Perps + Spot USDC of the member's account, cached 30 s. None when Hyperliquid can't be reached (shown as unknown)."""
    hit = _money.get(address)
    if hit and time.monotonic() - hit[0] < 30:
        return hit[1]
    try:
        out: dict[str, float] | None = xch.balances("LIVE", address)
    except Exception:  # noqa: BLE001
        out = None
    _money[address] = (time.monotonic(), out)
    return out


def connection(db: Session, member: Member) -> dict[str, Any]:
    """Everything the Connect Hyperliquid dialog shows, decided on the server. "connected" only when every step is verified."""
    from app.tradingbot.service import codes, rank_info, settings as earn_settings, wallet_of

    s = earn_settings(db)
    acc = execution.account(db, member)
    w = wallet_of(db, member)
    rk = rank_info(db, member, s)
    ok, why = execution.eligibility(db, member)
    listed = execution.allowlisted(db, member)
    st, rec = execution.agent_status(acc)
    if st != "NONE" and w is not None and acc.master_address and w.address.lower() != acc.master_address.lower():
        st = "WALLET_CHANGED"
    db.commit()
    connected = (ok or listed) and st == "ACTIVE" and bool(acc.live_enabled_at) and acc.mode == "LIVE"
    money = _balances(w.address) if w else None
    return {
        "eligible": ok or listed, "blocker": None if ok or listed else why,
        "rank": {"ok": rk["ok"], "required_level": s.min_rank_level, "level": rk["current"]["level"]},
        "wallet": {"address": w.address} if w else None,
        "referral": {"status": _referral_state(w), "code": codes(s)[0], "code_seen": w.referral_code_seen if w else None,
                     "url": s.referral_url, "checked_at": w.checked_at.isoformat() if w and w.checked_at else None},
        "deposit": {"status": ("verified" if w and (w.deposit_status == "verified" or (w.deposit_usd or 0) + (w.other_inflow_usd or 0) >= s.min_deposit_usd)
                               else w.deposit_status if w else "pending"),
                    "usd": round((w.deposit_usd or 0) + (w.other_inflow_usd or 0), 2) if w else 0.0, "min_usd": s.min_deposit_usd},
        "authorization": {"status": st, "agent": acc.mainnet_agent if st != "NONE" else None,
                          "approved_at": acc.mainnet_approved_at.isoformat() if acc.mainnet_approved_at and st != "NONE" else None,
                          "valid_until": int(rec["validUntil"]) if rec and rec.get("validUntil") else None,
                          "revoke_url": "https://app.hyperliquid.xyz/API"},
        "live": {"enabled": bool(acc.live_enabled_at), "mode": acc.mode},
        "auto": acc.auto_enabled, "connected": connected, "permissions": PERMISSIONS,
        "balances": money,  # the member's own account (never the API wallet's: an API wallet holds no money)
    }


def recheck_referral(db: Session, member: Member, client: Any) -> dict[str, Any]:
    """RETRY VERIFICATION: read the referral and deposits again from Hyperliquid for the verified wallet (rate limited)."""
    from app.tradingbot import service

    w = service.wallet_of(db, member)
    if w is None:
        raise AccountError("Connect and verify your wallet first.")
    try:
        service.limit("recheck", f"m{member.id}")
    except service.AccessError as e:
        raise AccountError(str(e)) from e
    service.run_check(db, member, w, client)
    return connection(db, member)


def activate(db: Session, member: Member, consent: bool, ex: Any = None) -> dict[str, Any]:
    """ENABLE BOT TRADING, after the API wallet is approved: the member's explicit consent, every pre-live check on real state
    (no order is placed), then LIVE with AUTO off. Nothing trades until the member confirms a signal (or turns AUTO on later)."""
    if not consent:
        raise AccountError("Confirm that you understand Kiwi can place real orders that can lose money.")
    checks = live_checks(db, member, ex)
    if not checks.get("passed"):
        failed = [c["detail"] for k, c in (checks.get("items") or {}).items() if not c["ok"] and k not in INFO_ONLY]
        raise AccountError("Not ready yet: " + "; ".join(failed[:3]))
    enable_live(db, member, "ENABLE LIVE")  # consent given above; enable_live re-checks eligibility and the fresh checks
    acc = execution.account(db, member)
    acc.auto_enabled, acc.auto_until = False, None
    signals.audit(db, f"m:{member.id}", "bot_trading_enabled", None, via="website")
    db.commit()
    return connection(db, member)


def connect_api_key(db: Session, member: Member, address: str | None, secret: str, consent: bool, client: Any, ex: Any = None,
                    replace: bool = False) -> dict[str, Any]:
    """CONNECT with an API key the member created on Hyperliquid (app.hyperliquid.xyz/API): no wallet connection on our site.

    Only the key is needed: Hyperliquid says which account an API wallet trades for (info "userRole" → agent → user). Only the
    owner of that account can approve an API wallet for it, so this proves the member controls the account. (`address` is
    optional and only cross-checked.) Then the referral and deposit are read from
    Hyperliquid for that address, and, with the member's consent and every Passport rule met, LIVE is enabled with AUTO off.
    The key is stored encrypted, never returned, never logged. A main-wallet key is refused and never stored."""
    import eth_account

    from app.models.tradingbot import TbWallet
    from app.tradingbot import evm, service

    try:
        service.limit("apikey", f"m{member.id}")
    except service.AccessError as e:
        raise AccountError(str(e)) from e
    raw = (secret or "").strip()
    raw = raw if raw.startswith("0x") else "0x" + raw
    try:
        if len(raw) != 66:
            raise ValueError
        agent = eth_account.Account.from_key(raw).address
    except Exception as e:  # noqa: BLE001 - never echo the input
        raise AccountError("That isn't an API wallet private key (64 characters, from Hyperliquid's API page).") from e
    try:
        role = xch.user_role("LIVE", agent)
    except Exception as e:  # noqa: BLE001
        raise AccountError("Couldn't reach Hyperliquid to check the key. Nothing was saved; try again in a moment.") from e
    kind = role.get("role")
    if kind == "agent":
        master = str((role.get("data") or {}).get("user") or "").lower()
    elif kind in ("user", "vault", "subAccount"):  # the key of an account itself, not of an API wallet: refused, never stored
        raise AccountError("That key belongs to a Hyperliquid account itself (a main wallet key): never share it with anyone. On "
                           "Hyperliquid's API page, generate an API wallet, authorize it, and paste that key instead.")
    else:
        raise AccountError("Hyperliquid doesn't know this API key yet. On app.hyperliquid.xyz/API, press Authorize API Wallet with the account "
                           "you trade on (it needs a deposit first), then paste the key again.")
    try:
        master = evm.normalize(master)
    except ValueError as e:
        raise AccountError("Hyperliquid's answer for this key had no account. Try again in a moment.") from e
    if address:
        try:
            given = evm.normalize(address)
        except ValueError:
            given = master
        if given not in (master, agent.lower()):
            raise AccountError(f"This API key trades for {master[:6]}…{master[-4:]}, not the address you entered. Leave the address empty "
                               "or use that account.")
    owner = db.scalar(select(TbWallet).where(TbWallet.address == master, TbWallet.member_id != member.id))
    if owner is not None:
        raise AccountError("This Hyperliquid account is already connected to another Kiwi Passport.")
    twin = db.scalar(select(TrAccount).where(TrAccount.mainnet_agent == agent, TrAccount.member_id != member.id))
    if twin is not None:  # one API wallet, one Passport
        raise AccountError("This API key is already connected to another Kiwi Passport. Create a new API wallet on Hyperliquid for this one.")
    mine = service.wallet_of(db, member)
    if mine is not None and mine.address != master and not replace:  # never switch the Passport's account silently
        raise AccountError(f"This key trades for {master[:6]}…{master[-4:]}, but your Kiwi Passport is linked to {mine.address[:6]}…"
                           f"{mine.address[-4:]}. Switch my Passport to this Hyperliquid account?")
    try:
        rec = xch.agent_record("LIVE", master, agent)
    except Exception as e:  # noqa: BLE001
        raise AccountError("Couldn't reach Hyperliquid to check the key. Nothing was saved; try again in a moment.") from e
    if rec is None:
        raise AccountError("This API key has expired or was removed on Hyperliquid. Create a new one on app.hyperliquid.xyz/API and paste it.")
    acc = execution.account(db, member)
    # any network: testnet positions belong to the old account too (reconciling them against the new one would mark them CLOSED)
    if acc.master_address and acc.master_address.lower() != master and (_open_on(db, acc, "LIVE") or _open_on(db, acc, "TESTNET")):
        raise AccountError("Close your open positions on the current account before connecting a different one.")
    # bind the proven account to this Passport (replacing an earlier wallet: its old results don't carry over)
    now = utcnow()
    w = service.wallet_of(db, member)
    if w is None:
        w = TbWallet(member_id=member.id, address=master, verified_at=now)
        db.add(w)
    elif w.address != master:
        service.audit(db, None, member.id, f"member:{member.id}", "wallet_replaced", {"from": w.address, "to": master, "proof": "api_wallet"})
        w.address, w.verified_at, w.checked_at = master, now, None
        w.referral_status = w.deposit_status = "pending"
        w.referral_code_seen, w.deposit_usd, w.other_inflow_usd, w.evidence = None, 0.0, 0.0, {}
    else:
        w.verified_at = now
    acc.master_address = master
    acc.mainnet_agent, acc.mainnet_agent_key, acc.mainnet_approved_at = agent, xch.enc(raw), now
    acc.live_enabled_at = None  # new LIVE entries wait for "enable" again; closing and reconciliation keep working with the new key
    if acc.mode == "LIVE":
        acc.auto_enabled, acc.auto_until = False, None
        if not _open_on(db, acc, "LIVE"):
            acc.mode = "PAPER"
    raw = ""  # noqa: F841 - drop our copy of the plain key
    execution._agent_seen[(master, agent.lower())] = (time.monotonic(), rec)
    service.audit(db, None, member.id, f"member:{member.id}", "wallet_verified", {"address": master, "proof": "api_wallet"})
    signals.audit(db, f"m:{member.id}", "agent_connected", None, network="LIVE", agent=agent, via="api_key")
    service.track(db, "wallet_verified", member, once=True)
    service.free_level(db, member, master)
    db.commit()
    service.run_check(db, member, w, client)  # the referral and deposit, from Hyperliquid's records for this address
    if consent and (execution.eligibility(db, member)[0] or execution.allowlisted(db, member)):
        try:
            return activate(db, member, True, ex)
        except AccountError as e:
            db.rollback()
            return {**connection(db, member), "note": str(e)}
    return connection(db, member)
