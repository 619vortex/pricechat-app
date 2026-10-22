"""Feedback, topic merging, drafts, cross-platform signals, safety guard, approvals, command center."""

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.community_schemas import TopicOut
from app.api.deps import DbSession, Runtime, activity_out
from app.api.social_schemas import (
    SocialActionsIn,
    ApprovalDecisionIn,
    ApprovalIn,
    ApprovalOut,
    CrossSignalOut,
    DraftOut,
    DraftRequestIn,
    FeedbackIn,
    FeedbackOut,
    GuardCheckIn,
    GuardDecisionOut,
    GuardSettingsOut,
    GuardSettingsUpdate,
    MergeSuggestionOut,
    PermissionOut,
    PermissionUpdate,
    PreferenceOut,
    SplitIn,
)
from app.community.merging import keep_separate, merge_topics, split_topic
from app.community.ranking import rank_opportunities
from app.community.tuning import CommunityTuning
from app.connectors.state import get_connector_state
from app.content.drafts import generate_drafts
from app.db.base import utcnow
from app.learning.feedback import FeedbackError, PreferenceModel, record_feedback
from app.models.community import CommunityTopic, ContentOpportunity
from app.models.content import Content
from app.models.enums import (
    ActivityLevel,
    ApprovalStatus,
    ContentStatus,
    CrossSignalStatus,
    FeedbackTarget,
    MergeStatus,
    OpportunityStatus,
    Platform,
    SocialAction,
    SocialOpportunityStatus,
    TopicStatus,
    TopicTrend,
)
from app.models.memory import Memory
from app.models.routing import ScheduledJob
from app.models.social import SocialPost
from app.models.social_intel import ApprovalRequest, CrossSignal, FeedbackEvent, MonitorRule, SocialOpportunity, TopicMergeSuggestion, WatchedAccount
from app.models.telegram import TelegramMessage, TelegramSource
from app.services.activity import ActivityLogger, recent_activity
from app.services.agent_service import build_retriever, get_agent_config
from app.services.safety import get_or_create_policy
from app.social.approvals import AVAILABLE_MODES, PermissionError_, decide, permissions, propose, set_permission
from app.social.guard import AUTOMATIC_LOCK_REASON, WRITE_EXECUTION_AVAILABLE, ActionRequest, SocialActionGuard
from app.timeutil import local_day_start

router = APIRouter(tags=["social"])


# --- feedback -----------------------------------------------------------------------------------


@router.post("/feedback/{kind}/{target_id}", response_model=FeedbackOut, status_code=status.HTTP_201_CREATED)
async def give_feedback(kind: FeedbackTarget, target_id: int, payload: FeedbackIn, db: DbSession, runtime: Runtime) -> FeedbackOut:
    if kind == FeedbackTarget.SOCIAL_ACTION or payload.decision.value == "skip":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Decide on proposed actions (including skip) in the approval queue")
    try:
        event = await record_feedback(db, kind, target_id, payload.decision, runtime.embedder, payload.note, payload.edits)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except FeedbackError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if kind in (FeedbackTarget.OPPORTUNITY, FeedbackTarget.TOPIC, FeedbackTarget.DRAFT) and runtime.scheduler:
        runtime.scheduler.request_run("opportunities.rank", 5)  # learned preferences change rankings
    return FeedbackOut.model_validate(event)


@router.get("/feedback", response_model=list[FeedbackOut])
def list_feedback(db: DbSession, kind: FeedbackTarget | None = None, limit: int = Query(100, ge=1, le=500)) -> list[FeedbackOut]:
    stmt = select(FeedbackEvent).order_by(FeedbackEvent.id.desc()).limit(limit)
    if kind is not None:
        stmt = stmt.where(FeedbackEvent.target_kind == kind)
    return [FeedbackOut.model_validate(e) for e in db.scalars(stmt)]


