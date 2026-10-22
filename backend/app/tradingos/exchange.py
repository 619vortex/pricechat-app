"""Trade Execution adapters and API-wallet authorization.

PaperExecutor: simulated fills at the real Hyperliquid price plus slippage and fees (no exchange call).
HyperliquidExecutor: real orders on testnet or mainnet through the official SDK, signed by the member's approved API wallet
("agent"). This code only trades with it (orders, cancels, leverage): it never sends withdrawals or transfers. Every answer is Hyperliquid's own: an order is only
FILLED when the exchange says filled, with its size and average price.

Authorization: the server creates the API wallet key (it never leaves the server, encrypted at rest), the member's own
wallet signs Hyperliquid's ApproveAgent message in the browser, the server checks it came from the member's verified
wallet, submits it, and confirms on Hyperliquid that the agent is listed.
"""

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.security.secrets import protect, unprotect
from app.tradingos import risk

def _keccak_fallback() -> None:
    """Where the signing library's native Keccak can't load (e.g. a Windows dev machine that blocks unsigned DLLs), use
    our pure-Python Keccak-256 (app.tradingbot.evm, checked against known vectors). Production (Linux) uses the native one."""
    try:
        from Crypto.Hash import keccak  # noqa: F401
    except OSError:
        from eth_hash.auto import keccak as auto

        from app.tradingbot.evm import keccak256

        auto.hasher = keccak256  # type: ignore[attr-defined]


_keccak_fallback()

URLS = {"TESTNET": "https://api.hyperliquid-testnet.xyz", "LIVE": "https://api.hyperliquid.xyz"}
APPROVE_TYPES = [{"name": "hyperliquidChain", "type": "string"}, {"name": "agentAddress", "type": "address"},
                 {"name": "agentName", "type": "string"}, {"name": "nonce", "type": "uint64"}]
AGENT_NAME = "PriceChat"


class ExchangeError(Exception):
    pass


@dataclass
class Result:
    status: str  # FILLED | PARTIAL | RESTING | REJECTED | ERROR
    filled: float = 0.0
    avg_px: float | None = None
    oid: int | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def cloid_for(*parts: Any) -> str:
    """Deterministic client order id (0x + 32 hex): the same request always has the same id (idempotent retries)."""
    return "0x" + hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:32]


def enc(key: str) -> str:
    return base64.b64encode(protect(key.encode())).decode()


def dec(blob: str) -> str:
    return unprotect(base64.b64decode(blob)).decode()


SDK_TIMEOUT = 15.0  # seconds per Hyperliquid request through the SDK


def check_cancel(resp: Any) -> None:
    """Hyperliquid's cancel answer: ok, or "never placed, already canceled, or filled" (gone either way); anything else raises."""
    if isinstance(resp, dict) and resp.get("status") == "ok":
        try:
            st = resp["response"]["data"]["statuses"][0]
        except (KeyError, IndexError, TypeError):
            st = None
        if st == "success":
            return
        err = str(st.get("error", "")) if isinstance(st, dict) else ""
        if "already canceled" in err or "never placed" in err:
            return
    raise ExchangeError(f"Hyperliquid refused the cancel: {str(resp)[:200]}")


def parse_status(resp: Any) -> Result:
    """Hyperliquid's order response → Result. Anything unrecognised is an ERROR, never a fill."""
    if not isinstance(resp, dict) or resp.get("status") != "ok":
        return Result("ERROR", error=str(resp)[:300], raw=resp if isinstance(resp, dict) else {"raw": str(resp)[:300]})
    try:
        st = resp["response"]["data"]["statuses"][0]
    except (KeyError, IndexError, TypeError):
        return Result("ERROR", error="no order status in Hyperliquid's answer", raw=resp)
    if not isinstance(st, dict):  # e.g. a bare string: `"filled" in st` would be a substring test, never a fill
        return Result("ERROR", error=f"unknown status {str(st)[:200]}", raw=resp)
    if "filled" in st:
        f = st["filled"]
        return Result("FILLED", float(f.get("totalSz") or 0), float(f.get("avgPx") or 0) or None, f.get("oid"), raw=resp)
    if "resting" in st:
        return Result("RESTING", oid=st["resting"].get("oid"), raw=resp)
    if "error" in st:
        return Result("REJECTED", error=str(st["error"])[:300], raw=resp)
    return Result("ERROR", error=f"unknown status {st}", raw=resp)


