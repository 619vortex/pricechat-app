"""The evidence pack: everything the Investment Committee may reason from, for one market at one moment, with a reference
for every fact. The committee's analysts see this pack and nothing else, so the rules here are the committee's honesty:

- Market: tickers.symbol_args + venues.resolve, the same resolution /chart and /signal use. A Hyperliquid perpetual when
  Hyperliquid lists it (never spot in its place); otherwise the venues.py market (OKX first), marked hl_tradable false and
  labelled with its exchange and market type. Unknown -> EvidenceError("unknown_symbol"); a DEX ticker shared by several
  tokens -> EvidenceError("ambiguous", choices); no price at all -> "no_data"; in live mode a stale price AND stale candles
  -> "stale" (nothing current to reason on). Hyperliquid unreadable while it would be the first choice -> "no_data" (an
  exchange's spot market is never quietly substituted for the perpetual).
- Candles: closed candles only (open + span <= as_of). Price: Hyperliquid mark (live), the exchange's last trade (other
  venues, live) or the last closed candle's close (backtest). A Hyperliquid snapshot older than market.STALE_AFTER sets
  price.stale; a run-timeframe candle older than 2 periods makes the technical section "stale".
- Signal: the /signal answer. Hyperliquid perps in live mode run signals.analyze with the admin's settings (read, never
  created); everywhere else the candle-only checks of the same rules (readout.chart_rules), which the rule name says.
- Missing data is reported missing: a section that can't be read is "unavailable" with the reason; nothing is filled in.
- Point-in-time (mode="backtest", as_of required; a live pack is always built for now): candles, funding, Fear & Greed and Price Chat's own open-interest snapshots are cut at
  as_of; news, CoinGecko, the live order book / exchange listings, the trade tape and community counts have no history and
  are "unavailable" with the limitation "no point-in-time history (backtest)".
- Sections: technical.py, news.py, sentiment.py, coingecko.py document their own lean / score rules. Fundamental: neutral
  (score 0) whenever it has data, unknown otherwise; low liquidity (24h volume < $1M), FDV > 3x market cap and circulating
  < 50% of total supply are listed as risk flags, never as a lean.
- HTTP: one httpx.AsyncClient (injectable for tests), 8 s timeouts, short TTL caches; the Hyperliquid market snapshot,
  candles and exchange tickers come from the shared market.data() / venues.client() caches."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.committee.evidence import coingecko, news, sentiment, technical
from app.committee.evidence.common import (
    NO_HISTORY,
    EvidenceError,
    FetchError,
    Refs,
    aware,
    cache_get,
    cache_put,
    client,
    health,
    iso,
    mark,
    post_json,
    rnd,
)
from app.tradingos import market, tickers, venues
from app.tradingos.market import API as HL_API
from app.tradingos.market import INTERVALS, STALE_AFTER, Candle, MarketError
from app.tradingos.tickers import NAMES

OKX_CANDLES = "https://www.okx.com/api/v5/market/candles"
LOW_VOLUME = 1_000_000
NOT_REACHABLE = ("couldn't be reached", "rate-limiting", "region", "answered 5")


def _section(status: str = "unavailable", limitations: list[str] | None = None) -> dict[str, Any]:
    return {"status": status, "limitations": limitations or [], "facts": {}, "items": [], "lean": "unknown", "score": None,
            "reasons": [], "refs": []}


def _coin_name(symbol: str, cg: dict[str, Any] | None) -> str | None:
    if cg and cg.get("name") and not cg.get("ambiguous"):
        return str(cg["name"])
    names = [k.title() for k, v in NAMES.items() if v == symbol.upper() and len(k) >= 4]
    return names[0] if names else None


# ---------------------------------------------------------------- market resolution
async def _resolve(symbol: str, md: market.MarketData, vs: Any) -> tuple[tickers.Symbol, venues.Listing]:
    sym, _ = tickers.symbol_args(symbol.split())
    if sym is None:
        raise EvidenceError("unknown_symbol", f"'{symbol}' isn't a market Price Chat can read.")
    if sym.venue is None and not sym.address and sym.kind in (None, "perp") and sym.quote in (None, "USD"):
        try:  # Hyperliquid comes first: if it can't be read, say so rather than quietly picking an exchange's spot market
            await asyncio.to_thread(md.markets)
        except MarketError as e:
            mark("hyperliquid", False, str(e))
            raise EvidenceError("no_data", f"Hyperliquid can't be read right now ({e}), so the market can't be resolved.") from e
    try:
        listing = await asyncio.to_thread(venues.resolve, sym, md, vs)
    except venues.Ambiguous as e:
        raise EvidenceError("ambiguous", str(e), [{"address": o.token_address or o.symbol, "label": o.label, "pool": o.pool_name,
                                                   "liquidity_usd": o.liquidity} for o in e.options]) from e
    except venues.NotFound as e:
        code = "no_data" if any(s in str(e) for s in NOT_REACHABLE) else "unknown_symbol"
        raise EvidenceError(code, str(e)) from e
    except MarketError as e:
        mark("hyperliquid", False, str(e))
        raise EvidenceError("no_data", str(e)) from e
    return sym, listing


def _asset(listing: venues.Listing) -> dict[str, Any]:
    if listing.venue == "hyperliquid":
        label = "Hyperliquid perpetual"
        mtype = "perp"
    elif listing.venue == "dex":
        label = f"{listing.label} pool {tickers.short_address(listing.symbol)}"
        mtype = "dex"
    else:
        mtype = listing.kind
        label = f"{venues.NAME[listing.venue]} {'spot' if listing.kind == 'spot' else 'perpetual'} {listing.symbol}"
    return {"symbol": listing.base.upper() if listing.venue != "dex" else listing.base, "venue": listing.venue, "market_type": mtype,
            "market_id": listing.symbol, "label": label, "hl_tradable": listing.venue == "hyperliquid",
            "alternatives": list(listing.alternatives)}


# ---------------------------------------------------------------- candles
async def _hl_candles_at(http: httpx.AsyncClient, coin: str, tf: str, as_of: datetime, n: int) -> list[Candle]:
    """Hyperliquid candles ending at as_of (stored research history first, when it reaches as_of)."""
    span = INTERVALS[tf] * 1000
    end = int(as_of.timestamp() * 1000)
    try:
        from app.tradingos import history

        rows = await asyncio.to_thread(history.candles, coin, tf)
        if rows and rows[-1].t + span >= end - span and rows[0].t <= end - span * n:
            return [c for c in rows if c.t + span <= end][-n:]
    except (OSError, ValueError, KeyError):
        pass  # no stored history for this coin / interval: ask Hyperliquid
    key = f"hlc:{coin}:{tf}:{end}"
    hit = cache_get(key, 300.0)
    if hit is not None:
        return hit
    out = await post_json(http, HL_API, {"type": "candleSnapshot", "req": {"coin": coin, "interval": tf, "startTime": end - span * (n + 2),
                                                                           "endTime": end}}, what="Hyperliquid candleSnapshot")
    if not isinstance(out, list):
        raise FetchError("Hyperliquid sent an unexpected candles answer")
    return cache_put(key, [Candle(int(r["t"]), float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]), float(r["v"])) for r in out])


async def _frames(http: httpx.AsyncClient, listing: venues.Listing, tfs: list[str], as_of: datetime, mode: str, md: Any,
                  vs: Any) -> tuple[dict[str, list[Candle]], dict[str, str], float]:
    """{tf: closed candles <= as_of}, {tf: error}, fetched_at."""
    as_ms = int(as_of.timestamp() * 1000)

    async def one(tf: str) -> list[Candle]:
        if listing.venue == "hyperliquid" and mode == "backtest":
            return await _hl_candles_at(http, listing.symbol, tf, as_of, technical.CANDLES)
        if listing.venue == "hyperliquid":
            return await asyncio.to_thread(md.candles, listing.symbol, tf, technical.CANDLES + 1)
        return await asyncio.to_thread(venues.candles, listing, tf, technical.CANDLES + 1, md, vs)

    got = await asyncio.gather(*(one(tf) for tf in tfs), return_exceptions=True)
    frames: dict[str, list[Candle]] = {}
    errors: dict[str, str] = {}
    for tf, res in zip(tfs, got):
        if isinstance(res, BaseException):
            errors[tf] = str(res) or type(res).__name__
            frames[tf] = []
        else:
            frames[tf] = technical.closed_by(list(res), tf, as_ms)[-technical.CANDLES:]
    return frames, errors, time.time()


def _candle_source(listing: venues.Listing, tf: str) -> tuple[str, str | None]:
    if listing.venue == "hyperliquid":
        return f"Hyperliquid {listing.symbol} perpetual {tf} candles (candleSnapshot)", HL_API
    if listing.venue == "okx":
        return f"OKX {listing.symbol} {tf} candles", f"{OKX_CANDLES}?instId={listing.symbol}&bar={venues.OKX_TF[tf]}"
    if listing.venue == "dex":
        return f"GeckoTerminal {listing.label} {tf} candles", f"{venues.GECKO}/networks/{listing.network}/pools/{listing.symbol}"
    return f"{venues.NAME[listing.venue]} {listing.symbol} {tf} candles", None


def vol_tf(frames: dict[str, list[Candle]]) -> str | None:
    return next((tf for tf in ("1h", "4h", "1d") if frames.get(tf)), None)


def _volume_24h(frames: dict[str, list[Candle]], as_of: datetime) -> float | None:
    """Notional volume of the 24 h before as_of from closed candles (finest frame that covers them), or None."""
    end = as_of.timestamp() * 1000
    for tf in ("1h", "4h", "1d"):
        cs = frames.get(tf) or []
        span = INTERVALS[tf] * 1000
        if cs and cs[0].t <= end - 86_400_000 and cs[-1].t + span >= end - span:
            return sum(c.v * c.c for c in cs if c.t >= end - 86_400_000 and c.t + span <= end)
    return None


# ---------------------------------------------------------------- signal
def _settings(db: Any) -> tuple[float, float, float]:
    """The admin's signal rule settings, read without creating the row (defaults = the column defaults)."""
    try:
        from sqlalchemy import select

        from app.models.tradingos import TsSettings

        s = db.scalar(select(TsSettings).limit(1)) if db is not None else None
    except Exception:  # noqa: BLE001 - no settings table: the documented defaults
        s = None
    if s is None:
        return 50_000_000.0, 8.0, 1.6
    return float(s.min_volume_usd), float(s.max_spread_bps), float(s.min_rr)


