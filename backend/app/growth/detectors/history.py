"""Format 2, historical comparisons: bear-market length vs prior cycles (days since the record close), this calendar month vs the same month
in every stored year, and then-vs-now one year apart. Always history, never a forecast."""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from app.growth import fmt
from app.growth.detectors.common import data_label, name, price
from app.growth.findings import Finding, chart, n, pts
from app.growth.memory import Memory, Series

CYCLE_DEPTH = -50.0  # a prior "bear market": a record close followed by at least a 50% fall before a new record
MIN_CYCLE_YEARS = 6


def cycles(s: Series) -> tuple[list[dict], dict | None]:
    """(completed cycles, the current one). A cycle: record close (top) -> lowest close before the next record (bottom), with depth <= -50%.
    A top on the very first stored days isn't a real record (history starts there) and is skipped."""
    done: list[dict] = []
    top_i = 0
    low_i = 0
    for i in range(1, len(s)):
        v = s.values[i]
        if v > s.values[top_i]:
            depth = (s.values[low_i] / s.values[top_i] - 1) * 100
            if depth <= CYCLE_DEPTH and top_i > 30:
                done.append({"top": s.dates[top_i], "top_v": s.values[top_i], "bottom": s.dates[low_i], "bottom_v": s.values[low_i],
                             "depth": depth, "days": (s.dates[low_i] - s.dates[top_i]).days, "top_i": top_i, "low_i": low_i, "end_i": i})
            top_i = low_i = i
        elif v < s.values[low_i]:
            low_i = i
    cur = {"top": s.dates[top_i], "top_v": s.values[top_i], "now_v": s.values[-1], "dd": (s.values[-1] / s.values[top_i] - 1) * 100,
           "days": (s.dates[-1] - s.dates[top_i]).days, "low_v": s.values[low_i], "low": s.dates[low_i], "top_i": top_i} if top_i > 30 else None
    return done, cur


