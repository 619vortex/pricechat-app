"""Phase 1 CLI (read-only).

  python -m fundarb quotes BTC-PERP ETH-PERP [--venues hyperliquid,lighter] [--save] [--json]
  python -m fundarb intervals [--symbol BTC-PERP]
  python -m fundarb gates [--watch] [--replay RUN_ID] [--json]
  python -m fundarb scan [--top 20] [--replay SCAN_ID|latest] [--json]   (Module 3: crypto pairs, both ranked lists)
  python -m fundarb scan-legacy ...                                       (the Phase 2 four-venue scan)
  python -m fundarb universe [--live-only] [--dex xyz] [--class EQUITY] [--csv out.csv] [--json] [--accept-schema] [--watch]
  python -m fundarb oracle-study [--markets 12] [--days 30] [--samples 20] [--every 15] [--no-policy] [--show STUDY_ID]
  python -m fundarb equity [--top 20] [--replay RUN_ID|latest]          (Module 4: index vs constituents, its own bucket)

Every venue answers or says why it couldn't; nothing is ever shown as a silent zero. (`scan` and `status` come in
Phases 2 and 3.)"""

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any

import numpy as np

from fundarb.config import load
from fundarb.history import QuoteStore
from fundarb.models import VenueQuote
from fundarb.venues import VENUES, PerpVenue


def _venues(names: str | None) -> list[PerpVenue]:
    s = load()
    chosen = [n.strip() for n in names.split(",")] if names else list(VENUES)
    unknown = [n for n in chosen if n not in VENUES]
    if unknown:
        raise SystemExit(f"unknown venue(s): {', '.join(unknown)} (choose from {', '.join(VENUES)})")
    return [VENUES[n](s) for n in chosen]


async def _one(v: PerpVenue, symbols: list[str]) -> list[tuple[str, str, VenueQuote | str]]:
    out: list[tuple[str, str, VenueQuote | str]] = []
    try:
        await v.connect()
    except Exception as exc:  # noqa: BLE001 - report the venue, keep the others
        return [(v.name, s, f"connect failed: {exc}") for s in symbols]
    try:
        for s in symbols:
            try:
                out.append((v.name, s, await v.quote(s)))
            except Exception as exc:  # noqa: BLE001
                out.append((v.name, s, f"{type(exc).__name__}: {exc}"))
    finally:
        await v.disconnect()
    return out


def _fmt(q: VenueQuote) -> list[str]:
    fees = f"{q.maker_bps:.2f}/{q.taker_bps:.2f}" if q.maker_bps is not None and q.taker_bps is not None else "n/a"
    return [q.venue, q.symbol, f"{q.funding_rate_raw:+.6%}", f"{q.funding_interval_hours:g}h", f"{q.funding_apr:+.2%}", q.funding_kind,
            f"{q.mark_price:,.6g}", f"{q.index_price:,.6g}", f"{q.bid:,.6g}/{q.ask:,.6g}",
            f"${q.depth_usd_1pct:,.0f}{'' if q.depth_complete else '+'}", fees]


def cmd_quotes(a: argparse.Namespace) -> int:
    venues = _venues(a.venues)
    results = [r for rs in asyncio.run(_gather(venues, a.symbols)) for r in rs]
    quotes = [q for _, _, q in results if isinstance(q, VenueQuote)]
    if a.json:
        print(json.dumps([asdict(q) if isinstance(q, VenueQuote) else {"venue": v, "symbol": s, "error": q} for v, s, q in results],
                         default=str, indent=1))
    else:
        head = ["venue", "symbol", "rate/interval", "interval", "APR", "kind", "mark", "index", "bid/ask", "depth±1%", "fee m/t bps"]
        rows = [_fmt(q) for q in quotes]
        widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(head)]
        print("  ".join(h.ljust(w) for h, w in zip(head, widths)))
        for r in rows:
            print("  ".join(c.ljust(w) for c, w in zip(r, widths)))
        for v, s, q in results:
            if not isinstance(q, VenueQuote):
                print(f"FAILED  {v} {s}: {q}")
        for q in quotes:
            if q.maker_bps is None:
                print(f"note    {q.venue}: fees {q.fee_source}")
        if any(not q.depth_complete for q in quotes):
            print("note    '+' = the fetched book ended inside the ±1% band, so depth is at least this much")
    if a.save and quotes:
        print(f"saved {QuoteStore(load().sqlite_path).save(quotes)} quotes to {load().sqlite_path}")
    return 0 if quotes else 1


async def _gather(venues: list[PerpVenue], symbols: list[str]) -> list[Any]:
    return await asyncio.gather(*(_one(v, symbols) for v in venues))


def cmd_intervals(a: argparse.Namespace) -> int:
    async def run() -> list[str]:
        lines = []
        for v in _venues(a.venues):
            try:
                await v.connect()
                f = await v.get_funding_rate(a.symbol)
                lines.append(f"{v.name:12s} {f.interval_hours:g} h   ({f.interval_source})")
            except Exception as exc:  # noqa: BLE001
                lines.append(f"{v.name:12s} FAILED: {exc}")
            finally:
                await v.disconnect()
        return lines
    print("\n".join(asyncio.run(run())))
    return 0


