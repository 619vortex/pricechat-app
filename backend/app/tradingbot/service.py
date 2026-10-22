"""VIP Trading Bot access: who may apply, proving a wallet, checking Hyperliquid, the application lifecycle, the private
Telegram group's join requests, and funnel analytics.

Everything is decided here, on the server, from the database and Hyperliquid's own records. The browser only ever
*asks*; rank, wallet, referral and deposit results are never taken from it.

Requirement results: verified (proved) · not met (proved not to be the case) · unverified (the API can't establish it:
the member may still apply and a person reviews it) · pending (not checked yet).

Member-facing stages: LOCKED → IN_PROGRESS → PENDING_VERIFICATION → ELIGIBLE → PENDING_APPROVAL → APPROVED,
or REJECTED / REVOKED.
"""

import base64
import hashlib
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import xp as ledger
from app.models.members import LinkedIdentity, Member
from app.models.tradingbot import TbApplication, TbAudit, TbEvent, TbNonce, TbSettings, TbWallet
from app.security.secrets import protect, unprotect
from app.tradingbot import evm
from app.tradingbot.hyperliquid import InfoClient, check

NONCE_TTL = timedelta(minutes=10)
RECHECK_EVERY = timedelta(seconds=60)
AUTO_RECHECK = timedelta(minutes=30)  # opening Earn re-reads Hyperliquid when the last check is older than this
REAPPLY_AFTER = timedelta(days=14)  # after a rejection
OPEN = ("PENDING_APPROVAL", "APPROVED")
APP_STATUSES = ("PENDING_APPROVAL", "APPROVED", "REJECTED", "REVOKED")
EVENT_KINDS = {"view", "cta", "passport", "rank_ok", "ref_click", "wallet_verified", "referral_verified", "deposit_verified",
               "apply", "approve", "reject", "revoke", "tg_join"}
PUBLIC_EVENTS = {"view", "cta", "ref_click"}  # the only ones a browser may report; the rest are recorded by the server


class AccessError(Exception):
    """A request that isn't allowed right now. The message is shown to the member."""


# ---------------------------------------------------------------- rate limits (per process; one API instance)
_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_hits_lock = threading.Lock()
LIMITS = {"nonce": (10, 600), "verify": (10, 600), "recheck": (6, 600), "apply": (3, 86400), "invite": (20, 3600), "event": (60, 600), "apikey": (8, 600)}


def limit(action: str, who: str) -> None:
    n, window = LIMITS[action]
    now = time.monotonic()
    with _hits_lock:
        q = _hits[(action, who)]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= n:
            raise AccessError("Too many tries in a short time. Please wait a few minutes.")
        q.append(now)


def reset_limits_for_tests() -> None:
    with _hits_lock:
        _hits.clear()


# ---------------------------------------------------------------- settings
def settings(db: Session) -> TbSettings:
    s = db.scalar(select(TbSettings).limit(1))
    if s is None:
        s = TbSettings()
        db.add(s)
        db.flush()
    return s


SETTING_FIELDS = {"public_enabled": bool, "min_rank_level": int, "min_deposit_usd": float, "referral_code": str, "referral_url": str,
                  "telegram_group_id": str, "auto_verify": bool, "manual_approval": bool, "applications_per_day": int,
                  "invite_ttl_hours": int, "review_rules": str, "bot_username": str, "bot_wallet": str}


def codes(s: TbSettings) -> list[str]:
    """Accepted referral codes. The first is the one shown to members; the rest are what Hyperliquid may record for the
    same referrer (e.g. the link says BANTER, the wallet's record says KLGXFMNI)."""
    return [c.strip().upper() for c in s.referral_code.split(",") if c.strip()] or ["BANTER"]


def update_settings(db: Session, actor: str, values: dict[str, Any]) -> TbSettings:
    s = settings(db)
    changed = {}
    ranks = {r.level for r in ledger.ranks(db)}
    for k, v in values.items():
        if k not in SETTING_FIELDS or v is None:
            continue
        v = SETTING_FIELDS[k](v.strip() if isinstance(v, str) else v)
        if k == "min_rank_level" and ranks and v not in ranks:
            raise AccessError(f"There is no Kiwi rank {v}.")
        if k == "min_deposit_usd" and not 0 <= v <= 1_000_000:
            raise AccessError("The minimum deposit must be between $0 and $1,000,000.")
        if k == "referral_code":
            parts = [c.strip().upper() for c in v.split(",") if c.strip()]
            if not parts or not all(c.isalnum() and len(c) <= 32 for c in parts) or len(",".join(parts)) > 64:
                raise AccessError("Referral codes are letters and digits, separated by commas (e.g. BANTER,KLGXFMNI).")
            v = ",".join(parts)
        if k == "referral_url" and not v.startswith("https://app.hyperliquid.xyz/"):
            raise AccessError("The referral link must be an https://app.hyperliquid.xyz/ address.")
        if k == "telegram_group_id":
            v = (v or "").removeprefix("https://t.me/").removeprefix("t.me/") or None
            if v is not None and not v.startswith(("-", "@")) and not v.isdigit() and v.replace("_", "").isalnum():
                v = "@" + v  # "botpricechat" → "@botpricechat"
            if v is not None and not ((v.lstrip("-").isdigit() and v.startswith("-100")) or
                                      (v.startswith("@") and 5 <= len(v) <= 33 and v[1:].replace("_", "").isalnum())):
                raise AccessError("The group is its @username (e.g. @botpricechat) or its id (e.g. -1001234567890).")
        if k == "bot_username":
            v = v.removeprefix("https://t.me/").removeprefix("@")
            if not (v.replace("_", "").isalnum() and 5 <= len(v) <= 32):
                raise AccessError("The bot is its Telegram username, e.g. cryptopricechatbot.")
        if k == "bot_wallet":
            try:
                v = evm.normalize(v) if v else None
            except ValueError as e:
                raise AccessError("The bot's account is its 0x… Hyperliquid address.") from e
        if k in ("applications_per_day", "invite_ttl_hours") and not 1 <= v <= 10_000:
            raise AccessError("That number is out of range.")
        if getattr(s, k) != v:
            changed[k] = {"from": getattr(s, k), "to": v}
            setattr(s, k, v)
    if changed:
        audit(db, None, None, actor, "settings_changed", changed)
    db.commit()
    return s


def settings_out(s: TbSettings) -> dict[str, Any]:
    return {k: getattr(s, k) for k in SETTING_FIELDS}


# ---------------------------------------------------------------- helpers
def audit(db: Session, app: TbApplication | None, member_id: int | None, actor: str, action: str, details: dict[str, Any]) -> None:
    db.add(TbAudit(application_id=app.id if app else None, member_id=member_id if member_id is not None else (app.member_id if app else None),
                   actor=actor[:64], action=action[:48], details=details))


