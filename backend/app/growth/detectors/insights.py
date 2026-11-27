"""Growth G2: historical comparisons and original insights from cleared data (Hyperliquid closes, alternative.me Fear & Greed).

- fear_aftermath: today's Fear & Greed is in extreme fear; what Bitcoin did 30 and 90 days after every earlier episode this low (history only,
  never a forecast: every figure is a past, measured return).
- year_paths: Bitcoin's / Ether's year-to-date path against every stored full year, drawn on one calendar; fires when this year is the worst
  or the best of them at this day of the year.
- gold_ratio: Bitcoin priced in PAXG (a gold-backed token, one troy ounce each) on Hyperliquid; fires on the lowest / highest since a date at
  least 180 days back.

Pure functions (Memory, as_of) -> list[Finding]. A gap of more than 3 days, or an implausible value, in what a finding measures: no finding.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta

from app.growth import fmt
from app.growth.detectors.common import data_label, name, price
from app.growth.findings import Finding, chart, n, pts
from app.growth.memory import Memory, Series

MAX_GAP_DAYS = 3

# --- shared guards ---------------------------------------------------------------------------------------------------------------


def _max_gap(s: Series, start: date, end: date) -> int:
    """Largest gap (days) between consecutive stored days inside [start, end] (and from start / to end)."""
    ds = [d for d in s.dates if start <= d <= end]
    if not ds:
        return 10**6
    gaps = [(ds[0] - start).days, (end - ds[-1]).days] + [(b - a).days for a, b in zip(ds, ds[1:])]
    return max(gaps)


def _jumpy(s: Series, start: date, end: date, limit: float) -> bool:
    """Any day-to-day move larger than `limit` (a fraction) inside [start, end]: a bad print, not a market."""
    vals = [(d, v) for d, v in zip(s.dates, s.values) if start <= d <= end]
    if any(v <= 0 for _, v in vals):
        return True
    return any(abs(b / a - 1) > limit for (_, a), (_, b) in zip(vals, vals[1:]))


# --- 1. fear aftermath -----------------------------------------------------------------------------------------------------------

FEAR_MAX = 20  # "extreme fear" readings only
EPISODE_GAP = 14  # readings this many days apart start a new episode
MIN_EPISODES = 5
H1, H2 = 30, 90


def episodes(fng: Series, btc: Series, level: float, as_of: date) -> list[list[date]]:
    """Runs of days (before as_of) with Fear & Greed <= level and a Bitcoin close; a new run starts after EPISODE_GAP days without one."""
    eps: list[list[date]] = []
    for d, v in zip(fng.dates, fng.values):
        if d >= as_of or v > level or btc.value(d) is None:
            continue
        if eps and (d - eps[-1][-1]).days < EPISODE_GAP:
            eps[-1].append(d)
        else:
            eps.append([d])
    return eps


def fear_aftermath(mem: Memory, as_of: date) -> list[Finding]:
    f, s = mem.get("fng"), price(mem, "BTC")
    if not f or not s:
        return []
    fi = f.index_on_or_before(as_of, 1)
    if fi is None or s.upto(as_of).last != as_of:
        return []
    level = f.values[fi]
    if not (0 <= level <= FEAR_MAX) or any(not (0 <= v <= 100) for v in f.values):
        return []
    rows = []
    for ep in episodes(f, s, level, as_of):
        d = ep[0]
        p0, p1, p2 = s.value(d), s.value(d + timedelta(days=H1)), s.value(d + timedelta(days=H2))
        if d + timedelta(days=H2) > as_of or not (p0 and p1 and p2):
            continue  # the current (or a too recent) episode has no 90 days after it yet
        if _max_gap(s, d, d + timedelta(days=H2)) > MAX_GAP_DAYS or _jumpy(s, d, d + timedelta(days=H2), 0.35):
            return []
        rows.append({"day": d, "fng": f.value(d), "days": len(ep), "p0": p0, "r1": (p1 / p0 - 1) * 100, "r2": (p2 / p0 - 1) * 100})
    if len(rows) < MIN_EPISODES:
        return []
    eps = len(rows)
    r1 = [r["r1"] for r in rows]
    r2 = [r["r2"] for r in rows]
    up1, up2 = sum(1 for x in r1 if x > 0), sum(1 for x in r2 if x > 0)
    med1, med2 = statistics.median(r1), statistics.median(r2)
    share_low = sum(1 for v in f.values if v <= level) / len(f.values)
    start = s.first
    nums = {"fng": n(level, fmt.num(level), "Fear & Greed today"), "eps": n(eps, str(eps), "earlier episodes with 90 days after them"),
            "up30": n(up1, str(up1), f"episodes with Bitcoin higher {H1} days later"), "up90": n(up2, str(up2), f"episodes with Bitcoin higher {H2} days later"),
            "med30": n(med1, fmt.pct(med1), f"median {H1}-day return"), "med90": n(med2, fmt.pct(med2), f"median {H2}-day return"),
            "h30": n(H1, str(H1), "days after"), "h90": n(H2, str(H2), "days after"), "gap": n(EPISODE_GAP, str(EPISODE_GAP), "days between episodes"),
            "start": n(0, fmt.month_year(start), "Bitcoin history starts"), "date": n(0, fmt.day(as_of), "as of")}
    templates = [
        "Fear & Greed is at {fng}.\nIn Hyperliquid data since {start}, it had dropped to {fng} or lower in {eps} earlier episodes ({gap}+ days apart).\n"
        "Bitcoin {h30} days later: higher in {up30} of {eps}, median {med30}.\n{h90} days later: higher in {up90} of {eps}, median {med90}.",
        "Bitcoin after Fear & Greed hit {fng} or lower ({eps} episodes since {start}, Hyperliquid data):\n"
        "{h30} days on: higher {up30} times of {eps}, median {med30}\n{h90} days on: higher {up90} times of {eps}, median {med90}\nToday: {fng}.",
    ]
    calc = (f"Fear & Greed on {as_of.isoformat()} (or the day before) = {level:.0f}; episodes = runs of days before {as_of.isoformat()} with F&G <= "
            f"{level:.0f} and a Bitcoin close, a new run after {EPISODE_GAP} days without one, measured from each run's first day, kept when the "
            f"close {H2} days later is stored; " + "; ".join(
                f"{r['day'].isoformat()} F&G {r['fng']:.0f} ({r['days']} days): close {fmt.raw(r['p0'], 2)}, +{H1}d {r['r1']:+.2f}%, +{H2}d {r['r2']:+.2f}%"
                for r in rows) + f"; higher after {H1}d {up1}/{eps}, median {med1:+.2f}%; after {H2}d {up2}/{eps}, median {med2:+.2f}% ({s.key}, fng)")
    table = [{"label": fmt.day(r["day"]), "sublabel": f"F&G {r['fng']:.0f} · {H1}d {fmt.pct(r['r1'])}", "value": round(r["r2"], 2),
              "display": fmt.pct(r["r2"]), "highlight": False} for r in rows]
    return [Finding(
        key=f"fearafter:BTC:{level:.0f}:{as_of.isoformat()}", format="history", detector="fear_aftermath", subject="FNG-BTC",
        headline=f"After F&G <= {level:.0f} ({eps} episodes since {start.isoformat()}): BTC higher {up1}/{eps} at +{H1}d (median {fmt.pct(med1)}), "
                 f"{up2}/{eps} at +{H2}d (median {fmt.pct(med2)})",
        numbers=nums, words={}, templates=templates, window=f"since {start.isoformat()}", series=[s.key, "fng"], sources=[s.source, "alternative_me"],
        as_of=as_of, calculation=calc, rarity=min(0.9, 0.5 + 0.4 * (1 - share_low)), comparatives=["lower", "higher"],
        chart=chart("rank_table", "After extreme fear", "", [{"name": f"{H2}-day return", "points": pts([(r["day"], r["r2"]) for r in rows], 2)}],
                    rows=table, sources=[s.source, "alternative_me"], as_of=as_of, hero="{up90} of {eps}", hero_role="highlight",
                    headline="times Bitcoin was higher {h90} days after Fear & Greed fell to {fng} or lower (median {med90}). Today: {fng}",
                    dek="Each earlier episode since {start} ({gap}+ days apart): first day at {fng} or lower, Bitcoin's return {h90} days later "
                        "(the {h30}-day return in grey)",
                    y_format="pct", options={}))]


# --- 2. year paths ---------------------------------------------------------------------------------------------------------------

MIN_YEARS = 3  # this year and at least two full stored years


def _same_day(y: int, as_of: date) -> date:
    return date(y, as_of.month, min(as_of.day, 28) if (as_of.month, as_of.day) == (2, 29) else as_of.day)


def year_paths(mem: Memory, as_of: date, coins: tuple[str, ...] = ("BTC", "ETH")) -> list[Finding]:
    out = []
    for coin in coins:
        s = price(mem, coin)
        if not s:
            continue
        s = s.upto(as_of)
        if s.last != as_of or (as_of - date(as_of.year, 1, 1)).days < 45:
            continue  # six weeks into the year at least
        years = []
        for y in range(s.first.year, as_of.year + 1):
            base_day = date(y - 1, 12, 31)
            end = _same_day(y, as_of)
            base = s.value(base_day)
            if base is None or s.index_on_or_before(end) is None or s.dates[s.index_on_or_before(end)] != end:  # type: ignore[index]
                continue  # a year counts only with its Dec 31 base and a close on this day
            if _max_gap(s, base_day, end) > MAX_GAP_DAYS or _jumpy(s, base_day, end, 0.35):
                break
            path = [(d, (v / base - 1) * 100) for d, v in zip(s.dates, s.values) if base_day < d <= end]
            years.append((y, path[-1][1], path, base))
        else:
            if len(years) < MIN_YEARS or years[-1][0] != as_of.year:
                continue
            now = years[-1][1]
            past = [r for _, r, _, _ in years[:-1]]
            if now < min(past):
                word = "worst"
            elif now > max(past):
                word = "best"
            else:
                continue
            a = name(coin)
            lst = " · ".join(f"{y} {fmt.pct(r, dp=0)}" for y, r, _, _ in years)
            nums = {"now": n(now, fmt.pct(now), f"{as_of.year} so far"), "list": n(0, lst, "year to date by year"),
                    "years": n(len(years), str(len(years)), "years compared"), "day": n(0, f"{fmt.MON[as_of.month - 1]} {as_of.day}", "day of the year"),
                    "first": n(years[0][0], str(years[0][0]), "first year compared"), "jan1": n(1, "Jan 1", "start of the year"), "year": n(as_of.year, str(as_of.year), "this year")}
            for k, (y, r, _, _) in enumerate(years[:-1], 1):
                nums[f"y{k}"] = n(r, fmt.pct(r), f"{y} to the same day")
            templates = [a + " from {jan1} to {day}, each year in Hyperliquid data:\n{list}\n{year} is the " + word + " of the {years} years since {first}.",
                         "{year} so far: " + a + " {now} to {day}, the " + word + " year to date of the {years} years in Hyperliquid data since {first}.\n{list}"]
            calc = (f"each year: close on {_same_day(0 + as_of.year, as_of).strftime('%m-%d')} / close on Dec 31 of the year before − 1, years with both "
                    f"closes and no gap over {MAX_GAP_DAYS} days ({s.key}): " + "; ".join(
                        f"{y}: {fmt.raw(p[-1][1], 2)}% (base {fmt.raw(b, 2)})" for y, _, p, b in years) + f"; {as_of.year} is the {word}")
            series = []
            for y, r, path, _ in years:
                cur = y == as_of.year
                mapped = [(date(as_of.year, d.month, d.day), v) for d, v in path if not (d.month == 2 and d.day == 29)]
                series.append({"name": str(y), "points": pts(mapped, 2), "role": ("neg" if r < 0 else "pos") if cur else "context",
                               "label": str(y), "width": 3.4 if cur else 2.0})
            last = series[-1]["points"][-1]
            anns = [{"type": "check", "x": last[0], "series": len(series) - 1, "number": "now"}]  # the end label prints it
            for k, (y, r, path, _) in enumerate(years[:-1]):
                p = series[k]["points"][-1]
                anns.append({"type": "check", "x": p[0], "series": k, "number": f"y{k + 1}"})
            out.append(Finding(
                key=f"yearpath:{coin}:{word}:{as_of.isoformat()}", format="history", detector="year_paths", subject=coin,
                headline=f"{a} {as_of.year} to {fmt.day(as_of)} {fmt.pct(now)}: {word} of {len(years)} stored years ({lst})",
                numbers=nums, words={}, templates=templates, window=f"{years[0][0]}-{as_of.year}", series=[s.key], sources=[s.source], as_of=as_of,
                calculation=calc, rarity=min(0.85, 0.35 + 0.1 * len(years) + min(0.2, abs(now - (min(past) if word == "worst" else max(past))) / 200)),
                comparatives=[word],
                chart=chart("drawdown_overlay", f"{a}, year to date", "", series, annotations=anns, sources=[s.source], as_of=as_of, hero="now",
                            hero_role="auto", headline=a + " from {jan1} to {day}: {year} is the " + word + " of the {years} years in " + data_label(s),
                            dek=a + ", % change from each year's Dec 31 close; every year is drawn on this year's calendar",
                            y_format="pct", options={"x_dates": True, "end_values": True}),
                notes=[f"{len(years)} years only: Hyperliquid history starts {s.first.isoformat()}"]))
    return out


# --- 3. gold ratio ---------------------------------------------------------------------------------------------------------------

MIN_SINCE_DAYS = 180


def gold_ratio(mem: Memory, as_of: date, pairs: tuple[tuple[str, str], ...] = (("BTC", "PAXG"),)) -> list[Finding]:
    out = []
    for num_c, den_c in pairs:
        a, b = price(mem, num_c), price(mem, den_c)
        if not a or not b:
            continue
        a, b = a.upto(as_of), b.upto(as_of)
        bm = dict(zip(b.dates, b.values))
        r = Series.of(f"{num_c}/{den_c}", [(d, v / bm[d]) for d, v in zip(a.dates, a.values) if bm.get(d, 0) > 0], a.source)
        if len(r) < 365 or r.last != as_of:
            continue
        if _max_gap(r, r.first, as_of) > MAX_GAP_DAYS or _jumpy(b, b.first, as_of, 0.25) or _jumpy(a, r.first, as_of, 0.35):
            continue
        v = r.values[-1]
        gold = den_c == "PAXG"
        nn, dn = name(num_c), ("PAXG" if gold else name(den_c))
        for low in (True, False):
            j = next((i for i in range(len(r) - 2, -1, -1) if (r.values[i] <= v if low else r.values[i] >= v)), None)
            word = "lowest" if low else "highest"
            nums = {"v": n(v, fmt.ratio(v), f"{num_c}/{den_c}"), "date": n(0, fmt.day(as_of), "as of"),
                    "px": n(a.values[-1], fmt.money(a.values[-1]), f"{num_c} close"), "gx": n(b.values[-1], fmt.money(b.values[-1]), f"{den_c} close")}
            if j is None:
                gap = (as_of - r.first).days
                nums["since"] = n(0, fmt.month_year(r.first), "stored history starts")
                claim = "the " + word + " in " + data_label(a) + " since {since}"
                calc_claim = f"no earlier ratio since {r.first.isoformat()} was {'lower' if low else 'higher'}"
            else:
                gap = (as_of - r.dates[j]).days
                if gap < MIN_SINCE_DAYS:
                    continue
                nums["since"] = n(0, fmt.month_year(r.dates[j]), "last time this " + ("low" if low else "high"))
                claim = "the " + word + " since {since}"
                calc_claim = f"most recent earlier ratio {'<=' if low else '>='} it: {r.values[j]:.4f} on {r.dates[j].isoformat()}"
            y = r.value(as_of - timedelta(days=365))
            tail = ""
            if y:
                nums["y"] = n(y, fmt.ratio(y), "a year ago")
                tail = " A year ago: {y}."
            if gold:
                templates = ["Bitcoin priced in gold: one bitcoin buys {v} PAXG (a gold-backed token, one troy ounce each) on Hyperliquid, " + claim + "."
                             + tail,
                             "Bitcoin {px} ÷ PAXG {gx} = {v} ounces of tokenised gold per bitcoin ({date}, Hyperliquid), " + claim + "." + tail]
                unit, dek = "ounces of gold per bitcoin", ("Bitcoin close divided by PAXG close (a gold-backed token, one troy ounce each), "
                                                          "each day both have one, in " + data_label(a))
            else:
                templates = [f"{nn} priced in {dn}: {{v}} on {{date}}, " + claim + "." + tail]
                unit, dek = f"{den_c} per {num_c}", f"{nn} close divided by {dn} close, each day both have one, in " + data_label(a)
            look = max(0, len(r) - 1100)
            anns = [{"type": "marker", "x": as_of.isoformat(), "label": "{v}\nToday", "role": "neg" if low else "pos", "number": "v"}]
            if y:
                anns.append({"type": "hline", "y": round(y, 4), "label": "A year ago: {y}", "role": "context"})
            if j is not None and j >= look:
                anns.append({"type": "point_label", "x": r.dates[j].isoformat(), "label": "As " + ("low" if low else "high") + "\n{since}",
                             "role": "context"})
            out.append(Finding(
                key=f"goldratio:{num_c}{den_c}:{word}:{as_of.isoformat()}", format="relative", detector="gold_ratio", subject=f"{num_c}/{den_c}",
                headline=f"{num_c}/{den_c} {v:.4g}: {word} " + (f"since {r.dates[j].isoformat()}" if j is not None else "in stored history"),
                numbers=nums, words={}, templates=templates,
                window="since " + (r.dates[j].isoformat() if j is not None else r.first.isoformat()), series=[a.key, b.key],
                sources=sorted({a.source, b.source}), as_of=as_of,
                calculation=f"{num_c}/{den_c} = {a.key} / {b.key} on each day both close; {as_of.isoformat()}: {fmt.raw(a.values[-1], 2)} / "
                            f"{fmt.raw(b.values[-1], 2)} = {v:.6f}; {calc_claim}",
                rarity=min(1.0, 0.45 + gap / 1000), comparatives=[word, "since"],
                chart=chart("ratio_line", f"{nn} priced in {dn}", "",
                            [{"name": f"{num_c}/{den_c}", "points": pts(list(zip(r.dates[look:], r.values[look:])), 4), "role": "context",
                              "label": "", "width": 2.6}],
                            annotations=anns, sources=sorted({a.source, b.source}), as_of=as_of, hero="v", hero_role="neg" if low else "pos",
                            headline=f"{unit}: " + claim.replace("the ", "its ", 1), dek=dek, y_format="oz" if gold else "ratio",
                            options={"end_labels": False})))
    return out


__all__ = ["fear_aftermath", "year_paths", "gold_ratio", "episodes"]


# ---------------------------------------------------------------------------------------------------------------- distance from the highs
DD_COINS = ("BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "LINK", "AVAX", "LTC", "ADA", "SUI", "HYPE")
DD_MIN_DAYS = 365  # each coin needs a year of stored history (its high must be a real one, not its listing week)
DD_MIN_ROWS = 6
DD_MAX_GAP = 3


def drawdown_table(mem: Memory, as_of: date, coins: tuple[str, ...] = DD_COINS) -> list[Finding]:
    """How far each large coin sits below its highest close in the stored data, deepest last. A ranking of distance from the highs, in
    Hyperliquid data only (a coin's high is its high in that data, and the text says so). Fires when Bitcoin is at least 20% below its high
    and the median coin is at least 40% below (a market-wide drawdown, not one coin)."""
    rows = []
    for coin in coins:
        s = price(mem, coin)
        if not s:
            continue
        s = s.upto(as_of)
        if s.last != as_of or (as_of - s.first).days < DD_MIN_DAYS:
            continue
        if any((s.dates[k] - s.dates[k - 1]).days > DD_MAX_GAP for k in range(max(1, len(s) - 400), len(s))):
            continue
        hi_i = max(range(len(s)), key=lambda k: s.values[k])
        if (as_of - s.dates[hi_i]).days < 7 or s.values[hi_i] <= 0:
            continue
        dd = (s.values[-1] / s.values[hi_i] - 1) * 100
        rows.append({"coin": coin, "dd": dd, "high": s.values[hi_i], "high_day": s.dates[hi_i], "now": s.values[-1], "first": s.first, "key": s.key,
                     "source": s.source})
    btc = next((r for r in rows if r["coin"] == "BTC"), None)
    if btc is None or len(rows) < DD_MIN_ROWS:
        return []
    med = statistics.median(r["dd"] for r in rows)
    if btc["dd"] > -20 or med > -40:
        return []
    rows.sort(key=lambda r: -r["dd"])  # shallowest first, deepest last
    deepest = rows[-1]
    earliest = min(r["first"] for r in rows)
    nums = {"btc": n(btc["dd"], fmt.pct(btc["dd"]), "Bitcoin below its highest close"),
            "btc_high": n(btc["high"], fmt.money(btc["high"]), "Bitcoin's highest close"),
            "btc_high_day": n(0, fmt.day(btc["high_day"]), "date of Bitcoin's highest close"),
            "median": n(med, fmt.pct(med), "median distance below the high"),
            "deep": n(deepest["dd"], fmt.pct(deepest["dd"]), f"{deepest['coin']} below its highest close"),
            "count": n(len(rows), str(len(rows)), "coins ranked"),
            "since": n(0, fmt.month_year(earliest), "stored history starts"),
            "date": n(0, fmt.day(as_of), "as of")}
    lst = " · ".join(f"{name(r['coin']) if r['coin'] in ('BTC', 'ETH') else r['coin']} {fmt.pct(r['dd'], dp=0)}" for r in rows[:6])
    nums["list"] = n(0, lst, "distance below the highest close, shallowest first")
    words = {"deep_coin": deepest["coin"]}
    templates = ["How far below their highest close in Hyperliquid data ({date}):\n{list}\nBitcoin is {btc} from its {btc_high} close of {btc_high_day}. "
                 "The median of the {count} coins: {median}.",
                 "Bitcoin is {btc} from its {btc_high} high close ({btc_high_day}).\nAcross {count} major coins in Hyperliquid data, the median is "
                 "{median}; the deepest, {deep_coin}, is {deep}."]
    calc = "; ".join(f"{r['coin']}: close {fmt.raw(r['now'], 4)} on {as_of.isoformat()} vs highest close {fmt.raw(r['high'], 4)} on "
                     f"{r['high_day'].isoformat()} = {fmt.pct(r['dd'])}" for r in rows) + f"; median {fmt.pct(med)} ({len(rows)} coins, {', '.join(r['key'] for r in rows)})"
    table = [{"label": name(r["coin"]) if r["coin"] in ("BTC", "ETH") else r["coin"], "value": round(r["dd"], 2), "display": fmt.pct(r["dd"], dp=0),
              "highlight": r["coin"] == "BTC", "role": "neg"} for r in rows]
    rarity = min(1.0, 0.55 + abs(med) / 200 + abs(btc["dd"]) / 400)
    return [Finding(
        key=f"drawdown_table:{as_of.isoformat()}", format="relative", detector="drawdown_table", subject="drawdown-majors",
        headline=f"Bitcoin {fmt.pct(btc['dd'])} from its high; median of {len(rows)} large coins {fmt.pct(med)}",
        numbers=nums, words=words, templates=templates, window="all stored history", series=[r["key"] for r in rows], sources=["hyperliquid"],
        as_of=as_of, calculation=calc, rarity=rarity, comparatives=["highest", "deepest"],
        chart=chart("rank_table", "Distance from the highs", "Drawdown", [{"name": "below the highest close, %", "points": pts([(r["coin"], r["dd"]) for r in rows], 2)}],
                    rows=table, sources=["hyperliquid"], as_of=as_of, hero="btc", hero_role="neg",
                    headline="Bitcoin's distance from its {btc_high} highest close ({btc_high_day}). The median of {count} major coins: {median}",
                    dek="Close on {date} vs each coin's highest close in Hyperliquid data (since {since})", y_format="pct", options={"mute_unhighlighted": False}))]
