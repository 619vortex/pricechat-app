"""Terminal Intelligence Engine, Phase 0: the shared semantic gate, in SHADOW (docs/intelligence/2026-11-25-terminal-intelligence-engine.md
section 4.3).

Both lanes (News Desk: events._newsworthy, Trending: engine.suggest) call observe() once per judged candidate, AFTER their own decision. It
records what the semantic gate WOULD decide next to what the rules decided (table intel_shadow) and nothing else: no return value is used,
nothing it does can change what either lane publishes, holds or rejects, and it never raises into a lane.

The decision (verdict()) is deterministic:
- The model (an app.intel.schema.ItemAnalysis from the PC worker or Claude, via app.intel.router) replaces ONLY the "is there a stated
  consequence" judgement (newsworthy.assess / _consequence, importance.judge's tier): newsworthy = materiality major or significant, not
  speculative, not opinion/feature (ops/intel/bench.py decide(), same rule), and the consequence is stated in the text, not inferred.
- The existing rules stay HARD VETOES and always win: hedge / rumour / forecast wording (the lanes' own patterns, classes.hedge_words), a
  round-up / feature / reaction (newsworthy.structural_weak, REACTION), the audience gate's outright refusals (a routine military update,
  diplomacy talk), and every lane-side reason that is not the consequence test (a repeat on any lane, the liquidation pause, caps,
  spacing, the automatic line, rumour watch, sensitivity, an unverified incident, a copy gate, the News Desk cross-lane hold).
- Claim classes are computed from the outlets / evidence by app.intel.classes, never by the model.
- No analysis: source "rules" and the rules' own verdict.

OFF by default (settings.intel_shadow_enabled): observe() then returns before any query or write. Jobs are enqueued only when
settings.intel_enabled is on as well (priority bulk: never awaited, never blocks a lane).
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.intel import classes
from app.intel.prompt import PROMPT_VERSION
from app.intel.schema import ItemAnalysis

logger = logging.getLogger(__name__)

LANES = ("newsdesk", "trending")
REFRESH_WINDOW = timedelta(days=2)  # pending rows older than this are left alone (their bulk job expired after 6 h anyway)
REFRESH_MAX = 500
SQLITE_SHADOW_BUSY_MS = 250  # SQLite only: the shadow write gives up quickly instead of making a lane wait on its own write lock

# A lane's reason that IS the consequence / newsworthiness test (the part the model replaces). Every other refusal is a hard veto.
CONSEQUENCE_REASONS = ("why should anyone care?", "stop-scroll", "not newsworthy", "why would our audience care?")
AUDIENCE_REFUSALS = ("a routine military update", "diplomacy talk")  # newsworthy.audience()'s refusals: overridable by the model (Phase 0)


@dataclass
class SemanticVerdict:
    newsworthy: bool  # what the semantic gate would decide, vetoes applied
    major: bool
    tier: str  # major | significant | notable | noise
    event_type: str
    consequence: dict[str, Any]
    entities: list[str]
    claims: list[dict[str, Any]]  # {text, class, auto_post, attributed_to}; class from app.intel.classes (sources), never the model
    source: str  # "model:<name>" | "rules"
    reasons: list[str] = field(default_factory=list)
    hard_veto: list[str] = field(default_factory=list)
    consequence_ok: bool = False  # the model's consequence judgement before the vetoes (rules: the rules' decision)
    headline_class: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide(a: ItemAnalysis) -> dict[str, Any]:
    """The deterministic decision from an analysis (identical to ops/intel/bench.py decide(), so the benchmark scores this gate)."""
    newsworthy = a.materiality in ("major", "significant") and not a.is_speculative and not a.is_opinion_or_feature
    return {"tier": a.materiality, "newsworthy": newsworthy, "major": newsworthy and a.materiality == "major"}


def _settings(settings: Any = None) -> Any:
    if settings is None:
        from app.config.settings import get_settings

        settings = get_settings()
    return settings


# --- the rules side -----------------------------------------------------------------------------------------------------------------


def rules_view(rules_assessment: Any = None, rules_ok: bool | None = None, reason: str = "") -> dict[str, Any]:
    """The rules' decision as plain data: newsworthy.Assessment, importance.Decision, an already stored dict, or just (ok, reason)."""
    a = rules_assessment
    if isinstance(a, dict):
        ok, tier, why = a.get("ok"), str(a.get("tier") or ""), str(a.get("reason") or "")
    elif a is not None and hasattr(a, "strength"):  # newsworthy.Assessment
        ok, why = bool(a.ok), str(a.reason or "")
        tier = ("major" if a.strength == "major" else "significant") if a.ok else ("notable" if a.strength != "none" else "noise")
    elif a is not None and hasattr(a, "tier"):  # importance.Decision
        from app.newsdesk import importance

        ok, tier, why = a.rank >= importance.ORDER[importance.SIGNIFICANT], str(a.tier), str(a.reason or "")
    else:
        ok, tier, why = rules_ok, "", ""
    if rules_ok is not None:
        ok = bool(rules_ok)  # the lane's actual decision wins over what its assessment alone said
    ok = bool(ok)
    return {"ok": ok, "tier": tier or ("significant" if ok else "noise"), "reason": (reason or why or "")[:500]}


