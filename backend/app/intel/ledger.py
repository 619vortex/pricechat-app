"""The ONE shared AI spend ledger (Terminal Intelligence Engine Phase 0; doc §6): a price table for the current Claude models and a hard
daily cap (settings.intel_daily_usd_cap, owner approved $3/day).

    est = ledger.estimate(model, tokens_in, max_tokens_out)
    row = ledger.reserve(db, model, est, "intel.analyze")   # None = over the cap: the caller falls back (never silently overspends)
    ... the paid call ...
    ledger.settle(db, row, tokens_in, tokens_out, cache_read)  # the real cost replaces the reservation (0 if nothing was billed)

or, for a caller that only reports spend afterwards: `can_spend(db, est)` then `record(db, model, in, out, purpose)`.

The cap counts today's (UTC) recorded calls AND open reservations, so concurrent callers can't both slip under it. An unknown model is
charged at Opus prices (an unknown price can only make the cap stricter).

TODO (later phase, not wired yet): route the six existing raw-httpx Anthropic call sites through this ledger and retire the two
inconsistent price tables (committee/llm.py COSTS, newsdesk/chief.py): newsdesk/writer.py, newsdesk/network.py, newsdesk/telegram.py,
trending/writer.py, terminal/feed.py, newsdesk/editor.py, newsdesk/chief.py (+ portfolio/condition.py, committee/llm.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.intel import AiUsage

M = 1_000_000


@dataclass(frozen=True, slots=True)
class Price:
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float


PRICES: dict[str, Price] = {
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01),
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.10),
    "claude-opus-5-5": Price(4.00, 20.00, 0.20),
}
UNKNOWN = PRICES["claude-opus-5-5"]
STALE_RESERVATION = timedelta(minutes=15)  # a reservation never settled (crash mid-call) stops counting after this


class OverCap(Exception):
    """Refused: the call would take today's AI spend past the cap. The caller must fall back (PC, rules), never retry the cloud."""


def price(model: str) -> Price:
    return PRICES.get((model or "").strip(), UNKNOWN)


def cost(model: str, tokens_in: int, tokens_out: int, cache_read: int = 0) -> float:
    p = price(model)
    return (max(0, tokens_in) * p.input + max(0, tokens_out) * p.output + max(0, cache_read) * p.cache_read) / M


def estimate(model: str, tokens_in: int, max_tokens_out: int) -> float:
    """Worst case for one call: every input token at the full input price, the whole max_tokens budget spent."""
    return cost(model, tokens_in, max_tokens_out)


def tokens_for(text: str) -> int:
    """A deliberately high token estimate for text (about 3 characters a token; real English is ~4)."""
    return len(text or "") // 3 + 16


def _cap(settings: Any = None) -> float:
    if settings is None:
        from app.config.settings import get_settings

        settings = get_settings()
    return float(settings.intel_daily_usd_cap)


def day_of(now: datetime | None = None) -> str:
    return (now or utcnow()).strftime("%Y-%m-%d")


def spent_today(db: Session, now: datetime | None = None) -> float:
    """Today's recorded spend plus reservations still open (recent ones only)."""
    now = now or utcnow()
    done = db.scalar(select(func.coalesce(func.sum(AiUsage.cost_usd), 0.0)).where(AiUsage.day == day_of(now), AiUsage.status != "reserved"))
    held = db.scalar(select(func.coalesce(func.sum(AiUsage.cost_usd), 0.0)).where(
        AiUsage.day == day_of(now), AiUsage.status == "reserved", AiUsage.at >= now - STALE_RESERVATION))
    return float(done or 0.0) + float(held or 0.0)


def can_spend(db: Session, est: float, settings: Any = None, now: datetime | None = None) -> bool:
    return spent_today(db, now) + max(0.0, est) <= _cap(settings) + 1e-12


def status(db: Session, settings: Any = None, now: datetime | None = None) -> dict[str, Any]:
    spent, cap = spent_today(db, now), _cap(settings)
    return {"day": day_of(now), "spent_usd": round(spent, 6), "cap_usd": cap, "over_cap": spent >= cap}


def reserve(db: Session, model: str, est: float, purpose: str, settings: Any = None, now: datetime | None = None) -> AiUsage | None:
    """Hold `est` against today's cap before a paid call; None when it doesn't fit (the caller falls back)."""
    now = now or utcnow()
    if not can_spend(db, est, settings, now):
        return None
    row = AiUsage(day=day_of(now), at=now, model=model[:60], purpose=purpose[:40], status="reserved", tokens_in=0, tokens_out=0,
                  cache_read=0, cost_usd=max(0.0, est))
    db.add(row)
    db.commit()
    return row


def settle(db: Session, row: AiUsage, tokens_in: int, tokens_out: int, cache_read: int = 0, ok: bool = True) -> AiUsage:
    """Replace the reservation with what the provider actually billed (its usage block). Nothing billed: pass zeros."""
    row.tokens_in, row.tokens_out, row.cache_read = max(0, tokens_in), max(0, tokens_out), max(0, cache_read)
    row.cost_usd = cost(row.model, tokens_in, tokens_out, cache_read)
    row.status = "done" if ok else "failed"
    db.commit()
    return row


def record(db: Session, model: str, tokens_in: int, tokens_out: int, purpose: str, cache_read: int = 0,
           now: datetime | None = None) -> AiUsage:
    """One finished paid call (for callers that check can_spend first and report afterwards)."""
    now = now or utcnow()
    row = AiUsage(day=day_of(now), at=now, model=model[:60], purpose=purpose[:40], status="done", tokens_in=max(0, tokens_in),
                  tokens_out=max(0, tokens_out), cache_read=max(0, cache_read), cost_usd=cost(model, tokens_in, tokens_out, cache_read))
    db.add(row)
    db.commit()
    return row