def track(db: Session, kind: str, member: Member | None = None, visitor: str | None = None, once: bool = False) -> None:
    """Record a funnel event. once=True: at most one per member (or visitor) ever, e.g. "rank_ok"."""
    if kind not in EVENT_KINDS:
        return
    v = hashlib.sha256(f"pc-tb|{visitor}".encode()).hexdigest() if visitor else None
    if once or kind == "view":
        q = select(TbEvent.id).where(TbEvent.kind == kind)
        q = q.where(TbEvent.member_id == member.id) if member else q.where(TbEvent.visitor == v)
        if kind == "view" and not once:  # views: once per visitor per day
            q = q.where(TbEvent.created_at >= utcnow() - timedelta(days=1))
        if (member or v) and db.scalar(q.limit(1)):
            return
    db.add(TbEvent(kind=kind, member_id=member.id if member else None, visitor=v))


def telegram_identity(db: Session, member: Member) -> LinkedIdentity | None:
    return db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == member.id, LinkedIdentity.platform == "telegram",
                                                  LinkedIdentity.status == "active"))


def wallet_of(db: Session, member: Member) -> TbWallet | None:
    return db.scalar(select(TbWallet).where(TbWallet.member_id == member.id))


def latest_application(db: Session, member: Member) -> TbApplication | None:
    return db.scalar(select(TbApplication).where(TbApplication.member_id == member.id).order_by(TbApplication.id.desc()).limit(1))


def _enc(v: str) -> str:
    return base64.b64encode(protect(v.encode())).decode()


def _dec(v: str) -> str:
    return unprotect(base64.b64decode(v)).decode()


def _aware(d: datetime | None) -> datetime | None:
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- the member's state
def rank_info(db: Session, member: Member, s: TbSettings) -> dict[str, Any]:
    from app.members.service import rank_out

    all_ranks = ledger.ranks(db)
    current = ledger.rank_for(db, member.rank_score)
    need = next((r for r in all_ranks if r.level == s.min_rank_level), all_ranks[-1] if all_ranks else current)
    nxt = next((r for r in all_ranks if r.level == current.level + 1), None)
    reached = current.level >= need.level
    kept = not reached and grandfathered(db, member)
    return {"current": rank_out(current), "required": rank_out(need), "ok": reached or kept, "grandfathered": kept,
            "xp": member.xp_total, "score": member.rank_score, "score_needed": need.min_xp,
            "remaining": max(0, need.min_xp - member.rank_score),
            # progress toward the required rank (or the next one once it's met)
            "progress": 1.0 if current.level >= need.level and nxt is None else
            min(1.0, member.rank_score / max(1, (need.min_xp if current.level < need.level else nxt.min_xp)))}


def grandfathered(db: Session, member: Member) -> bool:
    """Members who already connected the bot, traded a signal, connected an API wallet or chose paper trading keep Earn when
    the required rank goes up (Earn became the Scout Kiwi reward): nobody is locked out of their own positions."""
    if _paid(db, member, "earn.bot_connected") or _paid(db, member, "earn.first_trade"):
        return True
    t = trading_choice(db, member)
    return t["api"] or t["paper"]


def wallet_out(w: TbWallet | None) -> dict[str, Any] | None:
    if w is None:
        return None
    return {"address": evm.checksum(w.address), "verified_at": w.verified_at.isoformat(), "checked_at": w.checked_at.isoformat() if w.checked_at else None,
            "referral": w.referral_status, "referral_code_seen": w.referral_code_seen, "deposit": w.deposit_status,
            "deposit_usd": w.deposit_usd, "other_inflow_usd": w.other_inflow_usd, "account_value": (w.evidence or {}).get("account_value")}


def application_out(a: TbApplication | None) -> dict[str, Any] | None:
    if a is None:
        return None
    return {"id": a.public_id, "status": a.status, "created_at": a.created_at.isoformat(), "reviewed_at": a.reviewed_at.isoformat() if a.reviewed_at else None,
            "manual_review": a.needs_manual_review, "joined": a.joined_at is not None, "has_invite": bool(a.invite_link_enc),
            # a short, member-facing reason only when a reviewer chose to give one (notes starting with "member:")
            "message": a.review_notes.split("member:", 1)[1].strip() if "member:" in a.review_notes else None}


def state(db: Session, member: Member | None, admin: bool = False) -> dict[str, Any]:
    """Everything the funnel page shows, computed here. The browser renders it and can't change it."""
    s = settings(db)
    out: dict[str, Any] = {
        "open": s.public_enabled, "preview": admin and not s.public_enabled,
        "rules": {"min_rank_level": s.min_rank_level, "min_deposit_usd": s.min_deposit_usd, "referral_code": codes(s)[0],
                  "referral_url": s.referral_url, "telegram_ready": bool(s.telegram_group_id)},
        "signed_in": member is not None,
    }
    if member is None:
        return {**out, "stage": "LOCKED", "journey": 0, "steps": {"passport": "todo", "rank": "locked", "hyperliquid": "locked",
                                                                   "wallet": "locked", "deposit": "locked", "telegram": "locked", "apply": "locked"}}
    rk = rank_info(db, member, s)
    w = wallet_of(db, member)
    tg = telegram_identity(db, member)
    app = latest_application(db, member)
    ref = w.referral_status if w else "pending"
    dep = w.deposit_status if w else "pending"
    steps = {
        "passport": "done",
        "rank": "done" if rk["ok"] else "todo",
        "wallet": "done" if w else "todo",  # open to all: the first wallet gives a free level
        "hyperliquid": {"verified": "done", "not_referred": "failed", "unverified": "review"}.get(ref, "todo" if rk["ok"] else "locked"),
        "deposit": {"verified": "done", "insufficient": "failed", "unverified": "review"}.get(dep, "todo" if w else "locked"),
        "telegram": "done" if tg else ("todo" if rk["ok"] else "locked"),
    }
    can, why = can_apply(db, member, s, rk, w, tg, app)
    steps["apply"] = "done" if app and app.status in OPEN else ("todo" if can else "locked")
    if _paid(db, member, "earn.bot_connected"):
        stage = "APPROVED"  # joined the group and connected the bot: access is complete
    elif app and app.status in APP_STATUSES and not (app.status == "REJECTED" and can):
        stage = app.status
    elif not rk["ok"]:
        stage = "LOCKED"
    elif not w:
        stage = "IN_PROGRESS"
    elif ref == "verified" and dep == "verified":
        stage = "ELIGIBLE"
    else:
        stage = "PENDING_VERIFICATION"
    journey = {"LOCKED": 1 if rk["score"] > 0 else 0, "IN_PROGRESS": 1, "PENDING_VERIFICATION": 2, "ELIGIBLE": 3, "PENDING_APPROVAL": 3,
               "APPROVED": 4, "REJECTED": 3, "REVOKED": 3}[stage]
    if rk["ok"]:
        track(db, "rank_ok", member, once=True)
    from app.models.members import ReputationEvent

    wallet_xp = db.scalar(select(ReputationEvent.xp).where(ReputationEvent.member_id == member.id, ReputationEvent.event_type == "wallet.verified"))
    return {**out, "stage": stage, "journey": journey, "steps": steps, "member": {"username": member.username, "id": member.public_id},
            "wallet_xp": int(wallet_xp or 0),  # what connecting the wallet paid (shown so it's visible on Earn too)
            "tiers": tiers_out(w), "bot": {"username": s.bot_username, "track_record": bool(s.bot_wallet)},
            "access": {"ready": access_ready(db, member)[0], "group": _paid(db, member, "earn.joined_group"),
                       "bot": _paid(db, member, "earn.bot_connected"), "group_url": group_url(s)},
            "flow": {"hyperliquid": _paid(db, member, "earn.joined_hyperliquid"), "first_trade": completed(db, member),
                     "has_wallet": w is not None},
            "trading": trading_choice(db, member),
            "rank": rk, "wallet": wallet_out(w), "telegram": {"username": tg.username, "linked": True} if tg else {"linked": False},
            "application": application_out(app), "can_apply": can, "apply_blocker": why}


