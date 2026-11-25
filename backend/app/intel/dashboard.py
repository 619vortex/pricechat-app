"""Admin's "Intelligence (shadow)" page (Terminal Intelligence Engine, Phase 0): one read-only aggregate over the shadow record, the job
queue, the PC worker's heartbeat and the AI spend ledger, for a chosen window (1 h .. 14 days).

READ ONLY. build() never enqueues a job, never calls a model, never writes a row (it does not even run gate.refresh). With intel off and
empty tables every section is there, with zeros and empty lists. In shadow mode nothing here can change what any lane publishes.

Words used on the page
- decider: who produced the analysis behind a shadow verdict: "pc" (the owner's PC model), "cloud" (Claude, capped), "rules" (no analysis:
  the rules' own verdict) or "pending" (the analysis job has not been answered yet).
- lane decision: the lane's current newsworthy decision (IntelShadow.rules_ok); gate verdict: what the semantic gate would decide.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.intel import channel, gate, ledger
from app.intel.channel import aware
from app.models.intel import AiUsage, IntelJob, IntelWorker
from app.models.intel_shadow import IntelShadow

MAX_HOURS = 24 * 14
MAX_ROWS = 5000  # shadow rows / jobs read per window at most (Phase 0 volumes are a few hundred a day)
RECENT = 50
LIST = 50
TIERS = ("major", "significant", "notable", "noise")
TIER_RANK = {t: i for i, t in enumerate(TIERS)}
DECIDERS = ("pc", "cloud", "rules", "pending")
OVER_CAP = "over cap"  # anthropic_client.CloudUnavailable("over cap"), recorded on the job by router.fallback as "cloud: over cap"


def _settings(settings: Any = None) -> Any:
    return channel._settings(settings)


def _iso(t: datetime | None) -> str | None:
    return aware(t).isoformat() if t is not None else None  # type: ignore[union-attr]


def pct(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100); None for no values."""
    if not values:
        return None
    vals = sorted(values)
    k = max(0, min(len(vals) - 1, math.ceil(q / 100.0 * len(vals)) - 1))
    return vals[k]


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


# --- shadow rows ----------------------------------------------------------------------------------------------------------------


def _decider(row: IntelShadow, job: IntelJob | None) -> tuple[str, str]:
    """(decider, model) for one shadow row."""
    v = row.verdict or {}
    state = v.get("state") or "rules"
    if state == "pending":
        return "pending", ""
    if state == "done":
        by = (job.result_by if job is not None and job.status == "done" else None) or v.get("result_by")
        model = str(v.get("model") or ((job.result or {}).get("model") if job is not None else "") or "")
        if by not in ("pc", "cloud"):
            src = str(v.get("source") or "")
            by = "cloud" if model.startswith("claude") or "claude" in src else "pc"
        return by, model
    return "rules", ""


def _item(row: IntelShadow, job: IntelJob | None, decider: str, model: str) -> dict[str, Any]:
    v = row.verdict or {}
    rules = (v.get("input") or {}).get("rules") or {}
    return {"id": row.id, "lane": row.lane, "ref": row.ref, "title": row.title, "lane_ok": bool(row.rules_ok), "gate_ok": bool(row.semantic_ok),
            "agree": bool(row.agree), "tier": row.tier or "", "state": v.get("state") or "rules", "decider": decider, "model": model,
            "latency_ms": job.latency_ms if job is not None else None, "event_type": v.get("event_type") or "",
            "lane_reason": rules.get("reason") or "", "gate_reasons": list(v.get("reasons") or [])[:6], "hard_veto": list(v.get("hard_veto") or [])[:6],
            "seen": row.seen, "last_seen": _iso(row.last_seen)}


def _shadow(db: Session, since: datetime) -> dict[str, Any]:
    rows = list(db.scalars(select(IntelShadow).where(IntelShadow.last_seen >= since).order_by(IntelShadow.last_seen.desc()).limit(MAX_ROWS)))
    ids = {int(j) for r in rows if isinstance(j := (r.verdict or {}).get("job_id"), int)}
    jobs: dict[int, IntelJob] = {}
    id_list = sorted(ids)
    for i in range(0, len(id_list), 500):
        for j in db.scalars(select(IntelJob).where(IntelJob.id.in_(id_list[i:i + 500]))):
            jobs[j.id] = j
    by_decider = {d: 0 for d in DECIDERS}
    by_model: dict[str, int] = {}
    tiers = {t: 0 for t in (*TIERS, "unknown")}
    model_tiers = {t: 0 for t in (*TIERS, "unknown")}
    by_lane: dict[str, int] = {}
    recent: list[dict[str, Any]] = []
    misses: list[dict[str, Any]] = []
    false_pos: list[dict[str, Any]] = []
    for r in rows:
        job = jobs.get(int((r.verdict or {}).get("job_id") or 0))
        decider, model = _decider(r, job)
        by_decider[decider] += 1
        by_lane[r.lane] = by_lane.get(r.lane, 0) + 1
        tier = r.tier if r.tier in TIERS else "unknown"
        tiers[tier] += 1
        modelled = decider in ("pc", "cloud")
        if modelled:
            by_model[model or decider] = by_model.get(model or decider, 0) + 1
            model_tiers[tier] += 1
        item = _item(r, job, decider, model)
        if len(recent) < RECENT:
            recent.append(item)
        if not modelled:
            continue  # a miss / false positive needs the model's own reading, not the rules echoed back
        if not r.rules_ok and (r.tier == "major" or (r.tier == "significant" and r.semantic_ok)):
            misses.append(item)
        elif r.rules_ok and not r.semantic_ok and r.tier in ("noise", "notable"):
            false_pos.append(item)
    # rows come newest first and sort() is stable: within a tier the newest stays on top
    misses.sort(key=lambda x: (TIER_RANK.get(x["tier"], 9), not x["gate_ok"]))  # major first; a vetoed major after a clean one
    false_pos.sort(key=lambda x: -TIER_RANK.get(x["tier"], 0))  # noise first, then notable
    return {"total": len(rows), "by_decider": by_decider, "by_model": dict(sorted(by_model.items(), key=lambda kv: -kv[1])), "tiers": tiers,
            "model_tiers": model_tiers, "by_lane": by_lane, "recent": recent,
            "missed": {"possible_misses": misses[:LIST], "possible_false_positives": false_pos[:LIST], "misses_total": len(misses),
                       "false_positives_total": len(false_pos)}}


