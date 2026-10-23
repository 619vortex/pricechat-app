"""Inbound webhooks from outside services. Public (admin_auth.PUBLIC_PREFIXES "/api/webhooks/"): each one authenticates
the sender itself, by signature, over the raw request body."""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.deps import DbSession, Runtime
from app.brand_manager import tiktok_webhook
from app.security.rate_limit import limit

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/tiktok", include_in_schema=False, dependencies=[Depends(limit("webhook"))])
async def tiktok(request: Request, db: DbSession, runtime: Runtime) -> Any:
    """TikTok webhook deliveries (and the Developer Portal's Test URL): 200 once the TikTok-Signature checks out."""
    body = await request.body()  # the raw bytes: the signature covers them exactly as sent
    try:
        got = tiktok_webhook.receive(db, runtime.settings, request.headers.get(tiktok_webhook.HEADER), body)
    except tiktok_webhook.WebhookError as exc:
        header = request.headers.get(tiktok_webhook.HEADER)
        why = tiktok_webhook.diagnose(runtime.settings, header, body) if header and exc.code in ("bad_signature", "stale") else {}
        tiktok_webhook.log.warning("TikTok webhook refused: %s (%s, %d bytes, signature header %s) %s", exc.code, exc.status, len(body),
                                   "present" if header else "missing", why)
        return JSONResponse(status_code=exc.status, content={"detail": str(exc), "code": exc.code})
    return {"ok": True, "event": got.event, "duplicate": got.duplicate}
