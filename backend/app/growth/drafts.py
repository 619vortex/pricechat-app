"""The Originals queue (G1): run the detectors, keep the best findings per format with variety, write each draft (template now, the
writer's wording later if it passes the gates), and the owner's actions: approve, edit, regenerate, reject.

NOTHING HERE PUBLISHES. Approving only marks a draft approved; the owner copies the text and downloads the image and posts by hand. No X or
Telegram client is imported by this module.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.growth import detectors, gate, writer
from app.growth.findings import FORMATS, Finding
from app.growth.memory import Memory
from app.models.market_memory import GrDraft

log = logging.getLogger(__name__)

STATUSES = ("pending", "approved", "rejected", "regenerating")
GENERATOR = "g3-1"  # the detector set + verification version: a new one drafts once on the day it is deployed (jobs.growth_originals)
# G3 (2026-11-27): every wording keeps the instrument and the history its figures are measured in (Finding.required_phrases). A pending draft
# of an older generator that nobody has touched is redrafted under the current rules and the old one is marked superseded (rejected, with
# the reason), so the queue never holds two versions of one finding.
SYSTEM = "system"
# G2 v2 changed who these detectors may name (major coins only) or excluded them: their older pending drafts are retired, not redrafted
UNIVERSE_CHANGED = frozenset({"leaderboard", "divergence", "breadth_count", "volume_spike"})
REPEAT_SKIP = timedelta(days=7)  # the same finding key isn't drafted twice in a week
SIMILAR_DAYS = timedelta(days=3)  # same detector + subject within 3 days: novelty halved
STORY_DAYS = timedelta(days=3)  # nd_storylines told about the subject in 3 days: novelty 0.7
ALT_CHART = {"rank_table": "heat_grid", "heat_grid": "rank_table", "rolling_line": "series_with_marker", "series_with_marker": "rolling_line",
             "rebased_lines": "spread_lines", "spread_lines": "rebased_lines", "ratio_line": "series_with_marker", "move_card": "series_with_marker"}


class DraftError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _settings(settings: Any = None) -> Any:
    if settings is None:
        from app.config.settings import get_settings

        settings = get_settings()
    return settings


# --- novelty and selection -------------------------------------------------------------------------------------------------------


def novelty(db: Session, findings: list[Finding], now: datetime) -> list[Finding]:
    """Drops findings already drafted this week; lowers novelty for a recent draft on the same detector + subject, or a News Desk storyline
    that told the subject in the last few days (nd_storylines: the "already told" memory)."""
    recent = [r for r in db.execute(select(GrDraft.finding_key, GrDraft.detector, GrDraft.finding, GrDraft.created_at, GrDraft.status,
                                            GrDraft.writer).where(GrDraft.created_at >= now - REPEAT_SKIP))
              if not _replaceable(r[4], r[5], r[2])]  # an untouched draft of an older generator doesn't block its own redraft
    keys = {r[0] for r in recent}
    similar = {(r[1], (r[2] or {}).get("subject")) for r in recent if _aware(r[3]) >= now - SIMILAR_DAYS}
    told: list[str] = []
    try:
        from app.models.newsdesk import NdStoryline

        told = [(t or "").lower() for (t,) in db.execute(select(NdStoryline.title).where(NdStoryline.told_at.is_not(None),
                                                                                         NdStoryline.last_at >= now - STORY_DAYS))]
    except Exception:  # noqa: BLE001 - story memory is a ranking hint, never a blocker
        db.rollback()
    out = []
    for f in findings:
        if f.key in keys:
            continue
        if (f.detector, f.subject) in similar:
            f.novelty *= 0.5
            f.notes.append("a draft on the same detector and subject in the last 3 days")
        subj = (f.words.get("asset") or f.subject).lower()
        # Bitcoin and Ether are in nearly every News Desk storyline, so "told recently" says nothing about a data angle on them (11-27: the
        # record 7-week Bitcoin losing streak lost its slot to smaller coins this way)
        if told and len(subj) > 2 and f.subject.upper() not in ("BTC", "ETH") and any(subj in t for t in told):
            f.novelty *= 0.7
            f.notes.append("the News Desk told a story about this subject in the last 3 days")
        out.append(f)
    return out


# Not drafted as Originals (owner, 2026-11-27: "avoid misleading annualized statistics"): funding is an hourly rate whose annualised
# figure reads as a yearly return to a general audience; open-interest build-ups are trader jargon. The detectors still run (Terminal).
EXCLUDED = frozenset({"funding_extremes", "funding_streak", "oi_buildup"})

PER_DETECTOR = 2  # G2: at most two drafts of one detector per run (a run of five "X is down N% in 30 days" posts isn't variety)


def select_top(findings: list[Finding], per_format: int, max_total: int, per_detector: int = PER_DETECTOR) -> list[Finding]:
    """Best score first, at most per_format per format, per_detector per detector and one per (format, subject); then round-robin across
    formats so every format the detectors found something for is represented before any format gets a second slot."""
    by_fmt: dict[str, list[Finding]] = {k: [] for k in FORMATS}
    seen: set[tuple[str, str]] = set()
    per_det: dict[str, int] = {}
    for f in sorted(findings, key=lambda f: -f.score):
        if (f.format, f.subject) in seen or len(by_fmt[f.format]) >= per_format or f.score <= 0 or per_det.get(f.detector, 0) >= per_detector:
            continue
        per_det[f.detector] = per_det.get(f.detector, 0) + 1
        seen.add((f.format, f.subject))
        by_fmt[f.format].append(f)
    if sum(len(v) for v in by_fmt.values()) < max_total:  # a quiet day: one more per detector rather than fewer drafts
        seen_det = {(f.detector, f.subject) for v in by_fmt.values() for f in v}
        for f in sorted(findings, key=lambda f: -f.score):
            if ((f.detector, f.subject) in seen_det or len(by_fmt[f.format]) >= per_format or f.score <= 0
                    or per_det.get(f.detector, 0) >= per_detector + 1 or f in by_fmt[f.format]):
                continue  # a quiet day: a second angle on the same subject from another detector may fill a slot
            per_det[f.detector] = per_det.get(f.detector, 0) + 1
            seen_det.add((f.detector, f.subject))
            by_fmt[f.format].append(f)
    out: list[Finding] = []
    for rank in range(per_format):
        for k in FORMATS:
            if rank < len(by_fmt[k]) and len(out) < max_total:
                out.append(by_fmt[k][rank])
    return out


def _aware(t: datetime) -> datetime:
    from datetime import timezone

    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


# --- drafts ----------------------------------------------------------------------------------------------------------------------


def _replaceable(status: str, writer_name: str, finding: dict[str, Any] | None) -> bool:
    """A draft the current generator may replace: still waiting for review, not edited by the owner, made by an older generator."""
    return status in ("pending", "regenerating") and writer_name != "owner" and (finding or {}).get("generator", "g1") != GENERATOR


def supersede(db: Session, made: list[GrDraft], now: datetime) -> dict[str, list[int]]:
    """After a run: older-generator drafts nobody has reviewed are marked rejected with the reason (the Rejected tab keeps them, Regenerate
    brings one back): the ones the run redrafted (same finding key, or the same detector and subject on newer data), the ones drafted before
    source verification existed, and the ones the current rules no longer draft (excluded detectors, the detectors limited to major coins in
    G2 v2). Never touches an approved, owner-edited or current-generator draft."""
    by_key = {d.finding_key: d.id for d in made}
    by_subject = {(d.detector, (d.finding or {}).get("subject")): d.id for d in made}
    out: dict[str, list[int]] = {"superseded": [], "retired": []}
    for d in db.scalars(select(GrDraft).where(GrDraft.status.in_(("pending", "regenerating")))):
        if not _replaceable(d.status, d.writer, d.finding) or d.id in by_key.values():
            continue
        same = by_key.get(d.finding_key) or by_subject.get((d.detector, (d.finding or {}).get("subject")))
        if same:
            why, bucket = (f"Superseded by draft #{same}: the same finding (or the same angle on newer data), redrafted under the current rules "
                           f"({GENERATOR})"), "superseded"
        elif not (d.finding or {}).get("verification"):
            why, bucket = "Retired: drafted before its data was re-read from the source (G2 verification); the current batch is verified", "retired"
        elif d.detector in EXCLUDED or d.detector in UNIVERSE_CHANGED:
            why, bucket = (f"Retired: the current Originals rules ({GENERATOR}) no longer draft this: major coins only, no annualised funding "
                           "figures"), "retired"
        else:
            continue
        d.status, d.decided_at, d.decided_by, d.reject_reason, d.writer_job_id = "rejected", now, SYSTEM, why[:500], None
        out[bucket].append(d.id)
    db.commit()
    return out


def finding_of(d: GrDraft) -> Finding:
    return Finding.from_dict(d.finding)


def _enqueue_writer(db: Session, d: GrDraft, f: Finding, priority: str, settings: Any, avoid: str = "") -> int | None:
    from app.intel import router as intel_router

    try:
        job = intel_router.enqueue(db, writer.KIND, writer.build(f, d.version, avoid=avoid), priority, key=f"growth:{d.id}:{d.version}",
                                   settings=settings)
    except ValueError as e:
        log.warning("growth: writer job refused: %s", e)
        return None
    return job.id if job is not None else None


def create(db: Session, f: Finding, *, settings: Any = None, use_writer: bool = True, verification: dict[str, Any] | None = None) -> GrDraft | None:
    text, variant = writer.template_text(f)
    if text is None:
        log.warning("growth: no template of %s passes the gate: %s", f.key, gate.problems(f.text(0), f))
        return None
    fd = f.to_dict() | {"template_variant": variant, "generator": GENERATOR, **({"verification": verification} if verification else {})}
    d = GrDraft(finding_key=f.key[:160], format=f.format, detector=f.detector, status="pending", text=text, original_text=text, writer="template",
                finding=fd, chart=f.chart, sources=_sources(f), calculation=f.calculation, score=f.score, as_of=f.as_of, version=1, image_cache={})
    db.add(d)
    db.commit()
    if use_writer:
        jid = _enqueue_writer(db, d, f, "bulk", _settings(settings))
        if jid:
            d.writer_job_id = jid
            db.commit()
    return d


def _sources(f: Finding) -> list[dict[str, Any]]:
    from app.growth import sources as src

    out = []
    for n in f.sources:
        s = src.get(n)
        out.append({"name": n, "label": s.label if s else n, "attribution": s.attribution if s else "", "terms_url": s.terms_url if s else "",
                    "commercial_ok": bool(s and s.commercial_ok)})
    return out


def generate(db: Session, *, settings: Any = None, now: datetime | None = None, mem: Memory | None = None, as_of: date | None = None,
             use_writer: bool = True, verifier: Any = "auto") -> dict[str, Any]:
    """One generation pass (the daily job and Admin's Generate now). G2: every picked finding's stored inputs are re-read from their source
    first (app.growth.verify); one that doesn't match, or can't be re-read, is not drafted and the next best takes its place.
    verifier: "auto" (growth_verify_sources decides), None (no check), or an object with check(finding) -> {"ok": bool, ...}."""
    s = _settings(settings)
    now = now or utcnow()
    mem = mem or Memory.from_db(db)
    found, errors = detectors.run_all(mem, as_of)
    publishable = [f for f in found if f.publishable and f.detector not in EXCLUDED]
    held = len(found) - len(publishable)
    fresh = novelty(db, publishable, now)
    limit = int(s.growth_max_drafts_per_run)
    ranked = select_top(fresh, int(s.growth_drafts_per_format), len(fresh))
    if verifier == "auto":
        from app.growth.verify import Verifier

        verifier = Verifier(mem, now=now) if bool(getattr(s, "growth_verify_sources", True)) else None
    made: list[int] = []
    made_rows: list[GrDraft] = []
    refused: list[dict[str, Any]] = []
    for f in ranked:
        if len(made) >= limit:
            break
        check = verifier.check(f) if verifier is not None else None
        if check is not None and not check.get("ok"):
            refused.append({"key": f.key, "problems": (check.get("problems") or [])[:3]})
            log.warning("growth: %s not drafted, its data didn't verify: %s", f.key, "; ".join((check.get("problems") or [])[:3]))
            continue
        d = create(db, f, settings=s, use_writer=use_writer, verification=check)
        if d is not None:
            made.append(d.id)
            made_rows.append(d)
    replaced = supersede(db, made_rows, now)
    return {"found": len(found), "held_unlicensed": held, **replaced, "after_novelty": len(fresh), "drafted": len(made), "draft_ids": made, "errors": errors,
            "not_verified": refused, "generator": GENERATOR, "as_of": (as_of or detectors.default_as_of(mem) or now.date()).isoformat()}


def generated_today(db: Session, now: datetime) -> bool:
    """A draft from the current GENERATOR was made today (UTC): the daily job doesn't run again. A new generator (a deploy) runs once."""
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return any((f or {}).get("generator") == GENERATOR for (f,) in db.execute(select(GrDraft.finding).where(GrDraft.created_at >= day0)))


def apply_writer_results(db: Session) -> dict[str, int]:
    """Drafts whose writer job finished: the model's wording replaces the template when it passes every gate; otherwise the template stays.
    A draft the owner already edited, approved or rejected keeps its text."""
    from app.models.intel import IntelJob

    counts = {"applied": 0, "refused": 0, "rules": 0, "waiting": 0}
    for d in db.scalars(select(GrDraft).where(GrDraft.writer_job_id.is_not(None))):
        job = db.get(IntelJob, d.writer_job_id)
        if job is None or job.status in ("failed", "expired"):
            _writer_done(d, None)
            counts["rules"] += 1
            continue
        if job.status != "done":
            counts["waiting"] += 1
            continue
        raw = writer.parse_output((job.result or {}).get("output")) if job.result_by in ("pc", "cloud") else None
        f = finding_of(d)
        text, probs = writer.substitute(f, raw) if raw else (None, ["rules"])
        if text and d.status in ("pending", "regenerating") and d.writer != "owner":
            d.text, d.writer = text, str(job.result_by)
            d.image_cache = {}
            counts["applied"] += 1
        elif raw:
            counts["refused"] += 1
            d.finding = {**d.finding, "writer_refusals": ((d.finding or {}).get("writer_refusals") or [])[-4:] + [probs[:3]]}
        else:
            counts["rules"] += 1
        _writer_done(d, text)
    db.commit()
    return counts


def _writer_done(d: GrDraft, text: str | None) -> None:
    if d.status == "regenerating" and not text:
        f = Finding.from_dict(d.finding)
        nxt = int((d.finding or {}).get("template_variant", 0)) + 1
        t, v = writer.template_text(f, nxt)
        if t:
            d.text, d.writer = t, "template"
            d.finding = {**d.finding, "template_variant": v}
    if d.status == "regenerating":
        d.status = "pending"
    d.writer_job_id = None


# --- owner actions ---------------------------------------------------------------------------------------------------------------


def _get(db: Session, draft_id: int) -> GrDraft:
    d = db.get(GrDraft, draft_id)
    if d is None:
        raise DraftError(404, "No such draft.")
    return d


def approve(db: Session, draft_id: int, who: str, now: datetime | None = None) -> GrDraft:
    """Marks the draft approved. Does NOT publish: the owner posts it by hand (copy text + download image)."""
    d = _get(db, draft_id)
    if d.status == "regenerating":
        raise DraftError(409, "A new wording is being written; wait for it or reload.")
    bad = gate.problems(d.text, finding_of(d), as_edited=d.writer == "owner")
    if bad:
        raise DraftError(422, "The text fails the figures gate: " + "; ".join(bad[:5]))
    d.status, d.decided_at, d.decided_by, d.reject_reason = "approved", now or utcnow(), who[:80], ""
    db.commit()
    return d


def edit(db: Session, draft_id: int, text: str, who: str) -> GrDraft:
    """The owner's own wording: the figures gate still applies (every figure must be the finding's)."""
    d = _get(db, draft_id)
    if d.status not in ("pending", "approved"):
        raise DraftError(409, f"A {d.status} draft can't be edited.")
    text = (text or "").strip()
    bad = gate.problems(text, finding_of(d), as_edited=True)
    if bad:
        raise DraftError(422, "; ".join(bad[:8]))
    d.text, d.writer = text, "owner"
    d.finding = {**d.finding, "edited_by": who[:80]}
    d.version += 1
    if d.status == "approved":
        d.status, d.decided_at = "pending", None  # an edit after approval needs a fresh approval
    db.commit()
    return d


def regenerate(db: Session, draft_id: int, *, chart_variant: bool = False, settings: Any = None) -> GrDraft:
    """New wording: the writer (PC / Claude) when the intel router is on, else the next template variant at once. Optionally the alternative
    chart for the same data."""
    d = _get(db, draft_id)
    if d.status not in ("pending", "rejected"):
        raise DraftError(409, f"A {d.status} draft can't be regenerated.")
    f = finding_of(d)
    if chart_variant:
        from app.growth.graphics import variant_kinds

        kinds = variant_kinds(d.chart)  # the detector's own kind first, then its variants: "Other chart" cycles through them
        cur = d.chart.get("kind", "")
        alt = kinds[(kinds.index(cur) + 1) % len(kinds)] if cur in kinds and len(kinds) > 1 else None
        if alt:
            base = kinds[0]
            d.chart = {k: v for k, v in d.chart.items() if k != "variant_of"} | {"kind": alt} | ({"variant_of": base} if alt != base else {})
            d.image_cache = {}
    d.version += 1
    d.status, d.decided_at, d.reject_reason = "pending", None, ""
    db.commit()
    jid = _enqueue_writer(db, d, f, "slot", _settings(settings), avoid=d.text)
    if jid:
        d.writer_job_id, d.status = jid, "regenerating"
    else:
        nxt = int((d.finding or {}).get("template_variant", 0)) + 1
        t, v = writer.template_text(f, nxt)
        if t:
            d.text, d.writer = t, "template"
            d.finding = {**d.finding, "template_variant": v}
    db.commit()
    return d


def reject(db: Session, draft_id: int, reason: str, who: str, now: datetime | None = None) -> GrDraft:
    d = _get(db, draft_id)
    d.status, d.decided_at, d.decided_by, d.reject_reason = "rejected", now or utcnow(), who[:80], (reason or "").strip()[:500]
    d.writer_job_id = None
    db.commit()
    return d


def view(d: GrDraft) -> dict[str, Any]:
    f = d.finding or {}
    return {"id": d.id, "finding_key": d.finding_key, "format": d.format, "detector": d.detector, "status": d.status, "text": d.text,
            "chars": len(d.text), "original_text": d.original_text, "writer": d.writer, "score": round(d.score, 2),
            "as_of": d.as_of.isoformat() if d.as_of else None, "headline": f.get("headline", ""), "calculation": d.calculation,
            "sources": d.sources, "attribution": f.get("attribution", []), "numbers": f.get("numbers", {}), "window": f.get("window", ""),
            "rarity": f.get("rarity"), "novelty": f.get("novelty"), "notes": f.get("notes", []), "version": d.version,
            "chart": {k: v for k, v in (d.chart or {}).items() if k != "variants"},  # the variants stay server side (Other chart)
            "chart_kinds": _kinds(d.chart), "render_info": d.image_cache or {},  # render meta per format: template, version (learner)
            "created_at": d.created_at.isoformat() if d.created_at else None, "decided_at": d.decided_at.isoformat() if d.decided_at else None,
            "decided_by": d.decided_by, "reject_reason": d.reject_reason, "writer_pending": d.writer_job_id is not None,
            "verification": f.get("verification"), "generator": f.get("generator", "g1"), "subject": f.get("subject", ""),
            "posted": f.get("posted"), "coverage": Finding.from_dict(f).required_phrases() if f else [],
            "gate_problems": gate.problems(d.text, Finding.from_dict(f), as_edited=d.writer == "owner") if f else []}


def _kinds(chart: dict[str, Any] | None) -> list[str]:
    from app.growth.graphics import variant_kinds

    return variant_kinds(chart) if chart and chart.get("kind") else []
