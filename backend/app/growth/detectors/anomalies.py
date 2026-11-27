"""G2 market anomalies (format "extremes"): a run of daily losses / gains rare in the asset's own history, a 30-day volatility regime at
the edge of its own history, and Hyperliquid volume far above each perp's own 30-day average.

Pure functions (Memory, as_of) -> list[Finding], cleared Hyperliquid series only (common.price / px:<coin>:vol). Every claim names the
stored window ("in Hyperliquid data since March 2023"); nothing is claimed beyond it. Plausibility guards: a window with a gap of more than
3 days, or a daily move of more than 60% for a major, gives no finding (bad data is never news).
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta

from app.growth import fmt
from app.growth.detectors.common import MAJORS, data_label, name, percentile_rank, price, well_known
from app.growth.findings import Finding, chart, n, pts
from app.growth.memory import Memory, Series

MAX_GAP_DAYS = 3  # a window with a bigger hole in the stored closes is not measured
MAX_DAILY_MOVE = 60.0  # % for a major in one day: a data error, not news
MIN_HISTORY_DAYS = 365

# streak
MIN_STREAK = 4  # a run shorter than this is never a post
STREAK_SHARE = 0.03  # rare = at most 3% of the same-direction runs lasted this long...
STREAK_GAP_DAYS = 180  # ...or the last run this long ended at least 6 months ago
STREAK_BARS = 11  # at most this many bars (the run plus 2 days before it, at least 8): more and the value labels collide in portrait

# volatility
VOL_WINDOW = 30
VOL_TAIL = 0.05  # top / bottom 5% of the asset's own history
VOL_SINCE_DAYS = 90  # "highest since" is news only when "since" is at least 3 months back

# volume
VOL_UNIVERSE = 30
VOL_MIN_DAYS = 60  # new listings: volume around a listing isn't an anomaly
SPIKE_RATIO = 2.5
MAX_RATIO = 60.0  # beyond this the stored volume is suspect
# the ranking is a post only for a cluster: 3+ spikes with one at 4x+, or 5+ spikes (a market-wide surge). Backtest on the prod copy
# (Aug-Nov 2026): a bare "2+ perps at 2.5x" fired on almost half of all days.
TABLE_MIN_COUNT = 3
SINCE_DAYS = 90  # single-asset: the last day as high must be at least 3 months back
TABLE_TOP_RATIO = 4.0
TABLE_BROAD_COUNT = 5
_TICKER_OK = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")  # a ticker starting with a digit (0G, 2Z) would read as a figure in the text


# ---------------------------------------------------------------------------------------------------------------- helpers
def _window_clean(s: Series, lo: date, hi: date, major: bool = True) -> bool:
    """No gap > MAX_GAP_DAYS and no daily move beyond MAX_DAILY_MOVE (majors) between lo and hi."""
    i = s.index_on_or_before(hi)
    if i is None:
        return False
    j = i
    while j > 0 and s.dates[j - 1] >= lo:
        j -= 1
    if (s.dates[j] - lo).days > MAX_GAP_DAYS:
        return False
    for k in range(j + 1, i + 1):
        if (s.dates[k] - s.dates[k - 1]).days > MAX_GAP_DAYS or s.values[k - 1] <= 0 or s.values[k] <= 0:
            return False
        if major and abs(s.values[k] / s.values[k - 1] - 1) * 100 > MAX_DAILY_MOVE:
            return False
    return True


def _daily(s: Series) -> list[tuple[date, float]]:
    """(day, % change from the previous calendar day's close) wherever both closes are stored."""
    out = []
    for k in range(1, len(s)):
        if (s.dates[k] - s.dates[k - 1]).days == 1 and s.values[k - 1] > 0:
            out.append((s.dates[k], (s.values[k] / s.values[k - 1] - 1) * 100))
    return out


def _runs(daily: list[tuple[date, float]], down: bool) -> list[tuple[date, date, int]]:
    """Maximal runs of consecutive days moving in one direction: (first day, last day, length). A missing day ends a run."""
    out: list[tuple[date, date, int]] = []
    start: date | None = None
    prev: date | None = None
    length = 0
    for d, r in daily:
        hit = r < 0 if down else r > 0
        if hit and start is not None and prev is not None and (d - prev).days == 1:
            length += 1
        elif hit:
            if start is not None:
                out.append((start, prev, length))  # type: ignore[arg-type]
            start, length = d, 1
        else:
            if start is not None:
                out.append((start, prev, length))  # type: ignore[arg-type]
            start, length = None, 0
        prev = d
    if start is not None and prev is not None:
        out.append((start, prev, length))
    return out


# ---------------------------------------------------------------------------------------------------------------- streak
def streak(mem: Memory, as_of: date, coins: tuple[str, ...] = MAJORS) -> list[Finding]:
    """The current run of daily losses (or gains), when it is rare in the asset's own stored history."""
    out = []
    for coin in coins:
        s = price(mem, coin)
        if not s:
            continue
        s = s.upto(as_of)
        if len(s) < MIN_HISTORY_DAYS or s.last != as_of or (as_of - s.first).days < MIN_HISTORY_DAYS:
            continue
        if not _window_clean(s, as_of - timedelta(days=STREAK_BARS + 30), as_of):  # covers any run up to 42 days
            continue
        daily = _daily(s)
        if not daily or daily[-1][0] != as_of or daily[-1][1] == 0:
            continue
        down = daily[-1][1] < 0
        runs = _runs(daily, down)
        if not runs or runs[-1][1] != as_of:
            continue
        cur = runs[-1]
        length = cur[2]
        if length < MIN_STREAK:
            continue
        earlier = runs[:-1]
        as_long = [r for r in earlier if r[2] >= length]
        share = (len(as_long) + 1) / len(runs)
        last = as_long[-1] if as_long else None
        gap = (as_of - last[1]).days if last else None
        if not (share <= STREAK_SHARE or last is None or (gap is not None and gap >= STREAK_GAP_DAYS)):
            continue
        before = s.value(cur[0] - timedelta(days=1))
        if not before:
            continue
        move = (s.values[-1] / before - 1) * 100
        since = daily[0][0]
        adj, kind = ("lower", "losing") if down else ("higher", "winning")
        a = name(coin)
        nums = {"streak": n(length, str(length), f"consecutive daily closes {adj}"),
                "move": n(move, fmt.pct(move), "change over the run"),
                "count": n(len(as_long), fmt.num(len(as_long)), f"earlier {kind} runs as long"),
                "runs": n(len(runs), fmt.num(len(runs)), f"{kind} runs in the stored history"),
                "since": n(0, fmt.month_year(since), "stored history starts"),
                "date": n(0, fmt.day(as_of), "as of"),
                "start": n(0, fmt.day(cur[0]), "first day of the run")}
        words = {"asset": a, "data": data_label(s), "adj": adj, "kind": kind}
        comps = ["only", "longest", "last time"]
        if last is None:
            tail = "No " + kind + " run in {data} since {since} lasted this long."
            templates = ["{asset} has closed " + adj + " {streak} days in a row ({move} since {start}).\n" + tail,
                         "{asset}: {streak} straight daily closes " + adj + ", {move} in all. The longest " + kind + " run in {data} since {since}."]
            calc_last = "; no earlier run was as long"
            headline = "{asset} closed " + adj + " {streak} days in a row: the longest " + kind + " run in {data} since {since}"
        else:
            nums["last"] = n(0, fmt.day(last[1]), f"the last {kind} run this long ended")
            nums["last_len"] = n(last[2], str(last[2]), "that run's length, days")
            tail = "The last run this long ended on {last}."
            templates = ["{asset} has closed " + adj + " {streak} days in a row ({move} since {start}).\nIn {data} since {since}, only {count} of {runs} "
                         + kind + " runs lasted this long. " + tail,
                         "{asset}: {streak} straight daily closes " + adj + ", {move} in all.\nOnly {count} earlier " + kind + " runs in {data} since {since} "
                         "were as long. " + tail]
            calc_last = f"; most recent earlier run as long: {last[0].isoformat()} to {last[1].isoformat()} ({last[2]} days)"
            headline = "{asset} closed " + adj + " {streak} days in a row: only {count} of {runs} " + kind + " runs in {data} since {since} lasted this long"
        calc = (f"daily close changes (previous calendar day's close) in {s.key}; current run {cur[0].isoformat()} to {as_of.isoformat()}: {length} days "
                f"{adj}, close {fmt.raw(before, 2)} on {(cur[0] - timedelta(days=1)).isoformat()} -> {fmt.raw(s.values[-1], 2)} = {fmt.pct(move)}; "
                f"{len(as_long)} of {len(runs) - 1} earlier {kind} runs since {since.isoformat()} were >= {length} days{calc_last}")
        # chart: the last bars of daily changes, the run highlighted
        tail_days = [x for x in daily if x[0] > as_of - timedelta(days=min(max(length + 2, 8), STREAK_BARS))]
        cats = [((f"{fmt.MON[d.month - 1]} {d.day}" if k == 0 or d.day == 1 else str(d.day)), r) for k, (d, r) in enumerate(tail_days)]
        hl = [c for (d, _), (c, _) in zip(tail_days, cats) if d >= cur[0]]
        rarity = 1.0 if last is None else max(0.0, min(1.0, 1 - share * 8 + (min(gap or 0, 730) / 730) * 0.3))
        out.append(Finding(
            key=f"streak:{coin}:{'down' if down else 'up'}:{as_of.isoformat()}", format="extremes", detector="streak", subject=coin,
            headline=f"{a} {length} days {adj} in a row ({fmt.pct(move)}): {len(as_long)} of {len(runs)} {kind} runs since {since.isoformat()} as long",
            numbers=nums, words=words, templates=templates, window=f"{length}d run", series=[s.key], sources=[s.source], as_of=as_of,
            calculation=calc, rarity=rarity, comparatives=comps + (["none earlier"] if last is None else []),
            chart=chart("seasonality_bars", f"{a}: daily close changes", "Losing streak" if down else "Winning streak",
                        [{"name": "daily change, %", "points": [[c, round(r, 2)] for c, r in cats]}], sources=[s.source], as_of=as_of,
                        hero="{streak} days", hero_role="neg" if down else "pos", headline=headline,
                        dek="{asset}, daily close-to-close change in {data}; the run highlighted ({move} since {start})",
                        y_format="pct", options={"highlight": hl, "digits": 1})))
    return out


# ---------------------------------------------------------------------------------------------------------------- volatility
def _rolling_vol(s: Series) -> list[tuple[date, float]]:
    """(day, annualised % stdev of the 30 daily log returns ending that day), only where those 31 closes are consecutive days."""
    lr: list[tuple[date, float | None]] = []
    for k in range(1, len(s)):
        ok = (s.dates[k] - s.dates[k - 1]).days == 1 and s.values[k - 1] > 0 and s.values[k] > 0
        lr.append((s.dates[k], math.log(s.values[k] / s.values[k - 1]) if ok else None))
    out = []
    for i in range(VOL_WINDOW - 1, len(lr)):
        w = lr[i - VOL_WINDOW + 1:i + 1]
        if any(v is None for _, v in w) or (w[-1][0] - w[0][0]).days != VOL_WINDOW - 1:
            continue
        xs = [v for _, v in w]  # type: ignore[misc]
        m = sum(xs) / len(xs)  # type: ignore[arg-type]
        sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))  # type: ignore[operator]
        out.append((w[-1][0], sd * math.sqrt(365) * 100))
    return out


