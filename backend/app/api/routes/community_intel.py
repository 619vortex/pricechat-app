"""Community Intelligence API. 404 everywhere while COMMUNITY_INTEL_MODE is off (and, in preview, for members not on the list).

Members (under the public /api/members/ prefix, member session):
  GET  /members/intel                     my tips, track record, limits
  POST /members/intel/check               the form's live checks (source, kind, duplicates, flags); writes nothing
  POST /members/intel                     send a tip
  POST /members/intel/{id}/disclosure     answer the conflict-of-interest question (required before review)
  POST /members/intel/{id}/answer         reply to the desk's question
  POST /members/intel/{id}/appeal         appeal a rejection or a duplicate verdict (once, within 14 days)
  POST /members/intel/{id}/withdraw
  GET  /members/intel/leaderboard         Researcher boards: week, month, all time
Editor (owner login, the Terminal's Community lane):
  GET  /community-intel/queue, GET /community-intel/{id}, POST /community-intel/{id}/decide, POST /community-intel/{id}/appeal
Nothing here publishes anything.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field
from sqlalchemy import func, select

from app.api.deps import DbSession
from app.api.routes.members import SessionToken, _current, _require
from app.api.schemas import StrictInput
from app.community_intel import rules
from app.community_intel import service as ci
from app.db.base import utcnow
from app.models.community_intel import CiEvent, CiSubmission
from app.models.members import Member

router = APIRouter(tags=["community-intel"])
EDITOR = "owner"


def _member(db: DbSession, token: str | None) -> Member:
    m = _require(db, token)
    if not ci.visible(m):
        raise HTTPException(404, "Not found.")
    return m


def _mine(db: DbSession, m: Member, sid: int) -> CiSubmission:
    s = db.get(CiSubmission, sid)
    if s is None or s.member_id != m.id:
        raise HTTPException(404, "No such tip.")
    return s


def _fail(exc: ci.CiError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("/members/intel")
def mine(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _member(db, pc_session)
    rows = list(db.scalars(select(CiSubmission).where(CiSubmission.member_id == m.id).order_by(CiSubmission.created_at.desc()).limit(50)))
    level = ci.level_of(db, m)
    day = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = int(db.scalar(select(func.count()).select_from(CiSubmission).where(CiSubmission.member_id == m.id, CiSubmission.created_at >= day)) or 0)
    return {"tips": [ci.out(s) for s in rows], "record": ci.record(db, m), "limit": {"per_day": rules.PER_DAY.get(level, 2), "sent_today": sent_today, "level": level},
            "kinds": [{"key": k, "label": rules.KIND_LABEL[k]} for k in rules.KINDS], "points": rules.POINTS,
            "bot": "@CryptoPriceChatBot"}


class CheckIn(StrictInput):
    url: str = Field("", max_length=600)
    note: str = Field("", max_length=1200)


@router.post("/members/intel/check")
def check(body: CheckIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _member(db, pc_session)
    url = (body.url or ci.find_url(body.note)).strip()
    canon = ci.canonical(url) if url else ""
    host = ci.host_of(canon) if canon else ""
    src = ci.source_class(host) if url else {"class": "none", "label": "No link", "outlet": ""}
    if url and not canon:
        src = {"class": "invalid", "label": "Not a web address", "outlet": ""}
    tick = ci.tickers_in(body.note)
    out: dict[str, Any] = {"source": src, "kind": ci.guess_kind(host, body.note), "tickers": tick,
                           "flags": ci.safety_flags(m, body.note, tick, src["class"], utcnow())}
    if canon:
        prev = db.scalar(select(CiSubmission).where(CiSubmission.canonical_url == canon, CiSubmission.status != "withdrawn",
                                                    CiSubmission.created_at >= utcnow() - rules.DUP_WINDOW).order_by(CiSubmission.created_at).limit(1))
        if prev is not None:
            out["duplicate"] = {"mine": prev.member_id == m.id, "ref": ci.ref(prev) if prev.member_id == m.id else None}
    return out


class SubmitIn(StrictInput):
    url: str = Field("", max_length=600)
    note: str = Field("", max_length=1200)
    title: str = Field("", max_length=rules.TITLE_MAX)
    kind: Literal["news", "research", "anomaly", "discovery"] | None = None
    disclosure: Literal["none", "holds"]  # required on the Passport form
    credit: bool = True


@router.post("/members/intel")
def send(body: SubmitIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _member(db, pc_session)
    try:
        s = ci.submit(db, m, via="web", text=body.note, url=body.url, title=body.title, kind=body.kind, disclosure=body.disclosure, credit=body.credit)
    except ci.CiError as exc:
        db.rollback()
        raise _fail(exc) from exc
    return {"tip": ci.out(s)}


class DisclosureIn(StrictInput):
    value: Literal["none", "holds"]
    credit: bool | None = None


@router.post("/members/intel/{sid}/disclosure")
def disclose(sid: int, body: DisclosureIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _member(db, pc_session)
    s = _mine(db, m, sid)
    ci.set_disclosure(db, s, body.value, body.credit)
    return {"tip": ci.out(s)}


class AnswerIn(StrictInput):
    text: str = Field("", max_length=rules.NOTE_MAX)
    url: str = Field("", max_length=600)


@router.post("/members/intel/{sid}/answer")
def reply(sid: int, body: AnswerIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _member(db, pc_session)
    s = _mine(db, m, sid)
    try:
        ci.answer(db, s, body.text, body.url)
    except ci.CiError as exc:
        raise _fail(exc) from exc
    return {"tip": ci.out(s)}


class AppealIn(StrictInput):
    text: str = Field(min_length=1, max_length=rules.APPEAL_MAX)


@router.post("/members/intel/{sid}/appeal")
def appeal(sid: int, body: AppealIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _member(db, pc_session)
    s = _mine(db, m, sid)
    try:
        ci.appeal(db, s, body.text)
    except ci.CiError as exc:
        raise _fail(exc) from exc
    return {"tip": ci.out(s)}


@router.post("/members/intel/{sid}/withdraw")
def withdraw(sid: int, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _member(db, pc_session)
    s = _mine(db, m, sid)
    try:
        ci.withdraw(db, s)
    except ci.CiError as exc:
        raise _fail(exc) from exc
    return {"tip": ci.out(s)}


@router.get("/members/intel/leaderboard")
def board(db: DbSession, period: Literal["week", "month", "all"] = Query("week"), pc_session: SessionToken = None) -> dict[str, Any]:
    viewer = _current(db, pc_session)
    if ci.mode() == "off" or (ci.mode() == "preview" and not ci.visible(viewer)):
        raise HTTPException(404, "Not found.")
    b = ci.leaderboard(db, period)
    me = next((r for r in b["rows"] if viewer is not None and r["member_id"] == viewer.id), None)
    rows = [{k: r[k] for k in ("position", "username", "name", "points", "accepted", "published", "first_spotted")} for r in b["rows"]]
    return {"period": period, "since": b["since"], "rows": rows, "me": {k: me[k] for k in ("position", "points")} if me else None}


# --- the editor (owner login: every path outside /api/members/ is behind it) ----------------------------------------------------------


def _editor_on() -> None:
    if ci.mode() == "off":
        raise HTTPException(404, "Community Intelligence is off.")


def _with_member(db: DbSession, s: CiSubmission) -> dict[str, Any]:
    m = db.get(Member, s.member_id)
    o = ci.out(s, editor=True, member=m)
    if m is not None:
        from app.passport import service as passport

        rec = ci.record(db, m)
        o["record"] = rec
        o["member"]["community"] = passport.community_score(db, m)["level"]["name"]
    return o


FILTERS = {"review": ("review",), "needs_info": ("needs_info",), "checking": ("checking",), "duplicate": ("duplicate",),
           "decided": ("accepted", "merged", "rejected")}


@router.get("/community-intel/queue")
def queue(db: DbSession, filter: str = Query("review"), limit: int = Query(80, ge=1, le=300)) -> dict[str, Any]:
    _editor_on()
    q = select(CiSubmission).order_by(CiSubmission.created_at.desc())
    if filter == "appeals":
        q = q.where(CiSubmission.appeal_status == "open")
    elif filter == "flagged":
        q = q.where(CiSubmission.status.in_(rules.OPEN))
    elif filter in FILTERS:
        q = q.where(CiSubmission.status.in_(FILTERS[filter]))
    rows = list(db.scalars(q.limit(limit if filter != "flagged" else 400)))
    if filter == "flagged":
        rows = [r for r in rows if (r.checks or {}).get("flags")][:limit]
    counts = dict(db.execute(select(CiSubmission.status, func.count()).group_by(CiSubmission.status)).all())
    appeals = int(db.scalar(select(func.count()).select_from(CiSubmission).where(CiSubmission.appeal_status == "open")) or 0)
    return {"items": [_with_member(db, s) for s in rows], "counts": {**{k: int(v) for k, v in counts.items()}, "appeals": appeals},
            "reasons": rules.REASONS, "mode": ci.mode()}


@router.get("/community-intel/{sid}")
def detail(sid: int, db: DbSession) -> dict[str, Any]:
    _editor_on()
    s = db.get(CiSubmission, sid)
    if s is None:
        raise HTTPException(404, "No such tip.")
    events = [{"at": e.created_at.isoformat(), "actor": e.actor, "action": e.action, "note": e.note}
              for e in db.scalars(select(CiEvent).where(CiEvent.submission_id == s.id).order_by(CiEvent.created_at, CiEvent.id))]
    return {**_with_member(db, s), "events": events}


class DecideIn(StrictInput):
    action: Literal["accept", "merge", "ask", "reject", "reopen"]
    reason: str | None = Field(None, max_length=48)
    note: str | None = Field(None, max_length=rules.EDITOR_NOTE_MAX)
    story_key: str | None = Field(None, max_length=200)
    new_info: bool = False
    data_checked: bool = False
    fact_check: bool = False


@router.post("/community-intel/{sid}/decide")
def decide(sid: int, body: DecideIn, db: DbSession) -> dict[str, Any]:
    _editor_on()
    s = db.get(CiSubmission, sid)
    if s is None:
        raise HTTPException(404, "No such tip.")
    try:
        ci.decide(db, s, body.action, EDITOR, reason=body.reason, note=body.note, story_key=body.story_key, new_info=body.new_info,
                  data_checked=body.data_checked, fact_check=body.fact_check)
    except ci.CiError as exc:
        db.rollback()
        raise _fail(exc) from exc
    return _with_member(db, s)


class AppealDecisionIn(StrictInput):
    uphold: bool
    note: str = Field("", max_length=rules.EDITOR_NOTE_MAX)


@router.post("/community-intel/{sid}/appeal")
def decide_appeal(sid: int, body: AppealDecisionIn, db: DbSession) -> dict[str, Any]:
    _editor_on()
    s = db.get(CiSubmission, sid)
    if s is None:
        raise HTTPException(404, "No such tip.")
    try:
        ci.decide_appeal(db, s, body.uphold, EDITOR, body.note)
    except ci.CiError as exc:
        raise _fail(exc) from exc
    return _with_member(db, s)
