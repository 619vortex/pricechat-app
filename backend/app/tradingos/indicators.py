"""Technical indicators as pure functions over closing prices / candles (oldest first). Deterministic and testable."""

from collections.abc import Sequence


def ema(values: Sequence[float], period: int) -> list[float]:
    """Exponential moving average; the first value is seeded with the simple average of the first `period` values.
    Returns a list aligned with `values` (entries before the seed are the seed)."""
    if len(values) < period or period < 1:
        return []
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    out = [seed] * period
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes: Sequence[float], period: int = 14) -> float | None:
    """Wilder's RSI of the last value (0-100)."""
    if len(closes) <= period:
        return None
    gains = losses = 0.0
    for a, b in zip(closes[:period], closes[1:period + 1]):
        d = b - a
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g, avg_l = gains / period, losses / period
    for a, b in zip(closes[period:-1], closes[period + 1:]):
        d = b - a
        avg_g = (avg_g * (period - 1) + max(d, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0.0)) / period
    if avg_l == 0:
        return 100.0 if avg_g > 0 else 50.0
    return 100 - 100 / (1 + avg_g / avg_l)


def macd(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[float, float, float] | None:
    """(macd line, signal line, histogram) for the last value."""
    if len(closes) < slow + signal:
        return None
    f, s = ema(closes, fast), ema(closes, slow)
    line = [a - b for a, b in zip(f[slow - 1:], s[slow - 1:])]
    sig = ema(line, signal)
    if not sig:
        return None
    return line[-1], sig[-1], line[-1] - sig[-1]


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> float | None:
    """Wilder's Average True Range of the last candle."""
    n = len(closes)
    if n <= period or len(highs) != n or len(lows) != n:
        return None
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])) for i in range(1, n)]
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return a


def swing_levels(highs: Sequence[float], lows: Sequence[float], lookback: int = 50) -> tuple[float, float]:
    """(support, resistance): the lowest low and highest high of the last `lookback` candles."""
    h, lo = highs[-lookback:], lows[-lookback:]
    return min(lo), max(h)


def bollinger(closes: Sequence[float], period: int = 20, k: float = 2.0) -> tuple[float, float, float] | None:
    """(middle, upper, lower): the period's simple average ± k population standard deviations of the closes."""
    if len(closes) < period:
        return None
    w = list(closes[-period:])
    mid = sum(w) / period
    sd = (sum((x - mid) ** 2 for x in w) / period) ** 0.5
    return mid, mid + k * sd, mid - k * sd


def vwap(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], volumes: Sequence[float], period: int = 24) -> float | None:
    """Rolling volume-weighted average of the typical price over the last `period` candles. None without volume."""
    n = min(period, len(closes), len(volumes))
    if n == 0:
        return None
    tp = [(highs[-i] + lows[-i] + closes[-i]) / 3 for i in range(1, n + 1)]
    vol = [volumes[-i] for i in range(1, n + 1)]
    total = sum(vol)
    return sum(p * v for p, v in zip(tp, vol)) / total if total > 0 else None


# ---------------------------------------------------------------- whole series (aligned with the input; None = not enough data yet)
def rsi_series(closes: Sequence[float], period: int = 14) -> list[float | None]:
    """Wilder's RSI at every point (the same maths as rsi(), which is its last value)."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for a, b in zip(closes[:period], closes[1:period + 1]):
        gains += max(b - a, 0.0)
        losses += max(a - b, 0.0)
    avg_g, avg_l = gains / period, losses / period
    value = lambda g, lo: (100.0 if g > 0 else 50.0) if lo == 0 else 100 - 100 / (1 + g / lo)  # noqa: E731
    out[period] = value(avg_g, avg_l)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0.0)) / period
        out[i] = value(avg_g, avg_l)
    return out


def macd_series(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[list[float | None], list[float | None]]:
    """(macd line, signal line) at every point (macd()'s last values)."""
    n = len(closes)
    line: list[float | None] = [None] * n
    sig: list[float | None] = [None] * n
    if n < slow + signal:
        return line, sig
    f, s = ema(closes, fast), ema(closes, slow)
    raw = [a - b for a, b in zip(f[slow - 1:], s[slow - 1:])]
    se = ema(raw, signal)
    for j, v in enumerate(raw):
        line[slow - 1 + j] = v
        if j >= signal - 1:
            sig[slow - 1 + j] = se[j]
    return line, sig


def bollinger_series(closes: Sequence[float], period: int = 20, k: float = 2.0) -> list[tuple[float, float, float] | None]:
    """bollinger() at every point."""
    return [bollinger(closes[:i + 1], period, k) if i >= period - 1 else None for i in range(len(closes))]


# --- the three main indicators every signal, chart and /signal answer shows ------------------------------------------------

MAIN_MIN_CANDLES = 60  # EMA 50 + a few bars to compare


def main_three(closes: Sequence[float]) -> dict[str, float] | None:
    """RSI(14), MACD(12,26,9) with the previous histogram, EMA 20/50 and the price, from closed candles. None if too short."""
    if len(closes) < MAIN_MIN_CANDLES:
        return None
    r, m, m_prev = rsi(closes), macd(closes), macd(closes[:-1])
    e20, e50 = ema(closes, 20), ema(closes, 50)
    if r is None or m is None or not e20 or not e50:
        return None
    return {"price": closes[-1], "rsi": round(r, 1), "macd": m[0], "macd_signal": m[1], "macd_hist": m[2],
            "macd_hist_prev": m_prev[2] if m_prev else m[2], "ema20": e20[-1], "ema50": e50[-1]}


def main_three_lines(d: dict[str, float] | None) -> list[str]:
    """The three readings in plain words (no advice): ['RSI(14) 58.2 · neutral', 'MACD bullish · momentum rising',
    'EMA 20/50 price above both · 20 over 50']. Empty when there's no data."""
    if not d:
        return []
    r = d["rsi"]
    zone = "overbought" if r >= 70 else "oversold" if r <= 30 else "strong" if r >= 60 else "weak" if r <= 40 else "neutral"
    hist, prev = d["macd_hist"], d["macd_hist_prev"]
    if prev <= 0 < hist:
        macd_word = "bullish cross"
    elif prev >= 0 > hist:
        macd_word = "bearish cross"
    else:
        macd_word = "bullish" if hist > 0 else "bearish"
    if hist == prev:
        momentum = "momentum flat"
    elif hist >= 0:
        momentum = "momentum rising" if hist > prev else "momentum fading"
    else:  # below zero: a rising histogram means selling pressure easing
        momentum = "momentum improving" if hist > prev else "momentum falling"
    p, e20, e50 = d["price"], d["ema20"], d["ema50"]
    where = "price above both" if p > max(e20, e50) else "price below both" if p < min(e20, e50) else "price between them"
    stack = "20 over 50" if e20 > e50 else "20 under 50"
    return [f"RSI(14) {r:.1f} · {zone}", f"MACD {macd_word} · {momentum}", f"EMA 20/50 {where} · {stack}"]