def volatility_regime(mem: Memory, as_of: date, coins: tuple[str, ...] = MAJORS) -> list[Finding]:
    out = []
    for coin in coins:
        s = price(mem, coin)
        if not s:
            continue
        s = s.upto(as_of)
        if s.last != as_of or (as_of - s.first).days < MIN_HISTORY_DAYS + VOL_WINDOW:
            continue
        if not _window_clean(s, as_of - timedelta(days=VOL_WINDOW + 1), as_of):
            continue
        vol = _rolling_vol(s)
        if len(vol) < MIN_HISTORY_DAYS or vol[-1][0] != as_of:
            continue
        cur = vol[-1][1]
        vals = [v for _, v in vol]
        pr = percentile_rank(vals, cur)
        high = pr >= 1 - VOL_TAIL
        if not high and pr > VOL_TAIL:
            continue
        count = sum(1 for v in vals if (v >= cur if high else v <= cur))
        share = count / len(vals)
        word = "highest" if high else "lowest"
        prior = [(d, v) for d, v in vol[:-1] if (v >= cur if high else v <= cur) and d < as_of - timedelta(days=1)]
        last = prior[-1] if prior else None
        if last is not None and (as_of - last[0]).days < VOL_SINCE_DAYS:
            continue  # it was just as extreme a few weeks ago: not a regime change worth a post
        start = vol[0][0]
        a = name(coin)
        nums = {"vol": n(round(cur, 2), fmt.pct(round(cur, 2), signed=False), "30-day volatility, annualised"),
                "share": n(share * 100, fmt.pct(share * 100, signed=False), "share of days this " + ("high" if high else "low")),
                "count": n(count, fmt.num(count), "days as " + ("high" if high else "low")),
                "total": n(len(vals), fmt.num(len(vals)), "days measured"),
                "start": n(0, fmt.month_year(start), "first day measured"),
                "date": n(0, fmt.day(as_of), "as of"), "win": n(VOL_WINDOW, str(VOL_WINDOW), "window, days")}
        one = next((v for d, v in vol if d == as_of - timedelta(days=365)), None)
        words = {"asset": a, "data": data_label(s)}
        comps = [word, "only", "just"]
        if last is None:
            since_txt = "in {data} since {start}"
            calc_last = "; no earlier day was as " + ("high" if high else "low")
        else:
            nums["since"] = n(0, fmt.month_year(last[0]), "last day as " + ("high" if high else "low"))
            nums["then"] = n(round(last[1], 2), fmt.pct(round(last[1], 2), signed=False), "volatility on that day")
            since_txt = "since {since}"
            calc_last = f"; most recent earlier day as {'high' if high else 'low'}: {last[0].isoformat()} ({fmt.raw(last[1], 2)}%)"
        templates = ["{asset}'s 30-day volatility is {vol} (annualised), its " + word + " " + since_txt + ".\nIn {data} since {start}, it has been this "
                     + ("high" if high else "low") + " on only {share} of days.",
                     "{asset} volatility: {vol} annualised over 30 days, the " + word + " " + since_txt + ". Just {count} of {total} days in {data} "
                     "since {start} were as " + ("volatile" if high else "calm") + "."]
        if one is not None:
            nums["y1"] = n(round(one, 2), fmt.pct(round(one, 2), signed=False), "volatility a year earlier")
            templates[0] += " A year ago: {y1}."
        calc = (f"30-day volatility = stdev of the 30 daily log returns {(as_of - timedelta(days=VOL_WINDOW - 1)).isoformat()} to {as_of.isoformat()} "
                f"x sqrt(365) = {fmt.raw(cur, 2)}% ({s.key}); {count:,} of {len(vals):,} days since {start.isoformat()} were {'>=' if high else '<='} "
                f"it ({fmt.pct(share * 100, signed=False, dp=1)}){calc_last}")
        role = "neg" if high else "blue"
        anns: list[dict] = [{"type": "marker", "x": as_of.isoformat(), "label": "{vol}\nToday", "role": role, "number": "vol"}]
        if last is not None:
            anns.append({"type": "point_label", "x": last[0].isoformat(), "label": "{then}\n{since}", "role": "context", "number": "then"})
        out.append(Finding(
            key=f"volatility:{coin}:{word}:{as_of.isoformat()}", format="extremes", detector="volatility_regime", subject=coin,
            headline=f"{a} 30-day volatility {fmt.pct(cur, signed=False)}: {word} " + (f"since {last[0].isoformat()}" if last else f"since {start.isoformat()}"),
            numbers=nums, words=words, templates=templates, window="30d", series=[s.key], sources=[s.source], as_of=as_of, calculation=calc,
            rarity=max(0.0, min(1.0, 1 - share * 6 + (0.2 if last is None else min((as_of - last[0]).days, 730) / 730 * 0.2))),
            comparatives=comps,
            chart=chart("rolling_line", f"{a} 30-day volatility", "Volatility regime",
                        [{"name": "30-day volatility", "points": pts(vol, 2), "role": "context", "label": "", "width": 2.2}],
                        annotations=anns, sources=[s.source], as_of=as_of, hero="vol", hero_role=role,
                        headline="{asset}'s 30-day volatility is its " + word + " " + since_txt,
                        dek="{asset}, 30-day realised volatility (annualised) each day in {data} since {start}", y_format="share",
                        options={"end_labels": False, "include_zero": True})))
    return out


