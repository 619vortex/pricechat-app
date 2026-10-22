"""X (Twitter) API: connection, monitoring, posts, opportunities, analytics, and supervised actions (proposals go to the approval queue)."""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import StringConstraints
from sqlalchemy import String, cast, func, select

from app.api.deps import DbSession, Runtime
from app.api.social_schemas import (
    ApprovalOut,
    MetricSnapshotOut,
    MonitorRuleIn,
    MonitorRuleOut,
    MonitorRuleUpdate,
    ProfileOut,
    RelationshipOut,
    SecretStatusOut,
    SocialOpportunityOut,
    SocialOpportunityUpdate,
    SocialPostOut,
    ToggleIn,
    WatchIn,
    WatchOut,
    WatchUpdate,
    XCredentialsIn,
    XStatusOut,
)
from app.api.schemas import StrictInput
from app.connectors.state import get_connector_state
from app.connectors.x.normalize import profile as to_profile
from app.db.base import utcnow
from app.models.enums import ActivityLevel, Platform, SocialAction, SocialOpportunityStatus, SuggestedAction
from app.models.social import SocialPost
from app.models.social_intel import (
    ApprovalRequest,
    MonitorRule,
    SocialOpportunity,
    SocialPostMetric,
    SocialProfile,
    SocialRelationship,
    WatchedAccount,
)
from app.security import credentials
from app.services.activity import ActivityLogger
from app.services.agent_service import get_agent_config
from app.social.approvals import propose
from app.social.budget import PlatformBudget
from app.social.guard import ActionRequest
from app.social.store import upsert_profile
from app.social.x_service import X_SECRET_NAMES, make_x_client, remove_credentials, save_credentials, test_connection

router = APIRouter(prefix="/x", tags=["x"])


def _current_caps(caps: dict, creds: credentials.XCredentials) -> dict:
    """Fill in write capabilities missing from an older connection test (from saved credentials; no X calls)."""
    from app.social.x_service import CAPABILITIES, write_capabilities

    if all(f"write_{a}" in caps for a in ("post", "reply", "like", "repost", "follow")):
        return caps
    own = (caps.get("own_account") or {}).get("status", "not_configured")
    derived = write_capabilities(creds.user_context, own, None)
    out = {k: v for k, v in caps.items() if k != "write_actions"}
    for k, r in derived.items():
        out[k] = {"status": r.status, "detail": r.detail, "label": CAPABILITIES[k]}
    return out


def status_out(db: DbSession, runtime: Runtime) -> XStatusOut:
    s = runtime.settings
    state = get_connector_state(db, Platform.X)
    budget = PlatformBudget(db, s)
    infos = [credentials.info(n, s) for n in X_SECRET_NAMES]
    creds = credentials.x_credentials(s)
    st = "not_configured" if not creds.configured else ("paused" if state.paused else (state.status if state.status != "not_configured" else "disconnected"))
    return XStatusOut(
        configured=creds.configured, status=st,
        credentials=[SecretStatusOut(name=i.name, configured=i.configured, source=i.source, saved_at=i.saved_at) for i in infos],
        auth_modes=[m for m, on in (("app_only", creds.app_only), ("user_context", creds.user_context)) if on],
        account_username=state.account_username, account_id=state.account_external_id, account_name=state.account_name,
        capabilities=_current_caps(state.capabilities or {}, creds), last_success_at=state.last_success_at, last_error=state.last_error,
        last_error_at=state.last_error_at, paused=state.paused, blocked_until=state.blocked_until,
        usage_today=budget.today(), usage_month=budget.month(), budget=budget.limits(),
    )


@router.get("/status", response_model=XStatusOut)
def get_status(db: DbSession, runtime: Runtime) -> XStatusOut:
    return status_out(db, runtime)


@router.put("/credentials", response_model=XStatusOut)
def put_credentials(payload: XCredentialsIn, db: DbSession, runtime: Runtime) -> XStatusOut:
    """Store X credentials encrypted on this computer. They are never returned or logged."""
    values = payload.model_dump(exclude_none=True)
    # Keys are validated as pairs. The Bearer Token works alone; app keys and user-context tokens are optional.
    have = {k: bool(values.get(k) or credentials.resolve(f"x_{k}", runtime.settings)) for k in ("api_key", "api_secret", "access_token", "access_token_secret")}
    pairs = {"Consumer Key and Consumer Secret": ("api_key", "api_secret"), "Access Token and Access Token Secret": ("access_token", "access_token_secret")}
    for name, (a, b) in pairs.items():
        if (a in values or b in values) and not (have[a] and have[b]):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{name} belong together: paste both.")
    if ("access_token" in values or "access_token_secret" in values) and not (have["api_key"] and have["api_secret"]):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Access Token and Secret also need the Consumer Key and Consumer Secret of the same app.")
    save_credentials(db, runtime.settings, runtime.x, values)
    return status_out(db, runtime)


