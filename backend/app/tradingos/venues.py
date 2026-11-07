"""Which real market a member means, and its candles: Hyperliquid perpetuals (market.py), Binance / Bybit / OKX spot and
perpetual markets, and DEX pools (GeckoTerminal's public API). All public, read-only endpoints; nothing is estimated.

Resolution, in order (tickers.Symbol keeps what the member wrote explicitly):
- a contract address or a DEX word -> the DEX pool of that token (by ticker only when exactly one token has that ticker);
- an exchange named -> that exchange (spot unless they said perp, quote USDT unless they gave one);
- no exchange, no quote (or USD) -> the Hyperliquid perpetual when there is one (what /chart always did);
- otherwise OKX's market when OKX lists it (the owner's choice), else the most liquid matching exchange market (24h volume),
  labelled with its exchange and market type, with the other exchanges that list it named as alternatives;
- no exchange lists it (and none was named) -> its DEX pool: GeckoTerminal, then DexScreener (more chains, newer tokens).
Different tokens sharing one ticker on DEXes are never picked between: the member gets the choices with their addresses.
A spot market is always labelled Spot, a perpetual Perpetual."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from app.tradingos.market import INTERVALS, Candle, MarketData, MarketError
from app.tradingos.structure import StructureError, Venues
from app.tradingos.tickers import Symbol, cashtag, short_address

BINANCE_SPOT = "https://api.binance.com"
BINANCE_PERP = "https://fapi.binance.com"
BYBIT = "https://api.bybit.com"
OKX = "https://www.okx.com"
GECKO = "https://api.geckoterminal.com/api/v2"
EXCHANGES = ("binance", "bybit", "okx")
PREFERRED = "okx"  # with no exchange named, a market not on Hyperliquid comes from OKX when OKX lists it
NAME = {"binance": "Binance", "bybit": "Bybit", "okx": "OKX", "hyperliquid": "Hyperliquid"}
KIND = {"spot": "Spot", "perp": "Perpetual"}
NETWORK = {"eth": "Ethereum", "base": "Base", "bsc": "BNB Chain", "solana": "Solana", "arbitrum": "Arbitrum", "polygon_pos": "Polygon",
           "avax": "Avalanche", "ton": "TON", "optimism": "Optimism", "sui-network": "Sui", "tron": "Tron", "hyperevm": "HyperEVM"}
BYBIT_TF = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240", "1d": "D", "1w": "W"}
OKX_TF = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "4h": "4H", "1d": "1Dutc", "1w": "1Wutc"}
GECKO_TF = {"1m": ("minute", 1), "5m": ("minute", 5), "15m": ("minute", 15), "1h": ("hour", 1), "4h": ("hour", 4), "1d": ("day", 1)}
DEX_MIN_LIQUIDITY = 10_000  # USD: pools below this are dust (and often copycat tokens); never offered
DEXSCREENER = "https://api.dexscreener.com/latest/dex"  # second DEX source (public, no key)
DS_CHAIN = {"ethereum": "eth", "bsc": "bsc", "solana": "solana", "base": "base", "arbitrum": "arbitrum", "polygon": "polygon_pos",
            "avalanche": "avax", "ton": "ton", "optimism": "optimism", "sui": "sui-network", "tron": "tron", "hyperevm": "hyperevm"}  # -> GeckoTerminal id
NOT_LISTED = ("answered 400", "doesn't exist", "not supported", "invalid symbol", "symbol invalid", "params error")


_shared: Venues | None = None


def client() -> Venues:
    """One cached client per process (tests replace `_shared`)."""
    global _shared
    if _shared is None:
        _shared = Venues()
    return _shared


class NotFound(Exception):
    """No market matches (the message says where Price Chat looked)."""


class Ambiguous(Exception):
    """Several different assets match: the member has to pick (`options` are Listings with the details to choose by)."""

    def __init__(self, message: str, options: list[Listing]) -> None:
        super().__init__(message)
        self.options = options


@dataclass
class Listing:
    venue: str  # hyperliquid | binance | bybit | okx | dex
    kind: str  # perp | spot
    base: str  # as the venue names the asset (1000PEPE, kPEPE: the price is per that unit)
    quote: str  # USD for Hyperliquid perps (USDC-margined), USDT/USDC/... on exchanges, USD on DEX (GeckoTerminal prices)
    symbol: str  # the venue's own id: BTC, BTCUSDT, BTC-USDT-SWAP, the pool address
    price: float
    change_24h: float | None  # %
    volume_24h: float | None  # USD
    network: str | None = None
    dex: str | None = None
    token_address: str | None = None
    pool_name: str | None = None
    liquidity: float | None = None
    alternatives: list[str] = field(default_factory=list)  # other places it trades, as display labels

    @property
    def tag(self) -> str:
        """The published spelling: '$BTCUSDT' for exchange pairs, '$BTCUSD' for Hyperliquid perps, '$TOKEN' for DEX tokens."""
        return cashtag(self.base) if self.venue == "dex" else cashtag(self.base, self.quote)

    @property
    def label(self) -> str:
        """Where the data comes from, exactly: 'Binance Spot', 'Hyperliquid Perpetual', 'Uniswap V3 · Ethereum'."""
        if self.venue == "dex":
            return f"{dex_name(self.dex)} · {NETWORK.get(self.network or '', (self.network or '').title())}"
        return f"{NAME[self.venue]} {KIND[self.kind]}"

    @property
    def headline(self) -> str:
        return f"{self.tag} | {self.label}"


def dex_name(dex_id: str | None) -> str:
    """'uniswap_v3' -> 'Uniswap V3', 'pancakeswap-v3-bsc' -> 'Pancakeswap V3'."""
    if not dex_id:
        return "DEX"
    parts = dex_id.replace("-", "_").split("_")
    words = [p.upper() if (p[:1] == "v" and p[1:].isdigit()) or p in ("clmm", "amm", "dlmm", "cpmm", "cl") else p.title()
             for p in parts if p not in NETWORK and p not in ("bsc", "eth", "base")]
    return " ".join(words) or dex_id


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _not_listed(e: Exception) -> bool:
    return any(s in str(e).lower() for s in NOT_LISTED)


# ---------------------------------------------------------------- exchange listings
def venue_symbol(venue: str, kind: str, base: str, quote: str) -> str:
    if venue == "okx":
        return f"{base}-{quote}" + ("-SWAP" if kind == "perp" else "")
    return f"{base}{quote}"


def exchange_listing(vs: Venues, venue: str, kind: str, base: str, quote: str) -> Listing | None:
    """The 24h ticker of one exchange market, or None when that exchange doesn't list it. Raises StructureError when the
    exchange can't be read (region block, outage) so callers can say so."""
    bases = [base, f"1000{base}"] if kind == "perp" and not base.startswith("1000") else [base]  # memecoin perps are often per 1000
    for b in bases:
        sym = venue_symbol(venue, kind, b, quote)
        try:
            if venue == "binance":
                t = vs.get(f"{BINANCE_PERP if kind == 'perp' else BINANCE_SPOT}{'/fapi/v1' if kind == 'perp' else '/api/v3'}/ticker/24hr", {"symbol": sym}, 20)
                price, chg, vol = _f(t.get("lastPrice")), _f(t.get("priceChangePercent")), _f(t.get("quoteVolume"))
            elif venue == "bybit":
                rows = vs.get(f"{BYBIT}/v5/market/tickers", {"category": "linear" if kind == "perp" else "spot", "symbol": sym}, 20)["result"]["list"]
                if not rows:
                    continue
                t = rows[0]
                price, chg, vol = _f(t.get("lastPrice")), _f(t.get("price24hPcnt")) * 100, _f(t.get("turnover24h"))
            else:
                rows = vs.get(f"{OKX}/api/v5/market/ticker", {"instId": sym}, 20).get("data") or []
                if not rows:
                    continue
                t = rows[0]
                price, open_ = _f(t.get("last")), _f(t.get("open24h"))
                chg = (price - open_) / open_ * 100 if open_ else None
                vol = _f(t.get("volCcy24h")) * (price if kind == "perp" else 1)  # swaps: volCcy24h is in the base coin
        except StructureError as e:
            if _not_listed(e):
                continue
            raise
        if price > 0:
            return Listing(venue=venue, kind=kind, base=b, quote=quote, symbol=sym, price=price, change_24h=chg, volume_24h=vol)
    return None


