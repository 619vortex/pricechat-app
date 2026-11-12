"""/signal as a compact trading card (owner, 2026-11-12: "premium interactive Hyperliquid trading assistant").

One verdict per card, from the rules in signals.analyze and nothing else:
  LONG / SHORT  every rule passes (signals.analyze). Entry zone, SL, TP1 (the rules' target), TP2 (1.5x the TP1 distance,
                as execution places it), risk/reward after fees and spread, leverage, and the invalidation: a trade through
                the stop, or no fill within record.FILL_WITHIN candles (exactly how the record grades it).
  WAIT          anything else. Never a made-up plan: for each side, the exact conditions still missing, with their values now
                (EMA20/EMA50, the EMA20 pullback zone, RSI band, MACD, funding, BTC), straight from the same rules.

Confidence is evidence, not a guess: LONG/SHORT only exist when every rule passes, so the grade is how many of 15m / 1h / 4h
trend the same way (3 High, 2 Medium, 0-1 Low), shown with its basis. A WAIT shows how many conditions each side meets.

Every card is stored (ts_signal_cards), WAIT included: LONG/SHORT link the plan graded on real candles in ts_signal_ideas
(TP1, SL, never filled); WAIT is graded here on the candles that followed. The record shows all of it, never a best-of.
Buttons: Chart, Alert Me, Refresh, Trade (LONG/SHORT on Hyperliquid), Details. Trade only opens tgtrade's own /open preview
privately: the member's limits, the emergency stop, the mainnet switch and Confirm all still apply; nothing is sent from here.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingos import TsPriceAlert, TsSignalCard, TsSignalIdea
from app.tradingos import indicators as ind
from app.tradingos import record, risk, signals
from app.tradingos.market import INTERVALS, fmt_price

logger = logging.getLogger(__name__)

TFS = ("15m", "1h", "4h")
FUNDING_MAX = 0.0001  # signals.analyze: funding over this paying the trade's side = crowded
PULLBACK_ATR = 0.6
DEDUPE = timedelta(minutes=30)  # the same market, timeframe and verdict (and plan) within this is the same card
WAIT_GRADE = 24  # candles after a WAIT that its result covers
WAIT_GIVE_UP = 3 * WAIT_GRADE
MAX_TRIGGERS = 4  # the side closer to a setup
FAR_TRIGGERS = 2  # the other side (all of it in Details)
SETUP_ALERT_TTL = timedelta(days=7)
SETUP_CHECKS_PER_RUN = 6  # markets re-read per alerts run (each a few Hyperliquid calls)
CHART_EVERY = 90.0  # seconds: one chart per card in that time (the rest get a toast)
REFRESH_EVERY = 15.0
RISK_LINE = "<i>Not financial advice · leverage can lose more than the stop.</i>"
WAIT_LINE = "<i>Not financial advice.</i>"
ARROW = {"up": "▲", "down": "▼", "flat": "▬", None: "·"}
f = fmt_price


def esc(s: Any) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@dataclass
class Cond:
    ok: bool
    need: str  # the condition as a trigger, with its value now
    rule: str
    level: tuple[float, str] | None = None  # a price the chart can draw for it


# ---------------------------------------------------------------- reading the market
def trend_of(closes: list[float]) -> str | None:
    """The trend rule's direction on one timeframe: up (EMA20 > EMA50, price above EMA50), down (mirror), else flat."""
    if len(closes) < 55:
        return None
    e20, e50 = ind.ema(closes, 20), ind.ema(closes, 50)
    if not e20 or not e50:
        return None
    p = closes[-1]
    if e20[-1] > e50[-1] and p > e50[-1]:
        return "up"
    if e20[-1] < e50[-1] and p < e50[-1]:
        return "down"
    return "flat"


def context(closes: list[float], highs: list[float], lows: list[float], volumes: list[float]) -> dict[str, Any] | None:
    """What the side conditions read, from closed candles (the same arithmetic as signals.analyze)."""
    if len(closes) < 60:
        return None
    e20, e50 = ind.ema(closes, 20), ind.ema(closes, 50)
    rsi, m, m_prev, atr = ind.rsi(closes), ind.macd(closes), ind.macd(closes[:-1]), ind.atr(highs, lows, closes)
    if not (e20 and e50 and rsi is not None and m and atr):
        return None
    sup, res = ind.swing_levels(highs[:-1], lows[:-1], 48)
    return {"price": closes[-1], "ema20": e20[-1], "ema50": e50[-1], "ema50_prev": e50[-6], "rsi": rsi, "hist": m[2],
            "hist_prev": m_prev[2] if m_prev else None, "atr": atr, "support": sup, "resistance": res,
            "vol_ratio": ind.volume_ratio(volumes)}