@router.get("/feedback/preferences", response_model=list[PreferenceOut])
def preferences(db: DbSession, scope: str = Query("opportunity", pattern="^(opportunity|social_opportunity)$")) -> list[PreferenceOut]:
    """What Price Chat has learned from your decisions (interpretable feature weights)."""
    return [PreferenceOut(feature=w.feature, approvals=w.approvals, rejections=w.rejections, weight=w.weight) for w in PreferenceModel(db, scope).table()]


# --- topic merge / split ---------------------------------------------------------------------------


def _topic_brief(t: CommunityTopic | None) -> dict[str, object]:
    if t is None:
        return {}
    return {"id": t.id, "name": t.name, "description": t.description, "message_count": t.message_count, "aliases": t.aliases, "status": t.status.value}


@router.get("/community/merges", response_model=list[MergeSuggestionOut])
def list_merges(db: DbSession, status_filter: MergeStatus = Query(MergeStatus.PENDING, alias="status")) -> list[MergeSuggestionOut]:
    rows = db.scalars(select(TopicMergeSuggestion).where(TopicMergeSuggestion.status == status_filter).order_by(TopicMergeSuggestion.similarity.desc()))
    return [
        MergeSuggestionOut(
            id=s.id, topic_a=_topic_brief(db.get(CommunityTopic, s.topic_a_id)), topic_b=_topic_brief(db.get(CommunityTopic, s.topic_b_id)),
            similarity=s.similarity, shared_participants=s.shared_participants, shared_threads=s.shared_threads, verdict=s.verdict,
            reason=s.reason, judged_by=s.judged_by, status=s.status.value, created_at=s.created_at,
        )
        for s in rows
    ]


def _suggestion(db: DbSession, sid: int) -> TopicMergeSuggestion:
    s = db.get(TopicMergeSuggestion, sid)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Suggestion not found")
    if s.status != MergeStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Already decided ({s.status.value})")
    return s


