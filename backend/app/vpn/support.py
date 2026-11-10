"""Kiwi VPN support: a member's bug report or feedback, sent from the Windows app (its "kd." session) or the website.

Stored in vpn_support_reports for Admin › Kiwi VPN › Support, newest first. Before anything is stored, the message and the
diagnostics are redacted: WireGuard private / preshared key lines, anything shaped like a WireGuard key (44 characters of
base64), 64-hex keys, recovery phrases and bot tokens (app.security.redact). The app strips them itself first; this is the
server's own guarantee. The owner hears about new reports through the owner-alert path (logged only while no owner
Telegram chat is set), at most once per alerts.COOLDOWN, and never with the report's text.
"""

import json
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.members import Member
from app.models.vpn import VpnSupportReport
from app.security.redact import redact_secrets
from app.services.activity import redact as redact_fields

logger = logging.getLogger(__name__)

KINDS = ("bug", "feedback")
MAX_MESSAGE = 4000
MAX_DIAGNOSTICS = 20_000  # characters, after redaction; longer diagnostics are cut, never refused
REDACTED = "[redacted]"
ALERT_KEY = "vpn.support"

# a whole config line that carries a secret ("PrivateKey = ...", "PresharedKey=...", JSON "private_key": "...")
_KEY_LINE = re.compile(r"(?im)^[^\n]*\b(?:private[_ ]?key|preshared[_ ]?key|privatekey|presharedkey)\b[^\n]*$")
_WG_KEY = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=(?![A-Za-z0-9+/=])")  # a 32-byte key in base64


def redact(text: str) -> str:
    if not text:
        return text
    text = _KEY_LINE.sub(REDACTED, text)
    text = _WG_KEY.sub(REDACTED, text)
    return redact_secrets(text)


def _walk(value: Any) -> Any:
    """Each string in a JSON object redacted on its own (a config inside one keeps its line breaks until then)."""
    if isinstance(value, dict):
        return {k: _walk(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v) for v in value]
    return redact(value) if isinstance(value, str) else value


def diagnostics_text(value: Any) -> str | None:
    """The app's diagnostics (text or a JSON object) as redacted text of at most MAX_DIAGNOSTICS characters."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = json.dumps(_walk(redact_fields(value)), indent=1, sort_keys=True, default=str, ensure_ascii=False)
    clean = redact(value.strip())
    if not clean:
        return None
    if len(clean) > MAX_DIAGNOSTICS:
        clean = clean[: MAX_DIAGNOSTICS - 20].rstrip() + "\n[cut: too long]"
    return clean


def create(db: Session, member: Member, kind: str, message: str, app_version: str | None, diagnostics: Any, source: str) -> VpnSupportReport:
    row = VpnSupportReport(member_id=member.id, kind=kind, source=source, message=redact(message.strip())[:MAX_MESSAGE],
                           app_version=(app_version or "").strip()[:32] or None, diagnostics=diagnostics_text(diagnostics))
    db.add(row)
    db.commit()
    return row


def alert_text(member: Member, row: VpnSupportReport) -> str:
    version = f" (app {row.app_version})" if row.app_version else ""
    return f"Kiwi VPN: new {row.kind} report from {member.username or f'member #{member.id}'}{version}. Admin › Kiwi VPN › Support."


async def notify(text: str) -> None:
    """Runs after the response, with its own session: an alert can never fail or slow the member's report."""
    from app.alerts import notify_owner
    from app.db.session import SessionLocal

    try:
        with SessionLocal() as db:
            await notify_owner(db, ALERT_KEY, text)
    except Exception as exc:  # noqa: BLE001 - notify_owner already swallows its own errors; this guards opening the session
        logger.warning("Kiwi VPN support alert failed (%s)", type(exc).__name__)


def out(row: VpnSupportReport, username: str | None) -> dict[str, Any]:
    return {"id": row.id, "kind": row.kind, "source": row.source, "member": username or "(member gone)", "member_id": row.member_id,
            "created_at": row.created_at.isoformat() if row.created_at else None, "app_version": row.app_version, "message": row.message,
            "diagnostics": row.diagnostics, "done": row.done_at is not None, "done_at": row.done_at.isoformat() if row.done_at else None}


def admin_list(db: Session, limit: int = 100) -> list[dict[str, Any]]:
    rows = list(db.scalars(select(VpnSupportReport).order_by(VpnSupportReport.id.desc()).limit(limit)))
    people = dict(db.execute(select(Member.id, Member.username).where(Member.id.in_({r.member_id for r in rows}))).all()) if rows else {}
    return [out(r, people.get(r.member_id)) for r in rows]


def set_done(db: Session, report_id: int, done: bool) -> dict[str, Any] | None:
    row = db.get(VpnSupportReport, report_id)
    if row is None:
        return None
    row.done_at = (row.done_at or utcnow()) if done else None
    db.commit()
    member = db.get(Member, row.member_id)
    return out(row, member.username if member else None)
