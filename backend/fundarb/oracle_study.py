"""Module 4, rule 4: do the HIP-3 equity oracles go stale outside US market hours? Measured, not assumed.

Hyperliquid settles funding against the ORACLE, and publishes each settled hour's average premium
(fundingHistory.premium = the hour's average of (impact price - oracle) / oracle). If an oracle stops moving while the perp
keeps trading, the premium has to absorb the perp's whole move: the hourly change in premium tracks the perp's hourly
return with slope ~1. If the oracle tracks the underlying, the premium is noise around zero and the slope is ~0. A slope in
between means the oracle partly follows the perp's own price (self-referential), which makes premium-derived funding
unreliable without freezing.

Method (public endpoints only, verified live 2026-10-25):
- history: for the top FUNDARB-selected equity/index markets by 24h volume (no list in code) plus the highest-volume native
  crypto perp as a 24/7 control: fundingHistory over `days` (paged, 500 rows a call) and 1h candleSnapshot. For each pair of
  consecutive hours, d(premium) is regressed on log(avg price) change, avg price = (o + h + l + c) / 4 to match the
  premium's hourly averaging. Grouped by fundarb.sessions session.
- live: `samples` snapshots of metaAndAssetCtxs `every` seconds apart. Per market: share of samples where the oracle
  changed, and the correlation of oracle changes with the same / previous interval's mark change (an oracle that follows
  the mark lags it). Only the session the run happens in is observed; stored, printed, and not used for the verdict.

Verdict per session (median slope over the study markets): <= FUNDARB_ORACLE_TRACKS_BETA "tracks"; >= FUNDARB_ORACLE_FROZEN_BETA
"frozen"; in between "mark-influenced". The control's slope is printed next to it: if the control itself is not ~0, the
method is broken and the policy is not written. The result is stored in SQLite (oracle_studies) and encoded as the policy
file the equity bucket reads (FUNDARB_ORACLE_POLICY_PATH), with the study id as its citation."""

from __future__ import annotations

import asyncio
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from fundarb.sessions import SESSIONS, SessionClock
from fundarb.universe import AssetClass, Market
from fundarb.venues import HyperliquidVenue

FUNDING_PAGE = 500


