"""TEST FIXTURES: evidence packs shaped exactly like the contract's build_evidence output (Kiwi AI Investment Committee), and
a scripted fake LLM backend. No network anywhere."""

import copy
import json
from typing import Any, Callable

from app.committee.llm import Backend, Call, LLMError, Reply

AS_OF = "2026-10-23T12:00:00Z"


def _ref(i: str, source: str, url: str | None = None) -> dict[str, Any]:
    return {"id": i, "source": source, "url": url, "ts": AS_OF, "fetched_at": AS_OF}


def _section(status: str, lean: str, score: int | None, reasons: list[tuple[str, list[str]]], facts: dict[str, Any] | None = None,
             items: list[Any] | None = None, limitations: list[str] | None = None) -> dict[str, Any]:
    refs = sorted({r for _, rs in reasons for r in rs})
    return {"status": status, "limitations": limitations or [], "facts": facts or {}, "items": items or [], "lean": lean, "score": score,
            "reasons": [{"text": t, "refs": rs} for t, rs in reasons], "refs": refs}


def _frame(close: float, atr: float, trend: str, **kw: Any) -> dict[str, Any]:
    f = {"close": close, "ema20": kw.get("ema20", close), "ema50": kw.get("ema50", close), "ema200": kw.get("ema200", close),
         "rsi14": kw.get("rsi14", 55.0), "macd": 0.0, "macd_signal": 0.0, "macd_hist": 0.0, "bb_upper": close, "bb_mid": close,
         "bb_lower": close, "bb_width_pct": 3.1, "atr14": atr, "atr_pct": round(atr / close * 100, 3), "vol_ratio20": 1.2, "trend": trend,
         "structure": "HH/HL" if trend == "up" else "LH/LL" if trend == "down" else "mixed", "breakout": None,
         "realized_vol_pct": 2.4, "candles": 300, "last_closed": AS_OF}
    return f


def btc() -> dict[str, Any]:
    """Bullish BTC on Hyperliquid: price just through the long trigger, the rule-based signal says LONG."""
    tech = _section("ok", "bullish", 2, [("1h trend up with HH/HL structure above EMA50 66,900", ["T1"]),
                                         ("1h close above resistance 67,800", ["T1"]), ("4h trend up", ["T2"])],
                    facts={"frames": {"1h": _frame(67810.0, 320.0, "up", ema50=66900.0, rsi14=61.2), "4h": _frame(67810.0, 900.0, "up")},
                           "support": [66200.0, 65400.0], "resistance": [67800.0, 68900.0],
                           "scenarios": {"long": {"trigger": 67800.0, "trigger_rule": "1h close above resistance 67,800", "invalidation": 66200.0,
                                                  "invalidation_rule": "1h close below support 66,200", "targets": [68900.0],
                                                  "target_rule": "next resistance 68,900"},
                                         "short": {"trigger": 66200.0, "trigger_rule": "1h close below support 66,200", "invalidation": 67800.0,
                                                   "invalidation_rule": "1h close back above 67,800", "targets": [65400.0],
                                                   "target_rule": "next support 65,400"}},
                           "signal": {"rule": "trend_pullback_v1", "verdict": "LONG", "first_fail": None}})
    fund = _section("ok", "bullish", 1, [("Open interest $2.1B and 24h volume $3.4B on Hyperliquid", ["F1"])],
                    facts={"max_leverage": 50, "sz_decimals": 5, "only_isolated": False, "oi_usd": 2.1e9, "volume_24h_usd": 3.4e9,
                           "listed_on": ["hyperliquid", "okx", "binance", "bybit"], "token_unlocks": "unavailable"},
                    limitations=["token unlocks: no verified source configured"])
    news = _section("ok", "neutral", 0, [("2 headlines in 48h, none directional", ["N1", "N2"])],
                    items=[{"title": "Bitcoin holds near highs", "source": "CoinDesk", "url": "https://example.test/a", "published": AS_OF,
                            "age_h": 3, "match": "name"}])
    senti = _section("ok", "neutral", 0, [("Funding 0.0125% per hour, Fear & Greed 61 (Greed)", ["S1", "S2"])],
                     facts={"funding_1h_pct": 0.0125, "funding_24h_avg_pct": 0.011, "fear_greed": {"value": 61, "label": "Greed", "ts": AS_OF}},
                     limitations=["community mentions are noisy"])
    return {"asset": {"symbol": "BTC", "venue": "hyperliquid", "market_type": "perp", "market_id": "BTC", "label": "Hyperliquid perpetual",
                      "hl_tradable": True, "alternatives": []},
            "timeframe": "1h", "as_of": AS_OF, "mode": "live",
            "price": {"value": 67810.0, "kind": "mark", "source": "hyperliquid", "ts": AS_OF, "stale": False},
            "sections": {"technical": tech, "fundamental": fund, "news": news, "sentiment": senti},
            "refs": [_ref("T1", "Hyperliquid 1h candles"), _ref("T2", "Hyperliquid 4h candles"), _ref("F1", "Hyperliquid meta"),
                     _ref("N1", "CoinDesk RSS", "https://example.test/a"), _ref("N2", "Decrypt RSS", "https://example.test/b"),
                     _ref("S1", "Hyperliquid fundingHistory"), _ref("S2", "alternative.me Fear & Greed")],
            "built_at": AS_OF, "degraded": []}


