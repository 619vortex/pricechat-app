"""Kiwi Brain 2.0 API (/api/members/kiwi-brain/...): the signed-in member's own command center. Every route requires the
member session; nothing here returns another member's account, trades, config or events, and nothing here places orders
(Ask Kiwi and Teach Your Kiwi only PROPOSE changes; the member applies them through the existing validated settings)."""

import asyncio
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DbSession, Runtime
from app.api.routes.members import SessionToken, _require
from app.api.schemas import StrictInput
from app.models.tradingos import KbEvent
from app.tradingos import brain, brainx, market

router = APIRouter(tags=["kiwi-brain"])
_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}


def _cached(key: tuple[Any, ...], ttl: float) -> Any:
    hit = _cache.get(key)
    return hit[1] if hit and time.monotonic() - hit[0] < ttl else None


def _put(key: tuple[Any, ...], val: Any) -> Any:
    if len(_cache) > 500:
        _cache.clear()
    _cache[key] = (time.monotonic(), val)
    return val


def _bad_gateway(e: Exception) -> HTTPException:
    return HTTPException(status.HTTP_502_BAD_GATEWAY, f"Hyperliquid data unavailable: {e}")


@router.get("/members/kiwi-brain/overview")
async def overview(response: Response, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """Health per component, positions with verified protection, exposure, limits and config."""
    response.headers["Cache-Control"] = "no-store"
    m = _require(db, pc_session)
    return await asyncio.to_thread(brainx.overview, db, m, market.data())


@router.get("/members/kiwi-brain/evaluate")
async def evaluate(db: DbSession, pc_session: SessionToken = None, coin: str = Query("BTC", min_length=1, max_length=20)) -> dict[str, Any]:
    """A live rule-by-rule evaluation of one market by the real strategy code (nothing is created or sent)."""
    m = _require(db, pc_session)
    key = ("eval", m.id, coin.upper())
    if (hit := _cached(key, 20)) is not None:
        return hit
    try:
        return _put(key, await asyncio.to_thread(brainx.evaluate, db, m, market.data(), coin))
    except market.MarketError as e:
        raise _bad_gateway(e) from e


@router.get("/members/kiwi-brain/flow")
async def flow(db: DbSession, pc_session: SessionToken = None, coin: str = Query("BTC", min_length=1, max_length=20)) -> dict[str, Any]:
    _require(db, pc_session)
    try:
        return await asyncio.to_thread(brainx.flow, db, market.data(), coin)
    except market.MarketError as e:
        raise _bad_gateway(e) from e


STRUCTURE_LAYERS = {"oi": 60, "cvd": 30, "liq": 30, "zones": 120, "book": 10, "twap": 60, "heat": 60}  # layer -> cache seconds


def structure_layer(db: Any, coin: str, tf: str, layer: str) -> dict[str, Any]:
    """One market-structure layer (app/tradingos/structure.py), trimmed for the browser. Raises StructureError."""
    from datetime import datetime, timezone

    from app.tradingos import structure as ms

    span_ms = market.INTERVALS[tf] * 1000 * 200
    if layer == "oi":
        o = ms.open_interest(coin, tf, 200)
        o.pop("history_by_venue", None)
        return o
    if layer == "cvd":
        return ms.cvd(coin, tf, 200)
    if layer == "liq":
        lq = ms.reported_liquidations(coin, int(time.time() * 1000) - span_ms, db)
        lq["events"] = lq["events"][-3000:]
        return lq
    if layer == "zones":
        return ms.liquidation_zones(coin, tf)
    if layer == "book":
        b = ms.books(coin)
        for x in b["by_venue"].values():
            x.pop("bins", None)
        return b
    if layer == "twap":
        return ms.twap_patterns(coin, 30)
    since = datetime.fromtimestamp(time.time() - min(span_ms / 1000, 2 * 86400), tz=timezone.utc)
    hm = ms.heatmap(db, coin, since)
    snaps = hm["snapshots"]
    step = max(1, -(-len(snaps) // 240))
    hm["snapshots"] = snaps[::step]
    hm["recorded_coins"] = list(ms.HEATMAP_COINS)
    return hm


@router.get("/members/kiwi-brain/structure")
async def structure(db: DbSession, pc_session: SessionToken = None, coin: str = Query("BTC", min_length=1, max_length=20),
                    timeframe: Literal["5m", "15m", "1h", "4h"] = "1h", layers: str = Query("oi,cvd,liq", max_length=80)) -> dict[str, Any]:
    """Market structure layers for the Battlefield: aggregated open interest, cross-venue CVD, reported liquidations,
    ESTIMATED liquidation zones, order-book walls and imbalance, TWAP-like patterns (inferred), the recorded liquidity
    heatmap. Each layer carries its basis (observed / aggregated / estimate / inferred), venues and timestamps; a layer
    that couldn't be read comes back as {"error": why}, never as made-up data."""
    from app.tradingos import structure as ms

    _require(db, pc_session)
    want = [x for x in dict.fromkeys(layers.split(",")) if x in STRUCTURE_LAYERS]
    out: dict[str, Any] = {}
    for layer in want:
        key = ("structure", coin.upper(), timeframe, layer)
        if (hit := _cached(key, STRUCTURE_LAYERS[layer])) is not None:
            out[layer] = hit
            continue
        try:
            out[layer] = _put(key, await asyncio.to_thread(structure_layer, db, coin, timeframe, layer))
        except (ms.StructureError, market.MarketError) as e:
            out[layer] = {"error": str(e)}
    return {"coin": coin.upper(), "timeframe": timeframe, "layers": out, "basis": ms.BASIS}


@router.get("/members/kiwi-brain/contract")
async def contract(db: DbSession, pc_session: SessionToken = None, coin: str = Query("BTC", min_length=1, max_length=20)) -> dict[str, Any]:
    """Hyperliquid contract specs for the simulator (real, from metaAndAssetCtxs)."""
    _require(db, pc_session)
    try:
        mk = await asyncio.to_thread(market.data().market, coin)
    except market.MarketError as e:
        raise _bad_gateway(e) from e
    return {"coin": mk.coin, "mark": mk.mark, "max_leverage": mk.max_leverage, "sz_decimals": mk.sz_decimals, "funding_h": mk.funding,
            "taker_fee": 0.00045, "maker_fee": 0.00015, "min_notional": 10.0, "source": "hyperliquid:info metaAndAssetCtxs",
            "fee_note": "Hyperliquid base-tier fees; your tier may be lower."}


@router.get("/members/kiwi-brain/performance")
def performance(response: Response, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return brainx.performance(db, _require(db, pc_session))


@router.get("/members/kiwi-brain/replays")
def replays(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    return {"items": brainx.replayable(db, _require(db, pc_session))}


@router.get("/members/kiwi-brain/replay")
async def replay(db: DbSession, pc_session: SessionToken = None, signal_id: int | None = Query(None, ge=1),
                 position_id: int | None = Query(None, ge=1)) -> dict[str, Any]:
    m = _require(db, pc_session)
    try:
        return await asyncio.to_thread(brainx.replay, db, m, market.data(), signal_id, position_id)
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except market.MarketError as e:
        raise _bad_gateway(e) from e


@router.get("/members/kiwi-brain/notifications")
def notifications(response: Response, db: DbSession, pc_session: SessionToken = None, after_id: int = Query(0, ge=0)) -> dict[str, Any]:
    """Signal opened / closed / published events (public) after an id, for the website's notifications."""
    response.headers["Cache-Control"] = "no-store"
    _require(db, pc_session)
    q = select(KbEvent).where(KbEvent.kind == "signal", KbEvent.member_id.is_(None), KbEvent.id > after_id).order_by(KbEvent.id.desc()).limit(20)
    rows = [e for e in db.scalars(q) if (e.data or {}).get("after") in ("ACTIVE", "TARGET_HIT", "STOP_HIT", "CLOSED", "EXPIRED")
            or "published" in e.title]
    last = db.scalar(select(KbEvent.id).order_by(KbEvent.id.desc()).limit(1)) or 0
    return {"items": [brain.event_out(e) for e in reversed(rows)], "last_id": last}


# ---------------------------------------------------------------- Teach Your Kiwi
class ConfigIn(StrictInput):
    config: dict[str, Any]
    note: str | None = Field(None, max_length=500)


@router.get("/members/kiwi-brain/config")
def get_config(db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _require(db, pc_session)
    return brainx.current_config(db, m) | {"versions": brainx.config_versions(db, m), "defaults": brainx.DEFAULT_CONFIG}


@router.post("/members/kiwi-brain/config/preview")
def preview_config(payload: ConfigIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    """What the change would have done to the last 50 signals (nothing is saved)."""
    m = _require(db, pc_session)
    try:
        new = brainx.validate_config(payload.config)
    except brainx.ConfigError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return {"config": new, "impact": brainx.config_impact(db, brainx.current_config(db, m)["config"], new)}


@router.put("/members/kiwi-brain/config")
def put_config(payload: ConfigIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _require(db, pc_session)
    try:
        return brainx.save_config(db, m, payload.config, payload.note)
    except brainx.ConfigError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


class RollbackIn(StrictInput):
    version: int = Field(ge=1)


@router.post("/members/kiwi-brain/config/rollback")
def rollback(payload: RollbackIn, db: DbSession, pc_session: SessionToken = None) -> dict[str, Any]:
    m = _require(db, pc_session)
    try:
        return brainx.rollback_config(db, m, payload.version)
    except brainx.ConfigError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e


class _Teach(BaseModel):
    understood: bool = Field(description="false if nothing in the text maps to the supported fields")
    changes: dict[str, Any] = Field(description="ONLY the supported config fields to change, with valid values")
    unsupported: list[str] = Field(description="Every part of the instruction that the supported fields can't express, verbatim")
    explanation: str = Field(description="One short sentence per change: what the bot will do differently")


TEACH_SYSTEM = (
    "You convert a trader's plain-language rule into a configuration for a trading bot. You may ONLY use these fields:\n"
    "strategies: {\"trend_pullback_v1\": bool, \"breakout_4h_v1\": bool, \"test\": bool}  (test = the owner's manual setups)\n"
    "sides: \"both\" | \"long\" | \"short\"\n"
    "coins: list of tickers like [\"BTC\",\"ETH\"] (empty = any)\n"
    "timeframes: subset of [\"1h\",\"4h\"]  (trend pullback runs on 1h, breakout on 4h)\n"
    "min_rr: number 1-5 or null\n"
    "funding: \"any\" | \"negative\" | \"positive\"\n"
    "max_volatility_pct: number 0.1-20 (ATR as % of price) or null\n"
    "schedule: {\"days\": [0..6 Mon..Sun], \"from_h\": int, \"to_h\": int} in UTC, or null\n"
    "These are FILTERS on the bot's published signals: they can only skip trades, never create them. Anything else (indicator "
    "conditions like 'volume above average', 'RSI recovering', position limits, leverage, stop rules) goes in `unsupported`, word "
    "for word; never approximate it with a different field. Breakouts = breakout_4h_v1. Return JSON only."
)


class TeachIn(StrictInput):
    text: str = Field(min_length=3, max_length=600)


@router.post("/members/kiwi-brain/config/interpret")
async def interpret(payload: TeachIn, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    """Plain language → the supported config fields, shown for confirmation (never saved here)."""
    from app.ai.structured import complete_structured

    m = _require(db, pc_session)
    if not brainx.ask_allowed(m.id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Kiwi needs a break: try again in a few minutes.")
    cur = brainx.current_config(db, m)["config"]
    try:
        async with runtime.gate.interactive():
            out, _, _ = await complete_structured(runtime.router(db, background=False), "kiwi.teach", TEACH_SYSTEM,
                                                  f"Current config: {cur}\n\nInstruction: {payload.text}", _Teach)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"The AI model isn't available right now ({type(e).__name__}).") from e
    merged = {**cur, **{k: v for k, v in out.changes.items() if k in brainx.DEFAULT_CONFIG}}
    if isinstance(out.changes.get("strategies"), dict):
        merged["strategies"] = {**cur["strategies"], **out.changes["strategies"]}
    try:
        new = brainx.validate_config(merged)
    except brainx.ConfigError as e:
        return {"ok": False, "error": str(e), "unsupported": out.unsupported, "explanation": out.explanation}
    dropped = [k for k in out.changes if k not in brainx.DEFAULT_CONFIG]
    return {"ok": out.understood and new != cur, "config": new, "changes": {k: new[k] for k in new if new[k] != cur.get(k)},
            "unsupported": out.unsupported + dropped, "explanation": out.explanation, "impact": brainx.config_impact(db, cur, new)}


# ---------------------------------------------------------------- Ask Kiwi
class _Proposal(BaseModel):
    kind: Literal["none", "config", "limits"] = Field(description="config = Kiwi config filters; limits = risk limits; none = no change asked")
    changes: dict[str, Any] = Field(default_factory=dict, description="The exact fields to change")
    summary: str = Field("", description="What would change, one sentence")


class _Ask(BaseModel):
    answer: str = Field(description="Answer from the evidence only, citing ids like [E12], [P3], [MBTC]")
    cited: list[str] = Field(description="Every evidence id used")
    sufficient: bool = Field(description="false when the evidence can't answer the question")
    proposal: _Proposal = Field(default_factory=lambda: _Proposal(kind="none"))


ASK_SYSTEM = (
    "You are Kiwi, a crypto trading bot, explaining YOUR OWN records to the member who owns this account. Use ONLY the evidence "
    "lines provided (ids in brackets). Rules:\n"
    "- Cite ids for every factual claim, like [E123] or [P4] or [MBTC].\n"
    "- Never invent trades, prices, indicator values, reasons or results. If something isn't in the evidence, say it isn't recorded and set sufficient=false.\n"
    "- Backtests are HYPOTHETICAL; paper is SIMULATED; say so whenever you mention them. Never promise profits.\n"
    "- You cannot place, close or change trades. If the member asks for a change, put it in `proposal` (kind config: fields strategies, "
    "sides, coins, timeframes, min_rr, funding, max_volatility_pct, schedule; kind limits: max_leverage (1-5), max_loss_per_trade_usd, "
    "max_positions (1-3), daily_loss_limit_usd, allowed_coins, paused) and tell them to review and confirm it.\n"
    "- Short, direct, a little playful (you're a kiwi), no financial advice. Return JSON only."
)
LIMIT_FIELDS = {"max_leverage", "max_loss_per_trade_usd", "max_positions", "daily_loss_limit_usd", "allowed_coins", "paused"}


class AskIn(StrictInput):
    question: str = Field(min_length=2, max_length=500)


@router.post("/members/kiwi-brain/ask")
async def ask(payload: AskIn, db: DbSession, runtime: Runtime, pc_session: SessionToken = None) -> dict[str, Any]:
    from app.ai.structured import complete_structured

    m = _require(db, pc_session)
    if not brainx.ask_allowed(m.id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Kiwi needs a break: try again in a few minutes.")
    text, ids = await asyncio.to_thread(brainx.evidence, db, m, market.data())
    try:
        async with runtime.gate.interactive():
            out, result, route = await complete_structured(runtime.router(db, background=False), "kiwi.ask", ASK_SYSTEM,
                                                           f"Question: {payload.question}\n\nEvidence:\n{text}", _Ask)
    except Exception as e:  # noqa: BLE001 - no AI model: the common questions are answered straight from the records
        direct = await asyncio.to_thread(brainx.direct_answer, db, m, market.data(), payload.question)
        if direct is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, (
                f"Kiwi's AI model isn't available right now ({type(e).__name__}). Without it Kiwi can answer: why not long/short a coin, "
                "your risk exposure, funding paid, recent rejections, strategy performance, and why your last trade closed.")) from e
        evs = {int(c[1:]) for c in direct["cited"] if c.startswith("E") and c[1:].isdigit()}
        return direct | {"events": [brain.event_out(x) for x in db.scalars(select(KbEvent).where(
            KbEvent.id.in_(evs), (KbEvent.member_id.is_(None)) | (KbEvent.member_id == m.id)))] if evs else [],
            "model": "direct (records only, no AI model)"}
    cited = [c.strip("[] ") for c in out.cited if c.strip("[] ") in ids]
    proposal = None
    p = out.proposal
    if p.kind == "config" and p.changes:
        cur = brainx.current_config(db, m)["config"]
        try:
            new = brainx.validate_config({**cur, **{k: v for k, v in p.changes.items() if k in brainx.DEFAULT_CONFIG}})
            proposal = {"kind": "config", "config": new, "changes": {k: new[k] for k in new if new[k] != cur.get(k)}, "summary": p.summary,
                        "impact": brainx.config_impact(db, cur, new)}
        except brainx.ConfigError as e:
            proposal = {"kind": "invalid", "summary": f"{p.summary} (not possible: {e})"}
    elif p.kind == "limits" and p.changes:
        ch = {k: v for k, v in p.changes.items() if k in LIMIT_FIELDS}
        proposal = {"kind": "limits", "changes": ch, "summary": p.summary} if ch else None
    events = {int(c[1:]) for c in cited if c.startswith("E") and c[1:].isdigit()}
    return {"answer": out.answer.strip(), "cited": cited, "sufficient": out.sufficient and bool(cited), "proposal": proposal,
            "events": [brain.event_out(e) for e in db.scalars(select(KbEvent).where(KbEvent.id.in_(events), (KbEvent.member_id.is_(None)) | (KbEvent.member_id == m.id)))] if events else [],
            "model": result.model or route.model}
