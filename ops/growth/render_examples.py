"""Render the 15 "Original Intelligence" example posts from real data (design doc section 4).

Every figure is recomputed here from the fetched public data files; nothing in a graphic or a post is typed in by hand.
Output: ops/growth/examples/NN-name.png (x format), NN-name-portrait.png for three of them, and index.html (the review gallery:
graphic, post text with its character count, sources, and the calculation with its inputs — what each approval-queue draft
must show).

Run (from backend/):
    python ../ops/growth/render_examples.py --data <dir with y_*.json, cg_*.json, hl_ctx.json, fng.json>
"""

from __future__ import annotations

import argparse
import html
import json
import math
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "backend"))

from app.growth.chart_spec import Annotation, ChartSpec, Row, Series  # noqa: E402
from app.growth.render import render_info, signed  # noqa: E402

AS_OF = "25 Nov 2026 15:40 UTC"
TODAY = date(2026, 11, 25)
M = "−"


def pct(v: float, d: int = 1) -> str:
    return signed(v, d)


def ds(d: date) -> str:
    return d.isoformat()


def nice_date(d: date) -> str:
    return f"{d:%b} {d.day}, {d.year}"


# ---------------------------------------------------------------------------------------------------------------- data
class Data:
    def __init__(self, root: Path):
        self.root = root

    def j(self, name: str):
        return json.loads((self.root / name).read_text(encoding="utf-8"))

    def closes(self, sym: str) -> list[tuple[date, float]]:
        r = self.j(f"y_{sym}.json")["chart"]["result"][0]
        out = {}
        for t, v in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
            if v is None:
                continue
            out[datetime.fromtimestamp(t, timezone.utc).date()] = float(v)
        return sorted(out.items())


def ret(a: float, b: float) -> float:
    return (b / a - 1) * 100


def rolling_ret(cl: list[tuple[date, float]], n: int) -> list[tuple[date, float]]:
    return [(cl[i][0], ret(cl[i - n][1], cl[i][1])) for i in range(n, len(cl))]


def align(*series: list[tuple[date, float]]) -> tuple[list[date], list[list[float]]]:
    maps = [dict(s) for s in series]
    common = sorted(set.intersection(*(set(m) for m in maps)))
    return common, [[m[d] for d in common] for m in maps]


def corr(a: list[float], b: list[float]) -> float:
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return cov / math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))


def last_close_of_year(cl: list[tuple[date, float]], year: int) -> tuple[date, float]:
    return [p for p in cl if p[0].year == year][-1]


def last_close_on_or_before(cl: list[tuple[date, float]], d: date) -> tuple[date, float]:
    return [p for p in cl if p[0] <= d][-1]


@dataclass
class Example:
    n: int
    slug: str
    title: str
    detector: str
    spec: ChartSpec
    post: str
    sources: str
    calc: list[str]
    changes: list[str] = field(default_factory=list)
    portrait: bool = False


YF = "Yahoo Finance daily closes"
INTRADAY = "25 Nov value intraday"


def src(*parts: str) -> str:
    return "Source: " + " · ".join(parts) + f" · as of {AS_OF}"


# ---------------------------------------------------------------------------------------------------------------- examples
def ex1(D: Data) -> Example:
    dts, (xlp, xlk) = align(D.closes("XLP"), D.closes("XLK"))
    n = 21
    sp = [(dts[i], ret(xlp[i - n], xlp[i]) - ret(xlk[i - n], xlk[i])) for i in range(n, len(dts))]
    now_d, now_v = sp[-1]
    r_p, r_k = ret(xlp[-1 - n], xlp[-1]), ret(xlk[-1 - n], xlk[-1])
    ranked = sorted(sp, key=lambda t: -t[1])
    rank = [d for d, _ in ranked].index(now_d) + 1
    rec_d, rec_v = ranked[0]
    # the widest before this episode: windows ending more than 2 x 21 sessions ago
    prev_d, prev_v = max(sp[: -2 * n], key=lambda t: t[1])
    years = (dts[-1] - dts[0]).days / 365.25
    ordinal = {1: "widest", 2: "2nd-widest", 3: "3rd-widest"}.get(rank, f"{rank}th-widest")
    rec_txt = "today" if rank == 1 else f"{nice_date(rec_d).rsplit(',', 1)[0]}"
    spec = ChartSpec(
        kind="spread_lines", kicker="Sector rotation · 10-year extreme", hero=f"{now_v:.1f} pts", hero_role="highlight",
        headline=(f"Staples are beating tech by the {ordinal} margin in 10 years of daily data"
                  + ("" if rank == 1 else f". The record ({rec_v:.1f}) was set on {rec_txt}")),
        dek=f"Consumer staples (XLP) minus tech (XLK), 21-trading-day return, percentage points · {sp[0][0]:%b %Y} to now",
        series=[Series("XLP minus XLK", [round(v, 4) for _, v in sp], [ds(d) for d, _ in sp], role="highlight", label="", width=3.0)],
        annotations=[
            Annotation("marker", f"{now_v:.1f} pts\nToday", x=ds(now_d), role="highlight"),
            *([] if rank == 1 else [Annotation("point_label", f"{rec_v:.1f} pts\nRecord, {rec_txt}", x=ds(rec_d), role="context",
                                               align="left")]),
            Annotation("point_label", f"{prev_v:.1f} pts\nWidest before, {prev_d:%b %Y}", x=ds(prev_d), role="context"),
        ],
        y_format="pts", source=src(YF, INTRADAY), options={"end_labels": False},
    )
    post = (f"Defensive vs tech, past 21 trading days:\nConsumer staples {pct(r_p)}\nTech {pct(r_k)}\n"
            f"That {now_v:.1f}-point gap is the {ordinal} in 10 years of daily data"
            + ("." if rank == 1 else f", behind {rec_v:.1f} on {rec_txt}. Before this autumn, the record was {prev_v:.1f} ({prev_d:%b %Y}).")
            + "\nInvestors are hiding in toothpaste and groceries.")
    calc = [
        f"21-day return = close(t)/close(t−21 sessions) − 1. XLP: {xlp[-1]:.2f}/{xlp[-1 - n]:.2f} − 1 = {pct(r_p, 2)}; "
        f"XLK: {xlk[-1]:.2f}/{xlk[-1 - n]:.2f} − 1 = {pct(r_k, 2)} ({dts[-1 - n]} → {dts[-1]}, last close intraday).",
        f"Spread = {r_p:.2f} − ({r_k:.2f}) = {now_v:.2f} pts. Rank {rank} of {len(sp):,} daily spreads since {sp[0][0]} ({years:.1f} years).",
        f"Record: {rec_v:.2f} pts on {rec_d}. Widest before the current episode (windows ending ≥ 42 sessions ago): {prev_v:.2f} pts on {prev_d}.",
    ]
    changes = [] if rank == 1 else [f"Doc: 'the widest in 10 years'. Recomputed: today's {now_v:.1f} pts is rank {rank}; the record is "
                                    f"{rec_v:.1f} pts on {rec_d}. Wording changed to '{ordinal}'."]
    return Example(1, "rotation-record", "Rotation extreme", "rotation + record", spec, post, "Yahoo Finance (XLP, XLK)", calc, changes)