# ---------------------------------------------------------------------------------------------------------------- volume
def _ratios(vs: Series) -> list[tuple[date, float]]:
    """(day, volume / mean of the previous 30 days' volume) where at least 27 of those days are stored and every value is positive."""
    out = []
    pos = {d: i for i, d in enumerate(vs.dates)}
    for i, d in enumerate(vs.dates):
        prev = [vs.values[pos[d - timedelta(days=k)]] for k in range(1, 31) if (d - timedelta(days=k)) in pos]
        if len(prev) < 27 or vs.values[i] <= 0 or min(prev) <= 0:
            continue
        out.append((d, vs.values[i] / (sum(prev) / len(prev))))
    return out


def volume_spike(mem: Memory, as_of: date) -> list[Finding]:
    universe = [c for c in well_known(mem, as_of, min_days=VOL_MIN_DAYS) if _TICKER_OK.match(c)]  # 11-27: major coins only
    spikes: list[dict] = []
    for coin in universe:
        vs = mem.get(f"px:{coin}:vol")
        ps = price(mem, coin)
        if not vs or not ps or vs.source != "hyperliquid":
            continue
        vs, ps = vs.upto(as_of), ps.upto(as_of)
        if vs.last != as_of or (as_of - vs.first).days < VOL_MIN_DAYS:
            continue
        if not _window_clean(ps, as_of - timedelta(days=31), as_of, major=coin in MAJORS):
            continue
        rs = _ratios(vs)
        if not rs or rs[-1][0] != as_of:
            continue
        r = rs[-1][1]
        if r < SPIKE_RATIO or r > MAX_RATIO:
            continue
        prev = [vs.value(as_of - timedelta(days=k)) for k in range(1, 31)]
        avg = sum(v for v in prev if v) / len([v for v in prev if v])
        p1 = ps.value(as_of - timedelta(days=1))
        chg = (ps.values[-1] / p1 - 1) * 100 if p1 else None
        spikes.append({"coin": coin, "ratio": r, "vol": vs.values[-1], "avg": avg, "chg": chg, "rs": rs, "vs": vs})
    if not spikes:
        return []
    spikes.sort(key=lambda x: -x["ratio"])
    out: list[Finding] = []
    date_txt = fmt.day(as_of)
    # single-asset: BTC / ETH, or the only spike among the 10 most traded
    singles = [x for x in spikes if x["coin"] in ("BTC", "ETH")]
    if not singles and len(spikes) == 1 and spikes[0]["coin"] in universe[:10]:
        singles = spikes[:1]
    for x in singles[:2]:
        prior = [d for d, v in x["rs"][:-1] if v >= x["ratio"]]
        if prior and (as_of - prior[-1]).days < SINCE_DAYS:
            continue  # "the most since" a few weeks ago is not an anomaly
        out.append(_single_spike(x, as_of))
    if len(spikes) >= TABLE_MIN_COUNT and (spikes[0]["ratio"] >= TABLE_TOP_RATIO or len(spikes) >= TABLE_BROAD_COUNT):
        out.append(_spike_table(spikes, len(universe), as_of, date_txt))
    return out


