"""Format 5, relative performance: leaderboards (7d / 30d / YTD) across the most traded Hyperliquid perps, ratio records (ETH/BTC, SOL/ETH)
computed from cleared closes, divergences from Bitcoin, and beta vs Bitcoin against its own history."""

from __future__ import annotations

import math
from datetime import date, timedelta

from app.growth import fmt
from app.growth.detectors.common import data_label, most_traded, name, percentile_rank, price, ret
from app.growth.findings import Finding, chart, n, pts
from app.growth.memory import Memory, Series

LEADERS = 50
SHOW = 5


def leaderboard(mem: Memory, as_of: date) -> list[Finding]:
    out = []
    windows = [(7, "Past {days} days"), (30, "Past {days} days")]
    ytd = (as_of - date(as_of.year, 1, 1)).days
    if as_of.month >= 2:
        windows.append((ytd + 1, "{year} so far"))  # from the last close of the previous year
    btc = price(mem, "BTC")
    for days, label in windows:
        coins = most_traded(mem, as_of, LEADERS, min_days=days + 1)
        rows = []
        for c in coins:
            s = mem.get(f"px:{c}:close")
            r = ret(s, as_of, days) if s else None
            if r:
                rows.append((c, r[0]))
        if len(rows) < 20:
            continue
        by_volume = list(rows)
        rows.sort(key=lambda x: -x[1])
        best, worst = rows[:SHOW], rows[-SHOW:][::-1]
        b_txt = " · ".join(f"{c} {fmt.pct(r, dp=0)}" for c, r in best)
        w_txt = " · ".join(f"{c} {fmt.pct(r, dp=0)}" for c, r in worst)
        up = sum(1 for _, r in rows if r > 0)
        nums = {"best": n(0, b_txt, "best performers"), "worst": n(0, w_txt, "worst performers"), "total": n(len(rows), fmt.num(len(rows)), "perps ranked"),
                "up": n(up, fmt.num(up), "perps up"), "days": n(days, str(days), "window, days"), "year": n(as_of.year, str(as_of.year), "year")}
        label_txt = label.replace("{days}", str(days)).replace("{year}", str(as_of.year))
        br = ret(btc, as_of, days) if btc else None
        extra = ""
        if br:
            nums["btc"] = n(br[0], fmt.pct(br[0], dp=0), "Bitcoin, same window")
            extra = "\nBitcoin: {btc}. Higher over the period: {up} of {total}."
        templates = [label + ", the {total} most traded Hyperliquid perps:\nBest: {best}\nWorst: {worst}" + extra,
                     label + " on Hyperliquid ({total} most traded perps):\n▲ {best}\n▼ {worst}" + extra]
        spread = max(abs(best[0][1]), abs(worst[0][1]))
        start = as_of - timedelta(days=days)
        out.append(Finding(
            key=f"leaders:{days}d:{as_of.isoformat()}", format="relative", detector="leaderboard", subject=f"leaders-{days}",
            headline=f"{label_txt}: best {best[0][0]} {fmt.pct(best[0][1])}, worst {worst[0][0]} {fmt.pct(worst[0][1])}", numbers=nums, words={},
            templates=templates, window=f"{days}d", series=[f"px:{c}:close" for c, _ in rows], sources=["hyperliquid"], as_of=as_of,
            calculation=f"return = close {as_of.isoformat()} / close {start.isoformat()} − 1 for the {len(rows)} perps with the highest 30-day average "
                        f"USD volume and a close on both days; " + "; ".join(f"{c} {r:+.2f}%" for c, r in best + worst),
            rarity=min(0.85, 0.3 + spread / 250), comparatives=["best", "worst", "most"],
            chart=_leader_chart(best, worst, by_volume, days, label, bool(br), as_of)))
        out[-1].numbers |= {"best1": n(best[0][1], fmt.pct(best[0][1], dp=0), f"{best[0][0]}, best"),
                            "worst1": n(worst[0][1], fmt.pct(worst[0][1], dp=0), f"{worst[0][0]}, worst"),
                            "start": n(0, fmt.day(start), "window starts (close)"), "date": n(0, fmt.day(as_of), "as of")}
        out[-1].words |= {"best_coin": best[0][0], "worst_coin": worst[0][0]}
    return out


