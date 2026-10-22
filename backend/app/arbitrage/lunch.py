"""Lunch Bot: a PAPER pump.fun launch bot. It never holds a key, never signs, never sends a transaction.

What it does, every minute (job tradingos.lunch_bot):
1. Reads pump.fun's newest launches (its public site API, one request a minute: it rate-limits hard) and watches the
   SOL-quoted ones for their first `WATCH_MINUTES`.
2. Reads every watched token's bonding-curve account straight from Solana (getMultipleAccounts, up to 100 per call): the
   real reserves, so prices come from the chain, not from a feed.
3. Momentum entry: when buyers have already put `MIN_REAL_SOL` into the curve and its value is up `MIN_GROWTH` since the
   scanner first saw it, it "buys" `SIZE_SOL` at the curve's real price (exact constant-product maths with fees).
4. Exits at graduation (the curve completes and the token moves to its PumpSwap pool: sold at the pool's price), at the
   target, at the stop, or after `TIMEOUT_MINUTES`, each priced from the chain with fees.
5. Measures the "migration arbitrage" itself: for launches that graduated, the curve's last price vs the first price their
   PumpSwap pool traded at (GeckoTerminal one-minute candles). Strategy write-ups call that gap a guaranteed profit; these
   rows show what it really is.

Costs are assumptions, shown on the page: `CURVE_FEE` per curve trade, `POOL_FEE` per pool trade, `TX_COST_SOL` per
transaction (network + priority fee). A real bot would also face failed transactions and other bots buying first; paper
entries are at the price the scanner saw, up to a minute after the move."""

import asyncio
import base64
import logging
import struct
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

import httpx
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.arbitrage import ArbLunchDay, ArbLunchGraduation, ArbLunchTrade

logger = logging.getLogger(__name__)

PUMP_API = "https://frontend-api-v3.pump.fun"
GECKO = "https://api.geckoterminal.com/api/v2"
SOL_QUOTE = "11111111111111111111111111111111"  # pump.fun's quote_mint for SOL-quoted curves
UA = {"User-Agent": "PriceChat-LunchBot/1.0 (+https://pricechat.live/earn/arbitrage)", "Accept": "application/json"}
LAMPORTS, TOKEN_UNITS, SUPPLY = 1e9, 1e6, 1e9  # pump.fun tokens: 6 decimals, 1 billion supply

# the menu: two strategies, run side by side on paper so the data says which one works
SIZE_SOL = 0.1
# 🥗 Starter: early momentum on brand-new launches
WATCH_MINUTES = 10
MIN_REAL_SOL = 2.0
MIN_GROWTH = 0.30
TARGET = 1.00  # +100%
STOP = -0.40
TIMEOUT_MINUTES = 30
MAX_OPEN = 20
# 🍰 Graduation Run: a curve that's nearly full and still filling, held to graduation (the "buy before migration" play)
GRAD_SCAN_EVERY = 2  # runs between scans of pump.fun's fullest curves (it rate-limits; never on a graduation-check run)
GRAD_MIN_PROGRESS = 0.80  # at least 80% of the curve's tokens sold
GRAD_MAX_PROGRESS = 0.97  # above this, too close to the line for an entry to be realistic
GRAD_MIN_RISE = 0.01  # still filling: up at least 1 point of progress since the previous scan
GRAD_STOP = -0.25
GRAD_TIMEOUT_MINUTES = 90
GRAD_MAX_OPEN = 10
INITIAL_REAL_TOKENS = 793_100_000 * 10**6  # tokens a standard curve sells before it completes (raw units)
STRATEGIES = {"starter": {"target": TARGET, "stop": STOP, "timeout": TIMEOUT_MINUTES, "max_open": MAX_OPEN},
              "graduation": {"target": None, "stop": GRAD_STOP, "timeout": GRAD_TIMEOUT_MINUTES, "max_open": GRAD_MAX_OPEN}}
POOL_WAIT_MINUTES = 15  # graduated but no pool price yet: after this, closed at the curve's last price
# costs (assumptions, shown with every number)
CURVE_FEE = 0.0125
POOL_FEE = 0.003
TX_COST_SOL = 0.001
GRADUATION_EVERY = 10  # runs between graduation-gap checks (odd runs: never together with the even-run Graduation Run scan)
MAX_PLAUSIBLE_GAP_PCT = 50.0
BACKOFF = timedelta(minutes=3)  # after a 429 from pump.fun
HTTP_TIMEOUT = 8.0  # per request: the job is cancelled at 55 s
DEADLINE_SECONDS = 40.0  # after this, a run skips pool prices and graduation checks (the next run catches up)
GRADUATION_TRIES = 4  # graduations a check looks up at most (each is up to two GeckoTerminal calls)


