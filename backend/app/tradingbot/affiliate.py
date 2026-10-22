"""Hyperliquid affiliate (referrer) tracking for the OWNER: what Hyperliquid reports for our referral wallet, every referred
account, and which Kiwi Passport member each one is. Admin-only; nothing here is shown to members.

Sources (all public Hyperliquid info API reads, nothing is signed or sent):
  referral               {"type":"referral","user":WALLET}: our code, rewards (claimed/unclaimed), every referred account
                         with its cumulative volume, the fees it paid since referral and the fees rewarded to us
  clearinghouseState     each referred account's perps value and open positions (spotClearinghouseState for spot USDC)

Matching a referred address to a Passport member, strongest first:
  wallet    the member proved they own the address (signed message, tb_wallets)
  trading   the member's trading account is that address (tr_accounts.master_address, from their API key)
Members we know about but Hyperliquid doesn't list (wrong code, signed up before using the link, or another wallet) are listed
separately, and so are members who clicked the referral link but never verified a wallet.

Hyperliquid only returns CURRENT cumulative numbers, so snapshot() is stored hourly (tb_affiliate_snapshots) to chart growth.
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.members import LinkedIdentity, Member
from app.models.trading import TrAccount, TrPosition
from app.models.tradingbot import TbAffiliateSnapshot, TbEvent, TbWallet

WALLET = "0x26610e89a8b825f23e89e58879ce97d791ad4438"  # the owner's Hyperliquid referrer wallet (code BANTER)
_cache: dict[str, tuple[float, Any]] = {}


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _iso_ms(ms: Any) -> str | None:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat() if ms else None


def _cached(key: str, ttl: float, fetch: Any) -> Any:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fetch()
    _cache[key] = (time.time(), val)
    return val


def referral(post: Any, wallet: str = WALLET) -> dict[str, Any]:
    """Our referrer state, normalised. Raises if Hyperliquid can't be read (never filled in)."""
    r = post({"type": "referral", "user": wallet}) or {}
    st = (r.get("referrerState") or {})
    data = st.get("data") or {}
    refs = []
    for x in data.get("referralStates") or []:
        refs.append({"address": str(x.get("user", "")).lower(), "joined_at": _iso_ms(x.get("timeJoined")), "joined_ms": int(x.get("timeJoined") or 0),
                     "volume": _f(x.get("cumVlm")), "fees_paid": _f(x.get("cumRewardedFeesSinceReferred")), "rewarded_to_us": _f(x.get("cumFeesRewardedToReferrer"))})
    hist = [{"at": _iso_ms(h.get("time")), "amount": _f(h.get("earned") or h.get("amount") or h.get("claimed")), "raw": h}
            for h in (r.get("rewardHistory") or []) if isinstance(h, dict)]
    return {"wallet": wallet, "stage": st.get("stage"), "code": data.get("code"), "n_referrals": int(data.get("nReferrals") or len(refs)),
            "referred_by": r.get("referredBy"), "own_volume": _f(r.get("cumVlm")), "unclaimed": _f(r.get("unclaimedRewards")),
            "claimed": _f(r.get("claimedRewards")), "builder_rewards": _f(r.get("builderRewards")), "reward_history": hist, "referrals": refs}


def account(post: Any, address: str) -> dict[str, Any]:
    """A referred account's live state (cached 2 min): perps value, spot USDC, open positions."""
    def fetch() -> dict[str, Any]:
        st = post({"type": "clearinghouseState", "user": address}) or {}
        ms = st.get("marginSummary") or {}
        pos = [p.get("position") or {} for p in st.get("assetPositions") or []]
        open_ = [{"coin": p.get("coin"), "side": "LONG" if _f(p.get("szi")) > 0 else "SHORT", "value": _f(p.get("positionValue")),
                  "upnl": _f(p.get("unrealizedPnl"))} for p in pos if _f(p.get("szi")) != 0]
        usdc = 0.0
        try:
            spot = post({"type": "spotClearinghouseState", "user": address}) or {}
            usdc = sum(_f(b.get("total")) for b in spot.get("balances") or [] if b.get("coin") == "USDC")
        except Exception:  # noqa: BLE001 - spot unreadable: shown as unknown below
            usdc = -1.0
        return {"perps_value": _f(ms.get("accountValue")), "spot_usdc": None if usdc < 0 else usdc, "positions": open_,
                "notional": _f(ms.get("totalNtlPos")), "read_at": utcnow().isoformat()}
    try:
        return _cached(f"acc:{address}", 120, fetch)
    except Exception as e:  # noqa: BLE001
        return {"error": f"couldn't read ({type(e).__name__})"}


