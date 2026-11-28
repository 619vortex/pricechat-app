"""Price Chat Predictions API.

/api/members/predictions/*  the Passport (member session; under the public /api/members/ prefix). 404 while PREDICTIONS_MODE is off; in
                            preview only the members listed in PREDICTIONS_PREVIEW_MEMBERS see it.
/api/predictions/*          Admin › Community › Predictions (owner login): calls, create, post now, resolve by hand, cancel.
The Passport and Telegram share one backend (app/predictions/service.py): a vote made in either is the same vote.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.routes.members import SessionToken, _current, _require
from app.config.settings import get_settings
from app.db.base import utcnow
from app.models.members import Member
from app.models.predictions import PredQuestion, PredVote
from app.predictions import cards, rules, service, settle

router = APIRouter(tags=["predictions"])


def _visible(member: Member | None) -> bool:
    s = get_settings()
    if s.predictions_mode == "live":
        return True
    if s.predictions_mode == "preview" and member is not None:
        allowed = {u.strip().lower().lstrip("@") for u in s.predictions_preview_members.split(",") if u.strip()}
        return (member.username or "").lower() in allowed
    return False


def _gate(db: DbSession, token: str | None, need: bool = False) -> Member | None:
    member = _require(db, token) if need else _current(db, token)
    if not _visible(member):
        raise HTTPException(404, "Not found.")
    service.ensure_catalog(db)
    return member


def _q_out(db: DbSession, q: PredQuestion, member: Member | None, now: datetime) -> dict[str, Any]:
    mine = service.my_vote(db, q, service.Voter(member=member)) if member is not None else None
    t = service.tallies(db, q)
    total = sum(t.values())
    reveal = q.status != "open"  # the split stays hidden until the call locks
    return {"id": q.id, "kind": q.kind, "type": q.qtype, "asset": q.asset, "title": q.title, "options": q.options,
            "ref_price": q.ref_price, "ref_day": q.ref_day.isoformat() if q.ref_day else None, "settle_day": q.settle_day.isoformat(),
            "opens_at": service._aware(q.opens_at).isoformat(), "locks_at": service._aware(q.locks_at).isoformat(),  # type: ignore[union-attr]
            "settles_at": service._aware(q.settles_at).isoformat(), "status": q.status, "outcome": q.outcome,  # type: ignore[union-attr]
            "settle_price": q.settle_price, "void_reason": q.void_reason, "calls": total,
            "split": ({k: (v / total if total else 0.0) for k, v in t.items()} if reveal else None),
            "my_call": mine.option if mine else None,
            "xp": {"play": rules.XP[f"predictions.{q.kind}_play"], "right": rules.XP[f"predictions.{q.kind}_right"]},
            "source": f"Hyperliquid {q.asset} perpetual, daily close (00:00 UTC)"}


@router.get("/members/predictions")
def overview(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _gate(db, pc_session)
    now = utcnow()
    open_qs = service.current(db, now)
    recent = list(db.scalars(select(PredQuestion).where(PredQuestion.status.in_(("locked", "held", "settled", "void")))
                             .order_by(PredQuestion.settles_at.desc()).limit(6)))
    out: dict[str, Any] = {"open": [_q_out(db, q, member, now) for q in open_qs], "recent": [_q_out(db, q, member, now) for q in recent],
                           "signed_in": member is not None, "rules": _rules()}
    if member is not None:
        voter = service.Voter(member=member)
        out["stats"] = service.stats(db, voter, now)
        out["history"] = service.history(db, voter)
    return out


class VoteIn(BaseModel):
    option: str = Field(min_length=1, max_length=16)


@router.post("/members/predictions/{question_id}/vote")
def vote(question_id: int, body: VoteIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _gate(db, pc_session, need=True)
    q = db.get(PredQuestion, question_id)
    if q is None:
        raise HTTPException(404, "No such call.")
    try:
        res = service.vote(db, q, service.Voter(member=member), body.option, "web")
    except service.PredictionError as e:
        raise HTTPException(e.status, str(e)) from None
    return {**res, "question": _q_out(db, q, member, utcnow())}


@router.get("/members/predictions/leaderboard")
def leaderboard(db: DbSession, period: Literal["week", "month", "all"] = Query("week"), pc_session: SessionToken = None) -> dict[str, Any]:
    member = _gate(db, pc_session)
    board = service.leaderboard(db, period, utcnow(), limit=10_000, linked_only=True)
    me = next((r for r in board["rows"] if member is not None and r["voter_key"] == f"m:{member.id}"), None)
    rows = [{k: r[k] for k in ("position", "name", "points", "right", "decided", "calls", "accuracy")} for r in board["rows"][:50]]
    return {"period": period, "since": board["since"], "min_calls": board["min_calls"], "players": board["players"], "rows": rows,
            "me": {k: me[k] for k in ("position", "points", "right", "decided", "calls", "accuracy")} if me else None}


@router.get("/members/predictions/card.png")
def my_card(db: DbSession, fmt: Literal["portrait", "landscape"] = Query("portrait"), pc_session: SessionToken = None) -> Response:
    """The member's shareable card: this week's calls (a dot each), accuracy, streak and Predictor level."""
    member = _gate(db, pc_session, need=True)
    assert member is not None
    now = utcnow()
    voter = service.Voter(member=member)
    st = service.stats(db, voter, now)
    start = service.period_start("week", now)
    week = [h for h in service.history(db, voter, limit=40) if h["settle_day"] >= start.isoformat() and h["kind"] == "daily"]  # type: ignore[union-attr]
    week.reverse()
    right = sum(1 for h in week if h["result"] == "right")
    decided = sum(1 for h in week if h["result"] in ("right", "wrong"))
    png = cards.share_card({"name": f"@{member.username}", "level": st["level"]["name"],
                            "kicker": "Called it" if decided and right / decided >= 0.5 else "This week",
                            "headline": f"{right} of {decided}" if decided else f"{st['calls']} calls",
                            "sub": (f"Week {now.isocalendar()[1]} · #{st['week_position']} of {st['week_players']} callers" if st["week_position"]
                                    else f"Week {now.isocalendar()[1]} · Price Chat Predictions"),
                            "dots": [h["result"] == "right" if h["result"] in ("right", "wrong") else None for h in week],
                            "period_label": f"{get_settings().predictions_site_url.removeprefix('https://')}/u/{member.username}",
                            "accuracy_label": f"{st['accuracy']:.0%}" if st["accuracy"] is not None else "—", "streak": st["streak"]},
                           portrait=fmt == "portrait")
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=120",
                                                          "Content-Disposition": f'inline; filename="price-chat-predictions-{fmt}.png"'})


def _rules() -> dict[str, Any]:
    return {"daily": "Opens 13:30 UTC, locks 20:00 UTC, settles on the Hyperliquid BTC perpetual's daily close (00:00 UTC) against the day before.",
            "weekly": "Opens Monday 13:30 UTC, locks Wednesday 23:59 UTC, settles on Sunday's close. A boundary price belongs to the range above it.",
            "settlement": "The closed candle is read twice, 10 minutes apart; the call settles only when both reads match.",
            "tie": "A close exactly equal to the reference is a tie: no winner, taking part still earns its XP, streaks are unaffected.",
            "missing": "No verified close 6 hours after the close: the call is held and resolved by hand with a logged reason, or cancelled.",
            "cancelled": "A cancelled call earns no XP; XP already written for it is reversed.",
            "xp": "Predictor XP builds your Predictor level and achievements only. It never counts towards the Kiwi rank or any reward with money value.",
            "boards": f"Calendar week (Mon-Sun UTC), month and all time, by prediction points. Minimum settled calls: "
                      f"{rules.MIN_CALLS['week']} / {rules.MIN_CALLS['month']} / {rules.MIN_CALLS['all']}.",
            "money": "Free to play. No entry fees, no stakes, no cash prizes. Not financial advice."}


# --- admin -------------------------------------------------------------------------------------------------------------------------

admin = APIRouter(prefix="/predictions", tags=["predictions-admin"])


@admin.get("/questions")
def admin_list(db: DbSession, limit: int = Query(40, ge=1, le=200)) -> dict[str, Any]:
    s = get_settings()
    now = utcnow()
    rows = list(db.scalars(select(PredQuestion).order_by(PredQuestion.settles_at.desc()).limit(limit)))
    out = []
    for q in rows:
        t = service.tallies(db, q)
        out.append({**_q_out(db, q, None, now), "split": dict(t), "evidence": q.evidence, "audit": q.audit,
                    "tg_chat": q.tg_chat, "tg_message_id": q.tg_message_id, "created_by": q.created_by, "votes_by": _by_via(db, q.id)})
    return {"questions": out, "mode": s.predictions_mode, "group": s.predictions_group, "preview_chat": bool(s.predictions_preview_chat),
            "preview_members": [u.strip() for u in s.predictions_preview_members.split(",") if u.strip()], "rules": _rules(),
            "players_week": service.leaderboard(db, "week", now, limit=0)["players"]}


def _by_via(db: DbSession, qid: int) -> dict[str, int]:
    out: dict[str, int] = {}
    for via, status in db.execute(select(PredVote.via, PredVote.status).where(PredVote.question_id == qid)):
        k = via if status == "counted" else f"{via} ({status})"
        out[k] = out.get(k, 0) + 1
    return out


class CreateIn(BaseModel):
    kind: Literal["daily", "weekly"]
    day: date  # the daily call's day, or the weekly call's Monday


@admin.post("/questions")
def admin_create(body: CreateIn, db: DbSession) -> dict[str, Any]:
    """Create a call by hand for a day (the tick creates them on schedule; this is for a missed day or a test). The reference close is
    read from Hyperliquid now."""
    asset = rules.ASSETS[0]
    if body.kind == "weekly" and body.day.weekday() != 0:
        raise HTTPException(422, "A weekly call starts on a Monday.")
    slot = service.daily_slot(asset, body.day) if body.kind == "daily" else service.weekly_slot(asset, body.day)
    if db.scalar(select(PredQuestion.id).where(PredQuestion.slot == slot)) is not None:
        raise HTTPException(409, "That call already exists.")
    ref = settle.reference_close(asset, body.day - timedelta(days=1))
    if ref is None:
        raise HTTPException(503, "The reference close couldn't be read from Hyperliquid. Try again in a minute.")
    q = service.make_daily(asset, body.day, ref) if body.kind == "daily" else service.make_weekly(asset, body.day, ref)
    q.created_by = "owner"
    service._audit(q, "owner", "created", utcnow(), ref=ref)
    db.add(q)
    db.commit()
    return _q_out(db, q, None, utcnow())


class ResolveIn(BaseModel):
    close: float = Field(gt=0)
    reason: str = Field(min_length=5, max_length=300)


@admin.post("/questions/{qid}/resolve")
def admin_resolve(qid: int, body: ResolveIn, db: DbSession) -> dict[str, Any]:
    """Resolve a held (or locked) call by hand from a close you have checked: the reason is logged on the call."""
    q = db.get(PredQuestion, qid)
    if q is None:
        raise HTTPException(404, "No such call.")
    if q.status not in ("locked", "held"):
        raise HTTPException(409, "Only a locked or held call can be resolved by hand.")
    try:
        service.finalize(db, q, body.close, utcnow(), how="owner", who="owner", reason=body.reason)
    except service.PredictionError as e:
        raise HTTPException(e.status, str(e)) from None
    return _q_out(db, q, None, utcnow())


class CancelIn(BaseModel):
    reason: str = Field(min_length=5, max_length=300)


@admin.post("/questions/{qid}/cancel")
def admin_cancel(qid: int, body: CancelIn, db: DbSession) -> dict[str, Any]:
    q = db.get(PredQuestion, qid)
    if q is None:
        raise HTTPException(404, "No such call.")
    try:
        out = service.cancel(db, q, body.reason, "owner")
    except service.PredictionError as e:
        raise HTTPException(e.status, str(e)) from None
    return {**_q_out(db, q, None, utcnow()), **out}


@admin.get("/questions/{qid}/card.png")
def admin_card(qid: int, db: DbSession) -> Response:
    """The group post's image for a call (what Telegram shows)."""
    from app.predictions import telegram as tg

    q = db.get(PredQuestion, qid)
    if q is None:
        raise HTTPException(404, "No such call.")
    return Response(tg.card_png(q), media_type="image/png")


@admin.get("/preview/telegram")
def admin_preview(db: DbSession) -> dict[str, Any]:
    """The exact group caption and buttons of the newest call, for review before activation."""
    from app.predictions import telegram as tg

    q = db.scalar(select(PredQuestion).order_by(PredQuestion.settles_at.desc()).limit(1))
    if q is None:
        return {"question": None}
    return {"question": q.id, "caption_html": tg.caption(db, q, utcnow()), "buttons": tg.vote_buttons(q) if q.status == "open" else tg.info_buttons(),
            "rules": tg.RULES_TEXT, "now": datetime.now(timezone.utc).isoformat()}


router.include_router(admin)
