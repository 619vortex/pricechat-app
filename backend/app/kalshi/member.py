"""Earn › Kalshi: a member links their own Kalshi API key and sees their account live — balance, open positions marked at
the current bid after Kalshi's fee, resting orders, recent fills and settlements.

READ-ONLY. This module only sends GET requests; there is no order, cancel or transfer call anywhere in it. The private key
is sealed at rest (tradingos.exchange.enc → app.security.secrets) and only ever used to sign those GETs.

Signing (docs.kalshi.com, API keys): KALSHI-ACCESS-KEY / -TIMESTAMP (ms) / -SIGNATURE = base64(sign(ts + METHOD + path)),
path including /trade-api/v2 without the query. RSA keys → RSA-PSS SHA-256 (salt = digest length); Ed25519 → Ed25519.
The algorithm comes from the parsed key, never the PEM header.

Fees: Kalshi's model fee = coefficient × fee_multiplier × C × P × (1 − P), from the series' own fee_type/fee_multiplier
(GET /series/{ticker}); a series whose fee_type isn't a known quadratic model shows "fee unknown" instead of a guess.
KALSHI_FEE_COEFFICIENT (env, default 0.07: Kalshi's published taker coefficient) is the one configured number.

Geography: Kalshi decides who may hold an account (KYC). A jurisdiction answer from Kalshi stops the link (no retry, no
workaround) and the member sees Kalshi's own message.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import time
from decimal import ROUND_CEILING, Decimal
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.singleton import get_or_create
from app.db.base import utcnow
from app.models.kalshi import KalshiBot, KalshiLink
from app.models.members import Member

BASE_URLS = {"production": "https://external-api.kalshi.com", "demo": "https://external-api.demo.kalshi.co"}
API_PREFIX = "/trade-api/v2"
KEY_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
MAX_PEM = 8192
CACHE_S = 8.0  # one Kalshi read per member per 8 s however many tabs poll
SERIES_TTL_S = 3600.0
MAX_POSITIONS = 40  # positions priced live per refresh
SETTLED_PAGES = 10  # x 200: lifetime totals page through every settlement up to 2,000 (Kalshi has no historical settlements endpoint)
LIFETIME_TTL_S = 60.0
RECENT_SETTLED = 10
# TODO(verify): Kalshi documents no error code for geographic restrictions; HTTP 451 or these words in a 400/403 halt.
JURISDICTION_MARKERS = ("jurisdiction", "geo", "region", "country", "restricted location", "not available in your", "prohibited")
QUADRATIC = {"quadratic", "quadratic_with_maker_fees", "quadratic_with_combo_maker_fees"}

# Swappable in tests (no network).
transport: httpx.AsyncBaseTransport | None = None


class KalshiLinkError(Exception):
    def __init__(self, status: int, msg: str) -> None:
        super().__init__(msg)
        self.status = status


def coefficient() -> Decimal:
    return Decimal(os.environ.get("KALSHI_FEE_COEFFICIENT") or "0.07")


# ---------------------------------------------------------------- keys + signing
def load_key(pem: str) -> tuple[Any, str]:
    """The private key from the PEM Kalshi gave the member (→ key, "ed25519" | "rsa"); a clear message for anything else."""
    text = (pem or "").strip()
    if not text:
        raise KalshiLinkError(400, "Paste the private key Kalshi gave you when you made the API key.")
    if len(text) > MAX_PEM:
        raise KalshiLinkError(400, "That's too long to be a Kalshi private key.")
    if "PUBLIC KEY" in text:
        raise KalshiLinkError(400, "That's a public key. Paste the PRIVATE key (it starts with -----BEGIN … PRIVATE KEY-----).")
    try:
        key = serialization.load_pem_private_key(text.encode(), password=None)
    except (ValueError, TypeError):
        raise KalshiLinkError(400, "That isn't a readable private key. Paste the whole file, including the BEGIN and END lines.") from None
    if isinstance(key, ed25519.Ed25519PrivateKey):
        return key, "ed25519"
    if isinstance(key, rsa.RSAPrivateKey):
        return key, "rsa"
    raise KalshiLinkError(400, "Kalshi keys are RSA or Ed25519; this key is neither.")


def sign(key: Any, ts_ms: str, method: str, path: str) -> str:
    msg = (ts_ms + method.upper() + urlsplit(path).path).encode()
    if isinstance(key, rsa.RSAPrivateKey):
        sig = key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
    else:
        sig = key.sign(msg)
    return base64.b64encode(sig).decode()


def _error_text(r: httpx.Response) -> str:
    try:
        e = r.json().get("error") or {}
        return " ".join(str(e.get(k) or "") for k in ("code", "message", "details")).strip() or r.text[:300]
    except Exception:  # noqa: BLE001
        return r.text[:300]


class Reader:
    """Signed GETs for one key. No other HTTP method exists here."""

    def __init__(self, env: str, key_id: str, key: Any) -> None:
        self.key_id, self.key = key_id, key
        self.http = httpx.AsyncClient(base_url=BASE_URLS[env], timeout=12.0, transport=transport)

    async def __aenter__(self) -> "Reader":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.http.aclose()

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        full = API_PREFIX + path
        for attempt in range(3):
            ts = str(int(time.time() * 1000))
            headers = {"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-TIMESTAMP": ts, "KALSHI-ACCESS-SIGNATURE": sign(self.key, ts, "GET", full)}
            try:
                r = await self.http.get(full, params={k: v for k, v in params.items() if v is not None} or None, headers=headers)
            except httpx.TransportError:
                if attempt == 2:
                    raise KalshiLinkError(502, "Couldn't reach Kalshi. Try again in a minute.") from None
                await asyncio.sleep(0.4 * (attempt + 1))
                continue
            text = _error_text(r) if r.status_code >= 400 else ""
            if r.status_code == 451 or (r.status_code in (400, 403) and any(m in text.lower() for m in JURISDICTION_MARKERS)):
                raise KalshiLinkError(451, f"Kalshi says this account can't be used from here: {text}")
            if r.status_code in (401, 403):
                raise KalshiLinkError(401, f"Kalshi refused the key ({text or r.status_code}). Check the key ID, that the key wasn't "
                                           "deleted, and that it's a real-money key (demo keys only work with Demo).")
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == 2:
                    raise KalshiLinkError(503, "Kalshi is busy right now. Try again in a minute.")
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise KalshiLinkError(502, f"Kalshi answered {r.status_code}: {text}")
            try:
                data = r.json()
            except ValueError:
                raise KalshiLinkError(502, "Kalshi sent something that isn't JSON.") from None
            if not isinstance(data, dict):
                raise KalshiLinkError(502, f"Kalshi's answer to {path} has an unexpected shape.")
            return data
        raise KalshiLinkError(503, "Kalshi is busy right now.")


# ---------------------------------------------------------------- the link
def is_open(db: Session) -> bool:
    row = db.get(KalshiBot, 1)
    return bool(row and row.members_open)


def set_open(db: Session, open_: bool) -> bool:
    row = get_or_create(db, KalshiBot, lambda: KalshiBot(id=1))
    row.members_open = open_
    db.commit()
    return open_


def link_of(db: Session, member: Member) -> KalshiLink | None:
    return db.scalar(select(KalshiLink).where(KalshiLink.member_id == member.id))


def _link_view(link: KalshiLink | None) -> dict[str, Any] | None:
    if link is None:
        return None
    return {"env": link.env, "key_id": f"{link.key_id[:8]}…{link.key_id[-4:]}", "key_type": link.key_type,
            "connected_at": link.connected_at.isoformat(), "last_ok_at": link.last_ok_at.isoformat() if link.last_ok_at else None,
            "last_error": link.last_error}


def state(db: Session, member: Member | None) -> dict[str, Any]:
    return {"signed_in": member is not None, "link": _link_view(link_of(db, member)) if member else None}


async def connect(db: Session, member: Member, env: str, key_id: str, pem: str) -> dict[str, Any]:
    """Checks the key against Kalshi (GET /portfolio/balance) before keeping anything; replaces an earlier link."""
    from app.tradingos.exchange import enc

    if env not in BASE_URLS:
        raise KalshiLinkError(400, "Pick Real money or Demo.")
    key_id = (key_id or "").strip()
    if not KEY_ID.match(key_id):
        raise KalshiLinkError(400, "The Key ID looks like 0a472aed-cf27-…: 36 characters with dashes, shown next to the key on Kalshi.")
    key, kind = load_key(pem)
    async with Reader(env, key_id, key) as k:
        bal = await k.get("/portfolio/balance")
    if "balance" not in bal:
        raise KalshiLinkError(502, "Kalshi's balance answer has an unexpected shape.")
    pem_clean = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    link = link_of(db, member) or KalshiLink(member_id=member.id)
    now = utcnow()
    link.env, link.key_id, link.key_enc, link.key_type = env, key_id, enc(pem_clean), kind
    link.connected_at, link.last_ok_at, link.last_error = now, now, None
    db.add(link)
    db.commit()
    _cache.pop(member.id, None)
    return {"link": _link_view(link), "balance_usd": int(bal["balance"]) / 100}


def disconnect(db: Session, member: Member) -> None:
    link = link_of(db, member)
    if link is not None:
        db.delete(link)
        db.commit()
    _cache.pop(member.id, None)


# ---------------------------------------------------------------- the account, live
_cache: dict[int, tuple[float, dict[str, Any]]] = {}
_locks: dict[int, asyncio.Lock] = {}
_series: dict[tuple[str, str], tuple[float, dict[str, Any] | None]] = {}


def _d(x: Any) -> Decimal:
    try:
        return Decimal(str(x)) if x not in (None, "") else Decimal(0)
    except Exception:  # noqa: BLE001
        return Decimal(0)


def exit_fee(fee_type: str | None, multiplier: Any, price: Decimal, count: Decimal) -> Decimal | None:
    """Taker fee to sell `count` at `price` now, rounded up to $0.0001 (Kalshi rounds the balance change against the member);
    None when the series' fee model isn't one of Kalshi's quadratic models."""
    if fee_type not in QUADRATIC:
        return None
    if count <= 0 or not (0 < price < 1):
        return Decimal(0)
    raw = coefficient() * _d(multiplier or 1) * count * price * (1 - price)
    return (raw / Decimal("0.0001")).to_integral_value(ROUND_CEILING) * Decimal("0.0001")


