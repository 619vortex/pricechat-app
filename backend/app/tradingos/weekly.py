"""/markets weekly: the last 7 days in one message, from real data with its timestamps.

- prices, 7-day moves and ranges: Hyperliquid 1h candles (a rolling 168 hours) for the 30 most traded perpetuals
- total crypto market cap, 24h volume and BTC dominance: CoinGecko's /global
- funding: Hyperliquid's hourly funding history, this week's average vs the week before
- open interest: Price Chat's own 15-minute record (Hyperliquid keeps no OI history), only when it reaches back a week
Nothing is estimated or filled in: a section without data says so. No news headlines: no verified news feed is connected."""

import threading
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.tradingos import oi
from app.tradingos.market import MarketData, MarketError, fmt_price

COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"
TOP = 30
FUNDING_COINS = 12
MIN_VOLUME = 5_000_000  # 24h, so thin coins can't top the movers
CACHE_S = 600.0
_cache: dict[str, tuple[float, str]] = {}
_lock = threading.Lock()


def _usd(v: float) -> str:
    return f"${v / 1e12:.2f}T" if v >= 1e12 else f"${v / 1e9:.1f}B" if v >= 1e9 else f"${v / 1e6:.0f}M"


def _arrow(x: float) -> str:
    return f"▲ {x:.1f}%" if x > 0 else f"▼ {abs(x):.1f}%" if x < 0 else "0.0%"


def coingecko_global(http: httpx.Client | None = None) -> dict[str, Any] | None:
    try:
        c = http or httpx.Client(timeout=10.0)
        r = c.get(COINGECKO_GLOBAL, headers={"User-Agent": "PriceChat/1.0", "Accept": "application/json"})
        return (r.json() or {}).get("data") if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


def week_moves(md: MarketData, coins: list[str]) -> dict[str, dict[str, float]]:
    """coin -> {chg, hi, lo, vol_usd} over the last 168 hours (1h candles). A coin without enough candles is left out."""
    out = {}
    for c in coins:
        try:
            cs = md.candles(c, "1h", 170)
        except MarketError:
            continue
        if len(cs) < 160:
            continue
        w = cs[-168:]
        start = w[0].o
        out[c] = {"chg": (cs[-1].c - start) / start * 100 if start else 0.0, "hi": max(x.h for x in w), "lo": min(x.l for x in w),
                  "vol_usd": sum(x.v * x.c for x in w), "last": cs[-1].c}
    return out


def funding_weeks(md: MarketData, coins: list[str]) -> dict[str, tuple[float, float]]:
    """coin -> (average hourly funding this week, the week before), from Hyperliquid's funding history."""
    out = {}
    now_ms = time.time() * 1000
    for c in coins:
        try:
            rows = md.funding_history(c, 336)
        except MarketError:
            continue
        this = [r for t, r in rows if t >= now_ms - 168 * 3_600_000]
        prev = [r for t, r in rows if t < now_ms - 168 * 3_600_000]
        if len(this) >= 100 and len(prev) >= 100:
            out[c] = (sum(this) / len(this), sum(prev) / len(prev))
    return out