def side_conditions(side: str, x: dict[str, Any], tf: str, funding: float | None = None,
                    btc: tuple[float, float] | None = None) -> list[Cond]:
    """signals.analyze's conditions for one side, each with what it needs now. The trend rule is split into its three parts
    so a WAIT can say exactly which one is missing."""
    long = side == "LONG"
    p, e20, e50, atr = x["price"], x["ema20"], x["ema50"], x["atr"]
    word = "above" if long else "below"
    out = [Cond(e20 > e50 if long else e20 < e50, f"EMA20 {word} EMA50 (now {f(e20)} vs {f(e50)})", "trend"),
           Cond(p > e50 if long else p < e50, f"{tf} close {word} EMA50 {f(e50)}", "trend", (e50, f"{side}: close {word} EMA50")),
           Cond(e50 > x["ema50_prev"] if long else e50 < x["ema50_prev"],
                f"EMA50 turning {'up' if long else 'down'} (now {'rising' if e50 > x['ema50_prev'] else 'falling'})", "trend")]
    dist = abs(p - e20) / atr
    lo, hi = e20 - PULLBACK_ATR * atr, e20 + PULLBACK_ATR * atr
    edge = (hi, f"{side}: pullback zone top") if long else (lo, f"{side}: bounce zone bottom")
    out.append(Cond(dist <= PULLBACK_ATR, f"price back into {f(lo)}–{f(hi)} (EMA20 ±0.6 ATR, now {dist:.1f} ATR away)", "pullback", edge))
    r_lo, r_hi = (40, 65) if long else (35, 60)
    out.append(Cond(r_lo <= x["rsi"] <= r_hi, f"RSI {r_lo}–{r_hi} (now {x['rsi']:.0f})", "rsi"))
    h, hp = x["hist"], x["hist_prev"]
    turning = hp is not None and (h > hp if long else h < hp)
    out.append(Cond(turning or (h > 0 if long else h < 0), f"MACD histogram {'rising or above 0' if long else 'falling or below 0'} (now {h:+.3g})", "macd"))
    if funding is not None:
        ok = funding <= FUNDING_MAX if long else funding >= -FUNDING_MAX
        out.append(Cond(ok, f"funding {'at most +' if long else 'at least −'}0.0100%/h (now {funding * 100:+.4f}%)", "funding"))
    if btc is not None:
        bpx, b50 = btc
        out.append(Cond(not (bpx < b50 if long else bpx > b50), f"BTC {word} its {tf} EMA50 {f(b50)} (now {f(bpx)})", "btc"))
    return out


def market_gates(i: dict[str, Any], min_volume: float, max_spread: float) -> list[Cond]:
    """The rules that block both sides (signals.analyze): volume, spread, depth, volatility. Empty off Hyperliquid."""
    if "volume_24h" not in i:
        return []
    spread = max(i.get("spread_bps") or 0, i.get("impact_spread_bps") or 0)
    depth = min(i.get("depth_bid") or 0, i.get("depth_ask") or 0)
    atr_pct = i.get("atr_pct") or 0
    vol = i["volume_24h"] / 1e6
    return [Cond(i["volume_24h"] >= min_volume, f"24h volume ${vol:,.{1 if vol < 10 else 0}f}M (needs ${min_volume / 1e6:,.0f}M)", "volume"),
            Cond(spread <= max_spread, f"spread {spread:.1f} bps (max {max_spread:g})", "spread"),
            Cond(depth >= signals.MIN_DEPTH_USD, f"order book ${depth / 1e3:,.0f}k within 0.5% (needs $100k)", "depth"),
            Cond(0.25 <= atr_pct <= 5, f"volatility {atr_pct:.2f}% ATR (needs 0.25–5%)", "volatility")]


def confidence(side: str, tfs: dict[str, str | None]) -> tuple[str, int]:
    want = "up" if side == "LONG" else "down"
    agree = sum(1 for t in TFS if tfs.get(t) == want)
    return ("High" if agree == 3 else "Medium" if agree == 2 else "Low"), agree


def _costs(mid: float, spread_bps: float) -> float:
    return mid * (2 * signals.TAKER_FEE) + mid * spread_bps / 10_000


def plan_of(a: Any, tf: str, now: datetime) -> dict[str, Any]:
    lo, hi = min(a.entry), max(a.entry)
    mid = (lo + hi) / 2
    tp2 = risk.tp2_of(mid, a.target)
    i = a.indicators
    cost = _costs(mid, max(i.get("spread_bps") or 0, i.get("impact_spread_bps") or 0))
    rr2 = (abs(tp2 - mid) - cost) / (abs(mid - a.stop) + cost)
    expires = now + timedelta(seconds=INTERVALS[tf] * record.FILL_WITHIN)
    return {"side": a.decision, "lo": lo, "hi": hi, "stop": a.stop, "tp1": a.target, "tp2": tp2, "rr1": a.rr, "rr2": round(rr2, 2),
            "lev": list(a.lev or (1, 1)), "expires": expires.isoformat()}