async def _series_fee(k: Reader, env: str, event_ticker: str) -> dict[str, Any] | None:
    key = (env, event_ticker)
    hit = _series.get(key)
    if hit and time.monotonic() - hit[0] < SERIES_TTL_S:
        return hit[1]
    out: dict[str, Any] | None = None
    try:
        ev = (await k.get(f"/events/{event_ticker}")).get("event") or {}
        st = ev.get("series_ticker")
        if st:
            se = (await k.get(f"/series/{st}")).get("series") or {}
            out = {"series": st, "fee_type": se.get("fee_type"), "fee_multiplier": se.get("fee_multiplier"), "category": ev.get("category")}
    except KalshiLinkError as e:
        if e.status in (401, 451):
            raise
    _series[key] = (time.monotonic(), out)
    return out


async def _position(k: Reader, env: str, p: dict[str, Any]) -> dict[str, Any]:
    ticker = str(p.get("ticker") or "")
    pos = _d(p.get("position_fp"))
    side, count = ("yes" if pos > 0 else "no"), abs(pos)
    row: dict[str, Any] = {"ticker": ticker, "side": side, "count": float(count), "cost_usd": float(_d(p.get("market_exposure_dollars"))),
                           "realized_pnl_usd": float(_d(p.get("realized_pnl_dollars"))), "fees_paid_usd": float(_d(p.get("fees_paid_dollars"))),
                           "resting_orders": p.get("resting_orders_count")}
    try:
        m = (await k.get(f"/markets/{ticker}")).get("market") or {}
    except KalshiLinkError as e:
        if e.status in (401, 451):
            raise
        return {**row, "error": str(e)}
    yes_bid, yes_ask = _d(m.get("yes_bid_dollars")), _d(m.get("yes_ask_dollars"))
    no_bid = _d(m.get("no_bid_dollars")) if m.get("no_bid_dollars") not in (None, "") else (1 - yes_ask if yes_ask > 0 else Decimal(0))
    bid = yes_bid if side == "yes" else no_bid
    fee_info = await _series_fee(k, env, str(m.get("event_ticker") or "")) if m.get("event_ticker") else None
    fee = exit_fee((fee_info or {}).get("fee_type"), (fee_info or {}).get("fee_multiplier"), bid, count)
    gross = bid * count
    row.update({
        "title": m.get("title") or ticker, "subtitle": m.get("yes_sub_title") or m.get("subtitle") or "", "status": m.get("status"),
        "close_time": m.get("close_time"), "event_ticker": m.get("event_ticker"), "category": (fee_info or {}).get("category"),
        "yes_bid": float(yes_bid), "yes_ask": float(yes_ask), "bid": float(bid),
        "value_now_usd": float(gross), "exit_fee_usd": None if fee is None else float(fee),
        "net_if_sold_usd": None if fee is None else float(gross - fee),
        "pnl_if_sold_usd": None if fee is None else float(gross - fee - _d(p.get("market_exposure_dollars"))),
        "pays_if_right_usd": float(count), "fee_model": (fee_info or {}).get("fee_type") or "unknown",
    })
    return row


