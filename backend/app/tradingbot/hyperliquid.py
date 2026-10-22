"""Hyperliquid's public, read-only info API (POST https://api.hyperliquid.xyz/info). No key, no signing, no trading.

What it can and can't prove (checked against the live API):
- Referral: `{"type": "referral", "user": addr}` → `referredBy` is `{"referrer": addr, "code": "BANTER"}` once a referral
  code is attached to the account, or null. That is authoritative: it's Hyperliquid's own record, not a link click.
- Deposits: `{"type": "userNonFundingLedgerUpdates", "user": addr, "startTime": ms}` lists every non-trading balance
  change. Entries of type "deposit" are USDC bridged in from Arbitrum by this account: those count, summed over the
  whole history (a balance never does). Anything else coming in (spotTransfer / internalTransfer / send from another
  account, sub-account and vault moves, assets bridged by third-party bridges) can't be told apart from a gift or a
  transfer between someone's own wallets, so it's reported separately and never counted automatically: if the bridge
  deposits alone don't reach the minimum but other inflows exist, the deposit is UNVERIFIED and a person reviews it.
- Spot/perp moves within the same account (accountClassTransfer) are internal and ignored.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API = "https://api.hyperliquid.xyz/info"
PAGE_MAX = 2000  # the API returns at most this many ledger entries per call
MAX_PAGES = 25
FILL_PAGES = 5  # userFillsByTime only serves the 10,000 most recent fills anyway


class HyperliquidError(Exception):
    """The API couldn't be reached or answered something unexpected: the result is UNVERIFIED, never a guess."""


@dataclass
class Check:
    referral_status: str  # verified | not_referred | unverified
    referral_code: str | None
    deposit_status: str  # verified | insufficient | unverified
    deposit_usd: float
    other_inflow_usd: float
    evidence: dict[str, Any] = field(default_factory=dict)


class InfoClient:
    def __init__(self, url: str = API, transport: httpx.BaseTransport | None = None, timeout: float = 15.0) -> None:
        self.url = url
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def _post(self, body: dict[str, Any]) -> Any:
        try:
            r = self._http.post(self.url, json=body)
        except httpx.HTTPError as e:
            raise HyperliquidError(f"Hyperliquid couldn't be reached ({type(e).__name__}).") from e
        if r.status_code != 200:
            raise HyperliquidError(f"Hyperliquid answered {r.status_code}.")
        try:
            return r.json()
        except ValueError as e:
            raise HyperliquidError("Hyperliquid sent something that isn't JSON.") from e

    def referral(self, address: str) -> dict[str, Any]:
        out = self._post({"type": "referral", "user": address})
        if not isinstance(out, dict) or "referredBy" not in out:
            raise HyperliquidError("Hyperliquid's referral answer had no referredBy field.")
        return out

    def portfolio(self, address: str) -> dict[str, dict[str, Any]]:
        """Account value and PnL history per window (day, week, month, allTime, and perp-only), as Hyperliquid computes it."""
        out = self._post({"type": "portfolio", "user": address})
        if not isinstance(out, list):
            raise HyperliquidError("Hyperliquid's portfolio answer wasn't a list.")
        return {str(k): v for k, v in out if isinstance(v, dict)}

    def agents(self, address: str) -> list[dict[str, Any]]:
        """API wallets ("agents") this account has approved: [{name, address, validUntil}]. A trading bot connected with
        /connect shows up here (Price Chat only ever trades with them)."""
        out = self._post({"type": "extraAgents", "user": address})
        if not isinstance(out, list):
            raise HyperliquidError("Hyperliquid's agents answer wasn't a list.")
        return [a for a in out if isinstance(a, dict)]

    def ledger(self, address: str) -> list[dict[str, Any]]:
        """Every non-funding ledger update since the account began, oldest first (paged by time)."""
        out: list[dict[str, Any]] = []
        start = 0
        for _ in range(MAX_PAGES):
            page = self._post({"type": "userNonFundingLedgerUpdates", "user": address, "startTime": start})
            if not isinstance(page, list):
                raise HyperliquidError("Hyperliquid's ledger answer wasn't a list.")
            out += page
            if len(page) < PAGE_MAX:
                return out
            start = max(int(e.get("time") or 0) for e in page) + 1
        raise HyperliquidError("The ledger is longer than this check reads; a person needs to review it.")

    def fills(self, address: str, start_ms: int) -> tuple[list[dict[str, Any]], bool]:
        """The account's fills since start_ms, oldest first (paged by time), and whether reading stopped at FILL_PAGES."""
        out: list[dict[str, Any]] = []
        start = start_ms
        for _ in range(FILL_PAGES):
            page = self._post({"type": "userFillsByTime", "user": address, "startTime": start, "aggregateByTime": True})
            if not isinstance(page, list):
                raise HyperliquidError("Hyperliquid's fills answer wasn't a list.")
            out += [f for f in page if isinstance(f, dict)]
            if len(page) < PAGE_MAX:
                return out, False
            start = max(int(f.get("time") or 0) for f in page) + 1
        return out, True


