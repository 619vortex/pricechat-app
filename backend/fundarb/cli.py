"""Phase 1 CLI (read-only).

  python -m fundarb quotes BTC-PERP ETH-PERP [--venues hyperliquid,lighter] [--save] [--json]
  python -m fundarb intervals [--symbol BTC-PERP]

Every venue answers or says why it couldn't; nothing is ever shown as a silent zero. (`scan` and `status` come in
Phases 2 and 3.)"""

import argparse
import asyncio
import json
import logging
import sys
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
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