def can_apply(db: Session, member: Member, s: TbSettings, rk: dict[str, Any], w: TbWallet | None, tg: LinkedIdentity | None,
              app: TbApplication | None) -> tuple[bool, str | None]:
    if not rk["ok"]:
        return False, f"Reach Kiwi Rank {s.min_rank_level} first."
    if w is None:
        return False, "Verify your wallet first."
    if w.referral_status == "not_referred":
        return False, f"Hyperliquid doesn't show referral code {codes(s)[0]} on this wallet."
    if w.deposit_status == "insufficient":
        return False, f"Hyperliquid's records don't show ${s.min_deposit_usd:,.0f} deposited to this wallet yet."
    if "pending" in (w.referral_status, w.deposit_status):
        return False, "Check your wallet with Hyperliquid first."
    if tg is None:
        return False, "Connect your Telegram account (it's how you'll get into the group)."
    if app and app.status in OPEN:
        return False, "You've already applied."
    if app and app.status == "REVOKED":
        return False, "Your access was revoked. Contact the team if you think that's a mistake."
    if app and app.status == "REJECTED" and utcnow() - _aware(app.reviewed_at or app.created_at) < REAPPLY_AFTER:
        return False, "Your application wasn't approved. You can apply again 14 days after the decision."
    return True, None


# ---------------------------------------------------------------- wallet proof
def message_for(member: Member, address: str, nonce: str, issued: datetime, domain: str) -> str:
    return (f"{domain} asks you to prove you own this wallet.\n\n"
            "Signing this message is free. It does not send a transaction, move funds or give any permission.\n\n"
            f"Wallet: {evm.checksum(address)}\nKiwi Passport: {member.public_id}\nNonce: {nonce}\n"
            f"Issued: {issued.strftime('%Y-%m-%dT%H:%M:%SZ')}\nValid for 10 minutes, once.")


def new_nonce(db: Session, member: Member, address: str, domain: str) -> dict[str, str]:
    address = evm.normalize(address)
    limit("nonce", f"m{member.id}")
    now = utcnow()
    for old in db.scalars(select(TbNonce).where(TbNonce.member_id == member.id, TbNonce.used_at.is_(None))):
        old.used_at = now  # one live challenge per member
    n = secrets.token_hex(16)
    msg = message_for(member, address, n, now, domain)
    db.add(TbNonce(member_id=member.id, address=address, nonce=n, message=msg, expires_at=now + NONCE_TTL))
    db.commit()
    return {"message": msg, "nonce": n}


def verify_wallet(db: Session, member: Member, nonce: str, signature: str, client: InfoClient | None) -> TbWallet:
    """Check the signature over the exact message we issued (single use, unexpired, this member), then bind the wallet."""
    limit("verify", f"m{member.id}")
    now = utcnow()
    n = db.scalar(select(TbNonce).where(TbNonce.nonce == str(nonce)[:64], TbNonce.member_id == member.id))
    if n is None or n.used_at is not None or _aware(n.expires_at) < now:
        raise AccessError("That signing request expired or was already used. Start again.")
    n.used_at = now  # spent even if the signature turns out wrong: no retries on one nonce
    db.commit()
    try:
        signer = evm.recover(n.message, signature)
    except ValueError as e:
        raise AccessError(str(e)) from e
    if signer != n.address:
        raise AccessError("That signature came from a different wallet than the one you connected.")
    other = db.scalar(select(TbWallet).where(TbWallet.address == signer, TbWallet.member_id != member.id))
    if other is not None:
        raise AccessError("This wallet is already verified on another Kiwi Passport.")
    w = wallet_of(db, member)
    if w is None:
        w = TbWallet(member_id=member.id, address=signer, verified_at=now)
        db.add(w)
    elif w.address != signer:  # a new wallet: earlier results don't carry over
        audit(db, None, member.id, f"member:{member.id}", "wallet_replaced", {"from": w.address, "to": signer})
        w.address, w.verified_at, w.checked_at = signer, now, None
        w.referral_status = w.deposit_status = "pending"
        w.referral_code_seen, w.deposit_usd, w.other_inflow_usd, w.evidence = None, 0.0, 0.0, {}
    else:
        w.verified_at = now
    audit(db, None, member.id, f"member:{member.id}", "wallet_verified", {"address": signer})
    track(db, "wallet_verified", member, once=True)
    free_level(db, member, signer)
    db.commit()
    if client is not None and settings(db).auto_verify:
        run_check(db, member, w, client)
    return w


# Deposit tiers: cumulative deposits into the member's own Hyperliquid account (Hyperliquid's deposit records only).
# Each tier pays its XP once. (usd, xp, name)
TIERS = [(10, 50, "Bronze"), (100, 150, "Silver"), (1000, 400, "Gold"), (10000, 1000, "Diamond")]


def tier_xp(db: Session, member: Member, w: TbWallet) -> int:
    """Pay every deposit tier this wallet's verified deposits have reached and the member hasn't been paid for. Returns XP paid."""
    from app.members.catalog import ensure_catalog

    ensure_catalog(db)
    paid = 0
    for usd, xp, name in TIERS:
        if w.deposit_usd < usd:
            break
        try:
            r = ledger.award(db, member, "deposit.tier", f"tier:{usd}", xp=xp, claim_key=f"{w.address}:{usd}", commit=False,
                             source="wallet", reason=f"{name} tier: ${usd:,} deposited on Hyperliquid", actor="hyperliquid")
        except ValueError:
            return paid
        paid += r.xp if r.awarded else 0
    return paid


def tiers_out(w: TbWallet | None) -> dict[str, Any]:
    dep = w.deposit_usd if w else 0.0
    reached = [t for t in TIERS if dep >= t[0]]
    nxt = next((t for t in TIERS if dep < t[0]), None)
    return {"deposited": dep, "tiers": [{"usd": u, "xp": x, "name": n, "reached": dep >= u} for u, x, n in TIERS],
            "current": reached[-1][2] if reached else None,
            "next": {"usd": nxt[0], "xp": nxt[1], "name": nxt[2], "missing": round(nxt[0] - dep, 2)} if nxt else None}


