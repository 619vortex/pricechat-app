"""Earn › Funding Arb alerts (job tradingos.funding_alerts, every 10 min). READ-ONLY: posts a notice, never places an order.

Reads the funding board (funding.board, cached), takes each coin's best cross-venue spread (short the highest rate, long the
lowest), subtracts estimated taker fees on both legs (entry + exit, amortized over HOLD_HOURS, like fundarb's signal engine)
and posts to the Trading OS publish chat when the net spread APR is >= the threshold. One coin is posted at most every
COOLDOWN unless its net spread has grown GROWTH (x1.5) since the last post; the last post per coin is kept in the database.
Settings live in TsSettings.strategies["funding_alerts"] = {"on": bool, "min_net_apr": float} (Admin › Trading OS)."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.arbitrage import ArbFundingAlert
from app.tradingos import signals
from fundarb.models import HOURS_PER_YEAR

logger = logging.getLogger(__name__)

DEFAULT_ON = True
DEFAULT_MIN_NET_APR = 30.0  # % a year, after estimated fees
COOLDOWN = timedelta(hours=12)
GROWTH = 1.5  # a cooled-down coin is posted again early when its net spread is 50%+ wider than at the last post
MAX_PER_RUN = 3  # a burst of wide spreads never floods the chat
HOLD_HOURS = 168.0  # round-trip fees spread over a one-week hold (fundarb SignalConfig.expected_hold_hours)
# estimated base-tier taker fees, bps (assumptions: Orderly / Backpack only tell an authenticated account; Lighter's free tier
# is stressed to 2 bps so a zero never flatters the net)
TAKER_BPS = {"hyperliquid": 4.5, "lighter": 2.0, "orderly": 5.0, "backpack": 5.0}
DEFAULT_TAKER_BPS = 5.0
LINK = "https://pricechat.live/earn/funding"
NAMES = {"hyperliquid": "Hyperliquid", "lighter": "Lighter", "orderly": "Orderly", "backpack": "Backpack"}


def config(db: Session) -> dict[str, Any]:
    c = (signals.settings(db).strategies or {}).get("funding_alerts") or {}
    return {"on": bool(c.get("on", DEFAULT_ON)), "min_net_apr": float(c.get("min_net_apr", DEFAULT_MIN_NET_APR))}


def fee_apr(long: str, short: str) -> float:
    """Round-trip taker fees on both legs (2 x (long + short) bps) as an APR over HOLD_HOURS."""
    bps = 2 * (TAKER_BPS.get(long, DEFAULT_TAKER_BPS) + TAKER_BPS.get(short, DEFAULT_TAKER_BPS))
    return bps / 1e4 * (HOURS_PER_YEAR / HOLD_HOURS) * 100


def candidates(board: dict[str, Any], min_net_apr: float) -> list[dict[str, Any]]:
    """Every board row whose net spread APR clears the threshold, widest first."""
    out = []
    for r in board.get("rows") or []:
        legs = {l["venue"]: l for l in r.get("legs") or []}
        lo, hi = legs.get(r.get("long")), legs.get(r.get("short"))
        if not lo or not hi or lo is hi:
            continue
        net = round(float(r["gross_spread_apr_pct"]) - fee_apr(lo["venue"], hi["venue"]), 2)
        if net >= min_net_apr:
            out.append({"symbol": r["symbol"], "long": lo, "short": hi, "gross_apr": float(r["gross_spread_apr_pct"]), "net_apr": net})
    return sorted(out, key=lambda c: -c["net_apr"])


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def is_due(prev: ArbFundingAlert | None, net_apr: float, now: datetime) -> bool:
    if prev is None or now - _aware(prev.sent_at) >= COOLDOWN:
        return True
    return prev.net_apr_pct > 0 and net_apr >= prev.net_apr_pct * GROWTH


def _leg(l: dict[str, Any]) -> str:
    return f"{NAMES.get(l['venue'], l['venue'])} {l['rate_raw'] * 100:+.4f}% / {l['interval_hours']:g}h ({l['apr_pct']:+.1f}% APR)"


def message(c: dict[str, Any]) -> str:
    """Plain text (escaped by the sender for HTML)."""
    return (f"💸 Funding spread: {c['symbol']}\n"
            f"🟢 Long {_leg(c['long'])}\n"
            f"🔴 Short {_leg(c['short'])}\n"
            f"Net spread ≈ {c['net_apr']:.1f}% APR (gross {c['gross_apr']:.1f}%, minus est. taker fees on both legs, in and out, over a 7-day hold)\n"
            f"Rates move every hour: the spread can close before it pays.\n"
            f"{LINK}\n"
            f"Not financial advice.")


def inactive_reason(db: Session) -> str | None:
    """Why the job does nothing right now (checked before reading any venue), or None."""
    if not config(db)["on"]:
        return "Funding alerts are off (Admin › Trading OS)"
    if not (signals.settings(db).publish_chat or "").strip():
        return "No publish chat set (Admin › Trading OS)"
    return None


async def run(db: Session, now: datetime, client: Any, board: dict[str, Any] | None = None) -> dict[str, Any]:
    """Post due alerts to the publish chat. A failed send is logged and not recorded (the next run tries again)."""
    reason = inactive_reason(db)
    if reason:
        return {"skipped": reason, "posted": []}
    from app.arbitrage import funding
    from app.tradingos.bot import esc, with_referral

    cfg, chat = config(db), signals.settings(db).publish_chat.strip()
    data = board if board is not None else await funding.board()
    found, posted = candidates(data, cfg["min_net_apr"]), []
    for c in found:
        if len(posted) >= MAX_PER_RUN:
            break
        prev = db.scalar(select(ArbFundingAlert).where(ArbFundingAlert.symbol == c["symbol"]))
        if not is_due(prev, c["net_apr"], now):
            continue
        try:
            await client.send_message(chat, esc(with_referral(db, message(c))), html=True)
        except Exception as exc:  # noqa: BLE001 - one failed post never blocks the others
            logger.warning("Funding alert for %s not sent: %s", c["symbol"], exc)
            continue
        if prev is None:
            prev = ArbFundingAlert(symbol=c["symbol"], long_venue="", short_venue="", net_apr_pct=0.0, sent_at=now)
            db.add(prev)
        prev.long_venue, prev.short_venue, prev.net_apr_pct, prev.sent_at = c["long"]["venue"], c["short"]["venue"], c["net_apr"], now
        db.commit()  # each post recorded at once: a crash later in the run never re-posts it
        posted.append(f"{c['symbol']} {c['net_apr']:.1f}%")
    return {"above_threshold": len(found), "posted": posted, "min_net_apr": cfg["min_net_apr"]}