def read(md: Any, listing: Any, tf: str, st: Any, vs: Any = None) -> tuple[signals.Analysis, list[Any], dict[str, Any]]:
    """The analysis, its candles, and the card's evidence. Blocking (Hyperliquid reads): run it in a thread."""
    from app.tradingos import venues

    hl = listing.venue == "hyperliquid"
    if hl:
        a = signals.analyze(md, listing.symbol, tf, st.min_volume_usd, st.max_spread_bps, st.min_rr)
        candles = md.candles(a.coin, tf, 201)  # the same read analyze made (cached)

        def other(t: str) -> list[Any]:
            return md.candles(a.coin, t, 201 if t == tf else 120)
    else:
        candles = venues.candles(listing, tf, 200, md, vs or venues.client())
        a = signals.Analysis(coin=listing.tag, decision="NO TRADE", reasons=["the setup rules don't run on this market"])

        def other(t: str) -> list[Any]:
            return candles if t == tf else venues.candles(listing, t, 120, md, vs or venues.client())
    a.indicators = {**signals.chart_indicators(candles, tf), **a.indicators}
    cs = signals.closed(candles, tf)[-200:]
    x = context([c.c for c in cs], [c.h for c in cs], [c.l for c in cs], [c.v for c in cs])
    tfs: dict[str, str | None] = {}
    for t in TFS:
        try:
            tfs[t] = trend_of([c.c for c in signals.closed(other(t), t)])
        except Exception as e:  # noqa: BLE001 - one timeframe missing never blocks the card
            logger.info("signal card: no %s %s candles (%s)", listing.tag, t, type(e).__name__)
            tfs[t] = None
    btc = None
    if hl and a.coin != "BTC" and x is not None:
        bc = [c.c for c in signals.closed(md.candles("BTC", tf, 121), tf)]
        b50 = ind.ema(bc, 50)
        btc = (bc[-1], b50[-1]) if b50 else None
    i = a.indicators
    ev: dict[str, Any] = {"label": listing.label, "change_24h": listing.change_24h, "live": listing.price or i.get("price"), "tfs": tfs,
                          "inds": {k: (round(v, 10) if isinstance(v, float) else v) for k, v in {
                              "rsi": x and x["rsi"], "hist": x and x["hist"], "hist_prev": x and x["hist_prev"], "ema20": x and x["ema20"],
                              "ema50": x and x["ema50"], "atr": x and x["atr"], "vol_ratio": x and x["vol_ratio"],
                              "support": i.get("support"), "resistance": i.get("resistance"), "funding": i.get("funding_h"),
                              "volume_24h": i.get("volume_24h"), "oi_usd": i.get("oi_usd"),
                              "spread": max(i.get("spread_bps") or 0, i.get("impact_spread_bps") or 0) if hl else None,
                              "depth": min(i.get("depth_bid") or 0, i.get("depth_ask") or 0) if hl else None}.items()},
                          "checks": a.checks, "reasons": a.reasons[:3], "note": "" if hl else a.reasons[0], "closed": len(cs)}
    gates = market_gates(i, st.min_volume_usd, st.max_spread_bps)
    if hl and len(cs) < 120:
        gates.append(Cond(False, f"{tf} history: {len(cs)} closed candles (the rules need 120)", "history"))
    ev["blockers"] = [g.need for g in gates if not g.ok]
    if x is None or not hl:  # no read, or a market the rules don't run on: no triggers (they could never make a setup there)
        ev.update(met={}, triggers={}, sides={}, marks=[])
        return a, candles, ev
    sides = {s: side_conditions(s, x, tf, i.get("funding_h") if hl else None, btc) for s in ("LONG", "SHORT")}
    for s, cs_ in sides.items():  # every condition met, yet NO TRADE: the plan check that failed (a level in the way, R/R, stop width)
        if hl and a.decision == "NO TRADE" and all(c.ok for c in cs_) and not ev["blockers"] and a.reasons:
            cs_.append(Cond(False, f"plan check: {a.reasons[0]}", "plan"))
    ev["sides"] = {s: [[c.ok, c.need] for c in cs_] for s, cs_ in sides.items()}
    ev["met"] = {s: [sum(c.ok for c in gates + cs_), len(gates) + len(cs_)] for s, cs_ in sides.items()}
    ev["triggers"] = {s: [c.need for c in cs_ if not c.ok] for s, cs_ in sides.items()}
    ev["marks"] = [[c.level[0], c.level[1], s] for s, cs_ in sides.items() for c in cs_ if not c.ok and c.level]
    return a, candles, ev


# ---------------------------------------------------------------- storing
def store(db: Session, listing: Any, tf: str, a: Any, ev: dict[str, Any], chat_type: str, now: datetime | None = None) -> TsSignalCard:
    """The card for the record (or the same one shown within DEDUPE: same market, timeframe, verdict and plan)."""
    now = now or utcnow()
    setup = a.decision in ("LONG", "SHORT") and listing.venue == "hyperliquid"
    verdict = a.decision if setup else "WAIT"
    plan = plan_of(a, tf, now) if setup else None
    conf = ""
    if setup:
        conf, agree = confidence(verdict, ev["tfs"])
        ev = {**ev, "agree": agree, "rules": [sum(1 for c in a.checks if c.get("ok")), len(a.checks)]}
    price = float(ev.get("live") or a.indicators.get("price") or 0.0)
    for row in db.scalars(select(TsSignalCard).where(TsSignalCard.coin == listing.symbol, TsSignalCard.timeframe == tf,
                                                    TsSignalCard.verdict == verdict, TsSignalCard.created_at >= now - DEDUPE)
                          .order_by(TsSignalCard.id.desc())):
        if not setup or (row.plan and abs((row.plan["lo"] + row.plan["hi"]) / 2 - (plan["lo"] + plan["hi"]) / 2) <= record.SAME_ENTRY * plan["lo"]):
            row.data, row.price, row.confidence = ev, price, conf  # the newest read on the same card
            if setup:
                row.plan = {**plan, "expires": row.plan["expires"]}  # the fill window runs from when it was first shown
            db.commit()
            return row
    idea_id = None
    if setup:
        try:
            w = signals.best_plan(a)
            idea_id = record.log_plan(db, listing, tf, w, chat_type).id
        except Exception:  # noqa: BLE001 - the record never blocks a reply
            logger.exception("signal card plan not logged")
            db.rollback()
    row = TsSignalCard(venue=listing.venue, coin=listing.symbol, tag=listing.tag[:64], timeframe=tf, verdict=verdict, price=price,
                       confidence=conf, plan=plan, data=ev, idea_id=idea_id, chat_type=chat_type[:8],
                       status="OPEN" if listing.venue in record.GRADED_VENUES else "SKIPPED")
    db.add(row)
    db.commit()
    return row