def cmd_scan(a: argparse.Namespace) -> int:
    from fundarb.scan import scan
    from fundarb.signals import SignalConfig

    cfg = SignalConfig(leverage=a.leverage, buffer=a.buffer, expected_hold_hours=a.hold)
    r = asyncio.run(scan(load(), cfg, top=a.top, shortlist=a.shortlist, venues=a.venues.split(",") if a.venues else None))
    if a.json:
        print(json.dumps(r, indent=1, default=str))
        return 0
    f = lambda v, d=1, suf="%": "—" if v is None else f"{v:+.{d}f}{suf}"  # noqa: E731
    head = ["#", "perp", "short → long", "gross", "capital", "7d mean ± std", "z", "paid", "score", "net cap", "b/e h", "depth"]
    rows = []
    for i, o in enumerate(r["opportunities"], 1):
        mean = "—" if o["mean_apr_7d"] is None else f"{o['mean_apr_7d']:+.1f} ± {o['std_apr_7d']:.1f}%"
        rows.append([str(i) + ("x" if o["rejected"] else ""), o["symbol"].replace("-PERP", ""), f"{o['short']} → {o['long']}", f(o["gross_apr"]),
                     f(o["capital_apr"]), mean, f(o["z"], 1, "σ"), "—" if o["paid_share"] is None else f"{o['paid_share'] * 100:.0f}%", f(o["score"]),
                     f(o["net_capital_apr"]), "—" if o["breakeven_hours"] is None else f"{o['breakeven_hours']:.0f}",
                     "—" if o["depth_usd"] is None else f"${o['depth_usd']:,.0f}"])
    widths = [max(len(h), *(len(r_[i]) for r_ in rows)) if rows else len(h) for i, h in enumerate(head)]
    print("  ".join(h.ljust(w) for h, w in zip(head, widths)))
    for row in rows:
        print("  ".join(c.ljust(w) for c, w in zip(row, widths)))
    print(f"\nAPRs per year of one leg's notional; capital = per year of capital posted ({cfg.leverage:g}x per leg + {cfg.buffer:.0%} buffer on both legs). "
          f"score = capital-adjusted 7d mean − {cfg.stability_k:g}σ. x = rejected.")
    print(f"shortlisted {r['shortlisted']} · persistent spreads {r['persistent']} · transient {r['transient']}")
    for o in r["opportunities"]:
        for note in ([f"rejected: {o['rejected']}"] if o["rejected"] else []) + o["flags"]:
            print(f"  {o['symbol']}: {note}")
    for n, e in r["errors"].items():
        print(f"FAILED {n}: {e}")
    return 0


def _universe_table(snap: Any, rows: list[Any]) -> None:
    from collections import Counter

    def money(x: float | None) -> str:
        return "-" if x is None else f"${x / 1e6:,.1f}M" if x >= 1e6 else f"${x / 1e3:,.0f}k"

    hdr = ["market", "dex", "class", "coll", "funding/int", "int", "APR", "mark", "basis bp", "OI", "vol 24h", "maxLev", "margin",
           "impact spr bp", "OI cap", "live"]
    out = [hdr]
    for m in rows:
        out.append([m.raw_name, m.namespace or "native", m.asset_class.value, m.collateral_token,
                    "-" if m.funding_rate_raw is None else f"{m.funding_rate_raw:+.6%}",
                    "-" if m.funding_interval_hours is None else f"{m.funding_interval_hours:g}h",
                    "-" if m.funding_apr is None else f"{m.funding_apr:+.1%}", "-" if m.mark_price is None else f"{m.mark_price:,.6g}",
                    "-" if m.mark_oracle_basis_bps is None else f"{m.mark_oracle_basis_bps:+.1f}", money(m.open_interest_usd),
                    money(m.volume_24h_usd), f"{m.max_leverage:g}x", ("iso " if m.only_isolated else "") + m.margin_mode,
                    "-" if m.spread_bps is None else f"{m.spread_bps:.1f}", money(m.oi_cap_usd), "yes" if m.listing_live else "DELISTED"])
    widths = [max(len(str(r[i])) for r in out) for i in range(len(hdr))]
    for r in out:
        print("  ".join(str(v).ljust(w) for v, w in zip(r, widths)))
    allm = snap.markets
    live = [m for m in allm if m.listing_live]
    print()
    print(f"{len(allm)} markets on {len(snap.dexes)} dexes ({len(live)} live, {len(allm) - len(live)} delisted); "
          f"{snap.calls} API calls in {snap.elapsed_s:.2f}s; shown: {len(rows)}")
    print("by dex (live/all markets):")
    for d, i in snap.dexes.items():
        every = "?" if i["interval_hours"] is None else f"{i['interval_hours']:g}h"
        print(f"  {d or 'native'}: {i['full_name']}, margin {i['collateral_token']}, funding every {every}, "
              f"{sum(1 for m in live if m.namespace == d)}/{i['markets']}")
    print("by class (live):", dict(Counter(m.asset_class.value for m in live).most_common()))
    print("class rules (live):", dict(Counter(m.class_rule for m in live).most_common()))
    unc = sorted(m.raw_name for m in live if m.asset_class.value == "UNCLASSIFIED")
    if unc:
        print(f"UNCLASSIFIED live markets ({len(unc)}): {', '.join(unc)}")
    for k, v in snap.failures.items():
        print(f"FAILED {k}: {v}")
    sc = snap.schema
    print(f"schema: {sc.status} (hash {sc.hash[:12]}{'' if sc.confirmed_hash in (None, sc.hash) else f', confirmed {sc.confirmed_hash[:12]}'})"
          f"{'' if snap.ranking_allowed else ': ranking REFUSED until a human runs `fundarb universe --accept-schema`'}")
    for k, v in sc.added.items():
        print(f"  schema added   {k}: {v}")
    for k, v in sc.removed.items():
        print(f"  schema removed {k}: {v}")


