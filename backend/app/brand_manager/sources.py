"""More story sources for Brand Manager, all from APIs and records Price Chat already uses (no scraping, nothing generated):

- Hyperliquid (app/tradingos/market.py): the biggest 24h moves among liquid perpetuals, extreme funding, and 7-day open
  interest swings from Price Chat's own recorder (app/tradingos/oi.py), each with a real 48-hour price chart.
- CoinGecko's trending list (/search/trending: the most searched coins on CoinGecko in the last 24 hours).
- The Pokémon Top 100 (app/pokemon/top100.py): a card entering the ranking.
- The Price Chat community: this week's totals only (new members, verified missions). Never an individual member.

Every number in a brief comes straight from the source; a brief also carries `stat` (the one number its "big number"
scene shows) and `chart` (verified points only)."""

import logging
import re
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

HL_SOURCE = {"name": "Hyperliquid", "url": "https://app.hyperliquid.xyz/trade"}
FUNDING_DOC = {"text": "Funding is a regular payment between long and short traders on perpetual futures: when it is positive, "
                       "longs pay shorts; when it is negative, shorts pay longs.", "source": "Hyperliquid docs"}
OI_DOC = {"text": "Open interest is the total value of perpetual futures positions that are still open.", "source": "Hyperliquid docs"}
MIN_VOLUME = 50_000_000  # USD a day: only liquid markets make a story
MOVE_PCT = 6.0
FUNDING_PER_H = 0.0001  # 0.01% an hour (about 88% a year) and above is unusual
OI_PCT = 15.0


def _key(*parts: Any) -> str:
    from app.brand_manager.discovery import _key as k

    return k(*parts)


def _usd(v: float) -> str:
    if v >= 1e9:
        return f"${v / 1e9:.1f} billion"
    if v >= 1e6:
        return f"${v / 1e6:.0f} million"
    return f"${v:,.2f}" if v < 100 else f"${v:,.0f}"


def _price(v: float) -> str:
    return f"${v:,.2f}" if v >= 1 else f"${v:.4f}"


def _chart(md: Any, coin: str) -> dict[str, Any] | None:
    try:
        cs = md.candles(coin, "1h", 48)
    except Exception:  # noqa: BLE001 - a missing chart only drops the chart scene
        return None
    pts = [[int(c.t / 1000), round(c.c, 6)] for c in cs]
    return {"points": pts, "label": f"{coin} · last 48 hours · Hyperliquid"} if len(pts) >= 12 else None


