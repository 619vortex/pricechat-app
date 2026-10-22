"""Kiwi Passport rank rewards: the catalog, the server-side gate, beginner content, profile customization, the Knight
trading reward and Royal VIP access (claims, decisions, audit).

Unlocks follow the member's CURRENT rank, computed from the ledger (`xp.rank_for(rank_score)`) at every request: members
who already passed a rank qualify automatically, nothing is stored per member, XP is never recalculated. Every gated
endpoint calls `require`; hiding something in the browser is never the protection.

Nothing here moves money. The Knight reward is a claim a person reviews and fulfils by hand, and it can't be claimed at
all until an admin has activated it with written terms.
"""

import base64
import re
import unicodedata
import uuid
from datetime import timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import xp as ledger
from app.models.members import Member
from app.models.rewards import BeginnerContent, RankReward, RewardAudit, RewardClaim, RewardSettings
from app.security.secrets import protect, unprotect

REWARDS: list[dict[str, Any]] = [
    dict(rank_key="egg", kind="none", title="Your Kiwi journey starts here",
         description="Hatch your Kiwi to unlock your first exclusive content."),
    dict(rank_key="chick", kind="content", title="BEGINNER CONTENT",
         description="Exclusive beginner lessons from Price Chat: videos and guides, only for Kiwi Chicks and above."),
    dict(rank_key="kiwi", kind="profile", title="PROFILE CUSTOMIZATION",
         description="Choose your Passport display name and a Passport frame colour."),
    dict(rank_key="scout", kind="earn", title="UNLOCK EARN — Your Kiwi Trading Bot",
         description="Earn opens: the Kiwi trading bot onboarding and connecting your Hyperliquid API wallet. Paper trading first; "
                     "live trading keeps every existing safeguard."),
    dict(rank_key="warrior", kind="arena", title="VORTEX REALM",
         description="A 3D fantasy RPG in your browser: create a hero, fight monsters, take on quests and collect loot. Play it in your Passport."),
    dict(rank_key="knight", kind="trading_credit", title="$10 HYPERLIQUID TRADING REWARD",
         description="A one-time trading reward for Knight Kiwis with a verified Hyperliquid account. Reviewed by the Price Chat team."),
    dict(rank_key="royal", kind="vip", title="WHATSAPP VIP COMMUNITY",
         description="Request access to the private Price Chat WhatsApp VIP group: stocks, commodities and financial markets."),
    dict(rank_key="golden", kind="tba", title="Exclusive reward — To Be Announced", description=""),
    dict(rank_key="legend", kind="tba", title="Exclusive reward — To Be Announced", description=""),
    dict(rank_key="mythic", kind="tba", title="Exclusive reward — To Be Announced", description=""),
]
KINDS = {r["rank_key"]: r["kind"] for r in REWARDS}
CLAIMABLE = {"knight", "royal"}
NO_REWARD = {"none", "tba"}  # Egg (a teaser) and the ranks whose reward isn't announced: nothing to unlock

ACCENTS = ("green", "gold", "ice", "ember", "violet")
NAME_MIN, NAME_MAX = 2, 32
RENAME_COOLDOWN = timedelta(days=7)
# whole words (after look-alike folding) a display name may not contain: nobody can pose as Price Chat staff
STAFF_WORDS = ("admin", "administrator", "moderator", "mod", "mods", "staff", "support", "official", "pricechat", "worker",
               "manager", "owner", "team", "system", "founder", "ceo", "dev", "developer", "helpdesk", "verified")
STAFF_INFIX = ("admin", "moderator", "pricechat", "official", "support")  # refused even inside other words ("xadminx")
_FOLD = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "@": "a", "$": "s", "!": "i", "|": "i",
                       # Cyrillic / Greek look-alikes of Latin letters
                       "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ѕ": "s", "ј": "j",
                       "к": "k", "м": "m", "т": "t", "н": "h", "в": "b", "ԁ": "d", "ο": "o", "α": "a", "ε": "e", "ρ": "p",
                       "ν": "v", "ι": "i", "κ": "k", "τ": "t", "μ": "m"})
_ALLOWED = re.compile(r"^[\w .'\-]+$")