def cmd_universe(a: argparse.Namespace) -> int:
    from fundarb.universe import HyperliquidUniverse, market_row

    uni = HyperliquidUniverse(load())

    def show(snap: Any) -> None:
        rows = [m for m in snap.markets if (m.listing_live or not a.live_only) and (a.dex is None or (m.namespace or "native") == a.dex)
                and (a.asset_class is None or m.asset_class.value == a.asset_class.upper())]
        rows.sort(key=lambda m: (m.namespace, m.asset_class.value, -(m.volume_24h_usd or 0)))
        if a.csv:
            import csv
            with open(a.csv, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(market_row(rows[0])) if rows else ["raw_name"])
                w.writeheader()
                w.writerows(market_row(m) for m in rows)
        if a.json:
            print(json.dumps({"markets": [market_row(m) for m in rows], "dexes": snap.dexes, "failures": snap.failures,
                              "schema": {"status": snap.schema.status, "hash": snap.schema.hash, "added": snap.schema.added,
                                         "removed": snap.schema.removed}, "calls": snap.calls, "elapsed_s": snap.elapsed_s}, indent=2))
        else:
            _universe_table(snap, rows)
        if a.accept_schema and snap.schema.status != "ok":
            uni.guard.accept(snap.schema)
            print(f"schema {snap.schema.hash[:12]} confirmed and saved to {uni.guard.path}")

    if a.watch:
        asyncio.run(uni.run(show))
        return 0
    snap = asyncio.run(uni.fetch())
    show(snap)
    return 0 if len(snap.markets) > 100 and not snap.failures else 1


async def _gates_once(uni: Any, store: Any, cfg: Any) -> dict[str, Any] | None:
    """One gate run: universe -> cheap gates -> books for the survivors -> every gate -> persisted. None if the schema
    isn't confirmed (gating on an unconfirmed schema is how a renamed field becomes a column of zeroes)."""
    from fundarb.gates import cheap_pass, market_inputs, new_run_id, read_books, run_gates
    from fundarb.universe import market_row

    t0 = time.monotonic()
    snap = await uni.fetch()
    if not snap.ranking_allowed:
        print(f"REFUSED: universe schema is {snap.schema.status} (hash {snap.schema.hash[:12]}); added {snap.schema.added}, "
              f"removed {snap.schema.removed}. A human must run `fundarb universe --accept-schema` first.")
        return None
    ts = snap.fetched_at.isoformat()
    by = {m.raw_name: m for m in snap.markets}
    hist = store.basis_history(by, before_ts=ts)
    pre = [market_inputs(m, None, None, hist[m.raw_name], cfg) for m in snap.markets]
    cands = [x["raw_name"] for x in pre if cheap_pass(x, cfg)]
    books, book_err, book_calls = await read_books(uni.settings, cands, min_depth=cfg.min_depth_usd)
    inputs = [market_inputs(m, books.get(m.raw_name), book_err.get(m.raw_name), hist[m.raw_name], cfg) for m in snap.markets]
    rep = run_gates(inputs, cfg, new_run_id(), ts, {m.raw_name: m.volume_24h_usd or 0 for m in snap.markets})
    store.save_markets(ts, ({**market_row(m), "pairing_eligible": int(m.pairing_eligible), "listing_live": int(m.listing_live)}
                            for m in snap.markets))

    def quote(m: Any) -> dict[str, Any]:
        b = books.get(m.raw_name)
        return {"ts": ts, "raw_name": m.raw_name, "funding_rate_raw": m.funding_rate_raw, "funding_interval_hours": m.funding_interval_hours,
                "funding_apr": m.funding_apr, "premium": m.premium, "mark_price": m.mark_price, "oracle_price": m.oracle_price,
                "basis_bps": m.mark_oracle_basis_bps, "open_interest_usd": m.open_interest_usd, "volume_24h_usd": m.volume_24h_usd,
                "max_leverage": m.max_leverage, "spread_bps": b.spread_bps if b else m.spread_bps,
                "spread_source": "l2Book top of book" if b else m.spread_source, "depth_usd_1pct": b.depth_usd_1pct if b else None,
                "depth_source": b.source if b else None}

    store.save_quotes(quote(m) for m in snap.markets if m.listing_live)
    elapsed = round(time.monotonic() - t0, 2)
    store.save_gate_run({"run_id": rep.run_id, "ts": ts, "schema_hash": snap.schema.hash, "config": asdict(cfg), "counts": rep.counts,
                         "elapsed_s": elapsed, "calls": snap.calls + book_calls}, rep.results)
    return {"report": rep, "snap": snap, "books": books, "book_errors": book_err, "candidates": cands, "calls": snap.calls + book_calls,
            "elapsed_s": elapsed}


def _num(v: Any) -> Any:
    return f"{v:,.2f}" if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def _print_gates(out: dict[str, Any], store: Any) -> None:
    from collections import Counter

    from fundarb.gates import GATES

    rep, snap = out["report"], out["snap"]
    by = {m.raw_name: m for m in snap.markets}
    print(f"gate run {rep.run_id}  {rep.ts}")
    print(f"markets: {rep.total} total, {sum(1 for m in snap.markets if m.listing_live)} live; order books read for "
          f"{len(out['candidates'])} that passed the cheap gates ({len(out['book_errors'])} book failures)")
    print(f"{'gate':<18}{'pass':>6}{'fail':>6}{'not measured':>14}")
    for g in GATES:
        c = rep.counts[g]
        print(f"{g:<18}{c['pass']:>6}{c['fail']:>6}{c['not_measured']:>14}")
    print(f"PASSED ALL GATES: {len(rep.passed)}")
    res = {r["raw_name"]: r for r in rep.results}
    for name in sorted(rep.passed, key=lambda n: -(by[n].volume_24h_usd or 0)):
        m, r = by[name], res[name]
        ch = {c["gate"]: c for c in r["checks"]}
        print(f"  {name:<14}{m.asset_class.value:<10}{m.capital_bucket:<34}vol ${(m.volume_24h_usd or 0) / 1e6:>8,.1f}M"
              f"  OI ${(m.open_interest_usd or 0) / 1e6:>7,.1f}M  spread {ch['spread']['value']:>5.2f}bp"
              f"  depth1% ${ch['depth']['value'] / 1e6:>6,.2f}M  {'; '.join(r['flags'])}")
    print(f"capital buckets among the passers: {dict(Counter(by[n].capital_bucket.split(':')[0] for n in rep.passed))}; "
          f"by account: {dict(Counter(by[n].capital_bucket for n in rep.passed))}")
    print(f"passers by class: {dict(Counter(by[n].asset_class.value for n in rep.passed))}")
    print("TOP 20 REJECTS (by 24h volume), first gate failed:")
    for r in rep.top_rejects:
        print(f"  {r['raw_name']:<16}{r['gate']:<18}value {_num(r.get('value'))}  limit {_num(r.get('limit'))}  {r.get('note') or ''}")
    excluded = sorted(n for n, m in by.items() if not m.pairing_eligible and m.listing_live)
    if excluded:
        print("excluded from pairing (live): " + "; ".join(f"{n} ({by[n].pairing_note})" for n in excluded))
    fl = Counter(f.split(" (")[0].split(" ~")[0] for r in rep.results for f in r["flags"])
    if fl:
        print(f"flags: {dict(fl)}")
    for n, e in list(out["book_errors"].items())[:10]:
        print(f"BOOK FAILED {n}: {e}")
    for k, v in snap.failures.items():
        print(f"FAILED {k}: {v}")
    print(f"cost: {out['calls']} API calls, {out['elapsed_s']:.2f}s wall clock; store {store.path}: {store.counts()}")