def drawdown_cycle(mem: Memory, as_of: date, coins: tuple[str, ...] = ("BTC", "ETH")) -> list[Finding]:
    out = []
    for coin in coins:
        s = price(mem, coin)
        if not s:
            continue  # cycle claims need a long history from a cleared source (6+ years: none yet, see sources.py)
        s = s.upto(as_of)
        if s.last != as_of or s.years() < MIN_CYCLE_YEARS:
            continue
        done, cur = cycles(s)
        if not done or not cur or cur["dd"] > -20:
            continue
        last2 = done[-2:]
        nums = {"day": n(cur["days"], fmt.num(cur["days"]), "days since the record close"),
                "dd": n(cur["dd"], fmt.pct(cur["dd"], signed=False), "below the record close"),
                "ath": n(cur["top_v"], fmt.money(cur["top_v"]), "record close"), "ath_date": n(0, fmt.day(cur["top"]), "record close date")}
        for k, c in enumerate(last2, 1):
            nums[f"c{k}_days"] = n(c["days"], fmt.num(c["days"]), f"cycle {c['bottom'].year}: days top to bottom")
            nums[f"c{k}_year"] = n(c["bottom"].year, fmt.year(c["bottom"]), "bottom year")
            nums[f"c{k}_depth"] = n(c["depth"], fmt.pct(c["depth"], dp=0), f"cycle {c['bottom'].year}: depth")
        a = name(coin)
        if len(last2) == 2:
            templates = [a + "'s last two bear markets bottomed {c1_days} days ({c1_year}) and {c2_days} days ({c2_year}) after the record close.\n"
                         "This one is at day {day}, {dd} below the {ath} record close of {ath_date}.\nThose bottoms were {c1_depth} and {c2_depth}.",
                         "Days from record close to bottom, " + a + ":\n{c1_year}: {c1_days} days, {c1_depth}\n{c2_year}: {c2_days} days, {c2_depth}\n"
                         "Now: day {day}, {dd} below the {ath} record of {ath_date}."]
        else:
            templates = [a + "'s last bear market bottomed {c1_days} days after the record close ({c1_year}, {c1_depth}).\n"
                         "This one is at day {day}, {dd} below the {ath} record close of {ath_date}."]
        calc = "; ".join(f"cycle {c['top'].isoformat()} top {fmt.raw(c['top_v'], 2)} -> bottom {c['bottom'].isoformat()} {fmt.raw(c['bottom_v'], 2)} "
                         f"= {fmt.pct(c['depth'])} after {c['days']} days" for c in last2)
        calc += (f"; current: record close {fmt.raw(cur['top_v'], 2)} on {cur['top'].isoformat()}, close {fmt.raw(cur['now_v'], 2)} on {as_of.isoformat()} "
                 f"= {fmt.pct(cur['dd'])}, day {cur['days']} ({s.key}, cycles = record close then a fall of 50% or more before a new record)")
        series, anns = [], []

        def thin(idx: list[int], keep: set[int], top: date, top_v: float) -> list[list]:
            """Every other day (a lighter JSON), always keeping the bottom / today, so the marked points are plotted points."""
            sel = [i for k, i in enumerate(idx) if k % 2 == 0 or i in keep]
            return pts([((s.dates[i] - top).days, (s.values[i] / top_v - 1) * 100) for i in sel], 2)

        for k, c in enumerate(last2, 1):
            series.append({"name": f"{c['top'].year} top", "points": thin(list(range(c["top_i"], min(c["end_i"], c["low_i"] + 365))), {c["low_i"]},
                                                                          c["top"], c["top_v"]), "role": "context", "label": f"{c['bottom'].year} bottom"})
            anns.append({"type": "point_label", "x": c["days"], "series": k - 1, "label": "{c%d_depth}\nday {c%d_days}" % (k, k), "role": "context",
                         "number": f"c{k}_depth"})
        series.append({"name": "Now", "points": thin(list(range(cur["top_i"], len(s))), {len(s) - 1}, cur["top"], cur["top_v"]), "role": "neg",
                       "label": ""})
        anns.append({"type": "marker", "x": cur["days"], "series": len(series) - 1, "label": "{dd} below\nDay {day}, today", "role": "neg",
                     "number": "dd_signed", "align": "above"})
        nums["dd_signed"] = n(cur["dd"], fmt.pct(cur["dd"]), "below the record close (signed)")
        bottoms = " and ".join("day {c%d_days} ({c%d_year})" % (k, k) for k in range(1, len(last2) + 1))
        horizon = max([cur["days"]] + [c["days"] for c in last2]) + 60
        rarity = min(1.0, 0.55 + cur["days"] / max(c["days"] for c in last2) * 0.3)
        out.append(Finding(
            key=f"cycle:{coin}:{as_of.isoformat()}", format="history", detector="drawdown_cycle", subject=coin,
            headline=f"{a} day {cur['days']} of the drawdown ({fmt.pct(cur['dd'])}) vs prior bottoms at "
                     + ", ".join(f"{c['days']}d ({c['bottom'].year})" for c in last2),
            numbers=nums, words={}, templates=templates, window=f"since {cur['top'].isoformat()}", series=[s.key], sources=[s.source], as_of=as_of,
            calculation=calc, rarity=rarity, comparatives=["record", "last"],
            chart=chart("drawdown_overlay", f"{a}: drawdown from each record close", "", series, annotations=anns, sources=[s.source], as_of=as_of,
                        hero="Day {day}", hero_role="neg", headline="of " + a + "'s drawdown. Its last bear markets bottomed on " + bottoms,
                        dek=a + ", % below each record close, by days since that record · current record: {ath} on {ath_date}", y_format="pct",
                        options={"end_values": False, "x_max": min(horizon, max(c["days"] for c in last2) + 400)})))
    return out


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _prev_month_end(y: int, m: int) -> date:
    return date(y, m, 1) - timedelta(days=1)


