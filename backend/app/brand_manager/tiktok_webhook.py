"""TikTok webhooks (Developer Portal › Webhooks › Callback URL = https://pricechat.live/api/webhooks/tiktok).

TikTok POSTs JSON ({client_key, event, create_time, user_openid, content}) and signs every delivery
(developers.tiktok.com/doc/webhooks-verification): header `TikTok-Signature: t=<unix seconds>,s=<hex>`, where
s = HMAC-SHA256(key=client_secret, msg="<t>.<raw request body>"). The signature is checked over the RAW body bytes before
anything is parsed; a delivery older (or newer) than MAX_SKEW_S is refused as a replay. TikTok delivers at least once and
retries for 72 h, so each accepted body is recorded by its SHA-256 and a repeat is acknowledged without being handled again.

Public route (no Price Chat session: TikTok's servers have none); the signature with the app's client secret is the auth.
Nothing here logs the body, the signature or the secret: event name, open_id prefix and outcome only."""

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.models.brand_manager import BmTiktokWebhookEvent
from app.security.credentials import resolve

log = logging.getLogger("pricechat.tiktok.webhook")

HEADER = "TikTok-Signature"
MAX_SKEW_S = 300  # a signed delivery is accepted for 5 minutes (TikTok signs each retry anew)
MAX_BODY = 64 * 1024


class WebhookError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


@dataclass
class Accepted:
    event: str
    duplicate: bool


def parse_header(value: str | None) -> tuple[int, str]:
    """`t=1633174587,s=1849...` -> (timestamp, lowercase hex signature)."""
    parts: dict[str, str] = {}
    for item in (value or "").split(","):
        k, sep, v = item.strip().partition("=")
        if sep:
            parts[k.strip()] = v.strip()
    t, s = parts.get("t", ""), parts.get("s", "").lower()
    if not t.isdigit() or len(s) != 64 or any(c not in "0123456789abcdef" for c in s):
        raise WebhookError(401, "bad_signature", f"Missing or malformed {HEADER} header")
    return int(t), s


def sign(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def verify(settings: Settings, header: str | None, body: bytes, now: float | None = None) -> None:
    """Raises WebhookError unless the delivery is signed with our client secret within MAX_SKEW_S."""
    secret = resolve("tiktok_client_secret", settings)
    if not secret:
        raise WebhookError(503, "not_configured", "TikTok client secret isn't configured (Settings › API keys)")
    timestamp, given = parse_header(header)
    if not hmac.compare_digest(sign(secret, timestamp, body), given):
        raise WebhookError(401, "bad_signature", "Signature doesn't match")
    if abs((time.time() if now is None else now) - timestamp) > MAX_SKEW_S:
        raise WebhookError(401, "stale", "Signature timestamp outside the 5-minute window")


def diagnose(settings: Settings, header: str | None, body: bytes, now: float | None = None) -> dict[str, Any]:
    """Why a signed delivery didn't verify, for the refusal log: facts only (yes/no, numbers, where the secret comes from),
    never the secret, the signature or the body. `variant` names another signing layout that WOULD match, if any; the
    delivery is refused either way."""
    from app.security.credentials import info

    out: dict[str, Any] = {}
    try:
        data = json.loads(body)
        ck = data.get("client_key") if isinstance(data, dict) else None
        out["client_key"] = "absent" if not ck else "ours" if ck == resolve("tiktok_client_key", settings) else "other"
    except (ValueError, UnicodeDecodeError):
        out["client_key"] = "unparsed"
    s_info = info("tiktok_client_secret", settings)
    secret = resolve("tiktok_client_secret", settings) or ""
    out["secret"] = f"{s_info.source}/{len(secret)}"
    try:
        t, given = parse_header(header)
    except WebhookError:
        return out
    out["skew_s"] = int((time.time() if now is None else now) - t)
    if secret:
        def mac(key: bytes, msg: bytes) -> str:
            return hmac.new(key, msg, hashlib.sha256).hexdigest()

        k = secret.encode()
        variants = {"body_only": mac(k, body), "no_dot": mac(k, f"{t}".encode() + body),
                    "stripped_secret": mac(secret.strip().encode(), f"{t}.".encode() + body)}
        out["variant"] = next((name for name, v in variants.items() if hmac.compare_digest(v, given)), "none")
    return out


def parse(settings: Settings, body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise WebhookError(400, "malformed", "Body isn't JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("event"), str) or not data["event"].strip():
        raise WebhookError(400, "malformed", "Body has no event")
    ours = resolve("tiktok_client_key", settings)
    if ours and data.get("client_key") not in (None, "", ours):
        raise WebhookError(400, "wrong_app", "Event is for another client key")
    return data


def receive(db: Session, settings: Settings, header: str | None, body: bytes, now: float | None = None,
            at: datetime | None = None) -> Accepted:
    """Verify, parse, deduplicate and record one delivery."""
    if len(body) > MAX_BODY:
        raise WebhookError(413, "too_large", "Body too large")
    verify(settings, header, body, now)
    data = parse(settings, body)
    event, openid = data["event"].strip()[:64], str(data.get("user_openid") or "")[:128] or None
    digest = hashlib.sha256(body).hexdigest()
    if db.scalar(select(BmTiktokWebhookEvent.id).where(BmTiktokWebhookEvent.body_sha256 == digest)) is not None:
        log.info("TikTok webhook duplicate ignored: event=%s", event)
        return Accepted(event, True)
    ct = data.get("create_time")
    db.add(BmTiktokWebhookEvent(body_sha256=digest, event=event, user_openid=openid,
                                create_time=int(ct) if isinstance(ct, (int, float)) or (isinstance(ct, str) and ct.isdigit()) else None,
                                received_at=at or utcnow()))
    try:
        db.commit()
    except IntegrityError:  # the same delivery arriving twice at once
        db.rollback()
        return Accepted(event, True)
    log.info("TikTok webhook accepted: event=%s user=%s", event, (openid or "-")[:8])
    return Accepted(event, False)