@dataclass
class Watch:
    mint: str
    curve: str
    symbol: str
    name: str
    image: str | None
    launched_at: datetime
    first_seen_at: datetime
    first_mc_sol: float


@dataclass
class Curve:
    vtr: int  # virtual token reserves (raw units)
    vsr: int  # virtual SOL reserves (lamports)
    rtr: int
    rsr: int  # real SOL in the curve (lamports)
    complete: bool

    @property
    def price(self) -> float:
        """SOL per whole token."""
        return (self.vsr / LAMPORTS) / (self.vtr / TOKEN_UNITS) if self.vtr else 0.0

    @property
    def mc_sol(self) -> float:
        return self.price * SUPPLY

    @property
    def progress(self) -> float:
        """Share of the curve's sellable tokens already sold: 1.0 = it graduates."""
        return max(0.0, min(1.0, 1 - self.rtr / INITIAL_REAL_TOKENS))


@dataclass
class Near:
    """A curve close to graduating, from pump.fun's fullest-curves list."""
    mint: str
    curve: str
    symbol: str
    name: str
    image: str | None
    launched_at: datetime
    progress: float | None = None  # at the previous scan (None until the chain has been read once)


_watch: dict[str, Watch] = {}
_near: dict[str, Near] = {}
_triggers: dict[str, tuple["Curve", datetime]] = {}  # mint -> the curve the moment the live feed saw the Starter rule met
_stream_new = 0  # launches the live feed added since the last run (counted into the day by the tick)
_seen: deque[str] = deque(maxlen=5000)
_state: dict[str, Any] = {"runs": 0, "last_run_at": None, "last_error": None, "blocked_until": None, "watching": 0}


# --- curve maths -----------------------------------------------------------------------------------------------------------


def buy_tokens(c: Curve, sol_in: float) -> float:
    """Whole tokens a buy of `sol_in` SOL gets from the curve, after the curve fee."""
    x = sol_in * (1 - CURVE_FEE) * LAMPORTS
    return (c.vtr - c.vsr * c.vtr / (c.vsr + x)) / TOKEN_UNITS


def sell_sol(c: Curve, tokens: float) -> float:
    """SOL selling `tokens` into the curve returns, after the curve fee."""
    t = tokens * TOKEN_UNITS
    return (c.vsr - c.vsr * c.vtr / (c.vtr + t)) / LAMPORTS * (1 - CURVE_FEE)


def parse_curve(b64: str) -> Curve | None:
    raw = base64.b64decode(b64)
    if len(raw) < 49:
        return None
    vtr, vsr, rtr, rsr = struct.unpack_from("<4Q", raw, 8)  # after the 8-byte account discriminator
    return Curve(vtr, vsr, rtr, rsr, bool(raw[48]))


# --- data -----------------------------------------------------------------------------------------------------------------


class Blocked(Exception):
    pass


async def _pump(client: httpx.AsyncClient, **params: Any) -> list[dict[str, Any]]:
    r = await client.get(f"{PUMP_API}/coins", params={"offset": 0, "includeNsfw": "false", **params}, headers=UA)
    if r.status_code == 429:
        raise Blocked()
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


async def curves(client: httpx.AsyncClient, rpc: str, addresses: list[str]) -> dict[str, Curve | None]:
    out: dict[str, Curve | None] = {}
    for i in range(0, len(addresses), 100):
        chunk = addresses[i:i + 100]
        try:
            r = await client.post(rpc, json={"jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts", "params": [chunk, {"encoding": "base64"}]})
            r.raise_for_status()
        except httpx.HTTPError as exc:  # this chunk is left out: unreadable this run (a trade only closes as "gone" after its timeout)
            logger.info("lunch curve read failed (%s)", type(exc).__name__)
            continue
        for addr, acc in zip(chunk, (r.json().get("result") or {}).get("value") or []):
            out[addr] = parse_curve(acc["data"][0]) if acc and acc.get("data") else None
    return out