def _leader_chart(best: list, worst: list, by_volume: list, days: int, label: str, has_btc: bool, as_of: date) -> dict:
    """The 5 best and 5 worst as a ranked table; variant: every ranked perp as a heat grid (most traded first)."""
    when = "in {year} so far" if "{year}" in label else "over the past {days} days"
    rows = ([{"label": c, "value": round(r, 2), "display": fmt.pct(r, dp=0), "highlight": i == 0} for i, (c, r) in enumerate(best)]
            + [{"label": c, "value": round(r, 2), "display": fmt.pct(r, dp=0), "highlight": i == len(worst) - 1} for i, (c, r) in enumerate(worst[::-1])])
    return chart("rank_table", "Best and worst", "", [{"name": "best", "points": pts(best, 2)}, {"name": "worst", "points": pts(worst, 2)}],
                 rows=rows, sources=["hyperliquid"], as_of=as_of, hero="best1", hero_role="auto",
                 headline="{best_coin}, the best of the {total} most traded Hyperliquid perps " + when + ". The worst: {worst_coin} at {worst1}",
                 dek="Return from the close on {start} to the close on {date} · the 5 best and the 5 worst" + (" · Bitcoin: {btc}" if has_btc else ""),
                 y_format="pct", options={},
                 variants={"heat_grid": {"rows": [{"label": c, "value": round(r, 2), "display": fmt.pct(r, dp=0)} for c, r in by_volume],
                                         "dek": "Return from the close on {start} to the close on {date}, the {total} most traded Hyperliquid perps, "
                                                "most traded first" + (" · Bitcoin: {btc}" if has_btc else ""),
                                         "options": {"cap": 15 if days <= 7 else 40}}})


def _ratio(a: Series, b: Series) -> list[tuple[date, float]]:
    bm = dict(zip(b.dates, b.values))
    return [(d, v / bm[d]) for d, v in zip(a.dates, a.values) if d in bm and bm[d] > 0]


