"""Backend-only secret store.

Secrets typed into the dashboard (Telegram bot token, X credentials) and members' X tokens are encrypted:
  - with APP_ENCRYPTION_KEY (Fernet: AES + HMAC) whenever that key is set: production on Linux;
  - otherwise with Windows DPAPI (CryptProtectData, current-user scope): local development on Windows, where only
    this Windows account on this computer can decrypt them.
In production (ENVIRONMENT=production) nothing is ever stored unencrypted: without a key, saving refuses.
Values encrypted with the key are tagged, so a store can hold both (a Windows dev store read with a key set still
decrypts its DPAPI entries). Secrets are never returned by the API, never logged and never sent to a model.
"""

import base64
import ctypes
import json
import logging
import os
import sys
import threading
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_ENTROPY = b"PriceChat secret store v1"


class SecretStoreError(Exception):
    pass


# --- DPAPI -----------------------------------------------------------------------------


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data: bytes) -> tuple[_Blob, ctypes.Array]:  # type: ignore[type-arg]
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _from_blob(blob: _Blob) -> bytes:
    data = ctypes.string_at(blob.pbData, blob.cbData)
    ctypes.windll.kernel32.LocalFree(blob.pbData)  # type: ignore[attr-defined]
    return data


def dpapi_available() -> bool:
    return sys.platform == "win32"


_FERNET_TAG = b"fernet:"


def _keys() -> list:  # type: ignore[type-arg]
    """APP_ENCRYPTION_KEY then each APP_ENCRYPTION_KEY_PREVIOUS key, as Fernet ciphers ([] when no key is set)."""
    from cryptography.fernet import Fernet

    from app.config.settings import get_settings

    s = get_settings()
    raw = s.app_encryption_key.get_secret_value().strip() if s.app_encryption_key else ""
    if not raw:
        return []
    old = s.app_encryption_key_previous.get_secret_value() if s.app_encryption_key_previous else ""
    out = []
    for name, k in [("APP_ENCRYPTION_KEY", raw)] + [("APP_ENCRYPTION_KEY_PREVIOUS", k.strip()) for k in old.split(",") if k.strip()]:
        try:
            out.append(Fernet(k.encode()))
        except ValueError:
            raise SecretStoreError(f"{name} is not a valid Fernet key") from None
    return out


def _fernet():  # type: ignore[no-untyped-def]
    """The production cipher, when APP_ENCRYPTION_KEY is set (else None): encrypts with that key, decrypts with it or
    any APP_ENCRYPTION_KEY_PREVIOUS key (key rotation)."""
    keys = _keys()
    if not keys:
        return None
    from cryptography.fernet import MultiFernet

    return MultiFernet(keys)


def reseal(data: bytes) -> bytes | None:
    """Re-encrypt a stored value under the current key (key rotation, app/security/rekey.py).
    None = nothing to do (already under the current key, or not a Fernet value: DPAPI / plain).
    Raises SecretStoreError when no configured key can decrypt it."""
    if not data.startswith(_FERNET_TAG):
        return None
    keys = _keys()
    if not keys:
        raise SecretStoreError("A stored secret needs APP_ENCRYPTION_KEY to decrypt")
    from cryptography.fernet import InvalidToken, MultiFernet

    token = data[len(_FERNET_TAG):]
    try:
        keys[0].decrypt(token)
        return None
    except InvalidToken:
        pass
    try:
        return _FERNET_TAG + MultiFernet(keys).rotate(token)
    except InvalidToken:
        raise SecretStoreError("Could not decrypt a stored secret with any configured key") from None


def _production() -> bool:
    from app.config.settings import get_settings

    return get_settings().is_production


