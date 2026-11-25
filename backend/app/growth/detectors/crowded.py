"""Format 4, crowded trades: funding extremes across Hyperliquid perps, negative-funding streaks, open-interest build-ups vs 30 days ago,
(liquidation imbalances are left out: their feeds are not cleared). Always phrased as "has a history of... / doesn't always", never as advice or a forecast."""

from __future__ import annotations

from datetime import date, timedelta

from app.growth import fmt
from app.growth.detectors.common import hl_coins, name, percentile_rank, price
from app.growth.findings import Finding, chart, n, pts
from app.growth.memory import Memory

ANNUAL = 24 * 365 * 100  # hourly rate -> annualised %
EXTREME_NEG = -30.0  # annualised %, shorts paying longs
EXTREME_POS = 50.0
LIST_MAX = 5


def _ann(rate: float) -> float:
    return rate * ANNUAL


def funding_extremes(mem: Memory, as_of: date) -> list[Finding]:
    rows = []
    for k in mem.keys("fund:"):
        coin = k[5:]
        s = mem.get(k)
        if not s or s.last < as_of:
            continue
        v = s.value(as_of)
        if v is None:
            continue
        hist = [x for d, x in zip(s.dates, s.values) if d <= as_of]
        rows.append((coin, _ann(v), percentile_rank(hist, v) if len(hist) >= 60 else None, len(hist)))
    if len(rows) < 10:
        return []
    out = []
    for neg in (True, False):
        pick = sorted((r for r in rows if (r[1] <= EXTREME_NEG if neg else r[1] >= EXTREME_POS)), key=lambda r: r[1] if neg else -r[1])[:LIST_MAX]
        if len(pick) < 2:
            continue
        lst = " · ".join(f"{c} {fmt.pct(a, dp=0)}" for c, a, _, _ in pick)
        top = pick[0]
        own = top[2]
        tail = (own if neg else 1 - own) if own is not None else None
        nums = {"list": n(0, lst, "annualised funding, most extreme"), "date": n(0, fmt.day(as_of), "funding day"),
                "count": n(len(pick), str(len(pick)), "perps listed"), "total": n(len(rows), fmt.num(len(rows)), "perps with funding history")}
        if neg:
            templates = ["On Hyperliquid, shorts are paying longs to stay bearish:\n{list}\n(funding: the {date} average hourly rate, annualised)\n"
                         "Crowded shorts at extreme funding have a history of sharp squeezes. They don't always come.",
                         "Most negative funding on Hyperliquid ({date} average hourly rate, annualised):\n{list}\nShorts are paying to stay short. "
                         "That has a history of ending in squeezes, but not every time."]
        else:
            templates = ["On Hyperliquid, longs are paying up to stay long:\n{list}\n(funding: the {date} average hourly rate, annualised)\n"
                         "Crowded longs at extreme funding have a history of sharp flushes. They don't always come.",
                         "Highest funding on Hyperliquid ({date} average hourly rate, annualised):\n{list}\nLongs are paying to stay long. "
                         "That has a history of ending in flushes, but not every time."]
        calc = (f"annualised = daily mean of the hourly funding rate × 24 × 365; {as_of.isoformat()}: "
                + "; ".join(f"{c} {a / ANNUAL:.6%}/h -> {fmt.pct(a)}" for c, a, _, _ in pick)
                + f" (of {len(rows)} perps with funding stored)" + (f"; {top[0]}'s reading is in the most extreme {tail * 100:.1f}% of its "
                                                                    f"{top[3]} stored days" if tail is not None else ""))
        mag = abs(top[1])
        rarity = min(1.0, 0.35 + mag / 250 + (0.3 * (1 - min(1.0, tail * 5)) if tail is not None else 0))
        out.append(Finding(
            key=f"funding:{'neg' if neg else 'pos'}:{as_of.isoformat()}", format="crowded", detector="funding_extremes",
            subject="HL-funding-" + ("neg" if neg else "pos"), headline=("Shorts paying longs: " if neg else "Longs paying shorts: ") + lst,
            numbers=nums, words={}, templates=templates, window="1d", series=[f"fund:{c}" for c, *_ in pick], sources=["hyperliquid"], as_of=as_of,
            calculation=calc, rarity=rarity, comparatives=["most", "highest", "extreme"],
            chart=chart("rank_table", "Hyperliquid funding, annualised", f"{as_of.isoformat()} average hourly rate × 8,760",
                        [{"name": "annualised funding, %", "points": pts([(c, a) for c, a, _, _ in pick], 1)}], sources=["hyperliquid"], as_of=as_of,
                        x_kind="label")))
    return out


def _streak(dates: list[date], values: list[float], end: int) -> int:
    k = 0
    i = end
    while i >= 0 and values[i] < 0 and (i == end or (dates[i + 1] - dates[i]).days == 1):
        k += 1
        i -= 1
    return k


