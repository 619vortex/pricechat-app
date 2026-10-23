"""Sentiment section: Hyperliquid funding (latest hourly rate, 24 h and 7 d averages from fundingHistory), open interest
now and its 24 h change (Price Chat's own 15-minute snapshots, oi.py), taker flow over the last hour (the Hyperliquid
trade tape, hltape.py, when it runs in this process), the Fear & Greed index (alternative.me, daily history) and community
mentions (our Telegram and X ingest tables: mentions in the 24 h before as_of vs the daily average of the 7 days before).

Point-in-time: funding, open-interest snapshots and Fear & Greed are filtered to timestamps <= as_of, so they work in
backtests; the live trade tape and community counts are "unavailable" there.

Lean (contrarian, deterministic): 24 h average funding above +0.01 %/h (longs crowded) -1, below -0.01 %/h +1; Fear & Greed
<= 20 (extreme fear) +1, >= 80 (extreme greed) -1. score = the sum clamped to -2..2; bullish > 0, bearish < 0, neutral 0,
unknown when neither input is available. Taker flow and mentions are reported but don't vote (noisy)."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.committee.evidence.common import FetchError, aware, cache_get, cache_put, get_json, iso, mark, post_json, rnd
from app.tradingos.market import API as HL_API

FNG = "https://api.alternative.me/fng/"
CROWDED = 0.0001  # per hour (0.01 %/h)


async def funding(http: httpx.AsyncClient, coin: str, as_of: datetime) -> dict[str, Any]:
    """{"funding_1h_pct", "funding_24h_avg_pct", "funding_7d_avg_pct", "ts", "rows"} from fundingHistory, rows <= as_of."""
    end = int(as_of.timestamp() * 1000)
    start = end - 7 * 86_400_000
    key = f"hlfund:{coin}:{end // 300_000}"
    rows = cache_get(key, 300.0)
    if rows is None:
        out = await post_json(http, HL_API, {"type": "fundingHistory", "coin": coin, "startTime": start, "endTime": end},
                              what="Hyperliquid fundingHistory")
        if not isinstance(out, list):
            raise FetchError("Hyperliquid sent an unexpected funding answer")
        rows = cache_put(key, [(int(r["time"]), float(r["fundingRate"])) for r in out if "time" in r and "fundingRate" in r])
    rows = sorted((t, f) for t, f in rows if start <= t <= end)
    if not rows:
        raise FetchError(f"no Hyperliquid funding rows for {coin} in the 7 days before as_of")
    day = [f for t, f in rows if t > end - 86_400_000]
    return {"funding_1h_pct": rnd(rows[-1][1] * 100, 4), "funding_24h_avg_pct": rnd(sum(day) / len(day) * 100, 4) if day else None,
            "funding_7d_avg_pct": rnd(sum(f for _, f in rows) / len(rows) * 100, 4), "ts": iso(rows[-1][0] / 1000), "rows": len(rows),
            "covers_7d": rows[0][0] <= start + 3_600_000 * 2}


async def fear_greed(http: httpx.AsyncClient, as_of: datetime) -> dict[str, Any]:
    """The latest daily Fear & Greed value published at or before as_of (and the one 7 days earlier, when known)."""
    days_back = (datetime.now(timezone.utc) - as_of).days
    limit = 30 if days_back < 25 else 0  # 0 = the whole history
    key = f"fng:{limit}"
    body = cache_get(key, 600.0)
    if body is None:
        try:
            body = cache_put(key, await get_json(http, FNG, what="alternative.me Fear & Greed", params={"limit": limit}))
        except FetchError as e:
            mark("fear_greed", False, str(e))
            raise
    rows = []
    for r in (body or {}).get("data") or []:
        try:
            rows.append((int(r["timestamp"]), int(r["value"]), str(r.get("value_classification") or "")))
        except (KeyError, TypeError, ValueError):
            continue
    mark("fear_greed", bool(rows), f"{len(rows)} daily values")
    cut = as_of.timestamp()
    past = sorted(r for r in rows if r[0] <= cut)
    if not past:
        raise FetchError("no Fear & Greed value at or before as_of")
    t, v, label = past[-1]
    if cut - t > 2 * 86_400:
        raise FetchError(f"latest Fear & Greed value before as_of is {(cut - t) / 86_400:.0f} days old")
    week = [r for r in past if r[0] <= t - 7 * 86_400 + 3600]
    return {"value": v, "label": label, "ts": iso(t), "value_7d_ago": week[-1][1] if week else None}


def open_interest(db: Any, coin: str, as_of: datetime) -> dict[str, Any] | None:
    """OI now (the last snapshot within 1 h before as_of) and its change over 24 h, from Price Chat's own snapshots."""
    from app.tradingos import oi

    h = [(t, v) for t, v in oi.history(db, coin, as_of - timedelta(hours=30)) if t <= as_of.timestamp()]
    if not h or as_of.timestamp() - h[-1][0] > 3600:
        return None
    t_now, now_v = h[-1]
    then = [(t, v) for t, v in h if t <= t_now - 86_400 + 1800]
    chg = None
    if then and then[-1][1] and t_now - then[-1][0] <= 86_400 + 6 * 3600:
        chg = (now_v - then[-1][1]) / then[-1][1] * 100
    return {"oi_usd": rnd(now_v), "oi_change_24h_pct": round(chg, 2) if chg is not None else None, "ts": iso(t_now)}


