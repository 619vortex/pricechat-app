"""Hyperliquid trade tape: every public trade of a few markets over the last hour, from Hyperliquid's websocket (each trade
carries the buyer's and seller's on-chain addresses). Used to look for TWAP-like slicing per address (structure.py) where
Binance can't be reached. Started by the market-structure recorder job; a gap (disconnect) stays a gap, never filled in."""

import json
import logging
import threading
import time
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

WS = "wss://api.hyperliquid.xyz/ws"
KEEP_S = 3600
COINS = ("BTC", "ETH", "SOL")


class Tape:
    def __init__(self) -> None:
        self.trades: dict[str, deque[tuple[int, float, float, str, str]]] = {}  # coin -> (ms, px, sz, aggressor side, aggressor address)
        self.since: float | None = None  # when the current unbroken recording began (unix s)
        self.error: str | None = None
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.coins: tuple[str, ...] = ()

    def add(self, rows: list[dict[str, Any]]) -> None:
        cut = (time.time() - KEEP_S) * 1000
        with self.lock:
            for r in rows:
                users = r.get("users") or ["", ""]
                buy = r.get("side") == "B"  # B: the buyer took liquidity
                q = self.trades.setdefault(str(r.get("coin", "")).upper(), deque())
                q.append((int(r["time"]), float(r["px"]), float(r["sz"]), "buy" if buy else "sell", str(users[0] if buy else users[1])))
            for q in self.trades.values():
                while q and q[0][0] < cut:
                    q.popleft()

    def get(self, coin: str) -> tuple[list[tuple[int, float, float, str, str]], float | None]:
        with self.lock:
            return list(self.trades.get(coin.upper(), ())), self.since


_tape = Tape()


def tape() -> Tape:
    return _tape


def _run(t: Tape) -> None:
    from websockets.sync.client import connect

    wait = 5.0
    while True:
        try:
            with connect(WS, open_timeout=15, close_timeout=5, max_size=2**22) as ws:
                for c in t.coins:
                    ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": c}}))
                with t.lock:
                    t.since, t.error = time.time(), None
                    t.trades.clear()  # a new unbroken recording: nothing from before the gap is joined to it
                wait = 5.0
                while True:
                    msg = json.loads(ws.recv(timeout=90))
                    if msg.get("channel") == "trades":
                        t.add(msg.get("data") or [])
        except Exception as e:  # noqa: BLE001 - reconnect with backoff; the gap is reported via `since`
            with t.lock:
                t.since, t.error = None, f"{type(e).__name__}: {e}"[:200]
            logger.warning("Hyperliquid trade tape disconnected (%s); retrying in %.0fs", t.error, wait)
            time.sleep(wait)
            wait = min(120.0, wait * 2)


def ensure(coins: tuple[str, ...] = COINS) -> Tape:
    """Start the recorder thread once per process (the recorder job calls this every 2 minutes)."""
    with _tape.lock:
        if _tape.thread is None or not _tape.thread.is_alive():
            _tape.coins = coins
            _tape.thread = threading.Thread(target=_run, args=(_tape,), name="hl-tape", daemon=True)
            _tape.thread.start()
    return _tape