def trade_count(fills: list[dict[str, Any]]) -> int:
    """Trades = distinct orders that filled (one order filled in several pieces is one trade)."""
    return len({f.get("oid") if f.get("oid") is not None else f.get("tid") for f in fills})


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def classify(address: str, entries: list[dict[str, Any]]) -> tuple[float, float, list[dict[str, Any]]]:
    """(NET bridge deposits in USD = deposits − withdrawals, never below 0; other inflows in USD; the entries behind them).
    Net, so depositing, withdrawing and depositing the same money again doesn't count twice."""
    me = address.lower()
    deposits = withdrawn = other = 0.0
    used = []
    for e in entries:
        d = e.get("delta") or {}
        kind = d.get("type")
        if kind == "deposit":
            amt = _num(d.get("usdc"))
            deposits += amt
            used.append({"time": e.get("time"), "hash": e.get("hash"), "type": kind, "usd": amt, "counted": True})
        elif kind == "withdraw":
            amt = _num(d.get("usdc"))
            withdrawn += amt
            used.append({"time": e.get("time"), "hash": e.get("hash"), "type": kind, "usd": -amt, "counted": True})
        elif kind in ("internalTransfer", "spotTransfer", "send", "subAccountTransfer", "vaultWithdraw", "cStakingTransfer") \
                and str(d.get("destination") or d.get("user") or "").lower() == me and str(d.get("user") or "").lower() != me:
            amt = _num(d.get("usdcValue") if d.get("usdcValue") is not None else d.get("usdc"))
            other += amt
            used.append({"time": e.get("time"), "hash": e.get("hash"), "type": kind, "usd": amt, "counted": False})
    return round(max(0.0, deposits - withdrawn), 2), round(other, 2), used[-50:]


def check(client: InfoClient, address: str, code: str | list[str], min_deposit: float) -> Check:
    """Read the account and decide each requirement as verified / not met / UNVERIFIED. Never guesses. `code` may be several
    accepted codes (a referrer's link name and the code Hyperliquid records can differ)."""
    codes = {c.strip().upper() for c in ([code] if isinstance(code, str) else code) if c.strip()}
    ev: dict[str, Any] = {"address": address, "source": "api.hyperliquid.xyz/info (read-only)"}
    try:
        ref = client.referral(address)
        by = ref.get("referredBy")
        seen = (by or {}).get("code") if isinstance(by, dict) else None
        ev["referral"] = {"referredBy": by}
        r_status = "verified" if seen and str(seen).upper() in codes else "not_referred"
    except HyperliquidError as e:
        seen, r_status = None, "unverified"
        ev["referral_error"] = str(e)
    try:
        dep, other, used = classify(address, client.ledger(address))
        ev["deposits"] = used
        try:  # the account's current value, shown next to net deposits (it never decides anything: trading moves it)
            all_time = client.portfolio(address).get("allTime") or {}
            hist = all_time.get("accountValueHistory") or []
            ev["account_value"] = round(_num(hist[-1][1]), 2) if hist else 0.0
        except (HyperliquidError, AttributeError, IndexError, TypeError):
            pass
        if dep >= min_deposit:
            d_status = "verified"
        elif other > 0 and dep + other >= min_deposit:
            d_status = "unverified"  # money came in some other way: only a person can tell whose it was
        else:
            d_status = "insufficient"
    except HyperliquidError as e:
        dep = other = 0.0
        d_status = "unverified"
        ev["deposit_error"] = str(e)
    return Check(r_status, seen, d_status, dep, other, ev)


def pnl_summary(portfolio: dict[str, dict[str, Any]], points: int = 40) -> dict[str, Any]:
    """PnL and account value for 24h / 7d / 30d / all time, with a small series for a sparkline (Hyperliquid's own numbers)."""
    out: dict[str, Any] = {}
    for key, label in (("day", "24h"), ("week", "7d"), ("month", "30d"), ("allTime", "all")):
        w = portfolio.get(key) or {}
        pnl = [(int(t), _num(v)) for t, v in (w.get("pnlHistory") or [])]
        val = [(int(t), _num(v)) for t, v in (w.get("accountValueHistory") or [])]
        if not pnl:
            continue
        step = max(1, len(pnl) // points)
        # each window's pnlHistory runs from 0 at the window's start: the last point is that window's PnL
        out[label] = {"pnl": round(pnl[-1][1], 2), "value": round(val[-1][1], 2) if val else None,
                      "series": [round(v, 2) for _, v in pnl[::step]][-points:] + ([round(pnl[-1][1], 2)] if (len(pnl) - 1) % step else [])}
    return out
