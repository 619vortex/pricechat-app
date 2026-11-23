"""Admin -> Side Projects (owner, 2026-11-23): separate projects the admin only links to. GUMMY & BAKED Audit keeps its own backend,
database and sign-in; Price Chat stores and reads none of its data. This route only says where it lives, from two settings, and only to
the signed-in owner (every /api route outside admin_auth.PUBLIC_* needs the admin session). Read-only.

A link is shown only when it is a real deployed https address: a localhost, private-network or plain-http address is never sent, so
until the audit is deployed the admin shows "Not available yet"."""

import ipaddress
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter

from app.api.deps import Runtime

router = APIRouter(tags=["side-projects"])


def deployed_url(value: str | None) -> str | None:
    """The address if it is a public https URL; None for anything else (empty, http, localhost, a private or reserved IP, a bare name)."""
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        u = urlsplit(raw)
        host = (u.hostname or "").lower()
    except ValueError:
        return None
    if u.scheme != "https" or not host or u.username or u.password:
        return None
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan")):
        return None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return raw if "." in host else None  # a bare machine name is not a deployed address
    return raw if ip.is_global else None


@router.get("/admin/side-projects")
def side_projects(runtime: Runtime) -> dict[str, Any]:
    s = runtime.settings
    site, moderation = deployed_url(s.gummy_audit_url), deployed_url(s.gummy_audit_admin_url)
    return {"projects": [{
        "key": "gummy-baked", "label": "GUMMY & BAKED Audit",
        "about": "The separate public audit of the GUMMY/BAKED airdrop campaign: its own website, backend, database and moderator sign-in.",
        "available": site is not None, "site_url": site, "admin_url": moderation if site is not None else None,
    }]}
