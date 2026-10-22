"""Market-structure visuals for trading stories: the chart that explains the story, never a generic candlestick by default.

  liquidation event      -> reported liquidations (OKX) under the price
  aggressive buying/sell -> CVD (taker buys minus sells, Binance + OKX perps) under the price
  derivatives positioning-> aggregated open interest (Binance + Bybit + OKX history; current total incl. Hyperliquid)
  a big liquidity wall   -> order book depth with the walls marked
  algorithmic execution  -> TWAP-like slices (inferred) on the price

Every number comes from app/tradingos/structure.py (public exchange feeds). Each chart carries its basis (observed /
aggregated / estimate / inferred), venues, units and its UTC window; the facts it adds to the brief say the same in words,
so the script can only use numbers the fact checker can find. Nothing is drawn when the data isn't there."""

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from app.tradingos import structure as ms
from app.tradingos.market import MarketData, MarketError

logger = logging.getLogger(__name__)

RULES = (  # first match wins: the story's own words decide the visual
    ("liq", re.compile(r"liquidat|\brekt\b|wiped out|short squeeze|long squeeze|margin call", re.I)),
    ("twap", re.compile(r"\btwap\b|algorithmic|\balgo\b|time.sliced|sliced orders?", re.I)),
    ("depth", re.compile(r"order ?book|liquidity wall|\bwalls?\b|bid wall|ask wall|resting (bids|asks|orders)|\bdepth\b", re.I)),
    ("oi", re.compile(r"open interest|\bleverage|positioning|derivatives|futures|perps?\b|funding", re.I)),
    ("cvd", re.compile(r"buying pressure|selling pressure|aggressive|taker|order flow|\bcvd\b|sell-?off|dump|rall(y|ies|ied)|surge|plunge|"
                       r"\bup \d|\bdown \d", re.I)),
)
COINS = {"bitcoin": "BTC", "btc": "BTC", "ether": "ETH", "ethereum": "ETH", "eth": "ETH", "solana": "SOL", "sol": "SOL", "xrp": "XRP",
         "dogecoin": "DOGE", "doge": "DOGE", "bnb": "BNB", "cardano": "ADA", "hyperliquid": "HYPE", "hype": "HYPE", "sui": "SUI", "avalanche": "AVAX"}


def pick(text: str) -> str | None:
    for kind, rx in RULES:
        if rx.search(text or ""):
            return kind
    return None


def coin_of(brief: dict[str, Any]) -> str | None:
    c = (brief.get("coin") or {}).get("symbol")
    if c:
        return str(c).upper()
    words = re.findall(r"[A-Za-z]+", f"{brief.get('headline', '')} " + " ".join(f.get("text", "") for f in brief.get("facts") or [] if isinstance(f, dict)))
    for w in words:
        if w.lower() in COINS:
            return COINS[w.lower()]
    return None


def usd(v: float) -> str:
    a = abs(v)
    s = f"${a / 1e9:.1f} billion" if a >= 1e9 else f"${a / 1e6:.1f} million" if a >= 1e6 else f"${a / 1e3:,.0f}K" if a >= 1e3 else f"${a:,.0f}"
    return ("-" if v < 0 else "") + s.replace(".0 ", " ")


def _utc(ms_: float) -> str:
    d = datetime.fromtimestamp(ms_ / 1000, tz=timezone.utc)
    return f"{d:%B} {d.day}, {d:%H:%M} UTC"


def _price(md: MarketData, coin: str, tf: str = "1h", n: int = 48) -> list[list[float]]:
    try:
        return [[int(c.t / 1000), round(c.c, 6)] for c in md.candles(coin, tf, n)]
    except MarketError:
        return []


def _chart(kind: str, layer: dict[str, Any], title: str, source: str, points: list[list[float]], **extra: Any) -> dict[str, Any]:
    return {"kind": "market", "variant": kind, "title": title, "label": title, "source": source, "points": points, "prefix": "$", "suffix": "",
            "basis": layer["basis"], "basis_label": layer["basis_label"], "venues": layer["venues"], "unit": layer.get("unit", "USD"), **extra}