class RewardError(ValueError):
    """A problem the member (or admin) can act on; the message is shown. `status` is the HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# --- catalog ------------------------------------------------------------------------------------------------------


def ensure_rewards(db: Session) -> None:
    """Insert missing defaults. Rows edited in the dashboard are never overwritten."""
    have = set(db.scalars(select(RankReward.rank_key)))
    added = False
    for r in REWARDS:
        if r["rank_key"] not in have:
            db.add(RankReward(**r))
            added = True
    if db.get(RewardSettings, 1) is None:
        db.add(RewardSettings(id=1, knight_terms="", knight_regions="", vip_description=""))
        added = True
    if added:
        db.flush()


def settings(db: Session) -> RewardSettings:
    ensure_rewards(db)
    row = db.get(RewardSettings, 1)
    assert row is not None
    return row


def reward_rows(db: Session) -> dict[str, RankReward]:
    ensure_rewards(db)
    return {r.rank_key: r for r in db.scalars(select(RankReward))}


def current_level(db: Session, member: Member) -> int:
    """The member's rank right now, from the ledger (never a stored flag)."""
    return ledger.rank_for(db, member.rank_score).level


def _rank_level(db: Session, rank_key: str) -> int:
    lvl = next((r.level for r in ledger.ranks(db) if r.key == rank_key), None)
    if lvl is None:
        raise RewardError("Unknown rank.", 404)
    return lvl


def unlocked(db: Session, member: Member, rank_key: str) -> bool:
    row = reward_rows(db).get(rank_key)
    return bool(row and row.enabled and row.kind not in NO_REWARD and member.status == "active"
                and current_level(db, member) >= _rank_level(db, rank_key))


def require(db: Session, member: Member, rank_key: str) -> None:
    """The server-side gate for a rank reward (403 with what's needed)."""
    if unlocked(db, member, rank_key):
        return
    row = reward_rows(db).get(rank_key)
    if row is None or not row.enabled:
        raise RewardError("This reward isn't available right now.", 403)
    rank = next(r for r in ledger.ranks(db) if r.key == rank_key)
    raise RewardError(f"Reach {rank.name} ({rank.min_xp:,} XP) to unlock this.", 403)


def audit(db: Session, actor: str, action: str, member_id: int | None = None, claim: RewardClaim | None = None, **details: Any) -> None:
    db.add(RewardAudit(claim_id=claim.id if claim else None, member_id=member_id, actor=actor[:64], action=action[:48], details=details))


# --- the member's view ----------------------------------------------------------------------------------------------


def _claim(db: Session, member: Member, key: str) -> RewardClaim | None:
    return db.scalar(select(RewardClaim).where(RewardClaim.member_id == member.id, RewardClaim.reward_key == key))


def claim_out(c: RewardClaim | None) -> dict[str, Any] | None:
    if c is None:
        return None
    return {"id": c.public_id, "status": c.status, "created_at": c.created_at.isoformat(),
            "reviewed_at": c.reviewed_at.isoformat() if c.reviewed_at else None}


def _aware(d: Any) -> Any:
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def knight_state(db: Session) -> dict[str, Any]:
    """What members are told about the Knight reward. Only ACTIVE with written terms is claimable."""
    s = settings(db)
    expired = bool(s.knight_expires_at and _aware(s.knight_expires_at) <= utcnow())
    active = s.knight_status == "ACTIVE" and bool(s.knight_terms.strip()) and not expired
    return {"active": active, "status": "EXPIRED" if expired and s.knight_status == "ACTIVE" else s.knight_status,
            "amount_usd": s.knight_amount_usd, "terms": s.knight_terms if active else "", "regions": s.knight_regions if active else "",
            "expires_at": s.knight_expires_at.isoformat() if s.knight_expires_at else None}