# ---------------------------------------------------------------- the card
def _pct(px: float, base: float) -> str:
    return f"{(px - base) / base * 100:+.1f}%".replace("-", "−")


def _when(iso: str) -> str:
    d = datetime.fromisoformat(iso).astimezone(timezone.utc)
    return d.strftime("%H:%M UTC") if d - utcnow() < timedelta(hours=20) else d.strftime("%d %b %H:%M UTC")


def tf_row(tfs: dict[str, Any]) -> str:
    return " │ ".join(f"{t} {ARROW.get(tfs.get(t))}" for t in TFS)


def ind_row(i: dict[str, Any]) -> str:
    bits = []
    if i.get("rsi") is not None:
        bits.append(f"RSI {i['rsi']:.0f}")
    if i.get("hist") is not None:
        rising = i.get("hist_prev") is not None and i["hist"] > i["hist_prev"]
        bits.append(f"MACD {'>' if i['hist'] > 0 else '<'}0 {'▲' if rising else '▼'}")
    if i.get("ema20") and i.get("ema50"):
        bits.append(f"EMA20 {'>' if i['ema20'] > i['ema50'] else '<'} EMA50")
    if i.get("vol_ratio") is not None:
        bits.append(f"Vol {i['vol_ratio']:.1f}× avg")
    return esc(" · ".join(bits))  # "MACD <0" / "EMA20 < EMA50" are text: a raw "<0" made Telegram refuse the whole card


def _head(c: TsSignalCard) -> list[str]:
    d = c.data or {}
    icon = {"LONG": "🟢", "SHORT": "🔴"}.get(c.verdict, "⏸")
    chg = d.get("change_24h")
    move = f"  {chg:+.2f}% 24h".replace("-", "−") if chg is not None else ""
    return [f"{icon} <b>{c.verdict}  {esc(c.tag)}</b> · {c.timeframe} · {esc(d.get('label') or c.venue)}", f"<b>{f(c.price)}</b>{move}"]


def record_line(db: Session) -> str:
    s = setup_stats(db)
    if not s["graded"]:
        return f"📊 Setup record: none graded yet ({s['open']} being followed) · /record"
    return (f"📊 Setup record: {s['graded']} graded · {s['tp1']} TP1 · {s['sl']} SL · {s['unfilled']} never filled"
            + (f" · avg {s['avg_r']:+.2f}R" if s["avg_r"] is not None else "") + " · /record")


def text(db: Session | None, c: TsSignalCard, lead: str = "") -> str:
    """The compact card, Telegram HTML (fits a 1024-character photo caption too)."""
    d, i = c.data or {}, (c.data or {}).get("inds") or {}
    out = ([lead, ""] if lead else []) + _head(c)
    if c.plan:
        p = c.plan
        mid = (p["lo"] + p["hi"]) / 2
        lev = f"{p['lev'][0]}x" if p["lev"][0] == p["lev"][1] else f"{p['lev'][0]}–{p['lev'][1]}x"
        rules = d.get("rules") or [0, 0]
        out += [f"Confidence <b>{c.confidence}</b> · rules {rules[0]}/{rules[1]} · 15m/1h/4h {d.get('agree', 0)}/3 agree", "",
                "<code>"
                f"Entry {f(p['lo'])} – {f(p['hi'])}\n"
                f"SL    {f(p['stop'])}  {_pct(p['stop'], mid)}\n"
                f"TP1   {f(p['tp1'])}  {_pct(p['tp1'], mid)}  {p['rr1']:.1f}R\n"
                f"TP2   {f(p['tp2'])}  {_pct(p['tp2'], mid)}  {p['rr2']:.1f}R\n"
                f"Lev   {lev}</code>",
                f"⛔ Invalid if price trades {f(p['stop'])} or the zone isn't filled by {_when(p['expires'])}"]
    else:
        met = d.get("met") or {}
        if d.get("note"):
            out.append(f"Chart read only: {esc(d['note'])}")
        elif met:
            out.append(f"No setup yet · LONG {met['LONG'][0]}/{met['LONG'][1]} · SHORT {met['SHORT'][0]}/{met['SHORT'][1]} conditions met")
        if d.get("blockers"):
            out.append("⛔ Blocked both ways: " + esc("; ".join(d["blockers"])) + ". No setup until this clears.")
        heads = {"LONG": "▲ <b>LONG when</b>", "SHORT": "▼ <b>SHORT when</b>"}
        order = sorted(heads, key=lambda s_: -(met.get(s_) or [0])[0])  # the side closer to a setup first, in full; the other in short
        for n_side, side in enumerate(order):
            need = (d.get("triggers") or {}).get(side) or []
            room = MAX_TRIGGERS if n_side == 0 else FAR_TRIGGERS
            if need:
                more = f"\n  +{len(need) - room} more · Details" if len(need) > room else ""
                out += ["", heads[side], "\n".join(f"• {esc(n)}" for n in need[:room]) + more]
        if not met and not d.get("note"):
            out.append(f"Not enough {c.timeframe} history for a read yet.")
    out += ["", f"<code>{tf_row(d.get('tfs') or {})}</code>"]
    row = ind_row(i)
    if row:
        out.append(row)
    if c.plan and db is not None:
        out += ["", record_line(db), RISK_LINE]
    else:
        out += ["", WAIT_LINE]
    return "\n".join(out)


def _mark(ok: bool | None) -> str:
    return "✅" if ok else "▫️" if ok is None else "❌"