WALLET_XP_MIN = 100  # the wallet reward is at least this, or a whole level when that's more


def free_level(db: Session, member: Member, address: str) -> int:
    """The one-time reward for verifying a wallet: at least WALLET_XP_MIN XP, or enough to reach the next Kiwi rank if more. Once per
    Passport (max_per_member=1) and once per wallet (claim key), so one wallet can't level up many accounts.
    Returns the XP paid (0 if already claimed, at the top rank, or refused by the ledger's rules)."""
    import math

    from app.members.catalog import ensure_catalog
    from app.members.reputation import settings as rep_settings

    ensure_catalog(db)  # the "wallet.verified" rule exists before the first reward
    current = ledger.rank_for(db, member.rank_score)
    nxt = next((r for r in ledger.ranks(db) if r.level == current.level + 1), None)
    weight = rep_settings(db).price_chat_weight or 1.0
    need = max(WALLET_XP_MIN, math.ceil((nxt.min_xp - member.rank_score) / weight) if nxt else 0)
    try:
        r = ledger.award(db, member, "wallet.verified", f"wallet:{address}", xp=need, claim_key=address, commit=False, source="wallet",
                         reason=f"Connected a wallet: +{need} XP" + (f", {nxt.name} unlocked" if nxt and need * weight >= nxt.min_xp - member.rank_score else ""),
                         actor=f"member:{member.id}")
    except ValueError:  # the rule is missing (catalog not seeded): no reward, never a crash
        return 0
    return r.xp if r.awarded else 0


# ---------------------------------------------------------------- sign in with Ethereum
def login_message(address: str, nonce: str, issued: datetime, domain: str) -> str:
    return (f"{domain} asks you to sign in to Price Chat with this wallet.\n\n"
            "Signing this message is free. It does not send a transaction, move funds or give any permission.\n\n"
            f"Wallet: {evm.checksum(address)}\nNonce: {nonce}\nIssued: {issued.strftime('%Y-%m-%dT%H:%M:%SZ')}\nValid for 10 minutes, once.")


def login_nonce(db: Session, address: str, domain: str, who: str) -> dict[str, str]:
    address = evm.normalize(address)
    limit("nonce", f"login:{who}")
    now = utcnow()
    n = secrets.token_hex(16)
    msg = login_message(address, n, now, domain)
    db.add(TbNonce(member_id=None, address=address, nonce=n, message=msg, expires_at=now + NONCE_TTL))
    db.commit()
    return {"message": msg, "nonce": n}


def login_verify(db: Session, nonce: str, signature: str, who: str) -> tuple[Member, bool]:
    """(member, created): the Passport this wallet belongs to, or a new one bound to it. The signature must be over the exact
    sign-in message we issued (single use, unexpired)."""
    from app.members import service as members

    limit("verify", f"login:{who}")
    now = utcnow()
    n = db.scalar(select(TbNonce).where(TbNonce.nonce == str(nonce)[:64], TbNonce.member_id.is_(None)))
    if n is None or n.used_at is not None or _aware(n.expires_at) < now:
        raise AccessError("That sign-in request expired or was already used. Try again.")
    n.used_at = now
    db.commit()
    try:
        signer = evm.recover(n.message, signature)
    except ValueError as e:
        raise AccessError(str(e)) from e
    if signer != n.address:
        raise AccessError("That signature came from a different wallet than the one you connected.")
    w = db.scalar(select(TbWallet).where(TbWallet.address == signer))
    if w is not None:
        m = db.get(Member, w.member_id)
        if m is None or m.status != "active":
            raise AccessError("This Passport can't sign in.")
        return m, False
    m = members.create_member(db)
    db.add(TbWallet(member_id=m.id, address=signer, verified_at=now))
    audit(db, None, m.id, f"member:{m.id}", "wallet_signup", {"address": signer})
    track(db, "wallet_verified", m, once=True)
    free_level(db, m, signer)
    db.commit()
    return m, True


def run_check(db: Session, member: Member, w: TbWallet, client: InfoClient) -> TbWallet:
    s = settings(db)
    c = check(client, w.address, codes(s), s.min_deposit_usd)
    before = (w.referral_status, w.deposit_status)
    w.referral_status, w.referral_code_seen = c.referral_status, c.referral_code
    w.deposit_status, w.deposit_usd, w.other_inflow_usd = c.deposit_status, c.deposit_usd, c.other_inflow_usd
    w.evidence, w.checked_at = c.evidence, utcnow()
    if c.referral_status == "verified":
        track(db, "referral_verified", member, once=True)
    if c.deposit_status == "verified":
        track(db, "deposit_verified", member, once=True)
    tier_xp(db, member, w)  # deposit tiers pay XP once each ($10 / $100 / $1,000 / $10,000)
    if before != (w.referral_status, w.deposit_status):
        audit(db, None, member.id, "hyperliquid", "wallet_checked", {"referral": c.referral_status, "deposit": c.deposit_status,
                                                                     "deposit_usd": c.deposit_usd, "other_inflow_usd": c.other_inflow_usd})
    db.commit()
    return w


def recheck(db: Session, member: Member, client: InfoClient) -> TbWallet:
    w = wallet_of(db, member)
    if w is None:
        raise AccessError("Verify your wallet first.")
    if w.checked_at and utcnow() - _aware(w.checked_at) < RECHECK_EVERY:
        return w
    limit("recheck", f"m{member.id}")
    return run_check(db, member, w, client)


# ---------------------------------------------------------------- applications
def apply(db: Session, member: Member) -> TbApplication:
    s = settings(db)
    rk = rank_info(db, member, s)
    w, tg, prev = wallet_of(db, member), telegram_identity(db, member), latest_application(db, member)
    ok, why = can_apply(db, member, s, rk, w, tg, prev)
    if not ok:
        raise AccessError(why or "You can't apply yet.")
    since = utcnow() - timedelta(days=1)
    if (db.scalar(select(func.count()).select_from(TbApplication).where(TbApplication.created_at >= since)) or 0) >= s.applications_per_day:
        raise AccessError("We've reached today's application limit. Please try again tomorrow.")
    limit("apply", f"m{member.id}")
    assert w is not None and tg is not None
    manual = w.referral_status != "verified" or w.deposit_status != "verified"
    a = TbApplication(public_id=str(uuid.uuid4()), member_id=member.id, status="PENDING_APPROVAL", rank_level=rk["current"]["level"],
                      xp=member.xp_total, wallet=w.address, referral_status=w.referral_status, deposit_status=w.deposit_status,
                      deposit_usd=w.deposit_usd, telegram_user_id=int(tg.platform_user_id), telegram_username=tg.username,
                      needs_manual_review=manual,
                      evidence={"rank": rk["current"], "rank_score": member.rank_score, "wallet": wallet_out(w), "hyperliquid": w.evidence,
                                "checked_at": w.checked_at.isoformat() if w.checked_at else None})
    db.add(a)
    db.flush()
    audit(db, a, None, f"member:{member.id}", "applied", {"manual_review": manual, "referral": w.referral_status, "deposit": w.deposit_status})
    track(db, "apply", member)
    if not s.manual_approval and not manual:  # everything proved and the owner chose automatic approval
        _decide(db, a, "APPROVED", "auto", "Automatic approval: rank, referral and deposit all verified.")
    db.commit()
    return a