def _single_spike(x: dict, as_of: date) -> Finding:
    coin, r, rs, vs = x["coin"], x["ratio"], x["rs"], x["vs"]
    a = name(coin)
    prior = [(d, v) for d, v in rs[:-1] if v >= r]
    last = prior[-1] if prior else None
    vals = [v for _, v in rs]
    count = sum(1 for v in vals if v >= r)
    share = count / len(vals)
    start = rs[0][0]
    nums = {"ratio": n(round(r, 2), fmt.mult(r), "volume vs its 30-day average"),
            "vol": n(x["vol"], fmt.money(x["vol"]), "volume on the day, USD"),
            "avg": n(x["avg"], fmt.money(x["avg"]), "average daily volume, previous 30 days"),
            "date": n(0, fmt.day(as_of), "as of"), "start": n(0, fmt.month_year(start), "first day measured"),
            "count": n(count, fmt.num(count), "days with a ratio this high"), "total": n(len(vals), fmt.num(len(vals)), "days measured"),
            "win": n(30, "30", "averaging window, days")}
    words = {"asset": a, "ticker": coin}
    if x["chg"] is not None:
        nums["chg"] = n(x["chg"], fmt.pct(x["chg"]), "close-to-close change that day")
    comps = ["most", "only", "just"]
    if last is None:
        since_txt = "the most in Hyperliquid data since {start}"
        calc_last = "; no earlier day as high"
    else:
        nums["since"] = n(0, fmt.day(last[0]), "last day as high")
        since_txt = "the most since {since}"
        calc_last = f"; last day as high: {last[0].isoformat()} ({last[1]:.2f}x)"
    move = " Price that day: {chg}." if "chg" in nums else ""
    templates = ["{asset} perps traded {vol} on Hyperliquid on {date}: {ratio} the average of the previous 30 days, " + since_txt + "." + move,
                 "{asset} volume on Hyperliquid: {vol} on {date}, {ratio} its 30-day average ({avg} a day). Just {count} of {total} days since {start} "
                 "came in this far above average."]
    calc = (f"volume {fmt.raw(x['vol'], 0)} USD on {as_of.isoformat()} / mean of the previous 30 days {fmt.raw(x['avg'], 0)} = {r:.3f}x ({vs.key}); "
            f"{count} of {len(vals)} days since {start.isoformat()} had a ratio >= {r:.2f}{calc_last}")
    anns: list[dict] = [{"type": "hline", "y": 1.0, "role": "context", "label": "a usual day"},
                        {"type": "marker", "x": as_of.isoformat(), "label": "{ratio}\n{date}", "role": "highlight", "number": "ratio"}]
    window = [(d, v) for d, v in rs if d > as_of - timedelta(days=365)]
    return Finding(
        key=f"volume:{coin}:{as_of.isoformat()}", format="extremes", detector="volume_spike", subject=coin,
        headline=f"{a} Hyperliquid volume {r:.1f}x its 30-day average on {as_of.isoformat()}" + (f", most since {last[0].isoformat()}" if last else ""),
        numbers=nums, words=words, templates=templates, window="1d vs 30d", series=[vs.key], sources=["hyperliquid"], as_of=as_of, calculation=calc,
        rarity=max(0.0, min(1.0, 1 - share * 10)), comparatives=comps,
        chart=chart("rolling_line", f"{a} volume vs its 30-day average", "Volume anomaly",
                    [{"name": "volume ÷ 30-day average", "points": pts(window, 2), "role": "context", "label": "", "width": 1.8}],
                    annotations=anns, sources=["hyperliquid"], as_of=as_of, hero="ratio", hero_role="highlight",
                    headline="{asset} perps on Hyperliquid traded {ratio} their 30-day average on {date}, " + since_txt,
                    dek="{asset}, each day's Hyperliquid volume divided by the average of the 30 days before it (1 = a usual day)",
                    y_format="ratio", options={"end_labels": False, "include_zero": True}))


