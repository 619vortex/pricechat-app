"""Kiwi Risk Team: the desk that reviews every kitchen setup before it becomes a signal, and an AI Chief Risk Officer that
explains each decision in plain English.

The DECISIONS are made by five rule-based desks (deterministic, tested, same inputs → same answer). Each desk can only
VETO a setup or WARN about it: the team never adds a trade, moves a stop or raises a size. A language model never decides
anything here; it only writes the memo, from the desks' own numbers, and a memo whose numbers aren't in the evidence is
replaced by the rules' own summary.

  exposure  correlation: crypto moves together, so a second open signal on the SAME side (two longs) is one doubled bet.
            Veto while a kitchen signal on that side is open or awaiting approval.
  streak    after `streak` losing kitchen signals in a row, a pause of `pause_h` hours from the last close
  daily     the kitchen's closed signals today (UTC) at or below `day_stop_r` R: no new signals until tomorrow
  shock     the setup candle's range is more than `shock_atr` ATRs: a news / liquidation candle, not a setup
  edge      never vetoes: says plainly whether the strategy has a proven edge (the 2-year backtest below, and the live
            record of the last 30 closed kitchen signals)

Research behind it (2026-10-18, 2 years of Binance 15m history, kitchen.backtest(source="binance"), portfolio rules of
backtest.py): every kitchen configuration lost after costs. The setups average about 0.00R BEFORE costs and costs are
about 0.12R a trade, so no stop / target tweak fixes it. The desk rules were tested there too (adopt only if better on
both the first 60% and the last 40%): none improved the result per trade; they cut the TOTAL loss and the drawdown by
trading less (same-side guard: max drawdown 147% → 129% of the 0.5%-risk equity curve). So the team is a brake, not an
edge, and the edge desk says so on every review.

Owner settings (TsSettings.strategies["risk_desk"]): {"enabled": true, "same_side": true, "streak": 4, "pause_h": 6,
"day_stop_r": -3, "shock_atr": 3.0}. "enabled": false skips the team (the kitchen's own checks still run).
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingos import KbEvent, TsKitchen, TsSignal

STRATEGY = "intraday_15m_v1"  # kitchen.STRATEGY (kitchen imports this module)
DESKS = (("exposure", "Correlation desk"), ("streak", "Loss-streak desk"), ("daily", "Daily-loss desk"), ("shock", "Volatility desk"),
         ("edge", "Edge desk"))
DEFAULTS: dict[str, Any] = {"enabled": True, "same_side": True, "streak": 4, "pause_h": 6, "day_stop_r": -3.0, "shock_atr": 3.0}
BACKTEST = {"period": "2024-10 to 2026-09", "source": "Binance perps, 15m", "degen_r": -0.121, "degen_trades_per_day": 3.25,
            "standard_r": -0.104, "gross_r": 0.007, "cost_r": 0.128}
OPEN_STATUSES = ("WAITING", "ACTIVE", "PENDING_REVIEW")


def settings(s: Any) -> dict[str, Any]:
    over = (getattr(s, "strategies", None) or {}).get("risk_desk") or {}
    return {**DEFAULTS, **{k: v for k, v in over.items() if k in DEFAULTS}}


def signal_r(sig: Any) -> float | None:
    """A closed signal's result in R: its unlevered % result over its stop distance in %."""
    if sig.model_pnl_pct is None:
        return None
    mid = (sig.entry_lo + sig.entry_hi) / 2
    risk_pct = abs(mid - sig.stop) / mid * 100 if mid else 0
    return round(sig.model_pnl_pct / risk_pct, 3) if risk_pct > 0 else None


@dataclass
class Context:
    """What the desks know about the book at this moment (built from the signal records, or by a backtest)."""
    now: datetime
    open: list[tuple[int, str, str]] = field(default_factory=list)  # (number, coin, side) of open / pending kitchen signals
    closed: list[tuple[datetime, float]] = field(default_factory=list)  # (closed_at, R) of closed kitchen signals, oldest first


