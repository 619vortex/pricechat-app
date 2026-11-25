"""Format 1, market extremes: how rare an N-day move is in the asset's own history, the lowest / highest close since a date (never a claim
longer than the stored history), and Fear & Greed rarity."""

from __future__ import annotations

from datetime import date, timedelta

from app.growth import fmt
from app.growth.detectors.common import MAJORS, data_label, name, percentile_rank, price, whole_years
from app.growth.findings import Finding, chart, n, pts
from app.growth.memory import Memory

WINDOWS = (1, 7, 30, 90)
MIN_WINDOWS = 365  # a rarity percentile needs at least a year of windows
TAIL = 0.10  # report moves in the most extreme 10% of their own history
MIN_GAP_DAYS = 120  # "lowest since" is only news when "since" is at least 4 months back


def _histogram(values: list[float], bins: int = 40) -> list[tuple[float, float]]:
    lo, hi = min(values), max(values)
    if hi <= lo:
        return [(lo, float(len(values)))]
    w = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        counts[min(bins - 1, int((v - lo) / w))] += 1
    return [(round(lo + (i + 0.5) * w, 4), float(c)) for i, c in enumerate(counts)]


def rarity_move(mem: Memory, as_of: date, coins: tuple[str, ...] = MAJORS) -> list[Finding]:
    out = []
    for coin in coins:
        s = price(mem, coin)
        if not s:
            continue
        s = s.upto(as_of)
        if not s.dates or s.last != as_of:
            continue
        best: tuple[float, Finding] | None = None
        for days in WINDOWS:
            allw = s.window_returns(days)
            if len(allw) < MIN_WINDOWS or allw[-1][0] != as_of:
                continue
            _, r, v0, v1 = allw[-1]
            rets = [w[1] for w in allw]
            down = r < 0
            count = sum(1 for x in rets if (x <= r if down else x >= r))
            share = count / len(rets)
            if share > TAIL:
                continue
            prior = [w for w in allw if w[0] <= as_of - timedelta(days=days) and (w[1] <= r if down else w[1] >= r)]
            since = allw[0][0]
            adj = "bad" if down else "strong"
            nums = {"move": n(r, fmt.pct(r), f"{days}-day return"), "share": n(share * 100, fmt.pct(share * 100, signed=False), "share of windows"),
                    "count": n(count, fmt.num(count), "windows as extreme"), "total": n(len(rets), fmt.num(len(rets)), "windows in history"),
                    "since": n(0, fmt.month_year(since), "stored history starts"), "days": n(days, str(days), "window, days"),
                    "date": n(0, fmt.day(as_of), "as of")}
            words = {"asset": name(coin), "adj": adj, "dirword": "down" if down else "up", "data": data_label(s)}
            comparatives = ["only", "last time", "most", "first"]
            if prior:
                last = prior[-1][0]
                nums["last"] = n(0, fmt.month_year(last), "previous window as extreme ended")
                tail = "The last one ended in {last}."
                calc_last = f"; most recent earlier window: ended {last.isoformat()} ({fmt.pct(prior[-1][1])})"
            else:
                tail = "None earlier in {data} since {since}."
                comparatives.append("none earlier")
                calc_last = "; no earlier window was as extreme"
            if days == 1:
                templates = ["{asset} closed {move} on {date}.\nIn {data} since {since}, only {count} of {total} daily closes were this " + adj + " ({share}). " + tail,
                             "{asset}: {move} in one session ({date}).\nThat's in the most extreme {share} of daily moves in {data} since {since}. " + tail]
            else:
                templates = ["{asset} {move} in {days} days.\nIn {data} since {since}, only {share} of all {days}-day stretches were this " + adj + " ({count} of {total}). " + tail,
                             "{asset} is {dirword} {move_abs} over the past {days} days.\nOnly {count} of {total} {days}-day windows in {data} since {since} were this " + adj + ". " + tail]
                nums["move_abs"] = n(r, fmt.pct(abs(r), signed=False), f"{days}-day move, size (the value keeps its sign: the gate checks the direction)")
            calc = (f"{days}d return = {fmt.raw(v1, 2)} / {fmt.raw(v0, 2)} − 1 = {fmt.pct(r)}; {count:,} of {len(rets):,} {days}-day windows since "
                    f"{since.isoformat()} were {'≤' if down else '≥'} {fmt.pct(r)} ({fmt.pct(share * 100, signed=False, dp=1)}){calc_last}. Source series {s.key}.")
            f = Finding(
                key=f"rarity:{coin}:{days}d:{as_of.isoformat()}", format="extremes", detector="rarity", subject=coin,
                headline=f"{name(coin)} {fmt.pct(r)} in {days} day(s): most extreme {fmt.pct(share * 100, signed=False)} of {data_label(s)} since {since.isoformat()}",
                numbers=nums, words=words, templates=templates, window=f"{days}d", series=[s.key], sources=[s.source], as_of=as_of,
                calculation=calc, rarity=max(0.0, min(1.0, 1 - share * 5)), comparatives=comparatives,
                chart=chart("histogram_marker", f"Every {days}-day {name(coin)} move since {since.year}", f"{name(coin)} {fmt.pct(r)} in {days}d",
                            [{"name": f"{days}-day returns, %", "points": pts(_histogram(rets))}],
                            annotations=[{"type": "marker", "x": round(r, 2), "label": f"Now {fmt.pct(r)}"},
                                         {"type": "note", "text": f"{fmt.pct(share * 100, signed=False)} of windows this {adj}"}],
                            sources=[s.source], as_of=as_of, x_kind="number", x_label=f"{days}-day return, %"))
            if best is None or share < best[0]:
                best = (share, f)
        if best:
            out.append(best[1])
    return out


