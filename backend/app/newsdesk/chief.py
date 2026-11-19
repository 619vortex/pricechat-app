"""The AI Editor-in-Chief (owner, 2026-11-19: "Build an AI Editor-in-Chief that reviews shortlisted stories for novelty, surprise, real
consequences, audience interest, credibility and genuine viral potential. Require a specific explanation of why someone would care. Run AI
editorial judgment in SHADOW MODE first. Compare it with the current deterministic gate on fresh production candidates. Do not enable paid
AI calls without my approval.")

SHADOW, ALWAYS (in this version): it never publishes, holds or changes anything. The deterministic gate (app/newsdesk/newsworthy) decides.

OFF until the owner approves: with NEWSDESK_CHIEF_ENABLED unset (the default) no request is ever built for sending, and no key is read.
Every News Desk candidate is still SHORTLISTED here (nd_validation kind "chief"): the exact packet the editor would read is stored with
its size and the cost it would have, so the cost estimate is made from real candidates, and the stored packets can be replayed on the day
the owner approves to evaluate the editor on real past candidates first.

When enabled: its own key (newsdesk_chief_api_key, never the writers' or the image check's), its own model, a hard daily spend cap
counted from the API's own usage figures, at most MAX_PER_TICK reviews per desk run, only candidates seen in the last FRESH hours.

What it may NOT do, whatever the model says (check()):
- a reason that could be about any story ("this is significant", "markets may react") is no reason: the verdict counts as invalid;
- a figure in the reason that is not in the story's own text is flagged as possibly invented;
- it cannot raise credibility above the evidence: "publish" on a story the desk has not verified is recorded as blocked.

The editor is given the story, its sources and evidence level, and what Price Chat already told about it (story memory), but NOT the
gate's verdict, so the comparison is between two independent judgments.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.newsdesk import NdValidation

logger = logging.getLogger(__name__)

KIND = "chief"
MAX_PER_DAY = 80  # shortlisted candidates per day (the desk sees ~25-50)
MAX_PER_TICK = 4
FRESH = timedelta(hours=6)  # a candidate older than this is not reviewed when the editor is on (it is history by then)
OUTPUT_TOKENS_EST = 700  # the verdict (~250) plus low-effort thinking: an estimate until real usage figures exist
CHARS_PER_TOKEN = 3.6
# $ per million tokens (input, output): Anthropic list prices, 2026-11 (platform.claude.com pricing)
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}
VERIFIED = {"official", "two outlets", "price data", "exchange data"}
SCORES = ("novelty", "surprise", "consequence", "audience_interest", "credibility", "viral_potential")

SYSTEM = """You are the editor-in-chief of Price Chat, a news channel on Telegram and X read by crypto and market traders and by curious
general readers. The channel posts only a handful of stories a day. Your job is to pick the ones a sharp human editor would pick: the most
fascinating and consequential stories of the day, not the most frequent ones.

Judge ONE story. Score each 0-10:
- novelty: is this genuinely new, or a rerun of something already told (see WHAT WE ALREADY TOLD)? A routine update is low.
- surprise: would a well-informed reader be surprised? Expected outcomes, scheduled events and repetitive diplomacy are low.
- consequence: real, stated consequences for money, markets, lives or power. Not hypothetical ones.
- audience_interest: would our readers stop scrolling for it?
- credibility: how well the GIVEN sources establish it. You cannot score above the evidence: one unconfirmed outlet is at most 4.
- viral_potential: would people share it for its substance (not because it is misleading or sensational)?