def _decide(db: Session, a: TbApplication, status: str, actor: str, notes: str) -> None:
    a.status, a.reviewed_by, a.reviewed_at = status, actor[:64], utcnow()
    if notes:
        a.review_notes = (a.review_notes + "\n" if a.review_notes else "") + notes.strip()[:2000]
    audit(db, a, None, actor, status.lower(), {"notes": notes.strip()[:500]})
    track(db, {"APPROVED": "approve", "REJECTED": "reject", "REVOKED": "revoke"}[status], db.get(Member, a.member_id))


def review(db: Session, public_id: str, decision: str, actor: str, notes: str = "") -> TbApplication:
    """approve / reject (pending only) · revoke (approved only). Idempotent: repeating a decision changes nothing."""
    a = db.scalar(select(TbApplication).where(TbApplication.public_id == public_id))
    if a is None:
        raise LookupError("No such application.")
    target = {"approve": "APPROVED", "reject": "REJECTED", "revoke": "REVOKED"}.get(decision)
    if target is None:
        raise AccessError("Unknown decision.")
    if a.status == target:
        return a  # already done: a double click or a retry
    allowed = {"APPROVED": ("PENDING_APPROVAL",), "REJECTED": ("PENDING_APPROVAL",), "REVOKED": ("APPROVED",)}[target]
    if a.status not in allowed:
        raise AccessError(f"An application that is {a.status} can't be {target.lower()}.")
    if target == "REJECTED" and not notes.strip():
        raise AccessError("Add a short reason for the record.")
    _decide(db, a, target, actor, notes)
    if target == "REVOKED":
        a.revoked_at = utcnow()
    db.commit()
    return a


# ---------------------------------------------------------------- funnel v2: simple, one-time steps
# 1 Kiwi Rank · 2 Join Hyperliquid (the referral link, once) · 3 Join the Telegram group + connect the bot · 4 the first signal.
# Each step is done once, pays its XP once and disappears. Then the member sees the PnL leaderboard instead of the funnel.
BOT_SELF_XP = 25  # "I gave the bot my API" when Hyperliquid can't confirm an API wallet (verified: the rule's 100)
SIGNAL_ACTIONS = ("join", "skip", "auto")


def joined_hyperliquid(db: Session, member: Member) -> dict[str, Any]:
    """The member opened Hyperliquid with our referral link from Earn: the join step is done (once, +XP)."""
    from app.members.catalog import ensure_catalog

    if _paid(db, member, "earn.joined_hyperliquid"):
        return {"done": True, "xp": 0}
    ensure_catalog(db)
    r = _award_step(db, member, "earn.joined_hyperliquid", "earn:hyperliquid", source="earn", commit=False,
                     reason="Joined Hyperliquid with the Price Chat link")
    track(db, "ref_click", member)
    db.commit()
    return {"done": True, "xp": r.xp if r.awarded else 0}


def confirm_bot(db: Session, member: Member, client: InfoClient | None) -> dict[str, Any]:
    """"Did you give the bot your API? Confirm." Done at once; if Hyperliquid shows an approved API wallet on the member's
    verified wallet it's confirmed (+100), otherwise it's on their word (+25)."""
    from app.members.catalog import ensure_catalog
    from app.tradingbot.hyperliquid import HyperliquidError

    if _paid(db, member, "earn.bot_connected"):
        return {"done": True, "xp": 0}
    w = wallet_of(db, member)
    verified = False
    if w is not None and client is not None:
        try:
            now_ms = utcnow().timestamp() * 1000
            verified = any(not a.get("validUntil") or float(a["validUntil"]) > now_ms for a in client.agents(w.address))
        except HyperliquidError:
            verified = False
    ensure_catalog(db)
    r = _award_step(db, member, "earn.bot_connected", "earn:bot", claim_key=f"bot:{w.address}" if (w and verified) else None,
                     xp=None if verified else BOT_SELF_XP, source="earn", commit=False,
                     reason="Connected the trading bot" + ("" if verified else " (on your word)"))
    audit(db, None, member.id, f"member:{member.id}", "bot_connected", {"verified": verified})
    db.commit()
    return {"done": True, "xp": r.xp if r.awarded else 0, "verified": verified}


def bot_started(db: Session, member: Member) -> bool:
    """The member pressed Start in the bot with their linked Telegram account: the Telegram step is done (verified, not on
    their word: the bot saw it). Once per member."""
    from app.members.catalog import ensure_catalog

    if _paid(db, member, "earn.bot_connected"):
        return False
    ensure_catalog(db)
    _award_step(db, member, "earn.bot_connected", "earn:bot", xp=BOT_SELF_XP, source="earn", commit=False, reason="Started the trading bot")
    audit(db, None, member.id, f"member:{member.id}", "bot_connected", {"verified": True, "via": "start"})
    db.commit()
    return True


def trading_choice(db: Session, member: Member) -> dict[str, bool]:
    """Step 4: a Hyperliquid API wallet connected (verified on Hyperliquid at connect time), or paper trading chosen."""
    from app.models.trading import TrAccount
    from app.models.tradingos import TsAudit

    acc = db.scalar(select(TrAccount).where(TrAccount.member_id == member.id))
    api = bool(acc and acc.mainnet_agent_key and acc.mainnet_approved_at)
    paper = bool(db.scalar(select(TsAudit.id).where(TsAudit.actor == f"m:{member.id}", TsAudit.action == "paper_chosen").limit(1)))
    return {"api": api, "paper": paper, "live": bool(acc and acc.live_enabled_at and acc.mode == "LIVE")}


def _signal_source(db: Session) -> Any:
    from app.models.telegram import TelegramSource

    s = settings(db)
    name = (s.telegram_group_id or "").lstrip("@").lower()
    if not name:
        return None
    return db.scalar(select(TelegramSource).where(func.lower(TelegramSource.username) == name))


def signals(db: Session, limit_n: int = 8) -> dict[str, Any]:
    """The bot's latest posts in the signal group (bots and channel posts only: members' own messages are never shown)."""
    from sqlalchemy import or_

    from app.models.telegram import TelegramMessage

    src = _signal_source(db)
    if src is None:
        return {"connected": False, "items": []}
    rows = db.scalars(select(TelegramMessage).where(TelegramMessage.source_id == src.id, or_(
        TelegramMessage.author_is_bot.is_(True), TelegramMessage.author_id.is_(None))).order_by(TelegramMessage.sent_at.desc()).limit(limit_n))
    items = [{"id": m.id, "text": (m.text or "")[:1200], "at": m.sent_at.isoformat(), "author": m.author_name or "Bot"} for m in rows]
    return {"connected": True, "items": list(reversed(items))}


