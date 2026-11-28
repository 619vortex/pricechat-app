"""Price Chat Predictions: one backend for Telegram and the Passport.

A call (PredQuestion) is created at its opening time, takes votes until it locks, and settles from Hyperliquid's daily close (settle.py).
A vote (PredVote) is one row per voter per call: "m:<member>" for a Passport member (wherever they voted), "tg:<user>" for a Telegram
member who hasn't linked a Passport. Linking re-keys the Telegram votes to the member; when both exist for one call, the earlier vote
counts and the other is kept as a duplicate (no XP).

XP is written only for linked members, after settlement, through members/xp.award() (category "predictor", never the Kiwi rank): each
reward has its own source_ref ("pred:<question>:right"), so the ledger's UNIQUE (member, event, source_ref) pays it once, and a Telegram
vote's reward also carries a claim_key per Telegram account, so it can't be claimed again by a second Passport. Settled votes not yet
written are the member's pending XP (unlinked, or a Passport younger than a day); credit() writes them as soon as it can.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.members import LinkedIdentity, Member, ReputationEvent
from app.models.predictions import PredQuestion, PredTgUser, PredVote
from app.predictions import rules

log = logging.getLogger(__name__)
OPEN = ("open",)
_catalog_ok = False


class PredictionError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _aware(t: datetime | None) -> datetime | None:
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def at(d: date, t: Any) -> datetime:
    return datetime.combine(d, t, tzinfo=timezone.utc)


# --- catalog ---------------------------------------------------------------------------------------------------------------------


def ensure_catalog(db: Session) -> None:
    """The prediction XP rules (category "predictor") and achievements, inserted once; the dashboard owns them afterwards."""
    global _catalog_ok
    if _catalog_ok:
        return
    from app.models.members import Badge, XPRule

    have = set(db.scalars(select(XPRule.event_type).where(XPRule.event_type.like("predictions.%"))))
    for r in rules.XP_RULES:
        if r["event_type"] not in have:
            db.add(XPRule(event_type=r["event_type"], label=r["label"], description="Price Chat Predictions (Predictor progression only)",
                          xp=r["xp"], daily_cap=r["daily_cap"], max_per_member=1 if r["event_type"] == "predictions.first_call" else None,
                          min_member_age_hours=rules.MIN_MEMBER_AGE_HOURS, enabled=True, verification_source=rules.SOURCE,
                          availability="available", category=rules.CATEGORY,
                          availability_note="Predictor XP: never counts towards the Kiwi rank or any reward with money value."))
    # a rule someone moved to another category in the dashboard is put back: prediction XP must never reach the rank
    for rule in db.scalars(select(XPRule).where(XPRule.event_type.like("predictions.%"), XPRule.category != rules.CATEGORY)):
        rule.category = rules.CATEGORY
    have_b = set(db.scalars(select(Badge.key).where(Badge.key.like("pred_%"))))
    for b in rules.BADGES:
        if b["key"] not in have_b:
            db.add(Badge(key=b["key"], name=b["name"], description=b["description"], kind="auto", criteria=b["criteria"], enabled=True))
    db.commit()
    _catalog_ok = True


# --- questions ---------------------------------------------------------------------------------------------------------------------


def money(v: float) -> str:
    return f"${v:,.0f}"


def kmoney(v: float) -> str:
    return f"${v / 1000:,.1f}k"


def daily_slot(asset: str, d: date) -> str:
    return f"daily:{asset}:{d.isoformat()}"


def weekly_slot(asset: str, monday: date) -> str:
    return f"weekly:{asset}:{monday.isoformat()}"


def make_daily(asset: str, d: date, ref_close: float) -> PredQuestion:
    return PredQuestion(
        slot=daily_slot(asset, d), kind="daily", qtype="direction", asset=asset, title=f"Will {name(asset)} close today higher?",
        options=[{"key": "up", "label": "Higher"}, {"key": "down", "label": "Lower"}], ref_price=ref_close, ref_day=d - timedelta(days=1),
        settle_day=d, opens_at=at(d, rules.DAILY_OPEN), locks_at=at(d, rules.DAILY_LOCK), settles_at=at(d + timedelta(days=1), datetime.min.time()),
        status="open", evidence={}, audit=[])


def range_options(ref: float) -> list[dict[str, Any]]:
    lo = round(ref * (1 - rules.RANGE_STEP) / rules.RANGE_ROUND) * rules.RANGE_ROUND
    mid = round(ref / rules.RANGE_ROUND) * rules.RANGE_ROUND
    hi = round(ref * (1 + rules.RANGE_STEP) / rules.RANGE_ROUND) * rules.RANGE_ROUND
    return [{"key": "r1", "label": f"Under {kmoney(lo)}", "lo": None, "hi": lo},
            {"key": "r2", "label": f"{kmoney(lo)}–{kmoney(mid)[1:]}", "lo": lo, "hi": mid},
            {"key": "r3", "label": f"{kmoney(mid)}–{kmoney(hi)[1:]}", "lo": mid, "hi": hi},
            {"key": "r4", "label": f"Over {kmoney(hi)}", "lo": hi, "hi": None}]


def make_weekly(asset: str, monday: date, ref_close: float) -> PredQuestion:
    sunday = monday + timedelta(days=6)
    return PredQuestion(
        slot=weekly_slot(asset, monday), kind="weekly", qtype="range", asset=asset, title=f"Where does {name(asset)} close Sunday?",
        options=range_options(ref_close), ref_price=ref_close, ref_day=monday - timedelta(days=1), settle_day=sunday,
        opens_at=at(monday, rules.WEEKLY_OPEN), locks_at=at(monday + timedelta(days=rules.WEEKLY_LOCK_WEEKDAY), rules.WEEKLY_LOCK),
        settles_at=at(sunday + timedelta(days=1), datetime.min.time()), status="open", evidence={}, audit=[])


def name(asset: str) -> str:
    return {"BTC": "Bitcoin", "ETH": "Ether", "SOL": "Solana"}.get(asset, asset)


def outcome_for(q: PredQuestion, close: float) -> str:
    """The winning option for a settlement close. An exact tie with the reference (direction) is "tie": the call is void for
    rightness, participation XP still pays. A range boundary belongs to the range above it (lo inclusive, hi exclusive)."""
    if q.qtype == "direction":
        if q.ref_price is None:
            raise ValueError("a direction call needs its reference close")
        return "tie" if close == q.ref_price else ("up" if close > q.ref_price else "down")
    for o in q.options:
        lo, hi = o.get("lo"), o.get("hi")
        if (lo is None or close >= lo) and (hi is None or close < hi):
            return str(o["key"])
    raise ValueError("no range holds the close")


def label_of(q: PredQuestion, key: str | None) -> str:
    return next((str(o["label"]) for o in q.options if o["key"] == key), "Tie" if key == "tie" else str(key or ""))


def current(db: Session, now: datetime | None = None) -> list[PredQuestion]:
    """The calls taking votes now (open, not past their lock)."""
    now = now or utcnow()
    return [q for q in db.scalars(select(PredQuestion).where(PredQuestion.status == "open").order_by(PredQuestion.locks_at))
            if _aware(q.locks_at) > now]  # type: ignore[operator]


def tallies(db: Session, q: PredQuestion) -> dict[str, int]:
    rows = db.execute(select(PredVote.option, func.count()).where(PredVote.question_id == q.id, PredVote.status == "counted")
                      .group_by(PredVote.option))
    out = {str(o["key"]): 0 for o in q.options}
    for opt, n in rows:
        out[str(opt)] = int(n)
    return out


def _audit(q: PredQuestion, who: str, what: str, now: datetime, **extra: Any) -> None:
    q.audit = [*(q.audit or []), {"at": now.isoformat(), "who": who, "what": what, **extra}][-50:]


# --- voting ------------------------------------------------------------------------------------------------------------------------


@dataclass
class Voter:
    member: Member | None = None
    tg_user_id: int | None = None

    @property
    def key(self) -> str:
        return f"m:{self.member.id}" if self.member is not None else f"tg:{self.tg_user_id}"


def member_for_tg(db: Session, tg_user_id: int) -> Member | None:
    ident = db.scalar(select(LinkedIdentity).where(LinkedIdentity.platform == "telegram", LinkedIdentity.platform_user_id == str(tg_user_id),
                                                   LinkedIdentity.status == "active"))
    return db.get(Member, ident.member_id) if ident is not None else None


def tg_for_member(db: Session, member: Member) -> int | None:
    ident = db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == member.id, LinkedIdentity.platform == "telegram",
                                                   LinkedIdentity.status == "active"))
    return int(ident.platform_user_id) if ident is not None and str(ident.platform_user_id).lstrip("-").isdigit() else None


def vote(db: Session, q: PredQuestion, voter: Voter, option: str, via: str, now: datetime | None = None) -> dict[str, Any]:
    """Record (or change) one voter's call. Refuses after the lock, an unknown option, a banned member. Returns
    {"changed": bool, "option": key, "label": str, "first": bool}. The vote time is the server's."""
    now = now or utcnow()
    if q.status != "open" or now >= _aware(q.locks_at):  # type: ignore[operator]
        raise PredictionError(409, "Voting on this call has closed.")
    if option not in {str(o["key"]) for o in q.options}:
        raise PredictionError(422, "That isn't one of this call's options.")
    if voter.member is not None and voter.member.status != "active":
        raise PredictionError(403, "This account can't take part.")
    if voter.member is None and not voter.tg_user_id:
        raise PredictionError(401, "Sign in to make a call.")
    tg = voter.tg_user_id or (tg_for_member(db, voter.member) if voter.member is not None else None)
    v = db.scalar(select(PredVote).where(PredVote.question_id == q.id, PredVote.voter_key == voter.key))
    if v is None:
        v = PredVote(question_id=q.id, voter_key=voter.key, member_id=voter.member.id if voter.member else None, tg_user_id=tg,
                     option=option, via=via, voted_at=now, changes=0, status="counted")
        db.add(v)
        db.commit()
        return {"changed": False, "first": True, "option": option, "label": label_of(q, option)}
    if v.option == option:
        return {"changed": False, "first": False, "option": option, "label": label_of(q, option)}
    v.option, v.changed_at, v.changes, v.via = option, now, v.changes + 1, via
    db.commit()
    return {"changed": True, "first": False, "option": option, "label": label_of(q, option)}