verdict: "publish" (it deserves one of today's few posts), "hold" (worth watching, not yet), or "skip".
why_care: ONE sentence saying specifically why our reader would care: name who or what, and the concrete consequence or surprise. A
reason that would fit any story ("this is significant", "markets may react", "investors are watching") is not acceptable.
risks: anything that makes publishing it unwise (unverified, hedged source wording, exaggeration risk, a repeat), or an empty list.
best_angle: the one fact that makes it interesting, in at most 15 words, using only the given facts.

Never invent facts, figures or consequences. Rumours are never facts. Do not reward sensationalism."""

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "why_care", "scores", "risks", "best_angle"],
    "properties": {
        "verdict": {"type": "string", "enum": ["publish", "hold", "skip"]},
        "why_care": {"type": "string"},
        "scores": {"type": "object", "additionalProperties": False, "required": list(SCORES),
                   "properties": {k: {"type": "integer"} for k in SCORES}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "best_angle": {"type": "string"},
    },
}

GENERIC = re.compile(r"\b(this is (?:significant|important|big|major) (?:news|development|story)|significant development|major development|"
                     r"markets? (?:may|could|might) react|investors (?:are|will be) watching|(?:readers|people|audience) (?:will|would) (?:be )?(?:interested|care)|"
                     r"could have (?:major |significant |wide[- ]ranging )?(?:implications|consequences|impact)|important for (?:the )?(?:market|investors|readers))\b", re.I)
STOP = {"this", "that", "these", "those", "the", "its", "our", "readers", "traders", "investors", "people", "anyone", "everyone"}
LABELS = {"headline", "summary", "evidence", "first", "seen", "minutes", "ago", "sources", "source", "from", "the", "outlets", "would", "write"}
FIGURE = re.compile(r"\$?\d[\d,.]*\s*(?:%|bn|billion|million|m\b|k\b|trillion)?", re.I)


# ------------------------------------------------------------------------------------------------------------------ configuration
def config(settings: Any = None) -> tuple[str, str, str]:
    """(key, model, why-not). Never reads a key unless the editor is switched on."""
    from app.security import credentials

    if settings is None:
        from app.config.settings import get_settings

        settings = get_settings()
    if not getattr(settings, "newsdesk_chief_enabled", False):
        return "", "", "off: NEWSDESK_CHIEF_ENABLED is not set (no paid call without the owner's approval)"
    key = credentials.resolve("newsdesk_chief_api_key", settings) or ""
    model = str(getattr(settings, "newsdesk_chief_model", "") or "")
    if not key or not model:
        return "", "", "on, but there is no newsdesk_chief_api_key or model"
    return key, model, ""


def price(model: str, tokens_in: float, tokens_out: float) -> float:
    pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])
    return tokens_in / 1e6 * pin + tokens_out / 1e6 * pout


# ------------------------------------------------------------------------------------------------------------------ the packet
def packet(ev: Any, memory: dict[str, Any] | None, recent: list[str], now: datetime) -> str:
    """What the editor reads about one candidate: the story, its sources, the evidence, what we already told. Not the gate's verdict."""
    srcs = [s for s in (getattr(ev, "sources", None) or []) if isinstance(s, dict)]
    first = getattr(ev, "first_seen", None) or getattr(ev, "created_at", None)
    first = first if first is None or first.tzinfo else first.replace(tzinfo=timezone.utc)
    age = int((now - first).total_seconds() // 60) if first else None
    lines = [f"HEADLINE: {getattr(ev, 'title', '')}"]
    if getattr(ev, "headline", "") and ev.headline != ev.title:
        lines.append(f"AS WE WOULD WRITE IT: {ev.headline}")
    if (getattr(ev, "summary", "") or "").strip():
        lines.append(f"SUMMARY (from the outlets): {ev.summary.strip()[:700]}")
    lines.append(f"EVIDENCE: {getattr(ev, 'evidence', '') or 'unknown'} ({len(srcs)} source{'s' if len(srcs) != 1 else ''})")
    for s in srcs[:6]:
        lines.append(f"- {s.get('name') or s.get('outlet') or '?'}: {str(s.get('title') or '')[:160]}".rstrip(": "))
    if age is not None:
        lines.append(f"FIRST SEEN: {age} minutes ago")
    m = memory or {}
    if m.get("verdict") and m["verdict"] != "new story":
        lines.append(f"WHAT WE ALREADY TOLD: \"{m.get('first_told')}\" {m.get('told_hours_ago')} h ago, {m.get('times_told')} post(s) on "
                     f"{', '.join(m.get('told_on') or [])}" + (f"; new since then: {m['new']}" if m.get("new") else "; nothing substantially new found by the rules"))
    else:
        lines.append("WHAT WE ALREADY TOLD: nothing about this story.")
    if recent:
        lines.append("OUR LAST POSTS (for variety):\n" + "\n".join(f"- {t[:120]}" for t in recent[:10]))
    return "\n".join(lines)


def _recent(db: Session, now: datetime) -> list[str]:
    try:
        from app.newsdesk import newsworthy

        return [h.title for h in newsworthy.told(db, now, hours=24)][:10]
    except Exception:  # noqa: BLE001
        return []


def _today(db: Session, now: datetime) -> list[NdValidation]:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return list(db.scalars(select(NdValidation).where(NdValidation.kind == KIND, NdValidation.created_at >= day)))


def shortlist(db: Session, ev: Any, gate: dict[str, Any], memory: dict[str, Any] | None = None, now: datetime | None = None, settings: Any = None) -> None:
    """Every new News Desk candidate: store what the editor would read and what it would cost. Never calls anything. Never raises."""
    try:
        now = now or utcnow()
        ref = f"event:{ev.id}"
        if ev.id is None or db.scalar(select(NdValidation.id).where(NdValidation.kind == KIND, NdValidation.ref == ref)) is not None:
            return
        if len(_today(db, now)) >= MAX_PER_DAY:
            return
        text = packet(ev, memory, _recent(db, now), now)
        tokens_in = int((len(SYSTEM) + len(text) + 600) / CHARS_PER_TOKEN)  # + the schema
        model = str(getattr(settings, "newsdesk_chief_model", "") or "claude-opus-5-5") if settings is not None else "claude-opus-5-5"
        on = bool(settings is not None and getattr(settings, "newsdesk_chief_enabled", False))
        data = {"status": "queued" if on else "off", "event": ev.id, "title": getattr(ev, "title", ""), "packet": text, "tokens_in_est": tokens_in,
                "cost_est": {m: round(price(m, tokens_in, OUTPUT_TOKENS_EST), 5) for m in PRICES}, "model": model,
                "gate": {k: gate.get(k) for k in ("decision", "interest", "why", "evidence", "outlets")}, "seen_at": now.isoformat()}
        with db.begin_nested():
            db.add(NdValidation(kind=KIND, ref=ref, data=data))
    except Exception as e:  # noqa: BLE001
        logger.info("ai editor: #%s not shortlisted (%s)", getattr(ev, "id", "?"), type(e).__name__)


# ------------------------------------------------------------------------------------------------------------------ the verdict
def check(out: dict[str, Any], source_text: str, evidence: str) -> dict[str, Any]:
    """The editor's verdict as recorded: invalid when the reason is generic, flagged when it cites a figure the story doesn't contain,
    blocked when it would publish a story the desk has not verified."""
    why = str(out.get("why_care") or "").strip()
    verdict = str(out.get("verdict") or "")
    problems = []
    if len(why) < 40 or GENERIC.search(why):
        problems.append("generic reason: it would fit any story")
    story = re.sub(r"(?m)^(?:OUR LAST POSTS|WHAT WE ALREADY TOLD).*(?:\n- .*)*", "", source_text)  # the story itself, not our own history
    words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'-]{2,}|\d[\d,.]*\d|\d", story)} - LABELS
    named = {w.lower().strip(".,") for w in re.findall(r"(?<=[\s(\"'])[A-Z][A-Za-z'-]{2,}|\d[\d,.]*\d|\d", f" {why}")} - STOP
    if why and not (named & words):
        problems.append("the reason names nothing from the story")
    src_figs = {re.sub(r"[^\d.]", "", f).strip(".") for f in FIGURE.findall(story)}
    for f in FIGURE.findall(why):
        digits = re.sub(r"[^\d.]", "", f).strip(".")
        if digits and digits not in src_figs:
            problems.append(f"figure not in the sources: {f.strip()}")
            break
    final = verdict
    if verdict == "publish" and evidence not in VERIFIED:
        final, problems = "skip", [*problems, f"would publish an unverified story (evidence: {evidence or 'unknown'}): blocked"]
    if any(p.startswith(("generic reason", "the reason names nothing")) for p in problems):
        final = "invalid"
    return {"verdict": final, "model_verdict": verdict, "why_care": why[:400], "scores": out.get("scores") or {},
            "risks": [str(r)[:160] for r in out.get("risks") or []][:5], "best_angle": str(out.get("best_angle") or "")[:160], "problems": problems}


async def review(key: str, model: str, text: str, *, effort: str = "low", transport: Any = None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """One paid request (only ever reached through tick() with the editor switched on). (verdict JSON or None, usage)."""
    import httpx

    from app.newsdesk.writer import ANTHROPIC_URL

    body = {"model": model, "max_tokens": 4000, "system": SYSTEM, "messages": [{"role": "user", "content": text}],
            "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}}}
    try:
        async with httpx.AsyncClient(timeout=90, transport=transport) as client:
            r = await client.post(ANTHROPIC_URL, headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json=body)
        if r.status_code != 200:
            return None, {"error": f"HTTP {r.status_code}"}
        data = r.json()
        usage = data.get("usage") or {}
        if data.get("stop_reason") == "refusal":
            return None, {**usage, "error": "refusal"}
        raw = "".join(b.get("text", "") for b in data.get("content") or [] if isinstance(b, dict) and b.get("type") == "text")
        out = json.loads(raw)
        return (out if isinstance(out, dict) else None), usage
    except (httpx.HTTPError, ValueError) as e:
        return None, {"error": type(e).__name__}


def spent_today(db: Session, now: datetime) -> float:
    return round(sum(float((r.data or {}).get("cost_usd") or 0) for r in _today(db, now)), 4)


async def tick(db: Session, now: datetime | None = None, *, settings: Any = None, transport: Any = None) -> dict[str, Any]:
    """After each desk run. Does nothing at all unless the owner switched the editor on. Never raises."""
    now = now or utcnow()
    out: dict[str, Any] = {"reviewed": 0}
    try:
        key, model, why_not = config(settings)
        if not key:
            out["off"] = why_not
            return out
        cap = float(getattr(settings, "newsdesk_chief_daily_usd", 1.0) or 0)
        effort = str(getattr(settings, "newsdesk_chief_effort", "low") or "low")
        rows = list(db.scalars(select(NdValidation).where(NdValidation.kind == KIND, NdValidation.created_at >= now - FRESH)
                               .order_by(NdValidation.id.desc()).limit(40)))
        for row in [r for r in rows if (r.data or {}).get("status") in ("queued", "off")][:MAX_PER_TICK]:
            d = dict(row.data or {})
            est = price(model, d.get("tokens_in_est") or 1500, OUTPUT_TOKENS_EST)
            if spent_today(db, now) + est > cap:
                out["capped"] = f"daily cap ${cap:.2f} reached"
                break
            verdict, usage = await review(key, model, str(d.get("packet") or ""), effort=effort, transport=transport)
            cost = price(model, float(usage.get("input_tokens") or 0) + float(usage.get("cache_read_input_tokens") or 0) * 0.1,
                         float(usage.get("output_tokens") or 0)) if "input_tokens" in usage else 0.0
            d.update({"status": "reviewed" if verdict else "failed", "model": model, "usage": usage, "cost_usd": round(cost, 6), "reviewed_at": now.isoformat()})
            if verdict:
                d.update(check(verdict, str(d.get("packet") or ""), str((d.get("gate") or {}).get("evidence") or "")))
            row.data = d
            db.commit()
            out["reviewed"] += 1
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.warning("ai editor: tick failed (%s)", type(e).__name__)
    return out


# ------------------------------------------------------------------------------------------------------------------ the comparison
def _positive(labels: set[str]) -> bool | None:
    if labels & {"great", "missed"}:
        return True
    if "boring" in labels:
        return False
    return None


def evaluation(db: Session, now: datetime | None = None, settings: Any = None) -> dict[str, Any]:
    """The shadow comparison (gate vs editor, both against the owner's feedback where there is any) and the cost estimate from real
    shortlisted candidates."""
    from app.newsdesk import feedback

    now = now or utcnow()
    rows = list(db.scalars(select(NdValidation).where(NdValidation.kind == KIND).order_by(NdValidation.id.desc()).limit(500)))
    _, _, why_not = config(settings)
    labels = feedback.labels(db)
    reviewed = [r for r in rows if (r.data or {}).get("status") == "reviewed"]
    matrix: dict[str, int] = {}
    labelled = {"gate_right": 0, "editor_right": 0, "n": 0}
    disagreements = []
    for r in reviewed:
        d = r.data or {}
        gate = "publish" if (d.get("gate") or {}).get("decision") == "PUBLISH" else "skip"
        ed = "publish" if d.get("verdict") == "publish" else "skip"
        matrix[f"gate {gate} / editor {ed}"] = matrix.get(f"gate {gate} / editor {ed}", 0) + 1
        truth = _positive(labels.get(r.ref, set()))
        if truth is not None:
            labelled["n"] += 1
            labelled["gate_right"] += (gate == "publish") == truth
            labelled["editor_right"] += (ed == "publish") == truth
        if gate != ed and len(disagreements) < 15:
            disagreements.append({"event": d.get("event"), "title": d.get("title"), "gate": gate, "editor": d.get("verdict"), "why_care": d.get("why_care"),
                                  "gate_why": (d.get("gate") or {}).get("why"), "owner": sorted(labels.get(r.ref, set()))})
    # the cost, from real shortlisted candidates
    week = [r for r in rows if r.created_at and (r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=timezone.utc)) >= now - timedelta(days=7)]
    span_days = 7.0
    if week:
        oldest = min(r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=timezone.utc) for r in week)
        span_days = max(1.0, (now - oldest).total_seconds() / 86400)
    per_day = round(len(week) / span_days, 1) if week else 0.0
    mean_in = round(sum(int((r.data or {}).get("tokens_in_est") or 0) for r in week) / len(week)) if week else 0
    projection = {m: {"per_review": round(price(m, mean_in, OUTPUT_TOKENS_EST), 4), "per_day": round(per_day * price(m, mean_in, OUTPUT_TOKENS_EST), 3),
                      "per_month": round(30 * per_day * price(m, mean_in, OUTPUT_TOKENS_EST), 2)} for m in PRICES} if mean_in else {}
    invalid = sum(1 for r in reviewed if (r.data or {}).get("verdict") == "invalid")
    blocked = sum(1 for r in reviewed if any("blocked" in p for p in (r.data or {}).get("problems") or []))
    return {
        "enabled": not why_not, "status": why_not or "on (shadow)", "shortlisted": len(rows),
        "queued_off": sum(1 for r in rows if (r.data or {}).get("status") == "off"), "reviewed": len(reviewed),
        "spent_today": spent_today(db, now), "daily_cap": float(getattr(settings, "newsdesk_chief_daily_usd", 1.0) or 0) if settings is not None else 1.0,
        "model": str(getattr(settings, "newsdesk_chief_model", "claude-opus-5-5")) if settings is not None else "claude-opus-5-5",
        "cost": {"candidates_per_day": per_day, "mean_input_tokens": mean_in, "output_tokens_assumed": OUTPUT_TOKENS_EST, "by_model": projection},
        "agreement": matrix, "labelled": labelled, "invalid_reasons": invalid, "blocked_unverified": blocked, "disagreements": disagreements,
        "recent": [{"event": (r.data or {}).get("event"), "title": (r.data or {}).get("title"), "status": (r.data or {}).get("status"),
                    "verdict": (r.data or {}).get("verdict"), "why_care": (r.data or {}).get("why_care"),
                    "gate": ((r.data or {}).get("gate") or {}).get("decision")} for r in rows[:10]],
    }


def count(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(NdValidation).where(NdValidation.kind == KIND)) or 0)