def hl_listing(md: MarketData, base: str) -> Listing | None:
    _, raw = md.markets()
    ms = {k.upper(): v for k, v in raw.items()}
    m = ms.get(base.upper()) or (ms.get(f"K{base.upper()}") if not base.upper().startswith("K") else None)  # kPEPE = 1000 PEPE
    if m is None:
        return None
    return Listing(venue="hyperliquid", kind="perp", base=m.coin, quote="USD", symbol=m.coin, price=m.mark, change_24h=m.change_24h,
                   volume_24h=m.day_volume)


BLOCK_MEMORY = 600.0  # s: an exchange that blocks this server's region isn't asked again for 10 minutes
_blocked: dict[str, tuple[float, str]] = {}  # venue -> (until, reason)


def _exchange_scan(vs: Venues, base: str, quote: str, venues: tuple[str, ...], kinds: tuple[str, ...]) -> tuple[list[Listing], dict[str, str]]:
    """Every (exchange, kind) asked for, read in parallel. (listings found, {"Binance": "blocks requests from this server's region"})."""
    import time

    now = time.time()
    errors: dict[str, str] = {NAME[v]: _blocked[v][1] for v in venues if v in _blocked and _blocked[v][0] > now}
    jobs = [(v, k) for v in venues for k in kinds if NAME[v] not in errors]
    found: list[Listing] = []

    def one(vk: tuple[str, str]) -> tuple[tuple[str, str], Listing | None, str | None]:
        try:
            return vk, exchange_listing(vs, vk[0], vk[1], base, quote), None
        except StructureError as e:
            return vk, None, str(e)

    with ThreadPoolExecutor(max_workers=min(6, len(jobs) or 1)) as pool:
        for (v, _k), hit, err in pool.map(one, jobs):
            if hit:
                found.append(hit)
            elif err:
                errors[NAME[v]] = err
                if "region" in err:
                    _blocked[v] = (now + BLOCK_MEMORY, err)
    return found, errors