def ex2(D: Data) -> Example:
    cl = D.closes("BTC-USD")
    def peak_in(a: date, b: date) -> tuple[date, float]:
        return max((p for p in cl if a <= p[0] <= b), key=lambda p: p[1])
    cycles = [("2017–18", peak_in(date(2017, 1, 1), date(2018, 12, 31))), ("2021–22", peak_in(date(2021, 6, 1), date(2022, 12, 31)))]
    ath_d, ath_v = max(cl, key=lambda p: p[1])
    idx = {d: i for i, (d, _) in enumerate(cl)}
    horizon = 540
    series, anns, troughs = [], [], []
    for k, (lab, (pd_, pv)) in enumerate(cycles):
        seg = [(d, v) for d, v in cl if pd_ <= d and (d - pd_).days <= horizon]
        td, tv = min(((d, v) for d, v in cl if pd_ <= d and (d - pd_).days <= 600), key=lambda p: p[1])
        days = (td - pd_).days
        dd = ret(pv, tv)
        troughs.append((lab, pd_, pv, td, tv, days, dd))
        series.append(Series(f"{lab} cycle", [round(ret(pv, v), 4) for _, v in seg], [(d - pd_).days for d, _ in seg],
                             role="context", label=f"{lab}\nLow {pct(dd, 0)}, day {days}"))
        anns.append(Annotation("point_label", "", x=days, series=k, role="context"))  # the low's dot; its figures are in the end label
    cur = [(d, v) for d, v in cl if d >= ath_d]
    cur_days = (cur[-1][0] - ath_d).days
    cur_dd = ret(ath_v, cur[-1][1])
    series.append(Series("Now", [round(ret(ath_v, v), 4) for _, v in cur], [(d - ath_d).days for d, _ in cur], role="neg",
                         label=""))
    anns.append(Annotation("marker", f"{pct(cur_dd)}\nDay {cur_days}, today", x=cur_days, series=2, role="neg", align="above"))
    # still making new 12-month lows?
    last365 = [v for d, v in cl if (cl[-1][0] - d).days < 365]
    low12 = min(last365)
    low12_d = [d for d, v in cl if v == low12 and (cl[-1][0] - d).days < 365][-1]
    days_since_low = (cl[-1][0] - low12_d).days
    deeper = " · ".join(f"{pct(t[6], 0)} ({t[3].year})" for t in troughs)
    spec = ChartSpec(
        kind="drawdown_overlay", kicker="Cycle analog", hero=f"Day {cur_days}", hero_role="neg",
        headline=(f"Bitcoin's last two bear markets bottomed on day {troughs[0][5]} and day {troughs[1][5]}. "
                  f"This one is still falling"),
        dek=f"Bitcoin, % below its record close, by days since that record · {nice_date(ath_d)} record: ${ath_v:,.0f}",
        series=series, annotations=anns, y_format="pct", source=src(YF, INTRADAY),
        options={"end_values": False, "x_max": horizon},
    )
    low_txt = ("still making new 12-month lows" if days_since_low <= 7 else f"its 12-month low was {days_since_low} days ago")
    post = (f"Bitcoin's last two bear markets bottomed {troughs[0][5]} days ({troughs[0][3].year}) and {troughs[1][5]} days "
            f"({troughs[1][3].year}) after the top.\nThis one is at day {cur_days}, {abs(cur_dd):.1f}% below the ${ath_v:,.0f} record "
            f"close of {nice_date(ath_d).replace(', ', ', ')}, and {low_txt}.\nDeeper ones: {deeper.replace('-', M)}.")
    calc = [f"Cycle peak = highest close in the window; trough = lowest close within 600 days of the peak; day = trough − peak."]
    for lab, pd_, pv, td, tv, days, dd in troughs:
        calc.append(f"{lab}: peak {pd_} ${pv:,.0f} → trough {td} ${tv:,.0f} = {pct(dd)} after {days} days.")
    calc.append(f"Now: record close {ath_d} ${ath_v:,.0f}; {cl[-1][0]} ${cl[-1][1]:,.2f} (intraday) = {pct(cur_dd)} after {cur_days} days.")
    calc.append(f"12-month low: ${low12:,.0f} on {low12_d} ({days_since_low} days before the last close).")
    changes = [] if days_since_low <= 7 else [f"'still making new 12-month lows' is not supported: the 12-month low was {low12_d}"]
    return Example(2, "bear-market-length", "Bear-market length", "analog", spec, post, "Yahoo Finance (BTC-USD)", calc, changes,
                   portrait=True)