def _consequence_reason(reason: str) -> bool:
    r = (reason or "").strip().lower()
    for prefix in CONSEQUENCE_REASONS:
        if r.startswith(prefix):
            rest = r[len(prefix):].strip()
            return True
    return False


LEAD_SPLIT = re.compile(r"\s(?:as|while|after|amid|but)\s|;|\.\s|\s[-–—|]\s|,\s(?:and|as|while)\s", re.I)


def lead_clause(head: str) -> str:
    """The headline's first clause: what happened, before "as ...", "amid ...", "; ...", " - ..."."""
    return LEAD_SPLIT.split(head or "", maxsplit=1)[0]


def hard_vetoes(title: str, summary: str = "", rules: dict[str, Any] | None = None, vetoes: list[str] | tuple[str, ...] = ()) -> list[str]:
    """The existing rules that the semantic gate never overrides, in the lanes' own words."""
    from app.newsdesk import newsworthy

    head = (title or "").strip()
    out: list[str] = []
    if h := classes.hedge_words(lead_clause(head)):
        # Phase 0 benchmark: on the whole headline this vetoed "Anthropic hits $1.5 trillion valuation in record IPO as ECB warns AI
        # funding could ..." (a hedge in a trailing clause). Only the lead clause says whether the event itself happened.
        out.append(f"hedged, rumour or forecast wording ({h}): nothing has happened yet")
    weak = newsworthy.structural_weak(head)
    if not weak and (m := newsworthy.REACTION.search(head)):
        weak = f"a reaction or explainer ({m.group(0)})"
    if weak:
        out.append(f"not one story: {weak}")
    # newsworthy.audience()'s refusals ("a routine military update", "diplomacy talk") are a judgement of the consequence, which is the
    # part the model replaces; as vetoes they refused "Iran says three tankers hit mines in Hormuz" and "Poland issues air raid alerts as
    # Russia attacks Ukraine" in the Phase 0 benchmark. They are no longer vetoes (_consequence_reason treats them as overridable).
    r = rules or {}
    if not r.get("ok") and r.get("reason") and not _consequence_reason(str(r["reason"])) and not any(v in str(r["reason"]) for v in out):
        out.append(str(r["reason"]))  # a repeat, a cap, the liquidation pause, a hold: the lane's own veto
    out.extend(str(v) for v in vetoes or () if v)
    return list(dict.fromkeys(out))


def _claims(analysis: ItemAnalysis | None, title: str, summary: str, outlets: list[str], evidence: str) -> tuple[str, list[dict[str, Any]]]:
    official = evidence == "official"
    head = classes.cluster_class(title, summary, evidence=evidence, outlets=outlets, official=official)
    if analysis is None:
        return head, [{"text": title, "class": head, "auto_post": classes.AUTO_POST[head], "attributed_to": ""}]
    out = []
    for c in analysis.claims:
        k = classes.claim_class(c, evidence=evidence, outlets=outlets, official=official)
        out.append({"text": c.text, "class": k, "auto_post": classes.AUTO_POST[k], "attributed_to": c.attributed_to})
    return head, out