def protect(data: bytes) -> bytes:
    f = _fernet()
    if f is not None:
        return _FERNET_TAG + f.encrypt(data)
    if not dpapi_available():
        if _production():
            raise SecretStoreError("Refusing to store a secret unencrypted: set APP_ENCRYPTION_KEY")
        return data
    blob_in, _keep = _to_blob(data)
    entropy, _keep2 = _to_blob(_ENTROPY)
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptProtectData(  # type: ignore[attr-defined]
        ctypes.byref(blob_in), "PriceChat", ctypes.byref(entropy), None, None, 0x1, ctypes.byref(out)  # 0x1 = UI_FORBIDDEN
    )
    if not ok:
        raise SecretStoreError("Windows could not encrypt the secret (DPAPI)")
    return _from_blob(out)


def unprotect(data: bytes) -> bytes:
    if data.startswith(_FERNET_TAG):
        f = _fernet()
        if f is None:
            raise SecretStoreError("A stored secret needs APP_ENCRYPTION_KEY to decrypt")
        from cryptography.fernet import InvalidToken

        try:
            return f.decrypt(data[len(_FERNET_TAG):])
        except InvalidToken:
            raise SecretStoreError("Could not decrypt a stored secret (APP_ENCRYPTION_KEY changed? put the old one in APP_ENCRYPTION_KEY_PREVIOUS)") from None
    if not dpapi_available():
        if _production():
            raise SecretStoreError("A stored secret was not encrypted with APP_ENCRYPTION_KEY")
        return data
    blob_in, _keep = _to_blob(data)
    entropy, _keep2 = _to_blob(_ENTROPY)
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(  # type: ignore[attr-defined]
        ctypes.byref(blob_in), None, ctypes.byref(entropy), None, None, 0x1, ctypes.byref(out)
    )
    if not ok:
        raise SecretStoreError("Could not decrypt a stored secret (saved by another Windows user or computer?)")
    return _from_blob(out)


# --- store -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SecretInfo:
    name: str
    configured: bool
    source: str | None  # "dashboard" | "env" | None
    saved_at: datetime | None


class SecretStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _read(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SecretStoreError(f"Secret store is unreadable: {type(exc).__name__}") from None

    def _write(self, data: dict[str, dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        if not dpapi_available():
            os.chmod(tmp, 0o600)  # owner-only, on top of the encryption
        os.replace(tmp, self.path)

    def get(self, name: str) -> str | None:
        with self._lock:
            entry = self._read().get(name)
        if not entry:
            return None
        return unprotect(base64.b64decode(entry["value"])).decode("utf-8")

    def set(self, name: str, value: str) -> None:
        value = value.strip()
        if not value:
            raise ValueError("Secret value is empty")
        with self._lock:
            data = self._read()
            data[name] = {
                "value": base64.b64encode(protect(value.encode("utf-8"))).decode("ascii"),
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
            self._write(data)

    def delete(self, name: str) -> bool:
        with self._lock:
            data = self._read()
            existed = data.pop(name, None) is not None
            if existed:
                self._write(data)
            return existed

    def reseal(self) -> dict[str, int]:
        """Re-encrypt every entry under the current key (key rotation). Counts only; values are never returned."""
        out = {"rotated": 0, "current": 0, "failed": 0, "other": 0}
        with self._lock:
            data = self._read()
            for entry in data.values():
                try:
                    raw = base64.b64decode(entry["value"])
                except (KeyError, TypeError, ValueError):
                    out["other"] += 1
                    continue
                if not raw.startswith(_FERNET_TAG):
                    out["other"] += 1  # DPAPI (Windows dev) or plain: not ours to rotate
                    continue
                try:
                    new = reseal(raw)
                except SecretStoreError:
                    out["failed"] += 1  # left as it is
                    continue
                if new is None:
                    out["current"] += 1
                    continue
                entry["value"] = base64.b64encode(new).decode("ascii")
                out["rotated"] += 1
            if out["rotated"]:
                self._write(data)
        return out

    def saved_at(self, name: str) -> datetime | None:
        with self._lock:
            entry = self._read().get(name)
        return datetime.fromisoformat(entry["saved_at"]) if entry else None