def _replay(store: Any, run_id: str | None) -> int:
    from fundarb.gates import GateConfig, decide

    if run_id is None:
        print("no stored gate run to replay")
        return 1
    run, results = store.load_gate_run(run_id)
    cfg = GateConfig(**run["config"])
    diff = [r["raw_name"] for r in results if decide(r["inputs"], cfg) !=
            {"passed": bool(r["passed"]), "failed_gate": r["failed_gate"], "checks": r["checks"], "flags": r["flags"]}]
    print(f"replayed gate run {run_id}: {len(results)} markets, {len(results) - len(diff)} identical, {len(diff)} different"
          + (f": {', '.join(diff[:20])}" if diff else ""))
    return 0 if not diff else 1


def cmd_gates(a: argparse.Namespace) -> int:
    from fundarb.gates import GateConfig
    from fundarb.store import UniverseStore
    from fundarb.universe import HyperliquidUniverse

    s = load()
    store = UniverseStore(s.universe_db_path)
    if a.replay:
        return _replay(store, store.latest_gate_run() if a.replay == "latest" else a.replay)
    uni, cfg = HyperliquidUniverse(s), GateConfig.from_settings(s)

    async def loop() -> int:
        while True:
            out = await _gates_once(uni, store, cfg)
            if out is None:
                return 2
            if a.json:
                r = out["report"]
                print(json.dumps({"run_id": r.run_id, "ts": r.ts, "total": r.total, "counts": r.counts, "passed": r.passed,
                                  "top_rejects": r.top_rejects, "calls": out["calls"], "elapsed_s": out["elapsed_s"]}, indent=2, default=str))
            else:
                _print_gates(out, store)
            if not a.watch:
                return 0
            await asyncio.sleep(s.universe_refresh_seconds)
    return asyncio.run(loop())


def _oracle_state(r: dict[str, Any]) -> str:
    note = next((c["note"] for c in r["checks"] if c["gate"] == "oracle"), "")
    return "ENFORCING" if note.startswith("ENFORCING") else "WARMING"


def _pct(v: Any, d: int = 1) -> str:
    return "-" if v is None else f"{v:+.{d}f}%"


def _usd_m(v: Any) -> str:
    return "-" if v is None else f"${v / 1e6:.2f}M"


def _scan_rows(title: str, rows: list[dict[str, Any]], top: int) -> None:
    print(f"\n{title}")
    hdr = ["#", "pair", "short leg", "long leg", "gross APR", "7d avg", "net APR", "capital APR", "capital", "breakeven", "depth short / long",
           "spread sd", "score", "intervals", "oracle"]
    out = [hdr]
    for i, r in enumerate(rows[:top], 1):
        m, p = r["metrics"], r["inputs"]
        s, l = p["short"], p["long"]
        iv = "/".join("spot" if x["kind"] == "spot" else f"{x['interval_hours']:g}h" for x in (s, l))
        be = m["breakeven_hours"]
        out.append([str(i), p["symbol"].replace("-PERP", ""), f"{s['venue']}:{s['dex'] or '-'}" if s["venue"] == "hyperliquid" else s["venue"],
                    f"{l['venue']}:{l['dex'] or '-'}" if l["venue"] == "hyperliquid" else l["venue"], _pct(m["gross_apr_pct"]),
                    _pct(m["mean_7d_apr_pct"]), _pct(m["net_apr_pct"]),
                    _pct(m["capital_apr_pct"]), f"{m['capital_mode']} {m['capital_per_notional']:.3f}",
                    "-" if be is None else "never" if be == float("inf") else f"{be:.1f}h", f"{_usd_m(s['depth_usd_1pct'])} / {_usd_m(l['depth_usd_1pct'])}",
                    "-" if m["spread_stdev_bps"] is None else f"{m['spread_stdev_bps']:.1f}bp", "-" if m["score"] is None else f"{m['score']:.2f}", iv,
                    "PROVISIONAL" if m["oracle"] == "provisional" else "enforced"])
    widths = [max(len(r[i]) for r in out) for i in range(len(hdr))]
    for r in out:
        print("  ".join(v.ljust(w) for v, w in zip(r, widths)))