# ---------------------------------------------------------------- DEX (GeckoTerminal)
def _pool(p: dict[str, Any]) -> Listing:
    a = p.get("attributes") or {}
    rel = p.get("relationships") or {}
    network, _, address = str(p.get("id", "")).partition("_")
    token_id = ((rel.get("base_token") or {}).get("data") or {}).get("id", "")
    name = str(a.get("name") or "")
    base = name.split(" / ")[0].strip() if " / " in name else name
    vol = (a.get("volume_usd") or {}).get("h24")
    chg = (a.get("price_change_percentage") or {}).get("h24")
    return Listing(venue="dex", kind="spot", base=base.upper(), quote="USD", symbol=a.get("address") or address, price=_f(a.get("base_token_price_usd")),
                   change_24h=_f(chg) if chg is not None else None, volume_24h=_f(vol) if vol is not None else None, network=network,
                   dex=((rel.get("dex") or {}).get("data") or {}).get("id"), token_address=token_id.partition("_")[2] or None, pool_name=name,
                   liquidity=_f(a.get("reserve_in_usd")))


def _ds_pair(p: dict[str, Any]) -> Listing | None:
    """A DexScreener pair as a Listing, on the chains GeckoTerminal can draw candles for (else None: no chart, no indicators)."""
    net = DS_CHAIN.get(str(p.get("chainId") or ""))
    base, quote = p.get("baseToken") or {}, p.get("quoteToken") or {}
    if not net or not p.get("pairAddress") or not base.get("symbol"):
        return None
    chg, vol, liq = (p.get("priceChange") or {}).get("h24"), (p.get("volume") or {}).get("h24"), (p.get("liquidity") or {}).get("usd")
    dex = str(p.get("dexId") or "") + (f" {p['labels'][0]}" if p.get("labels") else "")
    return Listing(venue="dex", kind="spot", base=str(base["symbol"]).upper(), quote="USD", symbol=str(p["pairAddress"]), price=_f(p.get("priceUsd")),
                   change_24h=_f(chg) if chg is not None else None, volume_24h=_f(vol) if vol is not None else None, network=net, dex=dex or None,
                   token_address=str(base.get("address") or "") or None, pool_name=f"{base['symbol']} / {quote.get('symbol', '?')}",
                   liquidity=_f(liq) if liq is not None else None)