def overview(db: Session, member: Member | None) -> dict[str, Any]:
    """Every rank as a milestone card: artwork, requirement, reward, and (signed in) locked / unlocked / claim state."""
    from app.members.service import rank_out

    rows = reward_rows(db)
    level = current_level(db, member) if member else 0
    score = member.rank_score if member else 0
    all_ranks = ledger.ranks(db)
    cards = []
    for i, r in enumerate(all_ranks):
        rw = rows.get(r.key)
        nxt = all_ranks[i + 1] if i + 1 < len(all_ranks) else None
        is_unlocked = bool(member and rw and rw.enabled and rw.kind not in NO_REWARD and level >= r.level and member.status == "active")
        card: dict[str, Any] = {
            "rank": rank_out(r), "reached": bool(member) and level >= r.level, "current": level == r.level,
            "reward": {"kind": rw.kind if rw else "none", "title": rw.title if rw else "", "description": rw.description if rw else "",
                       "enabled": bool(rw and rw.enabled)},
            "unlocked": is_unlocked,
            "xp_to_reach": max(0, r.min_xp - score),
            "progress": 1.0 if level >= r.level else round(min(1.0, score / r.min_xp), 4) if r.min_xp else 1.0,
            "next_min_xp": nxt.min_xp if nxt else None,
        }
        if r.key in CLAIMABLE and member:
            card["claim"] = claim_out(_claim(db, member, r.key))
        cards.append(card)
    out: dict[str, Any] = {"ranks": cards, "level": level, "score": score, "knight": knight_state(db)}
    if member:
        out["profile"] = {"display_name": custom_display_name(member), "accent": member.passport_accent, "accents": list(ACCENTS),
                          "next_rename_at": _next_rename(member)}
    return out


# --- Kiwi Chick: beginner content ---------------------------------------------------------------------------------


def content_out(c: BeginnerContent, admin: bool = False) -> dict[str, Any]:
    out = {"id": c.id, "title": c.title, "description": c.description, "video_url": c.video_url, "thumbnail_url": c.thumbnail_url,
           "guide_text": c.guide_text, "position": c.position}
    if admin:
        out.update({"published": c.published, "updated_at": c.updated_at.isoformat()})
    return out


def beginner_content(db: Session, member: Member) -> dict[str, Any]:
    require(db, member, "chick")
    items = db.scalars(select(BeginnerContent).where(BeginnerContent.published.is_(True))
                       .order_by(BeginnerContent.position, BeginnerContent.id))
    out = [content_out(c) for c in items]
    return {"items": out, "coming_soon": not out}


def _url(v: str | None) -> str | None:
    v = (v or "").strip()
    if not v:
        return None
    if not re.match(r"^https://[^\s<>\"']+$", v) or len(v) > 500:
        raise RewardError("Links must be full https:// addresses.")
    return v


def save_content(db: Session, actor: str, values: dict[str, Any], item_id: int | None = None) -> BeginnerContent:
    c = db.get(BeginnerContent, item_id) if item_id else BeginnerContent(title="", description="", guide_text="")
    if c is None:
        raise RewardError("Content not found.", 404)
    if "title" in values:
        title = (values["title"] or "").strip()
        if not title:
            raise RewardError("A title is required.")
        c.title = title[:160]
    for k in ("description", "guide_text"):
        if k in values:
            setattr(c, k, (values[k] or "").strip()[:20000])
    for k in ("video_url", "thumbnail_url"):
        if k in values:
            setattr(c, k, _url(values[k]))
    if "position" in values and values["position"] is not None:
        c.position = int(values["position"])
    if "published" in values and values["published"] is not None:
        c.published = bool(values["published"])
    if not c.title:
        raise RewardError("A title is required.")
    if item_id is None:
        db.add(c)
    db.flush()
    audit(db, actor, "content_saved", content_id=c.id, published=c.published, fields=sorted(values))
    db.commit()
    return c


def delete_content(db: Session, actor: str, item_id: int) -> None:
    c = db.get(BeginnerContent, item_id)
    if c is None:
        raise RewardError("Content not found.", 404)
    audit(db, actor, "content_deleted", content_id=c.id, title=c.title)
    db.delete(c)
    db.commit()


# --- Kiwi: profile customization ----------------------------------------------------------------------------------


def _skeleton(text: str) -> str:
    """Lower-case, look-alikes folded to Latin, accents dropped: what a name LOOKS like."""
    t = unicodedata.normalize("NFKD", text.lower().translate(_FOLD))
    return "".join(ch for ch in t if not unicodedata.combining(ch))


def clean_display_name(raw: str) -> str:
    """NFKC, invisible / control characters removed, spaces collapsed."""
    t = unicodedata.normalize("NFKC", raw or "")
    t = "".join(ch for ch in t if unicodedata.category(ch)[0] != "C")  # Cc Cf Cs Co Cn: controls, zero-width, bidi...
    return re.sub(r"\s+", " ", t).strip()