def _print_scan(scan_id: str, res: dict[str, Any], lists: dict[str, list[dict[str, Any]]], gate: dict[str, Any], top: int) -> None:
    from collections import Counter

    ranked = lists["all_ranked"]
    provisional = any(r["metrics"]["oracle"] == "provisional" for r in ranked)
    print(f"scan {scan_id}  (gate run {gate['report'].run_id})" + ("   *** ORACLE GATE PROVISIONAL: fewer than the enforcing sample count ***"
                                                                    if provisional else ""))
    pairs = res["pairs"]
    kinds = Counter(p["kind"] for p in pairs)
    every = [*ranked, *lists["transient"], *lists["unprofitable"], *lists["net_unknown"], *lists["rejected"]]
    modes = Counter(r["metrics"]["capital_mode"] for r in every)
    print(f"legs: {len(res['legs'])} ({sum(1 for x in res['legs'].values() if x['venue'] == 'hyperliquid' and x['kind'] == 'perp')} Hyperliquid perps that "
          f"passed every gate, the rest their second legs); clusters: {len({m['cluster'] for m in res['clusters']['members']})} over "
          f"{res['clusters']['legs']} legs at corr >= {res['clusters']['threshold']} (stored, not printed)")
    print(f"candidate pairs: {len(pairs)} ({dict(kinds)}); capital model split: {dict(modes)} "
          f"(cross = both legs in one Hyperliquid account; every cross-venue pair is two accounts, so isolated)")
    print(f"ranked: {len(ranked)}  unprofitable after costs: {len(lists['unprofitable'])}  transient: {len(lists['transient'])}  "
          f"net unknown (fees): {len(lists['net_unknown'])}  "
          f"rejected: {len(lists['rejected'])}  dropped (no second leg): {len(res['dropped'])}")
    _scan_rows(f"TOP {top} BY CAPITAL APR (net APR on the capital both legs tie up)", lists["capital_apr"], top)
    _scan_rows(f"TOP {top} BY VARIANCE-ADJUSTED SCORE (capital APR / max(price-spread stdev bps, 1))", lists["score"], top)
    if lists["unprofitable"]:
        _scan_rows("BEST OF THE UNPROFITABLE (net APR <= 0 after round-trip costs over the hold; stored, not ranked)", lists["unprofitable"], 5)
    rej = Counter(r["metrics"]["reject_code"] for r in lists["rejected"])
    print(f"\nrejected by reason: {dict(rej.most_common())}")
    for code in [c for c, _ in rej.most_common()]:
        ex = next(r for r in lists["rejected"] if r["metrics"]["reject_code"] == code)
        print(f"  e.g. {ex['pair_id']}: {ex['metrics']['reject']}")
    nu = Counter(f"{r['inputs']['short']['venue']}/{r['inputs']['long']['venue']}" for r in lists["net_unknown"])
    if nu:
        print(f"net unknown (a venue serves fees only to a signed-in account): {dict(nu)}; gross APRs stored")
    if lists["transient"]:
        print(f"transient (spread not paid in each of the last windows; stored for study, never ranked): "
              f"{', '.join(r['pair_id'] for r in lists['transient'][:10])}{' ...' if len(lists['transient']) > 10 else ''}")
    print(f"dropped, no second leg: {', '.join(d['symbol'].replace('-PERP', '') for d in res['dropped']) or 'none'}")
    if res["dropped"]:
        print(f"  ({res['dropped'][0]['reason']})")
    others = Counter(m.asset_class.value for m in gate["snap"].markets if m.raw_name in gate["report"].passed and m.asset_class.value != "CRYPTO")
    print(f"not paired here: {dict(others)} passed the gates but are not crypto (Phase D equity bucket; never mixed into these lists)")
    for k, v in res["notes"].items():
        print(f"VENUE {k}: {v}")


async def _scan_once(s: Any, top: int) -> int:
    from fundarb.gates import GateConfig
    from fundarb.pairs import PairConfig, config_dict, gather_inputs, rank
    from fundarb.store import UniverseStore
    from fundarb.universe import HyperliquidUniverse, load_spot_aliases

    store = UniverseStore(s.universe_db_path)
    t0 = time.monotonic()
    gate = await _gates_once(HyperliquidUniverse(s), store, GateConfig.from_settings(s))
    if gate is None:
        return 2
    def depth(r: dict[str, Any]) -> dict[str, Any]:
        c = next(c for c in r["checks"] if c["gate"] == "depth")
        return {"depth_usd_1pct": c["value"] if c["ok"] is not None else None, "depth_source": c["note"]}

    results = {r["raw_name"]: {"passed": r["passed"], "oracle_state": _oracle_state(r), **depth(r)} for r in gate["report"].results}
    cfg = PairConfig.from_settings(s, top_n=top)
    res = await gather_inputs(s, gate["snap"].markets, results, cfg, load_spot_aliases(s.universe_overrides_path))
    lists = rank(res["pairs"], cfg)
    scan_id = "s" + gate["report"].run_id[1:]
    store.save_scan(scan_id, gate["report"].ts, config_dict(cfg), lists, res["clusters"])
    _print_scan(scan_id, res, lists, gate, top)
    elapsed = time.monotonic() - t0
    print(f"cost: gates {gate['calls']} calls + pairs {res['calls']} calls, {elapsed:.1f}s wall clock for one full scan "
          f"(funding interval {min((m.funding_interval_hours or 1) for m in gate['snap'].markets if m.funding_interval_hours) * 3600:.0f}s: "
          f"{'OK' if elapsed < 3600 else 'SLOWER THAN THE FUNDING INTERVAL'}); stored as {scan_id} in {store.path}")
    return 0


