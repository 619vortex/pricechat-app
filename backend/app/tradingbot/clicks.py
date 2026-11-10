"""Referral click tracking: every place the Hyperliquid referral link is shown points at /api/r/{placement}, which counts the
click and 302s to the current referral link. Stored: placement, time, coarse source (telegram / web / referer HOST). Never an IP,
user agent or member id. Hyperliquid sign-ups themselves stay in affiliate.py; this only says which spot sends people there."""

import re
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingbot import TbReferralClick

API = "https://api.pricechat.live"  # where Telegram links point (the website uses its own same-origin /api)
PLACEMENTS = {"card", "footer", "start", "help", "gate"}
_SITE = re.compile(r"^site_[a-z0-9_]{1,24}$")  # site_<page>
_BOTS = ("bot", "crawl", "spider", "slurp", "preview", "facebookexternalhit", "embedly", "iframely", "headless", "curl", "wget",
         "python-requests", "httpx", "go-http", "whatsapp", "skypeuri")  # link previews and scripts: not counted, UA never stored
SOURCES = {"tg": "telegram", "telegram": "telegram", "web": "web"}
WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}


def placement_of(raw: str) -> str:
    p = (raw or "").strip().lower()
    return p if p in PLACEMENTS or _SITE.match(p) else "other"


def source_of(src: str | None, referer: str | None) -> str:
    """?src= first (tg/web), else the referer's host only, else 'direct'."""
    if src and src.lower() in SOURCES:
        return SOURCES[src.lower()]
    host = (urlparse(referer).hostname or "").lower() if referer else ""
    if not host:
        return "direct"
    if host in ("t.me", "telegram.org", "web.telegram.org") or host.endswith(".telegram.org"):
        return "telegram"
    if host == "pricechat.live" or host.endswith(".pricechat.live"):
        return "web"
    return host[:64]


def is_bot(user_agent: str | None) -> bool:
    ua = (user_agent or "").lower()
    return not ua or any(b in ua for b in _BOTS)


def record(db: Session, placement: str, source: str) -> None:
    db.add(TbReferralClick(at=utcnow(), placement=placement_of(placement), source=source[:64]))
    db.commit()


def tracked_url(placement: str, src: str = "tg") -> str:
    return f"{API}/api/r/{placement_of(placement)}?src={src}"


def counts(db: Session) -> dict[str, Any]:
    """Clicks per placement for 24h / 7d / 30d (+ per source over 30d), busiest first."""
    now = utcnow()
    rows: dict[str, dict[str, int]] = {}
    for key, span in WINDOWS.items():
        q = select(TbReferralClick.placement, func.count()).where(TbReferralClick.at >= now - span).group_by(TbReferralClick.placement)
        for p, n in db.execute(q):
            rows.setdefault(p, {k: 0 for k in WINDOWS})[key] = int(n)
    q = (select(TbReferralClick.source, func.count()).where(TbReferralClick.at >= now - WINDOWS["30d"])
         .group_by(TbReferralClick.source))
    sources = {s: int(n) for s, n in db.execute(q)}
    placements = sorted(({"placement": p, **v} for p, v in rows.items()), key=lambda r: (-r["30d"], -r["7d"], r["placement"]))
    totals = {k: sum(r[k] for r in placements) for k in WINDOWS}
    return {"placements": placements, "totals": totals, "sources_30d": sources}