def taker_flow(coin: str) -> dict[str, Any] | None:
    """Aggressor buy / sell USD over the tape's window (≤ 1 h) when the Hyperliquid trade tape runs in this process."""
    from app.tradingos import hltape

    trades, since = hltape.tape().get(coin)
    if since is None or not trades:
        return None
    buy = sum(px * sz for _, px, sz, side, _ in trades if side == "buy")
    sell = sum(px * sz for _, px, sz, side, _ in trades if side == "sell")
    mins = (time.time() - max(since, trades[0][0] / 1000)) / 60
    return {"buy_usd": rnd(buy), "sell_usd": rnd(sell), "buy_share_pct": round(buy / (buy + sell) * 100, 1) if buy + sell else None,
            "window_min": round(min(mins, 60), 1), "trades": len(trades)}


def _mention_re(symbol: str, name: str | None) -> re.Pattern[str]:
    alts = [rf"(?<![A-Za-z0-9])\$?{re.escape(symbol.upper())}(?![A-Za-z0-9])"]
    if name and len(name) >= 4 and name.upper() != symbol.upper():
        alts.append(rf"(?i:\b{re.escape(name)}\b)")
    return re.compile("|".join(alts))


def community(db: Any, symbol: str, name: str | None, as_of: datetime) -> dict[str, Any] | None:
    """{"mentions_24h", "mentions_7d_avg", "sources"} from telegram_messages + social_posts, or None when both are empty
    over the 8 days before as_of. Candidates are pre-filtered in SQL (LIKE), then matched as whole words."""
    from sqlalchemy import func, or_, select

    from app.models.social import SocialPost
    from app.models.telegram import TelegramMessage

    rx = _mention_re(symbol, name)
    lo = as_of - timedelta(days=8)
    words = [symbol.lower()] + ([name.lower()] if name and len(name) >= 4 else [])
    counts = {"24h": 0, "7d": 0}
    total = 0
    for model, at in ((TelegramMessage, TelegramMessage.sent_at), (SocialPost, SocialPost.posted_at)):
        total += db.scalar(select(func.count()).select_from(model).where(at > lo, at <= as_of)) or 0
        rows = db.execute(select(at, model.text).where(at > lo, at <= as_of, or_(*(func.lower(model.text).like(f"%{w}%") for w in words)))
                          .limit(50_000)).all()
        for when, text in rows:
            if not when or not rx.search(text or ""):
                continue
            age = as_of - aware(when)
            if age <= timedelta(hours=24):
                counts["24h"] += 1
            elif age <= timedelta(days=8):
                counts["7d"] += 1
    if total == 0:
        return None
    return {"mentions_24h": counts["24h"], "mentions_7d_avg": round(counts["7d"] / 7, 1), "messages_scanned_8d": int(total),
            "sources": ["telegram_messages", "social_posts"]}


def lean(f: dict[str, Any]) -> tuple[str, int | None, list[str]]:
    pts, why, votes = 0, [], 0
    fund = f.get("funding_24h_avg_pct")
    if fund is not None:
        votes += 1
        if fund > CROWDED * 100:
            pts -= 1
            why.append(f"24h average funding {fund:+g}%/h: longs crowded (contrarian bearish)")
        elif fund < -CROWDED * 100:
            pts += 1
            why.append(f"24h average funding {fund:+g}%/h: shorts crowded (contrarian bullish)")
        else:
            why.append(f"24h average funding {fund:+g}%/h: not extreme")
    fg = f.get("fear_greed")
    if fg:
        votes += 1
        if fg["value"] <= 20:
            pts += 1
            why.append(f"Fear & Greed {fg['value']} ({fg['label']}): extreme fear (contrarian bullish)")
        elif fg["value"] >= 80:
            pts -= 1
            why.append(f"Fear & Greed {fg['value']} ({fg['label']}): extreme greed (contrarian bearish)")
        else:
            why.append(f"Fear & Greed {fg['value']} ({fg['label']}): not extreme")
    if not votes:
        return "unknown", None, why
    score = max(-2, min(2, pts))
    return ("bullish" if score > 0 else "bearish" if score < 0 else "neutral"), score, why