def details(c: TsSignalCard) -> str:
    """Every rule with its value, the timeframes, the market: the long read, on request."""
    d, i = c.data or {}, (c.data or {}).get("inds") or {}
    out = [f"📋 <b>{esc(c.tag)} · {c.timeframe} · the full read</b>"]
    if c.plan:
        out += ["", "<b>Rules (all must pass)</b>", *(f"{_mark(x.get('ok'))} {esc(x['rule'])}: {esc(x['value'])}" for x in d.get("checks") or [])]
    else:
        for side, rows in (d.get("sides") or {}).items():
            out += ["", f"<b>{side}</b> {d['met'][side][0]}/{d['met'][side][1]}", *(f"{_mark(ok)} {esc(need)}" for ok, need in rows)]
        if d.get("blockers"):
            out += ["", "<b>Market (both sides)</b>", *(f"❌ {esc(b)}" for b in d["blockers"])]
    out += ["", "<b>Trend</b> " + " · ".join(f"{t} {(d.get('tfs') or {}).get(t) or 'n/a'}" for t in TFS)]
    mk = []
    if i.get("volume_24h"):
        mk.append(f"24h vol ${i['volume_24h'] / 1e6:,.0f}M")
    if i.get("oi_usd"):
        mk.append(f"OI ${i['oi_usd'] / 1e6:,.0f}M")
    if i.get("funding") is not None:
        mk.append(f"funding {i['funding'] * 100:+.4f}%/h")
    if i.get("spread") is not None:
        mk.append(f"spread {i['spread']:.1f} bps")
    if i.get("depth"):
        mk.append(f"depth ${i['depth'] / 1e3:,.0f}k")
    if mk:
        out.append("<b>Market</b> " + " · ".join(mk))
    lv = [x for x in (f"S {f(i['support'])}" if i.get("support") else "", f"R {f(i['resistance'])}" if i.get("resistance") else "") if x]
    if lv:
        out.append("<b>Levels</b> " + " · ".join(lv) + " (48-candle swing)")
    if i.get("atr"):
        out.append(f"<b>ATR</b> {f(i['atr'])} · EMA20 {f(i['ema20'])} · EMA50 {f(i['ema50'])}")
    out += ["", f"Card #{c.id} · read {c.updated_at.astimezone(timezone.utc).strftime('%H:%M UTC') if c.updated_at else ''} on closed "
                f"{c.timeframe} candles · prices from {esc(d.get('label') or c.venue)}",
            RISK_LINE if c.plan else WAIT_LINE]
    return "\n".join(out)


def tradable(c: TsSignalCard, now: datetime | None = None) -> bool:
    return bool(c.plan and c.venue == "hyperliquid" and datetime.fromisoformat(c.plan["expires"]) > (now or utcnow()))


def buttons(c: TsSignalCard, view: str = "card") -> list[list[dict[str, str]]]:
    k = f"sc:{c.id}:"
    row1 = [{"text": "📈 Chart", "callback_data": k + "c"}]
    if c.venue == "hyperliquid":
        row1.append({"text": "🔔 Alert Me", "callback_data": k + "a"})
    row1.append({"text": "🔄 Refresh", "callback_data": k + "r"})
    row2 = [{"text": "⚡ Trade", "callback_data": k + "t"}] if tradable(c) else []
    row2.append({"text": "◀ Card", "callback_data": k + "s"} if view == "details" else {"text": "📋 Details", "callback_data": k + "d"})
    return [row1, row2]


@dataclass
class PlanView:
    """What chart.render_study draws for a card's plan."""
    side: str
    entry: tuple[float, float]
    stop: float
    target: float
    tp2: float
    full: bool = True


def chart_png(c: TsSignalCard, candles: list[Any]) -> bytes:
    from app.tradingos import chart

    d, i = c.data or {}, (c.data or {}).get("inds") or {}
    p = c.plan
    plan = PlanView(p["side"], (p["lo"], p["hi"]), p["stop"], p["tp1"], p["tp2"]) if p else None
    marks = [] if p else [(m[0], m[1], "LONG" == m[2]) for m in d.get("marks") or []]
    title = f"{c.tag} · {c.timeframe} · {c.verdict if p else 'WAIT'}"
    return chart.render_study(candles, title, d.get("label") or c.venue, show=min(120, len(candles)), interval_ms=INTERVALS[c.timeframe] * 1000,
                              levels=(i.get("support"), i.get("resistance")), plan=plan, change=d.get("change_24h"),
                              source=d.get("label") or "Hyperliquid", marks=marks)


# ---------------------------------------------------------------- the record
def setup_stats(db: Session) -> dict[str, Any]:
    """Every LONG/SHORT card's plan, graded on real candles: nothing removed (failed and never-filled setups count)."""
    ids = select(TsSignalCard.idea_id).where(TsSignalCard.verdict.in_(("LONG", "SHORT")), TsSignalCard.idea_id.is_not(None)).distinct()
    xs = list(db.scalars(select(TsSignalIdea).where(TsSignalIdea.id.in_(ids))))
    done = [x for x in xs if x.status in record.DONE]
    filled = [x for x in done if x.filled_at is not None]
    rs = [x.r for x in filled if x.r is not None]
    return {"shown": len(xs), "open": sum(x.status in record.OPEN for x in xs), "graded": len(done),
            "tp1": sum(x.status == "TARGET" for x in done), "sl": sum(x.status == "STOP" for x in done),
            "timed_out": sum(x.status == "EXPIRED" and x.filled_at is not None for x in done),
            "unfilled": sum(x.status == "EXPIRED" and x.filled_at is None for x in done),
            "avg_r": sum(rs) / len(rs) if rs else None, "filled": len(filled)}