def _replay_scan(s: Any, scan_id: str | None) -> int:
    from fundarb.pairs import PairConfig, rank
    from fundarb.store import UniverseStore

    store = UniverseStore(s.universe_db_path)
    scan_id = store.latest_scan() if scan_id in (None, "latest") else scan_id
    if scan_id is None:
        print("no stored scan to replay")
        return 1
    old = store.load_scan(scan_id)
    cfg = PairConfig(**old["config"])
    inputs = {r["pair_id"]: r["inputs"] for rows in old["lists"].values() for r in rows}
    new = rank(list(inputs.values()), cfg)
    names = ("capital_apr", "score", "transient", "unprofitable", "net_unknown", "rejected")
    diff = [name for name in names
            if [r["pair_id"] for r in new[name]] != [r["pair_id"] for r in old["lists"].get(name, [])]
            or [r["metrics"] for r in new[name]] != [r["metrics"] for r in old["lists"].get(name, [])]]
    print(f"replayed scan {scan_id}: {len(inputs)} pairs; lists identical: {', '.join(n for n in names if n not in diff)}"
          + (f"; DIFFERENT: {', '.join(diff)}" if diff else ""))
    return 0 if not diff else 1


def cmd_scan2(a: argparse.Namespace) -> int:
    s = load()
    if a.replay:
        return _replay_scan(s, a.replay)
    return asyncio.run(_scan_once(s, max(1, min(a.top, 20))))  # hard cap: never a "top 500"


# --- Module 4: oracle study + equity bucket ---------------------------------------------------------------------------

def _f(v: Any, fmt: str = "{:+.2f}") -> str:
    return "-" if v is None else fmt.format(v)


def _print_study(st: dict[str, Any]) -> None:
    print(f"ORACLE STALENESS STUDY {st['study_id']} ({st['measured_at'][:16]}Z): {len(st['markets'])} equity/index markets by 24h volume, "
          f"{st['days']:g} days of hourly settlements; control {st['control']} (24/7 crypto)")
    print("method: slope of the hourly change in fundingHistory.premium on the perp's hourly return (avg price). ~0 = the oracle "
          "tracks; ~1 = the oracle is frozen (premium absorbs the whole move); between = the oracle partly follows the perp itself")
    print(f"{'session':10s} {'verdict':16s} {'mkts':>4s} {'hours':>6s} {'slope med':>9s} {'slope range':>15s} {'corr med':>8s} "
          f"{'|prem| med':>10s} {'control':>8s}")
    for s, v in st["sessions"].items():
        rng = f"{v['beta_range'][0]:+.2f}..{v['beta_range'][1]:+.2f}" if v["beta_range"] else "-"
        print(f"{s:10s} {v['verdict']:16s} {v['markets']:>4d} {v['hours']:>6d} {_f(v['beta_median']):>9s} {rng:>15s} {_f(v['corr_median']):>8s} "
              f"{_f(v['median_abs_premium_bps'], '{:.1f}bp'):>10s} {_f(v['control_beta']):>8s}")
    print(f"method check: {'OK' if st['method_valid'] else 'FAILED'}: {st['method_check']}"
          + ("" if st["holidays_known"] else "; no holiday calendar loaded, so holidays count in their weekday session"))
    print("\nslope by market:  " + "  ".join(f"{s:>9s}" for s in ("rth", "extended", "overnight", "weekend")))
    for n in [*st["markets"], st["control"]]:
        row = st["per_market"].get(n, {})
        print(f"  {n or '-':18s}" + "  ".join(f"{_f((row.get(s) or {}).get('beta')):>9s}" for s in ("rth", "extended", "overnight", "weekend"))
              + ("   (control)" if n == st["control"] else ""))
    live = st["live"]
    if live["markets"]:
        ms = [v for k, v in live["markets"].items() if k != st["control"] and v.get("samples", 0) >= 3]
        moved = sum(1 for v in ms if v["oracle_changed_share"] > 0)
        fol = [v["corr_oracle_follows_mark"] for v in ms if v.get("corr_oracle_follows_mark") is not None]
        lead = [v["corr_mark_follows_oracle"] for v in ms if v.get("corr_mark_follows_oracle") is not None]
        print(f"\nlive ({live['session']} session, {live['samples']} samples {live['every_s']:g}s apart): oracle moved on {moved}/{len(ms)} markets; "
              f"median corr(oracle change, previous mark change) {_f(float(np.median(fol)) if fol else None)}, "
              f"corr(mark change, previous oracle change) {_f(float(np.median(lead)) if lead else None)} (observed only; not used for the verdict)")
    print(f"cost: {st['calls']} calls, {st['elapsed_s']}s")


def cmd_oracle_study(a: argparse.Namespace) -> int:
    from fundarb.events import load_operator
    from fundarb.oracle_study import run_study, write_policy
    from fundarb.sessions import SessionClock
    from fundarb.store import UniverseStore
    from fundarb.universe import HyperliquidUniverse

    s = load()
    store = UniverseStore(s.universe_db_path)
    if a.show:
        _print_study(store.load_study(a.show))
        return 0
    op = load_operator(s.equity_operator_path)
    clock = SessionClock.from_settings(s, op["calendar"].holidays())

    async def run() -> dict[str, Any]:
        snap = await HyperliquidUniverse(s).fetch()
        if not snap.ranking_allowed:
            print(f"WARN: universe schema is {snap.schema.status}; the study only reads names, classes and volumes from it")
        return await run_study(s, snap.markets, clock, n_markets=a.markets, days=a.days, samples=a.samples, every=a.every)

    st = asyncio.run(run())
    store.save_study(st)
    _print_study(st)
    if not st["method_valid"]:
        print(f"policy NOT written ({s.oracle_policy_path}): the method check failed")
        return 1
    if a.no_policy:
        print("policy not written (--no-policy)")
        return 0
    doc = write_policy(s.oracle_policy_path, st, s.oracle_stale_stop_mult)
    print("\npolicy written to " + str(s.oracle_policy_path) + " (cited as " + st["study_id"] + "):")
    for sess, p in doc["policy"].items():
        print(f"  {sess:10s} {p['verdict']:16s} funding reliable: {'yes' if p['funding_reliable'] else 'NO':3s}  stop x{p['stop_mult']:g}  entries {p['entries']}")
    return 0