def _spike_table(spikes: list[dict], universe: int, as_of: date, date_txt: str) -> Finding:
    top = spikes[:8]
    nums: dict = {"date": n(0, date_txt, "as of"), "count": n(len(spikes), fmt.num(len(spikes)), "perps at or above the threshold"),
                  "universe": n(universe, fmt.num(universe), "most traded perps checked"),
                  "thresh": n(SPIKE_RATIO, fmt.mult(SPIKE_RATIO), "threshold"), "win": n(30, "30", "averaging window, days")}
    words: dict = {}
    for k, x in enumerate(top[:3], 1):
        nums[f"r{k}"] = n(round(x["ratio"], 2), fmt.mult(x["ratio"]), f"{x['coin']} volume vs its 30-day average")
        words[f"a{k}"] = x["coin"]
    shown = min(3, len(top))
    lst = " · ".join(f"{{a{k}}} {{r{k}}}" for k in range(1, shown + 1))
    templates = ["Hyperliquid volume on {date}, against each perp's own 30-day average:\n" + lst + "\n{count} of the {universe} most traded perps "
                 "did at least {thresh} their usual volume.",
                 "Unusual volume on Hyperliquid ({date}): " + lst + " their 30-day average. {count} of the {universe} most traded perps cleared {thresh}."]
    calc = "; ".join(f"{x['coin']}: {fmt.raw(x['vol'], 0)} / {fmt.raw(x['avg'], 0)} = {x['ratio']:.3f}x" for x in spikes) + \
           f" (volume on {as_of.isoformat()} / mean of the previous 30 days, px:<coin>:vol; universe: the {universe} perps with the highest 30-day " \
           f"average volume and >= {VOL_MIN_DAYS} days listed)"
    rows = [{"label": x["coin"], "value": round(x["ratio"], 2), "display": fmt.mult(x["ratio"]), "highlight": k < 3, "role": "highlight",
             "sublabel": (fmt.pct(x["chg"]) if x["chg"] is not None else None)} for k, x in enumerate(top)]
    return Finding(
        key=f"volume_table:{as_of.isoformat()}", format="extremes", detector="volume_spike", subject="HL-volume",
        headline=f"{len(spikes)} of the {universe} most traded Hyperliquid perps at >= {SPIKE_RATIO}x their 30-day volume on {as_of.isoformat()}",
        numbers=nums, words=words, templates=templates, window="1d vs 30d", series=[x["vs"].key for x in spikes], sources=["hyperliquid"], as_of=as_of,
        calculation=calc, rarity=max(0.0, min(1.0, 0.45 + 0.06 * (spikes[0]["ratio"] - SPIKE_RATIO) + 0.04 * len(spikes))),
        comparatives=["most", "at least"],
        chart=chart("rank_table", "Volume vs 30-day average", "Volume anomaly", [], sources=["hyperliquid"], as_of=as_of, rows=rows,
                    hero="r1", hero_role="highlight",
                    headline="{a1} traded {r1} its 30-day average on Hyperliquid on {date}; {count} of the {universe} most traded perps cleared {thresh}",
                    dek="Volume on {date} ÷ each perp's average of the previous 30 days · price change that day in grey",
                    y_format="ratio", options={"ref_value": 1.0, "ref_label": "usual day"}))