def my_vote(db: Session, q: PredQuestion, voter: Voter) -> PredVote | None:
    return db.scalar(select(PredVote).where(PredVote.question_id == q.id, PredVote.voter_key == voter.key, PredVote.status == "counted"))


def on_telegram_linked(db: Session, member: Member, tg_user_id: int) -> dict[str, int]:
    """A Telegram account was just linked to a Passport (members/service.link_identity): its votes become the member's. Where both voted
    on one call, the earlier vote counts and the other stays as a duplicate. Pending XP is then written by credit()."""
    moved = dups = 0
    for v in db.scalars(select(PredVote).where(PredVote.voter_key == f"tg:{tg_user_id}")):
        mine = db.scalar(select(PredVote).where(PredVote.question_id == v.question_id, PredVote.voter_key == f"m:{member.id}"))
        if mine is not None:
            later, keep = (v, mine) if _aware(v.voted_at) >= _aware(mine.voted_at) else (mine, v)  # type: ignore[operator]
            later.status, later.voter_key = "duplicate", f"dup:{later.id}"
            db.flush()
            dups += 1
            if keep is mine:
                mine.tg_user_id = mine.tg_user_id or tg_user_id
                continue
        v.voter_key, v.member_id = f"m:{member.id}", member.id
        db.flush()
        moved += 1
    tu = db.scalar(select(PredTgUser).where(PredTgUser.tg_user_id == tg_user_id))
    if tu is not None and tu.note != f"linked:{member.id}":
        tu.note = f"linked:{member.id}"
    db.flush()
    return {"moved": moved, "duplicates": dups}