@router.post("/community/merges/{sid}/merge", response_model=TopicOut)
def do_merge(sid: int, db: DbSession, runtime: Runtime, keep: str = Query("larger", pattern="^(a|b|larger)$")) -> TopicOut:
    s = _suggestion(db, sid)
    a, b = db.get(CommunityTopic, s.topic_a_id), db.get(CommunityTopic, s.topic_b_id)
    if a is None or b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    keep_topic = a if keep == "a" or (keep == "larger" and a.message_count >= b.message_count) else b
    other = b if keep_topic is a else a
    try:
        kept = merge_topics(db, CommunityTuning.from_settings(runtime.settings), keep_topic.id, other.id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return TopicOut.model_validate(kept)


@router.post("/community/merges/{sid}/keep-separate", status_code=status.HTTP_204_NO_CONTENT)
def do_keep_separate(sid: int, db: DbSession) -> None:
    keep_separate(db, _suggestion(db, sid))


@router.post("/community/topics/{topic_id}/split", response_model=TopicOut, status_code=status.HTTP_201_CREATED)
def do_split(topic_id: int, payload: SplitIn, db: DbSession, runtime: Runtime) -> TopicOut:
    try:
        new = split_topic(db, CommunityTuning.from_settings(runtime.settings), topic_id, payload.message_ids, payload.name)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return TopicOut.model_validate(new)


# --- drafts & ranking ----------------------------------------------------------------------------


@router.post("/opportunities/{opp_id}/drafts", response_model=list[DraftOut], status_code=status.HTTP_201_CREATED)
async def make_drafts(opp_id: int, payload: DraftRequestIn, db: DbSession, runtime: Runtime) -> list[DraftOut]:
    """Generate a draft (or 3 variants) from an approved opportunity. Drafts are never published."""
    opp = db.get(ContentOpportunity, opp_id)
    if opp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunity not found")
    agent_id = get_agent_config(db).id
    try:
        async with runtime.gate.interactive():
            drafts = await generate_drafts(
                db, runtime.router(db, background=False), agent_id, opp, payload.draft_type, build_retriever(db, runtime, agent_id),
                runtime.embedder, CommunityTuning.from_settings(runtime.settings), variants=payload.variants,
            )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return [DraftOut.model_validate(d) for d in drafts]


@router.get("/drafts", response_model=list[DraftOut])
def list_drafts(db: DbSession, opportunity_id: int | None = None, limit: int = Query(100, ge=1, le=500)) -> list[DraftOut]:
    stmt = select(Content).where(Content.draft_type.is_not(None)).order_by(Content.created_at.desc(), Content.id.desc()).limit(limit)
    if opportunity_id is not None:
        stmt = stmt.where(Content.opportunity_id == opportunity_id)
    return [DraftOut.model_validate(c) for c in db.scalars(stmt)]


@router.post("/opportunities/rank")
async def rank_now(db: DbSession, runtime: Runtime) -> dict[str, int]:
    return {"ranked": await rank_opportunities(db, get_agent_config(db).id, runtime.embedder)}


# --- cross-platform signals -----------------------------------------------------------------------


def cross_out(db: DbSession, sig: CrossSignal) -> CrossSignalOut:
    """The signal plus who is behind its X side (counted from stored posts; no API calls)."""
    from app.models.social import SocialPost
    from app.models.social_intel import SocialProfile, WatchedAccount

    author_ids = {p.author_profile_id for p in (db.get(SocialPost, i) for i in sig.x_post_ids or []) if p and p.author_profile_id}
    watched = set(db.scalars(select(WatchedAccount.profile_id).where(WatchedAccount.profile_id.in_(author_ids)))) if author_ids else set()
    names = [p.username for p in (db.get(SocialProfile, i) for i in sorted(author_ids)) if p][:8]
    return CrossSignalOut.model_validate(sig).model_copy(update={"x_authors": len(author_ids), "x_watched_authors": len(watched), "x_author_names": names})


@router.get("/cross-signals", response_model=list[CrossSignalOut])
def list_cross(db: DbSession, status_filter: CrossSignalStatus | None = Query(CrossSignalStatus.ACTIVE, alias="status")) -> list[CrossSignalOut]:
    stmt = select(CrossSignal).order_by(CrossSignal.last_updated_at.desc())
    if status_filter is not None:
        stmt = stmt.where(CrossSignal.status == status_filter)
    return [cross_out(db, s) for s in db.scalars(stmt)]


@router.post("/cross-signals/{sid}/dismiss", response_model=CrossSignalOut)
def dismiss_cross(sid: int, db: DbSession) -> CrossSignalOut:
    s = db.get(CrossSignal, sid)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Signal not found")
    s.status = CrossSignalStatus.DISMISSED
    db.commit()
    return cross_out(db, s)


# --- safety guard, permissions, approvals -------------------------------------------------------------

NOTE = ("Supervised operation: every X action needs your approval, and the safety guard re-checks it before anything is sent. "
        "Automatic mode is locked. With dry run on, approved actions are checked end to end but nothing is sent to X.")


def guard_out(db: DbSession) -> GuardSettingsOut:
    agent_id = get_agent_config(db).id
    policy = get_or_create_policy(db, agent_id)
    return GuardSettingsOut(
        kill_switch=policy.kill_switch, writes_available=WRITE_EXECUTION_AVAILABLE,
        social_actions_active=policy.outbound_enabled and not policy.kill_switch, dry_run=policy.dry_run,
        automatic_locked_reason=AUTOMATIC_LOCK_REASON,
        platforms_enabled={p.value: (policy.platforms_enabled or {}).get(p.value, True) for p in (Platform.X, Platform.TELEGRAM)},
        blocked_targets=policy.blocked_targets or [], watchlist_only=policy.watchlist_only, cooldown_minutes=policy.cooldown_minutes,
        duplicate_window_hours=policy.duplicate_window_hours,
        permissions=[PermissionOut(platform=l.platform, action=l.action, mode=m, max_per_hour=l.max_per_hour, max_per_day=l.max_per_day,
                                   available_modes=AVAILABLE_MODES) for l, m in permissions(db, agent_id)],
        note=NOTE,
    )


@router.get("/social/guard", response_model=GuardSettingsOut)
def get_guard(db: DbSession) -> GuardSettingsOut:
    return guard_out(db)


@router.patch("/social/guard", response_model=GuardSettingsOut)
def update_guard(payload: GuardSettingsUpdate, db: DbSession) -> GuardSettingsOut:
    agent_id = get_agent_config(db).id
    policy = get_or_create_policy(db, agent_id)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    for k, v in changes.items():
        setattr(policy, k, v)
    db.commit()
    if "dry_run" in changes:
        ActivityLogger(db, agent_id).log(
            "dry_run_changed", "Dry run ON: approved actions are checked but not sent" if policy.dry_run
            else "Dry run OFF: approved actions are sent to X", level=ActivityLevel.AUDIT, actor="dashboard")
    return guard_out(db)


@router.put("/social/actions", response_model=GuardSettingsOut)
def set_social_actions(payload: SocialActionsIn, db: DbSession) -> GuardSettingsOut:
    """ACTIVE / PAUSED for all social write actions. Paused blocks every new write immediately; reading and analysis continue."""
    agent_id = get_agent_config(db).id
    policy = get_or_create_policy(db, agent_id)
    policy.kill_switch = not payload.active
    if payload.active:
        policy.outbound_enabled = True
    db.commit()
    ActivityLogger(db, agent_id).log(
        "social_actions_toggled", "Social actions ACTIVE (every action still needs your approval)" if payload.active
        else "Social actions PAUSED: no write action can run", level=ActivityLevel.AUDIT, actor="dashboard")
    return guard_out(db)


@router.put("/social/permissions/{platform}/{action}", response_model=GuardSettingsOut)
def update_permission(platform: Platform, action: SocialAction, payload: PermissionUpdate, db: DbSession) -> GuardSettingsOut:
    try:
        set_permission(db, get_agent_config(db).id, platform, action, payload.mode, payload.max_per_hour, payload.max_per_day)
    except PermissionError_ as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return guard_out(db)


@router.post("/social/guard/check", response_model=GuardDecisionOut)
def guard_check(payload: GuardCheckIn, db: DbSession) -> GuardDecisionOut:
    """Dry run: would this action be allowed? Nothing is executed."""
    d = SocialActionGuard(db, get_agent_config(db).id).evaluate(
        ActionRequest(payload.platform, payload.action, payload.target, payload.target, payload.text))
    return GuardDecisionOut(allowed=d.allowed, requires_approval=d.requires_approval, executable=d.executable, reason=d.reason, checks=d.checks)


@router.get("/approvals", response_model=list[ApprovalOut])
def list_approvals(db: DbSession, status_filter: ApprovalStatus | None = Query(None, alias="status")) -> list[ApprovalOut]:
    stmt = select(ApprovalRequest).order_by(ApprovalRequest.created_at.desc()).limit(200)
    if status_filter is not None:
        stmt = stmt.where(ApprovalRequest.status == status_filter)
    return [approval_out(db, a) for a in db.scalars(stmt)]


def approval_out(db: DbSession, a: ApprovalRequest) -> ApprovalOut:
    """Executed posts/replies carry the metrics X has reported since (collected by normal reading)."""
    out = ApprovalOut.model_validate(a)
    x_id = (a.execution_result or {}).get("x_id")
    if a.status == ApprovalStatus.EXECUTED and x_id:
        post = db.scalar(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.external_id == str(x_id)))
        if post is not None and post.metrics:
            out = out.model_copy(update={"performance": post.metrics})
    return out


