"""Kiwi AI Investment Committee: one run, start to finish (contract: Committee run).

run_committee: evidence pack (app.committee.evidence.build_evidence, or one passed in) → analysts in parallel → bull / bear →
research manager → trader → risk panel → portfolio manager (roles.py, each grounded, each with its rules fallback) → the
Kiwi risk gate (risk_gate.py: the REAL risk engine decides execution, never a model) → the run JSON, stored in cm_runs.
start_run: the same as a background task with its own database session (queued → running → done | failed), behind
  - the cache: the same market + timeframe within CACHE_FOR → that run again (no new calls, meta.cached, not counted)
  - the daily limit per Telegram user / member (COMMITTEE_DAILY_LIMIT; COMMITTEE_DAILY_LIMIT_RANKED for a Kiwi Passport at
    the boost rank, the same mechanism as the Vortex trade checks); admins are unlimited
  - COMMITTEE_MAX_CONCURRENT runs at once (the rest wait, queued)
Research only: nothing here places, changes or cancels an order or touches tr_positions / member accounts / API wallets;
committee runs never share a session or a lock with execution. The public run JSON never contains member data.
"""

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.committee import llm, prompts, risk_gate, roles
from app.db.base import utcnow
from app.models.committee import CmRun, CmUsage

logger = logging.getLogger(__name__)

CACHE_FOR = timedelta(minutes=15)
TIMEFRAMES = ("15m", "1h", "4h", "1d")
FRAMEWORK = "TradingAgents v0.7.0 pattern (ported)"
_TASKS: dict[str, asyncio.Task] = {}
_running = 0


class CommitteeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


class LimitReached(CommitteeError):
    pass


@dataclass
class CommitteeRun:
    id: str
    result: dict[str, Any]

    @property
    def cached(self) -> bool:
        return bool((self.result.get("meta") or {}).get("cached"))


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(d: datetime | None) -> str | None:
    return _aware(d).isoformat().replace("+00:00", "Z") if d else None


def _norm(symbol: str, timeframe: str) -> tuple[str, str]:
    sym = (symbol or "").strip().upper().removeprefix("$")[:32]
    if not sym:
        raise CommitteeError("bad_symbol", "Give a coin, e.g. BTC.")
    if timeframe not in TIMEFRAMES:
        raise CommitteeError("bad_timeframe", f"Timeframe must be one of {', '.join(TIMEFRAMES)}.")
    return sym, timeframe


# ---------------------------------------------------------------- evidence (the cm-evidence agent's package, imported lazily)
async def _evidence(db: Session, symbol: str, timeframe: str, as_of: datetime | None, mode: str) -> dict[str, Any]:
    from app.committee.evidence import EvidenceError, build_evidence

    try:
        return await build_evidence(db, symbol, timeframe, as_of=as_of, mode=mode)
    except EvidenceError as e:
        raise CommitteeError(getattr(e, "code", "evidence"), getattr(e, "message", None) or str(e)) from e


# ---------------------------------------------------------------- cache and limits
def cached_run(db: Session, symbol: str, timeframe: str, now: datetime | None = None) -> CmRun | None:
    """A live run of the same market + timeframe within CACHE_FOR that is done (or still on its way)."""
    now = now or utcnow()
    return db.scalar(select(CmRun).where(CmRun.symbol == symbol, CmRun.timeframe == timeframe, CmRun.mode == "live",
                                         CmRun.status.in_(("queued", "running", "done")), CmRun.created_at >= now - CACHE_FOR)
                     .order_by(CmRun.id.desc()).limit(1))


def _who(db: Session, requested_by: dict[str, Any]) -> tuple[str | None, Any]:
    """(usage key, the member or None). A Telegram user with a linked Kiwi Passport counts as that member."""
    member_id, tg = requested_by.get("member_id"), requested_by.get("tg_user_id")
    m = None
    if member_id is None and tg is not None:
        from app.models.members import LinkedIdentity, Member

        ident = db.scalar(select(LinkedIdentity).where(LinkedIdentity.platform == "telegram", LinkedIdentity.status == "active",
                                                       LinkedIdentity.platform_user_id == str(int(tg))))
        m = db.get(Member, ident.member_id) if ident else None
        m = m if m is not None and m.status == "active" else None
        member_id = m.id if m else None
    elif member_id is not None:
        from app.models.members import Member

        m = db.get(Member, int(member_id))
    if member_id is not None:
        return f"m:{int(member_id)}", m
    if tg is not None:
        return f"tg:{int(tg)}", None
    return None, None


def config(db: Session) -> dict[str, Any]:
    """Owner settings TsSettings.strategies["committee"]: {"on", "min_rank"} (+ "reference" for the risk gate)."""
    from app.models.tradingos import TsSettings
    from app.tradingos.assist import DEFAULTS

    s = db.scalar(select(TsSettings).limit(1))
    c = ((s.strategies or {}) if s else {}).get("committee") or {}
    return {"on": c.get("on", True) is not False, "min_rank": int(c.get("min_rank", DEFAULTS["min_rank"]))}