@router.delete("/credentials", response_model=XStatusOut)
def delete_credentials(db: DbSession, runtime: Runtime) -> XStatusOut:
    remove_credentials(db, runtime.settings, runtime.x)
    return status_out(db, runtime)


@router.post("/test", response_model=XStatusOut)
async def run_test(db: DbSession, runtime: Runtime) -> XStatusOut:
    """Check what the configured access can do (makes a few small, billed reads)."""
    await test_connection(db, runtime.settings, runtime.x)
    return status_out(db, runtime)


@router.put("/reading", response_model=XStatusOut)
def set_reading(payload: ToggleIn, db: DbSession, runtime: Runtime) -> XStatusOut:
    state = get_connector_state(db, Platform.X)
    state.paused = not payload.enabled
    if payload.enabled:
        state.blocked_until = None
    db.commit()
    runtime.x.status = "paused" if state.paused else (state.status if state.status != "not_configured" else "disconnected")
    ActivityLogger(db).log("x_reading_toggled", f"X reading {'resumed' if payload.enabled else 'paused'}", level=ActivityLevel.AUDIT, actor="dashboard")
    return status_out(db, runtime)


@router.post("/sync")
async def sync_now(runtime: Runtime) -> dict[str, object]:
    """Read X now. Never waits behind other background work: if a job (for example a strong-model
    analysis) is running, X reading is queued to run right after it and this returns immediately."""
    assert runtime.scheduler is not None
    if runtime.scheduler.busy or runtime.scheduler.is_queued("x.ingest"):
        runtime.scheduler.request_run("x.ingest", delay_seconds=0)
        return {"name": "x.ingest", "status": "queued", "result": {}, "error": None}
    out = await runtime.scheduler.run_job("x.ingest", trigger="manual")
    return {"name": out.name, "status": out.status, "result": out.result, "error": out.error}


# --- monitoring rules -----------------------------------------------------------------------


def _rule_counts(db: DbSession, start_hours: int, end_hours: int = 0) -> dict[int, int]:
    """Posts each monitoring rule found per time window, by when they were posted (stored data only)."""
    now = utcnow()
    stmt = select(SocialPost.rule_ids, SocialPost.posted_at).where(
        SocialPost.platform == Platform.X, SocialPost.posted_at >= now - timedelta(hours=start_hours),
        SocialPost.posted_at < now - timedelta(hours=end_hours),
    )
    counts: dict[int, int] = {}
    for ids, _ in db.execute(stmt):
        for rid in ids or []:
            counts[rid] = counts.get(rid, 0) + 1
    return counts


def _rule_counts_24h(db: DbSession) -> dict[int, int]:
    return _rule_counts(db, 24)


@router.get("/rules", response_model=list[MonitorRuleOut])
def list_rules(db: DbSession) -> list[MonitorRuleOut]:
    rows = db.scalars(select(MonitorRule).where(MonitorRule.platform == Platform.X).order_by(MonitorRule.priority, MonitorRule.id))
    counts = _rule_counts_24h(db)
    return [MonitorRuleOut.model_validate(r).model_copy(update={"posts_24h": counts.get(r.id, 0)}) for r in rows]


@router.post("/rules", response_model=MonitorRuleOut, status_code=status.HTTP_201_CREATED)
def add_rule(payload: MonitorRuleIn, db: DbSession) -> MonitorRuleOut:
    rule = MonitorRule(platform=Platform.X, **payload.model_dump())
    db.add(rule)
    db.commit()
    ActivityLogger(db).log("x_rule_added", f'Monitoring rule "{rule.name}" added ({rule.type.value}: {rule.query})', actor="dashboard")
    return MonitorRuleOut.model_validate(rule)