@router.post("/approvals", response_model=ApprovalOut, status_code=status.HTTP_201_CREATED)
def create_approval(payload: ApprovalIn, db: DbSession, runtime: Runtime) -> ApprovalOut:
    req = ActionRequest(payload.platform, payload.action, payload.target_external_id, payload.target_username, payload.text)
    try:
        a = propose(db, get_agent_config(db).id, req, payload.source_kind, payload.source_id, settings=runtime.settings)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return ApprovalOut.model_validate(a)


@router.post("/approvals/from-draft/{content_id}", response_model=ApprovalOut, status_code=status.HTTP_201_CREATED)
def approval_from_draft(content_id: int, db: DbSession, runtime: Runtime) -> ApprovalOut:
    """Content → draft → (edit) → proposed X post. Threads must be posted one post at a time for now."""
    c = db.get(Content, content_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Draft not found")
    platform = c.target_platform or Platform.X
    req = ActionRequest(platform, SocialAction.POST, None, None, c.body.strip())
    ctx = c.context or {}
    try:
        a = propose(db, get_agent_config(db).id, req, "draft", c.id,
                    {"title": c.title, "draft_type": c.draft_type, "reason": ctx.get("why"),
                     "model": (ctx.get("generated_by") or {}).get("model"), "context": [f"draft #{c.id}: {c.title or ''}"]},
                    settings=runtime.settings)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{exc}. Shorten the draft before sending it for approval.") from exc
    c.status = ContentStatus.PENDING_APPROVAL
    db.commit()
    return ApprovalOut.model_validate(a)


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalOut)
async def decide_approval(approval_id: int, payload: ApprovalDecisionIn, db: DbSession, runtime: Runtime) -> ApprovalOut:
    """Approve (optionally with edited text) or reject. Approving runs the guard again and then
    either records a dry run or sends the action to X; the result is in execution_result."""
    a = db.get(ApprovalRequest, approval_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval request not found")
    if payload.decision not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Decision must be 'approved' or 'rejected'")
    try:
        await decide(db, get_agent_config(db).id, a, payload.decision == ApprovalStatus.APPROVED, payload.note,
                     settings=runtime.settings, x_runtime=runtime.x, edited_text=payload.text)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return ApprovalOut.model_validate(a)


# --- processing state & command center ---------------------------------------------------------------


@router.get("/system/processing")
def processing(runtime: Runtime) -> dict[str, object]:
    running = [n for n in (runtime.scheduler.specs if runtime.scheduler else {}) if runtime.scheduler and runtime.scheduler.is_running(n)]
    labels = {n: s.label for n, s in (runtime.scheduler.specs.items() if runtime.scheduler else [])}
    return {"gate": runtime.gate.status(), "running_jobs": [{"name": n, "label": labels.get(n, n)} for n in running]}


@router.get("/pilot-status")
async def get_pilot_status(db: DbSession, runtime: Runtime) -> dict[str, object]:
    """Is each part ready for the real pilot? READY only when actually tested."""
    from app.services.pilot import pilot_status

    return await pilot_status(db, runtime)


@router.get("/command-center")
async def command_center(db: DbSession, runtime: Runtime) -> dict[str, object]:
    config = get_agent_config(db)
    model = config.model_name or runtime.provider.model
    health = await runtime.llm_health(model)
    today = local_day_start(utcnow())
    x_state = get_connector_state(db, Platform.X)

    def n(stmt) -> int:  # type: ignore[no-untyped-def]
        return int(db.scalar(stmt) or 0)

    trending = list(db.scalars(select(CommunityTopic).where(CommunityTopic.status == TopicStatus.ACTIVE, CommunityTopic.review_status != "rejected")
                                .order_by(CommunityTopic.count_24h.desc()).limit(5)))
    top_opps = list(db.scalars(select(ContentOpportunity).where(ContentOpportunity.status.in_([OpportunityStatus.DETECTED, OpportunityStatus.REVIEWING, OpportunityStatus.APPROVED]))
                               .order_by(ContentOpportunity.priority.desc()).limit(5)))
    jobs_failing = [r.name for r in db.scalars(select(ScheduledJob).where(ScheduledJob.consecutive_failures > 0))]
    from app.services.pilot import pilot_status

    pilot = await pilot_status(db, runtime)
    return {
        "pilot": pilot,
        "ai": {"online": health.available and health.model_loaded, "model": model, "provider": runtime.provider.name,
               "strong_model": runtime.settings.model_tier_strong, "fast_model": runtime.settings.model_tier_fast,
               "models": pilot["models"], "processing": runtime.gate.status(),
               "jobs": runtime.scheduler.queue() if runtime.scheduler else {"running": None, "waiting": []}},
        "telegram": {"status": runtime.telegram.status if runtime.telegram.configured else "not_configured",
                     "communities": n(select(func.count()).select_from(TelegramSource).where(TelegramSource.enabled.is_(True))),
                     "messages_today": n(select(func.count()).select_from(TelegramMessage).where(TelegramMessage.sent_at >= today)),
                     "topics": n(select(func.count()).select_from(CommunityTopic).where(CommunityTopic.status == TopicStatus.ACTIVE)),
                     "pending_memories": n(select(func.count()).select_from(Memory).where(Memory.layer == "community", Memory.review_status == "pending"))},
        "x": {"status": runtime.x.status if runtime.x.configured else "not_configured", "username": x_state.account_username,
              "monitored_accounts": n(select(func.count()).select_from(WatchedAccount).where(WatchedAccount.enabled.is_(True))),
              "rules": n(select(func.count()).select_from(MonitorRule).where(MonitorRule.enabled.is_(True))),
              "posts_today": n(select(func.count()).select_from(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.created_at >= today)),
              "posts_total": n(select(func.count()).select_from(SocialPost).where(SocialPost.platform == Platform.X)),
              "opportunities": n(select(func.count()).select_from(SocialOpportunity).where(SocialOpportunity.status == SocialOpportunityStatus.NEW))},
        "trending": [{"id": t.id, "name": t.name, "trend": t.trend.value, "messages_24h": t.count_24h,
                      "rising": t.trend in (TopicTrend.RISING, TopicTrend.NEW)} for t in trending],
        "cross_signals": [cross_out(db, s).model_dump(mode="json") for s in db.scalars(
            select(CrossSignal).where(CrossSignal.status == CrossSignalStatus.ACTIVE).order_by(CrossSignal.x_posts_24h.desc()).limit(5))],
        "content": {"opportunities": n(select(func.count()).select_from(ContentOpportunity).where(ContentOpportunity.status.in_([OpportunityStatus.DETECTED, OpportunityStatus.REVIEWING, OpportunityStatus.APPROVED]))),
                    "drafts_awaiting_review": n(select(func.count()).select_from(Content).where(Content.status == ContentStatus.DRAFT, Content.draft_type.is_not(None))),
                    "approvals_pending": n(select(func.count()).select_from(ApprovalRequest).where(ApprovalRequest.status == ApprovalStatus.PENDING)),
                    "top": [{"id": o.id, "title": o.title, "priority": o.priority_label or "unranked", "reasons": (o.reasons or [])[:3], "platforms": o.platforms} for o in top_opps]},
        "attention": {"failing_jobs": jobs_failing},
        "recent_activity": [activity_out(a).model_dump(mode="json") for a in recent_activity(db, limit=10)],
    }


@router.post("/community/questions/{question_id}/opportunity", status_code=status.HTTP_201_CREATED)
def question_to_opportunity(question_id: int, db: DbSession) -> dict[str, object]:
    """Recurring question → content opportunity (from the question's own evidence; no AI needed)."""
    from app.models.community import CommunityQuestion
    from app.models.enums import Urgency

    q = db.get(CommunityQuestion, question_id)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    existing = db.scalar(select(ContentOpportunity).where(ContentOpportunity.origin_kind == "question", ContentOpportunity.origin_id == q.id,
                                                          ContentOpportunity.status != OpportunityStatus.REJECTED))
    if existing is not None:
        return {"id": existing.id, "created": False}
    o = ContentOpportunity(
        title=f"Answer: {q.text[:250]}", why=f"Asked {q.ask_count} times by {q.asker_count} members of the community.",
        angle="A clear, simple explanation that answers the question directly.", evidence={"signal": "question", "asked": q.ask_count, "askers": q.asker_count},
        platforms=["x", "telegram", "youtube_shorts"], urgency=Urgency.HIGH if q.ask_count >= 5 else Urgency.MEDIUM,
        confidence=min(0.9, 0.4 + q.ask_count / 20), status=OpportunityStatus.DETECTED, origin_kind="question", origin_id=q.id,
    )
    db.add(o)
    db.commit()
    return {"id": o.id, "created": True}