def _dex_pools(vs: Venues, sym: Symbol, source: str) -> list[Listing]:
    """Pools for the token (by contract address, else ticker) from one source, before filtering."""
    if source == "geckoterminal":
        params: dict[str, Any] = {"query": sym.address or sym.base, "page": 1}
        if sym.chain:
            params["network"] = sym.chain
        return [_pool(p) for p in vs.get(f"{GECKO}/search/pools", params, 60).get("data") or []]
    body = vs.get(f"{DEXSCREENER}/tokens/{sym.address}", {}, 60) if sym.address else vs.get(f"{DEXSCREENER}/search", {"q": sym.base}, 60)
    return [x for x in (_ds_pair(p) for p in (body or {}).get("pairs") or []) if x is not None]


def dex_resolve(vs: Venues, sym: Symbol) -> Listing:
    """The DEX pool of a token: GeckoTerminal first, DexScreener when GeckoTerminal has no pool with enough liquidity (more
    chains and newer tokens). By contract address, or by ticker when exactly one token uses it."""
    pools: list[Listing] = []
    errors: list[str] = []
    for source in ("geckoterminal", "dexscreener"):
        try:
            found = _dex_pools(vs, sym, source)
        except StructureError as e:
            errors.append(f"{source}: {e}")
            continue
        found = [p for p in found if (p.liquidity or 0) >= DEX_MIN_LIQUIDITY and p.price > 0]
        if sym.address:
            found = [p for p in found if (p.token_address or "").lower() == sym.address.lower()]
        else:
            found = [p for p in found if p.base == sym.base.upper()]
        if sym.chain:
            found = [p for p in found if p.network == sym.chain]
        if sym.dex:
            found = [p for p in found if sym.dex in (p.dex or "")]
        if found:
            pools = found
            break
    if not pools:
        where = f" on {NETWORK.get(sym.chain, sym.chain)}" if sym.chain else ""
        down = f" ({'; '.join(errors)})" if errors else ""
        raise NotFound(f"No DEX pool with at least ${DEX_MIN_LIQUIDITY:,} liquidity for {sym.display}{where} (GeckoTerminal, DexScreener){down}.")
    by_token: dict[str, list[Listing]] = {}
    for p in pools:
        by_token.setdefault(f"{p.network}:{(p.token_address or '').lower()}", []).append(p)
    best = [max(ps, key=lambda p: p.liquidity or 0) for ps in by_token.values()]
    best.sort(key=lambda p: -(p.liquidity or 0))
    if len(best) > 1:
        raise Ambiguous(f"{len(best)} different tokens use the ticker {cashtag(sym.base)} on DEXes. Pick one by its contract address:", best[:4])
    return best[0]