def _members(db: Session) -> dict[str, dict[str, Any]]:
    """address -> the Passport member it belongs to, with how we know."""
    out: dict[str, dict[str, Any]] = {}
    for w, m in db.execute(select(TbWallet, Member).join(Member, Member.id == TbWallet.member_id)):
        out[w.address.lower()] = {"member": m, "via": "wallet", "wallet": w}
    for a, m in db.execute(select(TrAccount, Member).join(Member, Member.id == TrAccount.member_id).where(TrAccount.master_address.is_not(None))):
        out.setdefault(a.master_address.lower(), {"member": m, "via": "trading", "wallet": None})
    return out


def _member_out(db: Session, m: Member) -> dict[str, Any]:
    ids = {i.platform: (i.username or i.display_name) for i in db.scalars(select(LinkedIdentity).where(LinkedIdentity.member_id == m.id,
                                                                                                         LinkedIdentity.status == "active"))}
    acc = db.scalar(select(TrAccount).where(TrAccount.member_id == m.id))
    bot = {"mode": None, "auto": False, "open": 0, "closed": 0, "net": 0.0}
    if acc:
        rows = db.execute(select(TrPosition.status, func.count(), func.coalesce(func.sum(TrPosition.realized_pnl), 0.0))
                          .where(TrPosition.account_id == acc.id).group_by(TrPosition.status)).all()
        bot = {"mode": acc.mode, "auto": bool(acc.auto_enabled), "open": sum(n for s, n, _ in rows if s in ("OPEN", "PROTECTION_FAILED")),
               "closed": sum(n for s, n, _ in rows if s == "CLOSED"), "net": round(sum(p for s, _, p in rows if s == "CLOSED"), 4)}
    return {"id": m.id, "username": m.username, "display_name": m.display_name, "rank_level": m.rank_level, "xp": m.xp_total, "status": m.status,
            "joined_at": m.created_at.isoformat() if m.created_at else None, "telegram": ids.get("telegram"), "x": ids.get("x"),
            "google": bool(ids.get("google")) or "google" in ids, "bot": bot}


