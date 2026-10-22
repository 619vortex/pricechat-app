"""Kiwi VPN API.

Member routes live under /api/members/vpn... (public prefix; the member is whoever the pc_session cookie says, never an id in
the request). Admin routes live under /api/vpn/admin/... (owner login: every non-public /api route). The gate and all node
calls are in app.vpn.service, not here. A device's config (with its private key) is returned once and never stored.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import Field

from app.api.deps import DbSession
from app.api.routes.members import SessionToken, _require
from app.api.schemas import StrictInput
from app.security.rate_limit import LIMITS
from app.vpn import service
from app.vpn.service import VpnError

router = APIRouter(tags=["vpn"])


def _fail(e: VpnError) -> HTTPException:
    return HTTPException(e.status, str(e))


# --- member -------------------------------------------------------------------------------------------------------


@router.get("/members/vpn")
def my_vpn(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    return service.overview(db, _require(db, pc_session))


class DeviceInput(StrictInput):
    name: str = Field(min_length=1, max_length=32)
    server_id: int


@router.post("/members/vpn/devices", status_code=status.HTTP_201_CREATED)
def create_device(payload: DeviceInput, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    member = _require(db, pc_session)
    wait = LIMITS["vpn_device"].hit(str(member.id))
    if wait is not None:
        raise HTTPException(429, "Too many new devices. Please wait a few minutes.", headers={"Retry-After": str(wait)})
    try:
        dev, config, filename = service.create_device(db, member, payload.name, payload.server_id)
    except VpnError as e:
        raise _fail(e) from e
    return {"device": service.device_out(dev), "config": config, "filename": filename}


@router.delete("/members/vpn/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(device_id: int, db: DbSession, pc_session: SessionToken = None) -> Response:
    try:
        service.delete_device(db, _require(db, pc_session), device_id)
    except VpnError as e:
        raise _fail(e) from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- admin (owner login) ------------------------------------------------------------------------------------------


@router.get("/vpn/admin/overview")
def admin_overview(db: DbSession) -> dict[str, Any]:
    return service.admin_overview(db)


class SettingsUpdate(StrictInput):
    enabled: bool | None = None
    min_rank: str | None = Field(None, min_length=1, max_length=16)
    max_devices: int | None = Field(None, ge=1, le=service.MAX_DEVICES_LIMIT)
    monthly_cap_gb: int | None = Field(None, ge=0, le=service.MAX_CAP_GB)  # per member per month, 0 = unlimited


@router.put("/vpn/admin/settings")
def admin_settings(payload: SettingsUpdate, db: DbSession) -> dict[str, Any]:
    try:
        return service.update_settings(db, payload.model_dump(exclude_unset=True))
    except VpnError as e:
        raise _fail(e) from e


class ServerInput(StrictInput):
    name: str = Field(min_length=1, max_length=48)
    region: str = Field("", max_length=48)
    country_code: str = Field(min_length=2, max_length=2)
    agent_url: str = Field(min_length=9, max_length=255)
    agent_token: str = Field(min_length=32, max_length=512)
    cert_sha256: str = Field(min_length=64, max_length=128)
    capacity: int | None = Field(None, ge=1, le=100000)


class ServerUpdate(StrictInput):
    name: str | None = Field(None, min_length=1, max_length=48)
    region: str | None = Field(None, max_length=48)
    country_code: str | None = Field(None, min_length=2, max_length=2)
    capacity: int | None = Field(None, ge=1, le=100000)
    enabled: bool | None = None
    agent_token: str | None = Field(None, min_length=32, max_length=512)
    cert_sha256: str | None = Field(None, min_length=64, max_length=128)


def _server(db: DbSession, server_id: int) -> dict[str, Any]:
    return next(s for s in service.admin_overview(db)["servers"] if s["id"] == server_id)


@router.post("/vpn/admin/servers")
def admin_add_server(payload: ServerInput, db: DbSession) -> dict[str, Any]:
    try:
        server = service.add_server(db, payload.model_dump())
    except VpnError as e:
        raise _fail(e) from e
    return _server(db, server.id)


@router.patch("/vpn/admin/servers/{server_id}")
def admin_update_server(server_id: int, payload: ServerUpdate, db: DbSession) -> dict[str, Any]:
    try:
        service.update_server(db, server_id, payload.model_dump(exclude_unset=True))
    except VpnError as e:
        raise _fail(e) from e
    return _server(db, server_id)


@router.delete("/vpn/admin/servers/{server_id}")
def admin_delete_server(server_id: int, db: DbSession) -> dict[str, Any]:
    try:
        service.delete_server(db, server_id)
    except VpnError as e:
        raise _fail(e) from e
    return {"ok": True}


@router.post("/vpn/admin/servers/{server_id}/test")
def admin_test_server(server_id: int, db: DbSession) -> dict[str, Any]:
    try:
        return service.test_server(db, server_id)
    except VpnError as e:
        raise _fail(e) from e