# ---------------------------------------------------------------- resolution
def resolve(sym: Symbol, md: MarketData, vs: Venues) -> Listing:
    """The one market the member means (see the module docstring). Raises NotFound or Ambiguous."""
    if sym.address or sym.venue == "dex":
        return dex_resolve(vs, sym)
    if sym.venue == "hyperliquid":
        if sym.kind == "spot":
            raise NotFound("Price Chat reads Hyperliquid perpetuals only (not Hyperliquid spot).")
        if sym.quote not in (None, "USD", "USDC"):
            raise NotFound(f"Hyperliquid perpetuals are quoted in USD (USDC-margined), not {sym.quote}.")
        hit = hl_listing(md, sym.base)
        if hit is None:
            raise NotFound(f"{cashtag(sym.base)} isn't a Hyperliquid perpetual.")
        return hit
    if sym.venue is None and sym.kind in (None, "perp") and sym.quote in (None, "USD"):
        try:
            hit = hl_listing(md, sym.base)
        except MarketError:
            hit = None  # Hyperliquid unreachable: the exchanges can still answer
        if hit is not None:
            return hit
        if sym.quote == "USD":
            raise NotFound(f"{cashtag(sym.base, 'USD')} isn't a Hyperliquid perpetual. For an exchange pair write {cashtag(sym.base, 'USDT')}.")
    quote = sym.quote or "USDT"
    venues = (sym.venue,) if sym.venue else EXCHANGES
    kinds = (sym.kind,) if sym.kind else ("spot", "perp")
    found, errors = _exchange_scan(vs, sym.base, quote, venues, kinds)
    if not found:
        if not sym.venue and sym.kind != "perp":  # no exchange lists it and none was named: its DEX pool (Ambiguous lists the contracts)
            try:
                return dex_resolve(vs, sym)
            except NotFound:
                pass
        looked = " / ".join(NAME[v] for v in venues) + f" ({' and '.join(KIND[k].lower() for k in kinds)}, quote {quote})"
        extra = "".join(f"\n· {v}: {e}" for v, e in errors.items())
        hint = "" if sym.venue else f"\nNo DEX pool either. A token's contract address finds it on any chain."
        raise NotFound(f"No {cashtag(sym.base, quote)} market on {looked}" + (" or a DEX" if not sym.venue else "") + f".{extra}{hint}")
    pool = found
    if not sym.venue:  # no exchange named: OKX when it lists it
        pool = [x for x in found if x.venue == PREFERRED] or found
    spot = [x for x in pool if x.kind == "spot"]
    pick_from = spot if (spot and sym.kind is None) else pool  # no market type asked: the spot pair (a plain ticker means the asset)
    best = max(pick_from, key=lambda x: x.volume_24h or 0)
    best.alternatives = [x.label for x in sorted(found, key=lambda x: -(x.volume_24h or 0)) if x is not best][:3]
    return best


def scan(sym: Symbol, md: MarketData, vs: Venues) -> tuple[list[Listing], dict[str, str]]:
    """Every market for one asset: Hyperliquid perp + each exchange's spot and perpetual (quote as given, else USDT), most
    liquid first, plus the exchanges that couldn't be read. DEX pools are added only when no exchange lists it."""
    errors: dict[str, str] = {}
    out: list[Listing] = []
    if sym.address or sym.venue == "dex":
        return [dex_resolve(vs, sym)], errors
    try:
        hl = hl_listing(md, sym.base)
        if hl:
            out.append(hl)
    except MarketError as e:
        errors["Hyperliquid"] = str(e)
    venues = (sym.venue,) if sym.venue in EXCHANGES else EXCHANGES
    kinds = (sym.kind,) if sym.kind else ("spot", "perp")
    found, errs = _exchange_scan(vs, sym.base, sym.quote if sym.quote not in (None, "USD") else "USDT", venues, kinds)
    out += found
    errors.update(errs)
    if not out:
        try:
            out.append(dex_resolve(vs, Symbol(base=sym.base, chain=sym.chain)))
        except Ambiguous:
            raise
        except NotFound as e:
            errors["DEX"] = str(e)
    return sorted(out, key=lambda x: -(x.volume_24h or 0)), errors