def report(db: Session, post: Any, with_accounts: bool = True) -> dict[str, Any]:
    ref = referral(post)
    known = _members(db)
    rows, listed = [], set()
    for r in sorted(ref["referrals"], key=lambda x: -x["volume"]):
        listed.add(r["address"])
        k = known.get(r["address"])
        rows.append(r | {"member": _member_out(db, k["member"]) if k else None, "match": k["via"] if k else None,
                         "account": account(post, r["address"]) if with_accounts else None})
    # Passport members we know, that Hyperliquid doesn't list under our code
    not_listed = []
    for addr, k in known.items():
        if addr in listed or addr == WALLET:
            continue
        w = k["wallet"]
        not_listed.append({"address": addr, "via": k["via"], "member": _member_out(db, k["member"]),
                           "our_check": w.referral_status if w else None, "code_seen": w.referral_code_seen if w else None,
                           "checked_at": w.checked_at.isoformat() if w and w.checked_at else None})
    clicked = db.execute(select(TbEvent.member_id, func.count(), func.max(TbEvent.created_at)).where(TbEvent.kind == "ref_click", TbEvent.member_id.is_not(None))
                         .group_by(TbEvent.member_id)).all()
    walleted = {k["member"].id for k in known.values()}
    no_wallet = []
    for mid, n, last in clicked:
        if mid in walleted:
            continue
        m = db.get(Member, mid)
        if m:
            no_wallet.append({"member": _member_out(db, m), "clicks": int(n), "last_click": last.isoformat() if last else None})
    anon = db.scalar(select(func.count()).select_from(TbEvent).where(TbEvent.kind == "ref_click", TbEvent.member_id.is_(None))) or 0
    now_ms = time.time() * 1000
    tot = {"referrals": len(rows), "matched": sum(1 for r in rows if r["member"]), "volume": round(sum(r["volume"] for r in rows), 2),
           "fees_paid": round(sum(r["fees_paid"] for r in rows), 6), "rewarded_to_us": round(sum(r["rewarded_to_us"] for r in rows), 6),
           "new_7d": sum(1 for r in rows if now_ms - r["joined_ms"] < 7 * 86_400_000), "new_30d": sum(1 for r in rows if now_ms - r["joined_ms"] < 30 * 86_400_000),
           "active": sum(1 for r in rows if (r["account"] or {}).get("positions")),
           "balances": round(sum((r["account"] or {}).get("perps_value", 0) + ((r["account"] or {}).get("spot_usdc") or 0) for r in rows), 2) if with_accounts else None,
           "ref_clicks_signed_in": sum(int(n) for _, n, _ in clicked), "ref_clicks_anonymous": int(anon)}
    return {"at": utcnow().isoformat(), "source": "hyperliquid:info referral + clearinghouseState", "master": {k: v for k, v in ref.items() if k != "referrals"},
            "totals": tot, "referrals": rows, "not_listed": sorted(not_listed, key=lambda x: x["member"]["id"]), "clicked_no_wallet": no_wallet}


def snapshot(db: Session, post: Any) -> TbAffiliateSnapshot:
    """Store the current numbers (the history Hyperliquid doesn't keep for us)."""
    ref = referral(post)
    data = {"code": ref["code"], "n_referrals": ref["n_referrals"], "unclaimed": ref["unclaimed"], "claimed": ref["claimed"],
            "volume": round(sum(r["volume"] for r in ref["referrals"]), 2), "fees_paid": round(sum(r["fees_paid"] for r in ref["referrals"]), 6),
            "rewarded_to_us": round(sum(r["rewarded_to_us"] for r in ref["referrals"]), 6),
            "users": {r["address"]: [r["volume"], r["fees_paid"], r["rewarded_to_us"]] for r in ref["referrals"]}}
    row = TbAffiliateSnapshot(wallet=WALLET, data=data)
    db.add(row)
    db.commit()
    return row


def history(db: Session, days: int = 90) -> dict[str, Any]:
    rows = db.scalars(select(TbAffiliateSnapshot).where(TbAffiliateSnapshot.wallet == WALLET, TbAffiliateSnapshot.created_at >= utcnow() - timedelta(days=days))
                      .order_by(TbAffiliateSnapshot.created_at)).all()
    pts = [{"at": r.created_at.isoformat(), **{k: r.data.get(k) for k in ("n_referrals", "volume", "fees_paid", "rewarded_to_us", "unclaimed", "claimed")}} for r in rows]
    deltas: dict[str, dict[str, float]] = {}
    if rows:  # each referred account's volume over the last 24 h / 7 d, from the stored snapshots
        last = rows[-1].data.get("users", {})
        for label, hours in (("24h", 24), ("7d", 168)):
            base = next((r for r in rows if r.created_at >= rows[-1].created_at - timedelta(hours=hours)), rows[0])
            old = base.data.get("users", {})
            for a, v in last.items():
                deltas.setdefault(a, {})[label] = round(v[0] - (old.get(a) or [0])[0], 2)
    return {"points": pts, "volume_change": deltas, "since": pts[0]["at"] if pts else None}