# --- settlement and XP ---------------------------------------------------------------------------------------------------------------


def finalize(db: Session, q: PredQuestion, close: float, now: datetime, how: str, who: str = "system", reason: str = "") -> None:
    """Settle a call on a verified close (or the owner's resolution, with a reason)."""
    if q.status in ("settled", "void"):
        raise PredictionError(409, f"This call is already {q.status}.")
    out = outcome_for(q, close)
    q.outcome, q.settle_price, q.status, q.settled_at = out, close, "settled", now
    q.evidence = {**(q.evidence or {}), "how": how, "close": close, **({"reason": reason} if reason else {})}
    _audit(q, who, "settled", now, outcome=out, close=close, how=how, **({"reason": reason} if reason else {}))
    db.commit()


def cancel(db: Session, q: PredQuestion, reason: str, who: str, now: datetime | None = None) -> dict[str, int]:
    """Void a call: nobody gets XP for it. A call already settled has its XP reversed through the ledger (negative rows)."""
    from app.members import xp as ledger

    now = now or utcnow()
    if q.status == "void":
        raise PredictionError(409, "This call is already cancelled.")
    if not reason.strip():
        raise PredictionError(422, "Say why the call is cancelled (it is logged and shown).")
    reversed_n = 0
    for ev in db.scalars(select(ReputationEvent).where(ReputationEvent.source_ref.like(f"pred:{q.id}:%"), ReputationEvent.reverses_id.is_(None),
                                                      ReputationEvent.reversed_at.is_(None), ReputationEvent.xp > 0)):
        ledger.reverse(db, ev, actor=who, reason=f"Prediction cancelled: {reason}")
        reversed_n += 1
    q.status, q.void_reason = "void", reason.strip()[:300]
    _audit(q, who, "cancelled", now, reason=reason.strip()[:300], reversed=reversed_n)
    db.commit()
    return {"reversed": reversed_n}