def wait_stats(db: Session) -> dict[str, Any]:
    rows = list(db.scalars(select(TsSignalCard).where(TsSignalCard.verdict == "WAIT")))
    g = [r.result for r in rows if r.status == "GRADED" and r.result]
    n = len(g)
    return {"shown": len(rows), "graded": n, "open": sum(r.status == "OPEN" for r in rows),
            "up": sum(x["high_pct"] for x in g) / n if n else None, "down": sum(x["low_pct"] for x in g) / n if n else None,
            "big": sum(1 for x in g if max(x["high_pct"], -x["low_pct"]) >= x.get("atr_pct", 99) * 2) if n else 0}


def record_text(db: Session) -> str:
    s, w = setup_stats(db), wait_stats(db)
    out = ["📈 <b>/signal record</b> · every card, nothing removed", "",
           f"<b>LONG/SHORT setups</b> · {s['shown']} shown · {s['open']} still open"]
    if s["graded"]:
        out.append(f"<code>graded {s['graded']:>4}\nTP1    {s['tp1']:>4}\nSL     {s['sl']:>4}\ntimed  {s['timed_out']:>4}  (filled, no level in "
                   f"{record.RESOLVE_WITHIN} candles)\nunfill {s['unfilled']:>4}  (zone never reached)</code>")
        if s["avg_r"] is not None:
            out.append(f"Average result of the {s['filled']} filled: {s['avg_r']:+.2f}R (before fees)")
    else:
        out.append("None graded yet.")
    out += ["", f"<b>WAIT</b> · {w['shown']} shown · {w['graded']} graded"]
    if w["graded"]:
        out.append(f"Over the next {WAIT_GRADE} candles price went on average {w['up']:+.1f}% up and {w['down']:+.1f}% down at its extremes; "
                   f"in {w['big']} of {w['graded']} it ran more than 2 ATR one way.")
    out += ["", "TP1 = target before stop · SL = stop first (both in one candle counts as SL) · R = result in risk units.",
            WAIT_LINE]
    return "\n".join(out)


def grade_wait(c: TsSignalCard, candles: list[Any], now: datetime) -> bool:
    span = INTERVALS[c.timeframe] * 1000
    t0 = c.created_at.timestamp() * 1000
    cs = sorted((x for x in candles if x.t >= t0 and x.t + span <= now.timestamp() * 1000), key=lambda x: x.t)[:WAIT_GRADE]
    if len(cs) < WAIT_GRADE:
        return False
    atr = ((c.data or {}).get("inds") or {}).get("atr")
    c.result = {"high_pct": round((max(x.h for x in cs) / c.price - 1) * 100, 3), "low_pct": round((min(x.l for x in cs) / c.price - 1) * 100, 3),
                "close_pct": round((cs[-1].c / c.price - 1) * 100, 3), "candles": len(cs),
                "atr_pct": round(atr / c.price * 100, 4) if atr and c.price else None}
    c.status, c.resolved_at = "GRADED", datetime.fromtimestamp((cs[-1].t + span) / 1000, tz=timezone.utc)
    return True


def grade_all(db: Session, md: Any, now: datetime | None = None) -> dict[str, int]:
    """With the tradingos.ideas job: WAIT cards old enough get their result (Hyperliquid candles); too old to read: SKIPPED."""
    now = now or utcnow()
    rows = list(db.scalars(select(TsSignalCard).where(TsSignalCard.verdict == "WAIT", TsSignalCard.status == "OPEN")))
    out = {"graded": 0, "skipped": 0, "errors": 0}
    groups: dict[tuple[str, str], list[TsSignalCard]] = {}
    for c in rows:
        age = (now - c.created_at).total_seconds() / INTERVALS[c.timeframe]
        if c.venue != "hyperliquid" or age > WAIT_GIVE_UP:
            c.status = "SKIPPED"
            out["skipped"] += 1
        elif age >= WAIT_GRADE + 1:
            groups.setdefault((c.coin, c.timeframe), []).append(c)
    for (coin, tf), cs in groups.items():
        n = int(max((now - c.created_at).total_seconds() / INTERVALS[tf] for c in cs)) + 3
        try:
            candles = md.candles(coin, tf, n)
        except Exception as e:  # noqa: BLE001 - retried next run
            logger.info("WAIT grading: no %s %s candles (%s)", coin, tf, type(e).__name__)
            out["errors"] += 1
            continue
        out["graded"] += sum(grade_wait(c, candles, now) for c in cs)
    db.commit()
    return out


# ---------------------------------------------------------------- sending, buttons
async def build(db: Session, md: Any, listing: Any, tf: str, chat_type: str, vs: Any = None) -> tuple[TsSignalCard, list[Any]]:
    a, candles, ev = await asyncio.to_thread(read, md, listing, tf, signals.settings(db), vs)
    return store(db, listing, tf, a, ev, chat_type), candles


async def send(db: Session, client: Any, chat_id: int | str, c: TsSignalCard, reply_to: int | None = None, lead: str = "") -> Any:
    return await client.send_message(chat_id, text(db, c, lead), buttons(c), reply_to=reply_to, html=True)


_recent: dict[tuple[int, str], float] = {}  # (card, action) -> last time: chart and refresh limits


def _too_soon(card_id: int, action: str, every: float) -> bool:
    now = time.monotonic()
    if now - _recent.get((card_id, action), -1e9) < every:
        return True
    _recent[(card_id, action)] = now
    if len(_recent) > 2000:
        for k in sorted(_recent, key=_recent.get)[:1000]:  # type: ignore[arg-type]
            _recent.pop(k, None)
    return False