__all__ = ["streak", "volatility_regime", "volume_spike"]


# ---------------------------------------------------------------------------------------------------------------- weekly streak
WEEK_MIN_STREAK = 4  # weeks
WEEK_BARS = 12  # the run plus the weeks before it, at most this many bars


def _weekly(s: Series, as_of: date) -> list[tuple[date, float]]:
    """(Sunday, % change of the Sunday close from the previous Sunday's close): Sunday's daily close is the weekly close (00:00 UTC Monday).
    Only completed weeks up to as_of; a week whose two Sundays aren't both stored is left out (and breaks a run)."""
    closes = {d: v for d, v in zip(s.dates, s.values) if d <= as_of and d.weekday() == 6}
    out = []
    for d in sorted(closes):
        prev = closes.get(d - timedelta(days=7))
        if prev and prev > 0:
            out.append((d, (closes[d] / prev - 1) * 100))
    return out


def _week_runs(weekly: list[tuple[date, float]], down: bool) -> list[tuple[date, date, int]]:
    """Maximal runs of consecutive weeks moving one way: (first Sunday, last Sunday, weeks). A missing week ends a run."""
    out: list[tuple[date, date, int]] = []
    start = prev = None
    length = 0
    for d, r in weekly:
        hit = r < 0 if down else r > 0
        if hit and start is not None and prev is not None and (d - prev).days == 7:
            length += 1
        else:
            if start is not None:
                out.append((start, prev, length))  # type: ignore[arg-type]
            start, length = (d, 1) if hit else (None, 0)
        prev = d
    if start is not None and prev is not None:
        out.append((start, prev, length))
    return out


