"""Google OAuth 2.0 for the optional OAuth connection (authorization code + PKCE, read-only scope).

Standard flow only (https://developers.google.com/identity/protocols/oauth2/native-app):
dashboard → Google consent screen → redirect back to the backend with a one-time code → the
backend exchanges it for a refresh token (stored encrypted) → short-lived access tokens on demand.
No password ever passes through Price Chat.
"""

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.youtube.client import YouTubeAuthError, YouTubeUnavailable

SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
STATE_TTL = 600  # seconds a started login stays valid


@dataclass(slots=True)
class PendingLogin:
    verifier: str
    created: float


def new_login() -> tuple[str, PendingLogin, str]:
    """(state, pending, code_challenge)"""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return secrets.token_urlsafe(24), PendingLogin(verifier, time.monotonic()), challenge


def authorization_url(auth_url: str, client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    params = {
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    }
    return f"{auth_url}?{urlencode(params)}"


async def _token_request(token_url: str, data: dict[str, str], transport: httpx.AsyncBaseTransport | None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=20.0, transport=transport) as client:
            response = await client.post(token_url, data=data, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise YouTubeUnavailable(f"Cannot reach Google ({type(exc).__name__})") from None
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code >= 500:
        raise YouTubeUnavailable(f"Google sign-in server error {response.status_code}")
    if response.status_code != 200 or "access_token" not in body:
        # e.g. invalid_grant (code reused/expired, or access revoked), invalid_client (wrong client id/secret)
        raise YouTubeAuthError(f"Google refused the sign-in: {body.get('error', response.status_code)}"
                               + (f" ({body['error_description']})" if body.get("error_description") else ""))
    return body


async def exchange_code(token_url: str, client_id: str, client_secret: str, code: str, verifier: str, redirect_uri: str,
                        transport: httpx.AsyncBaseTransport | None = None) -> dict:
    return await _token_request(token_url, {
        "grant_type": "authorization_code", "code": code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri, "code_verifier": verifier,
    }, transport)


async def refresh(token_url: str, client_id: str, client_secret: str, refresh_token: str,
                  transport: httpx.AsyncBaseTransport | None = None) -> dict:
    return await _token_request(token_url, {
        "grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id, "client_secret": client_secret,
    }, transport)