async def pools_of(client: httpx.AsyncClient, mint: str) -> dict[str, str]:
    """{dex id: pool address} for the token on GeckoTerminal: "pump-fun" is the bonding curve itself, "pumpswap" the pool it
    graduated to."""
    r = await client.get(f"{GECKO}/networks/solana/tokens/{mint}/pools", params={"page": 1}, headers=UA)
    if r.status_code != 200:
        return {}
    out: dict[str, str] = {}
    for p in sorted(r.json().get("data") or [], key=lambda p: p.get("attributes", {}).get("pool_created_at") or ""):
        dex = str(p.get("relationships", {}).get("dex", {}).get("data", {}).get("id", ""))
        out.setdefault(dex, p["attributes"]["address"])
    return out


async def candle(client: httpx.AsyncClient, pool: str, which: str) -> float | None:
    """The pool's first one-minute open ("first") or latest one-minute close ("last"), in its quote token. Both sides of a
    comparison come from here, so they're always in the same units."""
    r = await client.get(f"{GECKO}/networks/solana/pools/{pool}/ohlcv/minute", params={"aggregate": 1, "limit": 1000, "currency": "token"}, headers=UA)
    if r.status_code != 200:
        return None
    rows = ((r.json().get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    if not rows:
        return None
    return float(min(rows, key=lambda c: c[0])[1]) if which == "first" else float(max(rows, key=lambda c: c[0])[4])


def _late(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


async def pool_price(client: httpx.AsyncClient, mint: str, deadline: float | None = None) -> dict[str, Any] | None:
    """The PumpSwap pool's latest one-minute close in SOL per token: {pool, price}, or None until GeckoTerminal has it (or the
    run is out of time)."""
    if _late(deadline):
        return None
    pool = (await pools_of(client, mint)).get("pumpswap")
    price = await candle(client, pool, "last") if pool and not _late(deadline) else None
    return {"pool": pool, "price": price} if price else None


# A standard SOL curve starts at 30 virtual SOL / 1,073,000,000 virtual tokens and completes with 279,900,000 virtual tokens
# left. The account is emptied on migration, so its last price is computed from that constant product.
START_VSR, START_VTR, FINAL_VTR = 30 * 10**9, 1_073_000_000 * 10**6, 279_900_000 * 10**6


def final_curve_price(vtr_raw: float) -> float | None:
    """SOL per token at completion, for a standard curve (its final virtual token reserves), else None."""
    if not vtr_raw or abs(vtr_raw / FINAL_VTR - 1) > 0.01:
        return None
    vsr = START_VSR * START_VTR / vtr_raw
    return (vsr / LAMPORTS) / (vtr_raw / TOKEN_UNITS)


# --- one run ---------------------------------------------------------------------------------------------------------------


def _day(db: Session, now: datetime) -> ArbLunchDay:
    d = now.date().isoformat()
    row = db.scalar(select(ArbLunchDay).where(ArbLunchDay.day == d))
    if row is None:
        row = ArbLunchDay(day=d, launches_seen=0, entries=0)
        db.add(row)
        db.flush()
    return row


def _launch_time(c: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(c["created_timestamp"]) / 1000, tz=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def prune(now: datetime) -> int:
    """Drops launches past their watch window (and their triggers). Called by every tick and by the live feed's poller."""
    gone = [m for m, w in _watch.items() if now - w.launched_at > timedelta(minutes=WATCH_MINUTES)]
    for m in gone:
        _watch.pop(m, None)
        _triggers.pop(m, None)
    return len(gone)


def _close(t: ArbLunchTrade, now: datetime, exit_sol: float, reason: str, **detail: Any) -> None:
    t.status, t.closed_at, t.exit_reason = "closed", now, reason
    t.exit_sol = round(max(0.0, exit_sol), 6)
    t.pnl_sol = round(t.exit_sol - t.entry_sol, 6)
    t.last_value_sol = t.exit_sol
    t.detail = {**(t.detail or {}), **detail}


# DB work runs in a worker thread (asyncio.to_thread) so the scheduler, the live feed and its poller keep running. Each helper is
# one step's whole use of the session, awaited one at a time: the session is never used from two threads at once.


def _count_launches(db: Session, now: datetime, n: int) -> None:
    _day(db, now).launches_seen += n


def _load(db: Session, mints: list[str]) -> tuple[list[ArbLunchTrade], set[tuple[str, str]]]:
    """Open trades, and every (mint, strategy) ever traded among the candidates: one query, not one per candidate."""
    opened = list(db.scalars(select(ArbLunchTrade).where(ArbLunchTrade.status == "open")))
    owned = {(m, s) for m, s in db.execute(select(ArbLunchTrade.mint, ArbLunchTrade.strategy).where(ArbLunchTrade.mint.in_(mints)))} if mints else set()
    return opened, owned


def _save_entries(db: Session, now: datetime, trades: list[ArbLunchTrade], stream_new: int) -> None:
    db.add_all(trades)
    if trades or stream_new:
        day = _day(db, now)
        day.entries += len(trades)
        day.launches_seen += stream_new  # launches the stream added since the last run
    db.commit()


async def tick(db: Session, now: datetime, rpc: str, transport: Any = None, stream: bool = False, stream_key: str | None = None) -> dict[str, Any]:
    """One run. With `stream`, PumpPortal's live feed (lunch_stream.py) is kept running alongside: it adds launches the moment
    they're created and records the exact moment (and price) a launch meets the Starter rule."""
    global _stream_new
    _state["runs"] += 1
    deadline = time.monotonic() + DEADLINE_SECONDS
    out: dict[str, Any] = {"new": 0, "entered": 0, "closed": 0}
    prune(now)
    if stream:
        from app.arbitrage import lunch_stream

        out["stream"] = lunch_stream.ensure_running(rpc, stream_key)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, transport=transport) as client:
        blocked = _state["blocked_until"] and now < _state["blocked_until"]
        if not blocked:
            try:
                fresh = await _pump(client, limit=50, sort="created_timestamp", order="DESC")
                for c in fresh:
                    at = _launch_time(c)
                    if (c.get("mint") in _seen or c.get("quote_mint", SOL_QUOTE) != SOL_QUOTE or c.get("complete") or not at
                            or not c.get("bonding_curve") or now - at > timedelta(minutes=WATCH_MINUTES)):
                        continue
                    _seen.append(c["mint"])
                    vsr, vtr = float(c.get("virtual_sol_reserves") or 0), float(c.get("virtual_token_reserves") or 0)
                    first = Curve(int(vtr), int(vsr), 0, 0, False)
                    _watch[c["mint"]] = Watch(c["mint"], c["bonding_curve"], str(c.get("symbol") or "?")[:40], str(c.get("name") or "")[:120],
                                              c.get("image_uri"), at, now, first.mc_sol)
                    out["new"] += 1
                await asyncio.to_thread(_count_launches, db, now, out["new"])
                if _state["runs"] % GRAD_SCAN_EVERY == 0:
                    out["near_graduation"] = await _scan_near(client)
            except Blocked:
                _state["blocked_until"] = now + BACKOFF
                out["pump_rate_limited"] = True
            except httpx.HTTPError as exc:  # 403, 5xx, a timeout: open trades are still priced and closed below
                out["pump_error"] = type(exc).__name__
        # the candidates are fixed here: a launch the live feed adds while this run awaits is left to the next run
        watched, near = list(_watch.items()), list(_near.items())
        open_trades, owned = await asyncio.to_thread(_load, db, list({m for m, _ in watched} | {m for m, _ in near}))
        addresses = list({w.curve for _, w in watched} | {n.curve for _, n in near} | {t.bonding_curve for t in open_trades})
        state = await curves(client, rpc, addresses) if addresses else {}
        taken = {(t.mint, t.strategy) for t in open_trades} | owned
        count = {s: sum(1 for t in open_trades if t.strategy == s) for s in STRATEGIES}
        entries: list[ArbLunchTrade] = []

        def enter(strategy: str, mint: str, curve: str, symbol: str, name: str, image: str | None, launched_at: datetime, c: Curve,
                  signal: dict[str, Any]) -> None:
            tokens = buy_tokens(c, SIZE_SOL)
            value = sell_sol(c, tokens) - TX_COST_SOL
            entries.append(ArbLunchTrade(strategy=strategy, mint=mint, bonding_curve=curve, symbol=symbol, name=name, image_uri=image, launched_at=launched_at,
                                         status="open", opened_at=now, entry_sol=round(SIZE_SOL + TX_COST_SOL, 6), tokens=tokens, entry_price=c.price,
                                         entry_mc_sol=round(c.mc_sol, 2), last_price=c.price, last_value_sol=round(value, 6), peak_value_sol=round(value, 6),
                                         last_checked_at=now, detail={"signal": signal}))
            taken.add((mint, strategy))
            count[strategy] += 1
            out["entered"] += 1

        def traded(mint: str, strategy: str) -> bool:
            return (mint, strategy) in taken  # open trades, this run's entries and every earlier trade of the candidates

        # no await from here to the save: the in-memory watchlist and the entries change together
        # 🥗 Starter entries: early momentum on new launches
        for m, w in watched:
            c = state.get(w.curve)
            trig = _triggers.pop(m, None)
            if trig and count["starter"] < MAX_OPEN and not (c is not None and c.complete):  # met live: entered at that moment's price
                tc, at = trig
                _watch.pop(m, None)
                if not traded(m, "starter"):
                    enter("starter", m, w.curve, w.symbol, w.name, w.image, w.launched_at, tc, {
                        "real_sol": round(tc.rsr / LAMPORTS, 3), "growth_pct": round((tc.mc_sol / w.first_mc_sol - 1) * 100, 1) if w.first_mc_sol else None,
                        "minutes_after_launch": round((at - w.launched_at).total_seconds() / 60, 1), "live": True,
                        "seconds_before_this_run": round((now - at).total_seconds())})
                continue
            if c is None or c.complete:
                if c is not None:
                    _watch.pop(m, None)
                continue
            if count["starter"] >= MAX_OPEN or now - w.first_seen_at < timedelta(seconds=50):
                continue
            real_sol, growth = c.rsr / LAMPORTS, (c.mc_sol / w.first_mc_sol - 1) if w.first_mc_sol else 0.0
            if real_sol < MIN_REAL_SOL or growth < MIN_GROWTH:
                continue
            _watch.pop(m, None)
            if not traded(m, "starter"):
                enter("starter", m, w.curve, w.symbol, w.name, w.image, w.launched_at, c, {
                    "real_sol": round(real_sol, 3), "growth_pct": round(growth * 100, 1),
                    "minutes_after_launch": round((now - w.launched_at).total_seconds() / 60, 1)})
        # 🍰 Graduation Run entries: a nearly full curve that's still filling
        for m, n in near:
            c = state.get(n.curve)
            if c is None:
                continue
            prev, n.progress = n.progress, c.progress
            if c.complete:
                _near.pop(m, None)
                continue
            if prev is None or count["graduation"] >= GRAD_MAX_OPEN or c.progress - prev < GRAD_MIN_RISE:
                continue
            if GRAD_MIN_PROGRESS <= c.progress <= GRAD_MAX_PROGRESS and not traded(m, "graduation"):
                enter("graduation", m, n.curve, n.symbol, n.name, n.image, n.launched_at, c, {
                    "progress_pct": round(c.progress * 100, 1), "rise_pts": round((c.progress - prev) * 100, 1), "real_sol": round(c.rsr / LAMPORTS, 2)})
        stream_new, _stream_new = _stream_new, 0
        if stream_new:
            out["new_from_stream"] = stream_new
        # entries saved before any GeckoTerminal call: the in-memory watchlist they came from is already updated
        await asyncio.to_thread(_save_entries, db, now, entries, stream_new)
        # exits, each by its strategy's rules (attribute changes only: no SQL until the commit below)
        for t in open_trades:
            rules = STRATEGIES.get(t.strategy, STRATEGIES["starter"])
            c = state.get(t.bonding_curve)
            age = now - t.opened_at
            if c is None:
                if age > timedelta(minutes=rules["timeout"] + 10):
                    _close(t, now, t.last_value_sol or 0.0, "gone", note="the curve account could not be read")
                    out["closed"] += 1
                continue
            t.last_checked_at = now
            if c.complete:  # graduated: sold in its PumpSwap pool (the curve account is emptied on migration)
                g = await pool_price(client, t.mint, deadline)
                last = t.last_price or t.entry_price
                if g:
                    _close(t, now, t.tokens * g["price"] * (1 - POOL_FEE) - TX_COST_SOL, "graduated", pool=g["pool"])
                    t.last_price = g["price"]
                    out["closed"] += 1
                elif age > timedelta(minutes=rules["timeout"] + POOL_WAIT_MINUTES):
                    _close(t, now, t.tokens * last * (1 - POOL_FEE) - TX_COST_SOL, "graduated", note="no pool price yet: valued at the curve's last price")
                    out["closed"] += 1
                else:
                    t.detail = {**(t.detail or {}), "awaiting_pool": True}
                continue
            value = sell_sol(c, t.tokens) - TX_COST_SOL
            t.last_price, t.last_value_sol = c.price, round(value, 6)
            t.peak_value_sol = round(max(t.peak_value_sol or value, value), 6)
            ret = value / t.entry_sol - 1
            reason = ("target" if rules["target"] is not None and ret >= rules["target"] else "stop" if ret <= rules["stop"]
                      else "timeout" if age >= timedelta(minutes=rules["timeout"]) else None)
            if reason:
                _close(t, now, value, reason)
                out["closed"] += 1
        await asyncio.to_thread(db.commit)
        if not blocked and _state["runs"] % GRADUATION_EVERY == 1 and not _late(deadline):
            try:
                out["graduations"] = await measure_graduations(db, client, now, deadline=deadline)
            except Blocked:
                _state["blocked_until"] = now + BACKOFF
            except httpx.HTTPError as exc:
                out["graduations_error"] = type(exc).__name__
    _state.update(last_run_at=now, last_error=None, watching=len(_watch))
    return out


async def _scan_near(client: httpx.AsyncClient) -> int:
    """pump.fun's fullest curves that haven't completed yet (SOL-quoted): the Graduation Run's candidates. Their progress is
    read from the chain each run; a candidate keeps its previous reading so 'still filling' can be measured."""
    rows = await _pump(client, limit=50, sort="market_cap", order="DESC", complete="false")
    fresh: dict[str, Near] = {}
    for c in rows:
        at = _launch_time(c)
        if c.get("complete") or c.get("quote_mint", SOL_QUOTE) != SOL_QUOTE or not c.get("bonding_curve") or not at:
            continue
        old = _near.get(c["mint"])
        fresh[c["mint"]] = Near(c["mint"], c["bonding_curve"], str(c.get("symbol") or "?")[:40], str(c.get("name") or "")[:120], c.get("image_uri"), at,
                                old.progress if old else None)
    _near.clear()
    _near.update(fresh)
    return len(fresh)


async def measure_graduations(db: Session, client: httpx.AsyncClient, now: datetime, per_run: int = 2, tries: int = GRADUATION_TRIES,
                              deadline: float | None = None) -> int:
    """Recently created launches that completed their curve: the curve's final price (computed: see final_curve_price) vs the
    first one-minute open of their PumpSwap pool (GeckoTerminal). At most `tries` lookups, and none after `deadline`."""
    done = await _pump(client, limit=20, sort="created_timestamp", order="DESC", complete="true")
    mints = [c["mint"] for c in done if c.get("mint")]
    known = await asyncio.to_thread(_known_graduations, db, mints) if mints else set()
    rows: list[ArbLunchGraduation] = []
    for c in done:
        if len(rows) >= per_run or tries <= 0 or _late(deadline):
            break
        if not c.get("mint") or c.get("quote_mint", SOL_QUOTE) != SOL_QUOTE or c["mint"] in known:
            continue
        curve_price = final_curve_price(float(c.get("virtual_token_reserves") or 0))
        if curve_price is None:
            continue  # not a standard curve: its final price can't be computed
        tries -= 1
        pools = await pools_of(client, c["mint"])
        if "pumpswap" not in pools or _late(deadline):
            continue  # GeckoTerminal hasn't indexed the pool yet: next time
        first = await candle(client, pools["pumpswap"], "first")
        if first is None:
            continue
        gap = (first / curve_price - 1) * 100 if first else None
        if gap is not None and abs(gap) > MAX_PLAUSIBLE_GAP_PCT:
            gap = None  # the pool is seeded at the curve's price: a first candle this far off is bad candle data, kept out of the stats
        rows.append(ArbLunchGraduation(mint=c["mint"], symbol=str(c.get("symbol") or "?")[:40], curve_price=curve_price, pool_price=first,
                                       gap_pct=round(gap, 3) if gap is not None else None, pool_address=pools["pumpswap"], pool_dex="pumpswap",
                                       completed_at=None, priced_at=now))  # pump.fun's list has no completion time
    await asyncio.to_thread(_save_graduations, db, rows)
    return len(rows)


def _known_graduations(db: Session, mints: list[str]) -> set[str]:
    return set(db.scalars(select(ArbLunchGraduation.mint).where(ArbLunchGraduation.mint.in_(mints))))


def _save_graduations(db: Session, rows: list[ArbLunchGraduation]) -> None:
    db.add_all(rows)
    db.commit()


# --- the page ---------------------------------------------------------------------------------------------------------------


def _trade_out(t: ArbLunchTrade) -> dict[str, Any]:
    value = t.exit_sol if t.status == "closed" else t.last_value_sol
    return {"id": t.id, "strategy": t.strategy, "mint": t.mint, "symbol": t.symbol, "name": t.name, "image_uri": t.image_uri, "status": t.status,
            "launched_at": t.launched_at.isoformat(), "opened_at": t.opened_at.isoformat(), "closed_at": t.closed_at.isoformat() if t.closed_at else None,
            "entry_sol": t.entry_sol, "entry_mc_sol": t.entry_mc_sol, "value_sol": value, "pnl_sol": round(value - t.entry_sol, 6) if value is not None else None,
            "pnl_pct": round((value / t.entry_sol - 1) * 100, 1) if value is not None and t.entry_sol else None, "exit_reason": t.exit_reason,
            "signal": (t.detail or {}).get("signal"), "awaiting_pool": bool((t.detail or {}).get("awaiting_pool")) and t.status == "open",
            "pump_url": f"https://pump.fun/coin/{t.mint}", "solscan_url": f"https://solscan.io/token/{t.mint}"}


def _closed_groups(db: Session) -> list[Any]:
    """Closed trades aggregated in SQL, one row per (strategy, exit reason): count, wins, pnl sum, worst, best."""
    pnl = func.coalesce(ArbLunchTrade.pnl_sol, 0.0)
    return list(db.execute(select(ArbLunchTrade.strategy, ArbLunchTrade.exit_reason, func.count(ArbLunchTrade.id), func.sum(case((pnl > 0, 1), else_=0)),
                                  func.sum(pnl), func.min(pnl), func.max(pnl))
                           .where(ArbLunchTrade.status == "closed").group_by(ArbLunchTrade.strategy, ArbLunchTrade.exit_reason)))


def _stats(groups: list[Any], opened: list[ArbLunchTrade]) -> dict[str, Any]:
    n, wins, total = sum(g[2] for g in groups), sum(int(g[3] or 0) for g in groups), sum(float(g[4] or 0) for g in groups)
    reasons: dict[str, int] = {}
    for g in groups:
        reasons[g[1] or "?"] = reasons.get(g[1] or "?", 0) + g[2]
    return {"trades": n, "wins": wins, "win_rate": round(wins / n * 100, 1) if n else None,
            "pnl_sol": round(total, 4), "avg_pnl_sol": round(total / n, 5) if n else None,
            "best_sol": round(max(float(g[6]) for g in groups), 4) if n else None, "worst_sol": round(min(float(g[5]) for g in groups), 4) if n else None,
            "by_exit": reasons,
            "open": len(opened), "open_value_sol": round(sum((t.last_value_sol or 0) for t in opened), 4), "open_cost_sol": round(sum(t.entry_sol for t in opened), 4)}


SERIES_DAYS = 30
GAP_SAMPLE = 2000  # newest measured gaps a median is taken over (avg / count are SQL over all rows)


def daily_series(db: Session, now: datetime, days: int = SERIES_DAYS) -> dict[str, list[dict[str, Any]]]:
    """Closed trades per strategy per UTC day over the last `days` (every day present, zeros included): trades, wins, pnl, and
    the running total from the window's start. One grouped SQL query: at most strategies x days rows."""
    start = (now - timedelta(days=days - 1)).date()
    pnl = func.coalesce(ArbLunchTrade.pnl_sol, 0.0)
    day = func.date(ArbLunchTrade.closed_at)
    rows = db.execute(select(ArbLunchTrade.strategy, day, func.count(ArbLunchTrade.id), func.sum(case((pnl > 0, 1), else_=0)), func.sum(pnl))
                      .where(ArbLunchTrade.status == "closed", ArbLunchTrade.closed_at >= datetime(start.year, start.month, start.day, tzinfo=timezone.utc))
                      .group_by(ArbLunchTrade.strategy, day))
    got = {(r[0], str(r[1])[:10]): r for r in rows}
    out: dict[str, list[dict[str, Any]]] = {}
    for strat in STRATEGIES:
        cum, pts = 0.0, []
        for i in range(days):
            d = (start + timedelta(days=i)).isoformat()
            r = got.get((strat, d))
            p = float(r[4] or 0) if r else 0.0
            cum += p
            pts.append({"day": d, "trades": int(r[2]) if r else 0, "wins": int(r[3] or 0) if r else 0, "pnl_sol": round(p, 4), "cumulative": round(cum, 4)})
        out[strat] = pts
    return out


def gap_stats(db: Session, now: datetime) -> dict[str, Any]:
    """Curve-to-pool gap: count / avg in SQL, median over the newest GAP_SAMPLE; all time and the last 7 days."""
    at = func.coalesce(ArbLunchGraduation.priced_at, ArbLunchGraduation.created_at)

    def one(since: datetime | None) -> dict[str, Any]:
        cond = [ArbLunchGraduation.gap_pct.is_not(None)] + ([at >= since] if since else [])
        n, avg, pos = db.execute(select(func.count(ArbLunchGraduation.id), func.avg(ArbLunchGraduation.gap_pct),
                                        func.sum(case((ArbLunchGraduation.gap_pct > 0, 1), else_=0))).where(*cond)).one()
        vals = list(db.scalars(select(ArbLunchGraduation.gap_pct).where(*cond).order_by(at.desc()).limit(GAP_SAMPLE)))
        return {"count": int(n or 0), "avg_gap_pct": round(float(avg), 2) if avg is not None else None,
                "median_gap_pct": round(median(vals), 2) if vals else None, "positive": int(pos or 0)}

    return {"all": one(None), "last_7d": one(now - timedelta(days=7)), "costs_pct": round((CURVE_FEE + POOL_FEE) * 100, 2)}


def summary(db: Session, now: datetime) -> dict[str, Any]:
    groups = _closed_groups(db)
    closed = list(db.scalars(select(ArbLunchTrade).where(ArbLunchTrade.status == "closed").order_by(ArbLunchTrade.closed_at.desc()).limit(30)))
    opened = list(db.scalars(select(ArbLunchTrade).where(ArbLunchTrade.status == "open").order_by(ArbLunchTrade.opened_at.desc())))
    grads = list(db.scalars(select(ArbLunchGraduation).order_by(ArbLunchGraduation.id.desc()).limit(200)))
    gaps = [g.gap_pct for g in grads if g.gap_pct is not None]
    days = list(db.scalars(select(ArbLunchDay).where(ArbLunchDay.day >= (now - timedelta(days=6)).date().isoformat()).order_by(ArbLunchDay.day)))
    blocked = _state["blocked_until"]
    return {
        "mode": "paper",
        "stats": _stats(groups, opened),
        "by_strategy": {s: _stats([g for g in groups if g[0] == s], [t for t in opened if t.strategy == s]) for s in STRATEGIES},
        "open": [_trade_out(t) for t in opened],
        "closed": [_trade_out(t) for t in closed],
        "graduations": {"measured": len(gaps), "avg_gap_pct": round(sum(gaps) / len(gaps), 2) if gaps else None,
                        "median_gap_pct": round(median(gaps), 2) if gaps else None, "positive": sum(1 for g in gaps if g > 0),
                        "after_costs_positive": sum(1 for g in gaps if g > (CURVE_FEE + POOL_FEE) * 100),
                        "recent": [{"mint": g.mint, "symbol": g.symbol, "curve_price": g.curve_price, "pool_price": g.pool_price, "gap_pct": g.gap_pct,
                                    "dex": g.pool_dex, "at": (g.priced_at or g.created_at).isoformat()} for g in grads[:12]]},
        "scoreboard": {"days": SERIES_DAYS, "series": daily_series(db, now), "gaps": gap_stats(db, now)},
        "days": [{"day": d.day, "launches_seen": d.launches_seen, "entries": d.entries} for d in days],
        "menu": {"size_sol": SIZE_SOL, "watch_minutes": WATCH_MINUTES, "min_real_sol": MIN_REAL_SOL, "min_growth_pct": MIN_GROWTH * 100,
                 "target_pct": TARGET * 100, "stop_pct": STOP * 100, "timeout_minutes": TIMEOUT_MINUTES, "max_open": MAX_OPEN,
                 "graduation": {"min_progress_pct": GRAD_MIN_PROGRESS * 100, "max_progress_pct": GRAD_MAX_PROGRESS * 100,
                                "min_rise_pts": GRAD_MIN_RISE * 100, "stop_pct": GRAD_STOP * 100, "timeout_minutes": GRAD_TIMEOUT_MINUTES,
                                "max_open": GRAD_MAX_OPEN, "near_now": len(_near)}},
        "costs": {"curve_fee_pct": CURVE_FEE * 100, "pool_fee_pct": POOL_FEE * 100, "tx_cost_sol": TX_COST_SOL},
        "scanner": {"last_run_at": _state["last_run_at"].isoformat() if _state["last_run_at"] else None, "watching": _state["watching"],
                    "rate_limited_until": blocked.isoformat() if blocked and blocked > now else None, "last_error": _state["last_error"],
                    "live": _live_status()},
    }


def _live_status() -> dict[str, Any] | None:
    from app.arbitrage import lunch_stream

    return lunch_stream.status() if lunch_stream._task is not None else None


def record_error(exc: Exception) -> None:
    _state["last_error"] = f"{type(exc).__name__}"[:120]