# ---------------------------------------------------------------- candles
def candles(listing: Listing, tf: str, n: int, md: MarketData, vs: Venues) -> list[Candle]:
    """Oldest first; the last one may still be forming (as on Hyperliquid)."""
    if listing.venue == "hyperliquid":
        return md.candles(listing.symbol, tf, n)
    try:
        if listing.venue == "bybit":
            cat = "linear" if listing.kind == "perp" else "spot"
            rows = vs.get(f"{BYBIT}/v5/market/kline", {"category": cat, "symbol": listing.symbol, "interval": BYBIT_TF[tf], "limit": min(n, 1000)}, 30)["result"]["list"]
            out = [Candle(int(r[0]), _f(r[1]), _f(r[2]), _f(r[3]), _f(r[4]), _f(r[5])) for r in rows]
        elif listing.venue == "okx":
            rows = vs.get(f"{OKX}/api/v5/market/candles", {"instId": listing.symbol, "bar": OKX_TF[tf], "limit": min(n, 300)}, 30).get("data") or []
            out = [Candle(int(r[0]), _f(r[1]), _f(r[2]), _f(r[3]), _f(r[4]), _f(r[5])) for r in rows]
        elif listing.venue == "binance":
            base = f"{BINANCE_PERP}/fapi/v1" if listing.kind == "perp" else f"{BINANCE_SPOT}/api/v3"
            rows = vs.get(f"{base}/klines", {"symbol": listing.symbol, "interval": tf, "limit": min(n, 1000)}, 30)
            out = [Candle(int(r[0]), _f(r[1]), _f(r[2]), _f(r[3]), _f(r[4]), _f(r[5])) for r in rows]
        else:
            weekly = tf == "1w"  # GeckoTerminal has no weekly candles: built here from its daily ones (UTC weeks from Monday)
            if tf not in GECKO_TF and not weekly:
                raise MarketError(f"DEX candles come in 1m 5m 15m 1h 4h 1d 1w (no {tf}).")
            unit, agg = GECKO_TF["1d" if weekly else tf]
            body = vs.get(f"{GECKO}/networks/{listing.network}/pools/{listing.symbol}/ohlcv/{unit}", {"aggregate": agg, "limit": min(n * 7 if weekly else n, 1000), "currency": "usd"}, 60)
            rows = ((body.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
            out = [Candle(int(r[0]) * 1000, _f(r[1]), _f(r[2]), _f(r[3]), _f(r[4]), _f(r[5])) for r in rows]
            if weekly:
                out = weeks(out)
    except StructureError as e:
        raise MarketError(f"{listing.label} {e}.") from e
    out.sort(key=lambda c: c.t)
    if tf not in INTERVALS:
        raise MarketError(f"Unknown timeframe {tf}.")
    return out[-n:]


WEEK_MS = 604_800_000
MONDAY_MS = 345_600_000  # 1970-01-05, the first Monday: weeks start there (as on the exchanges)


def weeks(days: list[Candle]) -> list[Candle]:
    """Daily candles -> weekly ones (UTC, Monday to Sunday). The last week may still be forming."""
    out: list[Candle] = []
    for c in sorted(days, key=lambda c: c.t):
        start = (c.t - MONDAY_MS) // WEEK_MS * WEEK_MS + MONDAY_MS
        if out and out[-1].t == start:
            w = out[-1]
            out[-1] = Candle(w.t, w.o, max(w.h, c.h), min(w.l, c.l), c.c, w.v + c.v)
        else:
            out.append(Candle(start, c.o, c.h, c.l, c.c, c.v))
    return out


def ambiguity_text(e: Ambiguous) -> str:
    """The choices, one line each, with the full contract address to copy (HTML)."""
    from app.tradingos.bot import esc
    from app.tradingos.readout import usd_short

    lines = [esc(str(e))]
    for o in e.options:
        lines.append(f"· {esc(o.pool_name or o.base)} · {esc(o.label)} · liquidity {usd_short(o.liquidity)}\n  <code>{esc(o.token_address or o.symbol)}</code>")
    lines.append("e.g. /chart " + esc(e.options[0].token_address or e.options[0].symbol) + " 1h")
    return "\n".join(lines)


__all__ = ["Ambiguous", "Listing", "NotFound", "ambiguity_text", "candles", "resolve", "scan", "short_address"]