# --- jobs: latency and the PC's work ----------------------------------------------------------------------------------------------


def _latency(jobs: list[IntelJob]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], dict[str, list[float]]] = {}
    for j in jobs:
        if j.status != "done" or j.latency_ms is None:
            continue
        by = j.result_by or "rules"
        model = str((j.result or {}).get("model") or "") if by in ("pc", "cloud") else ""
        g = groups.setdefault((by, model or by, j.kind), {"total": [], "compute": []})
        g["total"].append(float(j.latency_ms))
        secs = (j.result or {}).get("seconds")
        if by == "pc" and isinstance(secs, (int, float)) and not isinstance(secs, bool):
            g["compute"].append(float(secs) * 1000.0)
    out = []
    for (by, model, kind), g in sorted(groups.items()):
        t, c = g["total"], g["compute"]
        out.append({"route": by, "model": model, "kind": kind, "n": len(t), "p50_ms": pct(t, 50), "p95_ms": pct(t, 95), "max_ms": max(t),
                    "compute_p50_ms": pct(c, 50), "compute_p95_ms": pct(c, 95)})
    return out


def _job_counts(jobs: list[IntelJob]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_result: dict[str, int] = {}
    for j in jobs:
        by_status[j.status] = by_status.get(j.status, 0) + 1
        if j.status == "done":
            by_result[j.result_by or "rules"] = by_result.get(j.result_by or "rules", 0) + 1
    over_cap = sum(1 for j in jobs if OVER_CAP in (j.error or ""))
    return {"total": len(jobs), "by_status": by_status, "by_result": by_result, "refused_over_cap": over_cap}


def _pc_work(jobs: list[IntelJob], worker_id: int | None) -> dict[str, int]:
    """Jobs the PC claimed in the window. A claim later taken over by Railway loses its worker_id (router._take), so with worker_id None
    this counts every claim (one PC in Phase 0)."""
    mine = [j for j in jobs if j.claimed_at is not None and (worker_id is None or j.worker_id == worker_id)]
    completed = sum(1 for j in mine if j.status == "done" and j.result_by == "pc")
    failed = sum(1 for j in mine if j.status == "failed" or (j.error or "").startswith(("pc failed", "pc result rejected")))
    lost = sum(1 for j in mine if "lease expired" in (j.error or "") or (j.status in ("expired",) and j.result_by != "pc")
               or (j.status == "done" and j.result_by != "pc"))
    return {"claimed": len(mine), "completed": completed, "failed": failed, "expired_or_taken": lost,
            "in_flight": sum(1 for j in mine if j.status == "leased")}


def availability(w: IntelWorker, since: datetime, now: datetime) -> dict[str, Any]:
    """Share of the window the worker was online (heartbeat fresh) and ready (models, not busy), from channel.accrue's hourly record.
    The record starts at the first heartbeat after it was deployed: `covered_seconds` is the part of the window it can speak for."""
    avail = (w.info or {}).get(channel.AVAIL_KEY) or {}
    first = since.strftime("%Y-%m-%dT%H")
    online = ready = 0.0
    hours = sorted(k for k in avail if isinstance(k, str))
    for k in hours:
        v = avail[k]
        if k >= first and isinstance(v, list) and len(v) == 2:
            online += float(v[0] or 0)
            ready += float(v[1] or 0)
    window = (now - since).total_seconds()
    start = None
    if hours:
        try:
            start = datetime.strptime(hours[0], "%Y-%m-%dT%H").replace(tzinfo=now.tzinfo)
        except ValueError:
            start = None
    covered = max(0.0, (now - max(since, start)).total_seconds()) if start is not None else 0.0
    if covered:
        online, ready = min(online, covered), min(ready, covered)
    return {"online_seconds": round(online), "ready_seconds": round(ready), "window_seconds": round(window), "covered_seconds": round(covered),
            "history_since": _iso(start), "pct_online": round(online / covered, 4) if covered else None,
            "pct_ready": round(ready / covered, 4) if covered else None}


def _workers(db: Session, jobs: list[IntelJob], since: datetime, now: datetime) -> list[dict[str, Any]]:
    workers = list(db.scalars(select(IntelWorker).order_by(IntelWorker.id)))
    out = []
    for w in workers:
        info = w.info or {}
        online = channel.fresh(w, now)
        out.append({"id": w.id, "name": w.name, "enabled": bool(w.enabled), "online": online,
                    "ready": online and bool(info.get("models")) and not info.get("busy"), "last_seen_at": _iso(w.last_seen_at),
                    "models": list(info.get("models") or []), "busy": bool(info.get("busy")), "version": info.get("version") or "",
                    "gpu": info.get("gpu") or "", "availability": availability(w, since, now),
                    "jobs": _pc_work(jobs, None if len(workers) == 1 else w.id)})
    return out


# --- spend -------------------------------------------------------------------------------------------------------------------------


def _spend(db: Session, s: Any, since: datetime, now: datetime, refused: int) -> dict[str, Any]:
    first_day = ledger.day_of(since)
    rows = db.execute(select(AiUsage.day, AiUsage.model, AiUsage.status, func.count(), func.coalesce(func.sum(AiUsage.cost_usd), 0.0),
                             func.coalesce(func.sum(AiUsage.tokens_in), 0), func.coalesce(func.sum(AiUsage.tokens_out), 0))
                      .where(AiUsage.day >= first_day).group_by(AiUsage.day, AiUsage.model, AiUsage.status)).all()
    per_day: dict[str, dict[str, float]] = {}
    d = since.date()
    while d <= now.date():
        per_day[d.isoformat()] = {"usd": 0.0, "calls": 0}
        d += timedelta(days=1)
    per_model: dict[str, dict[str, float]] = {}
    total = 0.0
    calls = 0
    for day, model, status, n, usd, t_in, t_out in rows:
        if status == "reserved":
            continue  # an open hold is not spend yet (ledger.status shows today's holds inside spent_usd)
        usd = float(usd or 0.0)
        cell = per_day.setdefault(day, {"usd": 0.0, "calls": 0})
        cell["usd"] = round(cell["usd"] + usd, 6)
        cell["calls"] += int(n)
        m = per_model.setdefault(model, {"usd": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0})
        m["usd"] = round(m["usd"] + usd, 6)
        m["calls"] += int(n)
        m["tokens_in"] += int(t_in or 0)
        m["tokens_out"] += int(t_out or 0)
        total += usd
        calls += int(n)
    today = ledger.status(db, s, now)
    return {"today": today, "cap_usd": today["cap_usd"], "window_usd": round(total, 6), "window_calls": calls,
            "per_model": [{"model": k, **v} for k, v in sorted(per_model.items(), key=lambda kv: -kv[1]["usd"])],
            "per_day": [{"day": k, **v} for k, v in sorted(per_day.items())], "refused_over_cap": refused}


# --- the page ----------------------------------------------------------------------------------------------------------------------


def build(db: Session, hours: int = 24, *, now: datetime | None = None, settings: Any = None) -> dict[str, Any]:
    s = _settings(settings)
    now = now or utcnow()
    hours = max(1, min(MAX_HOURS, int(hours)))
    since = now - timedelta(hours=hours)
    shadow = _shadow(db, since)
    report = gate.report(db, since, limit=LIST)
    c = report["counts"]
    decided = c["total"] - c["pending"]
    jobs = list(db.scalars(select(IntelJob).where(IntelJob.created_at >= since).order_by(IntelJob.id.desc()).limit(MAX_ROWS)))
    counts = _job_counts(jobs)
    return {
        "generated_at": now.isoformat(), "hours": hours, "since": since.isoformat(),
        "mode": "shadow", "banner": "Shadow mode — AI decisions are recorded only; nothing is published by AI",
        "switches": {"intel_enabled": bool(s.intel_enabled), "intel_shadow_enabled": bool(s.intel_shadow_enabled),
                     "breaking_cloud_first": bool(s.intel_breaking_cloud_first), "cloud_model": s.intel_cloud_model,
                     "token_configured": bool((s.intel_worker_token_sha256 or "").strip()), "pc_online": channel.pc_online(db, now, s)},
        "decisions": {k: shadow[k] for k in ("total", "by_decider", "by_model", "tiers", "model_tiers", "by_lane", "recent")},
        "disagreements": {"counts": c, "decided": decided, "agreement_rate": _rate(c["agree"], decided),
                          "lane_yes_ai_no": report["rules_only"], "lane_no_ai_yes": report["semantic_only"],
                          "prompt_version": report["prompt_version"]},
        "latency": {"groups": _latency(jobs), "jobs": counts},
        "workers": _workers(db, jobs, since, now),
        "pc_jobs": _pc_work(jobs, None),
        "missed": shadow["missed"],
        "spend": _spend(db, s, since, now, counts["refused_over_cap"]),
    }
