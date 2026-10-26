"""Re-encrypt everything sealed with APP_ENCRYPTION_KEY under a new key (key rotation).

OPERATOR STEPS (e.g. after the host's 2026-10-21 incident):
  1. New key:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  2. Set APP_ENCRYPTION_KEY=<new> and APP_ENCRYPTION_KEY_PREVIOUS=<old> (several old keys: comma-separated).
  3. Deploy. Startup runs the pass once in the background (log lines "Rekey: ..."), or run it yourself:
     POST /api/system/rekey (owner login) -> counts per place. Safe to repeat: a second run rotates nothing.
  4. Check the counts: "failed" must be 0 everywhere (a failed value is left as it was and still needs a key that
     isn't configured). Then remove APP_ENCRYPTION_KEY_PREVIOUS and redeploy.
Rotating the key does not make leaked secrets safe: if the old key AND the database were exposed, revoke the
underlying credentials at their source too (bot token, X / Google / TikTok tokens, Hyperliquid API wallets, ...).
Database backups taken before the rotation stay readable only with the old key.
Email sign-in hashes (app/members/email_login.py) are keyed from APP_ENCRYPTION_KEY too, but hold nothing that
needs rotating: the change only voids codes pending at that moment (10-minute life; the member asks for a new one)
and restarts the per-address / per-IP send limits. Members' addresses are stored on Member.email, not hashed.

Every place that stores Fernet-sealed data is listed below (SEALING_MODULES / COLUMNS + the secret store file);
tests/test_rekey.py fails when a new protect() user or *_enc column appears without being listed here.
Values that aren't Fernet (Windows DPAPI in local development, or plain) are counted as "other" and left alone.
Secret values are never logged or returned: only counts.
"""

import base64
import binascii
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.security.secrets import SecretStoreError, _fernet, reseal

logger = logging.getLogger(__name__)

BATCH = 200

# Modules that call protect() (directly or via app.tradingos.exchange.enc). Adding one means adding its columns below.
SEALING_MODULES = {
    "app/security/secrets.py", "app/invites/service.py", "app/members/auth.py", "app/members/rewards.py",
    "app/members/x_actions.py", "app/tradingbot/service.py", "app/market/service.py", "app/tradingos/exchange.py",
    "app/vpn/service.py",
}

# how each site wraps protect()'s bytes into a text column
_B64: tuple[Callable[[str], bytes], Callable[[bytes], str]] = (base64.b64decode, lambda b: base64.b64encode(b).decode())
_LATIN1: tuple[Callable[[str], bytes], Callable[[bytes], str]] = (lambda s: s.encode("latin-1"), lambda b: b.decode("latin-1"))


def columns() -> list[tuple[Any, str, tuple[Callable[[str], bytes], Callable[[bytes], str]]]]:
    """(model, attribute, wrapping) for every database column holding protect() output."""
    from app.models.invites import InviteAccount
    from app.models.market import MarketRedemption
    from app.models.members import MemberAuthRequest, MemberXToken
    from app.models.rewards import RewardSettings
    from app.models.trading import TrAccount
    from app.models.tradingbot import TbApplication
    from app.models.kalshi import KalshiLink
    from app.models.vpn import VpnServer

    return [
        (InviteAccount, "session_enc", _B64),  # Telethon sessions (invites/service.py)
        (MemberAuthRequest, "secret", _B64),  # sign-in PKCE verifiers / OAuth1 secrets (members/auth.py)
        (MemberXToken, "access_token", _B64),  # members' X tokens (members/x_actions.py)
        (MemberXToken, "refresh_token", _B64),
        (RewardSettings, "vip_invite_enc", _B64),  # Royal VIP invite link (members/rewards.py)
        (TbApplication, "invite_link_enc", _B64),  # trading bot group invite links (tradingbot/service.py)
        (MarketRedemption, "address_enc", _LATIN1),  # shipping addresses (market/service.py)
        (TrAccount, "testnet_agent_key", _B64),  # Hyperliquid API-wallet keys (tradingos/exchange.py enc)
        (TrAccount, "mainnet_agent_key", _B64),
        (VpnServer, "agent_token_enc", _B64),  # Kiwi VPN node agent tokens (vpn/service.py)
        (KalshiLink, "key_enc", _B64),  # members' Kalshi API keys (kalshi/member.py)
    ]


def _counts() -> dict[str, int]:
    return {"rotated": 0, "current": 0, "failed": 0, "other": 0}


def _column(db: Session, model: Any, attr: str, wrap: Any, batch: int) -> dict[str, int]:
    out = _counts()
    unwrap, rewrap = wrap
    col, last = getattr(model, attr), 0
    keep = {model.updated_at: model.updated_at} if hasattr(model, "updated_at") else {}  # a re-seal isn't an edit
    while True:
        rows = db.execute(select(model.id, col).where(model.id > last, col.is_not(None)).order_by(model.id).limit(batch)).all()
        if not rows:
            return out
        for rid, value in rows:
            last = rid
            try:
                raw = unwrap(value)
            except (binascii.Error, ValueError, UnicodeError):
                out["other"] += 1
                continue
            try:
                new = reseal(raw)
            except SecretStoreError:
                out["failed"] += 1  # left as it is
                continue
            if new is None:
                out["current" if raw.startswith(b"fernet:") else "other"] += 1
                continue
            # only if unchanged since we read it (the app may have written a fresh value meanwhile)
            res = db.execute(update(model).where(model.id == rid, col == value).values({col: rewrap(new), **keep})
                             .execution_options(synchronize_session=False))
            out["rotated" if res.rowcount else "current"] += 1
        db.commit()


def run(db: Session, settings: Settings | None = None, batch: int = BATCH) -> dict[str, Any]:
    """Re-seal every stored value under the current key. Idempotent; returns counts per place."""
    from app.security import credentials

    settings = settings or get_settings()
    _fernet()  # a malformed key setting stops here (SecretStoreError), before anything is counted as undecryptable
    sites: dict[str, dict[str, int]] = {}
    for model, attr, wrap in columns():
        name = f"{model.__tablename__}.{attr}"
        try:
            sites[name] = _column(db, model, attr, wrap, batch)
        except Exception as exc:  # noqa: BLE001 - one broken table doesn't stop the others
            db.rollback()
            logger.warning("Rekey: %s failed (%s)", name, type(exc).__name__)
            sites[name] = {**_counts(), "error": 1}
    try:
        sites["secret store"] = credentials.secret_store(settings).reseal()
    except SecretStoreError as exc:
        logger.warning("Rekey: secret store failed (%s)", exc)
        sites["secret store"] = {**_counts(), "error": 1}
    totals = _counts()
    for name, c in sites.items():
        for k in totals:
            totals[k] += c.get(k, 0)
        logger.info("Rekey: %s rotated=%d current=%d failed=%d other=%d", name, c["rotated"], c["current"], c["failed"], c["other"])
    prev = settings.app_encryption_key_previous.get_secret_value() if settings.app_encryption_key_previous else ""
    return {"previous_keys": len([k for k in prev.split(",") if k.strip()]), "totals": totals, "sites": sites}


def run_at_startup(session_factory: Callable[[], Session]) -> None:
    """The startup pass (background thread): errors are logged, never fatal."""
    try:
        with session_factory() as db:
            out = run(db)
        logger.warning("Rekey finished: %s (remove APP_ENCRYPTION_KEY_PREVIOUS once failed=0)", out["totals"])
    except Exception:  # noqa: BLE001
        logger.exception("Rekey at startup failed")