class PaperExecutor:
    """Paper trading: no exchange orders. Fills at the real price ± assumed slippage; triggers are simulated by the tracker."""

    mode = "PAPER"

    def __init__(self, equity: float, mark: float) -> None:
        self._equity, self._mark = equity, mark

    def equity(self) -> float:
        return self._equity

    def set_leverage(self, coin: str, lev: int) -> None:
        return None

    def market(self, coin: str, is_buy: bool, size: float, limit_px: float, cloid: str, reduce_only: bool = False) -> Result:
        px = self._mark * (1 + risk.SLIPPAGE if is_buy else 1 - risk.SLIPPAGE)
        return Result("FILLED", size, px, raw={"paper": True})

    def trigger(self, coin: str, is_buy: bool, size: float, trigger_px: float, kind: str, cloid: str) -> Result:
        return Result("RESTING", raw={"paper": True, "trigger": trigger_px, "kind": kind})

    def cancel(self, coin: str, oid: int | None) -> None:
        return None


class HyperliquidExecutor:
    """Real orders (testnet or mainnet) through the official SDK, signed by the approved API wallet."""

    def __init__(self, mode: str, agent_key: str, master: str) -> None:
        import eth_account
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info

        self.mode, self.master = mode, master
        self.url = URLS[mode]
        # the SDK's default is no timeout: one stalled connection would hang an entry (its OPENING row) or a close (its row lock)
        # forever. A timeout raises, and every caller treats a raise as "may have reached the exchange" (adopt / reconcile).
        self.info = Info(self.url, skip_ws=True, timeout=SDK_TIMEOUT)
        self.ex = Exchange(eth_account.Account.from_key(agent_key), self.url, account_address=master, timeout=SDK_TIMEOUT)

    def equity(self) -> float:
        st = self.info.user_state(self.master)
        return trading_equity(self.mode, self.master, st)

    def state(self) -> dict[str, Any]:
        return self.info.user_state(self.master)

    def set_leverage(self, coin: str, lev: int) -> None:
        r = self.ex.update_leverage(lev, coin, is_cross=False)  # isolated margin
        if not isinstance(r, dict) or r.get("status") != "ok":
            raise ExchangeError(f"Hyperliquid refused the leverage change: {str(r)[:200]}")

    def market(self, coin: str, is_buy: bool, size: float, limit_px: float, cloid: str, reduce_only: bool = False) -> Result:
        from hyperliquid.utils.types import Cloid

        r = self.ex.order(coin, is_buy, size, limit_px, {"limit": {"tif": "Ioc"}}, reduce_only=reduce_only, cloid=Cloid.from_str(cloid))
        res = parse_status(r)
        if res.status == "FILLED" and res.filled + 1e-12 < size:
            res.status = "PARTIAL"
        return res

    def trigger(self, coin: str, is_buy: bool, size: float, trigger_px: float, kind: str, cloid: str) -> Result:
        from hyperliquid.utils.types import Cloid

        order_type = {"trigger": {"triggerPx": trigger_px, "isMarket": True, "tpsl": "sl" if kind == "sl" else "tp"}}
        return parse_status(self.ex.order(coin, is_buy, size, trigger_px, order_type, reduce_only=True, cloid=Cloid.from_str(cloid)))

    def cancel(self, coin: str, oid: int | None) -> None:
        """Raises ExchangeError when Hyperliquid refuses: a protective order that is still resting must never be booked as
        cancelled. "Already cancelled or filled" counts as gone."""
        if oid is not None:
            check_cancel(self.ex.cancel(coin, oid))

    def cancel_by_cloid(self, coin: str, cloid: str) -> None:
        from hyperliquid.utils.types import Cloid

        check_cancel(self.ex.cancel_by_cloid(coin, Cloid.from_str(cloid)))

    def fills_since(self, start_ms: int) -> list[dict[str, Any]]:
        return self.info.user_fills_by_time(self.master, start_ms)

    def mid_px(self, coin: str) -> float:
        return float(self.info.all_mids()[coin])


