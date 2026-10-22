"""Lunch Bot's live feed: PumpPortal's data stream (wss://pumpportal.fun/api/data; verified 2026-10-21) + a fast chain check.

- subscribeNewToken (free): every pump.fun launch the moment it's created (mint, bondingCurveKey, vSolInBondingCurve,
  vTokensInBondingCurve, marketCapSol). New SOL-quoted pump curves join Lunch Bot's watchlist at once, instead of on the
  next minute's list read (which pump.fun also rate-limits).
- Fast check (free): every POLL_SECONDS, every watched curve is read from Solana in one getMultipleAccounts call; the first
  moment a launch meets the Starter rule, that exact curve state is kept as the trigger, and the next tick enters at that
  price (within ~10 s of the move, not up to a minute later). Exits and everything else still come from the chain.
- subscribeTokenTrade (trade-by-trade triggers) needs a PumpPortal API key funded with 0.02 SOL (verified: refused
  without one); when the PUMPPORTAL_API_KEY setting is set, it's used on top.

The stream only ever fills in-memory state; the database is written by the tick (lunch.tick), so the two never race. It
runs inside the job's process, reconnecting with backoff; if it's down, Lunch Bot simply works as before."""

import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from typing import Any

from app.arbitrage import lunch

logger = logging.getLogger(__name__)

URL = "wss://pumpportal.fun/api/data"
INITIAL_VSOL = 30.0  # a standard curve's starting virtual SOL: real SOL in the curve = virtual - 30
MIN_STREAM_AGE_S = 10  # a launch must have been watched this long before a trigger counts (not its own first trade)
POLL_SECONDS = 10
_task: asyncio.Task[None] | None = None
_poller: asyncio.Task[None] | None = None
_state: dict[str, Any] = {"connected": False, "events": 0, "last_event_at": None, "reconnects": 0, "fast_triggers": 0}
_subscribed: set[str] = set()


def curve_of(msg: dict[str, Any]) -> "lunch.Curve | None":
    """The curve after this event, from its virtual reserves (SOL and whole tokens)."""
    try:
        vsol, vtok = float(msg["vSolInBondingCurve"]), float(msg["vTokensInBondingCurve"])
    except (KeyError, TypeError, ValueError):
        return None
    if vsol <= 0 or vtok <= 0:
        return None
    return lunch.Curve(int(vtok * lunch.TOKEN_UNITS), int(vsol * lunch.LAMPORTS), 0, int(max(0.0, vsol - INITIAL_VSOL) * lunch.LAMPORTS), False)


def handle(msg: dict[str, Any], now: datetime | None = None) -> str | None:
    """One stream message -> the in-memory watchlist / triggers. Returns a mint to subscribe to, if a new launch joined."""
    now = now or datetime.now(timezone.utc)
    mint = msg.get("mint")
    if not mint:
        return None
    _state["events"] += 1
    _state["last_event_at"] = now
    if msg.get("txType") == "create":
        if msg.get("pool") != "pump" or not msg.get("bondingCurveKey") or mint in lunch._seen:
            return None  # bonk / other launchpads, or already watched
        c = curve_of(msg)
        if c is None:
            return None
        lunch._seen.append(mint)
        lunch._watch[mint] = lunch.Watch(mint, msg["bondingCurveKey"], str(msg.get("symbol") or "?")[:40], str(msg.get("name") or "")[:120], None,
                                         now, now, c.mc_sol)
        lunch._stream_new += 1
        return mint
    if msg.get("txType") in ("buy", "sell"):
        check(mint, curve_of(msg), now)
    return None


def check(mint: str, c: "lunch.Curve | None", now: datetime) -> bool:
    """Record the trigger the first moment a watched launch meets the Starter rule (from a trade event or a fast chain read)."""
    w = lunch._watch.get(mint)
    if w is None or c is None or c.complete or mint in lunch._triggers or (now - w.first_seen_at).total_seconds() < MIN_STREAM_AGE_S:
        return False
    real_sol, growth = c.rsr / lunch.LAMPORTS, (c.mc_sol / w.first_mc_sol - 1) if w.first_mc_sol else 0.0
    if real_sol >= lunch.MIN_REAL_SOL and growth >= lunch.MIN_GROWTH:
        lunch._triggers[mint] = (c, now)  # the price the moment the rule was met
        return True
    return False


async def _poll(rpc: str) -> None:
    """Every POLL_SECONDS: the watched curves from Solana (one batched call), checked against the Starter rule."""
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as client:
        while True:
            lunch.prune(datetime.now(timezone.utc))  # the tick may not run (bot off, job stalled): the watchlist must not grow
            watch = {w.curve: m for m, w in lunch._watch.items() if m not in lunch._triggers}
            if watch:
                try:
                    now = datetime.now(timezone.utc)
                    for curve, c in (await lunch.curves(client, rpc, list(watch))).items():
                        if check(watch[curve], c, now):
                            _state["fast_triggers"] += 1
                except Exception as exc:  # noqa: BLE001 - a slow or failed read: the next one in 10 s
                    logger.info("lunch fast check failed (%s)", type(exc).__name__)
            await asyncio.sleep(POLL_SECONDS)


async def _run(api_key: str | None = None) -> None:
    import websockets

    attempt = 0
    url = f"{URL}?api-key={api_key}" if api_key else URL
    while True:
        try:
            async with websockets.connect(url, open_timeout=15, ping_interval=20) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                _state["connected"], attempt = True, 0
                _subscribed.clear()
                while True:
                    gone = [m for m in _subscribed if m not in lunch._watch]  # dropped from the watchlist: stop its trades
                    if gone:
                        await ws.send(json.dumps({"method": "unsubscribeTokenTrade", "keys": gone}))
                        _subscribed.difference_update(gone)
                    try:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    except asyncio.TimeoutError:
                        continue
                    new = handle(msg) if isinstance(msg, dict) else None
                    if new and api_key:  # trade-by-trade triggers: a funded PumpPortal key only (refused without one)
                        await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [new]}))
                        _subscribed.add(new)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - any stream failure: back off and reconnect; the tick keeps working meanwhile
            _state["connected"] = False
            _state["reconnects"] += 1
            attempt += 1
            delay = min(120.0, 2 ** attempt) * (0.5 + random.random() / 2)
            logger.info("pumpportal stream dropped (%s); reconnecting in %.0fs", type(exc).__name__, delay)
            await asyncio.sleep(delay)


def ensure_running(rpc: str, api_key: str | None = None) -> bool:
    """Start the stream and the fast chain check in this event loop if they aren't running (called by every Lunch Bot tick)."""
    global _task, _poller
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    if _task is None or _task.done():
        _task = loop.create_task(_run(api_key))
    if _poller is None or _poller.done():
        _poller = loop.create_task(_poll(rpc))
    return True


def stop() -> bool:
    """Cancel the stream and the fast chain check (the bot was switched off). Returns whether anything was running."""
    global _task, _poller
    running = False
    for t in (_task, _poller):
        if t is not None and not t.done():
            t.cancel()
            running = True
    _task = _poller = None
    _state["connected"] = False
    _subscribed.clear()
    return running


def status() -> dict[str, Any]:
    at = _state["last_event_at"]
    return {"connected": _state["connected"], "events": _state["events"], "last_event_at": at.isoformat() if at else None,
            "reconnects": _state["reconnects"], "fast_check_seconds": POLL_SECONDS, "fast_triggers": _state["fast_triggers"],
            "trade_feed": bool(_subscribed)}