def ex3(D: Data) -> Example:
    cl = D.closes("BTC-USD")
    m = {d: v for d, v in cl}
    rs = []
    for d, v in cl:
        p = date.fromordinal(d.toordinal() - 30)
        if p in m:
            rs.append((d, ret(m[p], v)))
    now_d, now_v = rs[-1]
    worse = [r for r in rs if r[1] <= now_v]
    share = len(worse) / len(rs) * 100
    before = [r for r in worse if (now_d - r[0]).days > 30]
    last = before[-1]
    spec = ChartSpec(
        kind="histogram_marker", kicker="Rarity", hero=pct(now_v), hero_role="neg",
        headline=f"Bitcoin's 30-day fall: only {share:.1f}% of 30-day stretches since 2016 were this bad",
        dek=f"Every 30-day Bitcoin return since {rs[0][0]:%b %Y} ({len(rs):,} windows), share of windows per 5-point bin",
        series=[Series("30-day returns", [round(v, 4) for _, v in rs])],
        annotations=[Annotation("marker", f"{pct(now_v)}\nYou are here", x=round(now_v, 4), role="neg")],
        y_format="pct", source=src(YF, INTRADAY), options={"bin_width": 5, "tail": "left"},
    )
    post = (f"Bitcoin {pct(now_v)} in 30 days.\nSince 2016, only {share:.1f}% of all 30-day stretches were this bad. "
            f"The last one was {last[0]:%B %Y}.")
    calc = [
        f"30-day return = close(d)/close(d − 30 calendar days) − 1. Now: {m[now_d]:,.2f}/{m[date.fromordinal(now_d.toordinal() - 30)]:,.2f} − 1 "
        f"= {pct(now_v, 2)} ({date.fromordinal(now_d.toordinal() - 30)} → {now_d}, last close intraday).",
        f"Windows ≤ {now_v:.2f}%: {len(worse)} of {len(rs):,} = {share:.2f}% (windows since {rs[0][0]}).",
        f"Last such window more than 30 days ago: {last[0]} ({pct(last[1], 2)}).",
    ]
    return Example(3, "rarity-30d", "Rarity", "rarity", spec, post, "Yahoo Finance (BTC-USD)", calc, portrait=True)


def ex4(D: Data) -> Example:
    cl = D.closes("BTC-USD")
    years = list(range(2017, TODAY.year + 1))
    vals = []
    for y in years:
        o = last_close_on_or_before(cl, date(y, 10, 31))
        e = last_close_on_or_before(cl, date(y, 11, 30))
        vals.append((y, ret(o[1], e[1]), o, e))
    cur = vals[-1][1]
    worse_since = max((y for y, v, *_ in vals[:-1] if v <= cur), default=None)
    left = 30 - TODAY.day
    parts = " · ".join(f"{y} {pct(v, 0)}" for y, v, *_ in vals[:-1])
    spec = ChartSpec(
        kind="seasonality_bars", kicker="Seasonality", hero=pct(cur), hero_role="neg",
        headline=f"Bitcoin's worst November since {worse_since}, with {left} days left",
        dek="Bitcoin's November return by year: close on 30 Nov vs close on 31 Oct",
        series=[Series("November", [round(v, 4) for _, v, *_ in vals], [str(y) for y, *_ in vals])],
        y_format="pct", source=src(YF, INTRADAY),
        options={"partial": [str(TODAY.year)], "partial_note": "so far", "digits": 0},
    )
    post = (f"Bitcoin in November:\n{parts}\n{TODAY.year} so far: {pct(cur, 0)}, the worst November since {worse_since} with "
            f"{left} days left.")
    calc = ["November return = close(30 Nov)/close(31 Oct) − 1."] + [
        f"{y}: {e[1]:,.2f}/{o[1]:,.2f} − 1 = {pct(v, 2)}" + (" (so far, intraday)" if y == TODAY.year else "") for y, v, o, e in vals]
    return Example(4, "november-seasonality", "Seasonality", "seasonality", spec, post, "Yahoo Finance (BTC-USD)", calc)


def ex5(D: Data) -> Example:
    cl = D.closes("NVDA")
    rs = rolling_ret(cl, 21)
    now_d, now_v = rs[-1]
    before = [r for r in rs[:-30] if r[1] <= now_v]
    last = before[-1]
    spec = ChartSpec(
        kind="rolling_line", kicker="First since", hero=pct(now_v), hero_role="neg",
        headline=f"Nvidia's fastest 21-day fall since {last[0]:%B %Y}",
        dek="Nvidia (NVDA), rolling 21-trading-day return, 10 years of daily closes",
        series=[Series("NVDA 21-day return", [round(v, 4) for _, v in rs], [ds(d) for d, _ in rs], role="context", label="",
                       width=2.4)],
        annotations=[
            Annotation("hline", "", y=round(now_v, 4), role="neg"),
            Annotation("marker", f"{pct(now_v)}\nToday", x=ds(now_d), role="neg", align="left"),
            Annotation("point_label", f"{pct(last[1])}\n{last[0]:%b %Y}", x=ds(last[0]), role="context"),
        ],
        y_format="pct", source=src(YF, INTRADAY), options={"end_labels": False, "include_zero": True},
    )
    post = f"Nvidia is down {abs(now_v):.1f}% in 21 trading days.\nThe last time it fell this far this fast was {last[0]:%B %Y}."
    calc = [
        f"21-day return = close(t)/close(t−21 sessions) − 1: {cl[-1][1]:.2f}/{cl[-22][1]:.2f} − 1 = {pct(now_v, 2)} "
        f"({cl[-22][0]} → {cl[-1][0]}, intraday).",
        f"Earlier windows (ending more than 30 sessions ago) at or below that: {len(before)}; the latest ends {last[0]} ({pct(last[1], 2)}).",
    ]
    return Example(5, "nvidia-first-since", "First-since", "first_since", spec, post, "Yahoo Finance (NVDA)", calc)


