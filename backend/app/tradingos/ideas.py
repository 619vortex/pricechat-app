"""Community trading ideas: "/idea BTC long tp 72k inv 64k breakout retest". A member's own opinion, shown as theirs, kept
apart from Price Chat's signals (no buttons, never traded by the bot, never counted in the signal track record).
The price when it was shared comes from Hyperliquid, so "/ideas" can show how each idea has moved since."""

import re
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingos import TsIdea
from app.tradingos.market import MarketData, fmt_price

USAGE = ("Share a trading idea:\n/idea BTC long tp 72000 inv 64000 your reason\n\n"
         "• long or short (required)\n• tp = target, inv = invalidation (optional; 72k works too)\n• then a short reason (up to 280 characters)\n\n"
         "/ideas shows the latest ideas · /ideas BTC for one coin.\nIdeas are members' opinions, not Price Chat signals, and are never traded.")
PER_DAY = 3
NOTE_MAX = 280
SIDES = {"long": "LONG", "buy": "LONG", "bull": "LONG", "bullish": "LONG", "short": "SHORT", "sell": "SHORT", "bear": "SHORT", "bearish": "SHORT"}
TARGET_WORDS = {"tp", "target", "tgt"}
INVALID_WORDS = {"inv", "invalidation", "invalid", "sl", "stop"}
LINK = re.compile(r"https?://|www\.|t\.me/|@\w{4,}", re.I)


class IdeaError(ValueError):
    """Shown to the member as is."""


@dataclass
class IdeaDraft:
    coin: str
    side: str
    target: float | None
    invalidation: float | None
    note: str


def _num(word: str) -> float | None:
    w = word.lower().replace(",", "").lstrip("$")
    mult = 1.0
    if w.endswith("k"):
        w, mult = w[:-1], 1_000.0
    elif w.endswith("m"):
        w, mult = w[:-1], 1_000_000.0
    try:
        v = float(w) * mult
    except ValueError:
        return None
    return v if v > 0 else None


def parse(words: list[str]) -> IdeaDraft:
    """The original words (case kept for the note). The coin and side can come in either order at the start."""
    if len(words) < 2:
        raise IdeaError(USAGE)
    head = [w.lower() for w in words[:2]]
    if head[0] in SIDES:
        side, coin = SIDES[head[0]], words[1]
    elif head[1] in SIDES:
        side, coin = SIDES[head[1]], words[0]
    else:
        raise IdeaError("Say long or short, e.g. /idea BTC long tp 72000 inv 64000 reason.")
    coin = coin.upper().lstrip("$")
    target = invalidation = None
    rest, i = words[2:], 0
    note: list[str] = []
    while i < len(rest):
        w = rest[i].lower().rstrip(":")
        if w in TARGET_WORDS | INVALID_WORDS and i + 1 < len(rest) and _num(rest[i + 1]) is not None:
            if w in TARGET_WORDS:
                target = _num(rest[i + 1])
            else:
                invalidation = _num(rest[i + 1])
            i += 2
            continue
        note.append(rest[i])
        i += 1
    text = " ".join(note).strip()
    if LINK.search(text):
        raise IdeaError("Please leave links and @mentions out of ideas.")
    if len(text) > NOTE_MAX:
        raise IdeaError(f"Keep the reason under {NOTE_MAX} characters (yours is {len(text)}).")
    return IdeaDraft(coin, side, target, invalidation, text)


def submit(db: Session, md: MarketData, words: list[str], telegram_user_id: int, author: str | None, chat_id: int | None,
           member_id: int | None = None) -> TsIdea:
    d = parse(words)
    m = md.market(d.coin)  # a real Hyperliquid perpetual, and its price now
    px = m.mark
    long = d.side == "LONG"
    if d.target is not None and (d.target <= px if long else d.target >= px):
        raise IdeaError(f"A {d.side.lower()} target should be {'above' if long else 'below'} the price now ({fmt_price(px)}).")
    if d.invalidation is not None and (d.invalidation >= px if long else d.invalidation <= px):
        raise IdeaError(f"A {d.side.lower()} invalidation should be {'below' if long else 'above'} the price now ({fmt_price(px)}).")
    since = utcnow() - timedelta(hours=24)
    n = db.scalar(select(func.count()).select_from(TsIdea).where(TsIdea.telegram_user_id == telegram_user_id, TsIdea.created_at >= since)) or 0
    if n >= PER_DAY:
        raise IdeaError(f"That's {PER_DAY} ideas in 24 hours already: please come back tomorrow.")
    idea = TsIdea(coin=m.coin.upper(), side=d.side, price=px, target=d.target, invalidation=d.invalidation, note=d.note,
                  telegram_user_id=telegram_user_id, author=(author or "")[:64] or None, member_id=member_id, chat_id=chat_id)
    db.add(idea)
    db.commit()
    return idea


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def card(idea: TsIdea, now_px: float | None = None) -> str:
    long = idea.side == "LONG"
    head = f"💡 <b>{idea.coin} {idea.side}</b> idea by {esc(idea.author or 'a member')} · at {fmt_price(idea.price)}"
    plan = []
    if idea.target:
        plan.append(f"target {fmt_price(idea.target)}")
    if idea.invalidation:
        plan.append(f"invalid below {fmt_price(idea.invalidation)}" if long else f"invalid above {fmt_price(idea.invalidation)}")
    lines = [head]
    if plan:
        lines.append(" · ".join(plan))
    if idea.note:
        lines.append(f"“{esc(idea.note)}”")
    if now_px:
        move = (now_px - idea.price) / idea.price * 100 * (1 if long else -1)
        lines.append(f"Since shared: {move:+.2f}% for the idea (now {fmt_price(now_px)})")
    return "\n".join(lines)


def confirmation(idea: TsIdea) -> str:
    return card(idea) + "\n\n<i>Community idea: a member's opinion, not a Price Chat signal. Never traded by the bot.</i>"


def latest(db: Session, md: MarketData, coin: str | None = None, n: int = 5) -> str:
    q = select(TsIdea).where(TsIdea.status == "open", TsIdea.created_at >= utcnow() - timedelta(days=7))
    if coin:
        q = q.where(TsIdea.coin == md.market(coin).coin.upper())
    rows = db.scalars(q.order_by(TsIdea.id.desc()).limit(n)).all()
    if not rows:
        return (f"No {coin.upper()} ideas this week." if coin else "No community ideas this week.") + " Share one: /idea BTC long tp 72000 inv 64000 reason"
    try:
        _, ms = md.markets()
        px = {k.upper(): v.mark for k, v in ms.items()}
    except Exception:  # noqa: BLE001 - the list still shows without live prices
        px = {}
    head = f"<b>Community ideas{' · ' + rows[0].coin if coin else ''}</b> (last 7 days)"
    foot = "<i>Members' opinions, not Price Chat signals. Never traded by the bot.</i>"
    return "\n\n".join([head, *(card(r, px.get(r.coin)) for r in rows), foot])