def seasonality(mem: Memory, as_of: date, coins: tuple[str, ...] = ("BTC", "ETH")) -> list[Finding]:
    out = []
    for coin in coins:
        s = price(mem, coin)
        if not s:
            continue
        s = s.upto(as_of)
        if s.last != as_of or as_of.day < 8:
            continue  # a week into the month at least
        base = s.value(_prev_month_end(as_of.year, as_of.month))
        if not base:
            continue
        mtd = (s.values[-1] / base - 1) * 100
        years = []
        for y in range(s.first.year, as_of.year):
            a, b = s.value(_prev_month_end(y, as_of.month)), s.value(_month_end(y, as_of.month))
            if a and b and s.dates[s.index_on_or_before(_prev_month_end(y, as_of.month))] == _prev_month_end(y, as_of.month):  # type: ignore[index]
                years.append((y, (b / a - 1) * 100))
        if len(years) < 3:
            continue
        rets = [r for _, r in years]
        mon = fmt.month(as_of)
        worse = [y for y, r in years if r <= mtd]
        better = [y for y, r in years if r >= mtd]
        if not worse or as_of.year - worse[-1] >= 4:
            word, ref = "worst", worse
        elif not better or as_of.year - better[-1] >= 4:
            word, ref = "best", better
        else:
            continue  # not an extreme month (a worse / better one in the last 3 years): no claim
        days_left = calendar.monthrange(as_of.year, as_of.month)[1] - as_of.day
        lst = " · ".join(f"{y} {fmt.pct(r, dp=0)}" for y, r in years[-9:])
        nums = {"list": n(0, lst, "full-month returns by year"), "mtd": n(mtd, fmt.pct(mtd, dp=0), "month to date"),
                "year": n(as_of.year, str(as_of.year), "this year"), "left": n(days_left, str(days_left), "days left in the month")}
        if ref:
            nums["since"] = n(ref[-1], str(ref[-1]), f"last {mon} this {'bad' if word == 'worst' else 'good'}")
            claim = "the " + word + " " + mon + " since {since} in " + data_label(s)
            calc_claim = f"the most recent year with a {mon} {'≤' if word == 'worst' else '≥'} it is {ref[-1]}"
        else:
            nums["start"] = n(years[0][0], str(years[0][0]), "first stored year")
            claim = "the " + word + " " + mon + " in " + data_label(s) + " since {start}"
            calc_claim = f"no stored {mon} since {years[0][0]} was {'lower' if word == 'worst' else 'higher'}"
        a = name(coin)
        templates = [a + " in " + mon + ":\n{list}\n{year} so far: {mtd}, " + claim + ", with {left} days left.",
                     "{year} so far: " + a + " {mtd} in " + mon + ", " + claim + " ({left} days to go).\nPast years: {list}"]
        calc = (f"MTD = close {fmt.raw(s.values[-1], 2)} on {as_of.isoformat()} / close {fmt.raw(base, 2)} on "
                f"{_prev_month_end(as_of.year, as_of.month).isoformat()} − 1 = {fmt.pct(mtd)}; each year's {mon} = last close of {mon} / last close of "
                f"the month before − 1 ({len(years)} years from {years[0][0]}); {calc_claim} ({s.key})")
        gap_years = as_of.year - ref[-1] if ref else len(years)
        out.append(Finding(
            key=f"season:{coin}:{as_of.year}-{as_of.month:02d}:{word}:{as_of.isoformat()}", format="history", detector="seasonality", subject=coin,
            headline=f"{a} {mon} {as_of.year} so far {fmt.pct(mtd)}: {word} {mon} " + (f"since {ref[-1]}" if ref else "in the stored history"),
            numbers=nums, words={}, templates=templates, window=f"{mon} {as_of.year}", series=[s.key], sources=[s.source], as_of=as_of,
            calculation=calc, rarity=min(1.0, 0.45 + 0.08 * gap_years), comparatives=[word, "since"],
            chart=chart("seasonality_bars", f"{a} in {mon}, by year", "",
                        [{"name": f"{mon} return, %", "points": pts([(str(y), r) for y, r in years] + [(str(as_of.year), mtd)], 2)}],
                        sources=[s.source], as_of=as_of, hero="mtd", hero_role="neg" if mtd < 0 else "pos",
                        headline=a + " in {year} so far: " + claim.replace("the ", "its ", 1),
                        dek=a + "'s " + mon + " return by year: last close of " + mon + " vs last close of the month before · {year} so far, {left} days left",
                        y_format="pct", options={"partial": [str(as_of.year)], "partial_note": "so far", "digits": 0})))
    return out