async def _signal(db: Any, listing: venues.Listing, tf: str, mode: str, run_cs: list[Candle], md: Any) -> dict[str, Any]:
    from app.tradingos import readout, signals

    if listing.venue == "hyperliquid" and mode == "live":
        try:
            a = await asyncio.to_thread(signals.analyze, md, listing.symbol, tf, *_settings(db))
        except MarketError as e:
            return {"rule": "trend_pullback_v1", "verdict": "WAIT", "first_fail": f"signal rules unavailable ({e})", "scope": "full"}
        verdict = "WAIT" if a.decision == "NO TRADE" else a.decision
        return {"rule": a.strategy or "trend_pullback_v1", "verdict": verdict, "first_fail": a.reasons[0] if verdict == "WAIT" and a.reasons else None,
                "scope": "full"}
    verdict, note = readout.chart_rules(run_cs)
    return {"rule": "trend_pullback_v1", "verdict": verdict, "first_fail": note if verdict == "WAIT" else None, "scope": "chart rules",
            "note": "candle-only checks of the same rules (volume, spread, depth, funding and BTC checks need live Hyperliquid data)"}


# ---------------------------------------------------------------- Hyperliquid meta
async def _only_isolated(http: httpx.AsyncClient, coin: str) -> bool | None:
    meta = cache_get("hlmeta", 600.0)
    if meta is None:
        meta = cache_put("hlmeta", await post_json(http, HL_API, {"type": "meta"}, what="Hyperliquid meta"))
    for u in (meta or {}).get("universe", []):
        if str(u.get("name", "")).upper() == coin.upper():
            return bool(u.get("onlyIsolated", False))
    return None


