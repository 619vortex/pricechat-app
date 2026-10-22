"""Who is really calling: the client address every rate limit and abuse check keys on.

Two ways in, and only one can be trusted to report the visitor:

* Through the website: browser -> Vercel (frontend/vercel.json rewrites /api/* to Railway) -> Railway's edge -> uvicorn.
  Vercel replaces X-Forwarded-For / X-Real-IP / X-Vercel-Forwarded-For with the visitor's address (it never passes a
  caller's own copy on). A `routes` transform in vercel.json also sets EDGE_HEADER to PROXY_SHARED_SECRET (Vercel's env),
  replacing anything the caller sent. Only when that header matches our PROXY_SHARED_SECRET (constant-time) do we believe
  what Vercel says the visitor is: X-Vercel-Forwarded-For (Railway's edge doesn't touch it), else the left-most
  X-Forwarded-For entry (Vercel wrote it; Railway appends after it).
* Directly to the Railway domain (anyone can): every header is the caller's to forge, EXCEPT the entry Railway's edge
  adds at the right of X-Forwarded-For (the address that connected to it). So the key is the right-most entry
  (TRUSTED_PROXY_HOPS counts in from the right, default 1) and never the left-most one. Without the secret configured,
  website traffic is also keyed this way: on Vercel's egress addresses, i.e. coarser, but never spoofable.

request.client is not used while an X-Forwarded-For header is present: uvicorn runs with --proxy-headers
--forwarded-allow-ips=* (needed so request.url keeps Railway's https scheme), which overwrites request.client with the
LEFT-most, caller-supplied entry. With no X-Forwarded-For at all (local runs, tests) uvicorn leaves the real socket peer.

client_ip() is the address (canonical form, for storing / hashing); client_key() is the rate-limit key, which puts a whole
IPv6 /64 in one bucket (one subscriber usually holds a /64, so rotating within it mustn't buy fresh buckets).
"""

import hmac
import ipaddress
import logging

from fastapi import Request

from app.config.settings import Settings, get_settings

log = logging.getLogger(__name__)

EDGE_HEADER = "x-pc-edge"
MIN_SECRET_LEN = 16
UNKNOWN = "unknown"


def _settings(request: Request) -> Settings:
    try:
        runtime = getattr(request.app.state, "runtime", None)
    except (AttributeError, KeyError, AssertionError):
        runtime = None
    return getattr(runtime, "settings", None) or get_settings()


def secrets_of(settings: Settings) -> list[bytes]:
    """The accepted PROXY_SHARED_SECRET values (comma-separated: old,new while rotating). Too-short values and an unexpanded
    Vercel reference ("$PROXY_SHARED_SECRET") are never accepted."""
    raw = settings.proxy_shared_secret.get_secret_value() if settings.proxy_shared_secret else ""
    out = []
    for s in raw.split(","):
        s = s.strip()
        if len(s) >= MIN_SECRET_LEN and not s.startswith("$"):
            out.append(s.encode("utf-8"))
    return out


def via_vercel(request: Request, settings: Settings | None = None) -> bool:
    """True only when the request carries our shared secret, i.e. it came through the website's Vercel rewrite."""
    accepted = secrets_of(settings or _settings(request))
    got = request.headers.get(EDGE_HEADER)
    if not accepted or not got:
        return False
    given = got.strip().encode("utf-8", "replace")
    ok = False
    for want in accepted:  # every candidate is compared (no early exit), each in constant time
        ok |= hmac.compare_digest(given, want)
    return ok


def parse_ip(raw: str | None) -> str | None:
    """A canonical address from one forwarded entry ("1.2.3.4", "1.2.3.4:5678", "[2001:db8::1]:443", "2001:DB8::1"),
    or None for anything else ("unknown", obfuscated identifiers, garbage)."""
    s = (raw or "").strip().strip('"')
    if not s or len(s) > 64:
        return None
    if s.startswith("["):  # [v6] or [v6]:port
        s = s[1:s.find("]")] if "]" in s else ""
    elif s.count(":") == 1:  # v4:port
        s = s.split(":", 1)[0]
    s = s.split("%", 1)[0]  # an IPv6 zone id
    try:
        ip = ipaddress.ip_address(s)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return str(ip)


def _forwarded_for(request: Request) -> list[str]:
    """Every X-Forwarded-For entry, in order, across repeated header lines."""
    return [p.strip() for line in request.headers.getlist("x-forwarded-for") for p in line.split(",")]


def client_ip(request: Request, settings: Settings | None = None) -> str:
    settings = settings or _settings(request)
    hops = _forwarded_for(request)
    trusted = via_vercel(request, settings)
    _note(request, settings, trusted)
    if trusted:
        vercel = request.headers.get("x-vercel-forwarded-for", "").split(",")[0]
        ip = parse_ip(vercel) or (parse_ip(hops[0]) if hops else None)
        if ip:
            return ip
    if hops:
        n = max(1, settings.trusted_proxy_hops)
        # the entry our own edge appended; never fall back to entries further left (those are the caller's to write)
        return (parse_ip(hops[-n]) if len(hops) >= n else None) or UNKNOWN
    peer = request.client.host if request.client else None
    return parse_ip(peer) or (peer or UNKNOWN)


_noted: set[str] = set()


def _note(request: Request, settings: Settings, trusted: bool) -> None:
    """Once per process: confirm the secret works, or flag Vercel-looking traffic without it (a mismatch between the
    Railway and Vercel values, or a frontend not redeployed since the env var was added). A forger can trigger the
    warning too, hence "may"."""
    if trusted and "ok" not in _noted:
        _noted.add("ok")
        log.info("Website traffic carries the proxy secret: rate limits key on the visitor Vercel reports.")
    elif not trusted and "bad" not in _noted and request.headers.get("x-vercel-id") and secrets_of(settings):
        _noted.add("bad")
        log.warning("A request with Vercel's headers arrived without a valid %s: PROXY_SHARED_SECRET may differ between "
                    "Railway and Vercel (or the frontend wasn't redeployed after setting it).", EDGE_HEADER)


def bucket(ip: str) -> str:
    """Rate-limit key for an address: IPv6 by its /64, IPv4 as is."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address):
        return str(ipaddress.IPv6Network((addr, 64), strict=False))
    return str(addr)


def client_key(request: Request, settings: Settings | None = None) -> str:
    return bucket(client_ip(request, settings))


def startup_check(settings: Settings) -> None:
    """One log line in production about how visitors are identified, so a missing / mistyped secret is visible."""
    if not settings.is_production:
        return
    raw = settings.proxy_shared_secret.get_secret_value().strip() if settings.proxy_shared_secret else ""
    if not raw:
        log.warning("PROXY_SHARED_SECRET is not set: rate limits key website visitors on Vercel's egress address "
                    "(right-most X-Forwarded-For hop). Set the same value in Railway and Vercel to key on the visitor.")
    elif not secrets_of(settings):
        log.warning("PROXY_SHARED_SECRET is too short (min %d characters) and is ignored.", MIN_SECRET_LEN)
