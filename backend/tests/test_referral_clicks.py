"""Tracked referral links: /api/r/{placement} counts a click (placement, time, coarse source only) and 302s to the referral link;
every bot surface shows the tracked link; the owner sees clicks per placement for 24h / 7d / 30d."""

import asyncio
from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingbot import TbReferralClick
from app.security import admin_auth
from app.tradingbot import clicks
from app.tradingbot.service import settings as earn_settings
from app.tradingos import bot

R = "https://api.pricechat.live/api/r/"
UA = {"user-agent": "Mozilla/5.0 (iPhone) Safari"}


class TG:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def send_message(self, chat_id: Any, text: str, buttons: Any = None, html: bool = False, reply_to: Any = None) -> dict[str, Any]:
        self.sent.append((text, buttons))
        return {"message_id": len(self.sent)}


def _rows(db: Session) -> list[TbReferralClick]:
    db.expire_all()
    return list(db.scalars(select(TbReferralClick).order_by(TbReferralClick.id)))


def test_redirect_records_the_click_and_nothing_personal(client: TestClient, db: Session) -> None:
    r = client.get("/api/r/card?src=tg", headers=UA, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://app.hyperliquid.xyz/join/BANTER"
    assert r.headers["cache-control"] == "no-store"
    client.get("/api/r/site_earn", headers={**UA, "referer": "https://pricechat.live/earn?x=1"}, follow_redirects=False)
    client.get("/api/r/../../etc", headers=UA, follow_redirects=False)  # not a route at all
    client.get("/api/r/WeIrD-spot", headers={**UA, "referer": "https://news.example.com/a/b"}, follow_redirects=False)
    client.get("/api/r/footer", headers={"user-agent": "TelegramBot (like TwitterBot)"}, follow_redirects=False)  # link preview
    rows = _rows(db)
    assert [(c.placement, c.source) for c in rows] == [("card", "telegram"), ("site_earn", "web"), ("other", "news.example.com")]
    assert set(TbReferralClick.__table__.columns.keys()) == {"id", "at", "placement", "source"}  # no IP / UA / member column
    assert admin_auth.is_public("GET", "/api/r/card") and not admin_auth.is_public("GET", "/api/tradingbot/admin/referral-clicks")


def test_no_referral_link_redirects_to_the_site_and_hides_the_links(client: TestClient, db: Session) -> None:
    earn_settings(db).referral_url = ""
    db.commit()
    r = client.get("/api/r/footer?src=tg", headers=UA, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == bot.SITE and _rows(db) == []
    assert bot.tracked_url(db, "card") is None and bot.referral_line(db) == "" and bot.signup_block(db, "start") == ""


def test_redirect_follows_the_current_referral_link(client: TestClient, db: Session) -> None:
    earn_settings(db).referral_url = "https://app.hyperliquid.xyz/join/KIWI"
    db.commit()
    assert client.get("/api/r/start", headers=UA, follow_redirects=False).headers["location"].endswith("/join/KIWI")


def test_bot_surfaces_use_tracked_urls(db: Session) -> None:
    assert bot.referral_url(db) == "https://app.hyperliquid.xyz/join/BANTER"  # the raw link stays available
    assert bot.footer(db).endswith(f"🎁 4% off Hyperliquid fees: {R}footer?src=tg")
    assert bot.signup_block(db, "help").endswith(f"{R}help?src=tg\n\n")
    tg = TG()
    asyncio.run(bot.handle_command(db, tg, 1, 9, "/start"))  # private /start: pitch + its own button
    text, buttons = tg.sent[-1]
    assert text.startswith("🎁 <b>4% off Hyperliquid fees</b>") and f"{R}start?src=tg" in text
    assert buttons[-1] == [{"text": "🎁 Sign up on Hyperliquid · 4% off fees", "url": f"{R}start?src=tg"}]
    asyncio.run(bot.handle_command(db, tg, 1, 9, "/help"))
    assert f"{R}help?src=tg" in tg.sent[-1][0] and "hyperliquid.xyz/join" not in tg.sent[-1][0]


def test_admin_counts_per_placement_and_window(client: TestClient, db: Session) -> None:
    now = utcnow()
    for placement, age in (("card", 1), ("card", 30), ("card", 24 * 10), ("footer", 2), ("site_earn", 24 * 40)):
        db.add(TbReferralClick(at=now - timedelta(hours=age), placement=placement, source="telegram"))
    db.commit()
    out = client.get("/api/tradingbot/admin/referral-clicks").json()
    assert out["placements"] == [{"placement": "card", "24h": 1, "7d": 2, "30d": 3}, {"placement": "footer", "24h": 1, "7d": 1, "30d": 1}]
    assert out["totals"] == {"24h": 2, "7d": 3, "30d": 4} and out["sources_30d"] == {"telegram": 4}
    assert clicks.placement_of("site_trading_bot") == "site_trading_bot" and clicks.placement_of("site_<x>") == "other"
