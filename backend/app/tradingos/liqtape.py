"""Live liquidation streams: OKX (every swap), Binance USDⓈ-M (all markets) and Bybit linear (the most traded markets), from
their public websockets. Each liquidation an exchange reports is buffered here and written to ts_liquidations by the
market-structure recorder job every 2 minutes. A venue that can't be reached (region block, outage) stays disconnected,
and charts say so; nothing is estimated in its place.

Binance's stream shows at most one liquidation per market per second (the latest), so its totals are a lower bound."""

import json
import logging
import threading
import time
from collections import deque
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OKX_WS = "wss://ws.okx.com:8443/ws/v5/public"
BINANCE_WS = "wss://fstream.binance.com/market/ws/!forceOrder@arr"  # the /ws/ route no longer delivers (checked 2026-10-20)
BYBIT_WS = "wss://stream.bybit.com/v5/public/linear"
MAX_BUFFER = 50_000


def base_of(symbol: str) -> str:
    """BTC-USDT-SWAP / BTCUSDT / 1000PEPEUSDT -> BTC / PEPE (the coin, as structure.symbols() maps it back)."""
    s = symbol.upper().split("-")[0]
    for suffix in ("USDT", "USDC", "USD"):
        if s.endswith(suffix) and len(s) > len(suffix):
            s = s[: -len(suffix)]
            break
    for mult in ("1000000", "10000", "1000"):
        if s.startswith(mult) and len(s) > len(mult):
            return s[len(mult):]
    return s


class Stream:
    def __init__(self, venue: str) -> None:
        self.venue = venue
        self.since: float | None = None  # current connection opened (unix s); None while disconnected
        self.error: str | None = None
        self.received = 0
        self.thread: threading.Thread | None = None


class LiqTape:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.buffer: deque[dict[str, Any]] = deque(maxlen=MAX_BUFFER)
        self.streams = {v: Stream(v) for v in ("okx", "binance", "bybit")}
        self.okx_ct: dict[str, tuple[float, str]] = {}  # instId -> (ctVal, ctValCcy)

    def push(self, venue: str, symbol: str, t: int, side: str, px: float, qty: float, usd: float) -> None:
        if px > 0 and qty > 0 and side in ("long", "short"):
            with self.lock:
                self.buffer.append({"venue": venue, "coin": base_of(symbol), "t": int(t), "side": side, "px": px, "qty": qty, "usd": usd})
                self.streams[venue].received += 1

    def drain(self) -> list[dict[str, Any]]:
        with self.lock:
            out = list(self.buffer)
            self.buffer.clear()
        return out

    def status(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return {v: {"connected": s.since is not None, "since": s.since, "error": s.error, "received": s.received} for v, s in self.streams.items()}

    # ------------------------------------------------------------------ message parsing (pure, tested)
    def on_okx(self, msg: dict[str, Any]) -> None:
        for row in msg.get("data") or []:
            inst = str(row.get("instId", ""))
            ct, ccy = self.okx_ct.get(inst, (0.0, ""))
            for d in row.get("details") or []:
                px, sz = float(d.get("bkPx") or 0), float(d.get("sz") or 0)
                if not ct or not px:
                    continue
                usd, qty = (sz * ct, sz * ct / px) if ccy == "USD" else (sz * ct * px, sz * ct)  # inverse swaps: contracts are USD
                self.push("okx", inst, int(d.get("ts") or 0), str(d.get("posSide", "")), px, qty, usd)

    def on_binance(self, msg: dict[str, Any]) -> None:
        o = msg.get("o") or {}
        px, q = float(o.get("ap") or o.get("p") or 0), float(o.get("z") or o.get("q") or 0)
        side = "long" if o.get("S") == "SELL" else "short" if o.get("S") == "BUY" else ""  # a forced SELL closes a long
        self.push("binance", str(o.get("s", "")), int(o.get("T") or 0), side, px, q, px * q)

    def on_bybit(self, msg: dict[str, Any]) -> None:
        for d in msg.get("data") or []:
            px, q = float(d.get("p") or 0), float(d.get("v") or 0)
            side = "long" if d.get("S") == "Buy" else "short" if d.get("S") == "Sell" else ""  # Bybit: "Buy" = a long was liquidated
            self.push("bybit", str(d.get("s", "")), int(d.get("T") or 0), side, px, q, px * q)


_tape = LiqTape()


def tape() -> LiqTape:
    return _tape


def _okx_contracts(t: LiqTape) -> None:
    rows = httpx.get("https://www.okx.com/api/v5/public/instruments", params={"instType": "SWAP"}, timeout=15).json().get("data") or []
    t.okx_ct = {r["instId"]: (float(r.get("ctVal") or 0), str(r.get("ctValCcy") or "")) for r in rows}


def _run(t: LiqTape, venue: str, symbols: tuple[str, ...]) -> None:
    from websockets.sync.client import connect

    s = t.streams[venue]
    wait = 5.0
    while True:
        try:
            if venue == "okx" and not t.okx_ct:
                _okx_contracts(t)
            url = {"okx": OKX_WS, "binance": BINANCE_WS, "bybit": BYBIT_WS}[venue]
            with connect(url, open_timeout=15, close_timeout=5, max_size=2**22) as ws:
                if venue == "okx":
                    ws.send(json.dumps({"op": "subscribe", "args": [{"channel": "liquidation-orders", "instType": "SWAP"}]}))
                elif venue == "bybit":
                    for i in range(0, len(symbols), 10):
                        ws.send(json.dumps({"op": "subscribe", "args": [f"allLiquidation.{x}" for x in symbols[i:i + 10]]}))
                with t.lock:
                    s.since, s.error = time.time(), None
                wait = 5.0
                last_ping = time.time()
                while True:
                    if venue in ("okx", "bybit") and time.time() - last_ping > 20:  # both drop idle connections
                        ws.send("ping" if venue == "okx" else json.dumps({"op": "ping"}))
                        last_ping = time.time()
                    try:
                        raw = ws.recv(timeout=25)
                    except TimeoutError:
                        continue
                    if raw == "pong":
                        continue
                    msg = json.loads(raw)
                    if venue == "okx" and (msg.get("arg") or {}).get("channel") == "liquidation-orders" and "data" in msg:
                        t.on_okx(msg)
                    elif venue == "binance" and msg.get("e") == "forceOrder":
                        t.on_binance(msg)
                    elif venue == "bybit" and str(msg.get("topic", "")).startswith("allLiquidation."):
                        t.on_bybit(msg)
        except Exception as e:  # noqa: BLE001 - reconnect with backoff; the gap is visible in status()
            with t.lock:
                s.since, s.error = None, f"{type(e).__name__}: {e}"[:200]
            if wait == 5.0:
                logger.warning("%s liquidation stream disconnected (%s)", venue, s.error)
            time.sleep(wait)
            wait = min(300.0, wait * 2)


def ensure(bybit_symbols: tuple[str, ...]) -> LiqTape:
    """Start each venue's stream once per process (the recorder job calls this every 2 minutes)."""
    for venue, s in _tape.streams.items():
        with _tape.lock:
            alive = s.thread is not None and s.thread.is_alive()
        if not alive:
            s.thread = threading.Thread(target=_run, args=(_tape, venue, bybit_symbols), name=f"liq-{venue}", daemon=True)
            s.thread.start()
    return _tape
