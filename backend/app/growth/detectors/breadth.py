"""Format 3, market breadth across the stored Hyperliquid universe: how many perps are up / down over 1, 7 and 30 days and how unusual
that is, and the share above their 200-day (50-day) average vs its own history."""

from __future__ import annotations

from datetime import date, timedelta

from app.growth import fmt
from app.growth.detectors.common import hl_coins, percentile_rank
from app.growth.findings import Finding, chart, n, pts
from app.growth.memory import Memory

MIN_UNIVERSE = 20
MIN_HISTORY_DAYS = 180


def _closes(mem: Memory) -> dict[str, dict[date, float]]:
    out = {}
    for c in hl_coins(mem):
        s = mem.get(f"px:{c}:close")
        if s:
            out[c] = dict(zip(s.dates, s.values))
    return out


def share_up_history(closes: dict[str, dict[date, float]], days: int, upto: date) -> list[tuple[date, float, int, int]]:
    """Per day: (day, share up %, up, total) over `days` calendar days, for days with at least MIN_UNIVERSE coins."""
    all_days = sorted({d for m in closes.values() for d in m if d <= upto})
    out = []
    for d in all_days:
        back = d - timedelta(days=days)
        up = tot = 0
        for m in closes.values():
            a, b = m.get(back), m.get(d)
            if a and b:
                tot += 1
                up += b > a
        if tot >= MIN_UNIVERSE:
            out.append((d, 100.0 * up / tot, up, tot))
    return out


def breadth_count(mem: Memory, as_of: date) -> list[Finding]:
    closes = _closes(mem)
    best: tuple[float, Finding] | None = None
    for days in (1, 7, 30):
        hist = share_up_history(closes, days, as_of)
        if len(hist) < MIN_HISTORY_DAYS or hist[-1][0] != as_of:
            continue
        _, share, up, tot = hist[-1]
        vals = [h[1] for h in hist]
        weak = share < 50
        tail = percentile_rank(vals, share) if weak else sum(1 for v in vals if v >= share) / len(vals)
        if tail > 0.15:
            continue
        down = tot - up
        coin_rets = sorted(((c, (m[as_of] / m[as_of - timedelta(days=days)] - 1) * 100) for c, m in closes.items()
                            if as_of in m and (as_of - timedelta(days=days)) in m), key=lambda x: x[1])
        since = hist[0][0]
        period = {1: "on {date}", 7: "over the past {days} days", 30: "over the past {days} days"}[days]
        nums = {"up": n(up, fmt.num(up), "perps up"), "down": n(down, fmt.num(down), "perps down"), "total": n(tot, fmt.num(tot), "perps counted"),
                "tail": n(tail * 100, fmt.pct(tail * 100, signed=False), "share of days with breadth this " + ("weak" if weak else "strong")),
                "since": n(0, fmt.month_year(since), "breadth history starts"), "days": n(days, str(days), "window, days"), "date": n(0, fmt.day(as_of), "as of")}
        adj = "weak" if weak else "strong"
        lead = "{up} of {total} Hyperliquid perps are up " + period + ". {down} are down." if days > 1 else \
            "{up} of {total} Hyperliquid perps closed up " + period + ". {down} closed down."
        templates = ["Crypto breadth: " + lead + "\nOnly {tail} of days since {since} had breadth this " + adj + ".",
                     lead + "\nIt isn't one coin, it's " + ("almost everything" if weak else "nearly the whole market") + ". Breadth this " + adj
                     + " showed up on just {tail} of days since {since}."]
        calc = (f"{up} of {tot} perps with a close on both {(as_of - timedelta(days=days)).isoformat()} and {as_of.isoformat()} closed higher "
                f"({share:.1f}%); {tail * 100:.1f}% of {len(hist):,} days since {since.isoformat()} had a share {'≤' if weak else '≥'} {share:.1f}% "
                f"(same universe: every stored Hyperliquid perp, px:*:close)")
        f = Finding(
            key=f"breadth:{days}d:{as_of.isoformat()}", format="breadth", detector="breadth_count", subject="HL-universe",
            headline=f"{up} of {tot} Hyperliquid perps up over {days}d ({share:.0f}%): {adj}er than {100 - tail * 100:.0f}% of days since {since.year}",
            numbers=nums, words={}, templates=templates, window=f"{days}d", series=["px:*:close"], sources=["hyperliquid"], as_of=as_of,
            calculation=calc, rarity=max(0.0, min(1.0, 1 - tail * 4)), comparatives=["only", "just", "almost", "nearly"],
            chart=chart("heat_grid", f"Hyperliquid perps, {days}-day change", f"{up} up · {down} down",
                        [{"name": f"{days}-day change, %", "points": pts(coin_rets, 2)}],
                        annotations=[{"type": "note", "text": f"{up} of {tot} up"}], sources=["hyperliquid"], as_of=as_of, x_kind="label"))
        if best is None or tail < best[0]:
            best = (tail, f)
    return [best[1]] if best else []