def doge() -> dict[str, Any]:
    """DOGE in a range: both scenarios exist, neither has triggered, the signal says WAIT."""
    ev = btc()
    ev["asset"] = {**ev["asset"], "symbol": "DOGE", "market_id": "DOGE"}
    t = ev["sections"]["technical"]
    t.update(lean="neutral", score=0, reasons=[{"text": "1h range between support 0.148 and resistance 0.158", "refs": ["T1"]},
                                                {"text": "4h EMA20 below EMA50", "refs": ["T2"]}])
    t["facts"] = {"frames": {"1h": _frame(0.1523, 0.0021, "range"), "4h": _frame(0.1523, 0.0048, "down")},
                  "support": [0.148, 0.145], "resistance": [0.158, 0.162],
                  "scenarios": {"long": {"trigger": 0.158, "trigger_rule": "1h close above resistance 0.158", "invalidation": 0.148,
                                         "invalidation_rule": "1h close below support 0.148", "targets": [0.162], "target_rule": "next resistance 0.162"},
                                "short": {"trigger": 0.148, "trigger_rule": "1h close below support 0.148", "invalidation": 0.158,
                                          "invalidation_rule": "1h close back above 0.158", "targets": [0.145], "target_rule": "next support 0.145"}},
                  "signal": {"rule": "trend_pullback_v1", "verdict": "WAIT", "first_fail": "trend: 4h EMA20 below EMA50"}}
    ev["sections"]["fundamental"].update(lean="neutral", score=0, reasons=[{"text": "Open interest $410M on Hyperliquid", "refs": ["F1"]}])
    ev["sections"]["fundamental"]["facts"] = {"max_leverage": 20, "sz_decimals": 0, "only_isolated": False, "oi_usd": 4.1e8,
                                              "volume_24h_usd": 6.0e8, "listed_on": ["hyperliquid", "okx", "binance"]}
    ev["price"] = {**ev["price"], "value": 0.1523}
    return ev


def conflicting() -> dict[str, Any]:
    """Bullish chart, bearish news and sentiment."""
    ev = btc()
    ev["sections"]["news"].update(lean="bearish", score=-2, reasons=[{"text": "SEC delays two spot ETF decisions", "refs": ["N1"]},
                                                                     {"text": "Exchange outflow headline", "refs": ["N2"]}])
    ev["sections"]["sentiment"].update(lean="bearish", score=-1, reasons=[{"text": "Funding 0.0125% per hour: longs crowded", "refs": ["S1"]}])
    ev["sections"]["fundamental"].update(lean="neutral", score=0)
    return ev


def no_news() -> dict[str, Any]:
    ev = btc()
    ev["sections"]["news"] = {"status": "unavailable", "limitations": ["all news feeds failed (timeout)"], "facts": {}, "items": [],
                              "lean": "unknown", "score": None, "reasons": [], "refs": []}
    ev["degraded"] = ["news: all feeds failed"]
    return ev


