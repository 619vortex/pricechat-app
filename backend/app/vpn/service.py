"""Kiwi VPN: who may use it, members' devices, servers and the 5-minute sync with each node agent.

Kiwi VPN: one free 30-day trial per account, then $9.99 for 30 days renewed by hand; free for Royal Kiwis and above. Devices belong to an account: a Passport member's, or an anonymous
key's (no member at all). An account has access while it has paid time (app/vpn/billing.py) or its member an unexpired manual
grant of the `kiwi_vpn` entitlement. Out of time, its devices are switched off and kept for KEEP_LAPSED, so paying again just
works; blocked or banned, they go at once. The same goes for browsers (the extension's logins on a server's proxy). Kiwi Rank plays no part. A device is created by allocating a tunnel IP, adding the peer on the
node, then committing; if the node call fails nothing is kept. Only the public key is ever stored.
"""

import base64
import binascii
import hashlib
import ipaddress
import logging
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import notify
from app.members.catalog import ensure_catalog
from app.models.members import Entitlement, Member, MemberEntitlement
from app.models.vpn import VpnAccount, VpnBrowser, VpnDevice, VpnPayment, VpnServer, VpnSettings
from app.security.secrets import protect, unprotect
from app.vpn import billing, health, keys, node, reviewer, trial_guard

OFFLINE_AFTER = 2  # checks in a row that couldn't reach a server's agent before it's marked offline (one could be a blip)

logger = logging.getLogger(__name__)

ENTITLEMENT = "kiwi_vpn"
MAX_DEVICES_LIMIT = 10
MAX_CAP_GB = 100000
GB = 1024 ** 3  # the website shows sizes in powers of 1024
TRIAL_DAYS = 30
MAX_TESTERS = 10
MAX_TESTER_DAYS = 90
ROYAL_LEVEL = 7  # Royal Kiwi on the rank ladder; higher ranks count too
LIVE_WINDOW = timedelta(minutes=10)  # the server reads handshakes and proxy use every 5 minutes
SETTINGS_DEFAULTS = {"enabled": False, "min_rank": "warrior", "max_devices": 3, "monthly_cap_gb": 0, "payments_enabled": False, "trial_enabled": True,  # min_rank: unused column, kept
                     "trial_one_per_identity": True, "trial_min_account_age_hours": 24, "trial_min_xp": 0, "trial_per_ip_day": 3,
                     "trial_per_network_day": 10, "sync_enforce_limits": False, "count_pc_once": False, "trial_guard_mode": "monitor"}
GUARD_MODES = ("off", "monitor", "enforce")  # off: guards not evaluated; monitor: evaluated and recorded, never refused; enforce: refused
# Admin-settable trial guards: (lowest, highest). 0 switches that check off. See app/vpn/trial_guard.py and docs/hardening/TRIAL-AND-LIMITS.md.
TRIAL_GUARDS = {"trial_min_account_age_hours": (0, 24 * 90), "trial_min_xp": (0, 1_000_000), "trial_per_ip_day": (0, 1000), "trial_per_network_day": (0, 10_000)}
SWITCHES = ("enabled", "trial_enabled", "payments_enabled", "trial_one_per_identity", "sync_enforce_limits", "count_pc_once")
OWNER_MAX_DEVICES = 50  # devices made in Admin; the server's capacity still applies
NOT_SUBSCRIBED = "Kiwi VPN is $9.99 for 30 days. Add time to start."
START_TRIAL = "Start your free 30-day trial on the Kiwi VPN page."
PAID_UNAVAILABLE = "Your free trial has ended. The $9.99 plan isn't available yet."
# the apps show `reason` as it is and can't take a payment, so once paying is possible the end of a trial or paid time says where to pay
TRIAL_ENDED = "Your free trial has ended. Add time on the Kiwi VPN page at pricechat.live/vpn: $9.99 for 30 days, and nothing renews by itself."
TIME_ENDED = "Your Kiwi VPN time has run out. Add time on the Kiwi VPN page at pricechat.live/vpn: $9.99 for 30 days, and nothing renews by itself."
KEEP_LAPSED = timedelta(days=30)
Who = Member | VpnAccount