def daily_limit(db: Session, requested_by: dict[str, Any], s: Any = None) -> tuple[int, bool]:
    """(runs per day, boosted?): more for a Kiwi Passport at the boost rank (same rule as the Vortex trade checks)."""
    s = s or llm.settings()
    _, m = _who(db, requested_by)
    if m is not None:
        from app.members import xp

        try:
            if xp.rank_for(db, m.rank_score).level >= config(db)["min_rank"]:
                return s.committee_daily_limit_ranked, True
        except Exception:  # noqa: BLE001 - no rank catalogue: the public limit
            db.rollback()
    return s.committee_daily_limit, False


def used_today(db: Session, requested_by: dict[str, Any]) -> int:
    key, _ = _who(db, requested_by)
    if key is None:
        return 0
    row = db.scalar(select(CmUsage).where(CmUsage.day == llm.day_of(), CmUsage.key == key).execution_options(populate_existing=True))
    return row.count if row else 0


def _count(db: Session, requested_by: dict[str, Any]) -> None:
    key, m = _who(db, requested_by)
    if key is None:
        return
    row = llm.usage_row(db, key, tg_user_id=requested_by.get("tg_user_id"), member_id=m.id if m else requested_by.get("member_id"))
    db.execute(update(CmUsage).where(CmUsage.id == row.id).values(count=CmUsage.count + 1).execution_options(synchronize_session=False))
    db.commit()


# ---------------------------------------------------------------- one run
def _new_row(symbol: str, timeframe: str, mode: str, depth: str, status: str, requested_by: dict[str, Any]) -> CmRun:
    return CmRun(public_id="cm_" + secrets.token_hex(8), symbol=symbol, timeframe=timeframe, mode=mode, depth=depth, status=status,
                 requested_by_tg=requested_by.get("tg_user_id"), requested_by_member=requested_by.get("member_id"),
                 chat_id=requested_by.get("chat_id"), message_id=requested_by.get("message_id"), created_at=utcnow(),
                 cost_usd=0.0, calls=0)


def stored(row: CmRun) -> dict[str, Any] | None:
    if row.result:
        return row.result
    return {"id": row.public_id, "status": row.status, "asset": {"symbol": row.symbol}, "timeframe": row.timeframe,
            "created_at": _iso(row.created_at), "error": row.error if row.status == "failed" else None}


async def run_committee(db: Session, symbol: str, timeframe: str = "1h", *, requested_by: dict[str, Any] | None = None, mode: str = "live",
                        as_of: datetime | None = None, depth: str = "full", evidence: dict[str, Any] | None = None,
                        backend: llm.Backend | None = None, provider: Any = None, gate: Any = None, use_cache: bool = True,
                        persist: bool = True, _row: CmRun | None = None, gate_ctx: Any = None) -> CommitteeRun:
    """gate_ctx: a risk_gate.GateContext (backtests: the point-in-time context); None = the live one from `db`."""
    symbol, timeframe = _norm(symbol, timeframe)
    requested_by = requested_by or {}
    depth = "quick" if depth == "quick" else "full"
    if use_cache and _row is None and mode == "live" and as_of is None and evidence is None:
        hit = cached_run(db, symbol, timeframe)
        if hit is not None and hit.status == "done" and hit.result:
            return CommitteeRun(hit.public_id, {**hit.result, "meta": {**hit.result["meta"], "cached": True}})
    started = time.perf_counter()
    row = _row
    if row is None and persist:
        row = _new_row(symbol, timeframe, mode, depth, "running", requested_by)
        db.add(row)
        db.commit()
    if row is not None:
        row.status, row.started_at = "running", utcnow()
        db.commit()
    try:
        ev = evidence if evidence is not None else await _evidence(db, symbol, timeframe, as_of, mode)
        if backend is None:
            backend, why = await llm.choose(db, provider, gate)
        else:
            why = None
        ledger = llm.Ledger(db, backend, run_id=row.id if row is not None else None)
        result = await _pipeline(db, ev, ledger, depth, why, gate_ctx)
    except Exception as e:
        if row is not None:
            row.status, row.finished_at = "failed", utcnow()
            row.error = (e.message if isinstance(e, CommitteeError) else f"internal error ({type(e).__name__})")[:500]
            db.commit()
        raise
    pid = row.public_id if row is not None else "cm_" + secrets.token_hex(8)
    result = {"id": pid, "status": "done", **result}
    result["meta"]["duration_s"] = round(time.perf_counter() - started, 2)
    if row is not None:
        a = result["asset"]
        row.venue, row.market_type, row.market_id = a.get("venue"), a.get("market_type"), str(a.get("market_id") or "")[:128] or None
        row.price_at = (result.get("price") or {}).get("value")
        row.verdict, row.rating, row.exec_status = result["verdict"]["action"], result["verdict"]["rating"], result["execution"]["status"]
        row.backend, row.cost_usd, row.calls = result["meta"]["backend"], result["meta"]["cost_usd"], result["meta"]["calls"]
        row.status, row.finished_at, row.result = "done", utcnow(), result
        db.commit()
    return CommitteeRun(pid, result)