async def _listing(c: TsSignalCard, md: Any) -> Any:
    from app.tradingos import tickers, venues

    sym = tickers.symbol_args([c.coin if c.venue == "hyperliquid" else c.tag])[0]
    return await asyncio.to_thread(venues.resolve, sym, md, venues.client())


async def callback(db: Session, client: Any, cq: dict[str, Any], uid: int, data: str) -> None:
    """sc:<card>:<c chart | a alert | r refresh | t trade | d details | s card>."""
    from app.tradingos import market
    from app.tradingos.bot import _answer

    try:
        _, cid, action = data.split(":")
        c = db.get(TsSignalCard, int(cid))
    except ValueError:
        c = None
    if c is None:
        await _answer(client, cq, uid, "This card isn't available any more. Send /signal again.", dm=False)
        return
    msg = cq.get("message") or {}
    chat_id, mid = (msg.get("chat") or {}).get("id"), msg.get("message_id")
    md = market.data()
    if action in ("d", "s"):
        try:
            if action == "d":
                await client.edit_message_text(chat_id, mid, details(c), html=True, buttons=buttons(c, "details"))
            else:
                await client.edit_message_text(chat_id, mid, text(db, c), html=True, buttons=buttons(c))
        except Exception as e:  # noqa: BLE001 - "message is not modified" and the like
            logger.info("signal card view not changed (%s)", str(e)[:120])
        await _answer(client, cq, uid, "", dm=False)
        return
    if action == "r":
        if _too_soon(c.id, "r", REFRESH_EVERY):
            await _answer(client, cq, uid, "Just refreshed. Try again in a few seconds.", dm=False)
            return
        try:
            listing = await _listing(c, md)
            new, _ = await build(db, md, listing, c.timeframe, c.chat_type)
        except Exception as e:  # noqa: BLE001
            logger.info("signal card refresh failed (%s)", type(e).__name__)
            await _answer(client, cq, uid, "Couldn't read the market right now. Try again in a moment.", dm=False)
            return
        try:
            await client.edit_message_text(chat_id, mid, text(db, new), html=True, buttons=buttons(new))
        except Exception as e:  # noqa: BLE001
            logger.info("signal card refresh not shown (%s)", str(e)[:120])
        await _answer(client, cq, uid, f"Updated: {new.verdict} at {f(new.price)}", dm=False)
        return
    if action == "c":
        if _too_soon(c.id, "c", CHART_EVERY):
            await _answer(client, cq, uid, "The chart for this card was just posted above.", dm=False)
            return
        await _answer(client, cq, uid, "Drawing the chart…", dm=False)
        try:
            if c.venue == "hyperliquid":
                candles = await asyncio.to_thread(md.candles, c.coin, c.timeframe, 200)
            else:
                from app.tradingos import venues

                candles = await asyncio.to_thread(venues.candles, await _listing(c, md), c.timeframe, 200, md, venues.client())
            png = await asyncio.to_thread(chart_png, c, candles)
        except Exception as e:  # noqa: BLE001
            logger.info("signal card chart failed (%s)", type(e).__name__)
            _recent.pop((c.id, "c"), None)
            return
        what = "entry, SL, TP1, TP2" if c.plan else "the price levels that would make it a setup"
        await client.send_photo(chat_id, png, f"{esc(c.tag)} · {c.timeframe} · {c.verdict} · {what}, support and resistance from "
                                f"{esc((c.data or {}).get('label') or c.venue)} candles", reply_to=mid, html=True)
        return
    if action == "a":
        await _alert(db, client, cq, uid, c, md)
        return
    if action == "t":
        await _trade(db, client, cq, uid, c)
        return


async def _alert(db: Session, client: Any, cq: dict[str, Any], uid: int, c: TsSignalCard, md: Any) -> None:
    """A setup: the entry-zone alert (alerts.py). A WAIT: a message when this market's next card on this timeframe is LONG or SHORT."""
    from app.tradingos import alerts
    from app.tradingos.bot import _answer

    if c.plan:
        p = c.plan
        btn = alerts.button(c.coin, PlanView(p["side"], (p["lo"], p["hi"]), p["stop"], p["tp1"], p["tp2"]), c.timeframe)
        if btn:
            await alerts.callback(db, client, cq, uid, btn[0][0]["callback_data"])
            return
    same = db.scalar(select(TsPriceAlert).where(TsPriceAlert.telegram_user_id == uid, TsPriceAlert.status == "active",
                                                TsPriceAlert.kind == "setup", TsPriceAlert.coin == c.coin.upper()))
    if same is not None and (same.plan or {}).get("tf") == c.timeframe:
        await _answer(client, cq, uid, f"Already watching {c.tag} {c.timeframe} for you (alert #{same.id}).", dm=False)
        return
    if not alerts._room(db, uid):
        await _answer(client, cq, uid, f"You have {alerts.MAX_ACTIVE} active alerts (the most). Remove one first: /alerts", alert=True)
        return
    a = alerts.create(db, uid, c.coin.upper(), "setup", c.price, c.price, None, {"tf": c.timeframe, "card": c.id, "tag": c.tag, "checked": 0})
    a.expires_at = utcnow() + SETUP_ALERT_TTL
    db.commit()
    try:
        await client.send_message(uid, f"🔔 Alert #{a.id}: I'll message you when {esc(c.tag)} on {c.timeframe} becomes a valid LONG or SHORT "
                                       f"setup (checked on every closed {c.timeframe} candle, for 7 days).\n/alerts lists yours.", html=True)
    except Exception:  # noqa: BLE001 - never pressed Start: no private alert possible
        from app.telegram.ingestion import get_bot_state

        db.delete(a)
        db.commit()
        name = get_bot_state(db).bot_username or "cryptopricechatbot"
        await _answer(client, cq, uid, f"Open t.me/{name} and press Start, then tap Alert Me again: alerts come privately.", alert=True, dm=False)
        return
    await _answer(client, cq, uid, f"Watching {c.tag} {c.timeframe} for a valid setup. Details sent privately.", dm=False)