def hyperliquid(db: Session, md: Any, now: datetime) -> list[Any]:
    """Synchronous (Hyperliquid's client is): discovery runs it in a thread."""
    from app.brand_manager.discovery import Topic
    from app.tradingos import oi

    try:
        top = md.top(30, MIN_VOLUME)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Hyperliquid topics skipped: %s", exc)
        return []
    day = now.strftime("%Y-%m-%d")
    out: list[Any] = []

    def base(m: Any) -> list[dict[str, str]]:
        return [{"text": f"{m.coin} perpetual price on Hyperliquid: {_price(m.mark)}.", "source": "Hyperliquid"},
                {"text": f"24-hour change: {m.change_24h:+.2f}%.", "source": "Hyperliquid"},
                {"text": f"24-hour trading volume: {_usd(m.day_volume)}.", "source": "Hyperliquid"}]

    def nums(m: Any) -> dict[str, Any]:
        return {"price_usd": round(m.mark, 6), "change_24h_pct": round(m.change_24h, 2), "volume_24h_usd": round(m.day_volume),
                "funding_pct_h": round(m.funding * 100, 4), "open_interest_usd": round(m.oi_usd)}

    movers = sorted((m for m in top if abs(m.change_24h) >= MOVE_PCT), key=lambda m: -abs(m.change_24h))[:3]
    for m in movers:
        up = m.change_24h > 0
        out.append(Topic("crypto_derivatives", f"{m.coin} is {'up' if up else 'down'} {abs(m.change_24h):.1f}% in 24 hours on Hyperliquid",
                         _key("hlmove", m.coin, day), 0.9 + min(abs(m.change_24h), 30) / 30, {
                             "headline": f"{m.coin} {'up' if up else 'down'} {abs(m.change_24h):.1f}% in 24 hours",
                             "coin": {"name": m.coin, "symbol": m.coin}, "facts": base(m), "numbers": nums(m),
                             "stat": {"value": abs(round(m.change_24h, 2)), "prefix": "+" if up else "-", "suffix": "%", "decimals": 2,
                                      "kicker": "24H MOVE"},
                             "chart": _chart(md, m.coin), "market_visual_hint": "cvd", "images": [], "sources": [HL_SOURCE]}))
    hot = sorted((m for m in top if abs(m.funding) >= FUNDING_PER_H), key=lambda m: -abs(m.funding))[:2]
    for m in hot:
        payer = "longs pay shorts" if m.funding > 0 else "shorts pay longs"
        out.append(Topic("crypto_derivatives", f"{m.coin} funding is {m.funding * 100:+.4f}% an hour on Hyperliquid: {payer}",
                         _key("hlfund", m.coin, day), 0.85 + min(abs(m.funding) / FUNDING_PER_H, 5) / 10, {
                             "headline": f"{m.coin} funding at {m.funding * 100:+.4f}% per hour",
                             "coin": {"name": m.coin, "symbol": m.coin},
                             "facts": base(m) + [{"text": f"Funding rate: {m.funding * 100:+.4f}% per hour ({payer}).", "source": "Hyperliquid"},
                                                 FUNDING_DOC],
                             "numbers": nums(m),
                             "stat": {"value": abs(round(m.funding * 100, 4)), "prefix": "+" if m.funding > 0 else "-", "suffix": "%",
                                      "decimals": 4, "kicker": "FUNDING PER HOUR"},
                             "chart": _chart(md, m.coin), "market_visual_hint": "oi", "images": [], "sources": [HL_SOURCE]}))
    for m in top[:12]:
        try:
            ch = oi.change(db, m.coin, 7, now)
        except Exception:  # noqa: BLE001
            ch = None
        if not ch or abs(ch[2]) < OI_PCT:
            continue
        then, last, pct = ch
        out.append(Topic("crypto_derivatives", f"Open interest in {m.coin} perps {'rose' if pct > 0 else 'fell'} {abs(pct):.0f}% in a week",
                         _key("hloi", m.coin, now.strftime("%G-W%V")), 0.85 + min(abs(pct), 60) / 120, {
                             "headline": f"{m.coin} open interest {'up' if pct > 0 else 'down'} {abs(pct):.1f}% in 7 days",
                             "coin": {"name": m.coin, "symbol": m.coin},
                             "facts": [{"text": f"{m.coin} open interest on Hyperliquid: {_usd(last)} now, {_usd(then)} 7 days ago.",
                                        "source": "Price Chat's Hyperliquid open-interest record"},
                                       {"text": f"7-day change in open interest: {pct:+.1f}%.", "source": "Price Chat's Hyperliquid open-interest record"},
                                       OI_DOC, *base(m)[:2]],
                             "numbers": {**nums(m), "oi_7d_ago_usd": round(then), "oi_change_7d_pct": round(pct, 1)},
                             "stat": {"value": abs(round(pct, 1)), "prefix": "+" if pct > 0 else "-", "suffix": "%", "decimals": 1,
                                      "kicker": "OPEN INTEREST · 7D"},
                             "chart": _chart(md, m.coin), "market_visual_hint": "oi", "images": [], "sources": [HL_SOURCE]}))
    out += liquidation_topics(db, now, {m.coin for m in top})
    return out


LIQ_HOUR_USD = 1_000_000  # reported in one hour by the exchanges Price Chat records: a story
# Above this, one coin's hour on the recorded feeds is not believed automatically: it is logged and held for a person. The
# recorded feeds are a lower bound (Binance shows one liquidation per market per second), and Telegram 204 (2026-11-21) published
# "$10.1B on Binance in an hour" from a unit error; the real figure was about $17M.
LIQ_REVIEW_USD = 500_000_000
LIQ_COIN = re.compile(r"[A-Z0-9]{2,15}")  # a coin symbol; "BTCUSD_PERP" is a contract and means the record was mis-keyed


