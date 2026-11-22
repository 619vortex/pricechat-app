"""Vortex trade checks: "/help <trade>" in the Telegram bot (contract: Price Chat ⇄ Vortex "/help <trade>" risk assessment).

The bot parses the trade (deterministic), computes FACTS from Hyperliquid (build_facts) and either posts a placeholder that
Vortex, the owner's home AI, turns into a written answer (it long-polls /api/vortex/claim, outbound only), or, when Vortex
hasn't been seen for SEEN_WINDOW, answers at once with the bot's own assessment (render_fallback). A job unanswered after
EXPIRE_AFTER is turned into the fallback (tradingos.vortex_expire, or inline when Vortex answers late). Replies from the
asker to an answer are follow-ups for FOLLOWUP_WINDOW, at most MAX_FOLLOWUPS per check; everything else stays silent.
Daily limits per Telegram user (more for a linked Kiwi Passport at the boost rank). Analysis only: never places or
changes a trade, and Vortex's text is untrusted (sanitize: plain text, no links, a fixed footer).
Settings: TsSettings.strategies["vortex_assist"] = {"on", "daily_public", "daily_ranked", "min_rank"}.

Risk score (1 low .. 10 very high), from 3, clamped (risk_of):
  +3 / +2 / +1  leverage × daily ATR% ≥ 50 / 25 / 12 (a normal day's move as a share of the margin; flag at ≥ 25)
  +1 leverage above the venue's max · +2 no stop-loss · +2 stop on the wrong side of the entry · +2 price already past the stop
  +2 liquidation within 1.5 daily ATRs of the price · +2 stop beyond the liquidation price
  +1 against the 4h trend · +1 funding against you (≥ 0.03% per 8 h) · +1 entry more than 2 daily ATRs from the nearest
  support (long) / resistance (short) · +1 RSI past 70 on a long / under 30 on a short · +1 reward smaller than the risk
  +1 the bot's own read is the other side · -1 stop set with R/R ≥ 2 · -1 no leverage or ≤ 3x
"""

import asyncio
import html
import logging
import math
import re
import secrets
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.singleton import get_or_create
from app.db.base import utcnow
from app.models.tradingos import TsAssistConvo, TsAssistJob, TsAssistState
from app.tradingos import indicators as ind
from app.tradingos import market, signals
from app.tradingos.market import fmt_price

logger = logging.getLogger(__name__)

SEEN_WINDOW = timedelta(seconds=120)  # Vortex counts as around when it long-polled this recently
EXPIRE_AFTER = timedelta(seconds=60)  # a job still unanswered after this gets the fallback
LEASE = timedelta(seconds=45)  # a claimed job may be handed out again after this (until it expires)
FOLLOWUP_WINDOW = timedelta(minutes=30)
MAX_FOLLOWUPS = 6
MAX_HISTORY = 6
MAX_QUESTION = 500
MAX_ANSWER = 1800
DEFAULTS = {"daily_public": 3, "daily_ranked": 10, "min_rank": 2}
OPEN = ("pending", "claimed")
PLACEHOLDER = "🌀 Vortex is checking your trade…"
THINKING = "🌀 Vortex is thinking…"
FOOTER = "Not financial advice · Vortex 🌀"
USAGE = ("🌀 <b>Trade check</b>: tell me your trade and I'll size up the risk.\n"
         "/help long BTC at 81k 10x sl 78k tp 90k\n/help short ETH 3200 sl 3350\n/help im in SOL long from 140 with $500 5x\n"
         "(/help alone lists the commands)")


# ---------------------------------------------------------------- settings and Vortex's presence
def token() -> str | None:
    from app.security.credentials import resolve

    return resolve("vortex_assist_token")


def config(db: Session) -> dict[str, Any]:
    """The owner's settings. On by default only once a token is set (an explicit on/off wins)."""
    c = (signals.settings(db).strategies or {}).get("vortex_assist") or {}
    has_token = bool(token())
    on = c.get("on")
    out = {k: int(c.get(k, v)) for k, v in DEFAULTS.items()}
    st = db.get(TsAssistState, 1)
    seen = st.last_seen_at if st else None
    return {**out, "on": on, "token_set": has_token, "enabled": bool(has_token if on is None else on),
            "last_seen": seen.isoformat() if seen else None}


def mark_seen(db: Session, now: datetime | None = None) -> None:
    st = get_or_create(db, TsAssistState, lambda: TsAssistState(id=1))
    st.last_seen_at = now or utcnow()


def online(db: Session, now: datetime | None = None) -> bool:
    st = db.get(TsAssistState, 1)
    return bool(st and st.last_seen_at and (now or utcnow()) - _aware(st.last_seen_at) <= SEEN_WINDOW)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- the parser