def verdict(analysis: ItemAnalysis | dict[str, Any] | None, title: str, summary: str = "", outlets: Any = (), evidence: str = "",
            rules_assessment: Any = None, *, rules_ok: bool | None = None, reason: str = "", vetoes: list[str] | tuple[str, ...] = (),
            model: str = "") -> SemanticVerdict:
    """What the semantic gate decides for one candidate. Deterministic; the model only answers "is there a stated consequence"."""
    if isinstance(analysis, dict):
        analysis = ItemAnalysis.model_validate(analysis)
    names = [str(o) for o in outlets or [] if str(o or "").strip()]
    rules = rules_view(rules_assessment, rules_ok, reason)
    veto = hard_vetoes(title, summary, rules, vetoes)
    head_class, claims = _claims(analysis, title, summary, names, evidence)
    if analysis is None:
        ok = bool(rules["ok"])
        return SemanticVerdict(newsworthy=ok, major=ok and rules["tier"] == "major", tier=rules["tier"], event_type="", consequence={},
                               entities=[], claims=claims, source="rules", reasons=[rules["reason"] or ("the rules pass it" if ok else "")],
                               hard_veto=veto, consequence_ok=ok, headline_class=head_class)
    d = decide(analysis)
    reasons = [analysis.reason] if analysis.reason else []
    consequence_ok = bool(d["newsworthy"])
    if analysis.is_speculative:
        reasons.append("the model reads it as speculative: nothing has happened yet")
    if analysis.is_opinion_or_feature:
        reasons.append("the model reads it as opinion, analysis or a feature")
    if consequence_ok and analysis.consequence.kind != "none" and not analysis.consequence.stated_in_text:
        # Recorded, not decisive. Phase 0 benchmark: models mark stated_in_text false when the event IS the consequence ("US strikes
        # Iranian missile site", "Iran attacks Kuwait"), so as a rule this dropped 2 of 18 major stories. Inferred claims are still
        # classed INFERENCE (never published) by app.intel.classes.
        reasons.append("the model says the consequence is its own inference, not stated in the text")
    if consequence_ok and analysis.consequence.kind == "none":
        consequence_ok = False
        reasons.append("no consequence kind stated")
    newsworthy = consequence_ok and not veto
    if consequence_ok and veto:
        reasons.append(f"vetoed by the rules: {veto[0]}")
    return SemanticVerdict(newsworthy=newsworthy, major=newsworthy and analysis.materiality == "major", tier=analysis.materiality,
                           event_type=analysis.event_type, consequence=analysis.consequence.model_dump(), entities=list(analysis.entities),
                           claims=claims, source=f"model:{model or 'unknown'}"[:80], reasons=reasons, hard_veto=veto,
                           consequence_ok=consequence_ok, headline_class=head_class)


# --- the shadow record ----------------------------------------------------------------------------------------------------------------


def job_key(lane: str, ref: str) -> str:
    return f"shadow:{lane}:{ref}:{PROMPT_VERSION}"[:120]


def _own_session(db: Session) -> tuple[Session, Any]:
    """A session of its own, on one connection of the lane's engine: the shadow commit never commits (or rolls back) the lane's
    transaction. SQLite: that connection waits at most SQLITE_SHADOW_BUSY_MS for a write lock (a lane holding one is never stalled)."""
    bind = db.get_bind()
    engine = getattr(bind, "engine", bind)
    conn = engine.connect()
    if engine.dialect.name == "sqlite":
        conn.exec_driver_sql(f"PRAGMA busy_timeout={SQLITE_SHADOW_BUSY_MS}")
        conn.commit()
    return Session(bind=conn, autoflush=False, expire_on_commit=False), conn


def _close(own: Session, conn: Any) -> None:
    try:
        own.close()
        conn.rollback()
        if conn.dialect.name == "sqlite":  # the connection goes back to the shared pool: restore the engine's own wait (app.db.session)
            conn.exec_driver_sql("PRAGMA busy_timeout=15000")
            conn.commit()
    finally:
        conn.close()


def _apply(row: Any, v: SemanticVerdict, rules_ok: bool, state: str, extra: dict[str, Any]) -> None:
    row.semantic_ok = bool(v.newsworthy)
    row.agree = bool(v.newsworthy) == bool(rules_ok)
    row.tier = (v.tier or "")[:12]
    row.source = "model" if v.source.startswith("model:") else "rules"  # column is short; the full "model:<name>" is in verdict
    row.verdict = {**extra, "state": state, **v.as_dict()}


def observe(db: Session, lane: str, ref: str, title: str, summary: str = "", outlets: Any = (), rules_ok: bool = False, *,
            evidence: str = "", reason: str = "", rules_assessment: Any = None, vetoes: list[str] | tuple[str, ...] = (),
            now: datetime | None = None, settings: Any = None) -> None:
    """Record one lane decision in the shadow table (and queue its analysis). Returns nothing; never raises; off by default."""
    try:
        s = _settings(settings)
        if not s.intel_shadow_enabled:
            return
        _observe(db, lane, ref, title, summary, outlets, rules_ok, evidence, reason, rules_assessment, vetoes, now or utcnow(), s)
    except Exception:  # noqa: BLE001 - shadow only: a failure here is logged and the lane carries on untouched
        logger.warning("intel shadow: observe failed for %s %s", lane, ref, exc_info=True)