def weekly_streak(mem: Memory, as_of: date, coins: tuple[str, ...] = MAJORS) -> list[Finding]:
    """The current run of weekly losses (or gains), Sunday close to Sunday close, when no earlier run in the stored history was as long, or
    the last one as long ended at least STREAK_GAP_DAYS ago. The week in progress is never counted."""
    out = []
    for coin in coins:
        s = price(mem, coin)
        if not s:
            continue
        s = s.upto(as_of)
        if (as_of - s.first).days < MIN_HISTORY_DAYS or s.last != as_of:
            continue
        weekly = _weekly(s, as_of)
        if len(weekly) < 52 or (as_of - weekly[-1][0]).days > 6 or weekly[-1][1] == 0:
            continue
        down = weekly[-1][1] < 0
        runs = _week_runs(weekly, down)
        if not runs or runs[-1][1] != weekly[-1][0]:
            continue
        cur = runs[-1]
        length = cur[2]
        if length < WEEK_MIN_STREAK or not _window_clean(s, cur[0] - timedelta(days=7), cur[1]):
            continue
        as_long = [r for r in runs[:-1] if r[2] >= length]
        last = as_long[-1] if as_long else None
        gap = (as_of - last[1]).days if last else None
        if last is not None and (gap or 0) < STREAK_GAP_DAYS:
            continue
        base = s.value(cur[0] - timedelta(days=7))
        end = s.value(cur[1])
        if not base or not end:
            continue
        move = (end / base - 1) * 100
        since = weekly[0][0] - timedelta(days=7)
        adj, kind, word = ("lower", "losing", "down") if down else ("higher", "winning", "up")
        a = name(coin)
        nums = {"weeks": n(length, str(length), f"consecutive weekly closes {adj}"),
                "move": n(move, fmt.pct(move), "change over the run (Sunday closes)"),
                "since": n(0, fmt.month_year(since), "stored weekly history starts"),
                "start": n(0, fmt.day(cur[0] - timedelta(days=7)), "the Sunday close before the run"),
                "end": n(0, fmt.day(cur[1]), "the last weekly close"),
                "runs": n(len(runs), fmt.num(len(runs)), f"weekly {kind} runs in the stored history")}
        words = {"asset": a, "data": data_label(s)}
        if last is None:
            templates = ["{asset} has closed " + adj + " {weeks} weeks in a row ({move} from the {start} weekly close to {end}).\n"
                         "No " + kind + " run this long in {data}, which begins in {since}.",
                         "{weeks} straight " + word + " weeks for {asset}: {move} in all.\nThe longest weekly " + kind + " run in {data}, which goes back to {since}."]
            headline = "{asset} closed " + adj + " {weeks} weeks in a row: the longest " + kind + " run in {data} since {since}"
            comps = ["longest", "none earlier"]
        else:
            nums["last"] = n(0, fmt.month_year(last[1]), f"the last weekly {kind} run this long ended")
            templates = ["{asset} has closed " + adj + " {weeks} weeks in a row ({move} from the {start} weekly close to {end}).\n"
                         "The last weekly run this long in {data} (since {since}) ended in {last}."]
            headline = "{asset} closed " + adj + " {weeks} weeks in a row, the first run this long since {last}"
            comps = ["first since", "last time"]
        calc = (f"weekly close = Sunday's daily close in {s.key}; {length} consecutive weekly closes {adj}: "
                f"{(cur[0] - timedelta(days=7)).isoformat()} {fmt.raw(base, 2)} -> {cur[1].isoformat()} {fmt.raw(end, 2)} = {fmt.pct(move)}; "
                f"{len(as_long)} earlier {kind} runs of {length}+ weeks among {len(runs) - 1} since {since.isoformat()}"
                + (f" (last ended {last[1].isoformat()})" if last else ""))
        bars = weekly[-max(length + 3, 8):][-WEEK_BARS:]
        cats = [(f"{fmt.MON[d.month - 1]} {d.day}", r) for d, r in bars]
        hl = [c for (d, _), (c, _) in zip(bars, cats) if d >= cur[0]]
        rarity = 1.0 if last is None else min(1.0, 0.6 + min(gap or 0, 730) / 1825)
        out.append(Finding(
            key=f"weekly_streak:{coin}:{word}:{cur[1].isoformat()}", format="extremes", detector="weekly_streak", subject=coin,
            headline=f"{a} {length} weeks {adj} in a row ({fmt.pct(move)})" + (" - longest in the stored history" if last is None else ""),
            numbers=nums, words=words, templates=templates, window=f"{length}w run", series=[s.key], sources=[s.source], as_of=as_of,
            calculation=calc, rarity=rarity * (1.0 if coin in ("BTC", "ETH") else 0.92), comparatives=comps,
            chart=chart("seasonality_bars", f"{a}: weekly close changes", "Weekly losing streak" if down else "Weekly winning streak",
                        [{"name": "weekly change, %", "points": [[c, round(r, 2)] for c, r in cats]}], sources=[s.source], as_of=as_of,
                        hero="{weeks} weeks", hero_role="neg" if down else "pos", headline=headline,
                        dek="{asset}, Sunday close to Sunday close (UTC) in {data}; the {weeks}-week run highlighted: {move}",
                        y_format="pct", options={"highlight": hl, "digits": 1})))
    return out