def _rule(db: DbSession, rule_id: int) -> MonitorRule:
    rule = db.get(MonitorRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found")
    return rule


@router.patch("/rules/{rule_id}", response_model=MonitorRuleOut)
def edit_rule(rule_id: int, payload: MonitorRuleUpdate, db: DbSession) -> MonitorRuleOut:
    rule = _rule(db, rule_id)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "query" in changes and changes["query"] != rule.query:
        rule.since_id = None  # a new query starts fresh
    for k, v in changes.items():
        setattr(rule, k, v)
    db.commit()
    return MonitorRuleOut.model_validate(rule)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: int, db: DbSession) -> None:
    db.delete(_rule(db, rule_id))
    db.commit()


# --- watchlist & relationships -------------------------------------------------------------


def watch_out(db: DbSession, w: WatchedAccount) -> WatchOut:
    p = db.get(SocialProfile, w.profile_id)
    rel = db.scalar(select(SocialRelationship).where(SocialRelationship.profile_id == w.profile_id))
    return WatchOut(
        id=w.id, profile=ProfileOut.model_validate(p), reason=w.reason, category=w.category, priority=w.priority,
        notes=w.notes, enabled=w.enabled, last_checked_at=w.last_checked_at, last_success_at=w.last_success_at, last_error=w.last_error,
        relationship=RelationshipOut.model_validate(rel) if rel else None,
    )


@router.get("/watchlist", response_model=list[WatchOut])
def list_watchlist(db: DbSession) -> list[WatchOut]:
    return [watch_out(db, w) for w in db.scalars(select(WatchedAccount).order_by(WatchedAccount.priority, WatchedAccount.id))]