def _observe(db: Session, lane: str, ref: str, title: str, summary: str, outlets: Any, rules_ok: bool, evidence: str, reason: str,
             rules_assessment: Any, vetoes: Any, now: datetime, s: Any) -> None:
    from app.models.intel_shadow import IntelShadow

    names = [str(o) for o in outlets or [] if str(o or "").strip()][:12]
    rules = rules_view(rules_assessment, rules_ok, reason)
    inputs = {"title": (title or "")[:300], "summary": (summary or "")[:900], "outlets": names, "evidence": evidence or "",
              "rules": rules, "vetoes": [str(v)[:300] for v in vetoes or () if v]}
    key = job_key(lane, ref)
    own, conn = _own_session(db)
    try:
        row = own.scalar(select(IntelShadow).where(IntelShadow.lane == lane, IntelShadow.ref == ref))
        if row is None:
            row = IntelShadow(lane=lane[:16], ref=ref[:64], title=inputs["title"], rules_ok=bool(rules["ok"]), semantic_ok=bool(rules["ok"]),
                              agree=True, tier=rules["tier"][:12], source="rules", seen=1, last_seen=now, verdict={})
            own.add(row)
            old: dict[str, Any] = {}
        else:
            row.seen = (row.seen or 0) + 1
            row.last_seen, row.title, row.rules_ok = now, inputs["title"], bool(rules["ok"])
            old = dict(row.verdict or {})
        extra = {"input": inputs, "key": key, "job_id": old.get("job_id") if old.get("key") == key else None}
        analysis, model = (old.get("analysis"), old.get("model", "")) if old.get("key") == key else (None, "")
        if analysis:  # already analysed: the inputs (vetoes, the rules' decision) may have changed, the verdict follows them
            v = verdict(analysis, inputs["title"], inputs["summary"], names, inputs["evidence"], rules, vetoes=inputs["vetoes"], model=model)
            _apply(row, v, rules["ok"], "done", {**extra, "analysis": analysis, "model": model})
        else:
            v = verdict(None, inputs["title"], inputs["summary"], names, inputs["evidence"], rules, vetoes=inputs["vetoes"])
            state = "rules"
            if s.intel_enabled:
                state = "pending"
                if extra["job_id"] is None:
                    extra["job_id"] = _enqueue(own, key, inputs, now, s)
            elif old.get("state") == "pending" and old.get("key") == key:
                state = "pending"  # a job queued while intel was on may still land
            _apply(row, v, rules["ok"], state, extra)
        own.commit()
    finally:
        _close(own, conn)


def _enqueue(own: Session, key: str, inputs: dict[str, Any], now: datetime, s: Any) -> int | None:
    """One analysis job per (lane, ref, prompt version), bulk: the PC answers when it can; never awaited."""
    from app.intel import router
    from app.models.intel import IntelJob

    existing = own.scalar(select(IntelJob.id).where(IntelJob.key == key, IntelJob.kind == "intel.analyze",
                                                    IntelJob.status.notin_(("failed", "expired"))).order_by(IntelJob.id.desc()).limit(1))
    if existing is not None:
        return int(existing)
    payload = {"title": inputs["title"], "summary": inputs["summary"], "outlets": inputs["outlets"]}
    job = router.enqueue(own, "intel.analyze", payload, "bulk", key=key, now=now, settings=s)
    return job.id if job is not None else None