def signal_action(db: Session, member: Member, signal_id: int, action: str) -> dict[str, Any]:
    """Join / skip / auto on a bot signal. The first one completes the funnel (+XP once). Trades themselves happen in the bot."""
    from app.members.catalog import ensure_catalog
    from app.members.missions import record_action

    if action not in SIGNAL_ACTIONS:
        raise AccessError("Unknown action.")
    record_action(db, member, f"signal_{action}", "telegram", f"signal:{signal_id}")
    ensure_catalog(db)
    r = _award_step(db, member, "earn.first_trade", "earn:first_trade", source="earn", commit=False,
                     reason={"join": "Joined your first bot signal", "skip": "Reviewed your first bot signal",
                             "auto": "Turned on auto-trading with the bot"}[action])
    audit(db, None, member.id, f"member:{member.id}", f"signal_{action}", {"signal": signal_id})
    db.commit()
    return {"done": True, "xp": r.xp if r.awarded else 0, "action": action}


def completed(db: Session, member: Member) -> bool:
    return _paid(db, member, "earn.first_trade")


def reset_earn(db: Session, member: Member, actor: str, reason: str) -> dict[str, Any]:
    """Owner tool: the member starts Earn over. Removes the earn.* XP events (with any reversals) and their one-time claims
    so the steps can be done and paid again, the applications, and the trading account (refused while a bot position is
    open). Keeps the Passport, other XP, linked accounts and the verified wallet. Audited."""
    from sqlalchemy import delete, or_

    from app.models.members import ReputationEvent, XPClaim
    from app.models.trading import TrAccount, TrPosition

    acc = db.scalar(select(TrAccount).where(TrAccount.member_id == member.id))
    if acc and db.scalar(select(TrPosition.id).where(TrPosition.account_id == acc.id,
                                                     TrPosition.status.in_(("OPENING", "OPEN", "PROTECTION_FAILED"))).limit(1)):
        raise ValueError("The member has an open bot position: close it first.")
    ids = list(db.scalars(select(ReputationEvent.id).where(ReputationEvent.member_id == member.id, ReputationEvent.event_type.like("earn.%"))))
    w = wallet_of(db, member)
    hashes = [ledger.claim_hash("earn.joined_group", f"tg:{t.platform_user_id}")
              for t in db.scalars(select(LinkedIdentity).where(LinkedIdentity.member_id == member.id, LinkedIdentity.platform == "telegram"))]
    if w:
        hashes.append(ledger.claim_hash("earn.bot_connected", f"bot:{w.address}"))
    if ids:
        db.execute(delete(ReputationEvent).where(or_(ReputationEvent.id.in_(ids), ReputationEvent.reverses_id.in_(ids))))
    freed = db.execute(delete(XPClaim).where(XPClaim.claim_hash.in_(hashes))).rowcount if hashes else 0
    apps = db.execute(delete(TbApplication).where(TbApplication.member_id == member.id)).rowcount
    from app.models.tradingos import TsSignalAction

    # like a new member: no earlier signal answers (Join / Skip / Auto / ENTER / REJECT) and no funnel history
    signal_answers = db.execute(delete(TsSignalAction).where(or_(TsSignalAction.member_id == member.id,
                                                                 TsSignalAction.who == f"m:{member.id}"))).rowcount
    db.execute(delete(TbEvent).where(TbEvent.member_id == member.id))
    if acc:
        db.delete(acc)
    db.flush()
    ledger.recalc(db, member)
    ledger.audit(db, member.id, actor, "earn_reset", {"reason": reason, "events": len(ids), "claims": freed, "applications": apps, "signal_answers": signal_answers,
                                                     "trading_account": acc is not None})
    db.commit()
    return {"reset": True, "events": len(ids), "claims": freed, "applications": apps, "signal_answers": signal_answers, "trading_account": acc is not None,
            "rank_level": member.rank_level, "xp_total": member.xp_total}


def leaderboard_members(db: Session) -> list[tuple[Member, TbWallet]]:
    """Members with a valid referral: their verified wallet is one Hyperliquid lists as referred with our code (their
    trades and PnL come from Hyperliquid). Opted-in only."""
    rows = db.execute(select(Member, TbWallet).join(TbWallet, TbWallet.member_id == Member.id).where(
        TbWallet.referral_status == "verified", Member.status == "active", Member.leaderboard_opt_out.is_(False)))
    return [(m, w) for m, w in rows]


# ---------------------------------------------------------------- getting access: join the group, connect the bot
MEMBER_STATUSES = {"member", "administrator", "creator"}
SELF_REPORTED_GROUP_XP = 10  # "I joined" when Telegram can't confirm it (yet): approved on the member's word, small XP
BOT_GROUP = "@botpricechat"  # the signals group: membership in THIS group is the step (the main Price Chat group doesn't count)


def _award_step(db: Session, member: Member, event_type: str, source_ref: str, **kw: Any) -> Any:
    """Pay an Earn step's XP, and mark the step done EVEN IF the XP is refused (daily cap, cooldown, a wallet that already
    claimed it...): a refused reward must never send the member back to a step they completed (the "Open Hyperliquid" loop)."""
    from app.models.members import ReputationEvent

    r = ledger.award(db, member, event_type, source_ref, **kw)
    if not r.awarded and not _paid(db, member, event_type):
        rule, now = ledger.get_rule(db, event_type), utcnow()
        db.add(ReputationEvent(member_id=member.id, event_type=event_type, xp=0, category=rule.category, source="earn", source_ref=source_ref[:128],
                               reason=f"{kw.get('reason') or rule.label} (step done; no XP: {r.refused})"[:256], occurred_at=now,
                               day=ledger.day_of(now), actor="system"))
        db.flush()
    return r


def _paid(db: Session, member: Member, event_type: str) -> bool:
    from app.models.members import ReputationEvent

    return db.scalar(select(ReputationEvent.id).where(ReputationEvent.member_id == member.id, ReputationEvent.event_type == event_type)) is not None


def access_ready(db: Session, member: Member) -> tuple[bool, str | None]:
    """Steps 1-3 done: the rank, a verified wallet, the referral and the deposit."""
    s = settings(db)
    if not rank_info(db, member, s)["ok"]:
        return False, f"Reach Kiwi Rank {s.min_rank_level} first."
    if not _paid(db, member, "earn.joined_hyperliquid"):
        return False, "Join Hyperliquid first."
    return True, None


def group_url(s: TbSettings) -> str | None:
    gid = s.telegram_group_id or ""
    return f"https://t.me/{gid[1:]}" if gid.startswith("@") else None