def liquidation_topics(db: Session, now: datetime, listed: set[str] | None = None) -> list[Any]:
    """A liquidation flush in the last hour of a market Hyperliquid lists (Price Chat's record of the OKX, Binance and
    Bybit liquidation feeds; whichever reported is named)."""
    from app.tradingos.structure import NAME
    from app.brand_manager.discovery import Topic
    from app.models.tradingos import TsLiquidation

    since = int((now - timedelta(hours=1)).timestamp() * 1000)
    rows = db.execute(select(TsLiquidation.coin, TsLiquidation.venue, TsLiquidation.side, func.sum(TsLiquidation.usd)).where(TsLiquidation.t >= since)
                      .group_by(TsLiquidation.coin, TsLiquidation.venue, TsLiquidation.side)).all()
    per: dict[str, dict[str, float]] = {}
    venues: dict[str, set[str]] = {}
    for coin, venue, side, total in rows:
        per.setdefault(coin, {})[side] = per.get(coin, {}).get(side, 0.0) + float(total or 0)
        venues.setdefault(coin, set()).add(NAME.get(venue, venue))
    out = []
    for coin, sides in per.items():
        if listed is not None and coin not in listed:
            continue
        where = " + ".join(sorted(venues[coin]))
        total = sum(sides.values())
        if total < LIQ_HOUR_USD:
            continue
        if not LIQ_COIN.fullmatch(coin or ""):
            logger.warning("liquidations: %s on %s refused: %r is not a coin symbol (mis-keyed contract records)", _usd(total), where, coin)
            continue
        if total > LIQ_REVIEW_USD:
            logger.warning("liquidations: %s of %s on %s in an hour held for review: above the %s automatic ceiling", _usd(total), coin, where, _usd(LIQ_REVIEW_USD))
            continue
        side = max(sides, key=lambda k: sides[k])
        out.append(Topic("crypto_derivatives", f"{_usd(total)} of {coin} positions liquidated on {where} in an hour, mostly {side}s",
                         _key("liq", coin, now.strftime("%Y-%m-%d-%H")), 0.9 + min(total / LIQ_HOUR_USD, 10) / 10, {
                             "headline": f"{coin} {side}s liquidated: {_usd(total)} on {where} in an hour",
                             "coin": {"name": coin, "symbol": coin},
                             "facts": [{"text": f"{where} reported {_usd(sides.get('long', 0))} of {coin} long liquidations and {_usd(sides.get('short', 0))} "
                                                f"of short liquidations in the hour to {now:%H:%M} UTC.", "source": f"{where} public liquidation data"},
                                       {"text": f"Only {where} liquidations are counted; other exchanges' are not included.", "source": "Price Chat"},
                                       {"text": "A liquidation is an exchange closing a leveraged position whose margin ran out.", "source": "Price Chat"}],
                             "numbers": {"liquidations_1h_usd": round(total)}, "market_visual_hint": "liq",
                             "stat": {"value": round(total / 1e6, 1), "prefix": "$", "suffix": "M", "decimals": 1, "kicker": "LIQUIDATED · 1H"},
                             "images": [], "sources": [{"name": f"{where} (public liquidation feeds)", "url": "https://www.okx.com/trade-swap"}]}))
    return out


async def coingecko_trending(client: httpx.AsyncClient, now: datetime) -> list[Any]:
    from app.brand_manager.discovery import COINGECKO, UA, Topic

    try:
        r = await client.get(f"{COINGECKO}/search/trending", headers=UA)
        r.raise_for_status()
        coins = [c.get("item") or {} for c in (r.json().get("coins") or [])][:7]
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("CoinGecko trending skipped: %s", exc)
        return []
    out = []
    for i, c in enumerate(coins[:3], start=1):
        name, sym = str(c.get("name") or ""), str(c.get("symbol") or "").upper()
        data = c.get("data") or {}
        price = data.get("price")
        ch = (data.get("price_change_percentage_24h") or {}).get("usd")
        if not name or not isinstance(price, (int, float)):
            continue
        facts = [{"text": f"{name} ({sym}) is number {i} on CoinGecko's trending list: the most searched coins on CoinGecko in the "
                          "last 24 hours.", "source": "CoinGecko"},
                 {"text": f"{name} price: {_price(float(price))}.", "source": "CoinGecko"}]
        nums: dict[str, Any] = {"price_usd": round(float(price), 6), "trending_rank": i}
        if isinstance(ch, (int, float)):
            facts.append({"text": f"24-hour change: {ch:+.2f}%.", "source": "CoinGecko"})
            nums["change_24h_pct"] = round(float(ch), 2)
        if c.get("market_cap_rank"):
            facts.append({"text": f"Market cap rank: #{c['market_cap_rank']}.", "source": "CoinGecko"})
            nums["market_cap_rank"] = int(c["market_cap_rank"])
        out.append(Topic("crypto_market", f"{name} is trending #{i} on CoinGecko today", _key("cgtrend", c.get("id"), now.strftime("%Y-%m-%d")),
                         0.8 + (4 - i) * 0.07, {
                             "headline": f"{name} ({sym}) is #{i} on CoinGecko's trending list", "coin": {"id": c.get("id"), "name": name, "symbol": sym},
                             "facts": facts, "numbers": nums,
                             "stat": {"value": i, "prefix": "#", "suffix": "", "decimals": 0, "kicker": "TRENDING ON COINGECKO"},
                             "images": [{"url": c["large"], "credit": "CoinGecko"}] if str(c.get("large") or "").startswith("https://") else [],
                             "sources": [{"name": "CoinGecko", "url": f"https://www.coingecko.com/en/coins/{c.get('id')}"}]}))
    return out