def ratio_record(mem: Memory, as_of: date, pairs: tuple[tuple[str, str], ...] = (("ETH", "BTC"), ("SOL", "ETH"), ("SOL", "BTC"))) -> list[Finding]:
    out = []
    for num_c, den_c in pairs:
        a, b = price(mem, num_c), price(mem, den_c)
        if not a or not b:
            continue
        r = Series.of(f"{num_c}/{den_c}", _ratio(a.upto(as_of), b.upto(as_of)), a.source)
        if len(r) < 365 or r.last != as_of:
            continue
        v = r.values[-1]
        for low in (True, False):
            j = next((i for i in range(len(r) - 2, -1, -1) if (r.values[i] <= v if low else r.values[i] >= v)), None)
            word = "lowest" if low else "highest"
            a_n, d_n = name(num_c), name(den_c)
            nums = {"v": n(v, fmt.ratio(v), f"{num_c}/{den_c}"), "date": n(0, fmt.day(as_of), "as of")}
            if j is None:
                nums["start"] = n(0, fmt.month_year(r.first), "stored history starts")
                claim = "the " + word + " in " + data_label(a) + " since {start}"
                gap = (as_of - r.first).days
                calc_claim = f"no earlier ratio since {r.first.isoformat()} was {'lower' if low else 'higher'}"
            else:
                gap = (as_of - r.dates[j]).days
                if gap < 120:
                    continue
                nums["since"] = n(0, fmt.month_year(r.dates[j]), "last time this " + ("low" if low else "high"))
                claim = "the " + word + " since {since}"
                calc_claim = f"most recent earlier ratio {'≤' if low else '≥'} it: {r.values[j]:.6f} on {r.dates[j].isoformat()}"
            y = r.value(as_of - timedelta(days=365))
            tail = ""
            if y:
                nums["y"] = n(y, fmt.ratio(y), "a year ago")
                tail = " A year ago: {y}."
            unit = f"{den_c} per {num_c}"
            templates = [f"{a_n} priced in {d_n}: {{v}} {unit} on {{date}}, " + claim + "." + tail,
                         f"{num_c}/{den_c} closed at {{v}} ({{date}}), " + claim + "." + tail]
            look = max(0, len(r) - 1100)
            out.append(Finding(
                key=f"ratio:{num_c}{den_c}:{word}:{as_of.isoformat()}", format="relative", detector="ratio_record", subject=f"{num_c}/{den_c}",
                headline=f"{num_c}/{den_c} {v:.5g}: {word} " + (f"since {r.dates[j].isoformat()}" if j is not None else "in stored history"),
                numbers=nums, words={}, templates=templates, window="since " + (r.dates[j].isoformat() if j is not None else r.first.isoformat()),
                series=[a.key, b.key], sources=sorted({a.source, b.source}), as_of=as_of,
                calculation=f"{num_c}/{den_c} = {a.key} / {b.key} on each day both close; {as_of.isoformat()}: {a.values[-1] if a.last == as_of else a.value(as_of)} / "
                            f"{b.value(as_of)} = {v:.6f}; {calc_claim}",
                rarity=min(1.0, 0.45 + gap / 1000), comparatives=[word, "since"],
                chart=chart("ratio_line", f"{a_n} priced in {d_n}", "",
                            [{"name": f"{num_c}/{den_c}", "points": pts(list(zip(r.dates[look:], r.values[look:])), 6), "role": "context", "label": "",
                              "width": 2.6}],
                            annotations=[{"type": "marker", "x": as_of.isoformat(), "label": "{v}\nToday", "role": "neg" if low else "pos", "number": "v"}]
                            + ([{"type": "hline", "y": round(y, 6), "label": "A year ago: {y}", "role": "context"}] if y else [])
                            + ([{"type": "point_label", "x": r.dates[j].isoformat(), "label": "As " + ("low" if low else "high") + "\n{since}",
                                 "role": "context"}] if j is not None and j >= look else []),
                            sources=sorted({a.source, b.source}), as_of=as_of, hero="v", hero_role="neg" if low else "pos",
                            headline=f"{unit}: {a_n} priced in {d_n} is at " + claim.replace("the ", "its ", 1),
                            dek=f"{a_n} close divided by {d_n} close, each day both have one, in " + data_label(a),
                            y_format="ratio", options={"end_labels": False})))
    return out


def divergence(mem: Memory, as_of: date, days: int = 30) -> list[Finding]:
    btc = price(mem, "BTC")
    if not btc:
        return []
    br = ret(btc, as_of, days)
    if not br:
        return []
    cands = []
    for c in most_traded(mem, as_of, 20, min_days=days + 1):
        if c == "BTC":
            continue
        s = mem.get(f"px:{c}:close")
        r = ret(s, as_of, days) if s else None
        if r and (r[0] > 0) != (br[0] > 0):
            cands.append((abs(r[0] - br[0]), c, r))
    if not cands:
        return []
    gap, coin, r = max(cands)
    if gap < 25:
        return []
    s = mem.get(f"px:{coin}:close")
    assert s is not None
    nm = name(coin)
    nums = {"n": n(20, "20", "universe size"), "btc": n(br[0], fmt.pct(br[0]), f"Bitcoin, {days}d"), "coin": n(r[0], fmt.pct(r[0]), f"{coin}, {days}d"),
            "gap": n(gap, fmt.pts(gap), "gap, percentage points"), "days": n(days, str(days), "window, days")}
    templates = ["Past {days} days:\nBitcoin {btc}\n" + nm + " {coin}\nA {gap}-point gap, the widest among the {n} most traded Hyperliquid perps.",
                 nm + " {coin} in {days} days while Bitcoin went {btc}. That {gap}-point split is the widest among the {n} most traded Hyperliquid perps."]
    start = as_of - timedelta(days=days)

    def window(x: Series) -> list[tuple[date, float]]:
        return [(d, v) for d, v in zip(x.dates, x.values) if start <= d <= as_of]

    return [Finding(
        key=f"diverge:{coin}:{days}d:{as_of.isoformat()}", format="relative", detector="divergence", subject=coin,
        headline=f"{nm} {fmt.pct(r[0])} vs Bitcoin {fmt.pct(br[0])} in {days}d ({gap:.1f} pts)", numbers=nums, words={}, templates=templates,
        window=f"{days}d", series=[s.key, btc.key], sources=sorted({"hyperliquid", btc.source}), as_of=as_of,
        calculation=f"{coin}: {fmt.raw(r[2], 4)} / {fmt.raw(r[1], 4)} − 1 = {fmt.pct(r[0])}; BTC: {fmt.raw(br[2], 2)} / {fmt.raw(br[1], 2)} − 1 = "
                    f"{fmt.pct(br[0])} ({start.isoformat()} to {as_of.isoformat()}); widest opposite-sign gap among the 20 most traded perps",
        rarity=min(0.9, 0.3 + gap / 120), comparatives=["widest", "most"],
        chart=_divergence_chart(window(btc), window(s), nm, r[0] > br[0], sorted({"hyperliquid", btc.source}), as_of))]