def account_snapshot(mode: str, master: str, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Read-only: the account's margin summary, positions, open orders and (for unified accounts) its USDC, from Hyperliquid."""
    with httpx.Client(timeout=10, transport=transport) as c:
        st = c.post(f"{URLS[mode]}/info", json={"type": "clearinghouseState", "user": master}).json()
        oo = c.post(f"{URLS[mode]}/info", json={"type": "frontendOpenOrders", "user": master}).json()
    if not isinstance(st, dict) or not isinstance(oo, list):
        raise ExchangeError("Unexpected account answer from Hyperliquid.")
    uni = is_unified(mode, master, transport)
    total, hold = spot_usdc(mode, master, transport) if uni else (0.0, 0.0)
    return {"state": st, "orders": oo, "unified": uni, "spot_usdc": total, "spot_hold": hold}


# ---------------------------------------------------------------- unified accounts (Hyperliquid "unifiedAccount" / portfolio margin)
# In these modes Spot USDC IS the trading collateral: the Perps account value can read $0 while the USDC is fully usable.
_abstraction: dict[tuple[str, str], tuple[float, bool]] = {}


def is_unified(mode: str, address: str, transport: httpx.BaseTransport | None = None) -> bool:
    k = (mode, address.lower())
    hit = _abstraction.get(k)
    if hit and time.monotonic() - hit[0] < 600:
        return hit[1]
    try:
        with httpx.Client(timeout=10, transport=transport) as c:
            out = c.post(f"{URLS[mode]}/info", json={"type": "userAbstraction", "user": address}).json()
        uni = out in ("unifiedAccount", "portfolioMargin")
    except Exception:  # noqa: BLE001 - unknown: treat as a classic account (Perps value only; never overstates the balance)
        return False
    _abstraction[k] = (time.monotonic(), uni)
    return uni


def spot_usdc(mode: str, address: str, transport: httpx.BaseTransport | None = None) -> tuple[float, float]:
    """(total, on hold) USDC in the account's Spot balance."""
    with httpx.Client(timeout=10, transport=transport) as c:
        sp = c.post(f"{URLS[mode]}/info", json={"type": "spotClearinghouseState", "user": address}).json()
    for b in (sp or {}).get("balances", []):
        if b.get("coin") == "USDC":
            return float(b.get("total") or 0), float(b.get("hold") or 0)
    return 0.0, 0.0


def trading_equity(mode: str, address: str, perp_state: dict[str, Any]) -> float:
    """What trades can use: the Perps account value, or for a unified account its USDC (the larger: never counted twice)."""
    pv = float((perp_state.get("marginSummary") or {}).get("accountValue") or 0)
    if not is_unified(mode, address):
        return pv
    return max(pv, spot_usdc(mode, address)[0])


# ---------------------------------------------------------------- API-wallet authorization (website flow)
def new_agent() -> tuple[str, str]:
    import eth_account

    key = "0x" + secrets.token_hex(32)
    return key, eth_account.Account.from_key(key).address


def approve_typed_data(mode: str, agent: str, nonce: int, chain_id_hex: str) -> dict[str, Any]:
    """The exact EIP-712 message Hyperliquid expects for ApproveAgent (the member's wallet signs it in the browser)."""
    from hyperliquid.utils.signing import user_signed_payload

    action = {"type": "approveAgent", "signatureChainId": chain_id_hex, "hyperliquidChain": "Mainnet" if mode == "LIVE" else "Testnet",
              "agentAddress": agent, "agentName": AGENT_NAME, "nonce": nonce}
    return user_signed_payload("HyperliquidTransaction:ApproveAgent", APPROVE_TYPES, action)


def signer_of(typed: dict[str, Any], signature: str) -> str:
    from eth_account import Account
    from eth_account.messages import encode_typed_data

    return Account.recover_message(encode_typed_data(full_message=typed), signature=signature).lower()


def submit_approval(mode: str, typed: dict[str, Any], signature: str, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    sig = signature.removeprefix("0x")
    r, s, v = "0x" + sig[:64], "0x" + sig[64:128], int(sig[128:130], 16)
    msg = typed["message"]
    action = {"type": "approveAgent", "signatureChainId": msg["signatureChainId"], "hyperliquidChain": msg["hyperliquidChain"],
              "agentAddress": msg["agentAddress"], "agentName": msg["agentName"], "nonce": msg["nonce"]}
    with httpx.Client(timeout=20, transport=transport) as c:
        resp = c.post(f"{URLS[mode]}/exchange", json={"action": action, "nonce": msg["nonce"], "signature": {"r": r, "s": s, "v": v},
                                                     "vaultAddress": None})
    try:
        return resp.json()
    except ValueError as e:
        raise ExchangeError(f"Hyperliquid answered {resp.status_code}") from e


def agent_record(mode: str, master: str, agent: str, transport: httpx.BaseTransport | None = None) -> dict[str, Any] | None:
    """Hyperliquid's own record of this API wallet for this master account ({name, address, validUntil}), or None when it isn't
    approved (never approved, revoked, replaced, or expired). Read from the public info endpoint: no key involved."""
    with httpx.Client(timeout=20, transport=transport) as c:
        out = c.post(f"{URLS[mode]}/info", json={"type": "extraAgents", "user": master}).json()
    now = time.time() * 1000
    for a in out if isinstance(out, list) else []:
        if str(a.get("address", "")).lower() == agent.lower() and (not a.get("validUntil") or float(a["validUntil"]) > now):
            return a
    return None


def balances(mode: str, address: str, transport: httpx.BaseTransport | None = None) -> dict[str, float]:
    """The account's money as Hyperliquid reports it (public info): the Perps account value (what trades use) and Spot USDC."""
    with httpx.Client(timeout=15, transport=transport) as c:
        perp = c.post(f"{URLS[mode]}/info", json={"type": "clearinghouseState", "user": address}).json()
        spot = c.post(f"{URLS[mode]}/info", json={"type": "spotClearinghouseState", "user": address}).json()
    usdc = next((float(b.get("total") or 0) for b in (spot or {}).get("balances", []) if b.get("coin") == "USDC"), 0.0)
    pv = float(((perp or {}).get("marginSummary") or {}).get("accountValue") or 0)
    uni = is_unified(mode, address, transport)
    return {"perps": round(pv, 2), "spot_usdc": round(usdc, 2), "unified": uni, "trading": round(max(pv, usdc) if uni else pv, 2)}


def user_role(mode: str, address: str, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Hyperliquid's public answer for what an address is: {"role": "agent", "data": {"user": master}} for an API wallet (and
    the account it trades for), "user" for an account, "missing" when Hyperliquid has never seen it."""
    with httpx.Client(timeout=20, transport=transport) as c:
        out = c.post(f"{URLS[mode]}/info", json={"type": "userRole", "user": address}).json()
    return out if isinstance(out, dict) else {}


def agent_listed(mode: str, master: str, agent: str, transport: httpx.BaseTransport | None = None) -> bool:
    return agent_record(mode, master, agent, transport) is not None