@router.post("/watchlist", response_model=WatchOut, status_code=status.HTTP_201_CREATED)
async def add_watch(payload: WatchIn, db: DbSession, runtime: Runtime) -> WatchOut:
    """Look the account up on X (one billed user read) and add it to the watchlist."""
    username = payload.username.lstrip("@")
    profile = db.scalar(select(SocialProfile).where(SocialProfile.platform == Platform.X, func.lower(SocialProfile.username) == username.lower()))
    if profile is None:
        client = make_x_client(db, runtime.settings, runtime.x)
        try:
            user = await client.user_by_username(username)
        finally:
            await client.aclose()
        profile = upsert_profile(db, to_profile(user))
    if db.scalar(select(WatchedAccount).where(WatchedAccount.profile_id == profile.id)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already on the watchlist")
    w = WatchedAccount(profile_id=profile.id, reason=payload.reason, category=payload.category, priority=payload.priority, notes=payload.notes)
    db.add(w)
    db.commit()
    ActivityLogger(db).log("x_watch_added", f"Watching @{profile.username} ({payload.category.value.replace('_', ' ')})", actor="dashboard")
    return watch_out(db, w)


def _watch(db: DbSession, watch_id: int) -> WatchedAccount:
    w = db.get(WatchedAccount, watch_id)
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not on the watchlist")
    return w


@router.patch("/watchlist/{watch_id}", response_model=WatchOut)
def edit_watch(watch_id: int, payload: WatchUpdate, db: DbSession) -> WatchOut:
    w = _watch(db, watch_id)
    for k, v in payload.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(w, k, v)
    db.commit()
    return watch_out(db, w)


@router.delete("/watchlist/{watch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_watch(watch_id: int, db: DbSession) -> None:
    db.delete(_watch(db, watch_id))
    db.commit()


class RelationshipNotes(StrictInput):
    notes: str


@router.put("/profiles/{profile_id}/relationship", response_model=RelationshipOut)
def set_relationship_notes(profile_id: int, payload: RelationshipNotes, db: DbSession) -> RelationshipOut:
    if db.get(SocialProfile, profile_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")
    rel = db.scalar(select(SocialRelationship).where(SocialRelationship.profile_id == profile_id))
    if rel is None:
        rel = SocialRelationship(profile_id=profile_id, topics={})
        db.add(rel)
    rel.notes = payload.notes[:4000]
    db.commit()
    return RelationshipOut.model_validate(rel)


# --- posts, mentions, analytics ---------------------------------------------------------------


def post_out(db: DbSession, p: SocialPost) -> SocialPostOut:
    a = db.get(SocialProfile, p.author_profile_id) if p.author_profile_id else None
    return SocialPostOut(
        id=p.id, platform=p.platform, external_id=p.external_id, url=p.url, text=p.text,
        author_username=a.username if a else p.author_handle, author_name=a.display_name if a else None,
        author_followers=a.followers_count if a else None, posted_at=p.posted_at, is_own=p.is_own,
        source_kinds=p.source_kinds or [], rule_ids=p.rule_ids or [], metrics=p.metrics or {}, lang=p.lang,
        in_reply_to_external_id=p.in_reply_to_external_id, ingested_at=p.created_at,
        topics=[r.name for r in (db.get(MonitorRule, rid) for rid in (p.rule_ids or [])) if r is not None],
    )


@router.get("/posts", response_model=list[SocialPostOut])
def list_posts(
    db: DbSession,
    source: str | None = Query(None, pattern="^(own|mention|search|watchlist)$"),
    rule_id: int | None = None,
    profile_id: int | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[SocialPostOut]:
    rows = list(db.scalars(select(SocialPost).where(SocialPost.platform == Platform.X).order_by(SocialPost.posted_at.desc(), SocialPost.id.desc()).limit(2000)))
    if source:
        rows = [p for p in rows if source in (p.source_kinds or []) or (source == "own" and p.is_own)]
    if rule_id is not None:
        rows = [p for p in rows if rule_id in (p.rule_ids or [])]
    if profile_id is not None:
        rows = [p for p in rows if p.author_profile_id == profile_id]
    return [post_out(db, p) for p in rows[:limit]]


@router.get("/posts/{post_id}/metrics", response_model=list[MetricSnapshotOut])
def post_metrics(post_id: int, db: DbSession) -> list[MetricSnapshotOut]:
    rows = db.scalars(select(SocialPostMetric).where(SocialPostMetric.post_id == post_id).order_by(SocialPostMetric.captured_at))
    return [MetricSnapshotOut.model_validate(r) for r in rows]


@router.get("/analytics")
def analytics(db: DbSession) -> dict[str, object]:
    """Own-post metrics as reported by X. Missing metrics stay null; nothing is estimated."""
    own = list(db.scalars(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.is_own.is_(True)).order_by(SocialPost.posted_at.desc()).limit(100)))
    keys = ("impressions", "likes", "replies", "reposts", "quotes", "bookmarks", "profile_visits", "url_clicks")
    totals: dict[str, int | None] = {}
    for k in keys:
        vals = [p.metrics.get(k) for p in own if (p.metrics or {}).get(k) is not None]
        totals[k] = sum(vals) if vals else None
    return {
        "posts": [post_out(db, p).model_dump(mode="json") for p in own],
        "totals": totals,
        "post_count": len(own),
        "note": "Metrics come from the X API. Values X did not provide are shown as unavailable, never estimated.",
    }


# --- X opportunities -----------------------------------------------------------------------------


def opp_out(db: DbSession, o: SocialOpportunity) -> SocialOpportunityOut:
    post = db.get(SocialPost, o.post_id)
    assert post is not None
    return SocialOpportunityOut(
        id=o.id, platform=o.platform, kind=o.kind.value, topic=o.topic, relevance=o.relevance, why=o.why,
        contribution=o.contribution, suggested_action=o.suggested_action.value, knowledge=o.knowledge or [],
        engagement=o.engagement or {}, signals=o.signals or {}, reasons=o.reasons or [], priority=o.priority,
        status=o.status, model=o.model, generated_at=o.generated_at, post=post_out(db, post),
    )


@router.get("/opportunities", response_model=list[SocialOpportunityOut])
def list_opportunities(db: DbSession, status_filter: SocialOpportunityStatus | None = Query(SocialOpportunityStatus.NEW, alias="status"),
                       limit: int = Query(50, ge=1, le=200)) -> list[SocialOpportunityOut]:
    stmt = select(SocialOpportunity).where(SocialOpportunity.platform == Platform.X)
    if status_filter is not None:
        stmt = stmt.where(SocialOpportunity.status == status_filter)
    return [opp_out(db, o) for o in db.scalars(stmt.order_by(SocialOpportunity.priority.desc()).limit(limit))]


@router.patch("/opportunities/{opp_id}", response_model=SocialOpportunityOut)
def update_opportunity(opp_id: int, payload: SocialOpportunityUpdate, db: DbSession) -> SocialOpportunityOut:
    o = db.get(SocialOpportunity, opp_id)
    if o is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunity not found")
    o.status = payload.status
    db.commit()
    return opp_out(db, o)


class ProposeIn(StrictInput):
    action: SocialAction | None = None  # default: what the AI suggested
    text: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None


class DraftReplyIn(StrictInput):
    standalone: bool = False  # True: a standalone post inspired by the post, not a reply
    direction: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


def _opp_parts(db: DbSession, opp_id: int) -> tuple[SocialOpportunity, SocialPost, SocialProfile | None]:
    o = db.get(SocialOpportunity, opp_id)
    if o is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunity not found")
    post = db.get(SocialPost, o.post_id)
    if post is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The original post is gone")
    author = db.get(SocialProfile, post.author_profile_id) if post.author_profile_id else None
    return o, post, author


def _original(post: SocialPost, author: SocialProfile | None) -> dict[str, object]:
    return {"id": post.external_id, "text": post.text, "url": post.url, "author": author.username if author else post.author_handle,
            "posted_at": post.posted_at.isoformat() if post.posted_at else None}


@router.post("/opportunities/{opp_id}/propose", response_model=ApprovalOut, status_code=status.HTTP_201_CREATED)
def propose_action(opp_id: int, payload: ProposeIn, db: DbSession, runtime: Runtime) -> ApprovalOut:
    """Put one action for this opportunity into the approval queue (nothing is executed here)."""
    o, post, author = _opp_parts(db, opp_id)
    action = payload.action or {SuggestedAction.REPLY: SocialAction.REPLY, SuggestedAction.QUOTE_CANDIDATE: SocialAction.POST,
                                SuggestedAction.STANDALONE_POST: SocialAction.POST}.get(o.suggested_action)
    if action is None or action == SocialAction.DIRECT_MESSAGE:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"'{o.suggested_action.value}' is not a social action; choose one")
    extra: dict[str, object] = {"original_post": _original(post, author), "topic": o.topic, "model": o.model,
                                "reason": (o.engagement or {}).get("suggestions", {}).get("reason") or o.why,
                                "context": [f"Why flagged: {o.why}"] + [f"Price Chat knowledge: {k.get('title') or ''}" for k in (o.knowledge or [])[:2]]}
    if action == SocialAction.FOLLOW:
        if author is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The author of this post is unknown")
        req = ActionRequest(Platform.X, action, author.external_id, author.username, None)
        extra["target_profile"] = {"username": author.username, "name": author.display_name, "followers": author.followers_count}
    elif action in (SocialAction.LIKE, SocialAction.REPOST):
        req = ActionRequest(Platform.X, action, post.external_id, author.username if author else post.author_handle, None)
    elif action == SocialAction.REPLY:
        if not payload.text:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Write the reply text, or use Draft reply")
        req = ActionRequest(Platform.X, action, post.external_id, author.username if author else post.author_handle, payload.text)
    else:  # standalone post (optionally quoting the original)
        if not payload.text:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Write the post text, or use Draft post")
        req = ActionRequest(Platform.X, SocialAction.POST, None, None, payload.text)
        if o.suggested_action == SuggestedAction.QUOTE_CANDIDATE:
            extra["quote_post_id"] = post.external_id
    try:
        a = propose(db, get_agent_config(db).id, req, "social_opportunity", o.id, extra, settings=runtime.settings)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    o.status = SocialOpportunityStatus.REVIEWING
    db.commit()
    return ApprovalOut.model_validate(a)


@router.post("/opportunities/{opp_id}/draft-reply", response_model=ApprovalOut, status_code=status.HTTP_201_CREATED)
async def draft_reply_for(opp_id: int, payload: DraftReplyIn, db: DbSession, runtime: Runtime) -> ApprovalOut:
    """AI writes a reply (or a standalone post) with the relevant context and puts it in the approval queue."""
    from app.social.x_replies import draft_reply

    o, post, author = _opp_parts(db, opp_id)
    agent_id = get_agent_config(db).id
    async with runtime.gate.interactive():
        drafted = await draft_reply(db, runtime.router(db, background=False), agent_id, o, standalone=payload.standalone,
                                    direction=payload.direction)
    if not drafted.worth_it:
        raise HTTPException(status.HTTP_409_CONFLICT, f"The AI found nothing worthwhile to add: {drafted.why or 'no reason given'}")
    action = SocialAction.POST if payload.standalone else SocialAction.REPLY
    req = ActionRequest(Platform.X, action, None if payload.standalone else post.external_id,
                        None if payload.standalone else (author.username if author else post.author_handle), drafted.text[:1000])
    extra = {"original_post": _original(post, author), "topic": o.topic, "reason": drafted.why or o.why, "model": drafted.model,
             "confidence": drafted.confidence, "context": drafted.context, "warnings": drafted.warnings}
    try:
        a = propose(db, agent_id, req, "social_opportunity", o.id, extra, settings=runtime.settings, actor="ai")
    except ValueError as exc:  # over the length limit even after a retry: hand it over for editing instead of failing
        req = ActionRequest(req.platform, req.action, req.target_external_id, req.target_username, drafted.text[:280])
        extra["warnings"] = [*drafted.warnings, f"{exc}; cut to 280 characters, please edit"]
        a = propose(db, agent_id, req, "social_opportunity", o.id, extra, settings=runtime.settings, actor="ai")
    o.status = SocialOpportunityStatus.REVIEWING
    db.commit()
    return ApprovalOut.model_validate(a)


@router.post("/test-post", response_model=ApprovalOut, status_code=status.HTTP_201_CREATED)
def prepare_test_post(db: DbSession, runtime: Runtime) -> ApprovalOut:
    """One harmless standalone post for the first supervised test. It only goes to the approval queue."""
    stamp = utcnow().strftime("%d %b %H:%M UTC")
    text = f"Testing Price Chat's new publishing setup ({stamp}). Nothing to see here yet."
    req = ActionRequest(Platform.X, SocialAction.POST, None, None, text)
    a = propose(db, get_agent_config(db).id, req, "test", None,
                {"reason": "Harmless test post to verify the approval → safety check → X pipeline", "model": None,
                 "context": ["Created by the 'Prepare test post' button"]}, settings=runtime.settings)
    return ApprovalOut.model_validate(a)


@router.get("/operator")
def operator_status(db: DbSession, runtime: Runtime) -> dict[str, object]:
    """Everything the X operator panel shows, from stored state only (no X API calls)."""
    from app.learning.operator import operator_signals
    from app.models.enums import ApprovalStatus
    from app.models.safety import ActionLimit
    from app.models.routing import ScheduledJob
    from app.services.safety import get_or_create_policy
    from app.social.guard import AUTOMATIC_LOCK_REASON, executed_today

    agent_id = get_agent_config(db).id
    policy = get_or_create_policy(db, agent_id)
    limits = {l.action.value: l for l in db.scalars(select(ActionLimit).where(ActionLimit.policy_id == policy.id, ActionLimit.platform == Platform.X))}
    done = executed_today(db)
    st = status_out(db, runtime)
    caps = st.capabilities or {}
    jobs = {j.name: j for j in db.scalars(select(ScheduledJob).where(ScheduledJob.name.in_(["x.opportunities", "x.ingest"])))}
    xo = jobs.get("x.opportunities")
    return {
        "x_status": st.status, "username": st.account_username,
        "user_context": "user_context" in st.auth_modes,
        "write_capability": {a: (caps.get(f"write_{a}") or {}).get("status", "unknown") for a in ("post", "reply", "like", "repost", "follow")},
        "social_actions_active": policy.outbound_enabled and not policy.kill_switch,
        "dry_run": policy.dry_run,
        "mode": "approval_required", "automatic": AUTOMATIC_LOCK_REASON,
        "today": [{"action": a, "done": done.get(a, 0), "limit": limits[a].max_per_day if a in limits else 0,
                   "per_hour": limits[a].max_per_hour if a in limits else 0, "enabled": bool(a in limits and limits[a].enabled)}
                  for a in ("post", "reply", "like", "repost", "follow")],
        "pending_approvals": int(db.scalar(select(func.count()).select_from(ApprovalRequest).where(
            ApprovalRequest.platform == Platform.X, ApprovalRequest.status == ApprovalStatus.PENDING)) or 0),
        "opportunities": int(db.scalar(select(func.count()).select_from(SocialOpportunity).where(SocialOpportunity.status == SocialOpportunityStatus.NEW)) or 0),
        "ai": {"screening_model": runtime.settings.model_tier_fast, "analysis_model": runtime.settings.model_tier_strong,
               "analysis_status": ("running" if runtime.scheduler and runtime.scheduler.is_running("x.opportunities") else (xo.last_status if xo else None)),
               "last_analysis_at": xo.last_finished_at if xo else None},
        "usage_today": st.usage_today,
        "learning": [{"key": s.key, "text": s.text} for s in operator_signals(db)],
    }


@router.get("/overview")
def x_overview(db: DbSession, runtime: Runtime) -> dict[str, object]:
    since = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    posts = select(func.count()).select_from(SocialPost).where(SocialPost.platform == Platform.X)
    return {
        "status": status_out(db, runtime).model_dump(mode="json"),
        "monitored_accounts": int(db.scalar(select(func.count()).select_from(WatchedAccount).where(WatchedAccount.enabled.is_(True))) or 0),
        "rules": int(db.scalar(select(func.count()).select_from(MonitorRule).where(MonitorRule.enabled.is_(True))) or 0),
        "posts_total": int(db.scalar(posts) or 0),
        "posts_today": int(db.scalar(posts.where(SocialPost.created_at >= since)) or 0),
        "mentions": int(db.scalar(select(func.count()).select_from(SocialPost).where(SocialPost.platform == Platform.X, cast(SocialPost.source_kinds, String).like('%"mention"%'))) or 0),
        "open_opportunities": int(db.scalar(select(func.count()).select_from(SocialOpportunity).where(SocialOpportunity.status == SocialOpportunityStatus.NEW)) or 0),
        "topic_activity": _topic_activity(db),
        "cost_note": "Costs are Price Chat's local estimate from X's published per-request prices. X does not report exact charges through the API; check console.x.com for actual billing.",
    }


def _topic_activity(db: DbSession) -> list[dict[str, object]]:
    """Monitored topics by posts found in the last 24 h vs the 24 h before (no API calls)."""
    now_counts, prev_counts = _rule_counts(db, 24), _rule_counts(db, 48, 24)
    rules = db.scalars(select(MonitorRule).where(MonitorRule.platform == Platform.X).order_by(MonitorRule.priority, MonitorRule.id))
    out = []
    for r in rules:
        cur, prev = now_counts.get(r.id, 0), prev_counts.get(r.id, 0)
        trend = "insufficient_data" if cur + prev < 4 else "rising" if cur >= prev * 1.5 else "falling" if cur <= prev * 0.67 else "steady"
        out.append({"id": r.id, "name": r.name, "posts_24h": cur, "posts_prev_24h": prev, "trend": trend, "enabled": r.enabled})
    return sorted(out, key=lambda t: -int(t["posts_24h"]))


# --- likes pilot: AI-recommended likes, human-approved one at a time --------------------------------------


@router.get("/likes")
def like_pilot(db: DbSession, runtime: Runtime, limit: int = Query(5, ge=1, le=10), include_filtered: bool = False) -> dict[str, object]:
    """Like capability, today's usage and the strongest few AI-recommended like candidates (possibly none)."""
    from app.social.likes import like_candidates, like_status

    agent_id = get_agent_config(db).id
    return {"status": like_status(db, agent_id, runtime.settings), **like_candidates(db, agent_id, limit, include_filtered)}


@router.post("/likes/{opp_id}/propose", response_model=ApprovalOut, status_code=status.HTTP_201_CREATED)
def propose_like(opp_id: int, db: DbSession, runtime: Runtime) -> ApprovalOut:
    """Put one recommended like into the approval queue (reuses a pending one). Nothing is liked here."""
    from app.models.enums import ApprovalStatus
    from app.social.likes import candidate_for, like_payload

    agent_id = get_agent_config(db).id
    try:
        c = candidate_for(db, agent_id, opp_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if c.pending_approval_id:
        existing = db.get(ApprovalRequest, c.pending_approval_id)
        if existing is not None and existing.status == ApprovalStatus.PENDING:
            return ApprovalOut.model_validate(existing)
    if c.problems:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Not a good like candidate: {'; '.join(c.problems)}")
    req = ActionRequest(Platform.X, SocialAction.LIKE, c.post["external_id"], c.post["author"], None)
    a = propose(db, agent_id, req, "social_opportunity", opp_id, like_payload(db, agent_id, runtime.settings, c), settings=runtime.settings)
    o = db.get(SocialOpportunity, opp_id)
    if o is not None and o.status == SocialOpportunityStatus.NEW:
        o.status = SocialOpportunityStatus.REVIEWING
        db.commit()
    return ApprovalOut.model_validate(a)