def _divergence_chart(b: list[tuple[date, float]], c: list[tuple[date, float]], nm: str, coin_ahead: bool, sources: list[str], as_of: date) -> dict:
    """Both closes over the window (the renderer rebases each to 0% at the first day, so each line ends on the Finding's return);
    variant: the gap itself, the leader minus the laggard, in percentage points (ends on the Finding's gap)."""
    bm = dict(b)
    sign = 1 if coin_ahead else -1
    gap = [(d, sign * ((v / c[0][1] - 1) * 100 - (bm[d] / b[0][1] - 1) * 100)) for d, v in c if d in bm]
    end = as_of.isoformat()
    dp = 6 if c[0][1] < 100 else 2
    return chart("rebased_lines", f"{nm} vs Bitcoin", "",
                 [{"name": "Bitcoin", "points": pts(b, 2), "role": "context"}, {"name": nm, "points": pts(c, dp), "role": "highlight"}],
                 annotations=[{"type": "check", "x": end, "series": 0, "number": "btc"}, {"type": "check", "x": end, "series": 1, "number": "coin"}],
                 sources=sources, as_of=as_of, hero="{gap} pts", hero_role="highlight",
                 headline="between " + nm + " ({coin}) and Bitcoin ({btc}) in {days} days: the widest split among the {n} most traded Hyperliquid perps",
                 dek="Change since the close {days} days ago, %, daily closes", y_format="pct", options={"shade_between": [0, 1], "shade_role": "highlight"},
                 variants={"spread_lines": {
                     "series": [{"name": (nm + " minus Bitcoin") if coin_ahead else ("Bitcoin minus " + nm), "points": pts(gap, 2), "role": "highlight",
                                 "label": "gap"}],
                     "annotations": [{"type": "check", "x": end, "number": "gap"}],
                     "dek": ("{days}-day change, " + nm + " minus Bitcoin" if coin_ahead else "{days}-day change, Bitcoin minus " + nm) + ", percentage points",
                     "y_format": "pts", "options": {}}})


def _beta(x: list[float], y: list[float]) -> float | None:
    if len(x) < 30:
        return None
    mx, my = sum(x) / len(x), sum(y) / len(y)
    var = sum((a - mx) ** 2 for a in x)
    if var <= 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / var