def _settled_votes(db: Session, voter_key: str) -> list[tuple[PredVote, PredQuestion]]:
    return list(db.execute(select(PredVote, PredQuestion).join(PredQuestion, PredQuestion.id == PredVote.question_id).where(
        PredVote.voter_key == voter_key, PredVote.status == "counted", PredQuestion.status == "settled").order_by(PredQuestion.settles_at)))


def right_streak(pairs: list[tuple[PredVote, PredQuestion]]) -> tuple[int, int, int | None]:
    """(current streak, best streak, id of the call that started the current one). A tie neither breaks nor extends a streak."""
    cur = best = 0
    start: int | None = None
    for v, q in pairs:
        if q.outcome == "tie":
            continue
        if v.option == q.outcome:
            if cur == 0:
                start = q.id
            cur += 1
            best = max(best, cur)
        else:
            cur, start = 0, None
    return cur, best, start


def share_for(db: Session, q: PredQuestion) -> float | None:
    t = tallies(db, q)
    total = sum(t.values())
    return (t.get(str(q.outcome), 0) / total) if total else None


def events_for(db: Session, v: PredVote, q: PredQuestion, history: list[tuple[PredVote, PredQuestion]]) -> list[tuple[str, str]]:
    """The (event_type, source_ref) a settled vote earns. `history`: the voter's settled votes up to and including this one."""
    out = [(f"predictions.{q.kind}_play", f"pred:{q.id}:play")]
    if len(history) == 1:
        out.append(("predictions.first_call", "pred:first"))
    if q.outcome == "tie" or v.option != q.outcome:
        return out
    out.append((f"predictions.{q.kind}_right", f"pred:{q.id}:right"))
    share = share_for(db, q)
    if share is not None and share < rules.CONTRARIAN_SHARE:
        out.append(("predictions.contrarian", f"pred:{q.id}:crowd"))
    cur, _best, start = right_streak(history)
    for n, ev in rules.STREAKS:
        if cur == n and start is not None:
            out.append((ev, f"pred:streak{n}:{start}"))
    if q.kind == "daily":
        iso = q.settle_day.isocalendar()
        week = [(hv, hq) for hv, hq in history if hq.kind == "daily" and hq.settle_day.isocalendar()[:2] == iso[:2]]
        if len(week) == 7 and all(hv.option == hq.outcome for hv, hq in week):
            out.append(("predictions.perfect_week", f"pred:perfect:{iso[0]}-W{iso[1]:02d}"))
        days = sorted({hq.settle_day for _, hq in history if hq.kind == "daily"})
        run = 1
        for k in range(len(days) - 1, 0, -1):
            if (days[k] - days[k - 1]).days != 1:
                break
            run += 1
        if run >= 30 and days and days[-1] == q.settle_day and run == 30:
            out.append(("predictions.regular_30", f"pred:regular:{days[-30].isoformat()}"))
    return out