def context(db: Session, now: datetime | None = None) -> Context:
    now = now or utcnow()
    opened = db.scalars(select(TsSignal).where(TsSignal.strategy == STRATEGY, TsSignal.status.in_(OPEN_STATUSES)))
    done = list(db.scalars(select(TsSignal).where(TsSignal.strategy == STRATEGY, TsSignal.closed_at.is_not(None),
                                                  TsSignal.model_pnl_pct.is_not(None)).order_by(TsSignal.closed_at.desc()).limit(60)))
    closed = []
    for sg in reversed(done):
        r = signal_r(sg)
        if r is not None and sg.closed_at is not None:
            closed.append((_aware(sg.closed_at), r))
    return Context(now, [(sg.number, sg.coin, sg.side) for sg in opened], closed)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def review(ctx: Context, cfg: dict[str, Any], coin: str, side: str, candle_range: float, atr: float | None) -> dict[str, Any]:
    """Every desk's vote on one setup. verdict: APPROVE (no veto) or VETO (the first veto is the reason)."""
    votes: list[dict[str, Any]] = []

    def vote(desk: str, v: str, detail: str) -> None:
        votes.append({"desk": desk, "label": dict(DESKS)[desk], "vote": v, "detail": detail})

    same = [o for o in ctx.open if o[2] == side and o[1] != coin]
    if cfg["same_side"] and same:
        n, c, _ = same[0]
        vote("exposure", "veto", f"#{n:03d} {c} {side} is already open: crypto moves together, a second {side} doubles the same bet")
    else:
        sides = sorted({o[2] for o in ctx.open})
        vote("exposure", "approve", f"{len(ctx.open)} kitchen signal{'s' if len(ctx.open) != 1 else ''} open" + (f" ({', '.join(sides)})" if sides else ""))
    k, pause = int(cfg["streak"]), timedelta(hours=float(cfg["pause_h"]))
    last = ctx.closed[-k:] if k else []
    if k and len(last) == k and all(r <= 0 for _, r in last) and ctx.now < last[-1][0] + pause:
        left = (last[-1][0] + pause - ctx.now).total_seconds() / 3600
        vote("streak", "veto", f"{k} losing signals in a row ({', '.join(f'{r:+.2f}R' for _, r in last)}): pausing, {left:.1f} h left")
    else:
        run = 0
        for _, r in reversed(ctx.closed):
            if r > 0:
                break
            run += 1
        vote("streak", "approve", f"{run} loss{'es' if run != 1 else ''} in a row (pause after {k})")
    day = ctx.now.astimezone(timezone.utc).date()
    today = [r for t, r in ctx.closed if t.astimezone(timezone.utc).date() == day]
    total = round(sum(today), 2)
    if today and total <= float(cfg["day_stop_r"]):
        vote("daily", "veto", f"today's kitchen signals {total:+.2f}R ({len(today)} closed), at or below the {cfg['day_stop_r']:g}R day stop")
    else:
        vote("daily", "approve", f"today {total:+.2f}R from {len(today)} closed (day stop {cfg['day_stop_r']:g}R)")
    if atr and atr > 0 and candle_range > float(cfg["shock_atr"]) * atr:
        vote("shock", "veto", f"the setup candle spans {candle_range / atr:.1f} ATR (limit {cfg['shock_atr']:g}): a shock candle, not a setup")
    else:
        vote("shock", "approve", f"setup candle {candle_range / atr:.1f} ATR" if atr else "no ATR reading")
    recent = [r for _, r in ctx.closed[-30:]]
    live = f"live last {len(recent)}: {sum(recent) / len(recent):+.2f}R a signal" if recent else "no live record yet"
    vote("edge", "warn", f"no proven edge: 2-year backtest {BACKTEST['degen_r']:+.3f}R a trade after costs ({BACKTEST['period']}); {live}")
    veto = next((v for v in votes if v["vote"] == "veto"), None)
    ok = sum(1 for v in votes if v["vote"] == "approve")
    return {"verdict": "VETO" if veto else "APPROVE", "reason": f"{veto['label']}: {veto['detail']}" if veto else
            f"approved by {ok} of {len(votes)} desks (the edge desk warns: no proven edge)", "votes": votes, "memo": None}


def check(cfg: dict[str, Any], rv: dict[str, Any] | None) -> tuple[str, str]:
    """The kitchen's "risk_team" check."""
    if not cfg.get("enabled", True):
        return "skip", "off (owner setting)"
    if rv is None:
        return "skip", ""
    return ("fail" if rv["verdict"] == "VETO" else "pass"), rv["reason"]


