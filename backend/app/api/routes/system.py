"""Owner-only maintenance: a consistent copy of the SQLite database, streamed to the owner (off-server backup).

Admin-only (not on admin_auth's public list). The copy is built with SQLite's online backup API in the container's
temporary disk, never on the data volume (which holds the live database and has little room to spare), and is
deleted once sent. Secrets in the database stay encrypted (the key is an environment variable, not in the file)."""

import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import Field

from app.api.deps import DbSession, Runtime
from app.api.schemas import StrictInput

router = APIRouter(tags=["system"])


@router.get("/system/db-backup")
def db_backup(db: DbSession, tasks: BackgroundTasks) -> FileResponse:
    bind = db.get_bind()
    path = bind.url.database if bind.url.get_backend_name() == "sqlite" else None
    if not path or not os.path.exists(path):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only a SQLite database file can be backed up this way.")
    fd, tmp = tempfile.mkstemp(prefix="price_chat-backup-", suffix=".db")
    os.close(fd)
    src, dst = sqlite3.connect(path), sqlite3.connect(tmp)
    try:
        src.backup(dst)  # consistent snapshot while the app keeps running
    finally:
        dst.close()
        src.close()
    tasks.add_task(os.remove, tmp)
    name = f"price_chat-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.db"
    return FileResponse(tmp, media_type="application/octet-stream", filename=name)


# --- email sign-in: the sender domain at Resend (owner only) ------------------------------------------------------------


@router.get("/system/email")
async def email_status(runtime: Runtime) -> dict[str, Any]:
    """Is email sign-in configured, and is the sender domain verified at Resend (with the DNS records it needs)?"""
    from app.services import mailer

    out: dict[str, Any] = {"configured": mailer.configured(runtime.settings), "from": runtime.settings.email_from}
    if out["configured"]:
        try:
            out["domain"] = await mailer.domain_status(runtime.settings, transport=getattr(runtime, "members_transport", None))
        except mailer.MailError as exc:
            out["error"] = str(exc)
    return out


@router.post("/system/email/domain")
async def email_domain_setup(runtime: Runtime) -> dict[str, Any]:
    """Add the sender domain at Resend if needed and ask Resend to verify its DNS records now."""
    from app.services import mailer

    try:
        return await mailer.domain_status(runtime.settings, create=True, verify=True, transport=getattr(runtime, "members_transport", None))
    except mailer.MailError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


class TestEmail(StrictInput):
    to: str = Field(min_length=3, max_length=254)


@router.post("/system/email/test")
async def email_test(payload: TestEmail, runtime: Runtime) -> dict[str, Any]:
    """Send one test message (a sample code that signs nobody in) to check real delivery."""
    from app.members.email_login import normalise
    from app.services import mailer

    try:
        to = normalise(payload.to)
        subject, text, html = mailer.code_email("000000", runtime.settings.email_code_minutes)
        msg_id = await mailer.send(runtime.settings, to, "[Test] " + subject, "This is a delivery test.\n\n" + text, html,
                                   getattr(runtime, "members_transport", None))
    except (mailer.MailError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"sent": True, "id": msg_id}


# --- QA: a dedicated test member for checking member features on the live site (owner only) ----------------------------

QA_USERNAME = "qa_scanner"


@router.post("/system/qa-member-session")
def qa_member_session(db: DbSession) -> dict[str, Any]:
    """A session for the ONE dedicated QA member (created on first use; private, never on leaderboards), so the owner
    can test member features end to end in production. It can't sign in as anyone else."""
    from sqlalchemy import select

    from app.members import service as members
    from app.models.members import Member

    m = db.scalar(select(Member).where(Member.username_key == QA_USERNAME))
    if m is None:
        m = members.create_member(db)
        m.username, m.username_key, m.username_chosen = QA_USERNAME, QA_USERNAME, True
    m.leaderboard_opt_out, m.public_profile = True, False
    db.commit()
    return {"username": m.username, "token": members.new_session(db, m, 1)}