def display_name_problem(db: Session, member: Member, name: str) -> str | None:
    if not NAME_MIN <= len(name) <= NAME_MAX:
        return f"Use {NAME_MIN}-{NAME_MAX} characters."
    if not _ALLOWED.match(name):
        return "Use letters, numbers, spaces and . - ' _ only."
    sk = _skeleton(name)
    words = re.findall(r"[a-z0-9]+", sk)
    squashed = "".join(words)
    if any(w in STAFF_WORDS for w in words) or squashed in STAFF_WORDS or any(x in squashed for x in STAFF_INFIX):
        return "That name looks like Price Chat staff. Pick another."
    from app.models.members import Member as M
    from app.models.workers import WkRole

    staff_ids = set(db.scalars(select(WkRole.member_id).where(WkRole.revoked_at.is_(None))))
    staff_ids.discard(member.id)
    if staff_ids:
        for uname, dname in db.execute(select(M.username, M.display_name).where(M.id.in_(staff_ids))):
            for other in (uname, dname):
                if other and "".join(re.findall(r"[a-z0-9]+", _skeleton(other))) == squashed:
                    return "That name belongs to a Price Chat team member."
    return None


def custom_display_name(member: Member) -> str | None:
    """The display name the member chose (Kiwi reward), or None. Older rows hold the first automatic name: not shown."""
    return member.display_name if member.display_name_changed_at else None


def _next_rename(member: Member) -> str | None:
    if member.display_name_changed_at is None:
        return None
    at = _aware(member.display_name_changed_at) + RENAME_COOLDOWN
    return at.isoformat() if at > utcnow() else None


def set_display_name(db: Session, member: Member, raw: str) -> Member:
    """Only ever the signed-in member's own Passport; the user id, username and sign-ins never change."""
    require(db, member, "kiwi")
    name = clean_display_name(raw)
    if name == member.display_name:
        return member
    wait = _next_rename(member)
    if wait:
        raise RewardError(f"You can change your display name again on {wait[:10]}.", 429)
    problem = display_name_problem(db, member, name)
    if problem:
        raise RewardError(problem)
    old = member.display_name
    member.display_name, member.display_name_changed_at = name, utcnow()
    ledger.audit(db, member.id, "member", "display_name_changed", {"from": old, "to": name})
    db.commit()
    return member


def set_accent(db: Session, member: Member, accent: str | None) -> Member:
    require(db, member, "kiwi")
    if accent is not None and accent not in ACCENTS:
        raise RewardError("Unknown frame colour.")
    member.passport_accent = accent
    db.commit()
    return member


# --- Knight: trading reward / Royal: VIP access (claims) ----------------------------------------------------------


def _verified_wallet(db: Session, member: Member) -> str | None:
    from app.tradingbot.service import wallet_of

    w = wallet_of(db, member)
    return w.address if w else None


def claim_knight(db: Session, member: Member, note: str = "") -> RewardClaim:
    require(db, member, "knight")
    if not knight_state(db)["active"]:
        raise RewardError("The Knight trading reward isn't active yet: it's pending activation.", 409)
    wallet = _verified_wallet(db, member)
    if wallet is None:
        raise RewardError("Connect and verify your Hyperliquid wallet first (Passport → Wallet).", 409)
    return _new_claim(db, member, "knight", wallet=wallet, note=note)


def request_vip(db: Session, member: Member, note: str = "") -> RewardClaim:
    require(db, member, "royal")
    existing = _claim(db, member, "royal")
    if existing and existing.status == "REJECTED" and utcnow() - _aware(existing.reviewed_at or existing.created_at) >= timedelta(days=30):
        existing.status, existing.member_note, existing.reviewed_at, existing.reviewed_by = "PENDING", note.strip()[:500], None, None
        audit(db, f"member:{member.id}", "vip_rerequested", member.id, existing)
        db.commit()
        return existing
    return _new_claim(db, member, "royal", note=note)