class VpnError(ValueError):
    """A problem the member (or admin) can act on; the message is shown. `status` is the HTTP status."""

    def __init__(self, message: str, status: int = 400, code: str | None = None, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code  # a machine-readable reason, sent as the X-Kiwi-Reason header (the trial's refusals)
        self.retry_after = retry_after


# --- secrets ---------------------------------------------------------------------------------------------------------


def enc(token: str) -> str:
    return base64.b64encode(protect(token.encode())).decode()


def agent_token(server: VpnServer) -> str:
    return unprotect(base64.b64decode(server.agent_token_enc)).decode()


# --- settings & the gate -----------------------------------------------------------------------------------------------


def settings(db: Session) -> VpnSettings:
    row = db.get(VpnSettings, 1)
    if row is None:
        row = VpnSettings(id=1, **SETTINGS_DEFAULTS)
        db.add(row)
        db.flush()
    return row


def settings_out(s: VpnSettings) -> dict[str, Any]:
    return {"enabled": s.enabled, "payments_enabled": s.payments_enabled, "trial_enabled": s.trial_enabled, "max_devices": s.max_devices, "monthly_cap_gb": s.monthly_cap_gb,
            "trial_one_per_identity": s.trial_one_per_identity, "trial_min_account_age_hours": s.trial_min_account_age_hours, "trial_min_xp": s.trial_min_xp,
            "trial_per_ip_day": s.trial_per_ip_day, "trial_per_network_day": s.trial_per_network_day, "sync_enforce_limits": s.sync_enforce_limits,
            "count_pc_once": s.count_pc_once, "trial_guard_mode": s.trial_guard_mode}


def _entitlement(db: Session) -> Entitlement | None:
    ensure_catalog(db)
    return db.scalar(select(Entitlement).where(Entitlement.key == ENTITLEMENT))


@dataclass
class Rules:
    """What qualifies() needs that is the same for every member: read once per server sync, not once per member."""

    ent: Entitlement | None
    grants: dict[int, MemberEntitlement] | None = None  # member id -> the VPN grant, for the members being checked
    people: dict[int, Member] | None = None
    free: bool = False  # payments are off: nobody needs paid time


def account_of(db: Session, who: Who) -> VpnAccount:
    if isinstance(who, VpnAccount):
        return who
    acct = db.scalar(select(VpnAccount).where(VpnAccount.member_id == who.id))
    if acct is None:
        acct = VpnAccount(member_id=who.id)
        db.add(acct)
        db.flush()
    return acct


def rules(db: Session, member_ids: list[int] | None = None) -> Rules:
    ent = _entitlement(db)
    grants = None
    if member_ids is not None:
        grants = {} if ent is None else {g.member_id: g for g in db.scalars(select(MemberEntitlement).where(
            MemberEntitlement.entitlement_id == ent.id, MemberEntitlement.member_id.in_(member_ids)))}
    people = {m.id: m for m in db.scalars(select(Member).where(Member.id.in_(member_ids)))} if member_ids else ({} if member_ids is not None else None)
    return Rules(ent, grants, people, free=is_free(settings(db)))


def access(db: Session, acct: VpnAccount, r: Rules | None = None, now: datetime | None = None) -> str:
    """active (free right now, paid time or a manual grant) | lapsed (none of them: devices off, kept for a while) | blocked (devices go now)."""
    now = now or utcnow()
    member = None
    if acct.member_id is not None:
        member = r.people.get(acct.member_id) if r is not None and r.people is not None else db.get(Member, acct.member_id)
        if member is None or member.status != "active":
            return "blocked"
    if acct.status != "active":
        return "blocked"
    if reviewer.is_reviewer(db, member):
        return "active" if reviewer.in_time(now) else "blocked"
    if is_tester(acct, now):
        return "active"
    if acct.is_owner or (r.free if r is not None else is_free(settings(db))):
        return "active"
    if is_royal(member):
        return "active"
    if (until := _aware(acct.paid_until)) is not None and until > now:
        return "active"
    if (ends := _aware(acct.trial_ends_at)) is not None and ends > now:
        return "active"
    ent = r.ent if r is not None else _entitlement(db)
    if member is not None and ent is not None and ent.enabled:
        if r is not None and r.grants is not None:
            grant = r.grants.get(member.id)
        else:
            grant = db.scalar(select(MemberEntitlement).where(MemberEntitlement.member_id == member.id,
                                                              MemberEntitlement.entitlement_id == ent.id))
        if grant is not None and (grant.expires_at is None or _aware(grant.expires_at) > now):
            return "active"
    return "lapsed"


def is_free(s: VpnSettings) -> bool:
    """Free for every member: only with both payments and the trial switched off in Admin."""
    return not s.payments_enabled and not s.trial_enabled


def no_access_reason(db: Session, acct: VpnAccount) -> str:
    s = settings(db)
    if s.trial_enabled and acct.member_id is not None and acct.trial_started_at is None:
        member = _member_of(db, acct)
        why = _trial_guards(db, member, s) if member is not None and s.trial_guard_mode == "enforce" else []
        return why[0][1] if why else START_TRIAL
    if not s.payments_enabled:
        return PAID_UNAVAILABLE
    return TIME_ENDED if acct.paid_until is not None else TRIAL_ENDED if acct.trial_started_at is not None else NOT_SUBSCRIBED


def is_royal(member: Member | None) -> bool:
    """Royal Kiwi or higher, from the rank the server keeps. XP under review doesn't count."""
    return member is not None and member.status == "active" and not member.xp_suspended and member.rank_level >= ROYAL_LEVEL


def _member_of(db: Session, acct: VpnAccount) -> Member | None:
    return db.get(Member, acct.member_id) if acct.member_id is not None else None


def _days_left(until: datetime, now: datetime) -> int:
    return max(0, -((now - until) // timedelta(days=1)))


def plan_out(db: Session, acct: VpnAccount, s: VpnSettings, state: str, now: datetime | None = None) -> dict[str, Any]:
    """What the account is on, for showing: kind, its end date (none for Royal or free) and the days left."""
    now = now or utcnow()
    member = _member_of(db, acct)
    paid, trial = _aware(acct.paid_until), _aware(acct.trial_ends_at)
    dated = lambda kind, until: {"kind": kind, "until": until.isoformat(), "days_left": _days_left(until, now)}  # noqa: E731
    if state == "blocked":
        return {"kind": "blocked", "until": None, "days_left": None}
    if state == "active":
        if acct.is_owner:
            return {"kind": "owner", "until": None, "days_left": None}
        if is_tester(acct, now):
            return dated("tester", _aware(acct.tester_until))
        if is_royal(member):
            return {"kind": "royal", "until": None, "days_left": None}
        if is_free(s):
            return {"kind": "free", "until": None, "days_left": None}
        if paid is not None and paid > now:
            return dated("paid", paid)
        if trial is not None and trial > now:
            return dated("trial", trial)
        return {"kind": "granted", "until": None, "days_left": None}
    return {"kind": "expired" if paid is not None or trial is not None else "not_activated", "until": None, "days_left": None}


TRIAL_OFF = "The free trial isn't available right now."
TRIAL_USED = "You've already used your free trial."


def _trial_guards(db: Session, member: Member, s: VpnSettings, now: datetime | None = None) -> list[tuple[str, str]]:
    """Every (code, message) of an abuse guard that would stop this Passport starting a trial: one trial per identity,
    the account's age, its XP. The per-address throttle is separate (it needs the request: trial_guard.ip_wait)."""
    now = now or utcnow()
    out: list[tuple[str, str]] = []
    if s.trial_one_per_identity and (label := trial_guard.used_identity(db, member)) is not None:
        out.append(("identity_used", f"A free trial has already been used with this {label}. It's one free trial per person."))
    if s.trial_min_account_age_hours > 0 and (made := _aware(member.created_at)) is not None:
        ready = made + timedelta(hours=s.trial_min_account_age_hours)
        if ready > now:
            hours = max(1, -((now - ready) // timedelta(hours=1)))
            out.append(("account_too_new", (f"Free trials open once a Kiwi Passport is {s.trial_min_account_age_hours} hours old. "
                                            f"Yours can start one in about {hours} hour{'s' if hours != 1 else ''}.")))
    if s.trial_min_xp > 0:
        xp = 0 if member.xp_suspended else int(member.xp_total or 0)
        if xp < s.trial_min_xp:
            out.append(("needs_xp", f"Free trials need {s.trial_min_xp} XP on your Kiwi Passport. You have {xp}."))
    return out


def trial_unavailable(db: Session, acct: VpnAccount, s: VpnSettings, preview: bool = False, now: datetime | None = None) -> tuple[str, str, int] | None:
    """(code, message, HTTP status) when this account can't start the free trial right now, else None. Everything but the per-address throttle."""
    if not s.trial_enabled or not (s.enabled or preview):
        return "off", TRIAL_OFF, 403
    member = _member_of(db, acct)
    if member is None:
        return "no_passport", "The free trial needs a Kiwi Passport.", 403
    if acct.trial_started_at is not None:
        return "used", TRIAL_USED, 409
    state = access(db, acct, now=now)
    if state == "active":
        return "has_access", "You already have Kiwi VPN.", 409
    if state != "lapsed":
        return "blocked", "Your account can't use Kiwi VPN.", 403
    if s.trial_guard_mode != "enforce":
        return None  # off or monitor: the abuse guards never refuse
    why = _trial_guards(db, member, s, now)
    return (why[0][0], why[0][1], 403) if why else None


def start_trial(db: Session, who: Member, preview: bool = False, ip: str | None = None, charge: Callable[[], None] | None = None) -> None:
    """One 30-day trial per account and per verified identity, only while the VPN is open and the trial is on. The one write that sets it can only win once.

    ip: the caller's real address (trial_guard.real_ip), or None when it isn't known to be theirs (never throttled).
    charge: the route's rate limit, spent only once every check has passed (security audit M1)."""
    s = settings(db)
    db.commit()
    acct = account_of(db, who)
    db.commit()
    why = trial_unavailable(db, acct, s, preview)
    if why is not None:
        raise VpnError(why[1], why[2], code=why[0])
    mode = s.trial_guard_mode
    would: list[str] = []  # monitor: what enforce would have refused for
    if mode != "off":
        if mode == "monitor":
            would = [code for code, _ in _trial_guards(db, who, s)]
        ip_wait, net_wait = trial_guard.ip_waits(ip, s.trial_per_ip_day, s.trial_per_network_day)
        if mode == "enforce" and (ip_wait or net_wait):
            raise VpnError("Too many free trials have started from your network today. Please try again later.", 429, code="network_limit",
                           retry_after=max(w for w in (ip_wait, net_wait) if w))
        would += (["ip_limit"] if ip_wait else []) + (["network_limit"] if net_wait else [])
    if charge is not None:
        charge()
    now = utcnow()
    done = db.execute(update(VpnAccount).where(VpnAccount.id == acct.id, VpnAccount.trial_started_at.is_(None))
                      .values(trial_started_at=now, trial_ends_at=now + timedelta(days=TRIAL_DAYS)))
    if done.rowcount != 1:
        db.commit()
        raise VpnError(TRIAL_USED, 409, code="used")
    trial_guard.mark(db, who)  # in every mode: enforcing later must know who has had a trial
    if would:
        trial_guard.record_signals(db, who.id, would, ip)
    db.commit()
    if mode != "off":
        trial_guard.ip_record(ip)
    db.refresh(acct)


def public_stats(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """Only what the server really knows. There's no uptime monitoring, so uptime is None."""
    now = now or utcnow()
    counts = _device_counts(db)
    servers = list(db.scalars(select(VpnServer).where(VpnServer.enabled.is_(True), VpnServer.testers_only.is_(False)).order_by(VpnServer.name, VpnServer.id)))
    best = recommended_id(servers, counts)
    online = [v for v in servers if v.online]
    since = now - LIVE_WINDOW
    live = set(db.scalars(select(VpnDevice.account_id).where(VpnDevice.last_handshake >= since))) | set(db.scalars(select(VpnBrowser.account_id).where(VpnBrowser.last_active >= since)))
    return {"active_users": len(live), "countries": len({(v.country_code or "").upper() for v in online if v.country_code}), "online_servers": len(online), "uptime": None,
            "servers": [{"id": v.id, "name": v.name, "region": v.region, "country_code": v.country_code, "online": v.online, "recommended": v.id == best, "browser": bool(v.proxy_host)} for v in servers]}


def qualifies(db: Session, who: Who, r: Rules | None = None) -> tuple[bool, str | None]:
    """The account-level rule, regardless of whether the VPN is open."""
    state = access(db, account_of(db, who), r)
    if state == "active":
        return True, None
    return False, "Your account can't use Kiwi VPN." if state == "blocked" else no_access_reason(db, account_of(db, who))


def sees(acct: VpnAccount | None, server: VpnServer) -> bool:
    """A testers-only server is shown to, and usable by, the owner and current testers only."""
    return not server.testers_only or (acct is not None and (acct.is_owner or is_tester(acct)))


def is_tester(acct: VpnAccount, now: datetime | None = None) -> bool:
    until = _aware(acct.tester_until)
    return until is not None and until > (now or utcnow()) and acct.status == "active"


def set_tester(db: Session, username: str, days: int, now: datetime | None = None) -> dict[str, Any]:
    """A named member may use the VPN while it's closed, until the date. Days 0 ends it. Nothing else about the VPN changes for anyone."""
    now = now or utcnow()
    member = db.scalar(select(Member).where(func.lower(Member.username) == username.strip().lower()))
    if member is None:
        raise VpnError("No member has that username.", 404)
    if member.status != "active":
        raise VpnError("That member's account isn't active.", 409)
    acct = account_of(db, member)
    if days <= 0:
        acct.tester_until = None
        db.commit()
        return {"username": member.username, "tester_until": None}
    if not 1 <= days <= MAX_TESTER_DAYS:
        raise VpnError(f"Testers can be added for 1 to {MAX_TESTER_DAYS} days.", 400)
    others = db.scalar(select(func.count()).select_from(VpnAccount).where(VpnAccount.tester_until > now, VpnAccount.id != acct.id)) or 0
    if others >= MAX_TESTERS:
        raise VpnError(f"There can be at most {MAX_TESTERS} testers. End one first.", 409)
    acct.tester_until = now + timedelta(days=days)
    db.commit()
    return {"username": member.username, "tester_until": _iso(acct.tester_until)}


def _testing(db: Session, who: Who) -> bool:
    acct = who if isinstance(who, VpnAccount) else db.scalar(select(VpnAccount).where(VpnAccount.member_id == who.id))
    return acct is not None and is_tester(acct)


def _reviewing(db: Session, who: Who) -> bool:
    """The store-review account, within its time (app/vpn/reviewer.py)."""
    member = who if isinstance(who, Member) else db.get(Member, who.member_id) if who.member_id is not None else None
    return reviewer.is_reviewer(db, member) and reviewer.in_time()


CLOSED = "Kiwi VPN is unavailable right now. Please try again later."


def eligibility(db: Session, member: Who, preview: bool = False) -> tuple[bool, str | None]:
    """preview: the owner, signed in to Admin, testing while the VPN is closed to everyone else."""
    s = settings(db)
    if not s.enabled and not preview and not _reviewing(db, member) and not _testing(db, member):
        return False, CLOSED
    return qualifies(db, member)


def update_settings(db: Session, changes: dict[str, Any]) -> dict[str, Any]:
    s = settings(db)
    if changes.get("max_devices") is not None:
        n = int(changes["max_devices"])
        if not 1 <= n <= MAX_DEVICES_LIMIT:
            raise VpnError(f"Devices per member: 1 to {MAX_DEVICES_LIMIT}.", 400)
        s.max_devices = n
    if changes.get("monthly_cap_gb") is not None:
        cap = int(changes["monthly_cap_gb"])
        if not 0 <= cap <= MAX_CAP_GB:
            raise VpnError(f"Monthly data per member: 0 (unlimited) to {MAX_CAP_GB} GB.", 400)
        s.monthly_cap_gb = cap  # the next sync pauses / resumes members to match
    for key, (low, high) in TRIAL_GUARDS.items():
        if changes.get(key) is not None:
            n = int(changes[key])
            if not low <= n <= high:
                raise VpnError(f"{key}: {low} (off) to {high}.", 400)
            setattr(s, key, n)
    if changes.get("trial_guard_mode") is not None:
        if changes["trial_guard_mode"] not in GUARD_MODES:
            raise VpnError("trial_guard_mode: off, monitor or enforce.", 400)
        s.trial_guard_mode = changes["trial_guard_mode"]
    for key in SWITCHES:  # payments_enabled: the next sync switches off whoever has no time left, and back on
        if changes.get(key) is not None:
            setattr(s, key, bool(changes[key]))
    db.commit()
    return settings_out(s)


# --- output ------------------------------------------------------------------------------------------------------------


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def device_out(d: VpnDevice) -> dict[str, Any]:
    return {"id": d.id, "name": d.name, "server_id": d.server_id, "ip": d.ip, "created_at": d.created_at.isoformat(),
            "last_handshake": _iso(d.last_handshake), "rx_bytes": d.rx_bytes, "tx_bytes": d.tx_bytes,
            "paused_until": _iso(d.paused_until)}


def device_counts(db: Session) -> dict[int, int]:
    return dict(db.execute(select(VpnDevice.server_id, func.count()).group_by(VpnDevice.server_id)).all())  # type: ignore[arg-type]


_device_counts = device_counts


def _load(server: VpnServer, count: int) -> float:
    return round(min(1.0, count / server.capacity), 4) if server.capacity > 0 else 1.0


def server_traffic(db: Session, now: datetime | None = None) -> dict[int, int]:
    """Bytes each server carried this month (its devices' and browser logins' up + down: roughly what the provider bills
    as the server's outgoing traffic, since everything a client sends or receives leaves the server once)."""
    month = _month(now or utcnow())
    out: dict[int, int] = {}
    for model in (VpnDevice, VpnBrowser):
        for sid, n in db.execute(select(model.server_id, func.sum(model.rx_bytes + model.tx_bytes))
                                 .where(model.usage_month == month).group_by(model.server_id)).all():
            if sid is not None:
                out[sid] = out.get(sid, 0) + int(n or 0)
    return out


def live_counts(db: Session, now: datetime | None = None) -> dict[int, int]:
    """Devices and browser logins in use on each server now (seen in the last sync window): Smart Connect's load signal."""
    since = (now or utcnow()) - LIVE_WINDOW
    out: dict[int, int] = {}
    for sid, n in db.execute(select(VpnDevice.server_id, func.count()).where(VpnDevice.last_handshake >= since).group_by(VpnDevice.server_id)).all():
        out[sid] = out.get(sid, 0) + int(n)
    for sid, n in db.execute(select(VpnBrowser.server_id, func.count()).where(VpnBrowser.last_active >= since).group_by(VpnBrowser.server_id)).all():
        out[sid] = out.get(sid, 0) + int(n)
    return out


def recommended_id(servers: list[VpnServer], counts: dict[int, int]) -> int | None:
    """The best server for a new device: online and not full, the lowest load, ties -> the lowest id."""
    ok = [v for v in servers if v.online and counts.get(v.id, 0) < v.capacity]
    return min(ok, key=lambda v: (_load(v, counts.get(v.id, 0)), v.id)).id if ok else None


def _month(now: datetime) -> str:
    return now.strftime("%Y-%m")


def next_month(now: datetime) -> datetime:
    """00:00 UTC on the 1st of the month after `now`."""
    return datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=timezone.utc)


def _aware(d: datetime | None) -> datetime | None:
    return d.replace(tzinfo=timezone.utc) if d is not None and d.tzinfo is None else d


def _paused(dev: VpnDevice, now: datetime) -> bool:
    until = _aware(dev.paused_until)
    return until is not None and until > now


def account_usage(db: Session, account_id: int, now: datetime | None = None) -> int:
    """Bytes (down + up) the account's devices and browsers used this UTC month."""
    month = _month(now or utcnow())
    return sum(int(db.scalar(select(func.coalesce(func.sum(t.rx_bytes + t.tx_bytes), 0))
                             .where(t.account_id == account_id, t.usage_month == month)) or 0) for t in (VpnDevice, VpnBrowser))


def usage_out(db: Session, who: Who, s: VpnSettings, now: datetime | None = None) -> dict[str, Any]:
    """This month's data for the member page: used / cap (0 = unlimited) and, over the cap, when it resumes."""
    now = now or utcnow()
    acct = account_of(db, who)
    paused = [u for d in db.scalars(select(VpnDevice).where(VpnDevice.account_id == acct.id))
              if _paused(d, now) and (u := _aware(d.paused_until)) is not None]
    return {"month": _month(now), "used_bytes": account_usage(db, acct.id, now), "cap_bytes": s.monthly_cap_gb * GB,
            "paused_until": _iso(max(paused)) if paused else None}


def _dns(server: VpnServer) -> list[str]:
    """The server's own resolver: the first DNS its agent reports (no logs; with `adblock` on it also blocks ads &
    trackers), or Cloudflare if it reports none."""
    dns = [str(d) for d in server.dns or []]
    return dns[:1] or ["1.1.1.1"]


def _v6_pool(value: str) -> str:
    """The agent's IPv6 pool if it's a valid v6 interface, else "" (IPv4 only)."""
    try:
        return value if value and ipaddress.ip_interface(value).version == 6 else ""
    except ValueError:
        return ""


def overview(db: Session, who: Who, preview: bool = False) -> dict[str, Any]:
    payments = settings(db).payments_enabled
    db.commit()  # the first call seeds the settings row
    bill = billing.options() if payments else None  # may ask the provider: before anything here can hold a write transaction
    s = settings(db)
    acct = account_of(db, who)
    ok, reason = eligibility(db, acct, preview)
    counts = _device_counts(db)
    servers = [v for v in db.scalars(select(VpnServer).where(VpnServer.enabled.is_(True)).order_by(VpnServer.name, VpnServer.id)) if sees(acct, v)]
    review = _reviewing(db, acct)
    if review:
        servers = [v for v in servers if v.name == reviewer.SERVER]
    best = recommended_id(servers, counts)
    live = live_counts(db)
    state_of = health.member_health(db, servers)
    devices = db.scalars(select(VpnDevice).where(VpnDevice.account_id == acct.id).order_by(VpnDevice.created_at, VpnDevice.id))
    open_here = s.enabled or preview or review or is_tester(acct)
    out = {"enabled": open_here, "test_mode": preview and not s.enabled, "payments": payments, "eligible": ok, "reason": reason, "max_devices": 1 if review else s.max_devices,
           "servers": [{"id": v.id, "name": v.name, "region": v.region, "country_code": v.country_code, "online": v.online,
                        "load": _load(v, counts.get(v.id, 0)), "live_load": _load(v, live.get(v.id, 0)), "adblock": v.adblock, "recommended": v.id == best,
                        "browser": bool(v.proxy_host), "proxy_host": v.proxy_host or None, "health": state_of.get(v.id, "ok")} for v in servers],
           "devices": [device_out(d) for d in devices], "usage": usage_out(db, acct, s),
           "paid_until": _iso(acct.paid_until), "anonymous": acct.member_id is None, "has_card": bool(acct.stripe_customer), "billing": bill}
    state = access(db, acct)
    member = _member_of(db, acct)
    # While the VPN is closed to this person, a plan can't be used, so none is claimed. Their trial or paid time is kept and shows again when it opens.
    out["plan"] = plan_out(db, acct, s, state) if open_here else {"kind": "closed", "until": None, "days_left": None}
    out["royal"] = is_royal(member)
    why = trial_unavailable(db, acct, s, preview)
    out["trial"] = {"available": why is None, "days": TRIAL_DAYS, "code": why[0] if why else None, "reason": why[1] if why else None}
    db.commit()  # first call seeds the settings row
    return out


# --- devices -----------------------------------------------------------------------------------------------------------


def _used_ips(db: Session, server_id: int) -> set[str]:
    return set(db.scalars(select(VpnDevice.ip).where(VpnDevice.server_id == server_id)))


def allocate_ip(db: Session, server: VpnServer, skip: set[str] | frozenset[str] = frozenset()) -> str:
    """The lowest free tunnel IP in the server's pool: never the network, the server itself, or a .0 / .1 / .255."""
    try:
        iface = ipaddress.ip_interface(server.address)
    except ValueError:
        raise VpnError("This server has no address pool yet. Try again shortly.", 503) from None
    used = _used_ips(db, server.id) | set(skip)
    for host in iface.network.hosts():
        if host == iface.ip or int(host) & 0xFF in (0, 1, 255):
            continue
        ip = str(host)
        if ip not in used:
            return ip
    raise VpnError("This server is full. Pick another one.", 503)


def clean_name(name: str) -> str:
    n = " ".join((name or "").split())
    if not 1 <= len(n) <= 32:
        raise VpnError("Device name: 1 to 32 characters.", 400)
    return n


APP_DEVICE = re.compile(r"^(.+) \((\d+)\)$")  # the Windows app names its device on each server "<PC> (<server id>)"


def slot_key(name: str, server_id: int, device_id: int | None) -> tuple[str, Any]:
    """Which device slot a device takes with count_pc_once on: the Windows app's devices of one PC share one ("pc", PC name),
    when the number in the name is the device's own server; anything else is a slot of its own."""
    m = APP_DEVICE.match(name or "")
    if m and int(m.group(2)) == int(server_id):
        return "pc", m.group(1)
    return "dev", device_id if device_id is not None else ("new", name, server_id)


def slots_used(devices: list[tuple[int | None, str, int]], count_pc_once: bool) -> int:
    """How many device slots these (id, name, server id) devices use. Off: one each. On: one per PC (security audit L6),
    plus one for each extra device of that PC on the same server (two devices on one server are two devices)."""
    if not count_pc_once:
        return len(devices)
    per: dict[tuple[str, Any], dict[int, int]] = {}
    for dev_id, name, server_id in devices:
        on = per.setdefault(slot_key(name, server_id, dev_id), {})
        on[server_id] = on.get(server_id, 0) + 1
    return sum(max(on.values()) for on in per.values())


def _slots_full(db: Session, acct_id: int, max_devices: int, name: str, server_id: int, count_pc_once: bool) -> bool:
    mine = [(i, n, sid) for i, n, sid in db.execute(select(VpnDevice.id, VpnDevice.name, VpnDevice.server_id).where(VpnDevice.account_id == acct_id)).all()]
    return slots_used(mine + [(None, name, server_id)], count_pc_once) > max_devices


def _checked_server(db: Session, acct_id: int, server_id: int, max_devices: int, name: str = "", count_pc_once: bool = False) -> VpnServer:
    db.scalar(select(VpnAccount.id).where(VpnAccount.id == acct_id).with_for_update())  # one device creation per account at a time
    if _slots_full(db, acct_id, max_devices, name, server_id, count_pc_once):
        raise VpnError(f"You can have up to {max_devices} devices. Delete one first.", 409)
    server = db.get(VpnServer, server_id)
    if server is None or not server.enabled or not sees(db.get(VpnAccount, acct_id), server):
        raise VpnError("Server not found.", 404)
    if not server.online or not server.public_key or not server.endpoint_host:
        raise VpnError("This server is offline right now. Pick another one or try again later.", 503)
    if db.scalar(select(func.count()).select_from(VpnDevice).where(VpnDevice.server_id == server.id)) >= server.capacity:
        raise VpnError("This server is full. Pick another one.", 503)
    return server


def owner_account(db: Session) -> VpnAccount:
    acct = db.scalar(select(VpnAccount).where(VpnAccount.is_owner.is_(True)))
    if acct is None:
        acct = VpnAccount(is_owner=True)
        db.add(acct)
        db.flush()
    return acct


def clean_public_key(value: str) -> str:
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise VpnError("That isn't a valid public key.", 400) from None
    if len(raw) != 32 or raw == bytes(32):
        raise VpnError("That isn't a valid public key.", 400)
    return value


def peer_out(dev: VpnDevice, server: VpnServer) -> dict[str, Any]:
    """Everything an app that holds its own private key needs to build its config. Nothing here is secret."""
    ip6 = keys.ipv6_for(server.address, dev.ip, server.address6)
    return {"server_public_key": server.public_key, "endpoint_host": server.endpoint_host, "listen_port": server.listen_port,
            "addresses": [f"{dev.ip}/32"] + ([f"{ip6}/128"] if ip6 else []), "dns": _dns(server), "allowed_ips": ["0.0.0.0/0", "::/0"], "keepalive": 25}


def device_peer(db: Session, who: Who, device_id: int) -> dict[str, Any]:
    """Current connection details for one of this account's own devices, so an app follows a server's changes without a new key."""
    acct = account_of(db, who)
    dev = db.get(VpnDevice, device_id)
    if dev is None or dev.account_id != acct.id:
        raise VpnError("Device not found.", 404)
    ok, reason = eligibility(db, acct, False)
    if not ok:
        raise VpnError(reason or "Kiwi VPN isn't available.", 403)
    server = db.get(VpnServer, dev.server_id)
    if server is None or not server.enabled:
        raise VpnError("Server not found.", 404)
    if not server.online or not server.public_key or not server.endpoint_host:
        raise VpnError("This server is offline right now. Pick another one or try again later.", 503)
    return peer_out(dev, server)


def device_precheck(db: Session, who: Who, name: str, server_id: int, preview: bool = False) -> None:
    """Everything create_device refuses for before it touches a node, without writing anything: routes call this BEFORE
    charging the rate limit, so an account that can't add a device never spends a token (security audit M1)."""
    name = clean_name(name)
    acct = account_of(db, who)
    db.commit()
    ok, reason = eligibility(db, acct, preview)
    if not ok:
        raise VpnError(reason or "Kiwi VPN isn't available.", 403)
    if _reviewing(db, acct):
        raise VpnError("This review account is for the browser extension only.", 403)
    s = settings(db)
    max_devices = OWNER_MAX_DEVICES if acct.is_owner else s.max_devices
    _over_cap(db, acct, s)
    if _slots_full(db, acct.id, max_devices, name, server_id, s.count_pc_once and not acct.is_owner):
        raise VpnError(f"You can have up to {max_devices} devices. Delete one first.", 409)
    server = db.get(VpnServer, server_id)
    if server is None or not server.enabled or not sees(acct, server):
        raise VpnError("Server not found.", 404)
    db.commit()


def _over_cap(db: Session, acct: VpnAccount, s: VpnSettings) -> None:
    if s.monthly_cap_gb > 0 and not acct.is_owner:
        use = usage_out(db, acct, s)
        if use["paused_until"] or use["used_bytes"] >= use["cap_bytes"]:
            raise VpnError(f"You've used this month's {s.monthly_cap_gb} GB of Kiwi VPN. It resumes on the 1st of next month.", 403)


def browser_precheck(db: Session, who: Who, server_id: int, preview: bool = False) -> None:
    """create_browser's refusals that need no node, before the route charges the rate limit (security audit M1)."""
    acct = account_of(db, who)
    db.commit()
    ok, reason = eligibility(db, acct, preview)
    if not ok:
        raise VpnError(reason or "Kiwi VPN isn't available.", 403)
    _over_cap(db, acct, settings(db))
    server = db.get(VpnServer, server_id)
    if server is None or not server.enabled or not server.proxy_host or not sees(acct, server) or (_reviewing(db, acct) and server.name != reviewer.SERVER):
        raise VpnError("That server isn't available for the browser extension.", 404)
    db.commit()


VIA = ("app", "web", "anon", "admin", "bot")


def create_device(db: Session, who: Who, name: str, server_id: int, preview: bool = False, own_public_key: str | None = None,
                  via: str | None = None) -> tuple[VpnDevice, str, str]:
    """(device, config text, filename). The config, holding the private key, exists only in this return value.

    With `own_public_key`, the app made its key itself: the server never sees the private half, so the config and filename come back empty. See `peer_out`.

    The tunnel IP is protected by the unique (server_id, ip) constraint: if a concurrent creation took the same IP first,
    the flush fails, the transaction is rolled back and the next free IP is tried (checks re-run on the fresh state)."""
    name = clean_name(name)
    acct = account_of(db, who)
    db.commit()
    acct_id = acct.id
    ok, reason = eligibility(db, acct, preview)
    if not ok:
        raise VpnError(reason or "Kiwi VPN isn't available.", 403)
    if _reviewing(db, acct):
        raise VpnError("This review account is for the browser extension only.", 403)
    s = settings(db)
    max_devices = OWNER_MAX_DEVICES if acct.is_owner else s.max_devices
    if s.monthly_cap_gb > 0 and not acct.is_owner:
        use = usage_out(db, acct, s)
        if use["paused_until"] or use["used_bytes"] >= use["cap_bytes"]:
            raise VpnError(f"You've used this month's {s.monthly_cap_gb} GB of Kiwi VPN. It resumes on the 1st of next month.", 403)
    if own_public_key is not None:
        private_key, public_key = "", clean_public_key(own_public_key)
        if db.scalar(select(VpnDevice.id).where(VpnDevice.public_key == public_key)) is not None:
            raise VpnError("That key is already in use.", 409)
    else:
        private_key, public_key = keys.generate_keypair()
    tried: set[str] = set()
    once = s.count_pc_once and not acct.is_owner
    for _ in range(8):
        server = _checked_server(db, acct_id, server_id, max_devices, name, once)
        ip = allocate_ip(db, server, tried)
        dev = VpnDevice(account_id=acct_id, server_id=server.id, name=name, public_key=public_key, ip=ip,
                        usage_month=utcnow().strftime("%Y-%m"), created_via=via if via in VIA else None)
        db.add(dev)
        try:
            db.flush()
            break
        except IntegrityError:
            db.rollback()
            tried.add(ip)
    else:
        raise VpnError("The server is busy. Try again in a moment.", 503)
    db.commit()  # the IP is reserved before the node is called: no write transaction (SQLite's only write lock) waits on it
    try:
        with node.client_for(server) as agent:
            agent.put_peer(public_key, f"{ip}/32")
    except node.NodeError as e:
        db.delete(dev)
        db.commit()
        logger.warning("vpn: adding a peer on server %s failed: %s", server_id, e)
        raise VpnError("This server is offline right now. Pick another one or try again later.", 503) from None
    if own_public_key is not None:
        return dev, "", ""
    config = keys.render_config(private_key, ip, _dns(server), server.public_key, server.endpoint_host,
                                server.listen_port, keys.ipv6_for(server.address, ip, server.address6))
    return dev, config, keys.filename(server.name, dev.name)


def _remove_browser(db: Session, b: VpnBrowser) -> None:
    """Off the proxy, best effort (the next sync removes a login it can't place), then the row."""
    server = db.get(VpnServer, b.server_id)
    if server is not None and server.proxy_host:
        try:
            with node.client_for(server) as agent:
                agent.delete_proxy_user(b.username)
        except node.NodeError as e:
            logger.warning("vpn: removing a browser login on server %s failed: %s", server.id, e)
    db.delete(b)


def create_browser(db: Session, who: Who, server_id: int, replace: str | None = None, preview: bool = False) -> dict[str, Any]:
    """A login for one browser on one server's proxy, returned once: only its hash is kept. Past the device limit the account's oldest browser makes room."""
    acct = account_of(db, who)
    db.commit()
    ok, reason = eligibility(db, acct, preview)
    if not ok:
        raise VpnError(reason or "Kiwi VPN isn't available.", 403)
    s = settings(db)
    if s.monthly_cap_gb > 0 and not acct.is_owner:
        use = usage_out(db, acct, s)
        if use["paused_until"] or use["used_bytes"] >= use["cap_bytes"]:
            raise VpnError(f"You've used this month's {s.monthly_cap_gb} GB of Kiwi VPN. It resumes on the 1st of next month.", 403)
    server = db.get(VpnServer, server_id)
    review = _reviewing(db, acct)
    if server is None or not server.enabled or not server.proxy_host or not sees(acct, server) or (review and server.name != reviewer.SERVER):
        raise VpnError("That server isn't available for the browser extension.", 404)
    if not server.online:
        raise VpnError("This server is offline right now. Pick another one or try again later.", 503)
    mine = list(db.scalars(select(VpnBrowser).where(VpnBrowser.account_id == acct.id).order_by(VpnBrowser.id)))
    for old in [b for b in mine if b.username == replace]:
        _remove_browser(db, old)
        mine.remove(old)
    limit = 1 if review else OWNER_MAX_DEVICES if acct.is_owner else s.max_devices
    while len(mine) >= limit:
        _remove_browser(db, mine.pop(0))
    username, password = secrets.token_hex(12), secrets.token_urlsafe(32)
    row = VpnBrowser(account_id=acct.id, server_id=server.id, username=username, secret_hash=hashlib.sha256(password.encode()).hexdigest(),
                     usage_month=utcnow().strftime("%Y-%m"))
    db.add(row)
    db.commit()
    try:
        with node.client_for(server) as agent:
            agent.put_proxy_user(username, row.secret_hash)
    except node.NodeError as e:
        db.delete(row)
        db.commit()
        logger.warning("vpn: adding a browser login on server %s failed: %s", server_id, e)
        raise VpnError("This server is offline right now. Pick another one or try again later.", 503) from None
    return {"username": username, "password": password, "host": server.proxy_host, "port": server.proxy_port, "server_id": server.id, "server": server.name}


def delete_browser(db: Session, who: Who, username: str) -> None:
    b = db.scalar(select(VpnBrowser).where(VpnBrowser.username == username, VpnBrowser.account_id == account_of(db, who).id))
    if b is None:
        raise VpnError("That browser no longer exists.", 404)
    _remove_browser(db, b)
    db.commit()


def admin_delete_browser(db: Session, browser_id: int) -> None:
    b = db.get(VpnBrowser, browser_id)
    if b is None:
        raise VpnError("Browser not found.", 404)
    _remove_browser(db, b)
    db.commit()


def delete_device(db: Session, who: Who, device_id: int) -> tuple[int, str]:
    """Returns the deleted device's (server id, name), so a replacement can be recognised (app.security.rate_limit.FREED)."""
    dev = db.get(VpnDevice, device_id)
    if dev is None or dev.account_id != account_of(db, who).id:
        raise VpnError("Device not found.", 404)
    gone = (dev.server_id, dev.name)
    _remove(db, dev)
    db.commit()
    return gone


def remove_app_devices(db: Session, member: Member) -> int:
    """Every WireGuard device this member's Kiwi VPN app made (security audit L8: "sign the app out everywhere" can take
    the PCs' keys with it). Made through an app session (created_via "app"), or, for devices from before that was
    recorded, named the way the Windows app names them ("<PC> (<server id>)" on that server). Browser logins stay."""
    acct = db.scalar(select(VpnAccount).where(VpnAccount.member_id == member.id))
    if acct is None:
        return 0
    gone = 0
    for dev in list(db.scalars(select(VpnDevice).where(VpnDevice.account_id == acct.id))):
        if dev.created_via == "app" or (dev.created_via is None and slot_key(dev.name, dev.server_id, dev.id)[0] == "pc"):
            _remove(db, dev)
            gone += 1
    db.commit()
    return gone


def own_device_with_key(db: Session, who: Who, public_key: str) -> VpnDevice | None:
    """This account's device registered with exactly this public key, if any (keys are unique across all devices)."""
    dev = db.scalar(select(VpnDevice).where(VpnDevice.public_key == public_key))
    if dev is None or dev.account_id != account_of(db, who).id:
        return None
    return dev


def _remove(db: Session, dev: VpnDevice, server: VpnServer | None = None) -> None:
    """Remove the peer on the node (best effort: sync removes leftovers), then the row."""
    server = server or db.get(VpnServer, dev.server_id)
    if server is not None:
        try:
            with node.client_for(server) as agent:
                agent.delete_peer(dev.public_key)
        except node.NodeError as e:
            logger.info("vpn: removing a peer on server %s failed (sync retries): %s", server.id, e)
    db.delete(dev)


# --- servers (admin) ---------------------------------------------------------------------------------------------------


def _apply_status(server: VpnServer, st: dict[str, Any], now: datetime) -> None:
    server.public_key, server.listen_port = st["public_key"], st["listen_port"]
    server.endpoint_host, server.address, server.dns = st["endpoint_host"], st["address"], st["dns"]
    server.online, server.last_seen, server.last_error = True, now, None
    server.adblock, server.fail_streak, server.address6 = bool(st.get("adblock")), 0, _v6_pool(str(st.get("address6") or ""))
    proxy = st.get("proxy") or {}
    server.proxy_host, server.proxy_port = proxy.get("host", ""), proxy.get("port", 443)


def _check_pool(address: str) -> None:
    try:
        ipaddress.ip_interface(address)
    except ValueError:
        raise node.NodeError("The VPN agent reported an invalid address pool") from None


def _clean_server_fields(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    if out.get("country_code") is not None:
        cc = str(out["country_code"]).strip().upper()
        if len(cc) != 2 or not cc.isalpha():
            raise VpnError("Country code: two letters (ISO-2).", 400)
        out["country_code"] = cc
    if out.get("cert_sha256") is not None:
        try:
            out["cert_sha256"] = node.normalize_fingerprint(out["cert_sha256"])
        except ValueError as e:
            raise VpnError(str(e), 400) from None
    if out.get("agent_url") is not None:
        url = str(out["agent_url"]).strip().rstrip("/")
        if not url.lower().startswith("https://"):
            raise VpnError("The agent URL must start with https://", 400)
        out["agent_url"] = url
    if out.get("agent_token") is not None and len(str(out["agent_token"]).strip()) < 32:
        raise VpnError("The agent token is too short (at least 32 characters).", 400)
    for k in ("name", "region"):
        if out.get(k) is not None:
            out[k] = " ".join(str(out[k]).split())
    if out.get("name") == "":
        raise VpnError("The server needs a name.", 400)
    return out


def add_server(db: Session, data: dict[str, Any]) -> VpnServer:
    d = _clean_server_fields(data)
    token = str(d["agent_token"]).strip()
    try:
        with node.connect(d["agent_url"], token, d["cert_sha256"]) as agent:
            st = agent.status()
        _check_pool(st["address"])
    except node.NodeError as e:
        raise VpnError(f"Couldn't verify the VPN agent: {e}", 400) from None
    server = VpnServer(name=d["name"], region=d.get("region") or "", country_code=d.get("country_code") or "", agent_url=d["agent_url"],
                       agent_token_enc=enc(token), cert_sha256=d["cert_sha256"], capacity=int(d.get("capacity") or 250), enabled=True,
                       testers_only=bool(d.get("testers_only", False)), traffic_included_gb=int(d.get("traffic_included_gb") or 0))
    _apply_status(server, st, utcnow())
    db.add(server)
    db.commit()
    return server


def update_server(db: Session, server_id: int, data: dict[str, Any]) -> VpnServer:
    server = db.get(VpnServer, server_id)
    if server is None:
        raise VpnError("Server not found.", 404)
    d = _clean_server_fields(data)
    for k in ("name", "region", "country_code", "capacity", "enabled", "cert_sha256", "testers_only", "traffic_included_gb"):
        if d.get(k) is not None:
            setattr(server, k, d[k])
    if d.get("agent_token"):
        server.agent_token_enc = enc(str(d["agent_token"]).strip())
    db.commit()
    return server


def delete_server(db: Session, server_id: int) -> None:
    server = db.get(VpnServer, server_id)
    if server is None:
        raise VpnError("Server not found.", 404)
    devices = list(db.scalars(select(VpnDevice).where(VpnDevice.server_id == server.id)))
    if devices:
        try:
            with node.client_for(server) as agent:
                for dev in devices:
                    try:
                        agent.delete_peer(dev.public_key)
                    except node.NodeError:
                        pass  # best effort: the server is going away
        except node.NodeError:
            pass
    for dev in devices:
        db.delete(dev)
    db.delete(server)
    db.commit()


def _unreachable(server: VpnServer, e: Exception) -> None:
    """A check that couldn't reach the agent. One failure is kept as the error but leaves the server online (a network
    blip shouldn't send new devices elsewhere or hide it); OFFLINE_AFTER in a row mark it offline. A success resets it."""
    server.fail_streak = (server.fail_streak or 0) + 1
    server.last_error = str(e)[:500]
    if server.fail_streak >= OFFLINE_AFTER:
        server.online = False


def test_server(db: Session, server_id: int) -> dict[str, Any]:
    server = db.get(VpnServer, server_id)
    if server is None:
        raise VpnError("Server not found.", 404)
    try:
        with node.client_for(server) as agent:
            st = agent.status()
        _check_pool(st["address"])
    except node.NodeError as e:
        _unreachable(server, e)
        db.commit()
        return {"ok": False, "error": str(e), "peers": 0}
    _apply_status(server, st, utcnow())
    db.commit()
    return {"ok": True, "error": None, "peers": len(st["peers"])}


def admin_overview(db: Session) -> dict[str, Any]:
    s = settings(db)
    since = utcnow() - timedelta(hours=24)
    counts = _device_counts(db)
    traffic = server_traffic(db)
    active = dict(db.execute(select(VpnDevice.server_id, func.count()).where(VpnDevice.last_handshake >= since)
                             .group_by(VpnDevice.server_id)).all())  # type: ignore[arg-type]
    servers = [{"id": v.id, "name": v.name, "region": v.region, "country_code": v.country_code, "endpoint_host": v.endpoint_host,
                "listen_port": v.listen_port, "agent_url": v.agent_url, "capacity": v.capacity, "enabled": v.enabled, "online": v.online,
                "adblock": v.adblock, "testers_only": v.testers_only,
                "traffic_month_bytes": traffic.get(v.id, 0), "traffic_included_gb": v.traffic_included_gb,
                "last_seen": _iso(v.last_seen), "last_error": v.last_error, "devices": counts.get(v.id, 0), "active_24h": active.get(v.id, 0),
                "proxy_host": v.proxy_host}
               for v in db.scalars(select(VpnServer).order_by(VpnServer.name, VpnServer.id))]
    out = {"settings": settings_out(s), "servers": servers,
           "totals": {"devices": sum(counts.values()),
                      "members": db.scalar(select(func.count(func.distinct(VpnDevice.account_id)))) or 0,
                      "active_24h": sum(active.values())},
           "billing": billing.totals(db), "trial_signals": trial_guard.signals_out(db)}
    db.commit()
    return out


def admin_customers(db: Session, limit: int = 200, now: datetime | None = None) -> list[dict[str, Any]]:
    """Accounts that have paid or have devices, newest first. An anonymous account shows only its number: nothing else exists."""
    now = now or utcnow()
    accounts = list(db.scalars(select(VpnAccount).where(
        (VpnAccount.paid_until.is_not(None)) | (VpnAccount.tester_until.is_not(None)) | (VpnAccount.status != "active") | VpnAccount.id.in_(select(VpnDevice.account_id)) | VpnAccount.id.in_(select(VpnBrowser.account_id))).order_by(VpnAccount.id.desc()).limit(limit)))
    ids = [a.id for a in accounts]
    devices: dict[int, list[VpnDevice]] = {}
    for d in db.scalars(select(VpnDevice).where(VpnDevice.account_id.in_(ids)).order_by(VpnDevice.id)) if ids else []:
        devices.setdefault(d.account_id, []).append(d)
    browsers: dict[int, list[VpnBrowser]] = {}
    for b in db.scalars(select(VpnBrowser).where(VpnBrowser.account_id.in_(ids)).order_by(VpnBrowser.id)) if ids else []:
        browsers.setdefault(b.account_id, []).append(b)
    names = dict(db.execute(select(VpnServer.id, VpnServer.name)).all())
    paid = {a: (n, t) for a, n, t in db.execute(select(VpnPayment.account_id, func.count(), func.sum(VpnPayment.paid_usd))
                                                .where(VpnPayment.account_id.in_(ids), VpnPayment.status == "credited").group_by(VpnPayment.account_id))} if ids else {}
    r = rules(db, sorted({a.member_id for a in accounts if a.member_id is not None}))
    out = []
    for a in accounts:
        m = r.people.get(a.member_id) if a.member_id is not None and r.people else None
        n, total = paid.get(a.id, (0, 0.0))
        out.append({"id": a.id, "owner": a.is_owner, "anonymous": a.member_id is None and not a.is_owner,
                    "label": "Admin" if a.is_owner else (m.username if m else "(member gone)") if a.member_id is not None else f"Anonymous #{a.id}",
                    "state": access(db, a, r, now), "paid_until": _iso(a.paid_until), "tester_until": _iso(a.tester_until) if is_tester(a, now) else None, "lapsed_at": _iso(a.lapsed_at), "has_card": bool(a.stripe_customer),
                    "payments": int(n), "paid_usd": round(float(total or 0), 2),
                    "devices": [{"id": d.id, "name": d.name, "server": names.get(d.server_id, "?"), "ip": d.ip, "last_handshake": _iso(d.last_handshake),
                                 "rx_bytes": d.rx_bytes, "tx_bytes": d.tx_bytes} for d in devices.get(a.id, [])],
                    "browsers": [{"id": b.id, "server": names.get(b.server_id, "?"), "last_active": _iso(b.last_active), "rx_bytes": b.rx_bytes, "tx_bytes": b.tx_bytes}
                                 for b in browsers.get(a.id, [])]})
    return out


def admin_payments(db: Session, limit: int = 50) -> list[dict[str, Any]]:
    """Recent payments. Older anonymous ones have lost their account link on purpose (billing.scrub). Anything needing a person comes with a note."""
    rows = db.execute(select(VpnPayment, VpnAccount).join(VpnAccount, VpnAccount.id == VpnPayment.account_id, isouter=True)
                      .order_by(VpnPayment.id.desc()).limit(limit)).all()
    people = dict(db.execute(select(Member.id, Member.username).where(Member.id.in_([a.member_id for _, a in rows if a is not None and a.member_id]))).all())
    return [{"id": p.id, "provider": p.provider, "network": p.network, "amount_usd": p.amount_usd, "paid_usd": p.paid_usd, "status": p.status,
             "days": round(p.seconds / 86400, 1), "created_at": _iso(p.created_at), "credited_at": _iso(p.credited_at), "note": p.note, "can_check": bool(p.provider_ref) and p.status in billing.UNFINISHED,
             "account": None if a is None else (people.get(a.member_id, "(member gone)") if a.member_id else f"Anonymous #{a.id}")} for p, a in rows]


def set_blocked(db: Session, account_id: int, blocked: bool) -> dict[str, Any]:
    """Block: every device comes off the node now and is deleted, whatever time is left. Unblock: can add devices again."""
    acct = db.get(VpnAccount, account_id)
    if acct is None:
        raise VpnError("Account not found.", 404)
    if acct.is_owner:
        raise VpnError("These are your own devices: revoke them one by one instead.", 400)
    acct.status = "blocked" if blocked else "active"
    removed = 0
    if blocked:
        for dev in list(db.scalars(select(VpnDevice).where(VpnDevice.account_id == acct.id))):
            _remove(db, dev)
            removed += 1
        for b in list(db.scalars(select(VpnBrowser).where(VpnBrowser.account_id == acct.id))):
            _remove_browser(db, b)
            removed += 1
    db.commit()
    return {"id": acct.id, "status": acct.status, "devices_removed": removed}


def block_key(db: Session, key: str) -> dict[str, Any]:
    acct = billing.by_key(db, key)
    if acct is None:
        raise VpnError("No account has that key.", 404)
    return set_blocked(db, acct.id, True)


def admin_delete_device(db: Session, device_id: int) -> None:
    dev = db.get(VpnDevice, device_id)
    if dev is None:
        raise VpnError("Device not found.", 404)
    _remove(db, dev)
    db.commit()


# --- payments arriving -------------------------------------------------------------------------------------------------


def activate(db: Session, account_id: int) -> int:
    """A payment just arrived: devices switched off for lack of time come back now, not at the next sync. Best effort: the sync does the same within 5 minutes if a server doesn't answer."""
    acct = db.get(VpnAccount, account_id)
    if acct is None or acct.lapsed_at is None or access(db, acct) != "active" or not settings(db).enabled:
        return 0
    now, restored = utcnow(), 0
    acct.lapsed_at = None
    devices = [d for d in db.scalars(select(VpnDevice).where(VpnDevice.account_id == acct.id)) if not _paused(d, now)]
    browsers = list(db.scalars(select(VpnBrowser).where(VpnBrowser.account_id == acct.id)))
    capped = settings(db).monthly_cap_gb > 0 and account_usage(db, acct.id, now) >= settings(db).monthly_cap_gb * GB
    db.commit()
    for server_id in sorted({d.server_id for d in devices} | {b.server_id for b in browsers}):
        server = db.get(VpnServer, server_id)
        if server is None or not server.enabled or not server.online:
            continue
        try:
            with node.client_for(server) as agent:
                for dev in (d for d in devices if d.server_id == server_id):
                    agent.put_peer(dev.public_key, f"{dev.ip}/32")
                    dev.rx_counter = dev.tx_counter = 0  # a fresh peer starts counting from zero
                    restored += 1
                for b in (b for b in browsers if b.server_id == server_id and not capped and server.proxy_host):
                    agent.put_proxy_user(b.username, b.secret_hash)
                    b.rx_counter = b.tx_counter = 0
                    restored += 1
        except node.NodeError as e:
            logger.info("vpn: restoring access on server %s failed (sync retries): %s", server_id, e)
    db.commit()
    return restored


def payment_of(db: Session, acct: VpnAccount, payment_id: int) -> VpnPayment:
    pay = db.get(VpnPayment, payment_id)
    if pay is None or pay.account_id != acct.id:
        raise VpnError("Payment not found.", 404)
    return pay


def check_my_payment(db: Session, acct: VpnAccount, payment_id: int) -> str:
    """Check now: the customer asks about their own payment. Access comes back straight away if the money's there."""
    pay = payment_of(db, acct, payment_id)
    try:
        status, credited = billing.check_payment(db, pay)
    except billing.BillingError as e:
        raise VpnError(str(e), e.status) from None
    if credited is not None:
        activate(db, credited)
    return status


def admin_check_payment(db: Session, payment_id: int) -> dict[str, Any]:
    pay = db.get(VpnPayment, payment_id)
    if pay is None:
        raise VpnError("Payment not found.", 404)
    try:
        status, credited = billing.check_payment(db, pay)
    except billing.BillingError as e:
        raise VpnError(str(e), e.status) from None
    if credited is not None:
        activate(db, credited)
    return {"id": payment_id, "status": status, "credited": credited is not None}


def admin_credit_payment(db: Session, payment_id: int, usd: float, reason: str, account_id: int | None = None, key: str | None = None) -> dict[str, Any]:
    pay = db.get(VpnPayment, payment_id)
    if pay is None:
        raise VpnError("Payment not found.", 404)
    acct = billing.by_key(db, key) if key else db.get(VpnAccount, account_id) if account_id is not None else None
    if (key or account_id is not None) and acct is None:
        raise VpnError("No account matches that.", 404)
    try:
        target = billing.credit_by_hand(db, pay, usd, reason, acct)
    except billing.BillingError as e:
        raise VpnError(str(e), e.status) from None
    activate(db, target.id)
    db.refresh(target)
    return {"id": payment_id, "status": pay.status, "account": target.id, "paid_until": _iso(target.paid_until)}


def admin_refund_payment(db: Session, payment_id: int, reason: str) -> dict[str, Any]:
    pay = db.get(VpnPayment, payment_id)
    if pay is None:
        raise VpnError("Payment not found.", 404)
    try:
        billing.refund_by_hand(db, pay, reason)
    except billing.BillingError as e:
        raise VpnError(str(e), e.status) from None
    return {"id": payment_id, "status": pay.status}


def reconcile_payments(db: Session, now: datetime | None = None) -> dict[str, Any]:
    out = billing.reconcile(db, now)
    restored = sum(activate(db, a) for a in out.pop("accounts"))
    return {**out, "restored": restored}


# --- expiry reminders (the vpn.reminders job) ----------------------------------------------------------------------------

REMINDER_DAYS = (3, 1)
STOP_REMINDING_AFTER = timedelta(days=7)  # someone long gone doesn't hear about it for the first time weeks later


def _reminder_text(end_kind: str, stage: int, until: datetime, can_pay: bool) -> tuple[str, str]:
    what = "free trial" if end_kind == "trial" else "paid time"
    when = f"{until.day} {until.strftime('%b %Y')}"
    renew = " Add time on the Kiwi VPN page: $9.99 for 30 days, and nothing renews by itself." if can_pay else " Paid plans aren't open yet."
    if stage == 0:
        return f"Your Kiwi VPN {what} has ended", f"Your devices are switched off and kept for 30 days.{renew}"
    days = "1 day" if stage == 1 else f"{stage} days"
    return f"Your Kiwi VPN {what} ends in {days}", f"It ends on {when}. After that your devices switch off.{renew}"


def due_reminders(db: Session, now: datetime | None = None) -> list[dict[str, Any]]:
    """Each Passport member hears once at 3 days left, once at 1 and once when it ends, in their notifications. Anonymous accounts have no way to be reached, so their page shows it instead.
    Anyone with free access (owner, tester, Royal, a grant) isn't told their time is ending. Returns what also should go by email."""
    now = now or utcnow()
    s = settings(db)
    if not s.enabled:
        return []
    accounts = list(db.scalars(select(VpnAccount).where(VpnAccount.member_id.is_not(None), VpnAccount.status == "active", (VpnAccount.paid_until.is_not(None)) | (VpnAccount.trial_ends_at.is_not(None)))))
    r = rules(db, sorted({a.member_id for a in accounts if a.member_id is not None}))
    emails: list[dict[str, Any]] = []
    for acct in accounts:
        paid, trial = _aware(acct.paid_until), _aware(acct.trial_ends_at)
        until = max(d for d in (paid, trial) if d is not None)
        kind = "paid" if paid is not None and paid == until else "trial"
        left = until - now
        if left < -STOP_REMINDING_AFTER or left > timedelta(days=REMINDER_DAYS[0]):
            continue
        member = r.people.get(acct.member_id) if r.people else None
        if member is None or access(db, acct, r, max(now, until + timedelta(seconds=1))) != "lapsed":  # a rank, a grant or free access carries them on: nothing is ending for them
            continue
        stage = 0 if left <= timedelta(0) else 1 if left <= timedelta(days=1) else 3
        if _aware(acct.reminded_until) == until and acct.reminder_stage is not None and acct.reminder_stage <= stage:
            continue
        title, body = _reminder_text(kind, stage, until, s.payments_enabled)
        notify.notify(db, member.id, "vpn_expiry", title, body, {"until": until.isoformat(), "stage": stage})
        acct.reminded_until, acct.reminder_stage = until, stage
        if member.email:
            emails.append({"to": member.email, "subject": title, "text": body})
    db.commit()
    return emails


# --- sync (the vpn.sync job) -------------------------------------------------------------------------------------------


def _ts(seconds: int) -> datetime | None:
    return datetime.fromtimestamp(seconds, tz=timezone.utc) if seconds > 0 else None


def _usage(dev: VpnDevice, peer: dict[str, Any], month: str) -> None:
    if dev.usage_month != month:
        dev.usage_month, dev.rx_bytes, dev.tx_bytes = month, 0, 0
    rx, tx = int(peer["rx_bytes"]), int(peer["tx_bytes"])
    dev.rx_bytes += rx - dev.rx_counter if rx >= dev.rx_counter else rx  # a smaller counter: the agent restarted / re-added it
    dev.tx_bytes += tx - dev.tx_counter if tx >= dev.tx_counter else tx
    dev.rx_counter, dev.tx_counter = rx, tx
    hs = _ts(int(peer["latest_handshake"]))
    if hs is not None:
        dev.last_handshake = hs


def _sync_state(db: Session, acct: VpnAccount, r: Rules, now: datetime, enabled: bool) -> str:
    """access(), and also "closed": Admin has closed the VPN, so everyone but the owner, testers and the store reviewer is off until it opens. Their devices are kept."""
    state = access(db, acct, r, now)
    if state != "active" or enabled or acct.is_owner or is_tester(acct, now):
        return state
    member = r.people.get(acct.member_id) if acct.member_id is not None and r.people is not None else None
    return "active" if reviewer.is_reviewer(db, member) else "closed"


def _keep_off(state: str, acct: VpnAccount, now: datetime) -> bool:
    """Off for now with its row kept: closed, or out of time and not for too long."""
    return state == "closed" or (state == "lapsed" and now - _aware(acct.lapsed_at) < KEEP_LAPSED)


def _sync_browsers(db: Session, agent: node.NodeClient, browsers: list[VpnBrowser], accounts: dict[int, VpnAccount], r: Rules,
                   users: dict[str, dict[str, int]], states: dict[int, str], out: dict[str, Any], now: datetime,
                   off: set[int] | frozenset[int] = frozenset()) -> None:
    """Make the proxy's logins match: active accounts under their data cap are on it; out of time or over the cap, off and kept; blocked, gone."""
    month, cap = _month(now), settings(db).monthly_cap_gb * GB
    wanted: set[str] = set()
    for b in browsers:
        acct = accounts[b.account_id]
        if b.account_id not in states:
            states[b.account_id] = _sync_state(db, acct, r, now, settings(db).enabled)
            acct.lapsed_at = (acct.lapsed_at or now) if states[b.account_id] == "lapsed" else None
        state, seen = states[b.account_id], users.get(b.username)
        if seen is not None:
            if b.usage_month != month:
                b.usage_month, b.rx_bytes, b.tx_bytes = month, 0, 0
            rx, tx = seen["rx_bytes"], seen["tx_bytes"]
            if rx != b.rx_counter or tx != b.tx_counter:
                b.rx_bytes += rx - b.rx_counter if rx >= b.rx_counter else rx  # a smaller counter: the proxy restarted
                b.tx_bytes += tx - b.tx_counter if tx >= b.tx_counter else tx
                b.rx_counter, b.tx_counter, b.last_active = rx, tx, now
        if state != "active" and not _keep_off(state, acct, now):
            db.delete(b)  # blocked or banned, or out of time for too long; its login goes with the unknown ones below
            out["revoked"] += 1
            continue
        db.flush()  # the bytes just counted must be in what the cap sees
        over = cap > 0 and not acct.is_owner and account_usage(db, acct.id, now) >= cap
        if state != "active" or over or b.id in off:
            continue  # off for now: not wanted on the proxy, removed below
        wanted.add(b.username)
        if seen is None:
            try:
                agent.put_proxy_user(b.username, b.secret_hash)
                b.rx_counter = b.tx_counter = 0
                out["readded"] += 1
            except node.NodeError:
                out["errors"] += 1
    for name in users.keys() - wanted:
        try:
            agent.delete_proxy_user(name)
            out["suspended"] += 1
        except node.NodeError:
            out["errors"] += 1


def over_limit(db: Session, accounts: dict[int, VpnAccount], server: VpnServer, s: VpnSettings, now: datetime) -> tuple[set[int], set[int]]:
    """With sync_enforce_limits on (security audit L7, off by default): (device ids, browser ids) on this server to switch
    off for now, rows kept. Past the device limit (the oldest devices keep their slots; browsers counted on their own, the
    oldest kept) and, on a testers-only server, every account that may not see it. Owner and reviewer are exempt from the
    limit (they have their own). Never removes anything: lowering the limit or ending a test back out undoes it."""
    if not s.sync_enforce_limits or not accounts:
        return set(), set()
    off_devices: set[int] = set()
    off_browsers: set[int] = set()
    ids = list(accounts)
    devices: dict[int, list[VpnDevice]] = {}
    for d in db.scalars(select(VpnDevice).where(VpnDevice.account_id.in_(ids)).order_by(VpnDevice.created_at, VpnDevice.id)):
        devices.setdefault(d.account_id, []).append(d)
    browsers: dict[int, list[VpnBrowser]] = {}
    for b in db.scalars(select(VpnBrowser).where(VpnBrowser.account_id.in_(ids)).order_by(VpnBrowser.id)):
        browsers.setdefault(b.account_id, []).append(b)
    for acct_id, acct in accounts.items():
        mine, logins = devices.get(acct_id, []), browsers.get(acct_id, [])
        if not sees(acct, server):
            off_devices |= {d.id for d in mine if d.server_id == server.id}
            off_browsers |= {b.id for b in logins if b.server_id == server.id}
        if acct.is_owner or _reviewing(db, acct):
            continue
        kept: list[tuple[int | None, str, int]] = []
        for d in mine:  # oldest first: a device keeps its slot unless the ones before it already fill the limit
            if slots_used(kept + [(d.id, d.name, d.server_id)], s.count_pc_once) <= s.max_devices:
                kept.append((d.id, d.name, d.server_id))
            elif d.server_id == server.id:
                off_devices.add(d.id)
        off_browsers |= {b.id for b in logins[s.max_devices:] if b.server_id == server.id}
    return off_devices, off_browsers


def sync_server(db: Session, server: VpnServer, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    out = {"server": server.id, "online": False, "updated": 0, "readded": 0, "removed": 0, "revoked": 0, "suspended": 0, "errors": 0}
    s = settings(db)
    ms: int | None = None
    peer_count = live = 0
    try:
        with node.client_for(server) as agent:
            t0 = time.monotonic()
            st = agent.status()
            ms = int((time.monotonic() - t0) * 1000)
            _check_pool(st["address"])
            _apply_status(server, st, now)
            out["online"] = True
            peer_count, live = len(st["peers"]), health.live_peers(st["peers"], now)
            peers = {p["public_key"]: p for p in st["peers"]}
            month = now.strftime("%Y-%m")
            ours: set[str] = set()
            states: dict[int, str] = {}
            devices = list(db.scalars(select(VpnDevice).where(VpnDevice.server_id == server.id)))
            browsers = list(db.scalars(select(VpnBrowser).where(VpnBrowser.server_id == server.id))) if server.proxy_host else []
            ids = sorted({d.account_id for d in devices} | {b.account_id for b in browsers})
            # accounts, members, the entitlement and grants read once for the server, not a query per member
            accounts = {a.id: a for a in db.scalars(select(VpnAccount).where(VpnAccount.id.in_(ids)))} if ids else {}
            r = rules(db, sorted({a.member_id for a in accounts.values() if a.member_id is not None}))
            off_devices, off_browsers = over_limit(db, accounts, server, s, now)
            for dev in devices:
                acct = accounts[dev.account_id]
                if dev.account_id not in states:
                    states[dev.account_id] = _sync_state(db, acct, r, now, s.enabled)
                    if states[dev.account_id] == "lapsed":
                        acct.lapsed_at = acct.lapsed_at or now
                    else:
                        acct.lapsed_at = None
                state = states[dev.account_id]
                if state == "active" and dev.id in off_devices:  # sync_enforce_limits: off for now, kept (L7)
                    ours.add(dev.public_key)
                    peer = peers.get(dev.public_key)
                    if peer is not None:
                        _usage(dev, peer, month)
                        try:
                            agent.delete_peer(dev.public_key)
                            out["suspended"] += 1
                        except node.NodeError:
                            out["errors"] += 1
                    continue
                if _keep_off(state, acct, now):  # out of time or closed: off, kept, back on when paid or opened
                    ours.add(dev.public_key)
                    peer = peers.get(dev.public_key)
                    if peer is not None:
                        _usage(dev, peer, month)
                        try:
                            agent.delete_peer(dev.public_key)
                            out["suspended"] += 1
                        except node.NodeError:
                            out["errors"] += 1
                    continue
                if state != "active":  # blocked or banned, or out of time for too long: gone
                    try:
                        agent.delete_peer(dev.public_key)
                    except node.NodeError:
                        out["errors"] += 1
                        ours.add(dev.public_key)  # keep the row: retried next run
                        continue
                    db.delete(dev)
                    ours.add(dev.public_key)  # its peer is already gone: not an "unknown" one below
                    out["revoked"] += 1
                    continue
                ours.add(dev.public_key)
                peer = peers.get(dev.public_key)
                if _paused(dev, now):  # over the monthly cap: its peer stays off the node until the month rolls over
                    if peer is not None:
                        _usage(dev, peer, month)
                        try:
                            agent.delete_peer(dev.public_key)
                        except node.NodeError:
                            out["errors"] += 1
                    continue
                dev.paused_until = None  # a pause that ran out (a new month): re-added below like any missing peer
                if peer is None or peer["allowed_ip"] != f"{dev.ip}/32":
                    try:
                        agent.put_peer(dev.public_key, f"{dev.ip}/32")
                        dev.rx_counter = dev.tx_counter = 0  # a fresh peer starts counting from zero
                        out["readded"] += 1
                    except node.NodeError:
                        out["errors"] += 1
                    continue
                _usage(dev, peer, month)
                out["updated"] += 1
            for key in peers.keys() - ours:  # peers nobody here owns (a deleted device whose DELETE failed, ...)
                try:
                    agent.delete_peer(key)
                    out["removed"] += 1
                except node.NodeError:
                    out["errors"] += 1
            if server.proxy_host:
                _sync_browsers(db, agent, browsers, accounts, r, st["proxy_users"], states, out, now, off_browsers)
    except node.NodeError as e:
        _unreachable(server, e)
        out["error"] = str(e)
    health.record(db, server, ok="error" not in out, ms=ms, peers=peer_count, live=live, error=out.get("error"), now=now)
    db.commit()
    return out


def enforce_caps(db: Session, now: datetime | None = None) -> dict[str, int]:
    """The monthly data cap: a member at or over it has every device paused (peer removed at the node, the device kept)
    until the 1st of next month; a member under it again (a new month, or the owner raised / removed the cap) resumes."""
    now = now or utcnow()
    cap = settings(db).monthly_cap_gb * GB
    used = dict(db.execute(select(VpnDevice.account_id, func.sum(VpnDevice.rx_bytes + VpnDevice.tx_bytes))
                           .where(VpnDevice.usage_month == _month(now)).group_by(VpnDevice.account_id)).all())  # type: ignore[arg-type]
    for acct_id, n in db.execute(select(VpnBrowser.account_id, func.sum(VpnBrowser.rx_bytes + VpnBrowser.tx_bytes))
                                 .where(VpnBrowser.usage_month == _month(now)).group_by(VpnBrowser.account_id)).all():
        used[acct_id] = int(used.get(acct_id) or 0) + int(n or 0)
    lapsed = set(db.scalars(select(VpnAccount.id).where(VpnAccount.lapsed_at.is_not(None))))
    owner = db.scalar(select(VpnAccount.id).where(VpnAccount.is_owner.is_(True)))
    pause: list[VpnDevice] = []
    resume: list[VpnDevice] = []
    for dev in db.scalars(select(VpnDevice).order_by(VpnDevice.id)):
        over = cap > 0 and dev.account_id != owner and int(used.get(dev.account_id) or 0) >= cap
        if over and not _paused(dev, now):
            dev.paused_until = next_month(now)
            pause.append(dev)
        elif not over and dev.paused_until is not None:
            dev.paused_until = None
            if dev.account_id not in lapsed:  # out of time too: stays off until paid
                resume.append(dev)
    db.commit()
    errors = 0
    for server_id in sorted({d.server_id for d in pause + resume}):  # best effort: the next sync finishes what fails here
        server = db.get(VpnServer, server_id)
        if server is None or not server.enabled or not server.online:
            continue
        try:
            with node.client_for(server) as agent:
                for dev in pause + resume:
                    if dev.server_id != server_id:
                        continue
                    try:
                        if dev in pause:
                            agent.delete_peer(dev.public_key)
                        else:
                            agent.put_peer(dev.public_key, f"{dev.ip}/32")
                            dev.rx_counter = dev.tx_counter = 0  # a fresh peer starts counting from zero
                    except node.NodeError:
                        errors += 1
        except node.NodeError:
            errors += 1
    db.commit()
    return {"paused": len(pause), "resumed": len(resume), "errors": errors}


def sync_all(db: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    results = [sync_server(db, v, now) for v in list(db.scalars(select(VpnServer).where(VpnServer.enabled.is_(True)).order_by(VpnServer.id)))]
    caps = enforce_caps(db, now)
    billing.scrub(db, now)
    health.prune(db, now)
    db.commit()
    return {"servers": len(results), "online": sum(r["online"] for r in results),
            **{k: sum(r[k] for r in results) for k in ("updated", "readded", "removed", "revoked", "suspended")},
            "errors": sum(r["errors"] for r in results) + caps["errors"], "paused": caps["paused"], "resumed": caps["resumed"]}