async def _pipeline(db: Session, ev: dict[str, Any], ledger: llm.Ledger, depth: str, why_rules: str | None,
                    gate_ctx: Any = None) -> dict[str, Any]:
    ctx = roles.Context(ev, ledger, depth)
    if why_rules:
        ctx.degraded.append(f"backend: {why_rules}")
    reports = await roles.analysts(ctx)
    deb = await roles.debate(ctx, reports)
    tr = await roles.trader(ctx, reports, deb)
    rk = await roles.risk_panel(ctx, reports, tr)
    pm = await roles.portfolio(ctx, deb, tr, rk)
    guard = roles.data_guard(ev)
    execution = risk_gate.evaluate(db, ev, pm["action"], roles.plan_from(ev, pm["action"]), stale_reason=guard,
                                   scenarios={"LONG": roles.plan_from(ev, "LONG"), "SHORT": roles.plan_from(ev, "SHORT")}, ctx=gate_ctx)
    verdict = {**pm, "scenarios": roles.scenarios_view(ev)}
    verdict = {k: verdict[k] for k in ("action", "rating", "summary", "scenarios", "what_changes_view", "author")}
    models = {**ledger.backend.models(), **ledger.models_used}
    return {
        "asset": ev.get("asset") or {}, "timeframe": ev.get("timeframe"), "as_of": ev.get("as_of"), "price": ev.get("price") or {},
        "analysts": reports, "debate": deb, "trader": tr, "risk_committee": rk, "verdict": verdict, "execution": execution,
        "meta": {"backend": ledger.backend.name, "models": {"fast": models.get("fast"), "deep": models.get("deep"),
                                                            "synthesis": models.get("synthesis")},
                 "calls": ledger.calls, "tokens_in": ledger.tokens_in, "tokens_out": ledger.tokens_out, "cost_usd": round(ledger.cost_usd, 6),
                 "duration_s": 0.0, "cached": False, "framework": FRAMEWORK, "prompt_version": prompts.PROMPT_VERSION,
                 "degraded": [*(f"evidence: {d}" for d in ev.get("degraded") or []), *ctx.degraded]},
        "evidence": ev,
    }


# ---------------------------------------------------------------- background runs
def running() -> int:
    return _running


async def _slot(max_concurrent: int) -> None:
    global _running
    while _running >= max_concurrent:
        await asyncio.sleep(0.2)
    _running += 1


async def _background(public_id: str, depth: str, provider: Any, gate: Any, backend: llm.Backend | None) -> None:
    global _running
    from app.db import session as dbs

    db = None
    try:
        await _slot(int(llm.settings().committee_max_concurrent))
        db = dbs.SessionLocal()
        row = db.scalar(select(CmRun).where(CmRun.public_id == public_id))
        if row is None:
            return
        try:
            await run_committee(db, row.symbol, row.timeframe, mode=row.mode, depth=depth, provider=provider, gate=gate,
                                backend=backend, use_cache=False, _row=row)
        except CommitteeError as e:
            logger.info("committee %s failed: %s", public_id, e.code)
        except Exception:  # noqa: BLE001 - recorded on the row (failed), never crashes the server
            logger.exception("committee %s crashed", public_id)
    finally:
        if db is not None:
            db.close()
            _running -= 1
        _TASKS.pop(public_id, None)


def start_run(db: Session, symbol: str, timeframe: str = "1h", *, requested_by: dict[str, Any] | None = None, depth: str = "full",
              provider: Any = None, gate: Any = None, backend: llm.Backend | None = None) -> str:
    """Queue a run and return its public id at once (a cached run's id when there is one). Raises LimitReached / CommitteeError.
    Must be called from a running event loop (the API route / the Telegram handler)."""
    symbol, timeframe = _norm(symbol, timeframe)
    requested_by = requested_by or {}
    if not config(db)["on"]:
        raise CommitteeError("off", "The committee is switched off.")
    hit = cached_run(db, symbol, timeframe)
    if hit is not None:
        return hit.public_id
    if not requested_by.get("admin"):
        limit, _ = daily_limit(db, requested_by)
        if used_today(db, requested_by) >= limit:
            raise LimitReached("limit", f"Daily limit reached ({limit} committee runs a day).")
    row = _new_row(symbol, timeframe, "live", "quick" if depth == "quick" else "full", "queued", requested_by)
    db.add(row)
    db.commit()
    if not requested_by.get("admin"):
        _count(db, requested_by)
    _TASKS[row.public_id] = asyncio.get_running_loop().create_task(_background(row.public_id, row.depth, provider, gate, backend))
    return row.public_id


async def wait(public_id: str) -> None:
    """Tests and the Telegram handler: until the background run finishes."""
    t = _TASKS.get(public_id)
    if t is not None:
        await t


def find(db: Session, public_id: str) -> CmRun | None:
    return db.scalar(select(CmRun).where(CmRun.public_id == public_id[:24]))