@dataclass
class Trade:
    side: str  # long | short
    coin: str  # the Hyperliquid perpetual
    entry: float | None = None
    leverage: float | None = None
    size_usd: float | None = None  # the position's notional
    margin_usd: float | None = None  # when the amount typed was the margin (with leverage: "$500 5x" is $500 margin)
    stop: float | None = None
    target: float | None = None


class ParseError(Exception):
    def __init__(self, code: str, word: str = "") -> None:
        super().__init__(code)
        self.code, self.word = code, word


_NUM = r"(\d+(?:\.\d+)?)(?:\s?([km])\b)?(?!\w|\.\d)"
_STOP = re.compile(r"\b(?:sl|stop[\s-]?loss|stoploss|stop|invalidation|inv)\b\s*(?:at|@|:|=|is|of)?\s*\$?" + _NUM)
_TARGET = re.compile(r"\b(?:tp\d?|take[\s-]?profit|targets?|tgt)\b\s*(?:at|@|:|=|is|of)?\s*\$?" + _NUM)
_LEV = [re.compile(r"\b(?:lev|leverage)\s*(?:at|of|:|=)?\s*x?(\d+(?:\.\d+)?)\s*x?\b"), re.compile(r"\b(\d+(?:\.\d+)?)\s*x\b"),
        re.compile(r"\bx\s?(\d+(?:\.\d+)?)\b")]
_KIND = r"(margin|size|notional|position)"
_SIZE = [re.compile(r"(?:\b" + _KIND + r"\s*(?:of|:|=)?\s*)?\$\s?" + _NUM + r"(?:\s*" + _KIND + r"\b)?"),
         re.compile(r"(?:\b" + _KIND + r"\s*(?:of|:|=)?\s*)?" + _NUM + r"\s*(?:usd[tc]?|dollars?|bucks|\$)(?!\w)(?:\s*" + _KIND + r"\b)?")]
_ENTRY = re.compile(r"(?:\b(?:at|from|entry|entered|avg|average|price|filled|in)\b|@)\s*(?:at|of|:|=|@)?\s*\$?" + _NUM)
_BARE = re.compile(r"(?<![\w.])\$?" + _NUM)
_LONG = re.compile(r"\b(long|longs|longed|longing|buy|bought|buying|bull|bullish)\b")
_SHORT = re.compile(r"\b(short|shorts|shorted|shorting|sell|sold|selling|bear|bearish)\b")
_SUFFIX = re.compile(r"(?:[-/]?(?:usdt|usdc|usd|perp))$")
STOPWORDS = set("""i im ive id in at from with a an the my me is was am on of to for and or it its this that position entry entered
opened open got have has had just now here what how should do does think you your risk risky check help please pls size margin
leverage lev stop loss sl tp target targets take profit x k m usd usdt usdc dollars dollar bucks perp perps spot hl hyperliquid
binance bybit okx avg average price filled today still too so if be can will would much max cross isolated iso trade again also
high low up down go going all some around about near plus but not no any long longs longed longing buy bought buying bull bullish
short shorts shorted shorting sell sold selling bear bearish worth good bad safe ok okay is it am are we our new into out tgt inv
invalidation position notional sir bro guys hey hi hello thanks thx yes yeah idk lol rate opinion view""".split())


def _num(v: str, suffix: str | None) -> float:
    return float(v) * (1_000 if suffix == "k" else 1_000_000 if suffix == "m" else 1)


def _cut(t: str, m: re.Match[str]) -> str:
    return t[:m.start()] + " " * (m.end() - m.start()) + t[m.end():]


def parse(text: str, known: set[str]) -> Trade:
    """Free text -> Trade. `known`: the tradable coins (upper case). ParseError("unparseable") without a side or coin,
    ParseError("unknown_coin", word) when the coin isn't one we can read."""
    from app.tradingos.bot import coin_name

    t = unicodedata.normalize("NFKC", text).lower().replace("×", "x").replace("’", "").replace("'", "")
    t = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", t)  # 81,000 -> 81000
    words = re.findall(r"\$?[a-z0-9]+(?:[-/][a-z]+)?", t)
    lm, sm = _LONG.search(t), _SHORT.search(t)
    side = None if not (lm or sm) else "long" if lm and (not sm or lm.start() < sm.start()) else "short"
    coin = unknown = None
    for w in words:
        base = _SUFFIX.sub("", w.lstrip("$")) or w.lstrip("$")
        if base.isdigit() or re.fullmatch(r"\d+(?:\.\d+)?[kmx]?", base) or base in STOPWORDS:
            continue
        name = coin_name(base)
        if name in known and (len(base) > 1 or w.startswith("$")):
            coin = name
            break
        if unknown is None and base.isalpha() and 2 <= len(base) <= 10:
            unknown = base.upper()
    if side is None or coin is None:
        if side is not None and unknown:
            raise ParseError("unknown_coin", unknown)
        raise ParseError("unparseable")
    tr = Trade(side=side, coin=coin)
    for attr, rx in (("stop", _STOP), ("target", _TARGET)):
        m = rx.search(t)
        if m:
            setattr(tr, attr, _num(m.group(1), m.group(2)))
            t = _cut(t, m)
    for rx in _LEV:
        m = rx.search(t)
        if m:
            tr.leverage = float(m.group(1))
            t = _cut(t, m)
            break
    for rx in _SIZE:
        m = rx.search(t)
        if m:
            amount = _num(m.group(2), m.group(3))
            kind = m.group(1) or m.group(4)
            if kind == "margin" or (kind is None and tr.leverage):
                tr.margin_usd, tr.size_usd = amount, amount * (tr.leverage or 1)
            else:
                tr.size_usd = amount
            t = _cut(t, m)
            break
    m = _ENTRY.search(t) or _BARE.search(t)
    if m:
        tr.entry = _num(m.group(1), m.group(2))
    if tr.leverage is not None and not 1 <= tr.leverage <= 200:
        tr.leverage = None
    for attr in ("entry", "stop", "target", "size_usd"):
        if getattr(tr, attr) is not None and getattr(tr, attr) <= 0:
            setattr(tr, attr, None)
    return tr