def _print_equity(run_id: str, ts: str, session: str, policy: dict[str, Any], rows: list[dict[str, Any]], pool: int) -> None:
    cite = (policy.get("citation") or {}).get("study_id") or "NO STUDY ON FILE (assumption: only regular hours trusted)"
    p = policy["policy"][session]
    print(f"=== EQUITY BUCKET: index vs constituents ({run_id}, {ts[:16]}Z) ===")
    print("SEPARATE BUCKET: isolated margin on every leg (each leg its own collateral); ranked only against other equity rows. "
          "Never compare these APRs with the crypto scan's.")
    print(f"session now: {session}; oracle policy {p['verdict']} ({cite}): funding reliable {'yes' if p['funding_reliable'] else 'NO'}, "
          f"entries {p['entries']}, stop x{p['stop_mult']:g}. Gated equity pool: {pool} names.")
    print(f"{'index':12s} {'basket (hedge $ per $1 index)':44s} {'R2':>5s} {'sd bp':>6s} {'z':>6s} {'HL h':>5s} {'signal':27s} "
          f"{'est cap APR':>11s} {'status':15s}")
    for r in rows:
        m = r["metrics"]
        if m["status"] == "rejected":
            print(f"{r['index']:12s} REJECTED: {m['reason']}")
            continue
        basket = " + ".join(f"{b:.2f} {n}" for n, b in m["betas"].items())
        apr = (m.get("expected") or {}).get("capital_apr_pct")
        print(f"{r['index']:12s} {basket:44s} {m['r2']:5.2f} {m['spread_sd_bps']:6.1f} {m['z']:+6.2f} {_f(m['half_life_h'], '{:.0f}'):>5s} "
              f"{m['signal']:27s} {_pct(apr):>11s} {m['status']:15s}")
    for r in rows:
        m = r["metrics"]
        if m["status"] == "rejected":
            continue
        print(f"\n{r['index']}: basket {m['basket_source']}; {m['hours']} shared hours")
        for n, f in m["funding"].items():
            print(f"  funding {n:14s} now {_f(f['now_hourly'] * 1e4 if f['now_hourly'] is not None else None, '{:+.3f}')}bp/h  estimate "
                  f"{_f(f['estimate_hourly'] * 1e4 if f['estimate_hourly'] is not None else None, '{:+.3f}')}bp/h{'  AT CAP' if f['at_cap'] else ''}")
        sz = m["sizing"]
        print(f"  sizing (risk ${sz['risk_budget_usd']:,.0f} at the stop z={m['stop_z']:g}, {sz['stop_distance_bps']:.0f}bp away): index notional "
              f"${sz['index_notional_usd']:,.0f}, capital ${sz['capital_usd']:,.0f} ({sz['capital_per_index_notional']:.2f} per $1 index)")
        for n, lg in sz["legs"].items():
            print(f"    {lg['side']:5s} {n:14s} ${lg['notional_usd']:>10,.0f}  {lg['leverage']:g}x of max {lg['max_leverage']:g}x  margin ${lg['margin_usd']:,.0f}  "
                  f"liq ~{lg['liq_distance_pct']:.0f}% away  {lg['bucket']}")
        if m.get("expected"):
            e = m["expected"]
            print(f"  estimate: reversion {e['reversion_bps']:+.1f}bp - costs {e['cost_bps']:.1f}bp + carry {e['carry_bps']:+.1f}bp over {e['hold_h']:.0f}h "
                  f"= {e['net_bps']:+.1f}bp on index notional -> capital APR {_pct(e['capital_apr_pct'])} ({e['model']})")
        for b in m["blockers"]:
            print(f"  BLOCKED: {b}")
        for fl in m["flags"]:
            print(f"  flag: {fl}")


async def _equity_once(s: Any, top: int) -> int:
    from fundarb.equity import IndexConfig, config_dict, gather_equity, rank_equity
    from fundarb.events import load_operator
    from fundarb.gates import GateConfig
    from fundarb.oracle_study import load_policy, unmeasured_policy
    from fundarb.sessions import SessionClock
    from fundarb.store import UniverseStore
    from fundarb.universe import HyperliquidUniverse

    t0 = time.monotonic()
    store = UniverseStore(s.universe_db_path)
    op = load_operator(s.equity_operator_path)
    clock = SessionClock.from_settings(s, op["calendar"].holidays())
    policy = load_policy(s.oracle_policy_path) or unmeasured_policy(s.oracle_stale_stop_mult)
    gate = await _gates_once(HyperliquidUniverse(s), store, GateConfig.from_settings(s))
    if gate is None:
        return 2
    results = {r["raw_name"]: {"passed": r["passed"], "failed_gate": r["failed_gate"], "checks": r["checks"],
                               "reason": next((f"{c['note'] or ''} (value {c['value']}, limit {c['limit']})" for c in r["checks"]
                                               if c["gate"] == r["failed_gate"]), "")} for r in gate["report"].results}
    ts = gate["report"].ts
    session = clock.session(datetime.fromisoformat(ts))
    cfg = IndexConfig.from_settings(s, top_n=top)
    res = await gather_equity(s, gate["snap"].markets, results, cfg, op, policy, session, ts)
    rows = rank_equity(res["rows"], cfg)
    run_id = "e" + gate["report"].run_id[1:]
    elapsed = round(time.monotonic() - t0, 1)
    store.save_equity_run({"run_id": run_id, "ts": ts, "config": config_dict(cfg), "policy": policy, "calls": gate["calls"] + res["calls"],
                           "elapsed_s": elapsed}, rows)
    _print_equity(run_id, ts, session, policy, rows, len(res["pool"]))
    print(f"\ncost: gates {gate['calls']} calls + equity {res['calls']} calls, {elapsed}s; stored as {run_id} in {store.path}")
    return 0


