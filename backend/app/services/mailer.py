"""Transactional email through Resend (resend.com): the passwordless sign-in codes, plus the owner's one-time domain
setup (add pricechat.live, read its DNS records, ask Resend to verify them).

The API key is resolved server-side (encrypted store, then .env) and never logged, returned or sent to the browser.
Outside production, when no key is set, a code is written to the server log instead (development only; production
never falls back: it refuses to send and the website says email sign-in is unavailable).
"""

import logging
from typing import Any

import httpx

from app.config.settings import Settings
from app.security.credentials import resolve

logger = logging.getLogger(__name__)

API = "https://api.resend.com"


class MailError(RuntimeError):
    """Sending failed. The message is safe to show the owner; members only ever see a generic sentence."""


def configured(settings: Settings) -> bool:
    return bool(resolve("resend_api_key", settings))


def available(settings: Settings) -> bool:
    """Can a sign-in code be delivered right now? (Production: only with a real provider.)"""
    return configured(settings) or not settings.is_production


def _key(settings: Settings) -> str:
    key = resolve("resend_api_key", settings)
    if not key:
        raise MailError("Email sending isn't set up: save the Resend API key on Settings › API keys.")
    return key


async def _call(settings: Settings, method: str, path: str, json: dict[str, Any] | None = None, transport: Any = None) -> Any:
    async with httpx.AsyncClient(timeout=15, transport=transport) as client:
        r = await client.request(method, API + path, json=json, headers={"Authorization": f"Bearer {_key(settings)}"})
    if r.status_code >= 400:
        try:
            msg = r.json().get("message") or r.text
        except ValueError:
            msg = r.text
        raise MailError(f"Resend refused the request ({r.status_code}): {str(msg)[:200]}")
    return r.json() if r.content else {}


async def send(settings: Settings, to: str, subject: str, text: str, html: str, transport: Any = None) -> str:
    """Send one email; returns the provider's message id ("dev-log" when only logged in development)."""
    if not configured(settings):
        if settings.is_production:
            raise MailError("Email sending isn't set up.")
        logger.warning("DEVELOPMENT ONLY (no Resend key): email to %s | %s | %s", to, subject, text)
        return "dev-log"
    body = await _call(settings, "POST", "/emails", {"from": settings.email_from, "to": [to], "subject": subject, "text": text, "html": html},
                       transport)
    return str(body.get("id") or "")


def code_email(code: str, minutes: int) -> tuple[str, str, str]:
    """(subject, text, html) for a sign-in code. No links: the code is typed on the page that asked for it."""
    subject = f"{code} is your Price Chat code"
    text = (f"Your Price Chat sign-in code is {code}\n\nIt expires in {minutes} minutes and works once.\n\n"
            "If you didn't ask for it, ignore this email: nobody can sign in without the code.")
    spaced = " ".join(code)
    html = f"""<!doctype html><html><body style="margin:0;background:#050806;font-family:Arial,Helvetica,sans-serif;color:#f4f6f5">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:40px 16px">
<table role="presentation" width="100%" style="max-width:440px;background:#0c120f;border:1px solid #1d2a23;border-radius:16px">
<tr><td style="padding:32px 28px">
<div style="font-weight:900;font-size:20px;letter-spacing:.02em">PRICE <span style="color:#00e58f">CHAT</span></div>
<p style="margin:24px 0 8px;font-size:15px;color:#b7c2bc">Your sign-in code</p>
<div style="font-size:34px;font-weight:800;letter-spacing:.18em;color:#00e58f">{spaced}</div>
<p style="margin:20px 0 0;font-size:13px;line-height:1.5;color:#8b9891">It expires in {minutes} minutes and works once.<br>
If you didn't ask for it, ignore this email: nobody can sign in without the code.</p>
</td></tr></table></td></tr></table></body></html>"""
    return subject, text, html


# --- owner: domain setup ---------------------------------------------------------------------------------------------


def _domain_of(settings: Settings) -> str:
    sender = settings.email_from
    addr = sender[sender.find("<") + 1: sender.rfind(">")] if "<" in sender else sender
    return addr.split("@", 1)[1].strip().lower()


async def domain_status(settings: Settings, create: bool = False, verify: bool = False, transport: Any = None) -> dict[str, Any]:
    """The sender domain at Resend: its status and the DNS records it needs (public values, no secrets).
    create=True adds the domain when Resend doesn't have it yet; verify=True asks Resend to check the DNS now."""
    name = _domain_of(settings)
    listed = await _call(settings, "GET", "/domains", transport=transport)
    dom = next((d for d in listed.get("data", []) if d.get("name") == name), None)
    if dom is None and create:
        dom = await _call(settings, "POST", "/domains", {"name": name}, transport)
    if dom is None:
        return {"domain": name, "exists": False, "status": "not_added", "records": []}
    if verify:
        await _call(settings, "POST", f"/domains/{dom['id']}/verify", transport=transport)
    full = await _call(settings, "GET", f"/domains/{dom['id']}", transport=transport)
    records = [{k: r.get(k) for k in ("record", "name", "type", "value", "priority", "status", "ttl")} for r in full.get("records", [])]
    return {"domain": name, "exists": True, "id": full.get("id"), "status": full.get("status"), "region": full.get("region"), "records": records}