def beta(mem: Memory, as_of: date, coins: tuple[str, ...] = ("ETH", "SOL"), window: int = 90) -> list[Finding]:
    btc = price(mem, "BTC")
    if not btc:
        return []
    bm = dict(zip(btc.dates, btc.values))
    out = []
    for coin in coins:
        s = price(mem, coin)
        if not s:
            continue
        days = [d for d in s.dates if d in bm and d <= as_of]
        sm = dict(zip(s.dates, s.values))
        rx, ry, rd = [], [], []
        for d0, d1 in zip(days, days[1:]):
            if (d1 - d0).days == 1:
                rx.append(math.log(bm[d1] / bm[d0]))
                ry.append(math.log(sm[d1] / sm[d0]))
                rd.append(d1)
        if len(rd) < window + 180 or rd[-1] != as_of:
            continue
        hist = []
        for i in range(window, len(rd) + 1, 1):
            b = _beta(rx[i - window:i], ry[i - window:i])
            if b is not None:
                hist.append((rd[i - 1], b))
        cur = hist[-1][1]
        vals = [b for _, b in hist]
        p = percentile_rank(vals, cur)
        high = p >= 0.5
        tail = (1 - p + 1 / len(vals)) if high else p
        if tail > 0.08:
            continue
        j = next((i for i in range(len(hist) - 2, -1, -1) if (hist[i][1] >= cur if high else hist[i][1] <= cur)), None)
        word = "highest" if high else "lowest"
        nm = name(coin)
        nums = {"beta": n(cur, f"{cur:.2f}", f"{window}-day beta to Bitcoin"), "window": n(window, str(window), "window, days")}
        if j is not None and (as_of - hist[j][0]).days >= 90:
            nums["since"] = n(0, fmt.month_year(hist[j][0]), "last reading this " + ("high" if high else "low"))
            claim = "the " + word + " reading since {since}"
        elif j is None:
            nums["start"] = n(0, fmt.month_year(hist[0][0]), "first reading")
            claim = "the " + word + " in " + data_label(s) + " since {start}"
        else:
            continue
        templates = ["Over the past {window} days, " + nm + " has moved {beta} times as much as Bitcoin on a typical day (beta), " + claim + ".",
                     nm + "'s {window}-day beta to Bitcoin: {beta}, " + claim + "."]
        look = max(0, len(hist) - 730)
        out.append(Finding(
            key=f"beta:{coin}:{word}:{as_of.isoformat()}", format="relative", detector="beta", subject=coin,
            headline=f"{nm} {window}d beta to BTC {cur:.2f}: {word} " + (f"since {hist[j][0].isoformat()}" if j is not None else "in stored history"),
            numbers=nums, words={}, templates=templates, window=f"{window}d", series=[s.key, btc.key], sources=sorted({s.source, btc.source}), as_of=as_of,
            calculation=f"beta = cov(r_{coin}, r_BTC) / var(r_BTC) over the last {window} daily log returns to {as_of.isoformat()} = {cur:.4f}; "
                        f"{len(hist):,} rolling readings since {hist[0][0].isoformat()}, this one is in the {'top' if high else 'bottom'} {tail * 100:.1f}%",
            rarity=max(0.0, min(1.0, 1 - tail * 6)), comparatives=[word, "since"],
            chart=chart("rolling_line", f"{nm}: {window}-day beta to Bitcoin", "",
                        [{"name": "beta", "points": pts(hist[look:], 3), "role": "context", "label": "", "width": 2.4}],
                        annotations=[{"type": "hline", "y": 1.0, "label": "Moves 1:1 with Bitcoin", "role": "context"},
                                     {"type": "marker", "x": as_of.isoformat(), "label": "{beta}\nToday", "role": "highlight", "number": "beta"}]
                        + ([{"type": "point_label", "x": hist[j][0].isoformat(), "label": "As " + ("high" if high else "low") + "\n{since}", "role": "context"}]
                           if "since" in nums and j is not None and j >= look else []),
                        sources=sorted({s.source, btc.source}), as_of=as_of, hero="beta", hero_role="highlight",
                        headline=nm + "'s {window}-day beta to Bitcoin: " + claim,
                        dek=nm + " vs Bitcoin: slope of daily log returns over a rolling {window} days (1 = moves one-for-one), in " + data_label(s),
                        y_format="corr", options={"end_labels": False})))
    return out