def breadth_ma(mem: Memory, as_of: date) -> list[Finding]:
    out = []
    for ma in (200, 50):
        s, cnt = mem.get(f"breadth:above{ma}"), mem.get(f"breadth:n{ma}")
        if not s or not cnt:
            continue
        s = s.upto(as_of)
        if len(s) < MIN_HISTORY_DAYS or s.last != as_of:
            continue
        v = s.values[-1]
        tot = int(cnt.value(as_of) or 0)
        if tot < MIN_UNIVERSE:
            continue
        p = percentile_rank(s.values, v)
        low = p <= 0.5
        tail = p if low else sum(1 for x in s.values if x >= v) / len(s)
        if tail > 0.08:
            continue
        j = next((i for i in range(len(s) - 2, -1, -1) if (s.values[i] <= v if low else s.values[i] >= v)), None)
        above = round(v * tot / 100)
        nums = {"share": n(v, fmt.pct(v, signed=False, dp=0), f"share above the {ma}-day average"), "count": n(above, fmt.num(above), "perps above"),
                "total": n(tot, fmt.num(tot), "perps with the lookback"), "ma": n(ma, str(ma), "average length, days")}
        word = "lowest" if low else "highest"
        if j is not None and (as_of - s.dates[j]).days >= 60:
            nums["since"] = n(0, fmt.month_year(s.dates[j]), "last reading this " + ("low" if low else "high"))
            claim = "The " + word + " reading since {since}."
            calc_claim = f"the most recent reading {'≤' if low else '≥'} it was {s.values[j]:.1f}% on {s.dates[j].isoformat()}"
        elif j is None:
            nums["since0"] = n(0, fmt.month_year(s.first), "breadth history starts")
            claim = "The " + word + " reading since our breadth record starts ({since0})."
            calc_claim = f"no earlier reading since {s.first.isoformat()} was {'lower' if low else 'higher'}"
        else:
            continue
        lead = ("Only " if low else "") + "{share} of Hyperliquid perps ({count} of {total}) are above their {ma}-day average."
        templates = [lead + "\n" + claim, "Hyperliquid perps above their {ma}-day average: {count} of {total} ({share}).\n" + claim]
        look = max(0, len(s) - 730)
        out.append(Finding(
            key=f"breadthma:{ma}:{word}:{as_of.isoformat()}", format="breadth", detector="breadth_ma", subject=f"HL-above{ma}",
            headline=f"{v:.0f}% of Hyperliquid perps above their {ma}d average: {word} since " + (s.dates[j].isoformat() if j is not None else "record start"),
            numbers=nums, words={}, templates=templates, window=f"{ma}d average", series=[s.key, cnt.key], sources=["derived_hl"], as_of=as_of,
            calculation=f"{above} of {tot} perps with {ma}+ days of closes are above their {ma}-day simple average ({v:.1f}%); {calc_claim}; "
                        f"{tail * 100:.1f}% of {len(s):,} days since {s.first.isoformat()} were this {'low' if low else 'high'}",
            rarity=max(0.0, min(1.0, 1 - tail * 5)), comparatives=[word, "only", "since"],
            chart=chart("rolling_line", f"Share of Hyperliquid perps above their {ma}-day average", f"{v:.0f}% now",
                        [{"name": f"% above {ma}d average", "points": pts(list(zip(s.dates[look:], s.values[look:])), 1)}],
                        annotations=[{"type": "marker", "x": as_of.isoformat(), "y": round(v, 1), "label": f"{v:.0f}%"}],
                        sources=["derived_hl"], as_of=as_of)))
        break  # one moving-average claim per run: 200 first
    return out
