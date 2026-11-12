"""Price alerts in the Telegram bot: /alert BTC 85000 (direction from the price now), /alert BTC above 85000, /alert ETH -5%,
/alerts, /alert off 3 | all (/unalert 3), and the "🔔 Alert me at entry" button under /signal (fires when the price enters
the plan's entry zone). Hyperliquid perps only. The tradingos.alerts job checks every active alert against one markets
read each minute; an alert fires once, by private message, only on a crossing (from the last check's mark)."""

import asyncio
import logging
import re
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingos import TsPriceAlert
from app.tradingos import market, tickers

logger = logging.getLogger(__name__)

COMMANDS = {"/alert", "/alerts", "/unalert"}
MAX_ACTIVE = 10
TTL = timedelta(days=30)
USAGE = ("Usage: /alert BTC 85000 · /alert BTC above 85000 · /alert ETH -5%\n"
         "/alerts lists yours · /alert off 3 · /alert off all")
UP, DOWN = {"ABOVE", "OVER", ">", "UP"}, {"BELOW", "UNDER", "<", "DOWN"}
_PCT = re.compile(r"^([+-])?(\d+(?:\.\d+)?)%$")
_PX = re.compile(r"^\$?(\d+(?:\.\d+)?)([KM])?$")


def _fmt(p: float) -> str:
    s = market.fmt_price(p)
    return s[:-2] if s.endswith(".0") else s  # 85,000 not 85,000.0


def parse_price(word: str) -> float | None:
    """85000 · 85,000 · $85000 · 85k · 1.2m -> the price; None otherwise."""
    m = _PX.match(word.upper().replace(",", ""))
    if not m:
        return None
    v = float(m.group(1)) * {"K": 1e3, "M": 1e6, None: 1}[m.group(2)]
    return v if v > 0 else None


def _coin(word: str, marks: dict[str, Any]) -> str | None:
    """A typed market -> its Hyperliquid perp name, or None (not a market / not on Hyperliquid / spot)."""
    from app.tradingos.bot import coin_name

    s = tickers.parse_symbol(word)
    if s is None or s.address or s.kind == "spot" or (s.venue and s.venue != "hyperliquid"):
        return None
    c = coin_name(s.base)
    return c if c in marks and marks[c].mark > 0 else None


def _active(db: Session, uid: int) -> list[TsPriceAlert]:
    return list(db.scalars(select(TsPriceAlert).where(TsPriceAlert.telegram_user_id == uid, TsPriceAlert.status == "active")
                           .order_by(TsPriceAlert.id)))


def _describe(a: TsPriceAlert) -> str:
    if a.kind == "setup":  # a /signal WAIT card's Alert Me: fires when that market's card turns LONG or SHORT
        return f"{(a.plan or {}).get('tag') or a.coin} {(a.plan or {}).get('tf', '1h')} becomes a valid setup"
    if a.kind == "zone":
        side = (a.plan or {}).get("side", "")
        return f"{a.coin} {side} entry zone {_fmt(a.price)}–{_fmt(a.price_hi or a.price)}".replace("  ", " ")
    return f"{a.coin} {a.kind} {_fmt(a.price)}"


def _room(db: Session, uid: int) -> bool:
    n = db.scalar(select(func.count()).select_from(TsPriceAlert).where(TsPriceAlert.telegram_user_id == uid, TsPriceAlert.status == "active"))
    return (n or 0) < MAX_ACTIVE


def listing(db: Session, uid: int) -> str:
    rows = _active(db, uid)
    if not rows:
        return "No active alerts. " + USAGE
    return (f"<b>Your alerts</b> ({len(rows)}/{MAX_ACTIVE})\n" + "\n".join(f"#{a.id} {_describe(a)}" for a in rows)
            + "\n\nRemove: /alert off 3 · /alert off all")


def remove(db: Session, uid: int, words: list[str]) -> str:
    if not words:
        return "Which one? /alerts lists them with their numbers: /alert off 3 · /alert off all"
    rows = _active(db, uid)
    if words[0] == "ALL":
        for a in rows:
            a.status = "off"
        db.commit()
        return f"Removed {len(rows)} alert{'s' * (len(rows) != 1)}." if rows else "No active alerts."
    ids = {int(w.lstrip("#")) for w in words if w.lstrip("#").isdigit()}
    hit = [a for a in rows if a.id in ids]  # only this user's own alerts
    if not hit:
        return "No such alert of yours. /alerts lists them."
    for a in hit:
        a.status = "off"
    db.commit()
    return "Removed " + ", ".join(f"#{a.id} {_describe(a)}" for a in hit) + "."


def create(db: Session, uid: int, coin: str, kind: str, price: float, now_px: float, price_hi: float | None = None,
           plan: dict[str, Any] | None = None) -> TsPriceAlert:
    a = TsPriceAlert(telegram_user_id=uid, coin=coin, kind=kind, price=price, price_hi=price_hi, last_price=now_px, plan=plan,
                     status="active", expires_at=utcnow() + TTL)
    db.add(a)
    db.commit()
    return a


