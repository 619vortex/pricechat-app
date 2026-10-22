"""Light rate limits for public, unauthenticated endpoints that write to the database or fan out (referral clicks, the Earn
boards, the public health check).

In-memory token buckets, per client and one shared ("*") per rule: production is ONE Railway replica, so memory is the
right place (nothing stored in the database; a restart simply forgets). With more replicas each would limit on its own.
The client is admin_auth.client_key (first X-Forwarded-For hop as set by Vercel / Railway, else the socket peer). It can be
spoofed by calling the backend directly, which is what the shared bucket per rule is for. At most MAX_KEYS clients are
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
