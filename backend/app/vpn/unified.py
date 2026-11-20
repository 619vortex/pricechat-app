"""One view of an account for every Kiwi VPN client: Android, Windows and the website read the same rows through this.

It adds nothing to the source of truth. Identity is `members`, plan and devices are `service.overview`, XP and rank are the ledger (`app.members.xp`), referrals are `app.members.referrals`. Nothing here keeps its own
copy, and nothing here writes XP or starts a trial. Clients poll it, on sign-in, on coming to the front, and about once a minute: no new infrastructure.

The routes sit under /members/vpn/v1, which a Kiwi VPN app's limited session can already reach (app.members.desktop_scope), so that limit doesn't get any wider. Windows 0.1.1 never calls them and is unaffected.
"""

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.members import leaderboard as boards
from app.members import referrals
from app.members import service as members
from app.models.members import Member
from app.vpn import service

API_VERSION = 1
REFRESH_SECONDS = 60
PLATFORMS = ("android", "ios", "windows", "website")

# Feature -> the lowest app version on each platform that has it. A platform not listed doesn't have it. A capability only says a client may show a feature: every route still checks the account for itself.
# Off everywhere until each feature is real and approved: gaming_mode_enabled, smart_routing_enabled, vpn_missions_enabled.
_CAPABILITIES: dict[str, dict[str, str]] = {
    "passport_enabled": {"android": "0.1.0", "ios": "0.1.0", "website": "0", "windows": "0.2.0"},  # Windows 0.1.1 has no Passport screen yet
    "referrals_enabled": {"android": "0.1.0", "ios": "0.1.0", "website": "0", "windows": "0.2.0"},  # also needs the program switch: see capabilities()
    "auto_call_enabled": {"android": "0.1.0"},  # uses Android's notification access, so it can't exist elsewhere
    "app_routing_enabled": {"android": "0.1.0"},  # per-app VPN, chosen and stored on the phone: the server never sees the list
    "gaming_mode_enabled": {},
    "smart_routing_enabled": {},
    "vpn_missions_enabled": {},
}


def _version(text: str | None) -> tuple[int, ...] | None:
    """(0, 1, 1) from "0.1.1" or "0.1.1-beta1". None for anything else: an unreadable version gets no feature."""
    m = re.match(r"^(\d{1,4}(?:\.\d{1,4}){0,3})(?:[-+][0-9A-Za-z.-]{0,32})?$", text or "")
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def _at_least(have: tuple[int, ...], need: tuple[int, ...]) -> bool:
    size = max(len(have), len(need))
    return have + (0,) * (size - len(have)) >= need + (0,) * (size - len(need))


def capabilities(db: Session, platform: str | None, version: str | None) -> dict[str, bool]:
    """What this client may show. Never grants access to anything: see the note above _CAPABILITIES."""
    have = _version(version)
    out = {}
    for feature, by_platform in _CAPABILITIES.items():
        need = by_platform.get(platform or "")
        out[feature] = need is not None and have is not None and _at_least(have, _version(need) or ())
    if not referrals.program(db).referrals_enabled:
        out["referrals_enabled"] = False
    return out


@dataclass(frozen=True)
class MissionDefinition:
    """A proposal. xp isn't an approved number, and nothing pays it: there's no XP rule, no claim route, and clients are never sent these while off. `verifiable`: can the server prove this happened by itself?
    Only then may it ever pay, since XP is never awarded on a client's word."""

    key: str
    title: str
    proposed_xp: int
    verifiable: bool
    how: str


PROPOSED_MISSIONS: tuple[MissionDefinition, ...] = (
    MissionDefinition("vpn.first_connection", "First successful VPN connection", 100, True, "A device of this account has a last_handshake recorded by the server's own sync. Paid once: source_ref vpn:first_connection."),
    MissionDefinition("vpn.onboarding", "Complete VPN onboarding", 200, False, "No server evidence: only a client could report it."),
    MissionDefinition("vpn.gaming_mode", "Try Gaming Mode", 150, False, "Gaming Mode doesn't exist yet, and using it would be a client report."),
    MissionDefinition("vpn.security_check", "Complete connection security check", 100, False, "No server evidence: only a client could report it."),
    MissionDefinition("vpn.referral", "Refer a verified new VPN user", 500, True, "The existing referral ledger, once a referred member has a server-recorded handshake. Needs its own rule and approval."),
)


def missions_out(caps: dict[str, bool]) -> list[dict[str, Any]]:
    """Nothing while the switch is off, so a client can't show an unapproved reward."""
    if not caps.get("vpn_missions_enabled"):
        return []
    return [{"key": m.key, "title": m.title, "xp": m.proposed_xp} for m in PROPOSED_MISSIONS if m.verifiable]


def passport_out(db: Session, member: Member, public_url: str, caps: dict[str, bool]) -> dict[str, Any] | None:
    """The same figures the website's Passport shows, from the same functions. No XP is written here."""
    if not caps.get("passport_enabled"):
        return None
    members.ensure_catalog(db)
    referral = None
    if caps.get("referrals_enabled"):
        code = referrals.code_for(db, member)
        stats = referrals.stats(db, member)
        referral = {"code": code, "link": referrals.invite_link(public_url, code), "joined": stats["joined"], "qualified": stats["qualified"], "xp_earned": stats["xp_earned"], "reward_xp": stats["reward_xp"]}
    return {
        "username": member.username,
        "display_name": member.display_name,
        "xp": member.xp_total,
        "bonus_xp": member.bonus_xp,
        "xp_suspended": member.xp_suspended,
        "rank": members.rank_progress(db, member),
        "badges": members.badges_of(db, member),
        "standing": boards.standing(db, member),
        "referral": referral,
    }


def state(db: Session, member: Member, public_url: str, platform: str | None, version: str | None) -> dict[str, Any]:
    """Everything a client needs on one screen. `vpn` is exactly what GET /members/vpn answers, so the two can't disagree."""
    caps = capabilities(db, platform, version)
    out = {
        "api_version": API_VERSION,
        "refresh_after_seconds": REFRESH_SECONDS,
        "capabilities": caps,
        "vpn": service.overview(db, member, False),
        "passport": passport_out(db, member, public_url, caps),
        "missions": missions_out(caps),
    }
    db.commit()
    return out
