"""Kiwi Rank Score: Price Chat XP plus a weighted, capped share of historical Telegram reputation (Combot).

    legacy_points   = min(round(combot_xp x combot_weight), legacy_cap)   (0 when Combot doesn't count, or no record)
    rank_score      = round(price_chat_xp x price_chat_weight) + legacy_points

The two sources stay separate: Combot numbers live in combot_reputation and never enter the XP ledger. Legacy points
follow the member's linked Telegram account, matched only by the numeric Telegram user id. Every weight is set in
the dashboard; changing one recalculates every member.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.members import CombotRecord, LinkedIdentity, Member, ReputationSettings


def settings(db: Session) -> ReputationSettings:
    row = db.get(ReputationSettings, 1)
    if row is None:
        row = ReputationSettings(id=1)
        db.add(row)
        db.flush()
    return row


def combot_record_for(db: Session, member: Member) -> CombotRecord | None:
    """The Combot record of the member's linked Telegram account (numeric id only, never the username)."""
    tg = db.scalar(select(LinkedIdentity.platform_user_id).where(LinkedIdentity.member_id == member.id,
                                                                 LinkedIdentity.platform == "telegram",
                                                                 LinkedIdentity.status == "active"))
    if tg is None:
        return None
    return db.scalar(select(CombotRecord).where(CombotRecord.telegram_user_id == str(tg)))


def legacy_points(rs: ReputationSettings, record: CombotRecord | None) -> int:
    if record is None or not rs.combot_enabled or record.combot_xp <= 0:
        return 0
    pts = round(record.combot_xp * rs.combot_weight)
    return min(pts, rs.legacy_cap) if rs.legacy_cap > 0 else pts


def compute(db: Session, member: Member) -> tuple[int, int]:
    """(legacy_points, rank_score) for the member's current ledger total."""
    rs = settings(db)
    legacy = legacy_points(rs, combot_record_for(db, member))
    return legacy, round(member.xp_total * rs.price_chat_weight) + legacy


def explain(db: Session, member: Member) -> dict[str, Any]:
    """The exact math, for the member's passport and the admin view. No mysterious numbers."""
    rs = settings(db)
    rec = combot_record_for(db, member)
    raw = round(rec.combot_xp * rs.combot_weight) if rec else 0
    legacy = legacy_points(rs, rec)
    pc = round(member.xp_total * rs.price_chat_weight)
    return {
        "price_chat_xp": member.xp_total, "price_chat_weight": rs.price_chat_weight, "price_chat_points": pc,
        "combot_counts": rs.combot_enabled,
        "combot": ({"xp": rec.combot_xp, "messages": rec.combot_messages, "rep": rec.combot_rep, "rank": rec.combot_rank,
                    "active_days": rec.combot_active_days, "source_timestamp": rec.source_timestamp.isoformat(),
                    "imported_at": rec.imported_at.isoformat(), "source_type": rec.source_type} if rec else None),
        "combot_weight": rs.combot_weight, "legacy_cap": rs.legacy_cap, "legacy_raw": raw, "legacy_points": legacy,
        "capped": bool(rec and rs.legacy_cap and raw > rs.legacy_cap and rs.combot_enabled),
        "rank_score": pc + legacy,
        "formula": f"{member.xp_total} Price Chat XP x {rs.price_chat_weight:g}"
                   + (f" + min({rec.combot_xp} Combot XP x {rs.combot_weight:g}, {rs.legacy_cap}) legacy" if rec and rs.combot_enabled else "")
                   + f" = {pc + legacy}",
    }


def recalc_all(db: Session) -> int:
    from app.members import xp as ledger

    n = 0
    for m in db.scalars(select(Member)):
        ledger.recalc(db, m)
        n += 1
    db.commit()
    return n