def build(kind: str, coin: str, md: MarketData, db: Any = None, vs: ms.Venues | None = None) -> tuple[dict[str, Any], list[dict[str, str]]] | None:
    """(chart, facts) for one visual, or None when the data isn't there (the story keeps its plain price chart)."""
    price = _price(md, coin)
    if kind == "cvd":
        c = ms.cvd(coin, "1h", 48, vs)
        if len(c["points"]) < 12:
            return None
        src = f"{' + '.join(c['venues'])} {coin} perpetuals · taker volume"
        when = f"between {_utc(c['start'])} and {_utc(c['end'])}"
        facts = [{"text": f"Taker buys minus taker sells on {' + '.join(c['venues'])} {coin} perpetuals {when}: {usd(c['delta_usd'])} "
                          f"({usd(c['buy_usd'])} bought, {usd(c['sell_usd'])} sold by aggressive orders).", "source": src},
                 {"text": "Cumulative volume delta adds up aggressive buying minus aggressive selling over time.", "source": "Price Chat"}]
        pts = [[int(t / 1000), round(v)] for t, v in c["points"]]
        return _chart("cvd", c, f"{coin} · aggressive buying minus selling", f"{src} · {c['basis_label']} · UTC", pts, price=price,
                      start=int(c["start"] / 1000), end=int(c["end"] / 1000)), facts
    if kind == "oi":
        o = ms.open_interest(coin, "1h", 48, vs)
        h = o["history"]
        if len(h) < 12:
            return None
        hv = " + ".join(o["history_venues"])
        ch = (h[-1][1] - h[0][1]) / h[0][1] * 100 if h[0][1] else 0.0
        facts = [{"text": f"{coin} perpetual open interest across {' + '.join(o['venues'])}: {usd(o['total_usd'])} at {_utc(o['fetched_at'])}.",
                  "source": "Exchange APIs (" + ", ".join(o["venues"]) + ")"},
                 {"text": f"Open interest on {hv} went from {usd(h[0][1])} to {usd(h[-1][1])} between {_utc(h[0][0])} and {_utc(h[-1][0])} "
                          f"({ch:+.1f}%).", "source": f"{hv} open-interest history"},
                 {"text": "Open interest is the total value of futures positions that are still open.", "source": "Price Chat"}]
        pts = [[int(t / 1000), round(v)] for t, v in h]
        return _chart("oi", o, f"{coin} open interest · {hv}", f"{hv} · {o['basis_label']} · USD · UTC", pts, price=price,
                      start=pts[0][0], end=pts[-1][0]), facts
    if kind == "liq":
        since = int(time.time() * 1000) - 24 * 3_600_000
        lq = ms.reported_liquidations(coin, since, db, vs)
        ev = lq["events"]
        if len(ev) < 5:
            return None
        t0, t1 = ev[0]["t"], ev[-1]["t"]
        w = max(300_000, (t1 - t0) // 36)
        buckets: dict[int, list[float]] = {}
        for e in ev:
            b = buckets.setdefault(int(t0 + (e["t"] - t0) // w * w), [0.0, 0.0])
            b[0 if e["side"] == "short" else 1] += e["usd"]
        bars = [[int(t / 1000), round(s), round(lg)] for t, (s, lg) in sorted(buckets.items())]
        when = f"between {_utc(t0)} and {_utc(t1)}"
        names = " + ".join(lq["venues"])
        facts = [{"text": f"{names} reported {usd(lq['long_usd'])} of {coin} long liquidations and {usd(lq['short_usd'])} of short liquidations {when}.",
                  "source": f"{names} public liquidation data"},
                 *({"text": f"{v} reported {usd(x['long_usd'])} of long and {usd(x['short_usd'])} of short liquidations in that time.",
                    "source": f"{v} public liquidation data"} for v, x in lq["by_venue"].items() if len(lq["by_venue"]) > 1),
                 {"text": f"Only {names} liquidations are counted; other exchanges' are not included.", "source": "Price Chat"}]
        pr = [p for p in _price(md, coin, "15m", 96) if t0 / 1000 - 900 <= p[0] <= t1 / 1000 + 900]
        return _chart("liq", lq, f"{coin} reported liquidations", f"{names} · {lq['basis_label']} · USD · UTC", pr or price, bars=bars,
                      start=int(t0 / 1000), end=int(t1 / 1000)), facts
    if kind == "depth":
        b = ms.books(coin, vs, md)
        if not b["walls"]:
            return None
        wl = b["walls"][0]
        rows = [[px, round(u), "bid" if px < b["mid"] else "ask"] for px, u in b["bins"] if abs(px - b["mid"]) / b["mid"] <= 0.012]
        facts = [{"text": f"Largest resting order in the {coin} perpetual books checked ({' + '.join(b['venues'])}) at {_utc(b['fetched_at'])}: "
                          f"{'an' if wl['venue'][0] in 'AEIOU' else 'a'} {wl['venue']} {wl['side']} of {usd(wl['usd'])} at ${wl['px']:,.0f}, {abs(wl['dist_pct']):.1f}% "
                          f"{'below' if wl['dist_pct'] < 0 else 'above'} the price.", "source": f"{wl['venue']} order book"}]
        return _chart("depth", b, f"{coin} order book · resting liquidity", f"{' + '.join(b['venues'])} · {b['basis_label']} · USD per ${b['step']:g}",
                      price, levels=rows, mid=b["mid"], walls=b["walls"][:3], at=int(b["fetched_at"] / 1000)), facts
    if kind == "twap":
        tw = ms.twap_patterns(coin, 60, vs)
        if not tw["patterns"]:
            return None
        p = tw["patterns"][0]
        who = " from one on-chain address" if p.get("address") else ""
        facts = [{"text": f"{p['venue']} {coin} perpetual trades between {_utc(p['start'])} and {_utc(p['end'])}: {p['prints']} {p['side']} orders{who} of "
                          f"about {p['slice_qty']:.3g} {coin} every {p['interval_s']:.0f} seconds, {usd(p['total_usd'])} in total.",
                  "source": f"{p['venue']} trades"},
                 {"text": "That pattern is consistent with time-sliced (TWAP-style) execution; who placed the orders is unknown.", "source": "Price Chat"}]
        pr = _price(md, coin, "1m", 70)
        return _chart("twap", tw, f"{coin} · regular {p['side']} slices (inferred)", f"{p['venue']} · {tw['basis_label']} · UTC", pr,
                      marks=[int(t / 1000) for t in p["times"]], side=p["side"], start=int(p["start"] / 1000), end=int(p["end"] / 1000)), facts
    return None


def attach(brief: dict[str, Any], md: MarketData, db: Any = None, kind: str | None = None) -> str | None:
    """Give a trading story the market-structure chart its words call for (and the facts behind it). Returns the visual used."""
    coin = coin_of(brief)
    text = f"{brief.get('headline', '')} " + " ".join(f.get("text", "") for f in brief.get("facts") or [] if isinstance(f, dict))
    kind = kind or pick(text)
    if not coin or not kind:
        return None
    try:
        md.market(coin)
        got = build(kind, coin, md, db)
    except (ms.StructureError, MarketError, KeyError, IndexError, TypeError, ValueError) as e:
        logger.info("market visual %s for %s skipped: %s", kind, coin, e)
        return None
    if not got:
        return None
    chart, facts = got
    brief["chart"] = chart
    brief["facts"] = [*(brief.get("facts") or []), *facts]
    brief["market_visual"] = kind
    return kind
