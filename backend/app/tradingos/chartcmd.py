"""/chart with options: "/chart BTC 15m bb oi funding", "/chart ETH 1h macd volume", "/chart BTC 1m depth oi wide".

The words can come in any order (a coin, a timeframe, any options). Every panel is drawn from real data only:
Hyperliquid candles (price, volume and the indicators computed from them), Hyperliquid funding history, Hyperliquid's
order book (depth), and Price Chat's own open-interest history (Hyperliquid publishes only the current value; see oi.py).
Market-structure options (cvd, aggoi, liq, zones, heatmap, walls, twap) come from structure.py; the caption labels each as
observed, aggregated, model estimate or inferred.
When a panel's data isn't available, the caption says why instead of drawing something made up."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.tradingos import chart, oi, readout, tickers, venues
from app.tradingos.market import INTERVALS, MarketData, MarketError, fmt_price
from app.tradingos.tickers import Symbol

OPTIONS = {  # what members type -> the panel
    "rsi": "rsi", "bb": "bb", "boll": "bb", "bollinger": "bb", "bands": "bb", "macd": "macd",
    "ma": "ma", "mas": "ma", "ema": "ma", "sma": "ma", "vol": "volume", "volume": "volume",
    "oi": "oi", "openinterest": "oi", "funding": "funding", "fund": "funding", "fr": "funding",
    "depth": "depth", "book": "depth", "ob": "depth", "orderbook": "depth", "wide": "wide",  # no 1-letter words: W is a coin
    "cvd": "cvd", "delta": "cvd", "flow": "cvd", "orderflow": "cvd", "aggoi": "aggoi", "oiall": "aggoi", "oiagg": "aggoi",
    "liq": "liq", "liqs": "liq", "liquidations": "liq", "rekt": "liq", "zones": "zones", "liqmap": "zones", "liqzones": "zones",
    "heatmap": "heatmap", "heat": "heatmap", "walls": "walls", "wall": "walls", "twap": "twap", "algo": "twap",
    "structure": "structure", "pro": "structure",
    "indicator": "indicators", "indicators": "indicators", "inds": "indicators",  # not "ind" / "ta": tickers people chart
}
STRUCTURE = {"cvd", "aggoi", "liq", "walls"}  # what "structure" turns on
INDICATORS = {"rsi", "macd", "ma"}  # what "indicators" turns on: the three main ones (RSI, MACD, EMA 20/50 on price)
NUMERIC_TF = {"1": "1m", "5": "5m", "15": "15m", "30": "30m", "60": "1h", "240": "4h", "1440": "1d"}
WORD_TF = {"h": "1h", "1h": "1h", "60m": "1h", "4h": "4h", "d": "1d", "1d": "1d", "daily": "1d", "day": "1d"}
USAGE = ("Usage: /chart BTC 15m [options]\n"
         "Timeframes: 1m 5m 15m 30m 1h 4h 1d\n"
         "Options: indicators (RSI + MACD + EMAs) · rsi · bb · macd · ma · volume · oi · funding · depth · wide\n"
         "Market structure: cvd · aggoi · liq · zones · heatmap · walls · twap · structure (= cvd aggoi liq walls)\n"
         "e.g. fc BTC indicators · /chart BTC 15m bb oi funding · /chart ETH 1h macd volume · /chart BTC 15m cvd liq heatmap")
DEFAULT_TF = "1h"


class ChartUsageError(ValueError):
    """The command couldn't be understood; the message says how to write it."""


@dataclass
class ChartRequest:
    coin: str  # the asset ("BTC"); the full market as asked is `sym`
    tf: str = DEFAULT_TF
    opts: set[str] = field(default_factory=set)
    sym: Symbol | None = None

    @property
    def wide(self) -> bool:
        return "wide" in self.opts

    @property
    def symbol(self) -> Symbol:
        return self.sym or Symbol(base=self.coin)


def _timeframe(word: str) -> str | None:
    w = word.lower()
    if w in INTERVALS:
        return w
    return NUMERIC_TF.get(w) or WORD_TF.get(w)