def _replay_equity(s: Any, run_id: str | None) -> int:
    from fundarb.equity import IndexConfig, rank_equity
    from fundarb.store import UniverseStore

    store = UniverseStore(s.universe_db_path)
    run_id = store.latest_equity_run() if run_id in (None, "latest") else run_id
    if run_id is None:
        print("no stored equity run to replay")
        return 1
    old = store.load_equity_run(run_id)
    new = rank_equity([{"index": r["index"], "inputs": r["inputs"]} for r in old["rows"]], IndexConfig(**old["config"]))
    same = [r["index"] for r in new] == [r["index"] for r in old["rows"]] and \
        [json.loads(json.dumps(r["metrics"], sort_keys=True, default=str)) for r in new] == [r["metrics"] for r in old["rows"]]
    print(f"replayed equity run {run_id}: {len(new)} index markets; ranking and metrics {'IDENTICAL' if same else 'DIFFERENT'}")
    return 0 if same else 1


def cmd_equity(a: argparse.Namespace) -> int:
    s = load()
    if a.replay:
        return _replay_equity(s, a.replay)
    return asyncio.run(_equity_once(s, max(1, min(a.top, 20))))


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(prog="fundarb", description="Funding-rate arbitrage scanner (read-only)")
    sub = p.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("quotes", help="normalized quotes for symbols from each venue")
    q.add_argument("symbols", nargs="+", help="canonical symbols, e.g. BTC-PERP")
    q.add_argument("--venues", help="comma-separated subset")
    q.add_argument("--save", action="store_true", help="append to the SQLite history")
    q.add_argument("--json", action="store_true")
    q.set_defaults(fn=cmd_quotes)
    i = sub.add_parser("intervals", help="each venue's funding interval and where it came from")
    i.add_argument("--symbol", default="BTC-PERP")
    i.add_argument("--venues")
    i.set_defaults(fn=cmd_intervals)
    sc = sub.add_parser("scan-legacy", help="Phase 2 scan: the top funding spreads across the four venues' shared symbols")
    sc.add_argument("--top", type=int, default=10)
    sc.add_argument("--shortlist", type=int, default=25, help="symbols whose 7-day history is read")
    sc.add_argument("--leverage", type=float, default=2.0, help="per leg")
    sc.add_argument("--buffer", type=float, default=0.25, help="idle collateral per venue, share of margin")
    sc.add_argument("--hold", type=float, default=168.0, help="expected hold, hours (spreads the round-trip cost)")
    sc.add_argument("--venues")
    sc.add_argument("--json", action="store_true")
    sc.set_defaults(fn=cmd_scan)
    s2 = sub.add_parser("scan", help="Module 3: crypto pairs (same asset, two venues / spot-perp), ranked by capital APR and by variance-adjusted score")
    s2.add_argument("--top", type=int, default=20, help="rows per list (hard cap 20)")
    s2.add_argument("--replay", metavar="SCAN_ID", help="re-rank a stored scan from its stored inputs ('latest' for the newest)")
    s2.set_defaults(fn=cmd_scan2)
    g = sub.add_parser("gates", help="Module 2: liquidity / quality / oracle gates over the whole universe, persisted (read-only)")
    g.add_argument("--watch", action="store_true", help="repeat every FUNDARB_UNIVERSE_REFRESH_SECONDS (builds the oracle history)")
    g.add_argument("--replay", metavar="RUN_ID", help="re-decide a stored run from its stored inputs ('latest' for the newest)")
    g.add_argument("--json", action="store_true")
    g.set_defaults(fn=cmd_gates)
    u = sub.add_parser("universe", help="every Hyperliquid perp (native + HIP-3 builder dexes), classified, with live context")
    u.add_argument("--live-only", action="store_true", help="hide delisted markets")
    u.add_argument("--dex", help="one dex: native, xyz, flx, ...")
    u.add_argument("--class", dest="asset_class", help="one class: CRYPTO, EQUITY, INDEX, COMMODITY, FX, PREIPO, RATES, UNCLASSIFIED")
    u.add_argument("--csv", help="also write the rows to this CSV file")
    u.add_argument("--json", action="store_true")
    u.add_argument("--accept-schema", action="store_true", help="confirm the field-name schema seen now (a human decision)")
    u.add_argument("--watch", action="store_true", help="refresh every FUNDARB_UNIVERSE_REFRESH_SECONDS (default 300)")
    u.set_defaults(fn=cmd_universe)
    o = sub.add_parser("oracle-study", help="Module 4 rule 4: measure whether equity oracles go stale outside US hours; writes the policy")
    o.add_argument("--markets", type=int, default=12, help="equity/index markets, by 24h volume")
    o.add_argument("--days", type=float, default=30.0)
    o.add_argument("--samples", type=int, default=20, help="live snapshots (0 to skip)")
    o.add_argument("--every", type=float, default=15.0, help="seconds between live snapshots")
    o.add_argument("--no-policy", action="store_true", help="measure and store, but don't rewrite the policy file")
    o.add_argument("--show", metavar="STUDY_ID", help="print a stored study")
    o.set_defaults(fn=cmd_oracle_study)
    e = sub.add_parser("equity", help="Module 4: index vs constituents (equity bucket, never mixed with crypto)")
    e.add_argument("--top", type=int, default=20, help="rows (hard cap 20)")
    e.add_argument("--replay", metavar="RUN_ID", help="re-decide a stored run from its stored inputs ('latest' for the newest)")
    e.set_defaults(fn=cmd_equity)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
