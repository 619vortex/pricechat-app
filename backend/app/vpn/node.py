"""HTTP client for the node agent on each VPN server (the contract: GET /v1/status, PUT / DELETE /v1/peers/{key}).

The agent uses a self-signed certificate, so no CA is trusted: the TLS connection is accepted only when the SHA-256 of the
agent's DER certificate equals the fingerprint pinned on the server record. The check runs inside the TLS handshake (a
custom SSLSocket / SSLObject class on the context), so a wrong or missing certificate fails the connection BEFORE any
request, and the bearer token is never sent to an unpinned peer. 10 s timeouts, no proxies, no redirects.
"""

import hashlib
import hmac
import re
import ssl
from typing import Any

import httpx

from app.vpn.keys import is_key, url_key

TIMEOUT = 10.0
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class NodeError(Exception):
    """The agent couldn't be reached, refused us, or answered something we don't accept. The message is safe to show."""


class PinMismatch(ssl.SSLError):
    pass


def normalize_fingerprint(value: str) -> str:
    """'AB:CD:...' / 'abcd...' / with spaces -> 64 lower-case hex characters (ValueError otherwise)."""
    fp = re.sub(r"[\s:]", "", value or "").lower()
    if not _HEX64.match(fp):
        raise ValueError("The certificate fingerprint must be a SHA-256 (64 hex characters).")
    return fp


def check_pin(der: bytes | None, expected_sha256: str) -> None:
    """Raise PinMismatch unless the DER certificate's SHA-256 is the pinned one (constant-time compare)."""
    if not der:
        raise PinMismatch("The VPN agent presented no certificate")
    if not expected_sha256 or not hmac.compare_digest(hashlib.sha256(der).hexdigest(), expected_sha256.lower()):
        raise PinMismatch("The VPN agent's certificate doesn't match the pinned fingerprint")


class _PinnedSocket(ssl.SSLSocket):
    def do_handshake(self, *args: Any, **kwargs: Any) -> None:
        super().do_handshake(*args, **kwargs)
        try:
            check_pin(self.getpeercert(binary_form=True), getattr(self.context, "pin_sha256", ""))
        except PinMismatch:
            self.close()
            raise


class _PinnedObject(ssl.SSLObject):
    def do_handshake(self) -> None:
        super().do_handshake()
        check_pin(self.getpeercert(binary_form=True), getattr(self.context, "pin_sha256", ""))


class PinnedContext(ssl.SSLContext):
    """TLS client context that trusts exactly one certificate, by fingerprint (no CA, no hostname)."""

    sslsocket_class = _PinnedSocket
    sslobject_class = _PinnedObject
    pin_sha256: str = ""


def pinned_context(cert_sha256: str) -> PinnedContext:
    ctx = PinnedContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # replaced by the fingerprint check in the handshake
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.pin_sha256 = normalize_fingerprint(cert_sha256)
    return ctx


def std_key(key: str) -> str:
    """A key the agent reports, URL-safe or standard base64, as standard WireGuard base64 (how devices store it)."""
    return key.replace("-", "+").replace("_", "/")


class NodeClient:
    def __init__(self, agent_url: str, token: str, cert_sha256: str, transport: httpx.BaseTransport | None = None,
                 timeout: float = TIMEOUT) -> None:
        url = (agent_url or "").rstrip("/")
        if transport is None:
            if not url.lower().startswith("https://"):
                raise NodeError("The agent URL must be https://")
            try:
                transport = httpx.HTTPTransport(verify=pinned_context(cert_sha256), retries=0)
            except ValueError as e:
                raise NodeError(str(e)) from None
        self._client = httpx.Client(base_url=url, transport=transport, timeout=httpx.Timeout(timeout), trust_env=False,
                                    follow_redirects=False, headers={"Authorization": f"Bearer {token}"})

    def __repr__(self) -> str:  # never the token
        return f"NodeClient({self._client.base_url!s})"

    def __enter__(self) -> "NodeClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        try:
            r = self._client.request(method, path, json=body)
        except httpx.TimeoutException:
            raise NodeError("The VPN agent didn't answer in time") from None
        except httpx.TransportError as e:  # the message may hold the URL; never anything secret, but keep it short
            if "fingerprint" in str(e) or "no certificate" in str(e):
                raise NodeError("The VPN agent's certificate doesn't match the pinned fingerprint") from None
            raise NodeError(f"Can't reach the VPN agent ({type(e).__name__})") from None
        if r.status_code == 401:
            raise NodeError("The VPN agent refused the token")
        if r.status_code != 200:
            raise NodeError(f"The VPN agent answered HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise NodeError("The VPN agent answered something that isn't JSON") from None

    def status(self) -> dict[str, Any]:
        data = self._call("GET", "/v1/status")
        try:
            peers = [{"public_key": std_key(str(p["public_key"])), "allowed_ip": str(p.get("allowed_ip") or ""), "rx_bytes": int(p.get("rx_bytes") or 0),
                      "tx_bytes": int(p.get("tx_bytes") or 0), "latest_handshake": int(p.get("latest_handshake") or 0)}
                     for p in data.get("peers") or []]
            out = {"public_key": std_key(str(data["public_key"])), "listen_port": int(data["listen_port"]),
                   "endpoint_host": str(data["endpoint_host"]), "address": str(data["address"]),
                   "dns": [str(d) for d in data.get("dns") or []], "peers": peers,
                   "adblock": data.get("adblock") is True}  # an agent that doesn't report it doesn't block
        except (KeyError, TypeError, ValueError, AttributeError):
            raise NodeError("The VPN agent's status is malformed") from None
        if not is_key(out["public_key"]):
            raise NodeError("The VPN agent reported an invalid server key")
        return out

    def put_peer(self, public_key: str, allowed_ip: str) -> None:
        self._call("PUT", f"/v1/peers/{url_key(public_key)}", {"allowed_ip": allowed_ip})

    def delete_peer(self, public_key: str) -> None:
        self._call("DELETE", f"/v1/peers/{url_key(public_key)}")


def connect(agent_url: str, token: str, cert_sha256: str) -> NodeClient:
    """Every agent connection goes through here (tests replace it with a fake node)."""
    return NodeClient(agent_url, token, cert_sha256)


def client_for(server: Any) -> NodeClient:
    """The client for one VpnServer row (decrypts its agent token)."""
    from app.vpn.service import agent_token

    return connect(server.agent_url, agent_token(server), server.cert_sha256)