def record_level(mem: Memory, as_of: date, coins: tuple[str, ...] = MAJORS) -> list[Finding]:
    """Lowest / highest close since a date. When no earlier close was as low (high), the claim is bounded by the stored history: "in N
    years of daily data", N = whole years actually stored, never more."""
    out = []
    for coin in coins:
        s = price(mem, coin)
        if not s:
            continue
        s = s.upto(as_of)
        if len(s) < 400 or s.last != as_of:
            continue
        c = s.values[-1]
        for low in (True, False):
            j = next((i for i in range(len(s) - 2, -1, -1) if (s.values[i] <= c if low else s.values[i] >= c)), None)
            word = "lowest" if low else "highest"
            if j is None:
                yrs = whole_years(s.first, as_of)
                if yrs < 1:
                    continue
                gap = (as_of - s.first).days
                nums = {"close": n(c, fmt.money(c), "close"), "date": n(0, fmt.day(as_of), "as of"),
                        "start": n(0, fmt.month_year(s.first), "stored history starts")}
                templates = ["{asset} closed at {close} on {date}: its " + word + " close in {data} since {start}.",
                             "{asset} {close} ({date}). No daily close in {data} since {start} was " + ("lower" if low else "higher") + "."]
                calc = f"close {fmt.raw(c, 2)} on {as_of.isoformat()} is the {word} of {len(s):,} closes since {s.first.isoformat()} ({s.key})"
                comps = [word, "lower" if low else "higher"]
                since_txt = f"{yrs}y"
            else:
                since_d = s.dates[j]
                gap = (as_of - since_d).days
                if gap < MIN_GAP_DAYS:
                    continue
                nums = {"close": n(c, fmt.money(c), "close"), "date": n(0, fmt.day(as_of), "as of"),
                        "since": n(0, fmt.month_year(since_d), "last close this " + ("low" if low else "high")),
                        "then": n(s.values[j], fmt.money(s.values[j]), "that close")}
                templates = ["{asset} closed at {close} on {date}, its " + word + " close since {since}.",
                             "{asset} at {close}: the " + word + " daily close since {since} ({then} back then)."]
                calc = (f"close {fmt.raw(c, 2)} on {as_of.isoformat()}; the most recent earlier close {'≤' if low else '≥'} it was "
                        f"{fmt.raw(s.values[j], 2)} on {since_d.isoformat()} ({gap} days earlier) in {s.key}")
                comps = [word, "since"]
                since_txt = since_d.isoformat()
            yr = s.upto(as_of)
            one = yr.value(as_of - timedelta(days=365))
            if one:
                ch = (c / one - 1) * 100
                nums["y1"] = n(ch, fmt.pct(ch), "12-month change")
                templates[0] += " Past year: {y1}."
            look = max(0, len(s) - 730)
            out.append(Finding(
                key=f"record:{coin}:{word}:{as_of.isoformat()}", format="extremes", detector="record", subject=coin,
                headline=f"{name(coin)} {word} close since {since_txt}", numbers=nums, words={"asset": name(coin), "data": data_label(s)}, templates=templates,
                window=f"since {since_txt}", series=[s.key], sources=[s.source], as_of=as_of, calculation=calc,
                rarity=min(1.0, 0.45 + gap / 1460), comparatives=comps,
                chart=chart("series_with_marker", f"{name(coin)} daily close", f"{word.capitalize()} close since {since_txt}",
                            [{"name": f"{name(coin)} close, USD", "points": pts(list(zip(s.dates[look:], s.values[look:])), 2)}],
                            annotations=[{"type": "marker", "x": as_of.isoformat(), "y": round(c, 2), "label": fmt.money(c)}]
                            + ([{"type": "hline", "y": round(s.values[j], 2), "label": fmt.day(s.dates[j])}] if j is not None else []),
                            sources=[s.source], as_of=as_of)))
    return out