def trade_args(c: TsSignalCard, max_loss: float | None = None) -> list[str]:
    """The card's plan as tgtrade's /open: the lower leverage, its stop and TP1 (TP2 follows from execution), and the far edge
    of the entry zone as the worst fill accepted (outside the zone the preview refuses). With the member's max loss per trade,
    the margin is sized so the stop (with fees and slippage, filled at that worst price) risks at most 95% of it; the preview
    still applies every one of their limits."""
    p = c.plan or {}
    long = p["side"] == "LONG"
    lev = int(p["lev"][0])
    worst = p["hi"] if long else p["lo"]
    g = lambda x: format(x, ".8g")  # noqa: E731
    size = []
    if max_loss:
        per_unit = abs(worst - p["stop"]) + worst * (2 * risk.TAKER_FEE + 2 * risk.SLIPPAGE)
        margin = int(0.95 * max_loss / per_unit * worst / lev * 100) / 100
        size = [f"{margin:.2f}"] if margin > 0 else []
    return [c.coin, "long" if long else "short", *size, f"{lev}x", "sl", g(p["stop"]), "tp", g(p["tp1"]), "limit", g(worst)]


async def _trade(db: Session, client: Any, cq: dict[str, Any], uid: int, c: TsSignalCard) -> None:
    """Trade: tgtrade's own /open preview, privately, for whoever pressed it. Nothing is placed without their Confirm."""
    from app.telegram.ingestion import get_bot_state
    from app.tradingos import tgtrade
    from app.tradingos.bot import SITE, _answer, has_access, member_for

    if not tradable(c):
        await _answer(client, cq, uid, "This setup's entry window has passed. Tap Refresh for the current read.", alert=True, dm=False)
        return
    m = member_for(db, uid)
    if m is None or not has_access(db, m):
        await _answer(client, cq, uid, f"Trading works for linked Kiwi Passports: {SITE}/passport → Connect Telegram.", alert=True, dm=False)
        return
    try:
        from app.tradingos import execution

        acc = execution.account(db, m)
        reply = await tgtrade.command(db, client, uid, m, "/open", trade_args(c, acc.max_loss_per_trade_usd))
        if reply:
            await client.send_message(uid, reply, html=True)
    except Exception as e:  # noqa: BLE001 - the member never pressed Start (or Telegram is down)
        logger.info("signal card trade preview not sent (%s)", type(e).__name__)
        name = get_bot_state(db).bot_username or "cryptopricechatbot"
        await _answer(client, cq, uid, f"Open t.me/{name} and press Start first: trade previews are private.", alert=True, dm=False)
        return
    await _answer(client, cq, uid, "Sent you a private preview. Nothing is placed until you Confirm.", dm=False)


# ---------------------------------------------------------------- setup alerts (alerts.check)
def _last_closed(tf: str, now: datetime) -> int:
    span = INTERVALS[tf]
    return int(now.timestamp() // span * span - span) * 1000


async def check_setups(db: Session, client: Any, md: Any, rows: list[TsPriceAlert], now: datetime) -> dict[str, int]:
    """Setup alerts: one read per market and timeframe once a new candle has closed; a LONG or SHORT card goes to everyone
    watching it, with its buttons, and their alert is done."""
    from app.telegram.errors import TelegramRequestError
    from app.tradingos import tickers, venues

    out = {"checked": 0, "fired": 0}
    groups: dict[tuple[str, str], list[TsPriceAlert]] = {}
    for a in rows:
        tf = (a.plan or {}).get("tf", "1h")
        if (a.plan or {}).get("checked", 0) < _last_closed(tf, now):
            groups.setdefault((a.coin, tf), []).append(a)
    for (coin, tf), alerts_ in list(groups.items())[:SETUP_CHECKS_PER_RUN]:
        try:
            listing = await asyncio.to_thread(venues.resolve, tickers.symbol_args([coin])[0], md, venues.client())
            card, _ = await build(db, md, listing, tf, "alert")
        except Exception as e:  # noqa: BLE001 - retried on the next run
            logger.info("setup alert: %s %s not read (%s)", coin, tf, type(e).__name__)
            continue
        out["checked"] += 1
        for a in alerts_:
            a.plan = {**(a.plan or {}), "checked": _last_closed(tf, now)}
            if card.verdict == "WAIT":
                continue
            try:
                await client.send_message(a.telegram_user_id, text(db, card, f"🔔 <b>Alert #{a.id}: {esc(card.tag)} {tf} is now a valid {card.verdict} setup</b>"),
                                          buttons(card), html=True)
            except TelegramRequestError:
                a.status = "blocked"
                continue
            except Exception as e:  # noqa: BLE001 - Telegram down: tried again next candle
                logger.warning("setup alert %s not sent (%s)", a.id, type(e).__name__)
                continue
            a.status, a.fired_at, a.fired_price = "fired", now, card.price
            out["fired"] += 1
        db.commit()
    return out