# ---------------------------------------------------------------- the AI Chief Risk Officer (memo only)
MEMO_SYSTEM = """You are the Chief Risk Officer of the Kiwi trading desk. The risk desks have already decided; you explain their
decision to the members in 2 or 3 short sentences of plain English.
Rules: use ONLY the numbers in the evidence, copied exactly. Never predict price, never promise profit, never give advice to buy or sell.
If the edge desk says there is no proven edge, say so plainly. If a desk vetoed, lead with why. Reply as JSON: {"memo": "..."}"""


def evidence(rv: dict[str, Any], coin: str, side: str) -> str:
    lines = [f"Setup: {coin} {side}. Verdict: {rv['verdict']}."]
    lines += [f"- {v['label']}: {v['vote'].upper()} · {v['detail']}" for v in rv["votes"]]
    return "\n".join(lines)


def fallback_memo(rv: dict[str, Any], coin: str, side: str) -> str:
    edge = next((v["detail"] for v in rv["votes"] if v["desk"] == "edge"), "")
    if rv["verdict"] == "VETO":
        return f"The risk team blocked {coin} {side}. {rv['reason']}. {edge[:1].upper() + edge[1:]}."
    return f"The risk team let {coin} {side} through: {rv['reason']}. Reminder: {edge}."


_NUM = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def grounded(memo: str, ev: str) -> bool:
    """Every number in the memo appears in the evidence (a model's invented number is never shown)."""
    have = {x.lstrip("+").replace(",", "") for x in _NUM.findall(ev)}
    return all(x.lstrip("+-").replace(",", "") in {h.lstrip("-") for h in have} for x in _NUM.findall(memo))


async def write_memos(db: Session, router: Any, limit: int = 3) -> dict[str, Any]:
    """The tradingos.riskteam job: memos for the latest reviewed kitchen events that don't have one (newest first). Each is
    published as a kitchen event (the Earn stream and the Kiwi Brain show it) and kept on the market's row."""
    from pydantic import BaseModel

    from app.ai.structured import complete_structured
    from app.tradingos import brain

    class _Memo(BaseModel):
        memo: str

    since = utcnow() - timedelta(hours=6)
    rows = db.scalars(select(KbEvent).where(KbEvent.source == "kitchen", KbEvent.member_id.is_(None), KbEvent.at >= since)
                      .order_by(KbEvent.id.desc()).limit(200))
    todo, seen = [], set()
    for e in rows:  # one memo per reviewed setup (coin + candle): a READY dish, or a setup the team vetoed
        k = (e.data or {}).get("kitchen") or {}
        rv, key = k.get("risk_team"), (e.coin, k.get("candle_t"))
        if k.get("memo_for"):
            seen.add(key)
            continue
        vetoed = any(c.get("id") == "risk_team" and c.get("status") == "fail" for c in k.get("checks") or [])
        if rv and not rv.get("memo") and k.get("setup") and (k.get("state") == "READY" or vetoed) and key not in seen:
            seen.add(key)
            todo.append(e)
    out: dict[str, Any] = {"written": 0, "by_model": 0}
    for e in todo[:limit]:
        k = dict(e.data["kitchen"])
        rv, coin, side = dict(k["risk_team"]), e.coin or k.get("symbol", ""), (k.get("setup") or {}).get("side", "")
        ev = evidence(rv, coin, side)
        memo, by = fallback_memo(rv, coin, side), "rules"
        try:
            res, _, route = await complete_structured(router, "kiwi.risk_memo", MEMO_SYSTEM, ev, _Memo)
            text = res.memo.strip()[:600]
            if text and grounded(text, ev):
                memo, by = text, route.model
                out["by_model"] += 1
        except Exception:  # noqa: BLE001 - no AI model: the rules' own memo
            pass
        rv["memo"] = {"text": memo, "by": by, "at": utcnow().isoformat()}
        row = db.scalar(select(TsKitchen).where(TsKitchen.coin == coin))
        if row is not None and (row.data or {}).get("candle_t") == k.get("candle_t"):
            row.data = {**row.data, "risk_team": rv}
        verdict = "approved" if rv["verdict"] == "APPROVE" else "vetoed"
        brain.log(db, "strategy", f"🛡️ {coin} risk team {verdict} {side}: {memo}"[:240], coin=coin, signal_id=e.signal_id, source="kitchen",
                  data={"kitchen": {**k, "risk_team": rv, "memo_for": e.id}})
        out["written"] += 1
    db.commit()
    return out