def sentiment_rarity(mem: Memory, as_of: date) -> list[Finding]:
    s = mem.get("fng")
    if not s:
        return []
    s = s.upto(as_of)
    if len(s) < MIN_WINDOWS or (as_of - s.last).days > 1:
        return []
    v = s.values[-1]
    low = v <= 30
    if not low and v < 70:
        return []
    count = sum(1 for x in s.values if (x <= v if low else x >= v))
    share = count / len(s)
    if share > 0.15:
        return []
    streak = 0
    for i in range(len(s) - 1, -1, -1):
        x = s.values[i]
        if not (x <= 25 if low else x >= 75) or (i < len(s) - 1 and (s.dates[i + 1] - s.dates[i]).days != 1):
            break
        streak += 1
    nums = {"value": n(v, fmt.num(v), "Fear & Greed"), "share": n(share * 100, fmt.pct(share * 100, signed=False), "share of days"),
            "count": n(count, fmt.num(count), "days this low" if low else "days this high"), "total": n(len(s), fmt.num(len(s)), "days stored"),
            "since": n(s.first.year, fmt.year(s.first), "index history starts"), "max": n(100, "100", "scale maximum")}
    adj = "low" if low else "high"
    templates = ["Crypto Fear & Greed: {value} out of {max}.\nSince {since}, the index has been this " + adj + " on just {share} of days ({count} of {total}).",
                 "Fear & Greed is at {value}. Only {count} of {total} daily readings since {since} were this " + adj + " ({share})."]
    if streak >= 5:
        nums["streak"] = n(streak, str(streak), "days in a row in extreme territory")
        templates[0] += "\nIt has been in extreme " + ("fear" if low else "greed") + " for {streak} days in a row."
    look = max(0, len(s) - 730)
    return [Finding(
        key=f"fng:{'low' if low else 'high'}:{as_of.isoformat()}", format="extremes", detector="sentiment", subject="FNG",
        headline=f"Fear & Greed {v:.0f}: this {adj} on {share * 100:.1f}% of days since {s.first.year}", numbers=nums, words={},
        templates=templates, window="daily", series=["fng"], sources=["alternative_me"], as_of=as_of,
        calculation=f"F&G {v:.0f} on {s.last.isoformat()}; {count:,} of {len(s):,} daily readings since {s.first.isoformat()} were "
                    f"{'≤' if low else '≥'} {v:.0f} ({share * 100:.2f}%)" + (f"; {streak} consecutive days {'≤ 25' if low else '≥ 75'}" if streak else ""),
        rarity=max(0.0, min(1.0, 1 - share * 4)), comparatives=["only", "just"],
        chart=chart("series_with_marker", "Crypto Fear & Greed", f"{v:.0f} today", [{"name": "Fear & Greed", "points": pts(list(zip(s.dates[look:], s.values[look:])), 0)}],
                    annotations=[{"type": "marker", "x": s.last.isoformat(), "y": v, "label": f"{v:.0f}"}, {"type": "band", "y0": 0, "y1": 25, "label": "extreme fear"}],
                    sources=["alternative_me"], as_of=as_of))]


__all__ = ["rarity_move", "record_level", "sentiment_rarity", "percentile_rank"]