async def premium_history(hl: HyperliquidVenue, coin: str, days: float) -> tuple[dict[int, float], dict[int, float]]:
    """({unix hour h: average premium over hour h}, {h: average price over hour h}). A settlement stamped at the top of
    hour h+1 averages the premium over hour h; a candle stamped h covers hour h."""
    now = int(time.time() * 1000)
    start = now - int(days * 86400 * 1000)
    prem: dict[int, float] = {}
    t0 = start
    while True:
        rows = await hl._info({"type": "fundingHistory", "coin": coin, "startTime": t0})
        for r in rows or []:
            if r.get("premium") is not None:
                prem[int(r["time"]) // 3_600_000 - 1] = float(r["premium"])
        if not rows or len(rows) < FUNDING_PAGE:
            break
        t0 = int(rows[-1]["time"]) + 1
    candles = await hl._info({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1h", "startTime": start, "endTime": now}})
    avg = {int(c["t"]) // 3_600_000: (float(c["o"]) + float(c["h"]) + float(c["l"]) + float(c["c"])) / 4 for c in candles or []}
    return prem, avg


def session_rows(prem: dict[int, float], avg: dict[int, float], clock: SessionClock) -> dict[str, list[tuple[float, float, float]]]:
    """Session -> [(d premium, log return of the hourly average price, premium)] over consecutive hours."""
    out: dict[str, list[tuple[float, float, float]]] = {}
    hours = sorted(set(prem) & set(avg))
    for a, b in zip(hours, hours[1:]):
        if b != a + 1 or avg[a] <= 0 or avg[b] <= 0:
            continue
        out.setdefault(clock.session(b * 3600 + 1800), []).append((prem[b] - prem[a], math.log(avg[b] / avg[a]), prem[b]))
    return out


def session_stats(rows: list[tuple[float, float, float]], min_hours: int = 24) -> dict[str, Any]:
    if len(rows) < min_hours:
        return {"hours": len(rows), "beta": None, "corr": None, "median_abs_premium_bps": None, "return_sd_bps": None}
    dp, rt, p = (np.array(x) for x in zip(*rows))
    denom = float(np.dot(rt, rt))
    sd = float(rt.std())
    return {"hours": len(rows), "beta": round(float(np.dot(dp, rt)) / denom, 4) if denom > 0 else None,
            "corr": round(float(np.corrcoef(dp, rt)[0, 1]), 4) if sd > 0 and float(dp.std()) > 0 else None,
            "median_abs_premium_bps": round(float(np.median(np.abs(p))) * 1e4, 3), "return_sd_bps": round(sd * 1e4, 3)}


def verdict(beta: float | None, tracks: float, frozen: float) -> str:
    if beta is None:
        return "unmeasured"
    return "tracks" if abs(beta) <= tracks else "frozen" if beta >= frozen else "mark-influenced"


def summarize(per_market: dict[str, dict[str, dict[str, Any]]], control: str | None, tracks: float, frozen: float) -> dict[str, Any]:
    """Pure: per-market session stats -> per-session verdicts over the study markets (the control excluded)."""
    out: dict[str, Any] = {}
    for s in SESSIONS:
        betas = [st[s]["beta"] for m, st in per_market.items() if m != control and st.get(s, {}).get("beta") is not None]
        corrs = [st[s]["corr"] for m, st in per_market.items() if m != control and st.get(s, {}).get("corr") is not None]
        prem = [st[s]["median_abs_premium_bps"] for m, st in per_market.items() if m != control and st.get(s, {}).get("beta") is not None]
        med = round(statistics.median(betas), 4) if betas else None
        cb = (per_market.get(control or "", {}).get(s) or {}).get("beta")
        out[s] = {"verdict": verdict(med, tracks, frozen), "markets": len(betas), "beta_median": med,
                  "beta_range": [min(betas), max(betas)] if betas else None, "corr_median": round(statistics.median(corrs), 4) if corrs else None,
                  "median_abs_premium_bps": round(statistics.median(prem), 3) if prem else None, "control_beta": cb,
                  "hours": sum(st.get(s, {}).get("hours", 0) for m, st in per_market.items() if m != control)}
    return out


def method_valid(sessions: dict[str, Any], tracks: float) -> tuple[bool, str]:
    cbs = [v["control_beta"] for v in sessions.values() if v["control_beta"] is not None]
    if not cbs:
        return False, "no control measurement: the method cannot be checked"
    worst = max(abs(b) for b in cbs)
    if worst > tracks:
        return False, f"the 24/7 crypto control shows slope {worst:+.3f} (> {tracks}): the method itself is off"
    return True, f"control slope within +-{worst:.3f} in every session (an oracle that tracks reads ~0)"


def _rule(session: str, v: str, stale_stop_mult: float) -> dict[str, Any]:
    if v == "tracks":
        return {"verdict": v, "funding_reliable": True, "stop_mult": 1.0, "entries": "preferred" if session == "rth" else "allowed"}
    # stale, frozen or never measured: premium-derived funding is not trusted and new entries wait for a tracked session
    return {"verdict": v, "funding_reliable": False, "stop_mult": stale_stop_mult, "entries": "blocked"}


def policy(sessions: dict[str, Any], stale_stop_mult: float) -> dict[str, dict[str, Any]]:
    """Pure: the study-wide verdicts -> what the equity bucket does in each session. A holiday (no separate measurement
    unless the calendar marked holidays) inherits the weekend's verdict when its own is unmeasured."""
    out: dict[str, dict[str, Any]] = {}
    for s in SESSIONS:
        v = sessions[s]["verdict"]
        if v == "unmeasured" and s == "holiday":
            v = sessions["weekend"]["verdict"]
        out[s] = _rule(s, v, stale_stop_mult)
    return out


def market_policy(per_market: dict[str, dict[str, dict[str, Any]]], control: str | None, tracks: float, frozen: float,
                  stale_stop_mult: float) -> dict[str, dict[str, dict[str, Any]]]:
    """Pure: each studied market's own verdict per session. The study-wide median can hide one market whose underlying
    trades in another time zone (its oracle goes stale inside US hours); a market's own measurement wins for that market."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for m, st in per_market.items():
        if m == control:
            continue
        out[m] = {s: _rule(s, verdict((st.get(s) or {}).get("beta"), tracks, frozen), stale_stop_mult)
                  for s in SESSIONS if (st.get(s) or {}).get("beta") is not None}
    return out


ENTRY_ORDER = {"preferred": 0, "allowed": 1, "blocked": 2}


def leg_policy(doc: dict[str, Any], session: str, names: list[str]) -> dict[str, Any]:
    """The strictest rule over a trade's legs: a leg with its own measurement uses it, the others the study-wide one."""
    rules = [((doc.get("markets") or {}).get(n) or {}).get(session) or doc["policy"][session] for n in names]
    worst = max(rules, key=lambda r: (ENTRY_ORDER[r["entries"]], r["stop_mult"]))
    stale = [n for n, r in zip(names, rules) if not r["funding_reliable"]]
    return {**worst, "funding_reliable": not stale, "stop_mult": max(r["stop_mult"] for r in rules), "stale_legs": stale}


async def live_sample(hl: HyperliquidVenue, names: list[str], samples: int, every: float) -> dict[str, dict[str, Any]]:
    dexes = sorted({n.split(":", 1)[0] if ":" in n else "" for n in names})
    want = set(names)
    snaps: list[dict[str, tuple[float, float]]] = []
    for i in range(samples):
        row: dict[str, tuple[float, float]] = {}
        for d in dexes:
            meta, ctxs = await hl._info({"type": "metaAndAssetCtxs", "dex": d})
            for u, c in zip(meta["universe"], ctxs):
                if u["name"] in want and c.get("oraclePx") and c.get("markPx"):
                    row[u["name"]] = (float(c["oraclePx"]), float(c["markPx"]))
        snaps.append(row)
        if i < samples - 1:
            await asyncio.sleep(every)
    out: dict[str, dict[str, Any]] = {}
    for n in names:
        seq = [s[n] for s in snaps if n in s]
        if len(seq) < 3:
            out[n] = {"samples": len(seq)}
            continue
        o = np.diff(np.log([x[0] for x in seq]))
        m = np.diff(np.log([x[1] for x in seq]))

        def corr(a: np.ndarray, b: np.ndarray) -> float | None:
            return round(float(np.corrcoef(a, b)[0, 1]), 3) if len(a) > 2 and a.std() > 0 and b.std() > 0 else None

        out[n] = {"samples": len(seq), "oracle_changed_share": round(float(np.mean(o != 0)), 3), "mark_changed_share": round(float(np.mean(m != 0)), 3),
                  "corr_same": corr(o, m), "corr_oracle_follows_mark": corr(o[1:], m[:-1]), "corr_mark_follows_oracle": corr(m[1:], o[:-1])}
    return out


def study_markets(markets: list[Market], n: int) -> tuple[list[str], str | None]:
    """The n highest-volume live equity/index markets in the equity bucket (pairing-eligible: an operator-excluded index,
    e.g. a crypto dominance index, is not an equity), and the highest-volume native crypto perp as the control."""
    eq = [m for m in markets if m.listing_live and m.pairing_eligible and m.asset_class in (AssetClass.EQUITY, AssetClass.INDEX)]
    eq.sort(key=lambda m: -(m.volume_24h_usd or 0))
    ctl = max((m for m in markets if m.listing_live and m.namespace == "" and m.asset_class is AssetClass.CRYPTO),
              key=lambda m: m.volume_24h_usd or 0, default=None)
    return [m.raw_name for m in eq[:n]], ctl.raw_name if ctl else None


async def run_study(settings: Any, markets: list[Market], clock: SessionClock, *, n_markets: int, days: float, samples: int,
                    every: float, transport: Any = None) -> dict[str, Any]:
    names, control = study_markets(markets, n_markets)
    hl = HyperliquidVenue(settings, transport)
    await hl.connect()
    try:
        return await _study(hl, settings, clock, names, control, days, samples, every)
    finally:
        await hl.disconnect()


async def _study(hl: HyperliquidVenue, settings: Any, clock: SessionClock, names: list[str], control: str | None, days: float,
                 samples: int, every: float) -> dict[str, Any]:
    t0 = time.monotonic()
    per: dict[str, dict[str, dict[str, Any]]] = {}
    sem = asyncio.Semaphore(4)

    async def one(name: str) -> None:
        async with sem:
            prem, avg = await premium_history(hl, name, days)
        rows = session_rows(prem, avg, clock)
        per[name] = {s: session_stats(rows.get(s, [])) for s in SESSIONS}

    await asyncio.gather(*(one(n) for n in [*names, *([control] if control else [])]))
    sessions = summarize(per, control, settings.oracle_tracks_beta, settings.oracle_frozen_beta)
    ok, why = method_valid(sessions, settings.oracle_tracks_beta)
    now = datetime.now(timezone.utc)
    live = await live_sample(hl, [*names, *([control] if control else [])], samples, every) if samples >= 3 else {}
    return {"study_id": now.strftime("o%Y%m%dT%H%M%S"), "measured_at": now.isoformat(), "days": days,
            "markets": names, "control": control, "per_market": per, "sessions": sessions, "method_valid": ok, "method_check": why,
            "holidays_known": bool(clock.holidays), "live": {"session": clock.session(now), "samples": samples, "every_s": every, "markets": live},
            "thresholds": {"tracks_beta": settings.oracle_tracks_beta, "frozen_beta": settings.oracle_frozen_beta},
            "calls": hl.calls, "elapsed_s": round(time.monotonic() - t0, 1)}


def write_policy(path: Path, study: dict[str, Any], stale_stop_mult: float) -> dict[str, Any]:
    doc = {"_doc": "Written by `fundarb oracle-study` (fundarb.oracle_study). The equity bucket reads it; re-run the study to "
                   "refresh it. Every session's policy cites the measurement it came from.",
           "citation": {"study_id": study["study_id"], "measured_at": study["measured_at"], "days": study["days"],
                        "markets": study["markets"], "control": study["control"], "method_check": study["method_check"],
                        "holidays_known": study["holidays_known"]},
           "measurement": study["sessions"], "policy": policy(study["sessions"], stale_stop_mult),
           "markets": market_policy(study["per_market"], study["control"], study["thresholds"]["tracks_beta"], study["thresholds"]["frozen_beta"],
                                    stale_stop_mult)}
    Path(path).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


def load_policy(path: Path) -> dict[str, Any] | None:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def unmeasured_policy(stale_stop_mult: float) -> dict[str, Any]:
    """No study on file: only regular hours are trusted, and that trust is labelled an assumption."""
    pol = {s: {"verdict": "unmeasured", "funding_reliable": False, "stop_mult": stale_stop_mult, "entries": "blocked"} for s in SESSIONS}
    pol["rth"] = {"verdict": "unmeasured (assumed tracking in regular hours)", "funding_reliable": True, "stop_mult": 1.0, "entries": "preferred"}
    return {"citation": None, "measurement": None, "policy": pol, "markets": {}}
