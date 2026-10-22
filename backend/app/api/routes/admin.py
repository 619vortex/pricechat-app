"""Owner login for the internal dashboard: login, logout, and "am I signed in?"."""

from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import Field

from app.api.deps import DbSession, Runtime
from app.api.schemas import StrictInput
from app.security import admin_auth

router = APIRouter(tags=["admin"])

AdminCookie = Annotated[str | None, Cookie(alias=admin_auth.COOKIE)]
DeviceCookie = Annotated[str | None, Cookie(alias=admin_auth.DEVICE_COOKIE)]


class AdminLogin(StrictInput):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.get("/admin/session")
def admin_session(db: DbSession, runtime: Runtime, pc_admin: AdminCookie = None) -> dict[str, Any]:
    """`required`: does this server ask for the owner login? `authenticated`: is this browser signed in?"""
    required = runtime.settings.admin_auth_required
    row = admin_auth.current(db, pc_admin) if required else None
    return {"required": required, "authenticated": (not required) or row is not None, "username": row.username if row else None}


@router.post("/admin/login")
def admin_login(payload: AdminLogin, request: Request, response: Response, db: DbSession, runtime: Runtime,
                pc_admin_device: DeviceCookie = None) -> Any:
    key = admin_auth.client_key(request)
    device = admin_auth.known_device(runtime.settings, pc_admin_device)  # a browser that signed in before: its own limit
    wait = admin_auth.retry_after(key, device)
    if wait is not None:
        return JSONResponse(status_code=status.HTTP_429_TOO_MANY_REQUESTS, headers={"Retry-After": str(wait)},
                            content={"detail": "Too many attempts. Please wait a few minutes and try again.", "code": "rate_limited"})
    if not admin_auth.check_credentials(runtime.settings, payload.username, payload.password):
        admin_auth.record_failure(key, device)
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED,
                            content={"detail": "Wrong username or password.", "code": "invalid_credentials"})
    admin_auth.start_session(db, runtime.settings, response, payload.username.strip())
    admin_auth.remember_device(runtime.settings, response)  # renewed on every sign-in; kept after logout
    return {"authenticated": True, "username": payload.username.strip()}


@router.post("/admin/logout")
def admin_logout(response: Response, db: DbSession, pc_admin: AdminCookie = None) -> dict[str, bool]:
    admin_auth.end_session(db, response, pc_admin)
    return {"ok": True}
