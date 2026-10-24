"""Phase 1 CLI (read-only).

  python -m fundarb quotes BTC-PERP ETH-PERP [--venues hyperliquid,lighter] [--save] [--json]
  python -m fundarb intervals [--symbol BTC-PERP]
  python -m fundarb gates [--watch] [--replay RUN_ID] [--json]
  python -m fundarb universe [--live-only] [--dex xyz] [--class EQUITY] [--csv out.csv] [--json] [--accept-schema] [--watch]

Every venue answers or says why it couldn't; nothing is ever shown as a silent zero. (`scan` and `status` come in
Phases 2 and 3.)"""

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict
from typing import Any

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
    sc = sub.add_parser("scan", help="the top funding spreads: capital-adjusted, variance-ranked, with costs and break-even")
    sc.add_argument("--top", type=int, default=10)
    sc.add_argument("--shortlist", type=int, default=25, help="symbols whose 7-day history is read")
    sc.add_argument("--leverage", type=float, default=2.0, help="per leg")
    sc.add_argument("--buffer", type=float, default=0.25, help="idle collateral per venue, share of margin")
    sc.add_argument("--hold", type=float, default=168.0, help="expected hold, hours (spreads the round-trip cost)")
    sc.add_argument("--venues")
    sc.add_argument("--json", action="store_true")
    sc.set_defaults(fn=cmd_scan)
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
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