def refresh(db: Session, now: datetime | None = None, settings: Any = None) -> dict[str, Any]:
    """Fill in the semantic side of pending shadow rows whose analysis job is done (called by intel.tick, and by intel.shadow when only
    the shadow is on). Rows with no job yet get one when intel is on. Never touches a lane's data."""
    from app.intel import router
    from app.models.intel import IntelJob
    from app.models.intel_shadow import IntelShadow

    s = _settings(settings)
    if not s.intel_shadow_enabled:
        return {"skipped": "intel shadow disabled"}
    now = now or utcnow()
    counts = {"model": 0, "rules": 0, "pending": 0, "queued": 0}
    rows = list(db.scalars(select(IntelShadow).where(IntelShadow.last_seen >= now - REFRESH_WINDOW)
                           .order_by(IntelShadow.last_seen.desc()).limit(REFRESH_MAX)))
    for row in rows:
        old = dict(row.verdict or {})
        if old.get("state") != "pending":
            continue
        inputs = old.get("input") or {}
        key = old.get("key") or job_key(row.lane, row.ref)
        job = db.get(IntelJob, old["job_id"]) if old.get("job_id") else None
        if job is None:
            job = db.scalar(select(IntelJob).where(IntelJob.key == key, IntelJob.kind == "intel.analyze").order_by(IntelJob.id.desc()).limit(1))
        if job is None:
            if s.intel_enabled and inputs.get("title"):
                got = _enqueue(db, key, inputs, now, s)
                row.verdict = {**old, "job_id": got}
                counts["queued"] += 1
            else:
                counts["pending"] += 1
            continue
        if job.status in ("queued", "leased"):
            counts["pending"] += 1
            continue
        args = (inputs.get("title") or row.title, inputs.get("summary") or "", inputs.get("outlets") or [], inputs.get("evidence") or "",
                inputs.get("rules") or {"ok": row.rules_ok})
        out = router.outcome(job)
        extra = {"input": inputs, "key": key, "job_id": job.id, "result_by": out.result_by}
        analysis = out.output if (job.status == "done" and out.result_by in ("pc", "cloud") and isinstance(out.output, dict)) else None
        if analysis is not None:
            try:
                model = str((job.result or {}).get("model") or out.result_by or "")
                v = verdict(analysis, *args, vetoes=inputs.get("vetoes") or [], model=model)
                _apply(row, v, row.rules_ok, "done", {**extra, "analysis": analysis, "model": model})
                counts["model"] += 1
                continue
            except Exception:  # noqa: BLE001 - a bad stored analysis falls back to the rules verdict
                logger.warning("intel shadow: bad analysis on job %s", job.id, exc_info=True)
        v = verdict(None, *args, vetoes=inputs.get("vetoes") or [])
        v.reasons.append(f"no analysis (job {job.status}, {out.result_by or 'no result'}): the rules verdict")
        _apply(row, v, row.rules_ok, "rules", extra)
        counts["rules"] += 1
    db.commit()
    return counts


def report(db: Session, since: datetime | None = None, *, limit: int = 100) -> dict[str, Any]:
    """The disagreement summary for Admin and the owner: how often the semantic gate and the rules agree, and every disagreement."""
    from app.models.intel_shadow import IntelShadow

    since = since or utcnow() - timedelta(days=1)
    rows = list(db.scalars(select(IntelShadow).where(IntelShadow.last_seen >= since).order_by(IntelShadow.last_seen.desc()).limit(5000)))
    counts: dict[str, Any] = {"total": len(rows), "agree": 0, "rules_only": 0, "semantic_only": 0, "pending": 0, "by_source": {},
                              "by_lane": {}}
    rules_only: list[dict[str, Any]] = []
    semantic_only: list[dict[str, Any]] = []
    for r in rows:
        v = r.verdict or {}
        state = v.get("state") or "rules"
        lane = counts["by_lane"].setdefault(r.lane, {"total": 0, "agree": 0, "rules_only": 0, "semantic_only": 0, "pending": 0})
        lane["total"] += 1
        counts["by_source"][r.source] = counts["by_source"].get(r.source, 0) + 1
        if state == "pending":
            counts["pending"] += 1
            lane["pending"] += 1
            continue
        if r.agree:
            counts["agree"] += 1
            lane["agree"] += 1
            continue
        item = {"lane": r.lane, "ref": r.ref, "title": r.title, "rules_ok": r.rules_ok, "semantic_ok": r.semantic_ok, "tier": r.tier,
                "source": v.get("source") or r.source, "event_type": v.get("event_type") or "", "consequence": v.get("consequence") or {},
                "rules_reason": ((v.get("input") or {}).get("rules") or {}).get("reason") or "", "semantic_reasons": v.get("reasons") or [],
                "hard_veto": v.get("hard_veto") or [], "claims": v.get("claims") or [], "seen": r.seen,
                "last_seen": r.last_seen.isoformat() if r.last_seen else None}
        if r.rules_ok:
            counts["rules_only"] += 1
            lane["rules_only"] += 1
            rules_only.append(item)
        else:
            counts["semantic_only"] += 1
            lane["semantic_only"] += 1
            semantic_only.append(item)
    return {"since": since.isoformat(), "prompt_version": PROMPT_VERSION, "counts": counts, "rules_only": rules_only[:limit],
            "semantic_only": semantic_only[:limit]}