async def command(db: Session, uid: int, cmd: str, args: list[str], md: Any) -> str:
    """/alert, /alerts, /unalert (private chat). Args come upper-cased."""
    if cmd == "/alerts" or (cmd == "/alert" and args[:1] == ["LIST"]):
        return listing(db, uid)
    if cmd == "/unalert":
        return remove(db, uid, args)
    if args[:1] in (["OFF"], ["DEL"], ["DELETE"], ["REMOVE"], ["STOP"]):
        return remove(db, uid, args[1:])
    if len(args) < 2:
        return USAGE
    _, marks = await asyncio.to_thread(md.markets)
    coin = _coin(args[0], marks)
    q, args = tickers.qualifiers(args)  # "binance" / "spot" -> not a Hyperliquid perp; "hl" / "perp" are fine
    if coin is None or (q.get("venue") or "hyperliquid") != "hyperliquid" or q.get("kind") == "spot":
        return f"Alerts are for Hyperliquid perps: {args[0].lstrip('$')} isn't one. Try /alert BTC 85000."
    if not _room(db, uid):
        return f"You have {MAX_ACTIVE} active alerts (the most). Remove one first: /alerts"
    now_px = marks[coin].mark
    rest = args[1:]
    if not rest:  # "/alert BTC perp": only the market was left
        return USAGE
    want = "above" if rest[0] in UP else "below" if rest[0] in DOWN else None
    if want:
        rest = rest[1:]
    if len(rest) != 1:
        return USAGE
    pct = _PCT.match(rest[0])
    if pct:
        if not pct.group(1) or want:
            return "Give the move with its sign: /alert ETH +5% or /alert ETH -5%"
        move = float(pct.group(2)) / 100
        if not 0 < move < (1 if pct.group(1) == "-" else 10):
            return "That move is out of range."
        want = "above" if pct.group(1) == "+" else "below"
        price = now_px * (1 + move if want == "above" else 1 - move)
    else:
        price = parse_price(rest[0])
        if price is None:
            return USAGE
    kind = want or ("above" if price >= now_px else "below")
    a = create(db, uid, coin, kind, price, now_px)
    note = ""
    if (kind == "above") == (now_px >= price):  # set the "wrong" way round: it waits for the next crossing
        note = f"\nIt's already {kind} now, so this fires on the next cross {kind}."
    return f"🔔 Alert #{a.id}: {coin} {kind} {_fmt(price)} (now {_fmt(now_px)}). I'll message you here once.{note}"


# ---------------------------------------------------------------- the /signal button
def _c(x: float, p: int) -> str:
    return f"{x:.{p}g}".replace("e+0", "e").replace("e+", "e").replace("e-0", "e-")


def button(coin: str, plan: Any, tf: str) -> list[list[dict[str, str]]] | None:
    """"🔔 Alert me at entry" with the whole plan in the callback data (Telegram allows 64 bytes): pa:COIN:L1h:lo:hi:stop:target."""
    for p in (6, 5, 4):
        data = ":".join(["pa", coin, plan.side[0] + tf, *(_c(x, p) for x in (*plan.entry, plan.stop, plan.target))])
        if len(data.encode()) <= 64:
            return [[{"text": "🔔 Alert me at entry", "callback_data": data}]]
    return None


def parse_button(data: str) -> tuple[str, dict[str, Any], float, float] | None:
    try:
        _, coin, st, lo, hi, stop, target = data.split(":")
        side = {"L": "LONG", "S": "SHORT"}[st[0]]
        lo_, hi_ = sorted((float(lo), float(hi)))
        return coin, {"side": side, "tf": st[1:] or "1h", "stop": float(stop), "target": float(target)}, lo_, hi_
    except (ValueError, KeyError, IndexError):
        return None


async def callback(db: Session, client: Any, cq: dict[str, Any], uid: int, data: str) -> None:
    """The button: a zone alert for whoever pressed it, confirmed privately (no Start in the bot yet: nothing is set)."""
    from app.tradingos.bot import _answer, esc

    got = parse_button(data)
    if got is None:
        await _answer(client, cq, uid, "This button isn't valid any more.", dm=False)
        return
    coin, plan, lo, hi = got
    same = next((a for a in _active(db, uid) if a.kind == "zone" and a.coin == coin.upper() and (a.price, a.price_hi) == (lo, hi)), None)
    if same is not None:  # a double tap: one alert, not two messages when it fires
        await _answer(client, cq, uid, f"Already set: alert #{same.id}, {coin} entry zone. /alerts lists yours.", dm=False)
        return
    if not _room(db, uid):
        await _answer(client, cq, uid, f"You have {MAX_ACTIVE} active alerts (the most). Remove one first: /alerts", alert=True)
        return
    try:
        _, marks = await asyncio.to_thread(market.data().markets)
        now_px = marks[coin.upper()].mark
    except (market.MarketError, KeyError):
        await _answer(client, cq, uid, "Couldn't read Hyperliquid right now. Try again in a moment.", alert=True)
        return
    a = create(db, uid, coin.upper(), "zone", lo, now_px, hi, plan)
    inside = lo <= now_px <= hi
    text = (f"🔔 Alert #{a.id}: I'll message you when {esc(coin)} enters the {plan['side']} entry zone {_fmt(lo)}–{_fmt(hi)}"
            f" (now {_fmt(now_px)}{', inside it: fires when it comes back' if inside else ''}).\n{_plan_line(plan, lo, hi)}")
    try:
        await client.send_message(uid, text, html=True)
    except Exception as e:  # noqa: BLE001 - no private message, so no alert
        from app.telegram.errors import TelegramRequestError
        from app.telegram.ingestion import get_bot_state

        db.delete(a)
        db.commit()
        if isinstance(e, TelegramRequestError):  # they never pressed Start (or blocked the bot)
            name = get_bot_state(db).bot_username or "cryptopricechatbot"
            note = f"Open t.me/{name} and press Start, then tap the button again: alerts come privately."
        else:  # Telegram busy or down: nothing wrong on their side
            note = "Couldn't set the alert right now. Tap the button again in a moment."
        await _answer(client, cq, uid, note, alert=True, dm=False)
        return
    await _answer(client, cq, uid, f"Alert set: {coin} entry zone. Details sent privately.", dm=False)