def ex6(D: Data) -> Example:
    dts, (b, n) = align(D.closes("BTC-USD"), D.closes("IXIC"))
    rb = [b[i] / b[i - 1] - 1 for i in range(1, len(dts))]
    rn = [n[i] / n[i - 1] - 1 for i in range(1, len(dts))]
    W, Y = 60, 252
    roll = [(dts[i], corr(rb[i - W:i], rn[i - W:i])) for i in range(W, len(rb) + 1)]
    now_d, now_v = roll[-1]
    prior = corr(rb[-W - Y:-W], rn[-W - Y:-W])
    start = date(TODAY.year - 3, TODAY.month, 1)
    shown = [r for r in roll if r[0] >= start]
    spec = ChartSpec(
        kind="rolling_line", kicker="Divergence", hero=f"{now_v:.2f}", hero_role="highlight",
        headline=f"Bitcoin is trading like a tech stock again: its 60-day correlation with the Nasdaq, up from {prior:.2f} the year before",
        dek="Correlation of daily returns, Bitcoin vs Nasdaq Composite, rolling 60 trading days",
        series=[Series("60-day correlation", [round(v, 4) for _, v in shown], [ds(d) for d, _ in shown], role="context", label="",
                       width=3.2)],
        annotations=[
            Annotation("hline", f"Year before: {prior:.2f}", y=round(prior, 4), role="context"),
            Annotation("marker", f"{now_v:.2f}\nNow", x=ds(now_d), role="highlight", align="left"),
        ],
        y_format="corr", source=src(YF, INTRADAY), options={"end_labels": False},
    )
    post = (f"Bitcoin is trading like a tech stock again.\n60-day correlation with the Nasdaq: {now_v:.2f}. The year before: "
            f"{prior:.2f}.\nWhen AI sells off, crypto now goes with it.")
    calc = [
        f"Daily simple returns on {len(dts):,} common trading dates (Bitcoin taken on Nasdaq session dates).",
        f"Now: Pearson correlation of the last {W} daily return pairs ({dts[-W]} → {dts[-1]}) = {now_v:.3f}.",
        f"Year before: correlation of the {Y} return pairs before that window ({dts[-W - Y]} → {dts[-W - 1]}) = {prior:.3f}.",
    ]
    changes = ["Doc said 'the average over the year before: 0.47'; it is the correlation over the 252 sessions before the window "
               f"({prior:.3f}); the average of the rolling 60-day values over that year is "
               f"{statistics.mean(v for _, v in roll[-W - Y:-W]):.2f}. Post text says 'The year before'."]
    return Example(6, "btc-nasdaq-correlation", "Divergence", "divergence", spec, post, "Yahoo Finance (BTC-USD, ^IXIC)", calc,
                   changes)


def ex7(D: Data) -> Example:
    h = D.j("hl_ctx.json")
    MIN_OI = 5e6
    rows = []
    for u, x in zip(h[0]["universe"], h[1]):
        if u.get("isDelisted"):
            continue
        oi = float(x["openInterest"]) * float(x["markPx"])
        if oi < MIN_OI:
            continue
        rows.append((float(x["funding"]) * 24 * 365 * 100, u["name"], oi, float(x["funding"])))
    rows.sort()
    top = rows[:10]
    top4 = top[:4]
    spec = ChartSpec(
        kind="rank_table", kicker="Positioning extreme", hero=pct(top[0][0], 0), hero_role="neg",
        headline="On Hyperliquid, shorts are paying longs to stay bearish",
        dek=f"Most negative funding, annualised from the current hourly rate (it resets every hour) · perps with ≥ $5M open interest",
        rows=[Row(n, round(f, 4), sublabel=f"OI ${oi / 1e6:,.0f}M", display=pct(f, 0), highlight=i < 4)
              for i, (f, n, oi, _) in enumerate(top)],
        y_format="pct", source=src("Hyperliquid metaAndAssetCtxs (instantaneous funding)"),
        options={"mute_unhighlighted": True},
    )
    lst = " · ".join(f"{n} {pct(f, 0)}" for f, n, *_ in top4)
    post = (f"On Hyperliquid, shorts are paying longs to stay bearish:\n{lst} (funding, annualized at the current hourly rate)\n"
            "Crowded shorts at extreme funding have a history of sharp squeezes. They don't always come.")
    calc = [f"Annualised funding = hourly funding × 24 × 365. Universe: {len(rows)} listed perps with open interest ≥ $5M "
            "(OI × mark price)."] + [f"{n}: {fr:.7f}/h × 8,760 = {pct(f, 1)} (OI ${oi / 1e6:,.1f}M)" for f, n, oi, fr in top4]
    changes = ["Doc did not state a filter; the doc's four (ADA, XLM, kPEPE, DOT) are reproduced with OI ≥ $5M. Without a filter, "
               "illiquid perps (e.g. BIGTIME −4,380%) dominate."]
    return Example(7, "funding-extremes", "Positioning extreme", "positioning", spec, post, "Hyperliquid", calc, changes)


STABLE_OR_PEGGED = {
    # USD/EUR stablecoins and yield-bearing tokenised cash/T-bill funds
    "usdt", "usdc", "usds", "usde", "dai", "usd1", "rlusd", "usdg", "usdy", "pyusd", "usyc", "eursafo", "usdf", "u", "buidl", "usdd",
    "bfusd", "ousd", "usdgo", "gho", "ustb", "jaaa", "usd0", "eurc", "tusd", "eutbl",
    # gold-backed tokens and a tokenised loan book (track gold / a loan pool, not crypto)
    "xaut", "paxg", "figr_heloc",
}