def stale() -> dict[str, Any]:
    ev = btc()
    ev["price"] = {**ev["price"], "stale": True, "ts": "2026-10-23T09:00:00Z"}
    ev["sections"]["technical"]["status"] = "stale"
    ev["sections"]["technical"]["limitations"] = ["last closed candle is 3 h old"]
    return ev


def okx_spot() -> dict[str, Any]:
    ev = btc()
    ev["asset"] = {"symbol": "PEPE2", "venue": "okx", "market_type": "spot", "market_id": "PEPE2-USDT", "label": "OKX spot PEPE2-USDT",
                   "hl_tradable": False, "alternatives": []}
    return ev


PACKS: dict[str, Callable[[], dict[str, Any]]] = {"BTC": btc, "DOGE": doge, "ETH": conflicting, "SOL": no_news, "XRP": stale, "PEPE2": okx_spot}


def pack_for(symbol: str) -> dict[str, Any]:
    ev = copy.deepcopy(PACKS.get(symbol, btc)())
    ev["asset"]["symbol"] = symbol if symbol in PACKS else symbol
    return ev


# ---------------------------------------------------------------- a scripted LLM
def grounded_answer(call: Call) -> dict[str, Any]:
    """A valid, grounded answer for every role (numbers only from the BTC / DOGE packs, or none)."""
    r = call.role
    if r.endswith("analyst"):
        return {"lean": "bullish" if r.startswith("technical") else "neutral", "confidence": "medium",
                "summary": "The evidence leans this way; see the cited refs.", "points": [{"text": "Trend is up above EMA50.", "refs": ["T1", "ZZ9"]}],
                "limitations": []}
    if r == "bull":
        return {"thesis": "Trend and structure favour longs.", "points": [{"text": "1h close above resistance.", "refs": ["T1"]}]}
    if r == "bear":
        return {"thesis": "Sentiment is crowded and news is flat.", "points": [{"text": "Greed reading on the index.", "refs": ["S2"]}]}
    if r == "rebuttals":
        return {"bull": "Crowding has not broken the trend.", "bear": "Breakouts fail often without news."}
    if r == "research manager":
        return {"rating": "Overweight", "summary": "The bull evidence carries it."}
    if r == "trader":
        return {"action": "LONG", "rationale": "The long scenario triggered."}
    if r == "risk panel":
        v = {"stance": "support", "concerns": ["funding paid by longs"]}
        return {"aggressive": v, "neutral": {**v, "stance": "reduce"}, "conservative": {**v, "stance": "reject"}, "summary": "Split panel."}
    if r == "portfolio manager":
        return {"action": "LONG", "rating": "Overweight", "summary": "Long on the breakout; the risk engine decides execution.",
                "what_changes_view": ["A 1h close below support 66,200"]}
    raise AssertionError(f"unexpected role {r}")


class FakeLLM(Backend):
    """TEST FIXTURE: answers per role. overrides[role] = dict (answer), str (raw text), Exception (raised) or a callable."""

    name = "router"

    def __init__(self, overrides: dict[str, Any] | None = None, delay: float = 0.0) -> None:
        self.overrides = overrides or {}
        self.calls: list[Call] = []
        self.delay = delay

    def models(self) -> dict[str, str]:
        return {"fast": "fake-fast", "deep": "fake-deep", "synthesis": "fake-synth"}

    async def complete(self, call: Call, run_id: int | None = None) -> Reply:
        import asyncio

        self.calls.append(call)
        if self.delay:
            await asyncio.sleep(self.delay)
        o = self.overrides.get(call.role, self.overrides.get("*"))
        if callable(o) and not isinstance(o, type):
            o = o(call)
        if isinstance(o, BaseException):
            raise o
        if o is None:
            o = grounded_answer(call)
        text = o if isinstance(o, str) else json.dumps(o)
        return Reply(text, {"fast": "fake-fast", "deep": "fake-deep", "synthesis": "fake-synth"}[call.tier], 100, 50)


TIMEOUT = LLMError("timeout after 90 s")