def parse(args: list[str], markets: set[str] | None = None) -> ChartRequest:
    """Words in any order: a market (BTC, $BTC, BTCUSDT, $BTCUSDT, BTC/USDT, BTCUSDT.P, a contract address), a timeframe,
    options, and optionally an exchange / market type / chain ("binance", "spot", "perp", "dex", "eth"). Raises
    ChartUsageError with a helpful message (never guesses between two coins). With `markets` (the listed coins), a second
    word is only "another coin" when it really is one; otherwise it's an unknown word."""
    tf, opts, rest = None, set(), []
    for raw in args:
        low = raw.lower().strip(",")
        if not low:
            continue
        if low in OPTIONS:
            opts |= STRUCTURE if OPTIONS[low] == "structure" else INDICATORS if OPTIONS[low] == "indicators" else {OPTIONS[low]}
            continue
        t = _timeframe(low)
        if t:
            if tf and tf != t:
                raise ChartUsageError(f"Two timeframes ({tf} and {t}): pick one.\n\n{USAGE}")
            tf = t
            continue
        rest.append(raw)
    quals, words = tickers.qualifiers(rest)
    sym, unknown = None, []
    for raw in words:
        s = tickers.parse_symbol(raw)
        if s is not None and sym is None:
            sym = s
            continue
        if s is not None and sym is not None and (markets is None or s.base in markets):
            raise ChartUsageError(f"Two coins ({sym.display} and {s.display}): one chart shows one market.\n\n{USAGE}")
        unknown.append(raw)
    if unknown:
        raise ChartUsageError(f"I didn't understand: {', '.join(w.lower() for w in unknown[:5])}.\n\n{USAGE}")
    if sym is None:
        raise ChartUsageError(USAGE)
    try:
        sym = tickers.with_qualifiers(sym, quals)
    except ValueError as e:
        raise ChartUsageError(str(e)) from None
    return ChartRequest(sym.base or tickers.short_address(sym.address or ""), tf or DEFAULT_TF, opts, sym)