async def check_group(db: Session, member: Member, bot: Any) -> dict[str, Any]:
    """Is the member's linked Telegram account in the bot group? Telegram answers (getChatMember); pays the XP once."""
    from app.members.catalog import ensure_catalog

    limit("recheck", f"g{member.id}")
    s = settings(db)
    if _paid(db, member, "earn.joined_group"):
        return {"done": True, "xp": 0}
    tg = telegram_identity(db, member)
    # Only the bot group (@botpricechat) counts. Telegram confirms -> full XP. If Telegram says they're NOT in it, the step
    # stays open ("join @botpricechat first"). Only when Telegram can't tell at all (Telegram not linked, or the bot can't
    # see the group) is it approved on the member's word, with small XP.
    # Only "not a member" answered by Telegram FOR @botpricechat ITSELF can refuse; any other group's answer, or no answer
    # (the bot can't see the group, Telegram down), never blocks: that was the "I joined" loop.
    verified, refused = False, False
    if tg is not None and bot is not None:
        for gid in dict.fromkeys([BOT_GROUP, s.telegram_group_id]):
            if not gid:
                continue
            try:
                info = await bot.get_chat_member(gid, int(tg.platform_user_id))
            except Exception:  # noqa: BLE001 - can't see that group: try the next
                continue
            status = info.get("status")
            if status in MEMBER_STATUSES or (status == "restricted" and info.get("is_member")):
                verified = True
                break
            if gid == BOT_GROUP and status in ("left", "kicked"):
                refused = True
    if refused and not verified:
        who = f"@{tg.username}" if tg and tg.username else "the Telegram account linked to your Passport"
        return {"done": False, "xp": 0, "message": f"Telegram says {who} isn't in {BOT_GROUP} yet. Join it with that account, then press "
                                                   f"\"I joined\" again."}
    ensure_catalog(db)
    r = _award_step(db, member, "earn.joined_group", "earn:group", claim_key=f"tg:{tg.platform_user_id}" if tg else None,
                     xp=None if verified else SELF_REPORTED_GROUP_XP, source="earn", commit=False,
                     reason="Joined t.me/botpricechat" + ("" if verified else " (on your word: Telegram couldn't confirm it)"))
    track(db, "tg_join", member, once=True)
    audit(db, None, member.id, "telegram" if verified else f"member:{member.id}", "joined_bot_group", {"verified": verified})
    db.commit()
    return {"done": True, "xp": r.xp if r.awarded else 0, "verified": verified}


def check_bot(db: Session, member: Member, client: InfoClient) -> dict[str, Any]:
    """Has the member connected the trading bot? /connect in the bot approves an API wallet ("agent") on their Hyperliquid
    account, which Hyperliquid lists publicly. Pays the XP once. Price Chat never sees or stores any key."""
    from app.members.catalog import ensure_catalog
    from app.tradingbot.hyperliquid import HyperliquidError

    limit("recheck", f"b{member.id}")
    if _paid(db, member, "earn.bot_connected"):
        return {"done": True, "xp": 0}
    ok, why = access_ready(db, member)
    if not ok:
        raise AccessError(why or "Finish the steps above first.")
    w = wallet_of(db, member)
    assert w is not None
    try:
        agents = client.agents(w.address)
    except HyperliquidError as e:
        raise AccessError("Hyperliquid couldn't be checked right now. Try again in a moment.") from e
    now_ms = utcnow().timestamp() * 1000
    live = [a for a in agents if not a.get("validUntil") or float(a["validUntil"]) > now_ms]
    if not live:
        return {"done": False, "xp": 0, "message": "Not connected yet. In the bot press /connect and finish on Hyperliquid."}
    ensure_catalog(db)
    r = _award_step(db, member, "earn.bot_connected", "earn:bot", claim_key=f"bot:{w.address}", reason="Connected the trading bot",
                     source="earn", commit=False)
    w.evidence = {**(w.evidence or {}), "agents": [{"name": a.get("name"), "address": a.get("address")} for a in live][:5]}
    track(db, "approve", member, once=True)
    audit(db, None, member.id, "hyperliquid", "bot_connected", {"agents": len(live)})
    db.commit()
    return {"done": True, "xp": r.xp if r.awarded else 0}


# ---------------------------------------------------------------- the private group
async def invite(db: Session, member: Member, bot: Any) -> dict[str, Any]:
    """The member's personal join-request link, only while APPROVED, only for the Telegram account the application is
    bound to. The link alone gets nobody in: the bot approves the join request only from that account."""
    limit("invite", f"m{member.id}")
    s = settings(db)
    a = latest_application(db, member)
    if a is None or a.status != "APPROVED":
        raise PermissionError("Private access is only for approved applications.")
    tg = telegram_identity(db, member)
    if tg is None or int(tg.platform_user_id) != a.telegram_user_id:
        raise PermissionError("Connect the same Telegram account you applied with.")
    if not s.telegram_group_id:
        raise AccessError("The private group isn't set up yet. The team has been told; please check back soon.")
    if bot is None:
        raise AccessError("Telegram isn't connected on our side right now. Please try again shortly.")
    now = utcnow()
    if a.invite_link_enc and a.invite_expires_at and _aware(a.invite_expires_at) > now + timedelta(minutes=5):
        return {"url": _dec(a.invite_link_enc), "expires_at": _aware(a.invite_expires_at).isoformat()}
    if a.invite_link_enc:  # expired or about to: retire it before making a new one
        try:
            await bot.revoke_invite_link(s.telegram_group_id, _dec(a.invite_link_enc))
        except Exception:  # noqa: BLE001 - an already-expired link can't be revoked; that's fine
            pass
    expires = now + timedelta(hours=s.invite_ttl_hours)
    try:
        link = await bot.create_join_request_link(s.telegram_group_id, f"pc-{a.public_id[:8]}", int(expires.timestamp()))
    except Exception as e:  # noqa: BLE001
        a.invite_error = f"{type(e).__name__}: {str(e)[:200]}"
        audit(db, a, None, "telegram", "invite_failed", {"error": a.invite_error})
        db.commit()
        raise AccessError("Telegram didn't create your invite. The team has been told; please try again later.") from e
    a.invite_link_enc, a.invite_created_at, a.invite_expires_at, a.invite_error = _enc(link), now, expires, None
    audit(db, a, None, "telegram", "invite_created", {"expires_at": expires.isoformat()})  # never the link itself
    db.commit()
    return {"url": link, "expires_at": expires.isoformat()}


async def revoke_access(db: Session, a: TbApplication, bot: Any) -> None:
    """After a revoke: retire the invite and take the member out of the group if they joined."""
    s = settings(db)
    if bot is None or not s.telegram_group_id:
        return
    if a.invite_link_enc:
        try:
            await bot.revoke_invite_link(s.telegram_group_id, _dec(a.invite_link_enc))
        except Exception:  # noqa: BLE001
            pass
        a.invite_link_enc = None
    if a.telegram_user_id and a.joined_at and not a.left_at:
        try:
            await bot.remove_member(s.telegram_group_id, a.telegram_user_id)
            a.left_at = utcnow()
            audit(db, a, None, "telegram", "removed_from_group", {})
        except Exception as e:  # noqa: BLE001
            audit(db, a, None, "telegram", "remove_failed", {"error": f"{type(e).__name__}"})
    db.commit()