def ex8(D: Data) -> Example:
    c = D.j("cg_top100.json")
    coins = [x for x in c if x["symbol"] not in STABLE_OR_PEGGED and x.get("price_change_percentage_7d_in_currency") is not None]
    up = [x for x in coins if x["price_change_percentage_7d_in_currency"] > 0]
    down = [x for x in coins if x["price_change_percentage_7d_in_currency"] < 0]
    excl = [x["symbol"].upper() for x in c if x["symbol"] in STABLE_OR_PEGGED]
    spec = ChartSpec(
        kind="heat_grid", kicker="Breadth", hero=f"{len(up)} of {len(coins)}", hero_role="neg",
        headline=f"top crypto coins are up this week. {len(down)} are down: it isn't just Bitcoin",
        dek=f"7-day change, CoinGecko top 100 by market cap, ordered by size · {len(excl)} stablecoins, tokenised cash and gold tokens excluded",
        rows=[Row(x["symbol"].upper(), round(x["price_change_percentage_7d_in_currency"], 4),
                  display=pct(x["price_change_percentage_7d_in_currency"], 0),
                  highlight=x["price_change_percentage_7d_in_currency"] > 0) for x in coins],
        y_format="pct", source=src("CoinGecko markets (7-day change)"), options={"cap": 20},
    )
    post = (f"Crypto breadth this week: {len(up)} of the top {len(coins)} coins (stablecoins, tokenised cash and gold tokens "
            f"excluded) are up.\n{len(down)} are down. This isn't a Bitcoin problem, it's everywhere.")
    calc = [f"CoinGecko top 100 by market cap; excluded {len(excl)}: {', '.join(excl)}.",
            f"Up over 7 days: {len(up)} ({', '.join(x['symbol'].upper() for x in up)}). Down: {len(down)}."]
    changes = [f"Doc: '11 of the top 83 … 72 down'. With every pegged asset excluded (tokenised T-bill funds and gold tokens too) "
               f"it is {len(up)} of {len(coins)}, {len(down)} down: the doc's extra 'up' coins were pegged tokens drifting up."]
    return Example(8, "breadth-grid", "Breadth", "ranking", spec, post, "CoinGecko", calc, changes)


def ex9(D: Data) -> Example:
    f = D.j("fng.json")["data"]
    pts = sorted((datetime.fromtimestamp(int(x["timestamp"]), timezone.utc).date(), int(x["value"]), x["value_classification"]) for x in f)
    now_d, now_v, now_c = pts[-1]
    low = [p for p in pts if p[1] <= now_v]
    share = len(low) / len(pts) * 100
    spec = ChartSpec(
        kind="series_with_marker", kicker="Sentiment rarity", hero=str(now_v), hero_role="neg",
        headline=f"Crypto Fear & Greed is in “{now_c}”: this low on just {share:.1f}% of days since {pts[0][0].year}",
        dek=f"Daily readings, {pts[0][0]:%b %Y} to today (0 = extreme fear, 100 = extreme greed)",
        series=[Series("Fear & Greed", [float(v) for _, v, _ in pts], [ds(d) for d, _, _ in pts], role="context", label="",
                       width=1.6)],
        annotations=[
            Annotation("band", f"{now_v} or lower: {len(low)} of {len(pts):,} days", y=0, y2=now_v, role="neg"),
            Annotation("marker", f"{now_v}\nToday", x=ds(now_d), role="neg"),
        ],
        y_format="index", source=src("alternative.me Fear & Greed Index"),
        options={"end_labels": False, "y_min": 0, "y_max": 100, "highlight_below": now_v,
                 "hero_note": "Fear & Greed Index: alternative.me"},
    )
    post = (f"Crypto Fear & Greed: {now_v}, “{now_c}”.\nSince {pts[0][0].year} the index has been this low on just "
            f"{share:.1f}% of days ({len(low)} of {len(pts):,}).")
    calc = [f"Daily readings {pts[0][0]} → {now_d}: {len(pts):,}. Readings ≤ {now_v}: {len(low)} → {share:.2f}%."]
    return Example(9, "fear-greed-rarity", "Sentiment rarity", "rarity", spec, post, "alternative.me", calc)


YTD_SET = [("CLF", "Oil (WTI)"), ("XLE", "Energy stocks"), ("XLP", "Staples"), ("XLV", "Healthcare"), ("GSPC", "S&P 500"),
           ("IXIC", "Nasdaq"), ("GCF", "Gold"), ("BTC-USD", "Bitcoin"), ("ETH-USD", "Ether"), ("SIF", "Silver"), ("SOL-USD", "Solana"),
           ("MSTR", "Strategy"), ("COIN", "Coinbase")]


def ex10(D: Data) -> Example:
    vals = []
    calc = ["YTD = last close (25 Nov, intraday) / last close of 2025 − 1."]
    for sym, name in YTD_SET:
        cl = D.closes(sym)
        e = last_close_of_year(cl, TODAY.year - 1)
        v = ret(e[1], cl[-1][1])
        vals.append((v, name, sym))
        calc.append(f"{name} ({sym}): {cl[-1][1]:,.2f}/{e[1]:,.2f} ({e[0]}) − 1 = {pct(v, 2)}")
    vals.sort(reverse=True)
    spec = ChartSpec(
        kind="rank_table", kicker="2026 leaderboard", hero=pct(vals[0][0]), hero_role="pos",
        headline=f"{vals[0][1].split(' (')[0]} leads 2026. The old economy is beating crypto and tech",
        dek="Year-to-date return, last close of 2025 to now · futures for oil, gold and silver",
        rows=[Row(n, round(v, 4), display=pct(v)) for v, n, _ in vals], y_format="pct", source=src(YF, INTRADAY),
    )
    short = {"Oil (WTI)": "Oil"}
    post = "2026 so far:\n" + " · ".join(f"{short.get(n, n)} {pct(v)}" for v, n, _ in vals) + "\nThe old economy is winning."
    return Example(10, "ytd-leaderboard", "YTD leaderboard", "ranking", spec, post, "Yahoo Finance (13 tickers)", calc, portrait=True)