# ---------------------------------------------------------------- FACTS
def _px(v: float | None) -> float | None:
    return None if v is None else float(f"{v:.6g}")


def _r(v: float | None, n: int = 2) -> float | None:
    return None if v is None else round(v, n)


def _k(p: float) -> str:
    """77.8k, 3.2k, 140, 0.0523: for short labels."""
    if p >= 1000:
        return f"{p / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    return fmt_price(p)


def _rescale(v: float | None, price: float) -> float | None:
    """'btc long 81' means 81k: a number 1000x too small for the market is read in thousands."""
    if v is None or price <= 0:
        return v
    if v < price / 100 and 0.5 <= v * 1000 / price <= 1.5:
        return v * 1000
    return v


def _pivots(cs: list[Any], k: int = 2) -> tuple[list[float], list[float]]:
    return ind.pivots([c.h for c in cs], [c.l for c in cs], k)


def _nearest(levels: list[float], ref: float, below: bool, n: int = 2, gap: float = 0.004) -> list[float]:
    """The n levels nearest to ref on one side, at least `gap` apart (clustered)."""
    return ind.nearest_levels(levels, ref, below, n, gap)


def build_facts(md: Any, tr: Trade, rules: tuple[str, float, float, float] = ("1h", 50_000_000, 8.0, 1.6),
                now: datetime | None = None) -> dict[str, Any]:
    """The FACTS of the contract, every number from Hyperliquid. rules: (timeframe, min volume, max spread, min R/R) for
    the bot's own read (signals.analyze)."""
    now = now or utcnow()
    m = md.market(tr.coin)
    price = m.mark
    entry = _rescale(tr.entry, price)
    src = "user" if entry else "market"
    entry = entry or price
    stop, target = _rescale(tr.stop, price), _rescale(tr.target, price)
    sign = 1 if tr.side == "long" else -1
    day = signals.closed(md.candles(m.coin, "1d", 90), "1d") or md.candles(m.coin, "1d", 90)
    atr = ind.atr([c.h for c in day], [c.l for c in day], [c.c for c in day]) or price * 0.03
    atr_pct = atr / price * 100
    h4 = md.candles(m.coin, "4h", 150)
    ci = signals.chart_indicators(h4, "4h")
    e20, e50 = ci.get("ema20"), ci.get("ema50")
    trend = "up" if e20 and e50 and e20 > e50 and price > e50 else "down" if e20 and e50 and e20 < e50 and price < e50 else "range"
    pl4, ph4 = _pivots(signals.closed(h4, "4h")[-120:])
    pld, phd = _pivots(day)
    lows, highs = pl4 + pld, ph4 + phd
    if day:  # the window's own extremes, when no swing stands on that side (a one-way market)
        lows.append(min(c.l for c in day))
        highs.append(max(c.h for c in day))
    support, resistance = _nearest(lows, price, True), _nearest(highs, price, False)
    mmr = 1 / (2 * max(1, m.max_leverage))  # Hyperliquid: maintenance margin is half the initial margin at max leverage
    lev = tr.leverage
    liq = liq_dist = None
    if lev:
        room = max(0.0, 1 / lev - mmr)  # isolated: the margin left above maintenance, per unit of entry
        liq = entry * (1 - room / (1 - mmr)) if sign > 0 else entry * (1 + room / (1 + mmr))
        liq_dist = sign * (price - liq) / price * 100
    stop_dist = sign * (entry - stop) / entry * 100 if stop else None
    rr = None
    if stop and target and stop_dist and stop_dist > 0 and sign * (target - entry) > 0:
        rr = sign * (target - entry) / (sign * (entry - stop))
    size = tr.size_usd
    risk_usd = size * stop_dist / 100 if size and stop_dist and stop_dist > 0 else None
    funding_8h = m.funding * 8 * 100
    funding_day = size * m.funding * 24 * sign if size else None  # > 0: you pay
    # suggested stop: past the nearest level on the trade's side, at least one daily ATR from the entry (else 1.5 ATR)
    sstop, why = entry - sign * 1.5 * atr, "1.5× the daily range from the entry"
    for lvl in _nearest(lows if sign > 0 else highs, entry, sign > 0, n=4):
        cand = lvl - sign * 0.15 * atr
        if atr <= sign * (entry - cand) <= 3 * atr:
            sstop, why = cand, f"{'below support' if sign > 0 else 'above resistance'} {_k(lvl)}"
            break
    view = _system_view(md, m.coin, rules, ci, tr.side)
    facts = {"coin": m.coin, "venue": "hyperliquid", "side": tr.side, "entry": _px(entry), "entry_source": src, "price": _px(price),
             "pnl_pct": _r(sign * (price - entry) / entry * 100), "leverage": lev, "size_usd": _r(size), "margin_usd": _r(tr.margin_usd),
             "max_leverage": m.max_leverage, "stop": _px(stop), "target": _px(target), "liq_price": _px(liq), "liq_distance_pct": _r(liq_dist),
             "stop_distance_pct": _r(stop_dist), "rr": _r(rr), "risk_usd": _r(risk_usd), "atr_pct": _r(atr_pct), "trend": trend,
             "rsi": _r(ci.get("rsi"), 1), "support": [_px(x) for x in support], "resistance": [_px(x) for x in resistance],
             "change_24h_pct": _r(m.change_24h), "funding_8h_pct": _r(funding_8h, 4), "funding_daily_cost_usd": _r(funding_day),
             "suggested_stop": _px(sstop), "suggested_stop_reason": why, "system_view": view,
             "as_of": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    d = sign * (entry - sstop) / entry
    facts["safer_leverage"] = max(1, min(m.max_leverage, math.floor(1 / (1.5 * d + mmr)))) if d > 0 else None  # liq ≥ 1.5× that stop away
    facts["risk_score"], facts["risk_flags"] = risk_of(facts)
    return facts


def _system_view(md: Any, coin: str, rules: tuple[str, float, float, float], ci: dict[str, Any], side: str) -> dict[str, Any]:
    """The bot's own read: a setup that passes every rule (signals.analyze), else a strong-enough WATCH idea, else WAIT."""
    verdict, idea = "WAIT", "no clean read on the chart"
    try:
        a = signals.analyze(md, coin, *rules)
        if a.decision in ("LONG", "SHORT"):
            verdict, idea = a.decision, f"{a.decision.lower()} setup that passes every rule ({rules[0]})"
    except Exception:  # noqa: BLE001 - the bot's read is extra: never blocks the check
        logger.info("assist: analyze failed for %s", coin, exc_info=True)
    if verdict == "WAIT" and ci:
        w = signals.watch_setup(signals.Analysis(coin=coin, decision="NO TRADE", indicators=ci))
        if w is not None:
            idea = w.idea
            if w.score >= 3:
                verdict = w.side
    return {"verdict": verdict, "agrees": None if verdict == "WAIT" else verdict == side.upper(), "idea": idea}


def risk_of(f: dict[str, Any]) -> tuple[int, list[str]]:
    """The deterministic risk score and its flags (the rules are in the module docstring)."""
    score, flags = 3, []
    long = f["side"] == "long"
    lev, atr = f.get("leverage"), f.get("atr_pct") or 0
    if lev:
        exposure = lev * atr
        score += 3 if exposure >= 50 else 2 if exposure >= 25 else 1 if exposure >= 12 else 0
        if exposure >= 25:
            flags.append("high leverage vs volatility")
        if lev > (f.get("max_leverage") or lev):
            score += 1
            flags.append(f"leverage above the venue max ({f['max_leverage']}x)")
    stop, entry, price, liq = f.get("stop"), f["entry"], f["price"], f.get("liq_price")
    if not stop:
        score += 2
        flags.append("no stop-loss")
    elif (f.get("stop_distance_pct") or 0) <= 0:
        score += 2
        flags.append("stop on the wrong side of the entry")
    elif (long and price <= stop) or (not long and price >= stop):
        score += 2
        flags.append("price already past your stop")
    if f.get("liq_distance_pct") is not None and f["liq_distance_pct"] <= 1.5 * atr:
        score += 2
        flags.append("liquidation within 1.5 daily moves")
    if stop and liq and ((long and stop <= liq) or (not long and stop >= liq)):
        score += 2
        flags.append("stop beyond liquidation")
    if (long and f["trend"] == "down") or (not long and f["trend"] == "up"):
        score += 1
        flags.append("against the trend")
    fund = f.get("funding_8h_pct") or 0
    if (long and fund >= 0.03) or (not long and fund <= -0.03):
        score += 1
        flags.append("funding against you")
    lvl = (f.get("support") or [None])[0] if long else (f.get("resistance") or [None])[0]
    if lvl and atr and abs(entry - lvl) / entry * 100 > 2 * atr and (entry > lvl if long else entry < lvl):
        score += 1
        flags.append("entry far above support" if long else "entry far below resistance")
    rsi = f.get("rsi")
    if rsi is not None and ((long and rsi > 70) or (not long and rsi < 30)):
        score += 1
        flags.append(f"RSI {rsi:.0f}: {'overbought' if long else 'oversold'}, chasing")
    rr = f.get("rr")
    if rr is not None and rr < 1:
        score += 1
        flags.append("reward smaller than the risk")
    if (f.get("system_view") or {}).get("agrees") is False:
        score += 1
        flags.append("the bot's own read is the other side")
    if rr is not None and rr >= 2:
        score -= 1
    if not lev or lev <= 3:
        score -= 1
    return max(1, min(10, score)), flags


# ---------------------------------------------------------------- the bot's own assessment (Telegram HTML)
def _esc(t: str) -> str:
    return html.escape(t, quote=False)


def _pct(x: float) -> str:
    return f"{x:+.2f}%"


def render_fallback(f: dict[str, Any], note: str = "") -> str:
    """The deterministic assessment from FACTS alone: what Vortex is told, laid out for a trader."""
    p = fmt_price
    long = f["side"] == "long"
    score = f["risk_score"]
    tone = "🟢 low" if score <= 3 else "🟡 medium" if score <= 6 else "🔴 high"
    lines = [f"🌀 <b>Trade check · ${_esc(f['coin'])} {f['side']}</b>" + (f"\n<i>{_esc(note)}</i>" if note else ""), "",
             f"<b>Risk {score}/10</b> · {tone}"]
    if f["entry_source"] == "user":
        lines.append(f"🎯 Entry {p(f['entry'])} · now {p(f['price'])} ({_pct(f['pnl_pct'])})")
    else:
        lines.append(f"🎯 Entry at market {p(f['price'])} (no entry given)")
    if f.get("leverage"):
        lines.append(f"⚡ {f['leverage']:g}x · liquidation ≈ {p(f['liq_price'])} ({f['liq_distance_pct']:.1f}% from the price)")
    else:
        lines.append("⚡ No leverage given: liquidation not estimated")
    if f.get("stop") and (f.get("stop_distance_pct") or 0) > 0:
        lines.append(f"🛑 Stop {p(f['stop'])} ({f['stop_distance_pct']:.1f}% from the entry)")
    elif f.get("stop"):
        lines.append(f"🛑 Stop {p(f['stop'])} is on the wrong side of the entry")
    else:
        lines.append("🛑 No stop-loss")
    if f.get("target"):
        lines.append(f"✅ Target {p(f['target'])}" + (f" · R/R 1:{f['rr']:.1f}" if f.get("rr") is not None else ""))
    money = []
    if f.get("size_usd"):
        money.append((f"${f['margin_usd']:,.0f} margin · " if f.get("margin_usd") else "") + f"${f['size_usd']:,.0f} position")
    if f.get("risk_usd") is not None:
        money.append(f"≈ ${f['risk_usd']:,.2f} lost at the stop")
    if f.get("funding_daily_cost_usd") is not None:
        c = f["funding_daily_cost_usd"]
        money.append(f"funding ≈ ${abs(c):,.2f}/day ({'you pay' if c > 0 else 'you earn'})")
    if money:
        lines.append("💵 " + " · ".join(money))
    rsi = f" · RSI {f['rsi']:.0f}" if f.get("rsi") is not None else ""
    lines.append(f"📈 Trend {f['trend']} (4h){rsi} · daily range ≈ {f['atr_pct']:.1f}% · 24h {_pct(f['change_24h_pct'])}")
    lvls = []
    if f.get("support"):
        lvls.append("Support " + " · ".join(_k(x) for x in f["support"]))
    if f.get("resistance"):
        lvls.append("Resistance " + " · ".join(_k(x) for x in f["resistance"]))
    if lvls:
        lines.append("🧱 " + " | ".join(lvls))
    v = f.get("system_view") or {}
    if v:
        agree = "" if v.get("agrees") is None else " (agrees)" if v["agrees"] else " (other side)"
        lines.append(f"🤖 Bot's read: {v['verdict']}{agree} · {_esc(v.get('idea') or '')}")
    if f["risk_flags"]:
        lines += ["", "⚠️ <b>Watch out</b>", *(f"· {_esc(x)}" for x in f["risk_flags"][:5])]
    safer = [f"· Stop {p(f['suggested_stop'])} ({_esc(f['suggested_stop_reason'])})"]
    if f.get("safer_leverage") and (not f.get("leverage") or f["leverage"] > f["safer_leverage"]):
        safer.append(f"· At most {f['safer_leverage']}x keeps liquidation well past that stop")
    d = abs(f["entry"] - f["suggested_stop"]) / f["entry"]
    if d > 0:
        safer.append(f"· Risking $10 at that stop = a ${10 / d:,.0f} position (size from the stop, not the leverage)")
    inv = f["support"][0] if long and f.get("support") else f["resistance"][0] if not long and f.get("resistance") else f["suggested_stop"]
    safer.append(f"· Invalidation: a daily close {'below' if long else 'above'} {p(inv)}")
    lines += ["", "🛡 <b>Safer</b>", *safer, "", f"<i>Not financial advice · Hyperliquid data, {f['as_of'][11:16]} UTC</i>"]
    return "\n".join(lines)


def plain(htm: str) -> str:
    """Telegram HTML -> plain text (follow-up history)."""
    return html.unescape(re.sub(r"<[^>]+>", "", htm))


# ---------------------------------------------------------------- Vortex's answer: untrusted text
_URL = re.compile(r"(?i)(?:\b(?:https?|ftp)://|\bwww\.)\S+|\b(?:t\.me|telegram\.(?:me|dog))/\S*"
                  r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|xyz|me|gg|app|co|ru|ly|link|info|biz|finance|exchange|trade|live"
                  r"|site|online|top|vip|cc|tk|to|so|ai|dev|fi|pro|club|bet|cash|money|fun|gl|ws|lol|wtf)\b(?:/\S*)?"
                  r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}/\S*")  # any other domain with a path
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")


def sanitize(text: str) -> str:
    """Plain text only: tags and control characters out, every link and @mention out, blank lines collapsed, capped."""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = _CTRL.sub("", t)
    t = re.sub(r"</?[A-Za-z!][^>\n]{0,300}>", "", t)  # tags only: "a close < 78k ... above > 80k" is prose, not markup
    t = _URL.sub("[link removed]", t)
    t = re.sub(r"(?<!\w)@[A-Za-z]\w{3,}", "", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t[:MAX_ANSWER]


def answer_html(text: str, f: dict[str, Any]) -> str:
    return f"🌀 <b>Vortex · ${_esc(f['coin'])} {_esc(f['side'])}</b>\n\n{_esc(text)}\n\n<i>{FOOTER}</i>"


# ---------------------------------------------------------------- limits
def _day_start(now: datetime) -> datetime:
    return now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def daily_limit(db: Session, tg_user_id: int, cfg: dict[str, Any]) -> tuple[int, bool]:
    """(checks per day, boosted?): more for a linked Kiwi Passport at the boost rank."""
    from app.members import xp
    from app.tradingos.bot import member_for

    m = member_for(db, tg_user_id)
    if m is not None:
        try:
            if xp.rank_for(db, m.rank_score).level >= cfg["min_rank"]:
                return cfg["daily_ranked"], True
        except Exception:  # noqa: BLE001 - no rank catalogue: the public limit
            db.rollback()
    return cfg["daily_public"], False


def used_today(db: Session, tg_user_id: int, now: datetime) -> int:
    return int(db.scalar(select(func.count()).select_from(TsAssistJob).where(
        TsAssistJob.tg_user_id == tg_user_id, TsAssistJob.kind == "assess", TsAssistJob.created_at >= _day_start(now))) or 0)


# ---------------------------------------------------------------- the bot side
def _rules(db: Session) -> tuple[str, float, float, float]:
    s = signals.settings(db)
    return s.timeframe, s.min_volume_usd, s.max_spread_bps, s.min_rr


def _new_job(convo: TsAssistConvo, kind: str, question: str, facts: dict[str, Any], status: str, user_id: int, chat_id: int,
             message_id: int | None, reply_to: int | None, fallback: str, now: datetime) -> TsAssistJob:
    return TsAssistJob(public_id="j_" + secrets.token_hex(8), convo_id=convo.id, kind=kind, status=status, tg_user_id=user_id,
                       chat_id=chat_id, message_id=message_id, reply_to=reply_to, question=question[:MAX_QUESTION], facts=facts,
                       fallback=fallback, claims=0, created_at=now)


def _msg_id(result: Any) -> int | None:
    return int(result["message_id"]) if isinstance(result, dict) and result.get("message_id") else None


async def start(db: Session, client: Any, chat_id: int, user_id: int, text: str, reply_to: int | None, now: datetime | None = None) -> str | None:
    """'/help <trade>': parse, check the daily limit, compute FACTS, then the placeholder for Vortex or the fallback at once.
    Returns a short reply for the caller to send (usage, limit) or None when it answered itself."""
    now = now or utcnow()
    question = text.strip().split(maxsplit=1)[1].strip()[:MAX_QUESTION] if len(text.strip().split(maxsplit=1)) > 1 else ""
    md = market.data()
    _, ms = await asyncio.to_thread(md.markets)
    try:
        tr = parse(question, set(ms))
    except ParseError as e:
        if e.code == "unknown_coin":
            return f"🌀 I can't find {_esc(e.word)} among Hyperliquid perpetuals, so I can't check it. Try BTC, ETH, SOL, HYPE…"
        return USAGE
    cfg = config(db)
    limit, boosted = daily_limit(db, user_id, cfg)
    if used_today(db, user_id, now) >= limit:
        msg = f"🌀 You've used today's {limit} trade checks (they reset at 00:00 UTC)."
        if not boosted and cfg["daily_ranked"] > limit:
            from app.tradingos.bot import SITE

            msg += f" Kiwi Passport members at Rank {cfg['min_rank']}+ get {cfg['daily_ranked']} a day: {SITE}/passport"
        return msg
    facts = await asyncio.to_thread(build_facts, md, tr, _rules(db), now)
    fallback = render_fallback(facts)
    convo = TsAssistConvo(chat_id=chat_id, tg_user_id=user_id, coin=tr.coin, side=tr.side, trade=asdict(tr), facts=facts,
                          followups=0, created_at=now)
    db.add(convo)
    db.flush()
    if online(db, now):
        raw = getattr(client, "_c", client)  # no footer: the message is edited into the answer
        sent = await raw.send_message(chat_id, PLACEHOLDER, html=True, reply_to=reply_to)
        db.add(_new_job(convo, "assess", question, facts, "pending", user_id, chat_id, _msg_id(sent), reply_to, fallback, now))
    else:
        sent = await client.send_message(chat_id, fallback, html=True, reply_to=reply_to)
        job = _new_job(convo, "assess", question, facts, "fallback", user_id, chat_id, _msg_id(sent), reply_to, fallback, now)
        job.answer, job.finished_at = plain(fallback), now
        db.add(job)
    db.commit()
    return None


def is_reply_to_check(db: Session, chat_id: int, message_id: int) -> bool:
    """Is this message one of the bot's trade-check messages (so a reply to it is a follow-up)?"""
    return db.scalar(select(TsAssistJob.id).where(TsAssistJob.chat_id == chat_id, TsAssistJob.message_id == message_id).limit(1)) is not None


async def followup(db: Session, client: Any, chat_id: int, user_id: int, text: str, msg_id: int | None, replied_to: int,
                   now: datetime | None = None) -> str:
    """A reply to a check's message: a follow-up job for Vortex (the asker only, within the window, up to the cap).
    Returns what happened (for logs and tests)."""
    now = now or utcnow()
    prev = db.scalar(select(TsAssistJob).where(TsAssistJob.chat_id == chat_id, TsAssistJob.message_id == replied_to).limit(1))
    convo = db.get(TsAssistConvo, prev.convo_id) if prev else None
    if convo is None or not config(db)["enabled"]:
        return "ignored"
    if convo.tg_user_id != user_id or now - _aware(convo.created_at) > FOLLOWUP_WINDOW or not text.strip():
        return "ignored"
    if convo.followups >= MAX_FOLLOWUPS:
        await client.send_message(chat_id, f"🌀 That's the {MAX_FOLLOWUPS} follow-ups for this check. Send /help with your trade for a new one.",
                                  reply_to=msg_id)
        return "cap"
    if not online(db, now):
        await client.send_message(chat_id, "🌀 Vortex is offline right now, so it can't take follow-ups. /help with your trade gives the "
                                           "numbers again.", reply_to=msg_id)
        return "offline"
    try:
        facts = await asyncio.to_thread(build_facts, market.data(), Trade(**convo.trade), _rules(db), now)
    except Exception:  # noqa: BLE001 - the market can't be read: the check's own numbers
        logger.info("assist: follow-up facts not refreshed", exc_info=True)
        facts = convo.facts
    note = "Vortex didn't answer in time, so here are the numbers again."
    sent = await client.send_message(chat_id, THINKING, html=True, reply_to=msg_id)
    db.add(_new_job(convo, "followup", text.strip(), facts, "pending", user_id, chat_id, _msg_id(sent), msg_id, render_fallback(facts, note), now))
    convo.followups += 1
    db.commit()
    return "queued"


# ---------------------------------------------------------------- the worker side (/api/vortex/*)
class Conflict(Exception):
    """The job is answered, expired or failed already (409)."""


def _history(db: Session, job: TsAssistJob) -> list[dict[str, str]]:
    turns: list[dict[str, str]] = []
    for j in db.scalars(select(TsAssistJob).where(TsAssistJob.convo_id == job.convo_id, TsAssistJob.id < job.id).order_by(TsAssistJob.id)):
        turns.append({"role": "user", "text": j.question[:MAX_QUESTION]})
        if j.answer:
            turns.append({"role": "vortex", "text": j.answer[:MAX_ANSWER]})
    return turns[-MAX_HISTORY:]


def claim(db: Session, max_jobs: int, now: datetime | None = None) -> list[dict[str, Any]]:
    """Hand out up to max_jobs pending jobs (or claimed ones whose lease ran out), leased for LEASE."""
    now = now or utcnow()
    if not config(db)["enabled"]:
        return []
    q = (select(TsAssistJob).where(TsAssistJob.status.in_(OPEN), TsAssistJob.created_at > now - EXPIRE_AFTER,
                                   (TsAssistJob.status == "pending") | (TsAssistJob.lease_until < now))
         .order_by(TsAssistJob.id).limit(max_jobs).with_for_update(skip_locked=True))
    out = []
    for j in db.scalars(q).all():
        j.status, j.claims, j.lease_until = "claimed", j.claims + 1, now + LEASE
        out.append({"id": j.public_id, "kind": j.kind, "question": j.question[:MAX_QUESTION], "facts": j.facts,
                    "history": _history(db, j) if j.kind == "followup" else [], "lease_seconds": int(LEASE.total_seconds()),
                    "lang_hint": "en"})
    db.commit()
    return out


def pending_count(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(TsAssistJob).where(TsAssistJob.status.in_(OPEN))) or 0)


def _take(db: Session, job: TsAssistJob, status: str, now: datetime) -> bool:
    """Move an open job to its final status, once: whoever wins (answer, fail, expiry) delivers its text."""
    res = db.execute(update(TsAssistJob).where(TsAssistJob.id == job.id, TsAssistJob.status.in_(OPEN))
                     .values(status=status, finished_at=now).execution_options(synchronize_session=False))
    db.commit()
    db.refresh(job)
    return bool(res.rowcount)


async def _deliver(client: Any, job: TsAssistJob, htm: str) -> None:
    """Edit the placeholder into the text; when it can't be edited (deleted, too old), send it as a new reply."""
    from app.telegram.errors import TelegramRequestError

    if job.message_id:
        try:
            await client.edit_message_text(job.chat_id, job.message_id, htm, html=True)
            return
        except TelegramRequestError as e:
            if "not modified" in str(e).lower():
                return
            logger.info("assist: placeholder not edited (%s), sending anew", str(e)[:120])
    sent = await client.send_message(job.chat_id, htm, html=True, reply_to=job.reply_to)
    job.message_id = _msg_id(sent) or job.message_id


async def _fallback(db: Session, client: Any, job: TsAssistJob, status: str, now: datetime) -> bool:
    if not _take(db, job, status, now):
        return False
    try:
        await _deliver(client, job, job.fallback)
    finally:
        job.answer = plain(job.fallback)
        db.commit()
    return True


def find(db: Session, public_id: str) -> TsAssistJob | None:
    return db.scalar(select(TsAssistJob).where(TsAssistJob.public_id == public_id[:24]))


async def answer(db: Session, client: Any, job: TsAssistJob, text: str, now: datetime | None = None) -> None:
    """Vortex's answer: sanitised, edited into the placeholder. Conflict when the job is finished, or expired (a late answer
    turns the placeholder into the fallback first)."""
    now = now or utcnow()
    if job.status not in OPEN:
        raise Conflict("This job is already finished.")
    if now - _aware(job.created_at) > EXPIRE_AFTER:
        await _fallback(db, client, job, "expired", now)
        raise Conflict("Too late: the bot already answered with its own assessment.")
    clean = sanitize(text)
    if not re.sub(r"\[link removed\]", "", clean).strip():
        raise ValueError("empty")
    if not _take(db, job, "answered", now):
        raise Conflict("This job is already finished.")
    try:
        await _deliver(client, job, answer_html(clean, job.facts))
    finally:
        job.answer = clean
        db.commit()


async def fail(db: Session, client: Any, job: TsAssistJob, now: datetime | None = None) -> None:
    if not await _fallback(db, client, job, "failed", now or utcnow()):
        raise Conflict("This job is already finished.")


async def expire(db: Session, client: Any, now: datetime | None = None) -> int:
    """tradingos.vortex_expire: placeholders still unanswered after EXPIRE_AFTER become the fallback."""
    from app.telegram.errors import TelegramRateLimitError, TelegramUnavailableError

    now = now or utcnow()
    n = 0
    for job in db.scalars(select(TsAssistJob).where(TsAssistJob.status.in_(OPEN), TsAssistJob.created_at <= now - EXPIRE_AFTER)
                          .order_by(TsAssistJob.id).limit(50)).all():
        try:
            n += await _fallback(db, client, job, "expired", now)
        except (TelegramUnavailableError, TelegramRateLimitError):
            break  # Telegram down or busy: marked expired, the asker misses this one (no double posts)
        except Exception:  # noqa: BLE001 - one bad job never blocks the rest
            logger.exception("assist: expiry of %s failed", job.public_id)
            db.rollback()
    return n


def stale(db: Session, now: datetime | None = None) -> bool:
    now = now or utcnow()
    return db.scalar(select(TsAssistJob.id).where(TsAssistJob.status.in_(OPEN), TsAssistJob.created_at <= now - EXPIRE_AFTER).limit(1)) is not None