def build(md: MarketData, db: Session | None, http: httpx.Client | None = None, now: datetime | None = None) -> str:
    """The weekly overview (HTML for Telegram). Blocking: run it in a thread. Cached 10 minutes."""
    with _lock:
        hit = _cache.get("weekly")
        if hit and time.time() - hit[0] < CACHE_S and now is None:
            return hit[1]
    now = now or datetime.now(timezone.utc)
    _, ms = md.markets()
    liquid = sorted((m for m in ms.values() if m.mark > 0 and m.day_volume >= MIN_VOLUME), key=lambda m: -m.day_volume)[:TOP]
    names = [m.coin for m in liquid]
    for c in ("BTC", "ETH"):
        if c in ms and c not in names:
            names.insert(0, c)
    moves = week_moves(md, names)
    lines = [f"<b>📅 Weekly market summary</b>", f"<i>7 days to {now:%a %d %b %H:%M} UTC</i>", ""]

    for c in ("BTC", "ETH"):
        mv = moves.get(c)
        if mv:
            lines.append(f"<b>{c}</b>  {fmt_price(mv['last'])}  {_arrow(mv['chg'])} 7d · range {fmt_price(mv['lo'])} – {fmt_price(mv['hi'])}")
        else:
            lines.append(f"<b>{c}</b>  7-day data unavailable from Hyperliquid right now")

    g = coingecko_global(http)
    if g:
        cap = (g.get("total_market_cap") or {}).get("usd")
        vol = (g.get("total_volume") or {}).get("usd")
        ch = g.get("market_cap_change_percentage_24h_usd")
        dom = (g.get("market_cap_percentage") or {}).get("btc")
        at = datetime.fromtimestamp(g.get("updated_at") or now.timestamp(), tz=timezone.utc)
        parts = []
        if cap:
            parts.append(f"Total market cap {_usd(cap)}" + (f" ({ch:+.1f}% 24h)" if ch is not None else ""))
        if vol and cap and vol <= cap:  # a day's volume above the whole market cap is a bad figure (CoinGecko has sent 17x)
            parts.append(f"24h volume {_usd(vol)}")
        elif vol:
            parts.append("24h volume left out (CoinGecko's figure failed a sanity check)")
        if dom:
            parts.append(f"BTC dominance {dom:.1f}%")
        if parts:
            lines += ["", "<b>Whole market</b>  " + " · ".join(parts) + f"  <i>(CoinGecko, {at:%H:%M} UTC)</i>"]
    else:
        lines += ["", "<b>Whole market</b>  CoinGecko didn't answer, so total market cap and volume are left out."]

    ranked = sorted(((c, mv) for c, mv in moves.items() if c not in ("BTC", "ETH")), key=lambda x: x[1]["chg"])
    up = [x for x in reversed(ranked) if x[1]["chg"] > 0][:4]
    down = [x for x in ranked if x[1]["chg"] < 0][:4]
    if up or down:
        lines.append("")
    if up:
        lines.append("<b>Weekly gainers</b>  " + " · ".join(f"{c} {_arrow(mv['chg'])}" for c, mv in up))
    if down:
        lines.append("<b>Weekly losers</b>  " + " · ".join(f"{c} {_arrow(mv['chg'])}" for c, mv in down))
    hl_vol = sum(mv["vol_usd"] for mv in moves.values())
    if moves:
        top_vol = sorted(moves.items(), key=lambda x: -x[1]["vol_usd"])[:3]
        lines.append(f"<b>Hyperliquid 7d volume</b>  {_usd(hl_vol)} across {len(moves)} top perps · most traded "
                     + ", ".join(f"{c} {_usd(mv['vol_usd'])}" for c, mv in top_vol))

    fw = funding_weeks(md, names[:FUNDING_COINS])
    if fw:
        lines += ["", "<b>Funding</b> (average per hour, this week vs last)"]
        for c in ("BTC", "ETH"):
            if c in fw:
                a, b = fw[c]
                lines.append(f"{c} {a * 100:+.4f}% vs {b * 100:+.4f}%" + (" · longs paying" if a > 0 else " · shorts paying" if a < 0 else ""))
        others = sorted(((c, a, b) for c, (a, b) in fw.items() if c not in ("BTC", "ETH")), key=lambda x: -abs(x[1] - x[2]))[:2]
        for c, a, b in others:
            lines.append(f"Biggest shift: {c} {b * 100:+.4f}% → {a * 100:+.4f}%")
    else:
        lines += ["", "<b>Funding</b>  Hyperliquid funding history unavailable right now."]

    oi_lines = []
    for c in names[:10]:
        ch = oi.change(db, c, 7) if db is not None else None
        if ch:
            oi_lines.append((c, ch))
    if oi_lines:
        oi_lines.sort(key=lambda x: -abs(x[1][2]))
        lines += ["", "<b>Open interest 7d</b>  " + " · ".join(f"{c} {_usd(v[1])} ({v[2]:+.1f}%)" for c, v in oi_lines[:4])]
    else:
        btc = ms.get("BTC")
        lines += ["", "<b>Open interest</b>  " + (f"BTC {_usd(btc.oi_usd)} now. " if btc else "")
                  + "Weekly change not available yet: Hyperliquid keeps no OI history and Price Chat's own record needs a full week."]

    if moves:
        breadth = sum(1 for mv in moves.values() if mv["chg"] > 0)
        btc = moves.get("BTC", {}).get("chg")
        alts = [mv["chg"] for c, mv in moves.items() if c not in ("BTC", "ETH")]
        med = sorted(alts)[len(alts) // 2] if alts else None
        trend = [f"{breadth} of {len(moves)} top perps are up on the week"]
        if btc is not None and med is not None:
            trend.append("alts beat BTC" if med > btc else "BTC beat the typical alt")
            trend[-1] += f" (median alt {med:+.1f}% vs BTC {btc:+.1f}%)"
        lines += ["", "<b>Trend</b>  " + " · ".join(trend)]
    lines += ["", "<i>Prices, volume and funding: Hyperliquid. Headlines aren't included (no verified news feed connected). "
              "Not financial advice.</i>"]
    text = "\n".join(lines)
    with _lock:
        _cache["weekly"] = (time.time(), text)
    return text