def ex11(D: Data) -> Example:
    mstr = D.closes("MSTR")
    btc = dict(D.closes("BTC-USD"))
    win = mstr[-22:]
    dts = [d for d, _ in win]
    bv = [btc[d] for d in dts]
    r_m, r_b = ret(win[0][1], win[-1][1]), ret(bv[0], bv[-1])
    ratio = r_m / r_b
    spec = ChartSpec(
        kind="rebased_lines", kicker="Leverage on leverage", hero=pct(r_m), hero_role="neg",
        headline=f"Strategy (MSTR) fell {ratio:.1f}× as far as Bitcoin in 21 trading days",
        dek=f"Change since {nice_date(dts[0])}, daily closes (Bitcoin taken on the stock's trading days)",
        series=[Series("Bitcoin", [round(v, 4) for v in bv], [ds(d) for d in dts], role="context"),
                Series("Strategy (MSTR)", [round(v, 4) for _, v in win], [ds(d) for d in dts], role="neg")],
        y_format="pct", source=src(YF, INTRADAY), options={"shade_between": [0, 1], "shade_role": "neg"},
    )
    post = (f"Past 21 trading days:\nBitcoin {pct(r_b)}\nStrategy (MSTR) {pct(r_m)}\nThe stock built to be “leveraged "
            "Bitcoin” is doing exactly that, in reverse.")
    calc = [f"MSTR: {win[-1][1]:.2f}/{win[0][1]:.2f} − 1 = {pct(r_m, 2)} ({dts[0]} → {dts[-1]}).",
            f"BTC on the same dates: {bv[-1]:,.2f}/{bv[0]:,.2f} − 1 = {pct(r_b, 2)}. Ratio {r_m:.2f}/{r_b:.2f} = {ratio:.2f}×."]
    return Example(11, "mstr-vs-btc", "Leverage on leverage", "divergence", spec, post, "Yahoo Finance (MSTR, BTC-USD)", calc)


def ex12(D: Data) -> Example:
    dts, (b, g) = align(D.closes("BTC-USD"), D.closes("GCF"))
    ratio = [(d, bb / gg) for d, bb, gg in zip(dts, b, g)]
    start = date(TODAY.year - 1, TODAY.month, TODAY.day)
    yr = [r for r in ratio if r[0] >= start]
    a_d, a_v = yr[0]
    now_d, now_v = yr[-1]
    gcl = D.closes("GCF")
    g_ytd = ret(last_close_of_year(gcl, TODAY.year - 1)[1], gcl[-1][1])
    spec = ChartSpec(
        kind="ratio_line", kicker="Ratio", hero=f"{now_v:.1f} oz", hero_role="highlight",
        headline=f"One Bitcoin buys {now_v:.1f} ounces of gold, down from {a_v:.1f} a year ago, even with gold {'down' if g_ytd < 0 else 'up'} {abs(g_ytd):.0f}% this year",
        dek="Bitcoin price divided by the gold futures price (troy ounces per Bitcoin), past 12 months",
        series=[Series("Ounces of gold per Bitcoin", [round(v, 4) for _, v in yr], [ds(d) for d, _ in yr], role="highlight",
                       label="oz per BTC")],
        annotations=[Annotation("hline", f"A year ago: {a_v:.1f} oz", y=round(a_v, 4), role="context")],
        y_format="oz", source=src(YF + " (BTC-USD, gold futures GC=F)", INTRADAY), options={"end_values": True},
    )
    post = (f"One Bitcoin buys {now_v:.1f} ounces of gold today, down from {a_v:.1f} a year ago, even though gold itself is down "
            f"{abs(g_ytd):.0f}% this year.")
    calc = [f"Ratio = BTC close / gold futures close on common dates.",
            f"Now ({now_d}, intraday): {b[-1]:,.2f}/{g[-1]:,.2f} = {now_v:.2f} oz.",
            f"A year ago (first common date ≥ {start}: {a_d}): {a_v:.2f} oz.",
            f"Gold YTD: {pct(g_ytd, 2)}."]
    return Example(12, "btc-in-gold", "Ratio", "rotation", spec, post, "Yahoo Finance (BTC-USD, GC=F)", calc)


def ex13(D: Data) -> Example:
    btc = D.closes("BTC-USD")
    nas = D.closes("IXIC")
    b_pk = max(btc, key=lambda p: p[1])
    n_pk = max(nas, key=lambda p: p[1])
    bs = [(d, ret(b_pk[1], v)) for d, v in btc if d >= b_pk[0]]
    ns = [(d, ret(n_pk[1], v)) for d, v in nas if d >= n_pk[0]]
    same = (b_pk[0].month, b_pk[0].day) == (n_pk[0].month, n_pk[0].day)
    spec = ChartSpec(
        kind="drawdown_overlay", kicker="Two tops, one date", hero=f"{n_pk[0]:%b} {n_pk[0].day}", hero_role="highlight",
        headline=f"Bitcoin peaked {nice_date(b_pk[0])}. The Nasdaq peaked {nice_date(n_pk[0])}",
        dek="% below each record close, daily",
        series=[Series("Bitcoin", [round(v, 4) for _, v in bs], [ds(d) for d, _ in bs], role="neg"),
                Series("Nasdaq", [round(v, 4) for _, v in ns], [ds(d) for d, _ in ns], role="highlight")],
        annotations=[Annotation("point_label", f"Bitcoin record\n{nice_date(b_pk[0])}", x=ds(b_pk[0]), series=0, role="context",
                                align="right"),
                     Annotation("point_label", f"Nasdaq record\n{nice_date(n_pk[0])}", x=ds(n_pk[0]), series=1, role="context",
                                align="below")],
        y_format="pct", source=src(YF, INTRADAY), options={"x_dates": True, "end_values": True},
    )
    post = (f"Two tops, one date:\nBitcoin peaked {nice_date(b_pk[0])}. The Nasdaq peaked {nice_date(n_pk[0])}.\n"
            f"Since then: Nasdaq {pct(ns[-1][1])}, Bitcoin {pct(bs[-1][1])}.")
    calc = [f"Bitcoin record close {b_pk[0]} ${b_pk[1]:,.0f}; now {btc[-1][1]:,.2f} → {pct(bs[-1][1], 2)}.",
            f"Nasdaq record close {n_pk[0]} {n_pk[1]:,.2f}; now {nas[-1][1]:,.2f} (intraday) → {pct(ns[-1][1], 2)}.",
            f"Same calendar date: {same}."]
    return Example(13, "two-tops-one-date", "Coincidence chart", "record + then_now", spec, post, "Yahoo Finance (BTC-USD, ^IXIC)",
                   calc)