def credit(db: Session, now: datetime | None = None, limit: int = 500) -> dict[str, int]:
    """Write the XP of settled votes that belong to a Passport and haven't been written yet. A Passport younger than a day keeps its
    XP pending (tried again on the next run); everything else is final once tried (a duplicate or a cap is not retried)."""
    from app.members import xp as ledger

    now = now or utcnow()
    ensure_catalog(db)
    todo = list(db.execute(select(PredVote, PredQuestion).join(PredQuestion, PredQuestion.id == PredVote.question_id).where(
        PredVote.member_id.is_not(None), PredVote.status == "counted", PredVote.credited_at.is_(None), PredVote.voter_key.like("m:%"),
        PredQuestion.status == "settled").order_by(PredQuestion.settles_at, PredVote.id).limit(limit)))
    paid = waiting = 0
    for v, q in todo:
        member = db.get(Member, v.member_id)
        if member is None:
            continue
        history = [(hv, hq) for hv, hq in _settled_votes(db, v.voter_key) if _aware(hq.settles_at) <= _aware(q.settles_at)]  # type: ignore[operator]
        too_new = False
        for event_type, ref in events_for(db, v, q, history):
            claim = f"tg:{v.tg_user_id}:{ref}" if v.tg_user_id and ref != "pred:first" else (f"tg:{v.tg_user_id}:first" if v.tg_user_id else None)
            res = ledger.award(db, member, event_type, ref, reason=f"Predictions: {label_of(q, v.option)} on {q.title}"[:256],
                               occurred_at=_aware(q.settled_at) or now, claim_key=claim, commit=False, source=rules.SOURCE)
            if res.refused == "too_new":
                too_new = True
                break
            paid += int(res.xp or 0)
        if too_new:
            waiting += 1
            db.rollback()
            continue
        v.credited_at = now
        db.commit()
    return {"paid_xp": paid, "waiting": waiting, "votes": len(todo)}


# --- stats and boards ----------------------------------------------------------------------------------------------------------------


def predictor_xp(db: Session, member: Member) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(ReputationEvent.xp), 0)).where(
        ReputationEvent.member_id == member.id, ReputationEvent.category == rules.CATEGORY)) or 0)


def pending_xp(db: Session, voter_key: str) -> int:
    """XP a voter has earned but not had written (unlinked, or a Passport younger than a day): plays, rights and crowd bonuses."""
    total = 0
    for v, q in _settled_votes(db, voter_key):
        if v.credited_at is not None:
            continue
        total += rules.XP[f"predictions.{q.kind}_play"]
        if q.outcome != "tie" and v.option == q.outcome:
            total += rules.XP[f"predictions.{q.kind}_right"]
            share = share_for(db, q)
            if share is not None and share < rules.CONTRARIAN_SHARE:
                total += rules.XP["predictions.contrarian"]
    return total