def _stamp(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


CAPTION_LIMIT = 1024 - 160  # Telegram's photo-caption limit, minus the footer added under every reply (bot channel + referral line, ~130)
DETAILS_LIMIT = 3600  # the follow-up message, well inside Telegram's 4096
HL_ONLY = {"funding": "Funding", "oi": "OI history", "depth": "Order book depth"}  # Hyperliquid panels


def build(db: Session | None, md: MarketData, req: ChartRequest, vs: Any = None) -> tuple[bytes, str]:
    """(PNG, caption with any details). Blocking: call it in a thread."""
    png, caption, details = build_full(db, md, req, vs)
    return png, caption + (f"\n\n{details}" if details else "")


def build_full(db: Session | None, md: MarketData, req: ChartRequest, vs: Any = None, settings: Any = None) -> tuple[bytes, str, str | None]:
    """(PNG, caption, details). The caption is the compact readout (Telegram HTML); the layer list, data-basis lines and
    notes go under it when everything fits a photo caption, otherwise into `details` for a message right after the chart
    (nothing is cut off). Raises venues.NotFound / venues.Ambiguous / MarketError. Blocking: call it in a thread."""
    from app.tradingos.bot import esc

    vs = vs or venues.client()
    listing = venues.resolve(req.symbol, md, vs)
    hl = listing.venue == "hyperliquid"
    m = md.market(listing.symbol) if hl else None
    show = 200 if req.wide else 120
    warm = 210 if "ma" in req.opts else 60  # extra candles so the indicators (EMA 200 with "ma") are settled at the left edge
    candles = venues.candles(listing, req.tf, show + warm, md, vs)
    if len(candles) < 2:
        raise MarketError(f"Not enough {req.tf} candles for {listing.tag}.")
    notes: list[str] = []
    panels: list[tuple[str, Any]] = []
    start_ms = candles[-show:][0].t if len(candles) > show else candles[0].t
    end_ms = candles[-1].t + INTERVALS[req.tf] * 1000
    for p in ("volume", "rsi", "macd"):
        if p in req.opts:
            panels.append((p, None))
    if not hl:
        for opt, name in HL_ONLY.items():
            if opt in req.opts:
                notes.append(f"{name}: not available for {listing.label}")
    if hl and "funding" in req.opts:
        hours = max(2, min(480, int((end_ms - start_ms) / 3_600_000) + 2))
        try:
            fh = [(t, r) for t, r in md.funding_history(m.coin, hours) if t >= start_ms - 3_600_000]
        except MarketError as e:
            fh = []
            notes.append(f"Funding history unavailable: {e}")
        if fh:
            panels.append(("funding", fh))
            if fh[0][0] > start_ms + 3_600_000:
                notes.append(f"Funding: last {hours} h (Hyperliquid funding is hourly)")
        elif not notes or not notes[-1].startswith("Funding"):
            notes.append("Funding: no funding payments in this window (funding is hourly)")
    if hl and "oi" in req.opts:
        hist = oi.history(db, m.coin, datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)) if db is not None else []
        if len(hist) >= 3:
            panels.append(("oi", [(t * 1000, v) for t, v in hist]))
        else:
            notes.append(f"OI now ${m.oi_usd / 1e6:,.0f}M. No OI chart yet: Hyperliquid has no OI history, Price Chat records it every "
                         "15 min (needs at least 3 snapshots in this window)")
    depth = None
    if hl and "depth" in req.opts:
        try:
            depth = md.levels(m.coin, 5 if req.tf in ("1m", "5m") else 4)
        except MarketError as e:
            notes.append(f"Order book unavailable: {e}")
    # market-structure layers are cross-exchange perpetual data for the asset (structure.py names its venues on each line)
    structure_coin = m.coin if m else _hl_coin(md, req.symbol.base) or req.symbol.base
    layers, basis = {}, []
    if req.opts & set(STRUCTURE_ORDER):
        if listing.venue == "dex":
            notes.append("Market-structure layers come from exchange perpetuals; there are none for a DEX token")
        else:
            layers, basis = _structure(db, structure_coin, req, candles[-show:], start_ms, panels, notes)
    layers["sources"] = [b.split(" ", 1)[1] for b in basis]
    title = f"{listing.tag} · {req.tf} · {listing.label}"
    if m:
        sub = (f"Last {fmt_price(m.mark)} · funding {m.funding * 100:+.4f}%/h · OI {readout.usd_short(m.oi_usd)} · "
               f"24h vol {readout.usd_short(m.day_volume)}")  # the 24h move is the badge after the title
    else:
        sub = f"Last {fmt_price(listing.price)}" + (f" · 24h vol {readout.usd_short(listing.volume_24h)}" if listing.volume_24h else "")
    r = readout.read(listing, candles, req.tf, md, settings)
    busy = bool(layers.get("zones") or layers.get("walls"))  # structure levels already crowd the price panel
    change = m.change_24h if m else listing.change_24h
    png = chart.render_study(candles, title, sub, show=show, interval_ms=INTERVALS[req.tf] * 1000, wide=req.wide,
                             ma="ma" in req.opts, bb="bb" in req.opts, panels=panels, depth=depth, source=listing.label,
                             levels=None if busy else (r.support, r.resistance), plan=r.plan, change=change, **layers)
    drawn = {k for k, _ in panels} | {k for k in ("zones", "walls", "twap") if layers.get(k)} | ({"heatmap"} if layers.get("heat") else set())
    shown = [o for o in ("rsi", "bb", "macd", "ma", "volume", "funding", "oi", "depth", *STRUCTURE_ORDER) if o in req.opts
             and (o not in ("funding", "oi", *STRUCTURE_ORDER) or o in drawn) and (o != "depth" or depth)]
    caption = readout.caption(listing, req.tf, r)
    extra = ([f"Layers: {' · '.join(shown)}"] if shown else []) + [esc(b) for b in basis] + [f"ℹ️ {esc(n)}" for n in notes]
    if listing.alternatives:
        extra.append(f"Also trades on: {esc(', '.join(listing.alternatives))}")
    if listing.venue == "dex":
        extra.append(f"Pool {esc(listing.pool_name or '')} · token <code>{esc(listing.token_address or '')}</code> · liquidity {readout.usd_short(listing.liquidity)}")
    if r.rules == "chart rules":
        extra.append("Chart rules: trend, pullback, RSI, MACD and levels from this market's candles")
    if not extra:
        return png, caption, None
    together = caption + "\n\n" + "\n".join(extra)
    if len(together) <= CAPTION_LIMIT:
        return png, together, None
    kept, size = [], 0
    for i, line in enumerate(extra):  # whole lines only: a cut inside an HTML tag would make Telegram refuse the message
        if size + len(line) > DETAILS_LIMIT:
            kept.append(f"… and {len(extra) - i} more notes")
            break
        kept.append(line)
        size += len(line) + 1
    return png, caption, f"<b>{esc(listing.tag)}</b> {readout.tf_label(req.tf)} · chart details\n" + "\n".join(kept)


def _hl_coin(md: MarketData, base: str) -> str | None:
    try:
        hit = venues.hl_listing(md, base)
    except MarketError:
        return None
    return hit.symbol if hit else None


STRUCTURE_ORDER = ("cvd", "aggoi", "liq", "zones", "heatmap", "walls", "twap")


