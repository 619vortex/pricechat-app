"""Telegram account mode (official client API): credentials, login, discovery, authorisation, history import.

Codes and passwords are passed straight to Telegram; they are never stored, logged or returned.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select

from app.api.community_schemas import TelegramSourceOut
from app.api.deps import DbSession, Runtime
from app.api.schemas import StrictInput
from app.models.enums import ActivityLevel
from app.models.telegram import TelegramSource
from app.services.activity import ActivityLogger
from app.telegram import account_service as svc

router = APIRouter(prefix="/telegram/account", tags=["telegram"])


class AccountCredentialsIn(StrictInput):
    api_id: int
    api_hash: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9a-fA-F]{32}$")] | None = None


class PhoneIn(StrictInput):
    phone: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\+?[0-9][0-9 ()-]{5,20}$")]


class CodeIn(StrictInput):
    code: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{3,8}$")]


class PasswordIn(StrictInput):
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)]


class AuthorizeIn(StrictInput):
    chat_id: int


class DialogOut(BaseModel):
    chat_id: int
    title: str
    chat_type: str
    username: str | None
    members: int | None
    authorized: bool


@router.get("")
def account_status(db: DbSession, runtime: Runtime) -> dict[str, object]:
    return svc.status(db, runtime.settings, runtime.telegram_account)


@router.put("/credentials")
def save_credentials(payload: AccountCredentialsIn, db: DbSession, runtime: Runtime) -> dict[str, object]:
    """API ID + API Hash from my.telegram.org. The hash is stored encrypted and never shown again."""
    if not 1 <= payload.api_id <= 2_147_483_647:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "API ID must be the number shown on my.telegram.org")
    if payload.api_hash is None and not svc.configured(db, runtime.settings):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Paste the API Hash too")
    svc.configure(db, runtime.settings, payload.api_id, payload.api_hash)
    return svc.status(db, runtime.settings, runtime.telegram_account)


@router.delete("/credentials")
def delete_credentials(db: DbSession, runtime: Runtime) -> dict[str, object]:
    svc.forget_credentials(db, runtime.settings)
    return svc.status(db, runtime.settings, runtime.telegram_account)


@router.post("/login")
async def start_login(payload: PhoneIn, db: DbSession, runtime: Runtime) -> dict[str, object]:
    """Telegram sends a login code to the Telegram app (or SMS) of this phone number."""
    await svc.start_login(db, runtime.settings, runtime.telegram_account, payload.phone.replace(" ", ""))
    return svc.status(db, runtime.settings, runtime.telegram_account)


@router.post("/code")
async def submit_code(payload: CodeIn, db: DbSession, runtime: Runtime) -> dict[str, object]:
    result = await svc.submit_code(db, runtime.settings, runtime.telegram_account, payload.code)
    return {"result": result, **svc.status(db, runtime.settings, runtime.telegram_account)}


@router.post("/password")
async def submit_password(payload: PasswordIn, db: DbSession, runtime: Runtime) -> dict[str, object]:
    result = await svc.submit_password(db, runtime.settings, runtime.telegram_account, payload.password)
    return {"result": result, **svc.status(db, runtime.settings, runtime.telegram_account)}


@router.post("/qr")
async def qr_start(db: DbSession, runtime: Runtime) -> dict[str, object]:
    """Start a QR login: returns the tg://login URL to show as a QR code (scan it in the Telegram app)."""
    url = await svc.start_qr(db, runtime.settings, runtime.telegram_account)
    return {"url": url, "account": svc.status(db, runtime.settings, runtime.telegram_account)}


@router.post("/qr/poll")
async def qr_poll(db: DbSession, runtime: Runtime) -> dict[str, object]:
    r = await svc.poll_qr(db, runtime.settings, runtime.telegram_account)
    return {**r, "account": svc.status(db, runtime.settings, runtime.telegram_account)}


@router.post("/cancel")
async def cancel_login(db: DbSession, runtime: Runtime) -> dict[str, object]:
    await svc.cancel_login(runtime.telegram_account)
    return svc.status(db, runtime.settings, runtime.telegram_account)


@router.post("/logout")
async def logout(db: DbSession, runtime: Runtime) -> dict[str, object]:
    """Logs the session out on Telegram's side and deletes it from this computer. Authorised sources and data stay."""
    await svc.logout(db, runtime.settings, runtime.telegram_account)
    return svc.status(db, runtime.settings, runtime.telegram_account)


@router.post("/dialogs", response_model=list[DialogOut])
async def discover(db: DbSession, runtime: Runtime) -> list[DialogOut]:
    """Groups and channels this account can access (names only; no messages are read). Private chats are never listed."""
    dialogs = await svc.discover(db, runtime.settings, runtime.telegram_account)
    allowed = set(db.scalars(select(TelegramSource.chat_id)))
    return [DialogOut(chat_id=d.chat_id, title=d.title, chat_type=d.chat_type, username=d.username, members=d.members,
                      authorized=d.chat_id in allowed) for d in dialogs]


@router.post("/sources", response_model=TelegramSourceOut, status_code=status.HTTP_201_CREATED)
def authorize(payload: AuthorizeIn, db: DbSession, runtime: Runtime) -> TelegramSourceOut:
    """AUTHORIZE FOR PRICE CHAT: add one group/channel the account can see to the allow-list.
    Only messages sent from now on are read, unless you import recent history explicitly."""
    d = next((x for x in runtime.telegram_account.discovered if x.chat_id == payload.chat_id), None)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not among the groups/channels this account can access: refresh the list first")
    if db.scalar(select(TelegramSource).where(TelegramSource.chat_id == d.chat_id)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This chat is already on the allow-list")
    src = TelegramSource(chat_id=d.chat_id, display_name=d.title[:256] or str(d.chat_id), chat_type=d.chat_type, username=d.username,
                         enabled=True, ingestion_enabled=True, via="account")
    db.add(src)
    db.commit()
    runtime.telegram.authorised_chats += 1
    ActivityLogger(db).log("telegram_source_added", f"Authorised '{src.display_name}' for Price Chat (read through the Telegram account)",
                           level=ActivityLevel.AUDIT, actor="dashboard", details={"chat_id": src.chat_id, "via": "account"})
    return TelegramSourceOut.model_validate(src)


def _account_source(db: DbSession, source_id: int) -> TelegramSource:
    src = db.get(TelegramSource, source_id)
    if src is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Telegram source not found")
    return src


@router.get("/sources/{source_id}/import-estimate")
async def import_estimate(source_id: int, db: DbSession, runtime: Runtime, hours: int = Query(..., description="24, 168 or 720")) -> dict[str, object]:
    src = _account_source(db, source_id)
    n = await svc.estimate(db, runtime.settings, runtime.telegram_account, src, hours)
    return {"hours": hours, "estimated_messages": n, "cap": svc.IMPORT_CAP,
            "note": "Approximate upper bound (deleted and service messages are included in the count)."}


class ImportIn(StrictInput):
    hours: int


@router.post("/sources/{source_id}/import")
async def import_history(source_id: int, payload: ImportIn, db: DbSession, runtime: Runtime) -> dict[str, object]:
    src = _account_source(db, source_id)
    result = await svc.import_history(db, runtime.settings, runtime.telegram_account, src, payload.hours)
    if result["stored"] and runtime.scheduler is not None:
        runtime.scheduler.request_run("community.process", 2)
    return result