def _new_claim(db: Session, member: Member, key: str, wallet: str | None = None, note: str = "") -> RewardClaim:
    if _claim(db, member, key) is not None:
        raise RewardError("You've already claimed this reward." if key == "knight" else "You've already requested access.", 409)
    c = RewardClaim(public_id=str(uuid.uuid4()), member_id=member.id, reward_key=key, status="PENDING",
                    rank_level=current_level(db, member), wallet=wallet, member_note=note.strip()[:500], admin_note="")
    db.add(c)
    try:
        db.flush()
    except IntegrityError:  # two requests at once: the unique (member, reward) pair lets only one through
        db.rollback()
        raise RewardError("You've already claimed this reward.", 409) from None
    audit(db, f"member:{member.id}", f"{key}_claimed", member.id, c, wallet=wallet)
    db.commit()
    return c


def vip_access(db: Session, member: Member) -> dict[str, Any]:
    """The invite link, only for an APPROVED request while the member still holds Royal Kiwi."""
    require(db, member, "royal")
    c = _claim(db, member, "royal")
    s = settings(db)
    out: dict[str, Any] = {"claim": claim_out(c), "description": s.vip_description, "invite_url": None}
    if c and c.status == "APPROVED" and s.vip_invite_enc:
        out["invite_url"] = unprotect(base64.b64decode(s.vip_invite_enc)).decode()
    return out


TRANSITIONS = {
    "knight": {"approve": ({"PENDING"}, "APPROVED"), "fulfill": ({"APPROVED"}, "FULFILLED"), "reject": ({"PENDING", "APPROVED"}, "REJECTED"),
               "reopen": ({"REJECTED"}, "PENDING")},
    "royal": {"approve": ({"PENDING"}, "APPROVED"), "reject": ({"PENDING"}, "REJECTED"), "revoke": ({"APPROVED"}, "REVOKED"),
              "restore": ({"REVOKED"}, "APPROVED"), "reopen": ({"REJECTED"}, "PENDING")},
}


def decide(db: Session, public_id: str, action: str, actor: str, note: str = "", reference: str | None = None) -> RewardClaim:
    """An admin decision. Approving re-checks eligibility (rank, the Knight terms and budget, the same verified wallet)."""
    c = db.scalar(select(RewardClaim).where(RewardClaim.public_id == public_id))
    if c is None:
        raise RewardError("Claim not found.", 404)
    rule = TRANSITIONS.get(c.reward_key, {}).get(action)
    if rule is None:
        raise RewardError("That action doesn't apply to this reward.")
    allowed, to = rule
    if c.status not in allowed:
        raise RewardError(f"A {c.status.lower()} claim can't be {action}d.", 409)
    member = db.get(Member, c.member_id)
    if member is None:
        raise RewardError("The member no longer exists.", 409)
    if to == "APPROVED":
        if not unlocked(db, member, c.reward_key):
            raise RewardError("The member no longer holds the rank for this reward.", 409)
        if c.reward_key == "knight":
            ks = knight_state(db)
            if not ks["active"]:
                raise RewardError("Activate the Knight reward (status, terms) before approving claims.", 409)
            s = settings(db)
            used = db.scalar(select(func.count()).select_from(RewardClaim).where(
                RewardClaim.reward_key == "knight", RewardClaim.status.in_(("APPROVED", "FULFILLED")))) or 0
            if used >= s.knight_budget:
                raise RewardError(f"The funded budget ({s.knight_budget} rewards) is used up. Raise it first.", 409)
            if _verified_wallet(db, member) != c.wallet:
                raise RewardError("The member's verified wallet changed since the claim. Reject it and let them claim again.", 409)
    if to == "FULFILLED" and not (reference or "").strip():
        raise RewardError("Add the fulfilment reference (how and when it was paid).")
    before = c.status
    c.status, c.reviewed_by, c.reviewed_at = to, actor[:64], utcnow()
    if note.strip():
        c.admin_note = (c.admin_note + "\n" if c.admin_note else "") + note.strip()[:2000]
    if reference:
        c.fulfillment_ref = reference.strip()[:200]
    audit(db, actor, f"{c.reward_key}_{action}", c.member_id, c, before=before, after=to, note=note.strip()[:500], reference=reference)
    from app.members.notify import notify

    label = {"knight": "Knight trading reward", "royal": "WhatsApp VIP access"}[c.reward_key]
    notify(db, c.member_id, "reward", f"{label}: {to.lower()}", "", {"claim": c.public_id})
    db.commit()
    return c