def _is_group(chat: dict[str, Any], gid: str) -> bool:
    """Is this chat the configured group? It's set as a numeric id or as an @username (a public group)."""
    if gid.startswith("@"):
        return ("@" + str(chat.get("username") or "")).lower() == gid.lower()
    return str(chat.get("id")) == gid


async def on_update(db: Session, bot: Any, update: dict[str, Any]) -> bool:
    """A chat_join_request / chat_member update from the bot's poll. True when it was for the private group (handled here).
    Every join request is approved (the group is open to everyone since 2026-10-19); an APPROVED application bound to
    that Telegram user is marked as join-requested."""
    s = settings(db)
    if not s.telegram_group_id:
        return False
    if "chat_join_request" in update:
        r = update["chat_join_request"]
        if not _is_group(r.get("chat") or {}, s.telegram_group_id):
            return False
        uid = int((r.get("from") or {}).get("id") or 0)
        a = db.scalar(select(TbApplication).where(TbApplication.telegram_user_id == uid, TbApplication.status == "APPROVED")
                      .order_by(TbApplication.id.desc()).limit(1))
        await bot.approve_join_request(s.telegram_group_id, uid)
        if a is not None:
            a.join_requested_at = utcnow()
        audit(db, a, None, "telegram", "join_request_approved", {} if a else {"telegram_user_id": uid})
        db.commit()
        return True
    if "chat_member" in update:
        u = update["chat_member"]
        if not _is_group(u.get("chat") or {}, s.telegram_group_id):
            return False
        new = u.get("new_chat_member") or {}
        uid = int((new.get("user") or {}).get("id") or 0)
        a = db.scalar(select(TbApplication).where(TbApplication.telegram_user_id == uid).order_by(TbApplication.id.desc()).limit(1))
        if a is not None:
            if new.get("status") in ("member", "administrator", "creator", "restricted"):
                if a.joined_at is None or a.left_at is not None:
                    a.joined_at, a.left_at = utcnow(), None
                    audit(db, a, None, "telegram", "joined_group", {})
                    track(db, "tg_join", db.get(Member, a.member_id), once=True)
            elif new.get("status") in ("left", "kicked"):
                a.left_at = utcnow()
                audit(db, a, None, "telegram", "left_group", {"status": new.get("status")})
            db.commit()
        return True
    return False


# ---------------------------------------------------------------- admin views
def stats(db: Session, days: int = 30) -> dict[str, Any]:
    since = utcnow() - timedelta(days=days)
    ev = lambda k: int(db.scalar(select(func.count()).select_from(TbEvent).where(TbEvent.kind == k, TbEvent.created_at >= since)) or 0)  # noqa: E731
    members = lambda k: int(db.scalar(select(func.count(distinct(TbEvent.member_id))).where(TbEvent.kind == k, TbEvent.created_at >= since)) or 0)  # noqa: E731
    by = lambda col: dict(db.execute(select(col, func.count()).group_by(col)).all())  # noqa: E731
    apps = by(TbApplication.status)
    s = settings(db)
    passport = members("passport")
    rank_ok = members("rank_ok")
    return {
        "days": days,
        "funnel": [  # in order; each is a distinct count, so the steps can be compared
            {"key": "view", "label": "Funnel visitors", "n": ev("view")},
            {"key": "cta", "label": "Clicked Check my eligibility", "n": ev("cta")},
            {"key": "passport", "label": "Signed-in Passports", "n": passport},
            {"key": "rank_ok", "label": f"Reached Rank {s.min_rank_level}", "n": rank_ok},
            {"key": "ref_click", "label": "Opened the referral link (a click, not a signup)", "n": ev("ref_click")},
            {"key": "wallet_verified", "label": "Wallets verified", "n": members("wallet_verified")},
            {"key": "referral_verified", "label": "Referral verified by Hyperliquid", "n": members("referral_verified")},
            {"key": "deposit_verified", "label": "Deposit verified by Hyperliquid", "n": members("deposit_verified")},
            {"key": "apply", "label": "Applications", "n": members("apply")},
            {"key": "approve", "label": "Approved", "n": members("approve")},
            {"key": "tg_join", "label": "Joined the Telegram group", "n": members("tg_join")},
        ],
        "below_rank": max(0, passport - rank_ok),
        "rank_rate": round(rank_ok / passport, 3) if passport else None,
        "wallets": {"total": int(db.scalar(select(func.count()).select_from(TbWallet)) or 0),
                    "referral": by(TbWallet.referral_status), "deposit": by(TbWallet.deposit_status)},
        "applications": {k: int(apps.get(k, 0)) for k in APP_STATUSES},
        "in_group": int(db.scalar(select(func.count()).select_from(TbApplication).where(
            TbApplication.status == "APPROVED", TbApplication.joined_at.is_not(None), TbApplication.left_at.is_(None))) or 0),
    }


def queue(db: Session, status: str | None = None, limit_n: int = 100) -> list[dict[str, Any]]:
    q = select(TbApplication, Member).join(Member, Member.id == TbApplication.member_id)
    if status:
        q = q.where(TbApplication.status == status)
    q = q.order_by(TbApplication.status != "PENDING_APPROVAL", TbApplication.id.desc()).limit(limit_n)
    out = []
    for a, m in db.execute(q).all():
        log = db.scalars(select(TbAudit).where(TbAudit.application_id == a.id).order_by(TbAudit.id)).all()
        out.append({
            "id": a.public_id, "status": a.status, "created_at": a.created_at.isoformat(), "member": {"username": m.username, "id": m.public_id},
            "rank_level": a.rank_level, "xp": a.xp, "wallet": evm.checksum(a.wallet), "referral": a.referral_status, "deposit": a.deposit_status,
            "deposit_usd": a.deposit_usd, "telegram": {"id": a.telegram_user_id, "username": a.telegram_username},
            "manual_review": a.needs_manual_review, "notes": a.review_notes, "reviewed_by": a.reviewed_by,
            "reviewed_at": a.reviewed_at.isoformat() if a.reviewed_at else None,
            "invite": {"created_at": a.invite_created_at.isoformat() if a.invite_created_at else None,
                       "expires_at": a.invite_expires_at.isoformat() if a.invite_expires_at else None, "error": a.invite_error},
            "joined_at": a.joined_at.isoformat() if a.joined_at else None, "left_at": a.left_at.isoformat() if a.left_at else None,
            "evidence": a.evidence,  # what Hyperliquid returned at the time (public, on-chain data); never the invite link
            "audit": [{"at": x.created_at.isoformat(), "actor": x.actor, "action": x.action, "details": x.details} for x in log],
        })
    return out