def ex14(D: Data) -> Example:
    cl = D.closes("IXIC")
    rs = rolling_ret(cl, 1)
    now_d, now_v = rs[-1]
    events = [r for r in rs if r[1] <= now_v]
    prior = [r for r in events if r[0] != now_d]
    yrs = (cl[-1][0] - cl[0][0]).days / 365.25
    worst = min(rs, key=lambda r: r[1])
    spec = ChartSpec(
        kind="move_card", kicker="Daily rarity · intraday", hero=pct(now_v), hero_role="neg",
        headline=f"Nasdaq today. In 10 years there have been only {len(prior)} sessions this bad or worse",
        dek=f"Every Nasdaq Composite session down {abs(now_v):.1f}% or more since {cl[0][0]:%b %Y} · today is intraday",
        series=[Series("Sessions ≤ today", [round(v, 4) for _, v in events], [ds(d) for d, _ in events], role="context")],
        annotations=[Annotation("marker", f"{pct(now_v)}\nToday", x=ds(now_d), role="neg", align="left"),
                     Annotation("point_label", f"{pct(worst[1])}\n{nice_date(worst[0])}", x=ds(worst[0]), role="context")],
        y_format="pct", source=src(YF, "25 Nov value intraday (about 10:40 New York)"),
        options={"threshold": round(now_v, 4), "x_min": ds(cl[0][0]), "x_max": ds(cl[-1][0])},
    )
    post = f"Nasdaq {pct(now_v)} today (intraday).\nIn 10 years there have been {len(prior)} sessions this bad or worse."
    calc = [f"1-day return = close(t)/close(t−1) − 1: {cl[-1][1]:,.2f}/{cl[-2][1]:,.2f} − 1 = {pct(now_v, 2)} (intraday).",
            f"Earlier sessions ≤ {now_v:.2f}% from {cl[1][0]} ({yrs:.1f} years): {len(prior)}. Worst: {worst[0]} ({pct(worst[1], 2)})."]
    changes = [f"Doc: '30 sessions this bad or worse' counted today itself; earlier sessions: {len(prior)} (today included: {len(events)})."]
    return Example(14, "nasdaq-daily-rarity", "Daily rarity", "rarity", spec, post, "Yahoo Finance (^IXIC)", calc, changes)


def ex15(D: Data) -> Example:
    t = D.j("cg_trending.json")["coins"]
    items = [(x["item"]["name"], x["item"]["symbol"], x["item"].get("market_cap_rank")) for x in t]
    ranked = [i for i in items if i[2]]
    tiny = [i for i in ranked if i[2] > 750]
    fng = sorted(D.j("fng.json")["data"], key=lambda x: int(x["timestamp"]))[-1]
    spec = ChartSpec(
        kind="rank_table", kicker="Search heat", hero=f"{len(tiny)} of {len(items)}", hero_role="highlight",
        headline="coins traders are searching on CoinGecko today rank outside the top 750",
        dek="CoinGecko trending searches (last 24 hours), each coin's market-cap rank, log scale",
        rows=[Row(sym.upper(), float(r), sublabel=name if name.upper() != sym.upper() else None, display=f"#{r:,}",
                  highlight=r > 750, role="highlight" if r > 750 else "context") for name, sym, r in ranked],
        y_format="rank", source=src("CoinGecko trending searches and market-cap ranks"),
        options={"scale": "log", "ref_value": 750, "ref_label": "Rank 750"},
    )
    post = (f"What crypto traders are searching today: {len(tiny)} of CoinGecko's {len(items)} trending coins rank outside the top "
            f"750 by market cap.\nIn a market in {fng['value_classification']}, attention is going to tiny caps, not majors.")
    calc = [f"CoinGecko /search/trending: {len(items)} coins; with a market-cap rank: {len(ranked)}.",
            "Ranks: " + ", ".join(f"{s} #{r}" for _, s, r in ranked) + ".",
            f"Outside the top 750: {', '.join(s for _, s, _ in tiny)}. Fear & Greed today: {fng['value']} ({fng['value_classification']})."]
    changes = [f"Doc: '3 of CoinGecko's 10 trending coins'. The feed lists {len(items)}; the count is {len(tiny)} of {len(items)}."]
    return Example(15, "search-heat", "Search heat", "narrative_velocity", spec, post, "CoinGecko", calc, changes)


LIGHT_SAMPLES = (11,)  # also rendered in the light theme (dark is the default: stronger on X's dark timeline)
EXAMPLES = [ex1, ex2, ex3, ex4, ex5, ex6, ex7, ex8, ex9, ex10, ex11, ex12, ex13, ex14, ex15]