def _settled(s: dict[str, Any]) -> dict[str, Any]:
    """One settlement row. Kalshi books closing one side as buying the other, so each matched YES/NO pair paid $1 when it
    netted and isn't in `revenue`: payout = revenue + min(YES, NO) x $1; realized P&L = payout - cost - fees."""
    yes, no = _d(s.get("yes_count_fp")), _d(s.get("no_count_fp"))
    cost, fee = _d(s.get("yes_total_cost_dollars")) + _d(s.get("no_total_cost_dollars")), _d(s.get("fee_cost"))
    revenue = Decimal(int(s.get("revenue") or 0)) / 100
    payout = revenue + min(yes, no)
    return {"ticker": s.get("ticker"), "result": s.get("market_result"), "settled_time": s.get("settled_time"),
            "yes_count": float(yes), "no_count": float(no), "cost_usd": float(cost), "fee_usd": float(fee), "revenue_usd": float(revenue),
            "payout_usd": float(payout), "pnl_usd": float(round(payout - cost - fee, 2))}


_lifetime: dict[int, tuple[float, list[dict[str, Any]], bool]] = {}


async def _all_settled(k: Reader, link: KalshiLink) -> tuple[list[dict[str, Any]], bool]:
    """Every settlement on the account, newest first (→ rows, complete). Cached a minute per link: it pages."""
    hit = _lifetime.get(link.id)
    if hit and time.monotonic() - hit[0] < LIFETIME_TTL_S:
        return hit[1], hit[2]
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(SETTLED_PAGES):
        d = await k.get("/portfolio/settlements", limit=200, cursor=cursor)
        rows += [_settled(s) for s in (d.get("settlements") or [])]
        cursor = d.get("cursor") or None
        if not cursor:
            break
    rows.sort(key=lambda r: r.get("settled_time") or "", reverse=True)
    _lifetime[link.id] = (time.monotonic(), rows, cursor is None)
    return rows, cursor is None