def claims(db: Session, reward_key: str | None = None, status: str | None = None, limit_n: int = 200) -> list[dict[str, Any]]:
    stmt = select(RewardClaim, Member).join(Member, Member.id == RewardClaim.member_id).order_by(RewardClaim.created_at.desc()).limit(limit_n)
    if reward_key:
        stmt = stmt.where(RewardClaim.reward_key == reward_key)
    if status:
        stmt = stmt.where(RewardClaim.status == status)
    return [{**(claim_out(c) or {}), "reward": c.reward_key, "member": {"id": m.public_id, "username": m.username,
             "rank_level": current_level(db, m)}, "rank_at_claim": c.rank_level, "wallet": c.wallet, "member_note": c.member_note,
             "admin_note": c.admin_note, "fulfillment_ref": c.fulfillment_ref, "reviewed_by": c.reviewed_by}
            for c, m in db.execute(stmt)]


def history(db: Session, limit_n: int = 200) -> list[dict[str, Any]]:
    rows = db.scalars(select(RewardAudit).order_by(RewardAudit.id.desc()).limit(limit_n))
    return [{"id": a.id, "at": a.created_at.isoformat(), "actor": a.actor, "action": a.action, "member_id": a.member_id,
             "claim_id": a.claim_id, "details": a.details} for a in rows]


# --- admin settings -----------------------------------------------------------------------------------------------

SETTING_FIELDS = {"knight_status": str, "knight_amount_usd": int, "knight_terms": str, "knight_regions": str, "knight_budget": int,
                  "vip_description": str, "arena_kills_per_xp": int, "arena_run_xp_cap": int, "arena_daily_xp_cap": int,
                  "arena_min_run_seconds": int, "arena_runs_per_hour": int}


def settings_out(db: Session) -> dict[str, Any]:
    s = settings(db)
    out = {k: getattr(s, k) for k in SETTING_FIELDS}
    out["knight_expires_at"] = s.knight_expires_at.isoformat() if s.knight_expires_at else None
    out["vip_invite_set"] = bool(s.vip_invite_enc)  # the link itself is never sent back, even to the dashboard
    return out


def update_settings(db: Session, actor: str, values: dict[str, Any]) -> dict[str, Any]:
    s = settings(db)
    changed: list[str] = []
    for k, v in values.items():
        if v is None or k not in SETTING_FIELDS:
            continue
        if k == "knight_status" and v not in ("NOT_CONFIGURED", "ACTIVE", "PAUSED"):
            raise RewardError("Unknown status.")
        if SETTING_FIELDS[k] is int and int(v) < 0:
            raise RewardError(f"{k} can't be negative.")
        if k in ("arena_kills_per_xp", "arena_runs_per_hour") and int(v) < 1:
            raise RewardError(f"{k} must be at least 1.")
        setattr(s, k, SETTING_FIELDS[k](v))
        changed.append(k)
    if "knight_expires_at" in values:
        s.knight_expires_at = values["knight_expires_at"]
        changed.append("knight_expires_at")
    if s.knight_status == "ACTIVE" and not s.knight_terms.strip():
        raise RewardError("Write the reward terms (conditions, regions, expiry) before activating it.")
    if values.get("vip_invite_url") is not None:
        url = values["vip_invite_url"].strip()
        if url and not re.match(r"^https://(chat\.whatsapp\.com|wa\.me)/\S+$", url):
            raise RewardError("Use a WhatsApp invite link (https://chat.whatsapp.com/...).")
        s.vip_invite_enc = base64.b64encode(protect(url.encode())).decode() if url else None
        changed.append("vip_invite_url")
    audit(db, actor, "settings_changed", fields=changed)  # values themselves stay out of the log (the invite link is secret)
    db.commit()
    return settings_out(db)


def update_reward(db: Session, actor: str, rank_key: str, values: dict[str, Any]) -> RankReward:
    row = reward_rows(db).get(rank_key)
    if row is None:
        raise RewardError("Unknown rank.", 404)
    for k in ("title", "description"):
        if values.get(k) is not None:
            setattr(row, k, values[k].strip()[:96 if k == "title" else 2000])
    if values.get("enabled") is not None:
        row.enabled = bool(values["enabled"])
    audit(db, actor, "reward_changed", rank=rank_key, enabled=row.enabled)
    db.commit()
    return row
