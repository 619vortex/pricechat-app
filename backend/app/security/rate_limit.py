"""Light rate limits for public, unauthenticated endpoints that write to the database or fan out (referral clicks, the Earn
boards, the public health check).

In-memory token buckets, per client and one shared ("*") per rule: production is ONE Railway replica, so memory is the
right place (nothing stored in the database; a restart simply forgets). With more replicas each would limit on its own.
The client is admin_auth.client_key (app.security.client_ip: the visitor Vercel reports when the request carries the shared
proxy secret, else the address Railway's edge saw; a caller can't pick its own key). The shared bucket per rule is the
backstop against many real addresses. At most MAX_KEYS clients are
remembered per rule; the least recently seen are forgotten first (a forgotten client just starts with a full bucket).
"""

import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from fastapi import HTTPException, Request

from app.security.admin_auth import client_key

MAX_KEYS = 10_000


@dataclass
class Bucket:
    """`burst` requests at once, then one every `every` seconds. `shared_*`: the same for all clients together."""

    burst: float
    every: float
    shared_burst: float
    shared_every: float
    _seen: OrderedDict[str, tuple[float, float]] = field(default_factory=OrderedDict)  # key -> (tokens, at)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _take(self, key: str, burst: float, every: float, now: float) -> float:
        """Spend a token: 0.0 when allowed, else seconds until one is free (nothing spent)."""
        tokens, at = self._seen.pop(key, (burst, now))
        tokens = min(burst, tokens + (now - at) / every)
        wait = 0.0 if tokens >= 1 else (1 - tokens) * every
        self._seen[key] = (tokens - 1 if not wait else tokens, now)  # re-inserted: most recently seen last
        while len(self._seen) > MAX_KEYS:
            self._seen.popitem(last=False)
        return wait

    def hit(self, key: str, now: float | None = None) -> int | None:
        """None when allowed, else the Retry-After seconds."""
        now, key = time.monotonic() if now is None else now, "c:" + key  # prefixed: no client can be "*"
        with self._lock:
            wait = self._take(key, self.burst, self.every, now)
            if not wait:
                wait = self._take("*", self.shared_burst, self.shared_every, now)
                if wait and key in self._seen:  # the shared limit refused: give the client its token back
                    tokens, at = self._seen[key]
                    self._seen[key] = (tokens + 1, at)
        return max(1, math.ceil(wait - 1e-9)) if wait else None

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()


# rule -> limits. Health: monitors poll about once a minute. Referral clicks: a person clicks a few times at most.
# Earn boards: the pages poll every ~30 s and are cached server-side; this only stops scripted hammering.
LIMITS = {
    "health": Bucket(burst=10, every=6, shared_burst=200, shared_every=0.1),  # 10/min per client after a burst of 10
    "referral": Bucket(burst=10, every=6, shared_burst=300, shared_every=0.2),  # over it: still redirected, click not counted
    "earn": Bucket(burst=30, every=1, shared_burst=600, shared_every=0.02),
    "vpn_device": Bucket(burst=5, every=600, shared_burst=60, shared_every=5),  # Kiwi VPN device creation, per member
    "vpn_key": Bucket(burst=30, every=5, shared_burst=600, shared_every=0.1),  # anonymous key requests, per client
    "vpn_new": Bucket(burst=5, every=600, shared_burst=120, shared_every=5),  # new anonymous keys, per client
    "vpn_reviewer": Bucket(burst=5, every=120, shared_burst=60, shared_every=5),  # the store-review password sign-in, per client
    "vpn_pay": Bucket(burst=6, every=120, shared_burst=120, shared_every=2),  # starting a payment, per client
    # starting a Google / X / Telegram sign-in (each stores a pending attempt) and the Telegram widget check: a person
    # needs a handful; the shared bucket is generous so a flood from one script can't lock everyone else out of signing in
    "signin": Bucket(burst=10, every=6, shared_burst=600, shared_every=0.05),
    "vortex_auth": Bucket(burst=5, every=60, shared_burst=30, shared_every=10),  # failed bearer tokens on /api/vortex/*
    # the public signal chart images (a candle fetch + render per signal per minute): the terminal shows up to ~10 at once
    "chart": Bucket(burst=20, every=3, shared_burst=300, shared_every=0.1),
    # starting a Kiwi AI Investment Committee run (each can cost several model calls; the daily limit per member still applies)
    "committee": Bucket(burst=5, every=20, shared_burst=30, shared_every=2),
    # the public candles under a committee run's chart (cached per market for a minute; a page loads them once per run shown)
    "committee_chart": Bucket(burst=20, every=3, shared_burst=300, shared_every=0.1),
    # visitors' Pokémon Market reads (card pages, charts, tables): a person browsing opens a few a second at most; a crawler
    # walking /api/pokemon/cards/<id> one id after another held every database connection on 2026-10-23
    # inbound webhooks (signature-checked): TikTok delivers in bursts after an outage; this only stops a flood of junk
    "webhook": Bucket(burst=120, every=0.1, shared_burst=600, shared_every=0.02),
    "market": Bucket(burst=60, every=0.5, shared_burst=1200, shared_every=0.01),
}


def retry_after(rule: str, request: Request) -> int | None:
    return LIMITS[rule].hit(client_key(request))


def limit(rule: str):  # type: ignore[no-untyped-def]
    """A route dependency: 429 + Retry-After when over the limit."""

    def check(request: Request) -> None:
        wait = retry_after(rule, request)
        if wait is not None:
            raise HTTPException(429, "Too many requests. Please slow down.", headers={"Retry-After": str(wait)})

    return check


def reset_for_tests() -> None:
    for b in LIMITS.values():
        b.clear()