@router.delete("/system/qa-member")
def delete_qa_member(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Remove the QA member and everything it created (its photos are deleted from the bucket first)."""
    from sqlalchemy import select

    from app.members import service as members
    from app.models.members import Member
    from app.models.portfolio import PokemonHolding, PokemonHoldingPhoto, PokemonPortfolio
    from app.portfolio import photos

    m = db.scalar(select(Member).where(Member.username_key == QA_USERNAME))
    if m is None:
        return {"deleted": False}
    store = photos.Storage(runtime.settings)
    gone = 0
    for ph in db.scalars(select(PokemonHoldingPhoto).join(PokemonHolding, PokemonHolding.id == PokemonHoldingPhoto.holding_id)
                         .join(PokemonPortfolio, PokemonPortfolio.id == PokemonHolding.portfolio_id).where(PokemonPortfolio.member_id == m.id)):
        for k in (ph.key, ph.thumb_key):
            try:
                store.delete(k)
                gone += 1
            except Exception:  # noqa: BLE001
                pass
    gone += _delete_qa_market(db, store, m.id)
    members.delete_member(db, m, actor="admin")
    return {"deleted": True, "photo_objects_removed": gone}


def _delete_qa_market(db: Any, store: Any, member_id: int) -> int:
    """The QA member's marketplace test rows (its own items, listings, orders...). Only ever called for the QA account."""
    from sqlalchemy import delete, or_, select

    from app.models.market import (
        MarketDispute, MarketItem, MarketLedgerEntry, MarketListing, MarketListingEvent, MarketOffer, MarketOrder, MarketPayment, MarketPhoto,
        MarketRedemption, MarketReport, MarketSeller, MarketTransfer, MarketWatch,
    )

    items = list(db.scalars(select(MarketItem.id).where(MarketItem.owner_member_id == member_id)))
    listings = list(db.scalars(select(MarketListing.id).where(or_(MarketListing.seller_id == member_id, MarketListing.item_id.in_(items or [0])))))
    orders = list(db.scalars(select(MarketOrder.id).where(or_(MarketOrder.buyer_id == member_id, MarketOrder.seller_id == member_id,
                                                              MarketOrder.listing_id.in_(listings or [0])))))
    others = db.scalar(select(MarketOrder.id).where(MarketOrder.id.in_(orders or [0]), MarketOrder.buyer_id != member_id, MarketOrder.seller_id != member_id))
    if others:
        raise HTTPException(status.HTTP_409_CONFLICT, "QA marketplace rows involve another member: not deleting automatically.")
    gone = 0
    for ph in db.scalars(select(MarketPhoto).where(MarketPhoto.item_id.in_(items or [0]))):
        for k in (ph.key, ph.thumb_key):
            try:
                store.delete(k)
                gone += 1
            except Exception:  # noqa: BLE001
                pass
    for stmt in (delete(MarketPayment).where(MarketPayment.order_id.in_(orders or [0])), delete(MarketLedgerEntry).where(MarketLedgerEntry.order_id.in_(orders or [0])),
                 delete(MarketDispute).where(or_(MarketDispute.order_id.in_(orders or [0]), MarketDispute.opened_by == member_id)),
                 delete(MarketOrder).where(MarketOrder.id.in_(orders or [0])), delete(MarketOffer).where(or_(MarketOffer.buyer_id == member_id, MarketOffer.listing_id.in_(listings or [0]))),
                 delete(MarketWatch).where(or_(MarketWatch.member_id == member_id, MarketWatch.listing_id.in_(listings or [0]))),
                 delete(MarketReport).where(MarketReport.listing_id.in_(listings or [0])), delete(MarketListingEvent).where(MarketListingEvent.listing_id.in_(listings or [0])),
                 delete(MarketListing).where(MarketListing.id.in_(listings or [0])), delete(MarketRedemption).where(MarketRedemption.member_id == member_id),
                 delete(MarketTransfer).where(MarketTransfer.item_id.in_(items or [0])), delete(MarketPhoto).where(MarketPhoto.item_id.in_(items or [0])),
                 delete(MarketItem).where(MarketItem.id.in_(items or [0])), delete(MarketSeller).where(MarketSeller.member_id == member_id)):
        db.execute(stmt)
    db.flush()
    return gone


# --- owner alerts: a private Telegram message when something important breaks (app/alerts.py) ---------------------------


class OwnerAlertsIn(StrictInput):
    enabled: bool | None = None
    telegram_chat: str | None = Field(None, max_length=32, pattern=r"^(-?\d{4,20})?$")  # numeric Telegram user id; "" = log only


@router.get("/system/alerts")
def owner_alerts(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    from app import alerts

    out = alerts.settings_out(db, runtime.settings)
    db.commit()
    return out


@router.put("/system/alerts")
def update_owner_alerts(payload: OwnerAlertsIn, db: DbSession, runtime: Runtime) -> dict[str, Any]:
    from app import alerts

    row = alerts.settings_row(db)
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.telegram_chat is not None:
        row.telegram_chat = payload.telegram_chat.strip()
    db.commit()
    return alerts.settings_out(db, runtime.settings)


@router.post("/system/alerts/link")
def link_owner_alerts(db: DbSession) -> dict[str, Any]:
    """A one-time deep link: open it on the phone, press Start, and the bot makes that chat the owner-alerts chat (and confirms there)."""
    from app import alerts
    from app.telegram.ingestion import get_bot_state

    bot = get_bot_state(db).bot_username
    if not bot:
        raise HTTPException(status.HTTP_409_CONFLICT, "The Telegram bot isn't connected yet (no bot username).")
    code, expires = alerts.start_owner_link(db)
    return {"url": f"https://t.me/{bot}?start={alerts.OWNER_LINK_PREFIX}{code}", "expires_at": expires.isoformat()}


@router.post("/system/alerts/test")
async def test_owner_alert(db: DbSession, runtime: Runtime) -> dict[str, Any]:
    """Send one test message now (no cooldown) and say exactly why it failed if it did."""
    from app import alerts

    chat = alerts.owner_chat(db)
    if chat is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Set your Telegram user id (and turn alerts on) first.")
    try:
        await alerts.send_text(runtime.settings, chat, "Price Chat: test alert. Owner alerts reach you here.", runtime.telegram.transport)
    except Exception as exc:  # noqa: BLE001 - shown to the owner (e.g. "chat not found": press Start in the bot first)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Telegram didn't deliver it: {exc}") from exc
    return {"sent": True}


# --- key rotation (owner only) ------------------------------------------------------------------------------------------


@router.post("/system/rekey")
def rekey(db: DbSession) -> dict[str, Any]:
    """Re-encrypt every stored secret under APP_ENCRYPTION_KEY (old keys in APP_ENCRYPTION_KEY_PREVIOUS). Counts only."""
    from app.security import rekey as rk

    return rk.run(db)