def top100_entries(db: Session, now: datetime) -> list[Any]:
    from app.brand_manager.discovery import Topic
    from app.models.pokemon import PokemonCard, PokemonTop100Event
    from app.pokemon.service import card_out

    out = []
    rows = db.scalars(select(PokemonTop100Event).where(PokemonTop100Event.kind == "entered", PokemonTop100Event.at >= now - timedelta(days=7),
                                                       PokemonTop100Event.rank.is_not(None)).order_by(PokemonTop100Event.rank).limit(3))
    for e in rows:
        card = db.get(PokemonCard, e.card_id)
        if card is None:
            continue
        c = card_out(card, light=True)
        name = f"{c['name']} ({c['set_name']}, {c['year']})"
        out.append(Topic("pokemon_market", f"{c['name']} just entered the Price Chat Pokémon Top 100 at #{e.rank}", _key("pktop", c["id"], e.rank),
                         0.95 + (100 - int(e.rank)) / 400, {
                             "headline": f"{c['name']} enters the Pokémon Top 100 at #{e.rank}", "card": c,
                             "facts": [{"text": f"{name} entered the Price Chat Pokémon Top 100 at rank #{e.rank} on {e.at:%B %d, %Y}.",
                                        "source": "Price Chat Pokémon Top 100"},
                                       {"text": "The Top 100 ranks vintage (1996-2003) PSA 10 cards by estimated market cap: PSA 10 market "
                                                "price times PSA 10 population.", "source": "Price Chat Pokémon Top 100"},
                                       {"text": "PSA 10 is PSA's highest grade, called Gem Mint.", "source": "PSA grading standards"}],
                             "numbers": {"rank": int(e.rank), "year": c.get("year")},
                             "stat": {"value": int(e.rank), "prefix": "#", "suffix": "", "decimals": 0, "kicker": "POKÉMON TOP 100"},
                             "images": [{"url": c["image_url"], "credit": "Card image"}] if c.get("image_url") else [],
                             "sources": [{"name": "Price Chat Pokémon Top 100", "url": "https://pricechat.live/pokemon"}]}))
    return out


def community_week(db: Session, now: datetime) -> list[Any]:
    """This week's community totals (aggregates only: no names, no individual accounts)."""
    from app.brand_manager.discovery import Topic
    from app.models.members import Member, ReputationEvent

    since = now - timedelta(days=7)
    try:
        new = int(db.scalar(select(func.count()).select_from(Member).where(Member.created_at >= since)) or 0)
        total = int(db.scalar(select(func.count()).select_from(Member)) or 0)
        actions = int(db.scalar(select(func.count()).select_from(ReputationEvent).where(ReputationEvent.created_at >= since)) or 0)
    except Exception:  # noqa: BLE001
        return []
    if new < 5:
        return []  # too small to be a story
    facts = [{"text": f"{new:,} people joined the Price Chat community (Kiwi Passport) in the last 7 days.", "source": "Price Chat"},
             {"text": f"Kiwi Passport members in total: {total:,}.", "source": "Price Chat"}]
    if actions:
        facts.append({"text": f"Members completed {actions:,} verified actions (missions, reads, check-ins) this week.", "source": "Price Chat"})
    return [Topic("pricechat_highlights", f"This week at Price Chat: {new:,} new members", _key("community", now.strftime("%G-W%V")), 0.75, {
        "headline": f"{new:,} new Price Chat members this week", "facts": facts,
        "numbers": {"new_members_7d": new, "members_total": total, "actions_7d": actions},
        "stat": {"value": new, "prefix": "+", "suffix": "", "decimals": 0, "kicker": "NEW MEMBERS THIS WEEK"},
        "images": [], "sources": [{"name": "Price Chat", "url": "https://pricechat.live/passport"}]})]
