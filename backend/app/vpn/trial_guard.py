"""Kiwi VPN free-trial abuse guards (security audit M4): one trial per verified identity, a soft per-address throttle.

Passports are free (email code, Google, X, Telegram), so "one trial per account" alone gave a new month to every new
account. Here every identity a trial was used with is remembered as a salted hash in vpn_trial_claims: the verified email
(normalised, so "a.b+1@gmail.com" and "ab@gmail.com" are one address) and each linked Google / X / Telegram account. A new
Passport holding any of them can't start another trial. Hashes are written when a trial starts, when a member who has had
a trial links another identity or adds an email (so identities can't be rotated through an old account), and by
migration 0113 for trials started before it. Like xp_claims they survive account deletion and store no identity.

The per-address throttle counts successful trial starts per client address and per network (IPv4 /24, IPv6 /48) over
24 hours, in memory (one Railway replica; a restart forgets, which is why it's "soft"). It applies only when the address
is the caller's real one (real_ip): website visitors whose address resolves to Vercel's shared egress are never counted,
or one person could lock everyone on that egress out.
"""

import hashlib
import ipaddress
import threading
import time
from collections import OrderedDict, deque

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.members import LinkedIdentity, Member
from app.models.vpn import VpnAccount, VpnTrialClaim
from app.security import client_ip

SALT = "kiwi-vpn-trial-v1"  # migration 0113 repeats this and normalise_email(): keep them identical
GMAIL = {"gmail.com", "googlemail.com"}
DAY = 86400.0
MAX_KEYS = 20_000


def normalise_email(email: str) -> str:
    """One mailbox, one key: lower-case, "+tag" dropped, and for Gmail the dots too (Gmail ignores both)."""
    addr = (email or "").strip().lower()
    if "@" not in addr:
        return addr
    local, domain = addr.rsplit("@", 1)
    local = local.split("+", 1)[0]
    if domain in GMAIL:
        local, domain = local.replace(".", ""), "gmail.com"
    return f"{local}@{domain}"


def claim_hash(key: str) -> str:
    return hashlib.sha256(f"{SALT}|{key}".encode()).hexdigest()


def identity_keys(db: Session, member: Member) -> list[tuple[str, str]]:
    """(label, key) for every verified identity of the member: the email (set only after a code was typed) and each linked platform account."""
    out: list[tuple[str, str]] = []
    if member.email:
        out.append(("email address", "email:" + normalise_email(member.email)))
    labels = {"google": "Google account", "x": "X account", "telegram": "Telegram account"}
    for platform, uid in db.execute(select(LinkedIdentity.platform, LinkedIdentity.platform_user_id).where(LinkedIdentity.member_id == member.id)).all():
        out.append((labels.get(platform, f"{platform} account"), f"{platform}:{uid}"))
    return out


def used_identity(db: Session, member: Member) -> str | None:
    """The label of the first identity of this member that has already had a trial (on any account, even a deleted one), or None."""
    keys = identity_keys(db, member)
    if not keys:
        return None
    hashes = {claim_hash(k): label for label, k in keys}
    hit = db.scalar(select(VpnTrialClaim.claim_hash).where(VpnTrialClaim.claim_hash.in_(list(hashes))).limit(1))
    return hashes[hit] if hit is not None else None


def mark(db: Session, member: Member) -> int:
    """Remember every identity of the member as having had a trial. Idempotent; commits nothing (the caller does). Returns how many were new."""
    added = 0
    for _, key in identity_keys(db, member):
        h = claim_hash(key)
        if db.scalar(select(VpnTrialClaim.id).where(VpnTrialClaim.claim_hash == h)) is not None:
            continue
        try:
            with db.begin_nested():
                db.add(VpnTrialClaim(claim_hash=h))
            added += 1
        except IntegrityError:
            pass  # written by a concurrent request: the same result
    return added


def mark_if_trial_used(db: Session, member: Member) -> None:
    """Called when a member links an identity or adds an email: if they've had a trial, the new identity has too."""
    started = db.scalar(select(VpnAccount.trial_started_at).where(VpnAccount.member_id == member.id))
    if started is not None:
        mark(db, member)


# --- the per-address throttle --------------------------------------------------------------------------------------


def real_ip(request: Request) -> str | None:
    """The caller's address when it is really theirs, else None.

    Real: the request came through the website with our shared secret (Vercel reported the visitor), or straight to the
    API (the desktop / Android apps, the extension) with no sign of Vercel, while a PROXY_SHARED_SECRET is configured.
    Not real: website traffic without a valid secret (it resolves to Vercel's few shared egress addresses), and anything
    while no secret is set (the address is then the left-most X-Forwarded-For entry, which a caller can write)."""
    settings = client_ip._settings(request)
    if not client_ip.secrets_of(settings):
        return None
    if not client_ip.via_vercel(request, settings) and request.headers.get("x-vercel-id"):
        return None
    ip = client_ip.client_ip(request, settings)
    return None if ip == client_ip.UNKNOWN else ip


def network_of(ip: str) -> str:
    """The address's network: IPv4 /24, IPv6 /48 (roughly one customer of a provider; a stand-in for an ASN lookup we don't have)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    prefix = 24 if addr.version == 4 else 48
    return str(ipaddress.ip_network((addr, prefix), strict=False))


class Starts:
    """Successful trial starts per key in the last 24 hours (in memory)."""

    def __init__(self) -> None:
        self._seen: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque[float]:
        q = self._seen.pop(key, deque())
        while q and now - q[0] > DAY:
            q.popleft()
        self._seen[key] = q
        while len(self._seen) > MAX_KEYS:
            self._seen.popitem(last=False)
        return q

    def count(self, key: str, now: float | None = None) -> int:
        with self._lock:
            return len(self._recent(key, time.monotonic() if now is None else now))

    def wait(self, key: str, limit: int, now: float | None = None) -> int | None:
        """Seconds until a start is allowed again under `limit` per day, or None if allowed now."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._recent(key, now)
            if limit <= 0 or len(q) < limit:
                return None
            return max(1, int(DAY - (now - q[-limit])) + 1)

    def add(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._recent(key, now).append(now)

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()


STARTS = Starts()


def ip_wait(ip: str | None, per_ip: int, per_network: int) -> int | None:
    """Seconds until this address may start a trial, or None. No real address: never throttled."""
    if ip is None:
        return None
    waits = [w for w in (STARTS.wait("ip:" + client_ip.bucket(ip), per_ip), STARTS.wait("net:" + network_of(ip), per_network)) if w is not None]
    return max(waits) if waits else None


def ip_record(ip: str | None) -> None:
    if ip is None:
        return
    STARTS.add("ip:" + client_ip.bucket(ip))
    STARTS.add("net:" + network_of(ip))


def reset_for_tests() -> None:
    STARTS.clear()
