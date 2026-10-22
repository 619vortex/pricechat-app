"""Supervised social growth pilot (Telegram + X): status, opportunity feed, review sessions, learning, analytics.

Nothing here sends anything anywhere: X actions are proposed into the approval queue, where the
human decides and the SocialActionGuard re-checks. Telegram stays read-only.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import Field, StringConstraints
from sqlalchemy import select

from app.api.deps import DbSession, Runtime
from app.api.schemas import StrictInput
from app.api.social_schemas import ApprovalOut
from app.community.tuning import CommunityTuning
from app.content.drafts import generate_drafts
from app.learning.operator import operator_signals, record_decision, set_preference
from app.models.community import ContentOpportunity
from app.models.enums import ApprovalStatus, DraftType, FeedbackDecision, OpportunityStatus, Platform, SocialAction, SocialOpportunityStatus
from app.models.social import SocialPost
from app.models.social_intel import ApprovalRequest, DiscoveryIgnore, SocialOpportunity, SocialProfile
from app.services import usage
from app.services.agent_service import build_retriever, get_agent_config
from app.social import growth
from app.social.approvals import MAX_TEXT, propose
from app.social.guard import AUTOMATIC_LOCK_REASON, ActionRequest

router = APIRouter(tags=["growth"])


# --- status ---------------------------------------------------------------------------------------------


@router.get("/growth/status")
async def growth_status(db: DbSession, runtime: Runtime) -> dict[str, object]:
    """PRICE CHAT SOCIAL GROWTH PILOT. READY only when the capability was actually tested."""
    from app.connectors.state import get_connector_state
    from app.models.telegram import TelegramMessage
    from app.services.pilot import pilot_status
    from app.services.safety import get_or_create_policy

    p = await pilot_status(db, runtime)
    checks = {c["key"]: c for c in p["checks"]}
    xs = get_connector_state(db, Platform.X)
    caps = {k: (v or {}).get("status") for k, v in (xs.capabilities or {}).items()}
    policy = get_or_create_policy(db, get_agent_config(db).id)
    processed = db.scalar(select(TelegramMessage.id).where(TelegramMessage.processed_at.is_not(None)).limit(1))

    def row(key: str, label: str, st: str, detail: str, next_step: str | None = None) -> dict[str, object]:
        return {"key": key, "label": label, "status": st, "detail": detail, "next_step": next_step}

    x = checks["x"]
    user_ok = caps.get("own_account") == "available"
    write_ok = user_ok and all(caps.get(f"write_{a}") in ("available", "unknown") for a in ("post", "reply"))
    rows = [
        row("x_read", "X read", "ready" if x["status"] == "ready" else "needs_setup", x["detail"], x.get("next_step")),
        row("x_user_auth", "X user auth", "ready" if user_ok else "needs_setup",
            "Your account is connected (user context)" if user_ok else "Needs the Access Token and Secret (Read and write)",
            None if user_ok else "X / Twitter › Connection: add the Access Token and Secret, then Test connection"),
        row("telegram", "Telegram", "ready" if checks["telegram"]["status"] == "ready" else checks["telegram"]["status"],
            checks["telegram"]["detail"], checks["telegram"].get("next_step")),
        row("community_data", "Community data", "ready" if processed else "waiting",
            "Telegram messages are being understood" if processed else "No Telegram messages processed yet",
            None if processed else "Authorise the Price Chat community; processing starts automatically"),
        row("ai", "AI", checks["ai"]["status"], checks["ai"]["detail"], checks["ai"].get("next_step")),
        row("opportunities", "Opportunities", checks["opportunities"]["status"], checks["opportunities"]["detail"], checks["opportunities"].get("next_step")),
        row("approvals", "Approvals", "ready", "Approval queue and safety guard active; every X action needs you"),
        row("x_execution", "X execution",
            "needs_setup" if not write_ok else ("paused" if (policy.kill_switch or not policy.outbound_enabled) else ("dry_run" if policy.dry_run else "ready")),
            "Needs X user auth with write permission" if not write_ok else
            ("Social actions are paused" if (policy.kill_switch or not policy.outbound_enabled) else
             ("Dry run: approved actions are checked but not sent" if policy.dry_run else "Approved actions are sent to X after the safety check")),
            None if write_ok else "X / Twitter › Connection"),
        row("automatic", "Automatic mode", "locked", AUTOMATIC_LOCK_REASON),
    ]
    return {
        "rows": rows, "mode": "supervised", "social_actions_active": policy.outbound_enabled and not policy.kill_switch,
        "dry_run": policy.dry_run, "automatic": "locked", "today": growth.today_stats(db),
    }


# --- feed & review sessions ------------------------------------------------------------------------------


@router.get("/growth/feed")
def feed(db: DbSession, include_filtered: bool = True, action: str | None = None) -> list[dict[str, object]]:
    items = growth.opportunity_feed(db, include_filtered=include_filtered)
    if action:  # e.g. action=like: only items where the AI suggested that action
        items = [i for i in items if action.lower() in {a.lower() for a in i.suggested_actions}]
    return [i.out() for i in items]


class SessionIn(StrictInput):
    size: int = Field(8, ge=5, le=10)


@router.post("/growth/review-session")
def start_review_session(payload: SessionIn, db: DbSession) -> dict[str, object]:
    """START REVIEW SESSION: the strongest 5–10 opportunities that passed the quality filter, one at a time."""
    items = growth.review_session(db, payload.size)
    filtered = sum(1 for i in growth.opportunity_feed(db, include_filtered=True, limit=500) if not i.passed)
    return {"items": [i.out() for i in items], "filtered_out": filtered}


class PrepareIn(StrictInput):
    direction: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


@router.post("/growth/prepare/{key}", response_model=ApprovalOut)
async def prepare(key: str, payload: PrepareIn, db: DbSession, runtime: Runtime) -> ApprovalOut:
    """Make sure the opportunity has a proposed X action in the queue (drafting it if needed)."""
    kind, _, raw = key.partition(":")
    if kind not in ("x", "telegram") or not raw.isdigit():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown opportunity key")
    oid = int(raw)
    agent_id = get_agent_config(db).id
    item = next((i for i in growth.opportunity_feed(db, limit=500) if i.key == key), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunity not found (or no longer open)")
    if item.pending_approval_id and not payload.direction:
        a = db.get(ApprovalRequest, item.pending_approval_id)
        if a is not None:
            return ApprovalOut.model_validate(a)
    high = item.label == "high"
    origin = "telegram+x" if item.source == "Telegram + X" else item.kind
    if kind == "x":
        return await _prepare_x(db, runtime, agent_id, oid, item, high, origin, payload.direction)
    return await _prepare_telegram(db, runtime, agent_id, oid, item, high, origin)


async def _prepare_x(db, runtime, agent_id, oid, item, high, origin, direction):  # type: ignore[no-untyped-def]
    from app.social.x_replies import draft_reply

    o = db.get(SocialOpportunity, oid)
    post = db.get(SocialPost, o.post_id) if o else None
    if o is None or post is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunity not found")
    standalone = o.suggested_action.value in ("standalone_post", "quote_candidate")
    async with runtime.gate.interactive():
        drafted = await draft_reply(db, runtime.router(db, background=False), agent_id, o, standalone=standalone,
                                    direction=direction, variants=high)
    if not drafted.worth_it:
        raise HTTPException(status.HTTP_409_CONFLICT, f"The AI found nothing worthwhile to add: {drafted.why or 'no reason given'}")
    author = db.get(SocialProfile, post.author_profile_id) if post.author_profile_id else None
    if standalone:
        req = ActionRequest(Platform.X, SocialAction.POST, None, None, drafted.text[:MAX_TEXT])
    else:
        req = ActionRequest(Platform.X, SocialAction.REPLY, post.external_id, author.username if author else post.author_handle, drafted.text[:MAX_TEXT])
    extra = {"original_post": {"id": post.external_id, "text": post.text, "url": post.url, "author": author.username if author else post.author_handle,
                               "posted_at": post.posted_at.isoformat() if post.posted_at else None},
             "topic": o.topic, "reason": drafted.why or o.why, "model": drafted.model, "confidence": drafted.confidence,
             "context": drafted.context, "warnings": drafted.warnings, "variants": drafted.variants, "origin": origin,
             "priority": item.label, "signals": item.signals}
    if standalone and o.suggested_action.value == "quote_candidate":
        extra["quote_post_id"] = post.external_id
    a = propose(db, agent_id, req, "social_opportunity", o.id, extra, settings=runtime.settings, actor="ai")
    o.status = SocialOpportunityStatus.REVIEWING
    db.commit()
    return ApprovalOut.model_validate(a)


async def _prepare_telegram(db, runtime, agent_id, oid, item, high, origin):  # type: ignore[no-untyped-def]
    o = db.get(ContentOpportunity, oid)
    if o is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunity not found")
    if o.status == OpportunityStatus.DETECTED:
        o.status = OpportunityStatus.REVIEWING
        db.commit()
    async with runtime.gate.interactive():
        drafts = await generate_drafts(db, runtime.router(db, background=False), agent_id, o, DraftType.X_POST,
                                       build_retriever(db, runtime, agent_id), runtime.embedder, CommunityTuning.from_settings(runtime.settings),
                                       variants=3 if high else 1, actor="ai")
    if not drafts:
        raise HTTPException(status.HTTP_409_CONFLICT, "No draft could be written")
    from app.social.x_replies import check_text

    variants = []
    evidence = " ".join([o.title, o.why, o.angle, *[str(x) for x in item.evidence]])
    for d in drafts:
        variants.append({"style": d.variant_label or "draft", "text": d.body.strip(), "warnings": check_text(d.body.strip(), evidence, False)[1]})
    best = next((v for v in variants if not v["warnings"] and len(v["text"]) <= MAX_TEXT), variants[0])
    ctx = drafts[0].context or {}
    community = ctx.get("community_context") or {}
    context_lines = [f"Telegram community: {line}" for line in item.evidence] + [f"Topic: {t}" for t in (community.get("topics") or [])[:2]]
    req = ActionRequest(Platform.X, SocialAction.POST, None, None, str(best["text"])[:MAX_TEXT])
    extra = {"topic": item.topic, "reason": o.why, "model": (ctx.get("generated_by") or {}).get("model"), "confidence": "medium",
             "context": context_lines, "warnings": list(best["warnings"]) + ([f"Cut to {MAX_TEXT} characters"] if len(str(best["text"])) > MAX_TEXT else []),
             "variants": variants if len(variants) > 1 else [], "origin": origin, "priority": item.label, "signals": item.signals,
             "content_ids": [d.id for d in drafts]}
    a = propose(db, agent_id, req, "opportunity", o.id, extra, settings=runtime.settings, actor="ai")
    return ApprovalOut.model_validate(a)


@router.post("/growth/skip/{approval_id}", response_model=ApprovalOut)
def skip(approval_id: int, db: DbSession) -> ApprovalOut:
    """SKIP in a review session: no decision now; the proposal stays pending (it expires after 48 h)."""
    a = db.get(ApprovalRequest, approval_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval request not found")
    if a.status != ApprovalStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This request is already {a.status.value}")
    record_decision(db, a, FeedbackDecision.SKIP)
    db.commit()
    return ApprovalOut.model_validate(a)


# --- learning, analytics, summary, usage -----------------------------------------------------------------


@router.get("/growth/learning")
def learning(db: DbSession) -> list[dict[str, object]]:
    """WHAT PRICE CHAT IS LEARNING (including disabled signals, so they can be turned back on)."""
    return [{"key": s.key, "text": s.text, "count": s.count, "of": s.of, "state": s.state} for s in operator_signals(db, include_disabled=True)]


class PreferenceIn(StrictInput):
    action: Annotated[str, StringConstraints(pattern=r"^(accept|disable|reset|activate)$")]


@router.put("/growth/learning/{key}")
def update_learning(key: str, payload: PreferenceIn, db: DbSession) -> list[dict[str, object]]:
    set_preference(db, key, payload.action)
    return learning(db)


@router.get("/growth/analytics")
def pilot_analytics(db: DbSession, days: int = Query(30, ge=1, le=365)) -> dict[str, object]:
    return growth.analytics(db, days)


@router.get("/growth/daily-summary")
def daily_summary(db: DbSession) -> dict[str, object]:
    return growth.daily_summary(db)


@router.get("/growth/usage")
def compute_usage(db: DbSession, runtime: Runtime, days: int = Query(1, ge=1, le=31)) -> dict[str, object]:
    """X API requests and estimated cost, Telegram requests, fast/strong model calls."""
    from app.social.budget import PlatformBudget

    u = usage.summary(db, days)
    models = u.get("model", {})
    secs = u.get("model:seconds", {})
    b = PlatformBudget(db, runtime.settings)
    return {
        "days": days,
        "x": {"today": b.today(), "month": b.month(), "budget": b.limits(),
              "note": "X cost is Price Chat's local estimate from published prices; X does not report exact charges via the API."},
        "telegram": {"requests": u.get("telegram", {}), "note": "The Telegram APIs have no per-request charge."},
        "models": {
            "fast_calls": sum(v for k, v in models.items() if k.startswith("fast:")),
            "strong_calls": sum(v for k, v in models.items() if k.startswith("strong:")),
            "fast_seconds": round(sum(v for k, v in secs.items() if k.startswith("fast:")), 1),
            "strong_seconds": round(sum(v for k, v in secs.items() if k.startswith("strong:")), 1),
            "by_task": models,
        },
    }


# --- creator discovery ------------------------------------------------------------------------------------


@router.get("/x/creators")
def creators(db: DbSession) -> list[dict[str, object]]:
    """Accounts that keep appearing in monitored X data. Suggestions only; Price Chat never follows on its own."""
    return growth.discover_creators(db)


@router.post("/x/creators/{profile_id}/ignore", status_code=status.HTTP_204_NO_CONTENT)
def ignore_creator(profile_id: int, db: DbSession) -> None:
    if db.get(SocialProfile, profile_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    if db.scalar(select(DiscoveryIgnore.id).where(DiscoveryIgnore.profile_id == profile_id)) is None:
        db.add(DiscoveryIgnore(profile_id=profile_id))
        db.commit()