# ---------------------------------------------------------------------------------------------------------------- gallery
def x_len(text: str) -> int:
    return len(text)  # no links in these drafts: X counts each character as 1 (no CJK/emoji here)


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Original Intelligence examples</title>
<style>
:root{{--bg:#0b0d11;--card:#12151b;--line:#232833;--text:#eef0f3;--muted:#8d95a3;--ok:#1ed882;--bad:#ff5252;--hi:#ffba2e}}
@media (prefers-color-scheme: light){{:root:not([data-theme="dark"]){{--bg:#f6f4ef;--card:#fff;--line:#e2ddd2;--text:#14161b;--muted:#646b76}}}}
.banner{{background:#3a2a00;color:#ffd27a;border-bottom:1px solid #6b4e00;padding:10px 16px;text-align:center;font-weight:700;font-size:14px}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 Inter,system-ui,-apple-system,Segoe UI,sans-serif}}
header{{max-width:1180px;margin:0 auto;padding:40px 16px 8px}} h1{{margin:0 0 6px;font-size:28px;letter-spacing:-.01em}}
header p{{color:var(--muted);margin:4px 0;max-width:820px}}
main{{max-width:1180px;margin:0 auto;padding:8px 16px 64px;display:grid;gap:28px}}
article{{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}}
.head{{display:flex;gap:12px;align-items:baseline;padding:16px 20px;border-bottom:1px solid var(--line)}}
.n{{font-weight:800;color:var(--hi)}} .head h2{{margin:0;font-size:18px}} .det{{color:var(--muted);font-size:13px;margin-left:auto}}
.imgs{{display:grid;grid-template-columns:1fr;gap:12px;padding:16px 20px 0}} .imgs.p{{grid-template-columns:minmax(0,2.2fr) minmax(0,1fr);align-items:start}} .imgs.two{{grid-template-columns:1fr 1fr}}
.imgs img{{width:100%;height:auto;border-radius:8px;border:1px solid var(--line);display:block}}
.body{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.2fr);gap:20px;padding:16px 20px 20px}}
@media (max-width:760px){{.body,.imgs.p,.imgs.two{{grid-template-columns:1fr}}}}
h3{{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:0 0 6px}}
.post{{white-space:pre-wrap;background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:0}}
.count{{font-size:13px;color:var(--muted);margin-top:6px}} .count b{{color:var(--ok)}} .count b.over{{color:var(--bad)}}
ul{{margin:0;padding-left:18px}} li{{margin:2px 0;overflow-wrap:anywhere}} .chg li{{color:var(--hi)}} .src{{color:var(--muted);font-size:13px;margin-top:10px}}
</style></head><body>
<div class="banner">Internal preview — figures from unlicensed sources (Yahoo, CoinGecko free); production drafts use licensed sources only</div>
<header><h1>Original Intelligence: 15 example posts</h1>
<p>Rendered by <code>app.growth.render</code> from <code>app.growth.chart_spec</code> specs. Every figure, in the graphic and in the post, is recomputed by
<code>ops/growth/render_examples.py</code> from public data fetched {as_of}. Drafts for review: nothing here has been posted.</p>
<p>Each card shows what an approval-queue draft must show: the graphic, the exact post text with its character count, the sources, and the calculation with its inputs.
Amber notes mark where a figure or wording differs from the design document.</p></header>
<main>{cards}</main></body></html>"""


def card(e: Example, imgs: list[str]) -> str:
    n = x_len(e.post)
    li = "".join(f"<li>{html.escape(c)}</li>" for c in e.calc)
    chg = ("<h3 style='margin-top:14px'>Differs from the design doc</h3><ul class='chg'>" +
           "".join(f"<li>{html.escape(c)}</li>" for c in e.changes) + "</ul>") if e.changes else ""
    im = "".join(f'<img src="{i}" alt="{html.escape(e.title)} graphic" loading="lazy">' for i in imgs)
    return (f'<article id="ex{e.n}"><div class="head"><span class="n">{e.n:02d}</span><h2>{html.escape(e.title)}</h2>'
            f'<span class="det">detector: {html.escape(e.detector)} · chart: {html.escape(e.spec.kind)}</span></div>'
            f'<div class="imgs{" p" if any("portrait" in i for i in imgs) else (" two" if len(imgs) > 1 else "")}">{im}</div><div class="body"><div><h3>Post text</h3>'
            f'<pre class="post">{html.escape(e.post)}</pre><div class="count"><b class="{"over" if n > 280 else ""}">{n}</b> / 280 characters</div>'
            f'<div class="src"><b>Sources:</b> {html.escape(e.sources)}<br>{html.escape(e.spec.source)}</div></div>'
            f'<div><h3>Calculation</h3><ul>{li}</ul>{chg}</div></div></article>')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default=str(HERE / "examples"))
    ap.add_argument("--only", type=int, nargs="*")
    ap.add_argument("--theme", default=None)
    a = ap.parse_args()
    D = Data(Path(a.data))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cards = []
    for fn in EXAMPLES:
        e = fn(D)
        if a.only and e.n not in a.only:
            continue
        if a.theme:
            e.spec.theme = a.theme
        imgs = []
        variants = [("x", None, "")] + ([("portrait", None, "-portrait")] if e.portrait else [])
        if e.n in LIGHT_SAMPLES:  # the alternative theme, for comparison
            variants.append(("x", "light", "-light"))
        for fmt, theme, suffix in variants:
            png, meta = render_info(e.spec, fmt, theme)
            name = f"{e.n:02d}-{e.slug}{suffix}.png"
            (out / name).write_bytes(png)
            imgs.append(name)
            print(f"{name}: {len(png) / 1024:.0f} KB overlaps={meta['label_overlaps']} phone_min={meta['phone_min_text_px']} "
                  f"post={x_len(e.post)}")
        if x_len(e.post) > 280:
            print(f"  !! post {e.n} is {x_len(e.post)} characters")
        cards.append(card(e, imgs))
        (out / f"{e.n:02d}-{e.slug}.spec.json").write_text(json.dumps(e.spec.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    if not a.only:
        (out / "index.html").write_text(PAGE.format(as_of=AS_OF, cards="\n".join(cards)), encoding="utf-8")


if __name__ == "__main__":
    main()