def _structure(db: Session | None, coin: str, req: ChartRequest, vis: list[Any], start_ms: int, panels: list[tuple[str, Any]],
               notes: list[str]) -> tuple[dict[str, Any], list[str]]:
    """The market-structure layers asked for (structure.py), each labelled with what kind of data it is. A layer whose data
    isn't available is left out and the caption says why."""
    from app.tradingos import structure as ms

    layers: dict[str, Any] = {}
    basis: list[str] = []
    tf = req.tf
    n = len(vis)

    def why(name: str, e: Exception) -> None:
        notes.append(f"{name} unavailable: {e}")

    if "cvd" in req.opts:
        try:
            c = ms.cvd(coin, tf, n + 2)
            panels.append(("cvd", c))
            basis.append(f"📊 CVD: {c['basis_label'].lower()}, {' + '.join(c['venues'])} perp taker volume")
            if c["skipped"]:
                notes.append("CVD without " + ", ".join(f"{v} ({w})" for v, w in c["skipped"].items()))
        except ms.StructureError as e:
            why("CVD", e)
    if "aggoi" in req.opts:
        try:
            o = ms.open_interest(coin, tf, n + 2)
            if len(o["history"]) >= 3:
                panels.append(("aggoi", o))
            else:
                notes.append("Aggregated OI history: no venue history for this market (" + ", ".join(f"{v} {w}" for v, w in o["history_skipped"].items()) + ")")
            basis.append(f"📊 OI: aggregated history {' + '.join(o['history_venues']) or '-'} ({o['period']}); now {' + '.join(o['venues'])}")
        except ms.StructureError as e:
            why("Aggregated OI", e)
    if "liq" in req.opts:
        try:
            lq = ms.reported_liquidations(coin, start_ms, db)
            panels.append(("liq", lq))
            basis.append(f"📊 Liquidations: reported by {' + '.join(lq['venues']) or 'no exchange'}"
                         + (f"; missing {', '.join(lq['skipped'])}" if lq["skipped"] else ""))
            if lq["first"] and lq["first"] > start_ms + INTERVALS[tf] * 1000 * 3:
                notes.append(f"Liquidation record starts {_stamp(lq['first'])}")
        except ms.StructureError as e:
            why("Liquidations", e)
    if "zones" in req.opts:
        try:
            z = ms.liquidation_zones(coin, tf)
            layers["zones"] = z["bins"]
            basis.append(f"🧮 Liquidation zones: MODEL ESTIMATE from {z['venues'][0]} OI changes, not confirmed positions (leverage mix "
                         + ", ".join(f"{lev}x" for lev, _ in ms.LEVERAGE_MIX) + " assumed)")
        except (ms.StructureError, KeyError) as e:
            why("Liquidation zones", e)
    if "heatmap" in req.opts:
        hm = ms.heatmap(db, coin, datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)) if db is not None else None
        if hm and len(hm["snapshots"]) >= 3:
            layers["heat"] = hm["snapshots"]
            basis.append(f"📊 Heatmap: resting liquidity recorded every 2 min ({' + '.join(hm['venues'])}); " + "; ".join(hm["notes"][:-1]))
        else:
            notes.append(f"No liquidity heatmap yet: Price Chat records books for {', '.join(ms.HEATMAP_COINS)} every 2 minutes"
                         if coin.upper() in ms.HEATMAP_COINS else f"Liquidity heatmaps are recorded for {', '.join(ms.HEATMAP_COINS)} only")
    if "walls" in req.opts:
        try:
            b = ms.books(coin)
            layers["walls"] = b["walls"][:4]
            imb = next(iter(b["imbalance"].items()), None)
            basis.append("📊 Walls: observed single book levels ≥10x the venue's median level · "
                         + (f"bid/ask imbalance within ±{imb[0]}: {imb[1]['imbalance'] * 100:+.0f}%" if imb else "")
                         + f" ({' + '.join(b['venues'])})")
            if not b["walls"]:
                notes.append("No book level stands out (≥10x the median) right now")
        except ms.StructureError as e:
            why("Order book walls", e)
    if "twap" in req.opts:
        minutes = max(10, min(60, int((vis[-1].t - vis[0].t) / 60_000) + INTERVALS[tf] // 60))
        try:
            tw = ms.twap_patterns(coin, minutes)
            layers["twap"] = tw["patterns"]
            basis.append(f"🔎 TWAP-like: INFERRED from {tw['trades']:,} {' + '.join(tw['venues'])} trades (last {minutes} min); not proof of who traded")
            if not tw["patterns"]:
                notes.append(f"No TWAP-like run (≥{ms.TWAP_MIN_PRINTS} same-size prints at regular intervals) in the last {minutes} min")
        except ms.StructureError as e:
            why("TWAP scan", e)
    return layers, basis