def then_now(mem: Memory, as_of: date) -> list[Finding]:
    s = price(mem, "BTC")
    f = mem.get("fng")
    if not s or not f:
        return []
    s = s.upto(as_of)
    then = as_of.replace(year=as_of.year - 1) if not (as_of.month == 2 and as_of.day == 29) else as_of - timedelta(days=365)
    p0, p1, f0, f1 = s.value(then), s.value(as_of), f.value(then), f.value(as_of, 1)
    if not (p0 and p1 and f0 is not None and f1 is not None):
        return []
    ch = (p1 / p0 - 1) * 100
    if abs(ch) < 25 and abs(f1 - f0) < 30:
        return []
    nums = {"p0": n(p0, fmt.money(p0), "Bitcoin a year ago"), "p1": n(p1, fmt.money(p1), "Bitcoin now"), "f0": n(f0, fmt.num(f0), "F&G a year ago"),
            "f1": n(f1, fmt.num(f1), "F&G now"), "ch": n(ch, fmt.pct(ch), "12-month change"), "then": n(0, fmt.day(then), "a year ago"),
            "date": n(0, fmt.day(as_of), "as of")}
    templates = ["One year apart:\n{then}: Bitcoin {p0}, Fear & Greed {f0}\n{date}: Bitcoin {p1}, Fear & Greed {f1}\nThat's {ch} for the price.",
                 "A year ago today Bitcoin closed at {p0} and Fear & Greed read {f0}.\nNow: {p1} ({ch}) and {f1}."]
    look = max(0, s.index_on_or_before(then) or 0)
    return [Finding(
        key=f"thennow:BTC:{as_of.isoformat()}", format="history", detector="then_now", subject="BTC",
        headline=f"Bitcoin {fmt.money(p0)} -> {fmt.money(p1)} ({fmt.pct(ch)}), F&G {f0:.0f} -> {f1:.0f} in a year", numbers=nums, words={},
        templates=templates, window="1y", series=[s.key, "fng"], sources=[s.source, "alternative_me"], as_of=as_of,
        calculation=f"close {fmt.raw(p0, 2)} on {then.isoformat()} -> {fmt.raw(p1, 2)} on {as_of.isoformat()} = {fmt.pct(ch)}; F&G {f0:.0f} -> {f1:.0f} ({s.key}, fng)",
        rarity=min(0.7, 0.3 + abs(ch) / 150 + abs(f1 - f0) / 250), comparatives=[],
        chart=_then_now_chart(s, f, look, then, as_of, ch))]


def _then_now_chart(s: Series, f: Series, look: int, then: date, as_of: date, ch: float) -> dict:
    """The year of Bitcoin closes with both ends marked (price and Fear & Greed in the labels); variant: the year of Fear & Greed."""
    fi = f.index_on_or_before(as_of, 1)
    fu = f.upto(f.dates[fi])  # type: ignore[index]
    f_pts = [(d, v) for d, v in zip(fu.dates, fu.values) if d >= then]
    role = "neg" if ch < 0 else "pos"
    return chart("series_with_marker", "One year apart", "",
                 [{"name": "Bitcoin close, USD", "points": pts(list(zip(s.dates[look:], s.values[look:])), 2), "role": "context", "label": "", "width": 2.6}],
                 annotations=[{"type": "marker", "x": then.isoformat(), "label": "{p0}\n{then} · Fear & Greed {f0}", "role": "context", "number": "p0",
                               "align": "right"},
                              {"type": "marker", "x": as_of.isoformat(), "label": "{p1}\nToday · Fear & Greed {f1}", "role": role, "number": "p1"}],
                 sources=[s.source, "alternative_me"], as_of=as_of, hero="ch", hero_role=role,
                 headline="Bitcoin in a year: {p0} to {p1}, while Fear & Greed went from {f0} to {f1}",
                 dek="Bitcoin daily close in USD, {then} to {date} · Fear & Greed Index readings on both days", y_format="price",
                 options={"end_labels": False},
                 variants={"rolling_line": {
                     "series": [{"name": "Fear & Greed", "points": pts(f_pts, 0), "role": "context", "label": "", "width": 2.2}],
                     "annotations": [{"type": "marker", "x": f_pts[0][0].isoformat(), "label": "{f0}\n{then}", "role": "context", "number": "f0",
                                      "align": "right"},
                                     {"type": "marker", "x": f_pts[-1][0].isoformat(), "label": "{f1}\nToday", "role": role, "number": "f1"}],
                     "dek": "Crypto Fear & Greed Index, daily readings from {then} (0 = extreme fear, 100 = extreme greed)", "y_format": "index",
                     "options": {"end_labels": False, "y_min": 0, "y_max": 100}}})
