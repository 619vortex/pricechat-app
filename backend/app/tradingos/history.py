"""Historical Hyperliquid data for research (backtests): candles per interval and hourly funding, cached as JSON files.

Hyperliquid's candleSnapshot serves the most recent 5000 candles per interval, so the depth is fixed by the exchange:
5m ≈ 17 days, 15m ≈ 52 days, 1h ≈ 208 days, 4h ≈ 2.3 years. Funding history is paged (500 rows per request).
Nothing here is used by live trading. Run: python -m app.tradingos.history [COINS...]
"""

import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from app.tradingos.market import INTERVALS, Candle

API = "https://api.hyperliquid.xyz/info"
DIR = Path(__file__).resolve().parents[3] / "data" / "backtest"
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "HYPE", "SUI", "AVAX", "LINK", "BNB", "LTC", "ADA"]
MAX_CANDLES = 5000


def _post(c: httpx.Client, body: dict[str, Any]) -> Any:
    for attempt in range(5):
        r = c.post(API, json=body)
        if r.status_code == 429:
            time.sleep(2 + attempt * 3)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Hyperliquid kept rate-limiting")


def fetch(coins: list[str], intervals: tuple[str, ...] = ("5m", "15m", "1h", "4h")) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    now = int(time.time() * 1000)
    with httpx.Client(timeout=60) as c:
        for coin in coins:
            for iv in intervals:
                start = now - INTERVALS[iv] * 1000 * MAX_CANDLES
                rows = _post(c, {"type": "candleSnapshot", "req": {"coin": coin, "interval": iv, "startTime": start, "endTime": now}})
                (DIR / f"{coin}_{iv}.json").write_text(json.dumps([[r["t"], r["o"], r["h"], r["l"], r["c"], r["v"]] for r in rows]))
                print(coin, iv, len(rows), flush=True)
                time.sleep(0.3)
            fund: list[list[Any]] = []
            t = now - 210 * 86_400_000
            while t < now:
                rows = _post(c, {"type": "fundingHistory", "coin": coin, "startTime": t})
                if not rows:
                    break
                fund += [[r["time"], r["fundingRate"]] for r in rows]
                t = rows[-1]["time"] + 1
                if len(rows) < 500:
                    break
                time.sleep(0.3)
            (DIR / f"{coin}_funding.json").write_text(json.dumps(fund))
            print(coin, "funding", len(fund), flush=True)


BINANCE = "https://data.binance.vision/data/futures/um/monthly"


def _binance_csv(c: httpx.Client, url: str) -> list[list[str]]:
    import csv
    import io
    import zipfile

    r = c.get(url)
    if r.status_code == 404:
        return []  # not listed yet that month
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        text = z.read(z.namelist()[0]).decode()
    return [row for row in csv.reader(io.StringIO(text)) if row and row[0][:1].isdigit()]  # some files have a header row


def fetch_binance(coins: list[str], months: int = 24, intervals: tuple[str, ...] = ("15m", "1h", "4h")) -> None:
    """Deep history for machine-learning research (ml.py): Binance USDⓈ-M perpetual klines and funding from the public
    monthly archive (data.binance.vision), the last `months` complete months. Hyperliquid only serves 5000 candles (52 days
    of 15m); Binance perps track the same prices closely. Funding (8h, or 4h on some coins) is spread over hourly rows so
    it reads like Hyperliquid's hourly funding. Saved in data/backtest/binance/ in the same format as fetch()."""
    from concurrent.futures import ThreadPoolExecutor

    out = DIR / "binance"
    out.mkdir(parents=True, exist_ok=True)
    now = time.gmtime()
    ym = [(now.tm_year * 12 + now.tm_mon - 1 - k) for k in range(months, 0, -1)]
    tags = [f"{m // 12}-{m % 12 + 1:02d}" for m in ym]
    with httpx.Client(timeout=120) as c:
        for coin in coins:
            sym = f"{coin}USDT"
            with ThreadPoolExecutor(8) as pool:
                for iv in intervals:
                    parts = pool.map(lambda tag: _binance_csv(c, f"{BINANCE}/klines/{sym}/{iv}/{sym}-{iv}-{tag}.zip"), tags)
                    rows = [[int(r[0]), r[1], r[2], r[3], r[4], r[5]] for p in parts for r in p]
                    (out / f"{coin}_{iv}.json").write_text(json.dumps(sorted({r[0]: r for r in rows}.values())))
                    print(coin, iv, len(rows), flush=True)
                parts = pool.map(lambda tag: _binance_csv(c, f"{BINANCE}/fundingRate/{sym}/{sym}-fundingRate-{tag}.zip"), tags)
                fund = []
                for r in (r for p in parts for r in p):
                    t, hours, rate = int(r[0]) // 3_600_000 * 3_600_000, int(r[1] or 8), float(r[2])
                    fund += [[t - h * 3_600_000, rate / hours] for h in range(hours - 1, -1, -1)]
                (out / f"{coin}_funding.json").write_text(json.dumps(sorted(fund)))
                print(coin, "funding", len(fund), flush=True)


def candles(coin: str, interval: str, source: str = "") -> list[Candle]:
    rows = json.loads((DIR / source / f"{coin}_{interval}.json").read_text())
    return [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]


def funding(coin: str, source: str = "") -> list[tuple[int, float]]:
    p = DIR / source / f"{coin}_funding.json"
    return [(int(t), float(f)) for t, f in json.loads(p.read_text())] if p.exists() else []


if __name__ == "__main__":
    args = [c.upper() for c in sys.argv[1:]]
    if args[:1] == ["BINANCE"]:
        fetch_binance(args[1:] or COINS)
    else:
        fetch(args or COINS)
