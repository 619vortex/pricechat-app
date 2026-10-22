"""OAuth 1.0a (HMAC-SHA1) request signing for X user-context requests (RFC 5849)."""

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import quote, urlsplit, urlunsplit


def _enc(value: str) -> str:
    return quote(str(value), safe="~")


def authorization_header(
    method: str,
    url: str,
    params: dict[str, str],
    consumer_key: str,
    consumer_secret: str,
    token: str,
    token_secret: str,
    nonce: str | None = None,
    timestamp: str | None = None,
    extra_oauth: dict[str, str] | None = None,
) -> str:
    """`token=""` signs without a token (the request-token step of "Sign in with X"); `extra_oauth` adds protocol
    parameters such as oauth_callback / oauth_verifier to the signed header."""
    oauth = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_version": "1.0",
        **({"oauth_token": token} if token else {}),
        **(extra_oauth or {}),
    }
    parts = urlsplit(url)
    base_url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))
    all_params = {**{k: str(v) for k, v in params.items()}, **oauth}
    pairs = sorted((_enc(k), _enc(v)) for k, v in all_params.items())
    param_string = "&".join(f"{k}={v}" for k, v in pairs)
    base_string = "&".join([method.upper(), _enc(base_url), _enc(param_string)])
    key = f"{_enc(consumer_secret)}&{_enc(token_secret)}"
    signature = base64.b64encode(hmac.new(key.encode(), base_string.encode(), hashlib.sha1).digest()).decode()
    oauth["oauth_signature"] = signature
    return "OAuth " + ", ".join(f'{_enc(k)}="{_enc(v)}"' for k, v in sorted(oauth.items()))