def lifetime(rows: list[dict[str, Any]], complete: bool) -> dict[str, Any]:
    wins = sum(1 for r in rows if r["pnl_usd"] > 0)
    losses = sum(1 for r in rows if r["pnl_usd"] < 0)
    return {"realized_pnl_usd": round(sum(r["pnl_usd"] for r in rows), 2), "settled": len(rows), "wins": wins, "losses": losses,
            "win_rate": wins / len(rows) if rows else None, "complete": complete}


async def _read(link: KalshiLink) -> dict[str, Any]:
    from app.tradingos.exchange import dec

    key, _ = load_key(dec(link.key_enc))
    async with Reader(link.env, link.key_id, key) as k:
        bal, pos, orders, fills, (settled, complete) = await asyncio.gather(
            k.get("/portfolio/balance"), k.get("/portfolio/positions", limit=200), k.get("/portfolio/orders", status="resting", limit=100),
            k.get("/portfolio/fills", limit=20), _all_settled(k, link))
        open_rows = [p for p in (pos.get("market_positions") or []) if _d(p.get("position_fp")) != 0]
        positions = await asyncio.gather(*(_position(k, link.env, p) for p in open_rows[:MAX_POSITIONS]))
    known = [p for p in positions if p.get("net_if_sold_usd") is not None]
    return {
        "env": link.env, "as_of": utcnow().isoformat(),
        "balance_usd": int(bal.get("balance") or 0) / 100, "portfolio_value_usd": int(bal.get("portfolio_value") or 0) / 100,
        "positions": sorted(positions, key=lambda p: -(p.get("value_now_usd") or 0)), "positions_total": len(open_rows),
        "totals": {"cost_usd": round(sum(p["cost_usd"] for p in positions), 2),
                   "net_if_sold_usd": round(sum(p["net_if_sold_usd"] for p in known), 2),
                   "pnl_if_sold_usd": round(sum(p["pnl_if_sold_usd"] for p in known), 2),
                   "unpriced": len(positions) - len(known)},
        "orders": [{"ticker": o.get("ticker"), "side": o.get("side"), "action": o.get("action"), "type": o.get("type"),
                    "price": float(_d(o.get("yes_price_dollars") if o.get("side") == "yes" else o.get("no_price_dollars"))),
                    "remaining": float(_d(o.get("remaining_count_fp") or o.get("remaining_count"))), "created_time": o.get("created_time")}
                   for o in (orders.get("orders") or [])],
        "fills": [{"ticker": f.get("ticker"), "side": f.get("side"), "action": f.get("action"),
                   "count": float(_d(f.get("count_fp") or f.get("count"))),
                   "price": float(_d(f.get("yes_price_dollars") if f.get("side") == "yes" else f.get("no_price_dollars"))),
                   "is_taker": f.get("is_taker"), "created_time": f.get("created_time")} for f in (fills.get("fills") or [])],
        "settlements": settled[:RECENT_SETTLED], "lifetime": lifetime(settled, complete),
        "fee_coefficient": float(coefficient()),
    }


async def account(db: Session, member: Member, *, fresh: bool = False) -> dict[str, Any]:
    link = link_of(db, member)
    if link is None:
        raise KalshiLinkError(404, "Connect your Kalshi API key first.")
    hit = _cache.get(member.id)
    if hit and not fresh and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    lock = _locks.setdefault(member.id, asyncio.Lock())
    async with lock:
        hit = _cache.get(member.id)
        if hit and not fresh and time.monotonic() - hit[0] < CACHE_S:
            return hit[1]
        try:
            out = await _read(link)
        except KalshiLinkError as e:
            link.last_error = str(e)[:500]
            db.commit()
            raise
        link.last_ok_at, link.last_error = utcnow(), None
        db.commit()
        _cache[member.id] = (time.monotonic(), out)
        return out


def members_overview(db: Session) -> dict[str, Any]:
    links = db.scalars(select(KalshiLink)).all()
    return {"open": is_open(db), "linked": len(links), "production": sum(1 for x in links if x.env == "production"),
            "erroring": sum(1 for x in links if x.last_error)}