def stats(db: Session, voter: Voter, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    pairs = _settled_votes(db, voter.key)
    decided = [(v, q) for v, q in pairs if q.outcome != "tie"]
    right = sum(1 for v, q in decided if v.option == q.outcome)
    cur, best, _ = right_streak(pairs)
    xp = predictor_xp(db, voter.member) if voter.member is not None else 0
    board = leaderboard(db, "week", now, limit=10_000)
    pos = next((r["position"] for r in board["rows"] if r["voter_key"] == voter.key), None)
    return {"calls": len(pairs), "decided": len(decided), "right": right,
            "accuracy": round(right / len(decided), 4) if len(decided) >= rules.MIN_FOR_ACCURACY else None,
            "accuracy_after": rules.MIN_FOR_ACCURACY, "streak": cur, "best_streak": best, "xp": xp, "pending_xp": pending_xp(db, voter.key),
            "level": rules.level_for(xp), "week_position": pos, "week_players": len(board["rows"]), "linked": voter.member is not None}


def history(db: Session, voter: Voter, limit: int = 30) -> list[dict[str, Any]]:
    rows = list(db.execute(select(PredVote, PredQuestion).join(PredQuestion, PredQuestion.id == PredVote.question_id).where(
        PredVote.voter_key == voter.key, PredVote.status == "counted").order_by(PredQuestion.settles_at.desc()).limit(limit)))
    out = []
    for v, q in rows:
        res = None if q.status not in ("settled", "void") else ("void" if q.status == "void" else "tie" if q.outcome == "tie" else
                                                                ("right" if v.option == q.outcome else "wrong"))
        out.append({"question_id": q.id, "kind": q.kind, "title": q.title, "settle_day": q.settle_day.isoformat(), "call": label_of(q, v.option),
                    "outcome": label_of(q, q.outcome) if q.outcome else None, "result": res, "status": q.status,
                    "settle_price": q.settle_price, "via": v.via})
    return out


def period_start(period: str, now: datetime) -> date | None:
    d = now.astimezone(timezone.utc).date()
    if period == "week":
        return d - timedelta(days=d.weekday())  # calendar week, Monday (UTC)
    if period == "month":
        return d.replace(day=1)
    return None


def leaderboard(db: Session, period: str, now: datetime | None = None, limit: int = 50, linked_only: bool = False,
                start: date | None = None, end: date | None = None) -> dict[str, Any]:
    """Prediction points (not XP) over settled calls in a calendar week / month / all time. Minimum settled calls to appear
    (rules.MIN_CALLS). Ties: accuracy, then the earlier first call. Opted-out and banned members never appear."""
    now = now or utcnow()
    start = start or period_start(period, now)
    q_stmt = select(PredVote, PredQuestion).join(PredQuestion, PredQuestion.id == PredVote.question_id).where(
        PredVote.status == "counted", PredQuestion.status == "settled")
    if start is not None:
        q_stmt = q_stmt.where(PredQuestion.settle_day >= start)
    if end is not None:
        q_stmt = q_stmt.where(PredQuestion.settle_day <= end)
    agg: dict[str, dict[str, Any]] = {}
    shares: dict[int, float | None] = {}
    for v, q in db.execute(q_stmt):
        a = agg.setdefault(v.voter_key, {"voter_key": v.voter_key, "member_id": v.member_id, "tg_user_id": v.tg_user_id, "calls": 0,
                                         "decided": 0, "right": 0, "points": 0, "first": _aware(v.voted_at)})
        a["calls"] += 1
        a["first"] = min(a["first"], _aware(v.voted_at))
        if q.outcome == "tie":
            continue
        a["decided"] += 1
        if v.option == q.outcome:
            a["right"] += 1
            a["points"] += rules.POINTS["daily_right"] if q.kind == "daily" else rules.POINTS["weekly_right"]
            if q.id not in shares:
                shares[q.id] = share_for(db, q)
            s = shares[q.id]
            if s is not None and s < rules.CONTRARIAN_SHARE:
                a["points"] += rules.POINTS["contrarian"]
    need = rules.MIN_CALLS.get(period, rules.MIN_CALLS["all"])
    members = {m.id: m for m in db.scalars(select(Member).where(Member.id.in_([a["member_id"] for a in agg.values() if a["member_id"]])))}
    tg_names = {t.tg_user_id: t for t in db.scalars(select(PredTgUser).where(PredTgUser.tg_user_id.in_(
        [a["tg_user_id"] for a in agg.values() if a["tg_user_id"] and not a["member_id"]])))}
    rows = []
    for a in agg.values():
        if a["calls"] < need:
            continue
        m = members.get(a["member_id"]) if a["member_id"] else None
        if m is not None and (m.status != "active" or m.leaderboard_opt_out):
            continue
        if m is None and linked_only:
            continue
        tu = tg_names.get(a["tg_user_id"])
        rows.append({**a, "first": a["first"].isoformat(), "accuracy": round(a["right"] / a["decided"], 4) if a["decided"] else 0.0,
                     "name": m.username if m is not None else (tu.first_name if tu and tu.first_name else "Telegram member"), "linked": m is not None})
    rows.sort(key=lambda r: (-r["points"], -r["accuracy"], r["first"]))
    for i, r in enumerate(rows):
        r["position"] = i + 1
    return {"period": period, "since": start.isoformat() if start else None, "min_calls": need, "rows": rows[:limit], "players": len(rows)}