def _plan_line(plan: dict[str, Any], lo: float, hi: float) -> str:
    return f"Plan: {plan.get('side', '')} entry {_fmt(lo)}–{_fmt(hi)} · stop {_fmt(plan['stop'])} · target {_fmt(plan['target'])}"


# ---------------------------------------------------------------- the job
def _side(px: float, lo: float, hi: float) -> int:
    return -1 if px < lo else 1 if px > hi else 0


def crossed(a: TsPriceAlert, px: float) -> bool:
    """Fires only on a crossing since the last check: above (last < level <= now), below (last > level >= now), zone
    (now inside from outside, or jumped clean over it between two checks)."""
    last = a.last_price
    if a.kind == "above":
        return last < a.price <= px
    if a.kind == "below":
        return last > a.price >= px
    lo, hi = a.price, a.price_hi or a.price
    s0, s1 = _side(last, lo, hi), _side(px, lo, hi)
    return s0 != 0 and (s1 == 0 or s0 == -s1)


def message(a: TsPriceAlert, px: float) -> str:
    tf = (a.plan or {}).get("tf", "1h")
    chart = f"/chart {a.coin} {tf}"
    if a.kind == "zone":
        return (f"🔔 {a.coin} entered the {(a.plan or {}).get('side', '')} entry zone {_fmt(a.price)}–{_fmt(a.price_hi or a.price)}"
                f" (now {_fmt(px)})\n{_plan_line(a.plan or {}, a.price, a.price_hi or a.price)}\n{chart} · Not financial advice.")
    return f"🔔 {a.coin} crossed {a.kind} {_fmt(a.price)} (now {_fmt(px)}) · {chart}"


async def check(db: Session, client: Any, md: Any, now: Any = None) -> dict[str, int]:
    """tradingos.alerts: expire old alerts, fire the crossed ones (once), remember each mark for the next crossing. Each fired
    alert is committed as it's sent, so a run cut short (timeout) never sends it again."""
    from app.telegram.errors import TelegramRequestError

    now = now or utcnow()
    rows = list(db.scalars(select(TsPriceAlert).where(TsPriceAlert.status == "active")))
    out = {"active": 0, "fired": 0, "expired": 0, "blocked": 0}
    if not rows:
        return out
    _, marks = await asyncio.to_thread(md.markets)  # one read for every alert
    telegram_ok = True
    setups: list[TsPriceAlert] = []
    for a in rows:
        if a.expires_at <= now:
            a.status = "expired"
            out["expired"] += 1
            continue
        if a.kind == "setup":  # no price crossing: the market's card is re-read on each closed candle (signalcard.check_setups)
            setups.append(a)
            continue
        m = marks.get(a.coin.upper())
        if m is None or m.mark <= 0:
            continue
        px = m.mark
        if crossed(a, px):
            if not telegram_ok:
                continue  # retried next run (last mark kept)
            try:
                await client.send_message(a.telegram_user_id, message(a, px), html=True)
            except TelegramRequestError as e:  # blocked the bot / never pressed Start: disable it
                logger.info("Price alert %s undeliverable: %s", a.id, str(e)[:120])
                a.status = "blocked"
                out["blocked"] += 1
                continue
            except Exception as e:  # noqa: BLE001 - Telegram down / rate-limited: the rest wait for the next run
                logger.warning("Price alert %s not sent (%s)", a.id, type(e).__name__)
                telegram_ok = False
                continue
            a.status, a.fired_at, a.fired_price = "fired", now, px
            out["fired"] += 1
            db.commit()
        a.last_price = px
    db.commit()
    if setups and telegram_ok:
        from app.tradingos import signalcard

        got = await signalcard.check_setups(db, client, md, setups, now)
        out["fired"] += got["fired"]
        out["setups_checked"] = got["checked"]
    out["active"] = len(rows) - out["fired"] - out["expired"] - out["blocked"]
    return out