def funding_streak(mem: Memory, as_of: date, coins: tuple[str, ...] = ("BTC", "ETH", "SOL")) -> list[Finding]:
    out = []
    for coin in coins:
        s = mem.get(f"fund:{coin}")
        if not s:
            continue
        s = s.upto(as_of)
        if len(s) < 90 or s.last != as_of:
            continue
        cur = _streak(s.dates, s.values, len(s) - 1)
        if cur < 4:
            continue
        longest = 0
        longest_end: date | None = None
        i = len(s) - 1 - cur
        while i >= 0:
            k = _streak(s.dates, s.values, i)
            if k > longest:
                longest, longest_end = k, s.dates[i]
            i -= max(1, k)
        nums = {"streak": n(cur, str(cur), "days in a row with negative average funding"), "since": n(0, fmt.month_year(s.first), "funding history starts")}
        if cur > longest:
            claim = "That's the longest run in Hyperliquid's funding data since {since}."
            comps = ["longest", "since"]
        else:
            if cur < 7:
                continue
            nums["longest"] = n(longest, str(longest), "longest earlier run, days")
            nums["longest_end"] = n(0, fmt.month_year(longest_end), "longest run ended") if longest_end else n(0, "", "")
            claim = "The longest run in Hyperliquid's funding data since {since} was {longest} days (ended {longest_end})."
            comps = ["longest", "since"]
        a = name(coin)
        templates = [a + " funding on Hyperliquid has been negative for {streak} days in a row: shorts are paying longs.\n" + claim
                     + "\nCrowded shorts have a history of getting squeezed. It doesn't always happen.",
                     "{streak} straight days of negative " + a + " funding on Hyperliquid. " + claim]
        look = max(0, len(s) - 180)
        out.append(Finding(
            key=f"fundstreak:{coin}:{as_of.isoformat()}", format="crowded", detector="funding_streak", subject=coin,
            headline=f"{a} funding negative {cur} days running (longest earlier run in data: {longest})", numbers=nums, words={},
            templates=templates, window=f"{cur}d", series=[s.key], sources=["hyperliquid"], as_of=as_of,
            calculation=f"daily mean hourly funding < 0 on each of the last {cur} days to {as_of.isoformat()}; longest earlier run since "
                        f"{s.first.isoformat()}: {longest} days" + (f" ending {longest_end.isoformat()}" if longest_end else "") + f" ({s.key})",
            rarity=min(1.0, 0.4 + cur / max(longest, 1) * 0.3 + (0.2 if cur > longest else 0)), comparatives=comps,
            chart=chart("rolling_line", f"{a} funding on Hyperliquid, annualised", f"{cur} days negative",
                        [{"name": "annualised funding, %", "points": pts([(d, _ann(v)) for d, v in zip(s.dates[look:], s.values[look:])], 2)}],
                        annotations=[{"type": "hline", "y": 0}, {"type": "span", "x0": s.dates[len(s) - cur].isoformat(), "x1": as_of.isoformat(),
                                                                "label": f"{cur} days"}], sources=["hyperliquid"], as_of=as_of)))
    return out


def oi_buildup(mem: Memory, as_of: date, days: int = 30) -> list[Finding]:
    out = []
    for k in mem.keys("oi:"):
        coin = k[3:]
        s = mem.get(k)
        px = price(mem, coin)
        if not s or not px:
            continue
        a, b = s.value(as_of - timedelta(days=days)), s.value(as_of)
        p0, p1 = px.value(as_of - timedelta(days=days)), px.value(as_of)
        if not (a and b and p0 and p1) or s.dates[s.index_on_or_before(as_of - timedelta(days=days))] != as_of - timedelta(days=days):  # type: ignore[index]
            continue
        oi_ch, px_ch = (b / a - 1) * 100, (p1 / p0 - 1) * 100
        if oi_ch < 40 or px_ch > 5:
            continue  # leverage building while the price goes nowhere or down
        nm = name(coin)
        nums = {"oi_ch": n(oi_ch, fmt.pct(oi_ch), f"open interest, {days}d"), "px_ch": n(px_ch, fmt.pct(px_ch), f"price, {days}d"),
                "oi": n(b, fmt.money(b), "open interest now"), "days": n(days, str(days), "window, days")}
        templates = ["Open interest in " + nm + " perps on Hyperliquid: {oi_ch} in {days} days, to {oi}. The price: {px_ch}.\n"
                     "Leverage building into a flat or falling price has a history of ending in forced selling. It doesn't always.",
                     nm + " on Hyperliquid, past {days} days: open interest {oi_ch}, price {px_ch}. More leverage, no progress."]
        out.append(Finding(
            key=f"oi:{coin}:{days}d:{as_of.isoformat()}", format="crowded", detector="oi_buildup", subject=coin,
            headline=f"{nm} OI {fmt.pct(oi_ch)} in {days}d while price {fmt.pct(px_ch)}", numbers=nums, words={}, templates=templates,
            window=f"{days}d", series=[s.key, px.key], sources=["derived_hl", px.source], as_of=as_of,
            calculation=f"OI {fmt.raw(a, 0)} -> {fmt.raw(b, 0)} USD = {fmt.pct(oi_ch)}; close {fmt.raw(p0, 4)} -> {fmt.raw(p1, 4)} = {fmt.pct(px_ch)} "
                        f"({(as_of - timedelta(days=days)).isoformat()} to {as_of.isoformat()}; OI = the day's last 15-minute snapshot)",
            rarity=min(1.0, 0.3 + oi_ch / 200), comparatives=[],
            chart=chart("spread_lines", f"{nm}: open interest vs price, rebased", f"OI {fmt.pct(oi_ch)} · price {fmt.pct(px_ch)}",
                        [{"name": "open interest", "points": pts([(d, v / a * 100) for d, v in zip(s.dates, s.values) if d >= as_of - timedelta(days=days)], 2)},
                         {"name": "price", "points": pts([(d, v / p0 * 100) for d, v in zip(px.dates, px.values) if as_of - timedelta(days=days) <= d <= as_of], 2)}],
                        sources=["derived_hl", px.source], as_of=as_of)))
    return sorted(out, key=lambda f: -f.rarity)[:3]


# Liquidations (ts_liquidations -> liq:*) come from OKX / Binance / Bybit feeds, which are NOT cleared for published findings
# (app/growth/sources.py): no detector reads liq:* series.


__all__ = ["funding_extremes", "funding_streak", "oi_buildup", "hl_coins"]