# ---------------------------------------------------------------- the pack
async def build_evidence(db: Any, symbol: str, timeframe: str = "1h", as_of: datetime | None = None, mode: str = "live", *,
                         http: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """The evidence pack (see the committee contract). Raises EvidenceError when there's no market to reason about."""
    if mode not in ("live", "backtest"):
        raise ValueError("mode must be 'live' or 'backtest'")
    if timeframe not in INTERVALS:
        raise ValueError(f"timeframe must be one of {', '.join(INTERVALS)}")
    now = datetime.now(timezone.utc)
    if mode == "backtest" and as_of is None:
        raise ValueError("a backtest pack needs as_of")
    as_of = min(aware(as_of), now) if (as_of and mode == "backtest") else now  # a live pack is always "now"
    own = http is None
    http = http or client()
    try:
        return await _build(db, symbol, timeframe, as_of, mode, http)
    finally:
        if own:
            await http.aclose()


async def _build(db: Any, symbol: str, timeframe: str, as_of: datetime, mode: str, http: httpx.AsyncClient) -> dict[str, Any]:
    md, vs = market.data(), venues.client()
    bt = mode == "backtest"
    refs = Refs()
    degraded: list[str] = []
    fetched = lambda: iso(time.time())  # noqa: E731

    sym, listing = await _resolve(symbol, md, vs)
    mark("hyperliquid" if listing.venue == "hyperliquid" else "venues", True, f"resolved {listing.label}")
    asset = _asset(listing)
    hl = listing.venue == "hyperliquid"
    base = asset["symbol"] if listing.venue != "dex" else listing.base

    # Hyperliquid perp context (live): mark, funding, OI, volume. Also for assets resolved elsewhere that HL lists (funding only).
    hl_market = None
    hl_at = None
    hl_coin = listing.symbol if hl else None
    if not bt or hl:
        try:
            hl_at, ms = await asyncio.to_thread(md.markets)
            if hl:
                hl_market = ms.get(listing.symbol.upper())
            elif listing.venue != "dex":
                alt = ms.get(base.upper())
                hl_coin = alt.coin if alt else None
            mark("hyperliquid", True, f"{len(ms)} perpetuals")
        except MarketError as e:
            mark("hyperliquid", False, str(e))
            degraded.append(f"hyperliquid: {e}")

    # ------------------------------------------------ technical
    tfs = technical.frames_for(timeframe)
    frames, ferr, frames_at = await _frames(http, listing, tfs, as_of, mode, md, vs)
    for tf, e in ferr.items():
        degraded.append(f"{tf} candles: {e}")
    if ferr and len(ferr) == len(tfs):
        mark("hyperliquid" if hl else "venues", False, "; ".join(ferr.values())[:200])
    run_cs = frames.get(timeframe) or []

    # price: Hyperliquid mark / the exchange's last trade (live), the last closed candle (backtest)
    price: dict[str, Any] | None = None
    if bt:
        if run_cs:
            last_close = (run_cs[-1].t + INTERVALS[timeframe] * 1000) / 1000
            price = {"value": rnd(run_cs[-1].c), "kind": "close", "source": listing.venue, "ts": iso(last_close), "stale": False}
    elif hl and hl_market is not None and hl_at is not None:
        price = {"value": rnd(hl_market.mark), "kind": "mark", "source": "hyperliquid", "ts": iso(hl_at),
                 "stale": time.time() - hl_at > STALE_AFTER}
    elif listing.price > 0:
        price = {"value": rnd(listing.price), "kind": "last", "source": listing.venue, "ts": fetched(), "stale": False}
    if price is None:
        raise EvidenceError("no_data", f"No price for {asset['label']} at {iso(as_of)}.")
    tech = technical.build(frames, timeframe, price["value"], as_of)
    if bt:
        price["stale"] = tech["status"] == "stale"
    elif price["stale"]:
        if tech["status"] in ("stale", "unavailable"):
            raise EvidenceError("stale", f"Market data for {asset['label']} is stale (price {price['ts']}, candles too).")
        tech["limitations"].append(f"price is older than {STALE_AFTER:.0f} s: stale")
        tech["status"] = "stale"
    pref = refs.add("P", f"{asset['label']} {price['kind']} price", HL_API if price["source"] == "hyperliquid" else None, price["ts"], fetched())

    frame_ref: dict[str, str] = {}
    for tf in tfs:
        if frames.get(tf):
            src, url = _candle_source(listing, tf)
            frame_ref[tf] = refs.add("T", src, url, (tech["facts"]["frames"].get(tf) or {}).get("last_closed"), iso(frames_at))
    signal = await _signal(db, listing, timeframe, mode, run_cs, md) if run_cs else None
    if signal:
        sref = refs.add("T", f"Price Chat signal rules ({signal['rule']}, {signal['scope']})", None, iso(as_of), fetched())
        tech["facts"]["signal"] = {**signal, "ref": sref}
    tsec = _section(tech["status"], tech["limitations"])
    tsec.update(facts=tech["facts"], lean=tech["lean"], score=tech["score"], refs=list(frame_ref.values()) + ([sref] if signal else []))
    tsec["reasons"] = [{"text": w, "refs": [frame_ref[w.split()[0]]] if w.split()[0] in frame_ref else []} for w in tech["why"]]
    if signal:
        tsec["reasons"].append({"text": f"{signal['rule']} ({signal['scope']}): {signal['verdict']}"
                                + (f", first failing check: {signal['first_fail']}" if signal["first_fail"] else ""), "refs": [sref]})

    # ------------------------------------------------ CoinGecko (live only) + names
    cg: dict[str, Any] | None = None
    cg_err = None
    if not bt and listing.venue != "dex":
        try:
            cg = await coingecko.lookup(http, base)
        except FetchError as e:
            cg_err = str(e)
            degraded.append(f"coingecko: {e}")
    name = _coin_name(base, cg)

    # ------------------------------------------------ fundamental
    fsec = _section()
    ff: dict[str, Any] = {"token_unlocks": "unavailable"}
    flim = ["token unlocks: no verified unlock-schedule source is configured"]
    frefs: list[str] = []
    if bt:
        flim.append(f"exchange listings, Hyperliquid meta (leverage, OI, spread) and CoinGecko: {NO_HISTORY}")
        if hl and hl_market is not None:  # a static instrument spec, not market data: today's value applies to the past
            ff["sz_decimals"] = hl_market.sz_decimals
            flim.append("sz_decimals: Hyperliquid's current lot size from the market list (a static instrument spec)")
        vol = _volume_24h(frames, as_of)
        if vol is not None:
            ff["volume_24h_usd"] = rnd(vol)
            frefs.append(refs.add("F", f"24h volume = sum of volume x close of the closed {vol_tf(frames)} candles in the 24 h before as_of",
                                  None, iso(as_of), fetched()))
            flim.append("24h volume is computed from the closed candles before as_of (point-in-time), not the exchange's 24h ticker")
    else:
        if hl and hl_market is not None:
            r = refs.add("F", "Hyperliquid metaAndAssetCtxs", HL_API, iso(hl_at), iso(hl_at))
            frefs.append(r)
            try:
                iso_only = await _only_isolated(http, listing.symbol)
            except FetchError as e:
                iso_only = None
                flim.append(f"only_isolated unknown: {e}")
            ff.update(max_leverage=hl_market.max_leverage, sz_decimals=hl_market.sz_decimals, only_isolated=iso_only,
                      oi_usd=rnd(hl_market.oi_usd), volume_24h_usd=rnd(hl_market.day_volume),
                      spread_bps=round(sp, 2) if (sp := hl_market.spread_bps) is not None else None)
            try:  # the live book: spread and USD depth within 0.5% of mid, for the risk gate's liquidity check
                b = await asyncio.to_thread(md.book, listing.symbol)
                ff["book"] = {"spread_bps": round(b["spread_bps"], 2), "bid_depth_usd": rnd(b["bid_depth_usd"]),
                              "ask_depth_usd": rnd(b["ask_depth_usd"])}
                frefs.append(refs.add("F", f"Hyperliquid l2Book {listing.symbol}", HL_API, fetched(), fetched()))
            except MarketError as e:
                flim.append(f"order book unavailable: {e}")
        elif listing.volume_24h is not None and listing.venue != "dex":
            frefs.append(refs.add("F", f"{venues.NAME[listing.venue]} 24h ticker {listing.symbol}", None, fetched(), fetched()))
            ff["volume_24h_usd"] = rnd(listing.volume_24h)
        elif listing.venue == "dex":
            frefs.append(refs.add("F", f"GeckoTerminal pool {listing.symbol}", f"{venues.GECKO}/networks/{listing.network}/pools/{listing.symbol}",
                                  fetched(), fetched()))
            ff.update(volume_24h_usd=rnd(listing.volume_24h), liquidity_usd=rnd(listing.liquidity), network=listing.network,
                      token_address=listing.token_address)
        if listing.venue != "dex":
            try:
                found, errs = await asyncio.to_thread(venues.scan, tickers.Symbol(base=base), md, vs)
                ff["listed_on"] = sorted({x.label for x in found})
                frefs.append(refs.add("F", "Exchange listings (Hyperliquid, Binance, Bybit, OKX 24h tickers)", None, fetched(), fetched()))
                flim += [f"{v} couldn't be checked: {e}" for v, e in errs.items()]
                if hl and not asset["alternatives"]:
                    asset["alternatives"] = [x for x in ff["listed_on"] if x != "Hyperliquid Perpetual"][:3]
            except (venues.NotFound, venues.Ambiguous, MarketError) as e:
                flim.append(f"exchange listings unavailable: {e}")
        if cg is not None:
            cref = refs.add("F", f"CoinGecko {cg['id']}" if cg.get("id") else f"CoinGecko search '{base}'",
                            f"{coingecko.API}/coins/{cg['id']}" if cg.get("id") else f"{coingecko.API}/search?query={base}",
                            cg.get("last_updated"), fetched())
            frefs.append(cref)
            cg_fact = {k: cg.get(k) for k in ("id", "matched_by", "ambiguous", "market_cap", "fdv", "circulating", "total", "max_supply", "rank",
                                               "ath", "ath_change_pct", "categories", "genesis_date", "last_updated")}
            if cg.get("ambiguous"):
                cg_fact = {"id": None, "matched_by": None, "ambiguous": True, "candidates": cg.get("candidates")}
                flim.append(f"CoinGecko: several coins use the ticker {base} inside the top {coingecko.AMBIGUOUS_RANK}; no CoinGecko numbers used")
            elif not cg.get("id"):
                cg_fact = {"id": None, "matched_by": None, "ambiguous": False}
                flim.append(f"CoinGecko: no ranked coin with the exact symbol {base}; no CoinGecko numbers used")
            ff["coingecko"] = cg_fact
        elif cg_err:
            flim.append(f"CoinGecko unavailable: {cg_err}")
        elif listing.venue == "dex":
            flim.append("CoinGecko isn't matched for DEX tokens (tickers aren't unique on DEXes)")
    if db is not None and hl_coin:
        try:
            oi_now = sentiment.open_interest(db, hl_coin, as_of)
        except Exception as e:  # noqa: BLE001 - our own table missing or unreadable: say so
            oi_now = None
            flim.append(f"open-interest history unreadable ({type(e).__name__})")
        if oi_now:
            ff["oi_history"] = oi_now
            if bt:
                ff["oi_usd"] = oi_now["oi_usd"]
            frefs.append(refs.add("F", "Price Chat open-interest snapshots (Hyperliquid, every 15 min)", None, oi_now["ts"], fetched()))
    risks = []
    vol = ff.get("volume_24h_usd")
    if vol is not None and vol < LOW_VOLUME:
        risks.append(f"low liquidity: 24h volume ${vol:,.0f} (< ${LOW_VOLUME:,})")
    c = ff.get("coingecko") or {}
    if c.get("fdv") and c.get("market_cap") and c["fdv"] > 3 * c["market_cap"]:
        risks.append(f"FDV {c['fdv'] / c['market_cap']:.1f}x the market cap (future supply overhang)")
    if c.get("circulating") and c.get("total") and c["circulating"] / c["total"] < 0.5:
        risks.append(f"only {c['circulating'] / c['total'] * 100:.0f}% of the total supply circulates")
    ff["risk_flags"] = risks
    has = any(k in ff for k in ("max_leverage", "volume_24h_usd", "oi_usd", "listed_on")) or bool(c.get("id"))
    extra = [x for x in flim if not x.startswith("token unlocks")]
    fsec.update(facts=ff, limitations=flim, refs=frefs, status=("ok" if not extra else "partial") if has else "unavailable",
                lean="neutral" if has else "unknown", score=0 if has else None,
                reasons=[{"text": f"risk: {r}", "refs": list(frefs)} for r in risks])

    # ------------------------------------------------ news
    if bt:
        nsec = _section("unavailable", [f"news: {NO_HISTORY}"])
    else:
        got = await news.collect(http, base, name, as_of)
        nsec = _section(got["status"], got["limitations"])
        ids = {}
        for fr in got["feeds"]:
            if fr["ok"]:
                ids[fr["source"]] = refs.add("N", f"{fr['source']} RSS", fr["url"], None, fetched())
        for it in got["items"]:
            it["ref"] = ids.get(it["source"])
        lean_, score, why = news.lean(got["items"])
        nsec.update(items=got["items"], lean=lean_, score=score, refs=list(ids.values()),
                    facts={"feeds": [{k: f[k] for k in ("source", "ok", "items")} for f in got["feeds"]], "window_h": news.MAX_AGE_H,
                           "matched": sum(1 for i in got["items"] if i["match"] != "macro"),
                           "macro": sum(1 for i in got["items"] if i["match"] == "macro")},
                    reasons=[{"text": w, "refs": list(ids.values())} for w in why])
        if got["status"] == "unavailable":
            degraded.append("news: every feed failed")

    # ------------------------------------------------ sentiment
    sf: dict[str, Any] = {}
    slim: list[str] = []
    srefs: dict[str, str] = {}
    tasks: dict[str, Any] = {"fear_greed": sentiment.fear_greed(http, as_of)}
    if hl_coin:
        tasks["funding"] = sentiment.funding(http, hl_coin, as_of)
    res = dict(zip(tasks, await asyncio.gather(*tasks.values(), return_exceptions=True)))
    if "funding" in res:
        f = res["funding"]
        if isinstance(f, BaseException):
            slim.append(f"funding unavailable: {f}")
        else:
            srefs["funding"] = refs.add("S", f"Hyperliquid {hl_coin} perpetual fundingHistory", HL_API, f["ts"], fetched())
            sf.update({k: f[k] for k in ("funding_1h_pct", "funding_24h_avg_pct", "funding_7d_avg_pct")}, funding_ref=srefs["funding"])
            if not hl:
                slim.append(f"funding is the Hyperliquid {hl_coin} perpetual's, not {asset['label']}'s (spot has no funding)")
            if not f["covers_7d"]:
                slim.append("funding history covers less than 7 days (newer listing)")
    else:
        slim.append("funding: no Hyperliquid perpetual for this asset")
    fg = res["fear_greed"]
    if isinstance(fg, BaseException):
        slim.append(f"Fear & Greed unavailable: {fg}")
    else:
        srefs["fear_greed"] = refs.add("S", "alternative.me Crypto Fear & Greed Index (daily)", sentiment.FNG, fg["ts"], fetched())
        sf["fear_greed"] = {**fg, "ref": srefs["fear_greed"]}
    oi_h = ff.get("oi_history")
    if oi_h:
        srefs["oi"] = frefs[-1]
        sf.update(oi_usd=oi_h["oi_usd"], oi_change_24h_pct=oi_h["oi_change_24h_pct"], oi_ref=srefs["oi"])
        if oi_h["oi_change_24h_pct"] is None:
            slim.append("open-interest history doesn't reach back 24 h")
    elif hl_market is not None and not bt:
        sf.update(oi_usd=rnd(hl_market.oi_usd), oi_change_24h_pct=None, oi_ref=frefs[0] if frefs else None)
        slim.append("open-interest 24h change: no Price Chat snapshots for this market")
    if bt:
        slim += [f"taker flow: {NO_HISTORY}", f"community mentions: {NO_HISTORY}"]
    else:
        flow = sentiment.taker_flow(hl_coin) if hl_coin else None
        if flow:
            srefs["flow"] = refs.add("S", f"Hyperliquid {hl_coin} trade tape (websocket, aggressor side)", "wss://api.hyperliquid.xyz/ws", fetched(), fetched())
            sf["taker_flow"] = {**flow, "ref": srefs["flow"]}
            slim.append("taker flow is noisy (one venue, at most one hour)")
        else:
            slim.append("taker flow: the Hyperliquid trade tape isn't recording this market in this process")
        try:
            comm = sentiment.community(db, base, name, as_of) if db is not None else None
            mark("community", True, "ingest tables read" if comm else "ingest tables empty")
        except Exception as e:  # noqa: BLE001 - tables missing on this database: reported, never guessed
            comm = None
            mark("community", False, type(e).__name__)
            slim.append(f"community mentions unreadable ({type(e).__name__})")
        if comm:
            srefs["community"] = refs.add("S", "Price Chat Telegram + X ingest (telegram_messages, social_posts)", None, iso(as_of), fetched())
            sf["community"] = {**comm, "ref": srefs["community"]}
            slim.append("community mentions are noisy (our own chats and watched X accounts only)")
        elif not any(s.startswith("community") for s in slim):
            slim.append("community mentions: no Telegram / X messages ingested in the 8 days before as_of")
    lean_, score, why = sentiment.lean(sf)
    s_status = "unavailable" if not (set(srefs) - {"oi"}) and "oi_usd" not in sf else "ok" if not slim else "partial"
    ssec = _section(s_status, slim)
    ssec.update(facts=sf, lean=lean_, score=score, refs=list(dict.fromkeys(srefs.values())),
                reasons=[{"text": w, "refs": [srefs["funding"]] if "funding" in w and "funding" in srefs else
                          [srefs["fear_greed"]] if "Fear" in w and "fear_greed" in srefs else []} for w in why])

    return {"asset": asset, "timeframe": timeframe, "as_of": iso(as_of), "mode": mode, "price": {**price, "ref": pref},
            "sections": {"technical": tsec, "fundamental": fsec, "news": nsec, "sentiment": ssec},
            "refs": refs.rows, "built_at": iso(time.time()), "degraded": degraded}


# ---------------------------------------------------------------- /committee status
PROBE_EVERY = 300.0


async def provider_status(http: httpx.AsyncClient | None = None) -> dict[str, dict[str, Any]]:
    """{name: {"ok", "age_s", "detail"}} for every provider. Uses what the last packs recorded; a provider not heard from
    in 5 minutes gets one cheap probe. age_s = seconds since it last answered (None: never)."""
    own = http is None
    http = http or client()
    try:
        h = health()
        stale = [p for p in ("hyperliquid", "venues", "coingecko", "news", "fear_greed", "community")
                 if p not in h or h[p]["checked"] is None or time.time() - h[p]["checked"] > PROBE_EVERY]
        await asyncio.gather(*(_probe(p, http) for p in stale), return_exceptions=True)
    finally:
        if own:
            await http.aclose()
    out = {}
    now = time.time()
    for p, v in health().items():
        out[p] = {"ok": bool(v["ok"]), "age_s": round(now - v["last_ok"], 1) if v["last_ok"] else None, "detail": v["detail"]}
    return out


async def _probe(name: str, http: httpx.AsyncClient) -> None:
    from app.committee.evidence.common import get_json

    try:
        if name == "hyperliquid":
            _, ms = await asyncio.to_thread(market.data().markets)
            mark(name, True, f"{len(ms)} perpetuals")
        elif name == "venues":
            vs = venues.client()
            await asyncio.to_thread(vs.get, f"{venues.OKX}/api/v5/market/ticker", {"instId": "BTC-USDT"}, 20)
            mark(name, True, "OKX ticker answered")
        elif name == "coingecko":
            await get_json(http, f"{coingecko.API}/ping", what="CoinGecko", headers=coingecko._headers())
            mark(name, True, "ping answered")
        elif name == "news":
            await news.collect(http, "BTC", "Bitcoin", datetime.now(timezone.utc))  # marks itself
        elif name == "fear_greed":
            await sentiment.fear_greed(http, datetime.now(timezone.utc))  # marks itself
        elif name == "community":
            from sqlalchemy import func, select

            from app.db.session import SessionLocal
            from app.models.telegram import TelegramMessage

            def count() -> int:
                with SessionLocal() as s:
                    since = datetime.now(timezone.utc) - timedelta(hours=24)
                    return int(s.scalar(select(func.count()).select_from(TelegramMessage).where(TelegramMessage.sent_at > since)) or 0)
            n = await asyncio.to_thread(count)
            mark(name, True, f"{n} Telegram messages ingested in 24 h" if n else "no Telegram messages ingested in 24 h")
    except (FetchError, MarketError, Exception) as e:  # noqa: BLE001 - a failed probe is a status, not a crash
        mark(name, False, str(e) or type(e).__name__)
