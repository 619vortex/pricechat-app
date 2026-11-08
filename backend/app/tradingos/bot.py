"""Telegram Bot Controller for the existing Price Chat bot: private-chat commands, the ENTER / REJECT / AUTO buttons on
signal cards, and publishing / updating cards in the signal group.

Account-specific answers only go to the member's private chat with the bot. The group only ever gets signal cards
(no balances, credentials, positions or names). ENTER never trades on its own: it sends the member a private preview
sized to their limits, and only their Confirm (within 5 minutes, single use) executes, in their own mode (PAPER by
default; TESTNET / LIVE only after they authorized an API wallet on the website). Keys are never asked for here.
"""

import asyncio
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.members import LinkedIdentity, Member
from app.models.tradingos import TsSignal, TsSignalAction
from app.tradingos import alerts, chart, market, performance, readout, signals, tickers, venues

logger = logging.getLogger(__name__)

SITE = "https://pricechat.live"
HELP = """<b>Price Chat bot</b>

🥝 <b>AI Investment Committee</b> (research only)
/committee BTC 4h · analysts, bull vs bear, risk check, verdict
/debate ETH · bull vs bear · /research SOL · the full report
/risk BTC · risk committee + Kiwi risk engine · /committee status

📈 <b>Market data</b>
/price BTC ETH SOL · price + 24h move
/markets · today's movers, volume, funding (/markets weekly)
SUI indicator · RSI, MACD, EMA 20/50
/scan KAS · where it trades + a quick read

📊 <b>Charts</b>
/chart BTC 1h · candles, EMAs and a readout
fc btc · chart only (4h), with buttons for 1m 5m 15m 1h 4h 1d 1w · fc btc 15min
Add-ons: rsi macd bb ma volume oi funding depth cvd aggoi liq zones heatmap walls twap wide

🔎 <b>Technical analysis</b>
/signal · the most promising setup right now (top markets)
/signal DOGE · the best setup on one coin, even without a full signal
/signals · the bot's open signals
/record · how /signal ideas played out (hit rate, R)

🔔 <b>Alerts</b> (DM me)
/alert BTC 85000 · /alert ETH -5% · /alerts · /alert off 3

💡 <b>Community ideas</b>
/idea BTC long tp 72000 inv 64000 why · /ideas (members' views, never traded)

🤖 <b>Trading bot</b> (DM me)
/status · /connect · /positions · /pnl · /trades
/risk · /auto · /pause · /resume · /close
/market BTC · /open BTC long 100 5x · /enter (reply to a signal) · /balance · /orders
/sl · /tp · /cancel · /closeall · /settings (Banter group, private chat)

📜 /rules · the group rules (admins: reply /warn 1-5 to mute, /unmute, /strikes)

🛡 <b>Kiwi VPN</b> (DM me)
/vpn · your VPN devices · /vpn add phone

Coins: BTC · $BTC · BTCUSDT · a contract address, plus binance bybit okx hl spot perp dex
Timeframes: 1m 5m 15m 30m 1h 4h 1d · Not financial advice."""
CLOSED = ("TARGET_HIT", "STOP_HIT", "CLOSED", "EXPIRED", "CANCELLED")


def member_for(db: Session, telegram_user_id: int) -> Member | None:
    ident = db.scalar(select(LinkedIdentity).where(LinkedIdentity.platform == "telegram", LinkedIdentity.status == "active",
                                                   LinkedIdentity.platform_user_id == str(telegram_user_id)))
    m = db.get(Member, ident.member_id) if ident else None
    return m if m is not None and m.status == "active" else None


def has_access(db: Session, m: Member | None) -> bool:
    """The private trading service: a linked Kiwi Passport at the required rank (Earn's rule)."""
    if m is None:
        return False
    from app.tradingbot.service import rank_info, settings as earn_settings

    return bool(rank_info(db, m, earn_settings(db))["ok"])


def _min_rank(db: Session) -> int:
    from app.tradingbot.service import settings as earn_settings

    return earn_settings(db).min_rank_level


def buttons(sig: TsSignal, ref_url: str | None = None) -> list[list[dict[str, str]]] | None:
    """ENTER / REJECT / AUTO while the signal is open, and a Hyperliquid sign-up button (the referral link) under them."""
    signup = [[{"text": "🎁 Sign up on Hyperliquid · 4% off fees", "url": ref_url}]] if ref_url else []
    if sig.status in CLOSED:
        return signup or None
    return [[{"text": "ENTER", "callback_data": f"sig:{sig.id}:enter"}, {"text": "REJECT", "callback_data": f"sig:{sig.id}:reject"},
             {"text": "AUTO", "callback_data": f"sig:{sig.id}:auto"}], *signup]


async def _md(fn: Any, *args: Any) -> Any:
    return await asyncio.to_thread(fn, *args)


def _pct(x: float) -> str:
    return f"{x:+.2f}%"


# ---------------------------------------------------------------- commands (private chats and groups)
PUBLIC = {"/start", "/help", "/markets", "/price", "/indicator", "/fc", "/chart", "/scan", "/signals", "/signal", "/record", "/idea", "/ideas",
          "/committee", "/debate", "/research", "/rules", "/market"}  # groups too ("/risk <coin>" as well: the committee's risk review)
MARKET_ARGS = {"/fc", "/chart", "/scan", "/signal", "/price", "/indicator", "/committee", "/debate", "/research"}
COMMITTEE = {"/committee", "/debate", "/research"}  # committeecmd.py (Kiwi AI Investment Committee)
INDICATOR_WORDS = {"indicator", "indicators", "rsi"}  # "SUI indicator" / "sui rsi" -> /indicator SUI
PRIVATE = ({"/status", "/positions", "/pnl", "/connect", "/trades", "/risk", "/auto", "/pause", "/resume", "/close", "/vpn"} | alerts.COMMANDS
           | {"/open", "/enter", "/closeall", "/sl", "/tp", "/orders", "/cancel", "/balance", "/settings"})  # DMs only (tgtrade.py)
HEAVY = {"/chart"}  # image replies that chart_routing "bot_chat" moves out of the main community chat
HEADLINE = ("BTC", "ETH", "SOL", "HYPE")


# names people type ("fc gold", "/price bitcoin") -> the Hyperliquid perpetual. Gold is PAXG (a gold-backed token).
NAMES = {"BITCOIN": "BTC", "XBT": "BTC", "ETHEREUM": "ETH", "ETHER": "ETH", "SOLANA": "SOL", "HYPERLIQUID": "HYPE",
         "GOLD": "PAXG", "XAU": "PAXG", "DOGECOIN": "DOGE", "RIPPLE": "XRP", "CARDANO": "ADA", "CHAINLINK": "LINK",
         "AVALANCHE": "AVAX", "LITECOIN": "LTC", "POLKADOT": "DOT", "TRON": "TRX", "TONCOIN": "TON", "SUI": "SUI",
         "PEPE": "KPEPE", "SHIB": "KSHIB", "SHIBA": "KSHIB", "BONK": "KBONK"}
def coin_name(word: str) -> str:
    w = word.upper().lstrip("$")
    return NAMES.get(w, w)


def fc_command(text: str) -> str | None:
    """'SUI indicator' / 'sui rsi 1h' -> '/indicator ...' (just those words, so chat isn't caught). 'fc gold', 'FC bitcoin',
    'Fc $SOL 4h', 'fc $BTCUSDT 15m rsi' (another price bot's habit) -> '/fc ...', only when every word after the market is a
    timeframe, chart option or exchange word, so ordinary chat starting with "fc" is left alone. None for anything else."""
    from app.tradingos import chartcmd, tickers

    words = text.strip().split()
    if (2 <= len(words) <= 3 and words[1].lower() in INDICATOR_WORDS and tickers.parse_symbol(words[0]) is not None
            and (len(words) == 2 or chartcmd._timeframe(words[2].lower()))):
        return "/indicator " + " ".join([words[0].lstrip("$"), *words[2:]])
    if len(words) < 2 or len(words) > 7 or words[0].lower() != "fc":
        return None
    if tickers.parse_symbol(words[1]) is None:
        return None
    for w in words[2:]:
        low = w.lower()
        if not (low in chartcmd.OPTIONS or chartcmd._timeframe(low) or low in tickers.VENUE_WORDS or low in tickers.KIND_WORDS):
            return None
    return "/fc " + " ".join([words[1].lstrip("$"), *words[2:]])


def is_ours(text: str, bot_username: str | None) -> bool:
    """In a group, answer "/price" and "/price@ourbot", never "/price@someotherbot"."""
    first = text.strip().split()[0]
    if "@" not in first:
        return True
    return bool(bot_username) and first.split("@", 1)[1].lower() == bot_username.lower()


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _arrow(x: float) -> str:
    return f"▲ {x:.1f}%" if x > 0 else f"▼ {abs(x):.1f}%" if x < 0 else "0.0%"


def markets_digest(md: Any) -> str:
    """Curated highlights, not a dump: headline prices, the day's biggest movers among liquid markets, the most traded,
    and funding extremes. Liquid = at least $20M traded in 24h (so thin coins can't top the movers)."""
    _, ms = md.markets()
    liquid = [m for m in ms.values() if m.day_volume >= 20_000_000 and m.mark > 0]
    head = [ms[c] for c in HEADLINE if c in ms]
    lines = ["<b>Hyperliquid · market overview</b>", ""]
    lines += [f"<b>{tickers.cashtag(m.coin)}</b>  {market.fmt_price(m.mark)}  {_arrow(m.change_24h)}" for m in head]
    movers = sorted(liquid, key=lambda m: m.change_24h)
    up = [m for m in reversed(movers) if m.change_24h > 0][:3]
    down = [m for m in movers if m.change_24h < 0][:3]
    if up:
        lines += ["", "<b>Top gainers</b>  " + " · ".join(f"{tickers.cashtag(m.coin)} {_arrow(m.change_24h)}" for m in up)]
    if down:
        lines += ["<b>Top losers</b>  " + " · ".join(f"{tickers.cashtag(m.coin)} {_arrow(m.change_24h)}" for m in down)]
    vol = sorted(liquid, key=lambda m: -m.day_volume)[:3]
    lines += ["<b>Most traded</b>  " + " · ".join(f"{tickers.cashtag(m.coin)} ${m.day_volume / 1e9:.1f}B" if m.day_volume >= 1e9 else f"{tickers.cashtag(m.coin)} ${m.day_volume / 1e6:.0f}M" for m in vol)]
    hot = max(liquid, key=lambda m: m.funding, default=None)
    cold = min(liquid, key=lambda m: m.funding, default=None)
    if hot and cold and (hot.funding > 0.00002 or cold.funding < -0.00002):
        lines += [f"<b>Funding</b>  {tickers.cashtag(hot.coin)} {hot.funding * 100:+.4f}%/h (longs pay) · {tickers.cashtag(cold.coin)} {cold.funding * 100:+.4f}%/h (shorts pay)"]
    total = sum(m.day_volume for m in ms.values())
    lines += ["", f"24h volume ${total / 1e9:.1f}B · /price $BTC · /chart $BTC 1h"]
    return "\n".join(lines)


SAFETY = "🔒 Never share a private key, seed phrase or API secret here, not even with me."


def welcome_group(names: list[str], bot_username: str) -> tuple[str, list[list[dict[str, str]]]]:
    """The one short welcome posted in the signal group when people join (the bot can't message anyone privately first)."""
    who = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
    text = (f"👋 Welcome {esc(who)}!\nSignals land here with ENTER / REJECT / AUTO. Tap <b>Start</b> for your private assistant: "
            "paper trading first, nothing trades without your OK.\n\n📜 Please read the group rules: /rules")
    return text, [[{"text": "▶ Start the bot", "url": f"https://t.me/{bot_username}?start=welcome"}]]


def onboarding(db: Session, m: Member | None) -> tuple[str, list[list[dict[str, str]]]]:
    """The private /start: how it works, then THIS member's own setup (Passport, rank, mode, API wallet, AUTO) and the next step."""
    how = ("<b>How it works</b>\n"
           "1. Tap ENTER on a signal in @botpricechat: I DM you a preview (size, stop, targets, max loss).\n"
           "2. Nothing trades until you Confirm. You start in PAPER: real prices, no real money.\n"
           "3. Go live by connecting a Hyperliquid API wallet on the website (never here). AUTO can trade signals for you; /pause stops it.")
    site = [{"text": "Open my trading terminal", "url": f"{SITE}/earn"}]
    if m is None:
        return (f"👋 <b>Price Chat trading bot</b> 🥝\n\n{how}\n\n<b>Start:</b> link this Telegram to your free Kiwi Passport, then send "
                f"/start again.\n\n{SAFETY}", [[{"text": "Link my Kiwi Passport", "url": f"{SITE}/passport"}]])
    from app.tradingbot.service import rank_info, settings as earn_settings

    rk = rank_info(db, m, earn_settings(db))
    acc = _acc(db, m)
    agent = acc.mainnet_approved_at and "mainnet" or acc.testnet_approved_at and "testnet"
    auto = (f"ON until {acc.auto_until:%d %b %H:%M} UTC" if acc.auto_until else "ON") if acc.auto_enabled else "off"
    lines = [f"👋 <b>Hi {esc(m.username)}</b> 🥝", "", how, "", "<b>Your setup</b>",
             f"{'✅' if rk['ok'] else '❌'} Kiwi Rank {rk['current']['level']}" + ("" if rk["ok"] else f" · trading opens at Rank {rk['required']['level']}"),
             f"Mode: <b>{acc.mode}</b>" + (" (simulated)" if acc.mode == "PAPER" else " (real money)" if acc.mode == "LIVE" else " (test funds)"),
             f"{'✅' if agent else '➖'} API wallet: " + (f"approved ({agent})" if agent else "not yet (only needed to go live)"),
             f"{'✅' if acc.auto_enabled else '➖'} AUTO: {auto}" + (" · NEW TRADES STOPPED" if acc.paused else ""), ""]
    btn = [site]
    if not rk["ok"]:
        nxt = "<b>Next:</b> level up your Kiwi to unlock trading."
        btn = [[{"text": "Level up my Kiwi", "url": f"{SITE}/passport"}]]
    elif not acc.auto_enabled and not agent:
        nxt = "<b>Next:</b> tap ENTER on the next signal to try it in PAPER."
    elif not acc.auto_enabled:
        nxt = "<b>Next:</b> tap ENTER on signals, or turn on AUTO in your terminal."
    else:
        nxt = f"✅ AUTO trades new signals in {acc.mode}. /trades to follow, /pause to stop."
    return "\n".join(lines) + nxt + f"\n\n{SAFETY}", btn


def _is_main_chat(db: Session, chat_id: int, chat_username: str | None) -> bool:
    main = (signals.settings(db).main_chat or "").strip().lower()
    return bool(main) and (main == str(chat_id) or (bool(chat_username) and main.lstrip("@") == (chat_username or "").lower()))


CHANNEL_FOOTER = "\n\n📢 Signals: @botpricechat"  # under every command reply (Telegram links the @username itself)


def referral_url(db: Session) -> str | None:
    """The owner's Hyperliquid referral link (Earn settings › referral link), or None."""
    from app.tradingbot.service import settings as earn_settings

    try:
        url = (earn_settings(db).referral_url or "").strip()
    except Exception:  # noqa: BLE001 - a settings read must never block a reply
        return None
    return url if url.startswith("https://") else None


def tracked_url(db: Session, placement: str) -> str | None:
    """The referral link as shown: /api/r/{placement} counts the click, then 302s to referral_url (clicks.py). None when unset."""
    from app.tradingbot.clicks import tracked_url as _tracked

    return _tracked(placement) if referral_url(db) else None


def referral_line(db: Session, placement: str = "footer") -> str:
    """The sign-up line under replies, cards and announcements (Hyperliquid gives referred traders 4% off fees), or ''."""
    url = tracked_url(db, placement)
    return f"🎁 4% off Hyperliquid fees: {url}" if url else ""


def signup_block(db: Session, placement: str = "help") -> str:
    """The sign-up pitch at the top of /start and /help."""
    url = tracked_url(db, placement)
    if not url:
        return ""
    return f"🎁 <b>4% off Hyperliquid fees</b> with our link (the bot trades there): {url}\n\n"


def footer(db: Session) -> str:
    """The bot channel, then the Hyperliquid referral link below it."""
    ref = referral_line(db)
    return CHANNEL_FOOTER + (f"\n{ref}" if ref else "")


FC_BOT_LINK = "\n\n🤖 Price Chat bot: @botpricechat"  # "fc" charts: the bot link without the word "signals" (fc never shows them)


def fc_footer(db: Session) -> str:
    """Under an "fc" chart: the Price Chat bot link, then the Hyperliquid referral link."""
    ref = referral_line(db)
    return FC_BOT_LINK + (f"\n{ref}" if ref else "")


FC_PICKED = "• {} •"  # the timeframe on show


def fc_buttons(words: list[str], tf: str) -> list[list[dict[str, str]]] | None:
    """The timeframe buttons under an "fc" chart: two rows, the one on show marked. Each press redraws the same chart on
    that timeframe (handle_callback, "fc:<tf>:<words>"). None when the words don't fit Telegram's 64-byte callback data."""
    from app.tradingos.chartcmd import FC_TIMEFRAMES

    words_s = " ".join(words)
    if not words_s or len(f"fc:15m:{words_s}".encode()) > 64:
        return None
    row = [{"text": FC_PICKED.format(t) if t == tf else t, "callback_data": f"fc:{t}:{words_s}"} for t in FC_TIMEFRAMES]
    return [row[:4], row[4:]]


def _fc_showing(message: dict[str, Any]) -> str | None:
    """The timeframe marked on a chart's buttons (what it shows now)."""
    for row in ((message.get("reply_markup") or {}).get("inline_keyboard") or []):
        for b in row:
            if str(b.get("text", "")).startswith("•"):
                return str(b.get("callback_data", "")).split(":")[1] if str(b.get("callback_data", "")).startswith("fc:") else None
    return None


_FC_BUSY: set[tuple[Any, Any]] = set()  # charts being redrawn: a second press waits for the first


async def _fc_callback(db: Session, client: Any, cq: dict[str, Any], uid: int, data: str) -> None:
    """A timeframe button under an "fc" chart: the same message gets the chart on that timeframe (photo, caption and
    buttons replaced in place). Chart only: no readout and no signal, as "fc"."""
    from app.tradingos import chartcmd

    msg = cq.get("message") or {}
    chat_id, mid = (msg.get("chat") or {}).get("id"), msg.get("message_id")
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[1] not in chartcmd.FC_TIMEFRAMES or not parts[2].strip() or chat_id is None or mid is None:
        await _answer(client, cq, uid, "This chart can't be changed. Type fc BTC 15m for a new one.", dm=False)
        return
    tf, words = parts[1], parts[2].split()
    if _fc_showing(msg) == tf:
        await _answer(client, cq, uid, f"Already showing {tf}.", dm=False)
        return
    key = (chat_id, mid)
    if key in _FC_BUSY:
        await _answer(client, cq, uid, "Drawing the chart…", dm=False)
        return
    _FC_BUSY.add(key)
    try:
        md = market.data()
        try:
            req = chartcmd.parse([*words, tf], set((await _md(md.markets))[1]))
            png, caption, _ = await asyncio.to_thread(chartcmd.build_chart_response, db, md, req, venues.client(), signals.settings(db))
        except (chartcmd.ChartUsageError, market.MarketError, venues.NotFound, venues.Ambiguous) as e:
            await _answer(client, cq, uid, f"No {tf} chart right now: {str(e)[:150]}", alert=True, dm=False)
            return
        await client.edit_photo(chat_id, mid, png, caption + fc_footer(db), fc_buttons(words, tf), html=True)
        await _answer(client, cq, uid, f"{tf} chart", dm=False)
    finally:
        _FC_BUSY.discard(key)


def with_referral(db: Session, text: str, placement: str = "footer") -> str:
    ref = referral_line(db, placement)
    return f"{text}\n{ref}" if ref else text


class _Footed:
    """The Telegram client for one command: every message and photo caption it sends ends with the footer (bot channel +
    referral link) when it fits Telegram's limit, else it goes without. HTML is never sliced mid-line (a cut tag or entity
    makes Telegram refuse the whole message)."""

    def __init__(self, client: Any, foot: str = CHANNEL_FOOTER) -> None:
        self._c = client
        self._foot = foot

    def __getattr__(self, name: str) -> Any:
        return getattr(self._c, name)

    def _footed(self, text: str, limit: int) -> str:
        if _u16(text + self._foot) <= limit:  # raw HTML is never shorter than what Telegram counts
            return text + self._foot
        return _whole_lines(text, limit)

    async def send_message(self, chat_id: int | str, text: str, *a: Any, **kw: Any) -> Any:
        return await self._c.send_message(chat_id, self._footed(text, 4096), *a, **kw)

    async def send_photo(self, chat_id: int | str, png: bytes, caption: str, *a: Any, **kw: Any) -> Any:
        if not self._foot.strip() or _u16(caption + self._foot) <= 1024:
            return await self._c.send_photo(chat_id, png, caption + self._foot, *a, **kw)
        # a full caption: the bot channel and referral links follow as their own message, so a reply never goes without them
        out = await self._c.send_photo(chat_id, png, _whole_lines(caption, 1024), *a, **kw)
        await self._c.send_message(chat_id, self._foot.strip(), html=True)
        return out


def _u16(s: str) -> int:
    """Telegram's length: UTF-16 code units (an emoji is two)."""
    return len(s.encode("utf-16-le")) // 2


def _whole_lines(text: str, limit: int) -> str:
    """Over the limit: keep whole lines only (tags never span a line here); a single overlong line goes as is."""
    cut = len(text)
    while _u16(text[:cut]) > limit:
        cut = text.rfind("\n", 0, cut)
        if cut <= 0:
            return text
    return text[:cut]


async def handle_command(db: Session, client: Any, chat_id: int, user_id: int, text: str, group: bool = False, reply_to: int | None = None,
                         chat_username: str | None = None, author: str | None = None) -> None:
    client = _Footed(client, footer(db))
    db.commit()  # (a first run creates settings rows): no write transaction waits on Telegram or an exchange below
    parts = text.strip().split()
    cmd = parts[0].split("@")[0].lower()
    # market commands keep the words as typed (a Solana contract address is case-sensitive); the parsers ignore case
    args = parts[1:] if cmd in MARKET_ARGS else [p.upper() for p in parts[1:]]
    brief = cmd == "/fc"  # "fc sui": the chart and its read, no signal (that's /signal sui)
    if cmd == "/fc":  # "fc bitcoin": another price bot's habit, answered with our 4h chart (any /chart option still works)
        from app.tradingos.chartcmd import _timeframe

        cmd, args = "/chart", (args + ([] if any(_timeframe(a.lower()) for a in args[1:]) else ["4h"]) if args else [])
    if cmd not in PUBLIC | PRIVATE:
        if not group:  # in groups an unknown command is usually another bot's: stay quiet
            await client.send_message(chat_id, f"I don't know {esc(cmd)}. /help lists every command.", html=True)
        return
    if cmd == "/start" and not group:  # the private welcome: how it works + this member's own setup and next step
        starter = member_for(db, user_id)
        if starter is not None:
            from app.tradingbot.service import bot_started

            try:
                bot_started(db, starter)  # the website's Telegram step turns done on its own
            except Exception:  # noqa: BLE001 - never blocks the welcome
                db.rollback()
        text_, btns = onboarding(db, starter)
        ref = tracked_url(db, "start")
        if ref:  # the Hyperliquid sign-up: pitched first, and a button of its own
            text_ = signup_block(db, "start") + text_
            btns = [*(btns or []), [{"text": "🎁 Sign up on Hyperliquid · 4% off fees", "url": ref}]]
        await client.send_message(chat_id, text_, btns, html=True)
        return
    committee = cmd in COMMITTEE or (cmd == "/risk" and len(parts) > 1)  # "/risk BTC": the committee; "/risk": your limits
    if group and cmd == "/enter":  # replying to a signal in the group: the preview goes to the member privately, nothing private here
        await _enter_from_group(db, client, chat_id, user_id, args, reply_to)
        return
    if group and cmd in PRIVATE and not committee:
        from app.telegram.ingestion import get_bot_state

        name = get_bot_state(db).bot_username or "cryptopricechatbot"
        what = "Alerts are set" if cmd in alerts.COMMANDS else "Account commands work"
        await client.send_message(chat_id, f"{what} privately: t.me/{name}", reply_to=reply_to)
        return
    if cmd == "/help" and len(parts) > 1 and await _trade_check(db, client, chat_id, user_id, text, reply_to):
        return
    s = signals.settings(db)
    db.commit()
    bot_chat = (s.publish_chat or "").strip()
    redirect = (group and cmd in HEAVY and s.chart_routing == "bot_chat" and _is_main_chat(db, chat_id, chat_username)
                and bot_chat.lower() not in ("", str(chat_id), "@" + (chat_username or "").lower()))
    try:
        if cmd in ("/idea", "/ideas"):
            reply = await _idea_command(db, chat_id, user_id, cmd, parts[1:], author)
        elif committee:  # market research for anyone, groups too (the words keep their case: contract addresses)
            from app.tradingos import committeecmd

            reply = await committeecmd.command(db, client, chat_id, user_id, cmd, parts[1:], reply_to, chat_username, footer(db))
        elif cmd == "/vpn":  # device names keep the case they were typed in
            reply = await vpn_command(db, client, chat_id, user_id, parts[1:])
        elif redirect:  # the chart goes to Bot Chat; the main chat gets one short line pointing there
            reply = await _command(db, client, bot_chat, user_id, cmd, args, None, brief=brief)
            if reply is None:
                reply = f"📊 Posted in Bot Chat to keep this chat readable: t.me/{esc(bot_chat.lstrip('@'))}"
        else:
            reply = await _command(db, client, chat_id, user_id, cmd, args, reply_to, brief=brief)
    except market.MarketError as e:
        reply = f"Couldn't read the market data right now: {esc(str(e))}"
    except venues.Ambiguous as e:
        reply = venues.ambiguity_text(e)
    except venues.NotFound as e:
        reply = esc(str(e))
    except Exception:  # noqa: BLE001 - a command must never break the ingestion loop
        logger.exception("bot command %s failed", cmd)
        reply = "Something went wrong, try again in a moment."
    if reply:
        await client.send_message(chat_id, reply, html=True, reply_to=reply_to)
    logger.info("Bot answered %s in %s chat %s", "fc" if brief else cmd, "a group" if group else "a private", chat_id if group else "-")


async def _trade_check(db: Session, client: Any, chat_id: int, user_id: int, text: str, reply_to: int | None) -> bool:
    """'/help <trade>': Vortex's risk check (assist.py), groups and private chats. False when it's switched off (then /help
    answers as always)."""
    from app.tradingos import assist

    if not assist.config(db)["enabled"]:
        return False
    try:
        reply = await assist.start(db, client, chat_id, user_id, text, reply_to)
    except market.MarketError as e:
        db.rollback()
        reply = f"Couldn't read the market data right now: {esc(str(e))}"
    except Exception:  # noqa: BLE001 - a command must never break the ingestion loop
        logger.exception("trade check failed")
        db.rollback()
        reply = "Something went wrong, try again in a moment."
    if reply:
        await client.send_message(chat_id, reply, html=True, reply_to=reply_to)
    return True


async def _command(db: Session, client: Any, chat_id: int, user_id: int, cmd: str, args: list[str], reply_to: int | None = None,
                   brief: bool = False) -> str | None:
    md = market.data()
    m = member_for(db, user_id)
    if cmd in ("/start", "/help"):
        if cmd == "/start" and m is None:
            return f"{signup_block(db, 'start')}Link your Kiwi Passport: {SITE}/passport → Connect Telegram.\n\n{HELP}"
        return signup_block(db, cmd.lstrip("/")) + HELP
    if cmd == "/rules":  # the group rules (app/telegram/moderation.py), for anyone, groups too
        from app.telegram.moderation import RULES_TEXT

        return RULES_TEXT
    if cmd == "/market":  # one perp's numbers, for anyone, groups too (tgtrade.py)
        from app.tradingos import tgtrade

        return await _md(tgtrade.market_text, args[0].lstrip("$")) if args else "Usage: /market BTC"
    if cmd == "/markets":
        if args and args[0] in ("WEEKLY", "WEEK", "7D"):
            from app.tradingos import weekly

            return await asyncio.to_thread(weekly.build, md, db)
        if args:
            return "Usage: /markets (today) or /markets weekly (the last 7 days)"
        return await _md(markets_digest, md)
    if cmd == "/price":
        sym, _ = tickers.symbol_args(args)
        if sym is None:
            return "Usage: /price BTC · /price $BTCUSDT · /price $KAS bybit"
        words = {w.lower() for w in (*tickers.VENUE_WORDS, *tickers.KIND_WORDS)}
        coins = [a for a in args if tickers.parse_symbol(a) is not None and a.lower() not in words]
        if len(coins) > 1 and len(coins) == len(args):  # "/price btc eth sol": one line each (up to 8), resolved together
            async def line(word: str) -> str:
                one, _ = tickers.symbol_args([word])
                try:
                    return _price_line(await _md(venues.resolve, one, md, venues.client()))
                except (venues.NotFound, venues.Ambiguous):
                    return f"<b>{esc(word.upper().lstrip('$'))}</b>  not found"
            return "\n".join(await asyncio.gather(*(line(w) for w in coins[:8])))
        x = await _md(venues.resolve, sym, md, venues.client())
        return _price_line(x)  # just the price and the 24h move
    if cmd == "/indicator":
        return await _indicator(md, args)
    if cmd == "/chart":
        return await _chart(db, client, chat_id, md, args, reply_to, brief=brief)
    if cmd == "/scan":
        return await _scan(db, md, args)
    if cmd == "/signals":
        open_ = db.scalars(select(TsSignal).where(TsSignal.status.in_(signals.OPEN), TsSignal.published_at.is_not(None))
                           .order_by(TsSignal.number.desc()).limit(5)).all()
        if not open_:
            return "No open signals: they're only posted when every rule passes. /signal BTC shows the best setup now."
        return "\n\n".join(esc(signals.card(x)) for x in open_)
    if cmd == "/record":  # the /signal track record (record.py): public, no user data
        from app.tradingos import record

        return record.record_table(db)
    if cmd == "/connect":
        return ("<b>Connect Hyperliquid</b> (2 minutes)\n"
                f"1. {SITE}/earn → <b>Connect Hyperliquid API</b>\n"
                "2. On Hyperliquid › API, create an API wallet and authorize it\n"
                "3. Paste its key on the website. You stay in PAPER until you turn on live.\n\n" + SAFETY)
    if cmd == "/signal":  # market analysis only (no account data), so it's answered for anyone, in groups too
        from app.tradingos.chartcmd import _timeframe

        st = signals.settings(db)
        tf = next((_timeframe(a.lower()) for a in args if _is_tf(a)), None) or st.timeframe
        coin_args = [a for a in args if not _is_tf(a)]
        if not coin_args:  # no coin: the most promising setup across the most traded markets
            return await _best_signal(db, client, chat_id, reply_to, md, tf)
        sym, _ = tickers.symbol_args(coin_args)
        if sym is None:
            return "Usage: /signal (the best setup now) · /signal DOGE · /signal $DOGE 1h"
        listing = await _md(venues.resolve, sym, md, venues.client())
        # the full rules where they run (Hyperliquid perps); elsewhere a chart read: NO TRADE, never a made-up plan
        if listing.venue != "hyperliquid":
            candles = await _md(venues.candles, listing, tf, 200, md, venues.client())
            a = signals.Analysis(coin=listing.tag, decision="NO TRADE", reasons=["chart-based read only on this market"])
        else:
            a = await _md(signals.analyze, md, listing.symbol, tf, st.min_volume_usd, st.max_spread_bps, st.min_rr)
            candles = await _md(md.candles, a.coin, tf, 200)
        a.indicators = {**signals.chart_indicators(candles, tf), **a.indicators}  # short histories still get a read
        return await _send_signal(db, client, chat_id, reply_to, listing, tf, a, candles)
    if cmd in alerts.COMMANDS:  # per Telegram user: no Passport needed
        return await alerts.command(db, user_id, cmd, args, md)
    # ---- the private trading service: a linked Kiwi Passport at the required rank
    if m is None:
        return f"Link your Kiwi Passport first: {SITE}/passport → Connect Telegram."
    if cmd == "/status":
        from app.tradingbot.service import rank_info, settings as earn_settings, wallet_of

        rk = rank_info(db, m, earn_settings(db))
        w = wallet_of(db, m)
        return "\n".join([f"<b>{esc(m.username)}</b>", f"Kiwi Rank {rk['current']['level']} · {esc(rk['current']['name'])}"
                          + ("" if rk["ok"] else f" (trading needs Rank {rk['required']['level']})"),
                          "Telegram: connected", f"Hyperliquid wallet: {'connected' if w else 'not connected · /connect'}",
                          f"Trading mode: {_acc(db, m).mode}" + (" · NEW TRADES STOPPED" if _acc(db, m).paused else "")])
    if not has_access(db, m):
        return f"The trading service opens at Kiwi Rank {_min_rank(db)}. Level up on {SITE}/passport."
    from app.tradingos import tgtrade

    if cmd in tgtrade.COMMANDS:  # /open, /enter, /closeall, /sl, /tp, /orders, /cancel, /balance, /settings
        return await tgtrade.command(db, client, chat_id, m, cmd, args)
    if cmd in ("/positions", "/pnl"):
        from app.tradingbot.service import wallet_of

        w = wallet_of(db, m)
        acc_ = _acc(db, m)
        if cmd == "/positions":
            out = [await _md(tgtrade.bot_positions_text, db, acc_)]
            if w is not None:  # and the member's own Hyperliquid account (mainnet), as before
                acc = await _md(md.positions, w.address)
                rows = [f"<b>{esc(p['coin'])}</b> {p['side']} {abs(p['size'])} @ {market.fmt_price(p['entry'])} · uPnL ${p['pnl']:+,.2f}"
                        + (f" · liq {market.fmt_price(p['liq'])}" if p["liq"] else "") for p in acc["positions"]]
                out.append(f"<b>Your Hyperliquid account</b> · equity ${acc['equity']:,.2f}\n" + ("\n".join(rows) if rows else "No open positions."))
            return "\n\n".join(out)
        from app.tradingos import execution

        res = execution.results(db, acc_)[acc_.mode]
        out = [f"<b>Bot results · {acc_.mode}</b>: {res['trades']} closed · net ${res['net']:+,.2f} · fees ${res['fees']:,.2f}"
               + (f" · win rate {res['win_rate']}%" if res["win_rate"] is not None else "")]
        if w is not None:
            from app.tradingbot.hyperliquid import InfoClient, pnl_summary

            pn = pnl_summary(await _md(InfoClient().portfolio, w.address))
            out.append("<b>Your Hyperliquid P&amp;L</b>\n" + ("\n".join(f"{k}: ${v['pnl']:+,.2f}" for k, v in pn.items()) if pn else "No history yet."))
        return "\n\n".join(out)
    return await _trading_command(db, client, chat_id, m, cmd, args)


async def vpn_command(db: Session, client: Any, chat_id: int, user_id: int, words: list[str]) -> str | None:
    """/vpn in the member's private chat: their Kiwi VPN devices. `/vpn add <name> [server]` creates one and sends its
    WireGuard config as a .conf document (shown once, never stored); `/vpn delete <id>` removes one."""
    from app.security.rate_limit import LIMITS
    from app.vpn import service as vpn

    if int(chat_id) != int(user_id):  # never in a group, even if a caller forgets to check
        return "Account commands work privately."
    m = member_for(db, user_id)
    if m is None:
        return f"Link your Kiwi Passport first: {SITE}/passport → Connect Telegram."
    view = await asyncio.to_thread(vpn.overview, db, m)
    sub = words[0].lower() if words else ""
    if sub in ("add", "new", "create"):
        if not view["eligible"]:
            return esc(view["reason"] or "Kiwi VPN isn't available.")
        rest = words[1:]
        online = [s for s in view["servers"] if s["online"]]
        server = None
        if len(rest) > 1:  # "/vpn add laptop frankfurt" or "/vpn add laptop 2": the last word picks the server
            key = rest[-1].lower().lstrip("#")
            server = next((s for s in view["servers"] if key in (str(s["id"]), s["name"].lower(), s["region"].lower(),
                                                                   s["country_code"].lower())), None)
            if server is not None:
                rest = rest[:-1]
        if server is None:
            if not online:
                return "No Kiwi VPN server is online right now. Try again later."
            server = next((s for s in online if s.get("recommended")), None) or min(online, key=lambda s: (s["load"], s["id"]))
        if LIMITS["vpn_device"].hit(str(m.id)) is not None:
            return "Too many new devices. Please wait a few minutes."
        try:
            dev, config, filename = await asyncio.to_thread(vpn.create_device, db, m, " ".join(rest) or "phone", server["id"])
        except vpn.VpnError as e:
            return esc(str(e))
        await client.send_document(chat_id, config.encode(), filename, html=True,
                                   caption=(f"🛡 <b>{esc(dev.name)}</b> · {esc(server['name'])}\nImport this file in the free WireGuard app. "
                                            "It's shown once and holds your private key: keep it to yourself. Lost it? /vpn delete "
                                            f"{dev.id}, then add a new one."))
        return None
    if sub in ("delete", "remove", "del"):
        if len(words) < 2 or not words[1].lstrip("#").isdigit():
            return "Usage: /vpn delete 3 (the number from /vpn)"
        try:
            await asyncio.to_thread(vpn.delete_device, db, m, int(words[1].lstrip("#")))
        except vpn.VpnError as e:
            return esc(str(e))
        return "Device deleted."
    lines = ["🛡 <b>Kiwi VPN</b>"]
    if not view["eligible"]:
        lines.append(esc(view["reason"] or "Kiwi VPN isn't available."))
    names = {s["id"]: s["name"] for s in view["servers"]}
    for d in view["devices"]:
        seen = d["last_handshake"][:16].replace("T", " ") + " UTC" if d["last_handshake"] else "never connected"
        lines.append(f"#{d['id']} <b>{esc(d['name'])}</b> · {esc(names.get(d['server_id'], '?'))} · {seen}")
    if view["eligible"]:
        if not view["devices"]:
            lines.append("No devices yet.")
        lines.append(f"{len(view['devices'])} of {view['max_devices']} devices.")
        if view["servers"]:
            lines.append("Servers: " + ", ".join(f"{s['id']} {esc(s['name'])}{'' if s['online'] else ' (offline)'}{' (recommended)' if s.get('recommended') else ''}"
                                               for s in view["servers"]))
        use = view.get("usage") or {}
        if use.get("paused_until"):
            lines.append(f"⏸ Paused: you've used this month's data. It resumes on {use['paused_until'][:10]} (UTC).")
        lines.append("/vpn add phone (recommended server) · /vpn add laptop 2 (server 2)\n/vpn delete 3 · Get the free WireGuard app, "
                     "then import the file I send.")
    return "\n".join(lines)


def _is_tf(word: str) -> bool:
    from app.tradingos.chartcmd import _timeframe

    return bool(_timeframe(word.lower()))


def _price_line(x: Any) -> str:
    """/price: the asset, its price and the 24h move. Nothing else."""
    chg = f"  {_arrow(x.change_24h)} 24h" if x.change_24h is not None else ""
    return f"<b>{esc(tickers.cashtag(x.base) if x.base else x.tag)}</b>  {market.fmt_price(x.price)}{chg}"


async def _indicator(md: Any, args: list[str]) -> str:
    """'SUI indicator' / /indicator SUI [tf]: the price line and the three main indicators (RSI, MACD, EMA 20/50), 4h by default."""
    from app.tradingos import indicators
    from app.tradingos.chartcmd import _timeframe

    sym, _ = tickers.symbol_args([a for a in args if not _is_tf(a)])
    if sym is None:
        return "Usage: SUI indicator · /indicator SUI · /indicator $SUI 1h"
    tf = next((_timeframe(a.lower()) for a in args if _is_tf(a)), None) or "4h"
    x = await _md(venues.resolve, sym, md, venues.client())
    candles = await _md(venues.candles, x, tf, 120, md, venues.client())
    lines = indicators.main_three_lines(indicators.main_three([c.c for c in signals.closed(candles, tf)]))
    if not lines:
        return _price_line(x) + f"\nNot enough {tf} history for the indicators yet."
    return "\n".join([_price_line(x), "", *(esc(line) for line in lines), f"<i>{readout.tf_label(tf)} candles</i>"])


def signal_text(listing: Any, tf: str, a: Any, candles: list[Any]) -> str:
    """/signal on a Hyperliquid perpetual, in Telegram HTML: the setup when every rule passes; otherwise NO TRADE with why
    and what would change it (no entry, stop or target), or WAIT when there's no read at all. Analysis on request: never a
    published signal, never traded."""
    from app.tradingos import indicators

    price = a.indicators.get("price") or (candles[-1].c if candles else None)
    inds = indicators.main_three_lines(a.indicators.get("main3") or indicators.main_three([c.c for c in signals.closed(candles, tf)]))
    head = [f"📊 <b>{esc(listing.tag)}</b> | {readout.tf_label(tf)} | {esc(listing.label)}"]
    if price:
        chg = f" ({listing.change_24h:+.2f}% 24h)" if listing.change_24h is not None else ""
        head.append(f"Price {market.fmt_price(price)}{chg}")
    block = ["", *(f"· {esc(x)}" for x in inds)] if inds else []
    f = market.fmt_price

    def plan(lo: float, hi: float, stop: float, target: float, rr: float, ref: float) -> list[str]:
        mid = (lo + hi) / 2  # stop and target from the entry; the entry from the price now

        def pct(px: float, base: float) -> str:
            return f"{(px - base) / base * 100:+.1f}%"
        return [f"🎯 Entry  {f(lo)} – {f(hi)}" + ("" if lo <= ref <= hi else f"  ({pct(mid, ref)} from now)"),
                f"🛑 Stop   {f(stop)}  ({pct(stop, mid)})", f"✅ Target {f(target)}  ({pct(target, mid)}) · R/R 1:{rr:.1f}"]
    if a.decision == "NO TRADE":  # the owner's rule (2026-10-31): no entry, stop or target the rules didn't produce
        why = "; ".join(esc(r) for r in a.reasons[:2]) or "no setup"
        if not price:
            return "\n".join([*head, "", "⏸ <b>WAIT</b> · no clean read on this chart", f"Why: {why}", *block, "", "<i>Not financial advice.</i>"])
        return "\n".join([*head, "", "⏸ <b>NO TRADE</b> · no setup passes every rule", f"Why: {why}",
                          f"What would change it: {_what_changes(a)}", *block, "", "<i>Not financial advice.</i>"])
    lo, hi = min(a.entry), max(a.entry)
    lev = f"{a.lev[0]}x" if a.lev[0] == a.lev[1] else f"{a.lev[0]}–{a.lev[1]}x"
    return "\n".join([*head, "", f"{'🟢' if a.decision == 'LONG' else '🔴'} <b>{a.decision} setup</b> · passes every rule (not a published signal)",
                       *plan(lo, hi, a.stop, a.target, a.rr, (lo + hi) / 2), f"⚡ {lev}", *block, "",
                       f"💬 {esc(a.reasons[0].replace(' (closed candles)', ''))}" if a.reasons else "", "<i>Not financial advice.</i>"])


def _what_changes(a: Any) -> str:
    """NO TRADE: the chart's own levels a setup would need, never a made-up plan."""
    i, f = a.indicators, market.fmt_price
    need = " or ".join(x for x in (f"a close above resistance {f(i['resistance'])} (long side)" if i.get("resistance") else "",
                                   f"a close below support {f(i['support'])} (short side)" if i.get("support") else "") if x)
    if not need:
        return "the failing rules above passing on a closed candle"
    return need + (", with EMA20 / EMA50 lining up with the move" if i.get("ema20") and i.get("ema50") else "")


def _full_plan(a: Any) -> Any:
    """/signal's plan only when every rule passes: a NO TRADE never gets prices, a chart plan or a track-record entry."""
    p = signals.best_plan(a)
    return p if p is not None and p.full else None


SCAN_TOP = 15  # /signal without a coin: how many of the most traded Hyperliquid markets are compared


async def best_signal(db: Session, md: Any, tf: str, chat_type: str = "group") -> tuple[str, bytes | None]:
    """The most promising setup that passes every rule across the top markets (by score), else NO TRADE:
    its text and chart. /signal alone and the daily brief. Analysis on request, never published or traded."""
    st = signals.settings(db)
    gate = asyncio.Semaphore(4)

    async def one(coin: str) -> tuple[Any, Any] | None:
        async with gate:
            try:
                a = await _md(signals.analyze, md, coin, tf, st.min_volume_usd, st.max_spread_bps, st.min_rr)
            except market.MarketError:
                return None
        return a, _full_plan(a)

    coins = [m.coin for m in await _md(md.top, SCAN_TOP, st.min_volume_usd)]
    read = [g for g in await asyncio.gather(*(one(c) for c in coins)) if g]
    got = [g for g in read if g[1]]
    if not read:
        return "Couldn't read the markets right now. Try /signal BTC.", None
    if not got:
        return (f"⏸ <b>NO TRADE</b> · none of the {len(coins)} most traded markets has a setup that passes every rule "
                "right now. /signal BTC for one coin's read.\n\n<i>Not financial advice.</i>"), None
    a, _ = max(got, key=lambda g: g[1].score)
    listing = await _md(venues.resolve, tickers.symbol_args([a.coin])[0], md, venues.client())
    candles = await _md(md.candles, a.coin, tf, 200)
    a.indicators = {**signals.chart_indicators(candles, tf), **a.indicators}
    lead = f"🏆 <b>Most promising now</b> · best of the {len(coins)} most traded markets"
    return await signal_post(db, listing, tf, a, candles, lead, chat_type)


def _chat_type(chat_id: int | str) -> str:
    return "private" if isinstance(chat_id, int) and chat_id > 0 else "group"  # Telegram groups have negative ids (or @names)


async def _best_signal(db: Session, client: Any, chat_id: int | str, reply_to: int | None, md: Any, tf: str) -> str | None:
    text, png = await best_signal(db, md, tf, _chat_type(chat_id))
    if png is None:
        return text
    await client.send_photo(chat_id, png, text, reply_to=reply_to, html=True)
    return None


async def signal_post(db: Session, listing: Any, tf: str, a: Any, candles: list[Any], lead: str = "",
                      chat_type: str = "group") -> tuple[str, bytes | None]:
    """/signal's reply: text (+ the track-record line) and the chart when there's a plan. The plan is logged for grading."""
    from app.tradingos import record
    from app.tradingos.market import INTERVALS

    text = (f"{lead}\n\n" if lead else "") + signal_text(listing, tf, a, candles)
    plan = _full_plan(a)
    if plan is None:
        return text, None
    try:  # the record never blocks a reply
        line = record.record_line(db, record.log_plan(db, listing, tf, plan, chat_type).family)
        text += f"\n{line}" if line else ""
    except Exception:  # noqa: BLE001
        logger.exception("signal idea not logged")
        db.rollback()
    if len(candles) < 2:
        return text, None
    title = f"{listing.tag} · {readout.tf_label(tf)} · {plan.side} {'setup' if plan.full else 'idea'}"
    png = await asyncio.to_thread(chart.render_study, candles, title, listing.label, show=min(120, len(candles)),
                                  interval_ms=INTERVALS[tf] * 1000, levels=(a.indicators.get("support"), a.indicators.get("resistance")),
                                  plan=plan, change=listing.change_24h, source=listing.label)
    return text, png


async def _send_signal(db: Session, client: Any, chat_id: int | str, reply_to: int | None, listing: Any, tf: str, a: Any,
                       candles: list[Any]) -> str | None:
    text, png = await signal_post(db, listing, tf, a, candles, chat_type=_chat_type(chat_id))
    if png is None:
        return text
    plan = _full_plan(a)
    btns = alerts.button(a.coin, plan, tf) if listing.venue == "hyperliquid" and plan else None  # 🔔 Alert me at entry
    await client.send_photo(chat_id, png, text, buttons=btns, reply_to=reply_to, html=True)
    return None


BRIEF_WINDOW = 3  # hours after brief_hour it may still go out (a deploy or restart at 08:00 doesn't lose the day)


async def daily_brief(db: Session, client: Any, md: Any = None, now: Any = None) -> str:
    """tradingos.brief: once a day at brief_hour (UTC) in publish_chat: the market overview, then the most promising /signal
    setup with its chart and the track-record line. The date is saved before sending: at most once a day across restarts."""
    s = signals.settings(db)
    chat = (s.publish_chat or "").strip()
    now = now or utcnow()
    today = now.date().isoformat()
    if not s.brief_enabled or not chat:
        return "off"
    if s.brief_sent_on == today:
        return "already sent today"
    if not s.brief_hour <= now.hour < s.brief_hour + BRIEF_WINDOW:
        return f"waiting for {s.brief_hour:02d}:00 UTC"
    md = md or market.data()
    digest = await _md(markets_digest, md)
    try:
        text, png = await best_signal(db, md, s.timeframe, "brief")
    except Exception:  # noqa: BLE001 - no setup today: the overview still goes out
        logger.exception("daily brief: no setup")
        db.rollback()
        text, png = "", None
    signals.settings(db).brief_sent_on = today
    db.commit()
    try:
        await client.send_message(chat, f"☀️ <b>Daily brief</b> · {now:%a %d %b}\n\n{digest}", html=True)
    except Exception:
        signals.settings(db).brief_sent_on = None  # nothing went out: the next run tries again
        db.commit()
        raise
    if png is None:  # no chart means no setup (best_signal's "Couldn't read the markets"): not posted in the group
        return "sent (overview only)"
    try:
        await _Footed(client, footer(db)).send_photo(chat, png, text, html=True)
    except Exception as e:  # noqa: BLE001 - the overview went out: not retried (no duplicates)
        logger.info("daily brief setup skipped: %s", e)
        return "sent (overview only)"
    return "sent"


async def _chart(db: Session, client: Any, chat_id: int | str, md: Any, args: list[str], reply_to: int | None, note: str | None = None,
                 brief: bool = False) -> str | None:
    """A chart with the compact readout caption; details that don't fit a photo caption follow as one message."""
    from app.tradingos import chartcmd

    try:
        req = chartcmd.parse(args, set((await _md(md.markets))[1]))
    except chartcmd.ChartUsageError as e:
        return esc(str(e))
    if brief:  # "fc btc": the chart, the timeframe buttons and the two links, nothing else (no readout, no signal, no notes)
        png, caption, _ = await asyncio.to_thread(chartcmd.build_chart_response, db, md, req, venues.client(), signals.settings(db))
        raw = getattr(client, "_c", client)  # not the usual footer: fc has its own (no "Signals" line)
        await raw.send_photo(chat_id, png, caption + fc_footer(db), fc_buttons(chartcmd.fc_words(args), req.tf), reply_to=reply_to, html=True)
        return None
    build = chartcmd.build_full  # /chart: chart + readout
    png, caption, details = await asyncio.to_thread(build, db, md, req, venues.client(), signals.settings(db))
    if note:  # on top of the caption when it fits, else first in the details message
        line = f"ℹ️ {esc(note)}\n"
        if len(line) + len(caption) <= chartcmd.CAPTION_LIMIT:
            caption = line + caption
        else:
            details = line + (f"\n{details}" if details else "")
    await client.send_photo(chat_id, png, caption, reply_to=reply_to, html=True)
    if details:
        await client.send_message(chat_id, details, html=True)
    return None


async def _scan(db: Session, md: Any, args: list[str]) -> str:
    """/scan $KAS: every market for the asset (Hyperliquid perp, Binance / Bybit / OKX spot and perpetual, else DEX), most
    liquid first, with the readout of the most liquid one. Real tickers only; exchanges that can't be read are named."""
    sym, _ = tickers.symbol_args([a for a in args if not _is_tf(a)])
    if sym is None:
        return "Usage: /scan $KAS · /scan $BTCUSDT · /scan 0x… (a DEX token)"
    from app.tradingos.chartcmd import _timeframe

    tf = next((_timeframe(a.lower()) or "4h" for a in args if _is_tf(a)), "4h")
    found, errors = await _md(venues.scan, sym, md, venues.client())
    head = f"🔎 <b>{esc(tickers.cashtag(sym.base) if sym.base else sym.display)}</b> · where it trades"
    if not found:
        return head + "\nNo market found." + "".join(f"\n· {esc(v)}: {esc(e)}" for v, e in errors.items())
    rows = []
    for x in found[:6]:
        chg = f" ({x.change_24h:+.1f}%)" if x.change_24h is not None else ""
        vol = f" · vol {readout.usd_short(x.volume_24h)}" if x.volume_24h else ""
        rows.append(f"{esc(x.headline)}  {market.fmt_price(x.price)}{chg}{vol}")
    best = found[0]
    lines = [head, *rows]
    lines += [f"{esc(v)}: {esc(e)}" for v, e in errors.items()]
    try:
        c = await _md(venues.candles, best, tf, 200, md, venues.client())
        r = await _md(readout.read, best, c, tf, md, signals.settings(db))
        lines += ["", f"<b>{esc(best.tag)}</b> {readout.tf_label(tf)} · {esc(best.label)}", f"Trend: {r.trend}",
                  *(f"· {esc(x)}" for x in r.indicators),
                  f"Support: {market.fmt_price(r.support) if r.support else 'n/a'} · Resistance: {market.fmt_price(r.resistance) if r.resistance else 'n/a'}",
                  f"Signal: {r.signal} ({r.rules})" + (f" · {esc(r.signal_note)}" if r.signal == "WAIT" else ""), f"Kiwi Take: {esc(r.take)}"]
    except market.MarketError as e:
        lines += ["", f"Readout unavailable: {esc(str(e))}"]
    chart_hint = best.token_address if best.venue == "dex" else f"{best.tag} {best.venue}"
    lines += [f"Updated: {utcnow():%Y-%m-%d %H:%M} UTC · /chart {esc(chart_hint)} {tf}"]
    return "\n".join(lines)


async def _idea_command(db: Session, chat_id: int, user_id: int, cmd: str, words: list[str], author: str | None) -> str:
    """/idea and /ideas: community opinions, stored apart from signals (ideas.py)."""
    from app.tradingos import ideas

    md = market.data()
    if cmd == "/ideas":
        return await asyncio.to_thread(ideas.latest, db, md, words[0] if words else None)
    m = member_for(db, user_id)
    try:
        idea = await asyncio.to_thread(ideas.submit, db, md, words, user_id, author or (m.username if m else None), chat_id, m.id if m else None)
    except ideas.IdeaError as e:
        return esc(str(e))
    return ideas.confirmation(idea)


# ---------------------------------------------------------------- trading controls (private chat only)
def _acc(db: Session, m: Member) -> Any:
    from app.tradingos import execution

    acc = execution.account(db, m)
    db.commit()
    return acc


def _pos_line(p: Any) -> str:
    f = market.fmt_price
    if p.status == "CLOSED":
        return f"#{p.id} {esc(p.coin)} {p.side} · closed ({esc(p.close_reason or '')}) · ${p.realized_pnl:+,.2f}"
    return (f"#{p.id} <b>{esc(p.coin)}</b> {p.side} {p.size:g} @ {f(p.entry_px or 0)} · stop {f(p.stop)} · TP1 {f(p.tp1)}"
            + (" ✓" if p.tp1_done else "") + f" · TP2 {f(p.tp2)} · {p.status}")


async def _trading_command(db: Session, client: Any, chat_id: int, m: Member, cmd: str, args: list[str]) -> str | None:
    from app.models.trading import TrPosition
    from app.tradingos import execution

    acc = _acc(db, m)
    if cmd == "/risk":
        return "\n".join([f"<b>Mode: {acc.mode}</b>" + (" (real money)" if acc.mode == "LIVE" else ""),
                          f"Max leverage {acc.max_leverage}x (isolated)", f"Max loss per trade ${acc.max_loss_per_trade_usd:,.2f}",
                          f"Max margin per trade {acc.max_allocation_pct:g}% of equity", f"Max open positions {acc.max_positions}",
                          f"Daily loss limit ${acc.daily_loss_limit_usd:,.2f} (today ${execution.daily_pnl(db, acc):+,.2f})",
                          f"Contracts: {esc(acc.allowed_coins) or 'any published signal'}",
                          "New trades: " + ("STOPPED · /resume" if acc.paused else "on · /pause"), "", f"Change them on {SITE}/earn"])
    if cmd == "/auto":
        if not acc.auto_enabled:
            return f"AUTO is off. Turn it on, with your limits, at {SITE}/earn."
        until = f" until {acc.auto_until:%Y-%m-%d %H:%M} UTC" if acc.auto_until else ""
        return f"AUTO is on ({acc.mode}){until}, within your limits (/risk). /pause stops new trades."
    if cmd == "/pause":
        acc.paused, acc.paused_reason = True, "STOP NEW TRADES (Telegram)"
        signals.audit(db, f"m:{m.id}", "stop_new_trades", None, via="telegram")
        db.commit()
        return "⏸ <b>NEW TRADES STOPPED.</b> Open positions keep their stop and targets. /resume to restart."
    if cmd == "/resume":
        if acc.paused_reason and acc.paused_reason.startswith("Protection failed"):
            return f"New trades were stopped by a safety check ({esc(acc.paused_reason)}). Review it on {SITE}/earn first."
        acc.paused, acc.paused_reason = False, None
        signals.audit(db, f"m:{m.id}", "resume_new_trades", None, via="telegram")
        db.commit()
        return "▶️ New trades on again, within your limits (/risk)."
    if cmd in ("/trades", "/close"):
        rows = db.scalars(select(TrPosition).where(TrPosition.account_id == acc.id).order_by(TrPosition.id.desc()).limit(10)).all()
        live = [p for p in rows if p.status in execution.CLOSABLE and p.size > 0]  # a position whose stop failed must be closable here too
        if cmd == "/close":
            if args:
                try:
                    pos = await asyncio.to_thread(execution.close_position, db, m, int(args[0].lstrip("#")))
                except ValueError:
                    return "Usage: /close 12"
                except execution.TradeError as e:
                    return esc(str(e))
                return f"Closed #{pos.id} {esc(pos.coin)} · ${pos.realized_pnl:+,.2f}"
            if not live:
                return "No open bot positions."
            await client.send_message(chat_id, "Close which position? (market, reduce-only; only this bot's position and its own orders)",
                                      [[{"text": f"Close #{p.id} {p.coin} {p.side}", "callback_data": f"cl:{p.id}"}] for p in live])
            return None
        res = execution.results(db, acc)[acc.mode]
        head = (f"<b>{acc.mode} results</b>: {res['trades']} closed · net ${res['net']:+,.2f} · fees ${res['fees']:,.2f}"
                + (f" · win rate {res['win_rate']}%" if res["win_rate"] is not None else ""))
        return head + "\n\n" + ("\n".join(_pos_line(p) for p in rows) if rows else "No bot trades yet. Press ENTER on a signal.")
    return "Unknown command. Send /help."


# ---------------------------------------------------------------- the buttons on signal cards
async def _answer(client: Any, cq: dict[str, Any], uid: int, text: str, alert: bool = False, dm: bool = True) -> None:
    """Answer a button press. Telegram refuses answers that come too late (the bot reads presses in batches, and a preview
    reads the exchange first): then the same text goes to the member privately, so a result is never lost."""
    try:
        await client.answer_callback(cq["id"], text, alert=alert)
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("Button answer refused (%s: %s)", type(exc).__name__, str(exc)[:160])
    if dm:
        try:
            await client.send_message(uid, esc(text), html=True)
        except Exception as exc:  # noqa: BLE001 - the member never pressed Start: nothing else can reach them
            logger.warning("Button result not delivered (%s: %s)", type(exc).__name__, str(exc)[:160])


async def _enter_from_group(db: Session, client: Any, chat_id: int, user_id: int, args: list[str], reply_to: int | None) -> None:
    """/enter replying to a signal card in a group: the preview goes to the member's private chat; the group hears only that."""
    from app.telegram.ingestion import get_bot_state
    from app.tradingos import tgtrade

    name = get_bot_state(db).bot_username or "cryptopricechatbot"
    m = member_for(db, user_id)
    if m is None or not has_access(db, m):
        await client.send_message(chat_id, f"Trading works privately for linked Kiwi Passports: t.me/{name}", reply_to=reply_to)
        return
    try:
        reply = await tgtrade.command(db, client, user_id, m, "/enter", args)
        if reply:
            await client.send_message(user_id, reply, html=True)
    except Exception:  # noqa: BLE001 - never pressed Start: private messages aren't possible yet
        await client.send_message(chat_id, f"Open t.me/{name} and press Start first: trade previews are private.", reply_to=reply_to)
        return
    await client.send_message(chat_id, "Sent you a private preview.", reply_to=reply_to)


async def handle_callback(db: Session, client: Any, cq: dict[str, Any]) -> None:
    data = str(cq.get("data") or "")
    uid = int((cq.get("from") or {}).get("id") or 0)
    if data[:3] in ("mo:", "mn:", "ca:", "pr:") and uid:  # Telegram trading's Confirm / Cancel (tgtrade.py)
        from app.tradingos import tgtrade

        m = member_for(db, uid)
        if m is None:
            await _answer(client, cq, uid, "Connect your Kiwi Passport first.", alert=True)
            return
        await _answer(client, cq, uid, "Working…", dm=False)
        await client.send_message(uid, await tgtrade.callback(db, client, m, uid, data), html=True)
        return
    if data[:3] in ("cf:", "cx:", "cl:") and uid:
        await _trade_callback(db, client, cq, uid, data)
        return
    if data.startswith("fc:") and uid:  # a timeframe button under an "fc" chart
        await _fc_callback(db, client, cq, uid, data)
        return
    if data.startswith("cm:") and uid:  # the committee's views (committeecmd.py)
        from app.tradingos import committeecmd

        await committeecmd.callback(db, client, cq)
        return
    if data.startswith("pa:") and uid:  # 🔔 Alert me at entry (under /signal)
        await alerts.callback(db, client, cq, uid, data)
        return
    if not data.startswith("sig:") or not uid:
        return
    try:
        _, sid, action = data.split(":")
        sig = db.get(TsSignal, int(sid))
    except ValueError:
        return
    if sig is None or action not in ("enter", "reject", "auto"):
        await _answer(client, cq, uid, "This signal isn't available.", dm=False)
        return
    if sig.status in CLOSED:
        await _answer(client, cq, uid, f"Signal #{sig.number:03d} is {signals.STATUS_LABEL[sig.status].lower()}.")
        return
    m = member_for(db, uid)
    verified = has_access(db, m)
    if not verified or m is None:
        await _answer(client, cq, uid, f"Connect your Kiwi Passport (Rank {_min_rank(db)}) first: {SITE}/earn", alert=True)
        return
    if action == "enter":
        from app.tradingos import tgtrade

        why = await tgtrade.gate_for_entry(db, m)  # the Banter group's referral, from Hyperliquid's own record
        if why:
            await _answer(client, cq, uid, why[:195], alert=True)
            return
        await _enter(db, client, cq, m, uid, sig)
        return
    db.add(TsSignalAction(signal_id=sig.id, who=f"tg:{uid}", member_id=m.id if m else None, telegram_user_id=uid, action=action, verified=True))
    try:
        db.commit()
    except IntegrityError:  # a double tap / a repeated callback: one answer per person per signal
        db.rollback()
        await _answer(client, cq, uid, "Already noted.", dm=False)
        return
    signals.audit(db, f"tg:{uid}", f"button_{action}", sig)
    db.commit()
    text = {"reject": f"Skipped #{sig.number:03d} for you. Nothing was traded.",
            "auto": f"AUTO is set on {SITE}/earn with your own limits (mode, size, max loss, duration). Nothing was enabled from this button."}[action]
    await _answer(client, cq, uid, text, alert=action != "reject")


async def _enter(db: Session, client: Any, cq: dict[str, Any], m: Member, uid: int, sig: TsSignal) -> None:
    """ENTER: a private preview sized to the member's limits, with Confirm / Cancel. Nothing is sent to the exchange yet."""
    from app.tradingos import execution

    try:
        it = await asyncio.to_thread(execution.preview, db, m, sig)
    except execution.TradeError as e:
        await _answer(client, cq, uid, f"#{sig.number:03d} not entered: {str(e)[:170]}", alert=True)
        return
    except market.MarketError:
        await _answer(client, cq, uid, "Couldn't read Hyperliquid right now. Try again in a moment.", alert=True)
        return
    try:
        await client.send_message(uid, esc(execution.preview_text(sig, it)), [[{"text": "✅ Confirm", "callback_data": f"cf:{it.token}"},
                                                                                {"text": "Cancel", "callback_data": f"cx:{it.token}"}]], html=True)
    except Exception:  # noqa: BLE001 - the member never pressed Start in the bot: private messages aren't possible yet
        from app.telegram.ingestion import get_bot_state

        name = get_bot_state(db).bot_username or "cryptopricechatbot"
        await _answer(client, cq, uid, f"Open t.me/{name} and press Start, then ENTER again: your preview is private.", alert=True, dm=False)
        return
    await _answer(client, cq, uid, "Preview sent to you privately. Confirm there within 5 minutes.", dm=False)


async def _trade_callback(db: Session, client: Any, cq: dict[str, Any], uid: int, data: str) -> None:
    """Confirm / Cancel on a private preview, and Close on a position. Only the member's own intents and positions."""
    from app.tradingos import execution

    m = member_for(db, uid)
    if m is None:
        await _answer(client, cq, uid, "Connect your Kiwi Passport first.", alert=True)
        return
    kind, arg = data[:2], data[3:]
    try:
        if kind == "cx":
            execution.cancel_intent(db, m, arg)
            await _answer(client, cq, uid, "Cancelled. Nothing was traded.")
            return
        if kind == "cl":
            await _answer(client, cq, uid, "Closing…", dm=False)
            pos = await asyncio.to_thread(execution.close_position, db, m, int(arg))
            await client.send_message(uid, f"Closed #{pos.id} {esc(pos.coin)} · ${pos.realized_pnl:+,.2f}", html=True)
            return
        await _answer(client, cq, uid, "Sending the order…", dm=False)  # a refused answer never stops the order or its result
        pos = await asyncio.to_thread(execution.confirm, db, m, arg)
    except (execution.TradeError, ValueError) as e:
        await client.send_message(uid, esc(str(e)), html=True)
        return
    except market.MarketError:
        await client.send_message(uid, "Couldn't read Hyperliquid right now. Nothing was sent.")
        return
    f = market.fmt_price
    if pos.status == "OPEN":
        text = (f"<b>{pos.mode} · filled</b> {esc(pos.coin)} {pos.side} {pos.size:g} @ {f(pos.entry_px or 0)}\n"
                f"Stop {f(pos.stop)} · TP1 {f(pos.tp1)} (half) · TP2 {f(pos.tp2)} · {pos.leverage}x isolated\n/trades · /close")
    else:
        text = f"<b>{pos.mode} · {pos.status}</b> {esc(pos.error or '')}\nNew trades were stopped for safety. See /trades."
    await client.send_message(uid, text, html=True)
    sig = db.get(TsSignal, pos.signal_id)
    if sig is not None:
        await refresh(db, client, sig)


# ---------------------------------------------------------------- publishing
def _card(db: Session, sig: TsSignal) -> str:
    return signals.card(sig, signals.participation(db, sig), performance.record_line(performance.record_of(db, sig)))


async def publish(db: Session, client: Any, sig: TsSignal, actor: str) -> TsSignal:
    """Post the signal card (chart + caption + buttons) to the signal group. Idempotent: a published signal isn't reposted."""
    s = signals.settings(db)
    if sig.tg_message_id:
        return sig
    md = market.data()
    candles = await _md(md.candles, sig.coin, sig.timeframe, 120)
    png = await asyncio.to_thread(chart.render, candles, f"#{sig.number:03d} {tickers.cashtag(sig.coin)} · {sig.side}", (sig.entry_lo, sig.entry_hi),
                                  sig.stop, sig.target, f"{sig.timeframe} · {sig.strategy}")
    if sig.status == "PENDING_REVIEW":
        sig.status = "WAITING"
    try:
        msg = await client.send_photo(s.publish_chat, png, with_referral(db, _card(db, sig), "card"),
                                      buttons(sig, tracked_url(db, "card")))
    except Exception as e:  # noqa: BLE001
        sig.tg_error = f"{type(e).__name__}: {str(e)[:200]}"
        signals.audit(db, actor, "publish_failed", sig, error=sig.tg_error)
        db.commit()
        raise
    sig.tg_chat, sig.tg_message_id, sig.tg_error = s.publish_chat, int(msg["message_id"]), None
    sig.published_at, sig.approved_by = utcnow(), actor
    signals.audit(db, actor, "published", sig, chat=s.publish_chat)
    from app.tradingos import brain

    brain.log(db, "signal", f"Signal #{sig.number:03d} {sig.coin} {sig.side} published to {s.publish_chat}", signal_id=sig.id, coin=sig.coin,
              source="telegram", data={"chat": s.publish_chat, "message_id": sig.tg_message_id})
    db.commit()
    return sig


async def refresh(db: Session, client: Any, sig: TsSignal) -> None:
    """Update the existing card in place (status, price, participation, model result)."""
    if not (sig.tg_chat and sig.tg_message_id):
        return
    try:
        await client.edit_caption(sig.tg_chat, sig.tg_message_id, with_referral(db, _card(db, sig), "card"),
                                  buttons(sig, tracked_url(db, "card")))
    except Exception as e:  # noqa: BLE001 - "message is not modified" and friends: the card stays as it was
        logger.info("card update skipped for #%s: %s", sig.number, e)


def announcement(sig: TsSignal, before: str) -> str | None:
    """A short group notification for a signal opening or closing (a NEW message, so members get notified; the card itself is
    edited in place). Every number is the signal record's. Results are the model's (unlevered, after fees), not anyone's fills."""
    tag = f"#{sig.number:03d} {tickers.cashtag(sig.coin)} {sig.side}"
    if sig.strategy == KITCHEN:
        return kitchen_announcement(sig, before)
    manual = " · ✍️ Manual setup" if sig.strategy in ("test", "manual") else ""
    now = f" @ {market.fmt_price(sig.last_price)}" if sig.last_price else ""
    res = f"\nSignal result: {sig.model_pnl_pct:+.2f}% (no leverage, after fees)" if sig.model_pnl_pct is not None else ""
    watch = f"\n\n👀 Watch the Kiwi: {SITE.replace('https://', '')}/earn"
    if sig.status == "ACTIVE" and before in ("WAITING", "PENDING_REVIEW"):
        return (f"{'🟢' if sig.side == 'LONG' else '🔴'} {tag} is OPEN{manual}\nPrice reached the entry zone "
                f"{market.fmt_price(sig.entry_lo)} – {market.fmt_price(sig.entry_hi)}{now}.\n"
                f"🛑 Stop {market.fmt_price(sig.stop)} · ✅ TP1 {market.fmt_price(sig.target)}{watch}")
    if sig.status == "TARGET_HIT":
        return f"✅ {tag} CLOSED · TARGET HIT{now}{manual}{res}\n\nKIWI EATS TONIGHT 🥝{watch}"
    if sig.status == "STOP_HIT":
        return f"🛑 {tag} CLOSED · STOP HIT{now}{manual}{res}\n\nTHE BIRD LIVES ANOTHER DAY 💀{watch}"
    if sig.status == "CLOSED":
        return f"⏱ {tag} CLOSED after 48 hours{now}{manual}{res}{watch}"
    if sig.status == "EXPIRED" and before in ("WAITING", "PENDING_REVIEW") and sig.published_at:
        return f"⌛ {tag} EXPIRED{manual}\nThe entry zone was never reached, so nothing opened."
    return None


KITCHEN = "intraday_15m_v1"  # kitchen.STRATEGY (kept literal: kitchen imports the signal pipeline this module belongs to)
MODE_LABEL = {"PAPER": "🧪 PAPER · simulated, no real money", "TESTNET": "🧪 TESTNET · test money", "LIVE": "💵 LIVE · real money"}


def _model_label(sig: TsSignal) -> str:
    from app.tradingos import intraday

    an = sig.analysis if isinstance(sig.analysis, dict) else {}
    model = an.get("model")
    tf = "15m degen" if an.get("mode") == "degen" else "15m / 30m intraday"
    return f"{tf} · {intraday.LABEL.get(model, model)}" if model else sig.strategy


def kitchen_announcement(sig: TsSignal, before: str) -> str | None:
    """The kitchen's group notifications. Every number is the signal record's; results are the signal's own (unlevered,
    after fees), never anyone's account: members' trades follow their own settings."""
    tag = f"#{sig.number:03d} {tickers.cashtag(sig.coin)} {sig.side}"
    watch = f"\n\n👀 Watch the Kiwi cook: {SITE.replace('https://', '')}/earn"
    now = f" @ {market.fmt_price(sig.last_price)}" if sig.last_price else ""
    if sig.status == "ACTIVE" and before in ("WAITING", "PENDING_REVIEW"):
        an = sig.analysis if isinstance(sig.analysis, dict) else {}
        entry = (f"⚡ Opened at market {market.fmt_price((sig.entry_lo + sig.entry_hi) / 2)}" if an.get("market_entry") else
                 f"Entry {market.fmt_price(sig.entry_lo)} – {market.fmt_price(sig.entry_hi)}{now}")
        hold = f" · max {an['max_hold_min']} min, then closed at market" if an.get("max_hold_min") else ""
        brain = (f"🧠 Kiwi ML: {an['ml_p'] * 100:.0f}% estimated chance of profit{'' if an.get('ml_filter') else ' (advisory)'}\n"
                 if isinstance(an.get("ml_p"), (int, float)) else "")
        rt = an.get("risk_team") if isinstance(an.get("risk_team"), dict) else None
        brain += f"🛡️ AI Risk Team: {rt['reason']}\n" if rt and rt.get("verdict") == "APPROVE" else ""
        return (f"🥝 ORDER UP! {tickers.cashtag(sig.coin)} {sig.side} is served!\n{tag} · {_model_label(sig)}\n"
                f"{entry}{hold}\n"
                f"🛑 Stop {market.fmt_price(sig.stop)} · ✅ TP1 {market.fmt_price(sig.target)}\n{brain}"
                f"Signal, not an account: your trades follow your own Earn settings.{watch}")
    if sig.status in ("TARGET_HIT", "STOP_HIT", "CLOSED"):
        how = {"TARGET_HIT": "TARGET HIT ✅", "STOP_HIT": "STOP HIT 🛑", "CLOSED": "time's up ⏱ closed at market"}[sig.status]
        res = f"\nSignal result: {sig.model_pnl_pct:+.2f}% (no leverage, after fees)" if sig.model_pnl_pct is not None else ""
        return f"🍽️ KIWI FINISHED COOKING! {tag} · {how}{now}{res}{watch}"
    if sig.status == "EXPIRED" and before in ("WAITING", "PENDING_REVIEW") and sig.published_at:
        return f"🔥 {tag} burnt: the entry zone was never reached, so nothing opened."
    return None


ANNOUNCE_WINDOW = timedelta(hours=2)  # only recent position changes are announced (never a backlog after a deploy)


def position_open_text(pos: Any, sig: TsSignal | None) -> str:
    return (f"🥝 ORDER UP! {tickers.cashtag(pos.coin)} {pos.side} is served!\n{MODE_LABEL.get(pos.mode, pos.mode)}\n"
            f"Entry {market.fmt_price(pos.entry_px or 0)} · size {pos.size:g} · {pos.leverage}x\n"
            f"🛑 Stop {market.fmt_price(pos.stop)} · ✅ TP1 {market.fmt_price(pos.tp1)} · TP2 {market.fmt_price(pos.tp2)}\n"
            + ("Your own trade (/open)" if sig is not None and sig.status == "MANUAL" else
               f"Strategy: {_model_label(sig) if sig else '—'}" + (f" · signal #{sig.number:03d}" if sig else ""))
            + f"\nMax loss at the stop ${pos.max_loss_usd:,.2f}\n\n{SITE}/earn")


def position_close_text(pos: Any, sig: TsSignal | None) -> str:
    sim = " (simulated)" if pos.mode == "PAPER" else ""
    return (f"🍽️ KIWI FINISHED COOKING! {tickers.cashtag(pos.coin)} {pos.side} closed ({pos.close_reason or 'closed'})\n{MODE_LABEL.get(pos.mode, pos.mode)}\n"
            f"Entry {market.fmt_price(pos.entry_px or 0)} → exit {market.fmt_price(pos.exit_px or 0)}\n"
            f"Realized P&L after fees{sim}: ${pos.realized_pnl:+,.2f} (fees ${pos.fees:,.2f})\n"
            + ("Your own trade (/open)" if sig is not None and sig.status == "MANUAL" else f"Strategy: {_model_label(sig) if sig else '—'}")
            + f"\n\n{SITE}/earn")


def due_position_announcements(db: Session, now: Any = None) -> list[tuple[Any, str, str]]:
    """(position, "open" | "close", text) for the members' private chats, each marked as announced here (at most once:
    marked before sending, so a retry can never send it twice)."""
    from app.models.trading import TrPosition

    now = now or utcnow()
    since = now - ANNOUNCE_WINDOW
    out = []
    rows = db.scalars(select(TrPosition).where(TrPosition.entry_px.is_not(None), TrPosition.status.in_(("OPEN", "CLOSED")),
                                               TrPosition.created_at >= since, TrPosition.announced_open_at.is_(None)))
    for pos in rows:
        pos.announced_open_at = now
        if pos.status == "OPEN":
            out.append((pos, "open", position_open_text(pos, db.get(TsSignal, pos.signal_id))))
    for pos in db.scalars(select(TrPosition).where(TrPosition.status == "CLOSED", TrPosition.closed_at >= since,
                                                   TrPosition.announced_close_at.is_(None))):
        pos.announced_close_at = now
        pos.announced_open_at = pos.announced_open_at or now
        if pos.entry_px is not None:
            out.append((pos, "close", position_close_text(pos, db.get(TsSignal, pos.signal_id))))
    db.commit()
    return out


def due_skip_announcements(db: Session, now: Any = None) -> list[tuple[Any, str]]:
    """(account, text) for each AUTO skip recorded in the last ANNOUNCE_WINDOW, marked as told before sending (at most once)."""
    from app.models.trading import TrAccount, TrIntent

    now = now or utcnow()
    out = []
    for it in db.scalars(select(TrIntent).where(TrIntent.status == "SKIPPED", TrIntent.created_at >= now - ANNOUNCE_WINDOW)):
        plan = dict(it.plan or {})
        if plan.get("told_at") or not plan.get("skipped"):
            continue
        plan["told_at"] = now.isoformat()
        it.plan = plan  # a new dict, so the JSON change is saved
        sig, acc = db.get(TsSignal, it.signal_id), db.get(TrAccount, it.account_id)
        if sig is None or acc is None:
            continue
        out.append((acc, f"⏭️ AUTO skipped {tickers.cashtag(sig.coin)} {sig.side} #{sig.number:03d} ({sig.timeframe})\n"
                         f"Why: {str(plan['skipped'])[:300]}\nYour settings: {SITE}/earn"))
    db.commit()
    return out


async def announce_skips(db: Session, client: Any) -> int:
    """Tell each member, privately, when AUTO didn't take a signal and why (their timeframe, Kiwi config, limits...)."""
    sent = 0
    for acc, text in due_skip_announcements(db):
        ident = db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == acc.member_id, LinkedIdentity.platform == "telegram",
                                                       LinkedIdentity.status == "active"))
        if ident is None:
            continue
        try:
            await client.send_message(int(ident.platform_user_id), esc(with_referral(db, text)), html=True)
            sent += 1
        except Exception as e:  # noqa: BLE001 - the member never pressed Start: logged, not retried (no duplicates)
            logger.info("AUTO skip notice for account %s skipped: %s", acc.id, e)
    return sent


async def announce_positions(db: Session, client: Any) -> int:
    """ORDER UP / KIWI FINISHED COOKING in each member's private chat with the bot (accounts are never posted in groups)."""
    from app.models.trading import TrAccount

    sent = 0
    for pos, kind, text in due_position_announcements(db):
        acc = db.get(TrAccount, pos.account_id)
        ident = db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == acc.member_id, LinkedIdentity.platform == "telegram",
                                                       LinkedIdentity.status == "active")) if acc else None
        if ident is None:
            continue
        try:
            await client.send_message(int(ident.platform_user_id), esc(with_referral(db, text)), html=True)
            sent += 1
        except Exception as e:  # noqa: BLE001 - the member never pressed Start: logged, not retried (no duplicates)
            logger.info("position %s %s announcement skipped: %s", pos.id, kind, e)
    return sent


UNPROTECTED_KEY = "trading:unprotected:"  # + position id: the owner alert's key (app.alerts: at most once per 6 h while it lasts)


def unprotected_positions(db: Session) -> list[Any]:
    """Testnet / live bot positions still held with no confirmed stop-loss: the stop was refused and the emergency close didn't
    fill (PROTECTION_FAILED, size left, no resting stop)."""
    from app.models.trading import TrOrder, TrPosition

    rows = db.scalars(select(TrPosition).where(TrPosition.status == "PROTECTION_FAILED", TrPosition.mode != "PAPER", TrPosition.size > 0)).all()
    return [p for p in rows if db.scalar(select(TrOrder.id).where(TrOrder.position_id == p.id, TrOrder.kind == "sl",
                                                                  TrOrder.status == "RESTING").limit(1)) is None]


def unprotected_text(pos: Any, closes: int, exhausted: bool) -> str:
    from app.tradingos import execution

    head = (f"🚨 NO STOP-LOSS: your bot position {tickers.cashtag(pos.coin)} {pos.side} ({MODE_LABEL.get(pos.mode, pos.mode)}) "
            f"is open on Hyperliquid without a stop-loss.\nWhy: {(pos.error or 'the stop-loss was refused')[:300]}\n")
    if exhausted:
        body = (f"The bot tried to close it {closes} times and has stopped trying to close it. It keeps trying to place the stop-loss "
                "every minute.\n")
    else:
        body = (f"The bot tries to place the stop-loss again every minute, and to close the position ({closes} of "
                f"{execution.EMERGENCY_RETRIES} close attempts so far).\n")
    return (head + body + "To be safe, close it yourself now: app.hyperliquid.xyz → Positions → Close.\n"
            f"New bot trades are stopped on your account.\n\n{SITE}/earn")


async def announce_unprotected(db: Session, client: Any) -> dict[str, int]:
    """A position left without a stop-loss is never silent: the member gets a private message (once when it happens, once more
    when the emergency closes are used up), and the owner an alert for LIVE money (again every 6 h while it lasts, and one
    'recovered' message when it's protected or closed). Marked before sending, so a retry can never send a member twice."""
    from app import alerts as owner_alerts
    from app.models.alerts import OwnerAlert
    from app.models.trading import TrAccount, TrPosition
    from app.models.tradingos import TsAudit
    from app.tradingos import execution

    out = {"members_told": 0, "owner_alerts": 0, "owner_recovered": 0}
    open_ids = set()
    for pos in unprotected_positions(db):
        open_ids.add(pos.id)
        acc = db.get(TrAccount, pos.account_id)
        if acc is None:
            continue
        closes = execution._closes(db, pos)
        exhausted = closes >= execution.EMERGENCY_RETRIES
        text = unprotected_text(pos, closes, exhausted)
        action = "unprotected_told_final" if exhausted else "unprotected_told"
        told = db.scalar(select(TsAudit.id).where(TsAudit.actor == f"account:{acc.id}", TsAudit.action == action,
                                                 TsAudit.signal_id == pos.signal_id).limit(1))
        if told is None:
            db.add(TsAudit(actor=f"account:{acc.id}", action=action, signal_id=pos.signal_id, details={"position": pos.id, "mode": pos.mode}))
            db.commit()
            ident = db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == acc.member_id, LinkedIdentity.platform == "telegram",
                                                           LinkedIdentity.status == "active"))
            if ident is not None and client is not None:
                try:
                    await client.send_message(int(ident.platform_user_id), esc(text), html=True)
                    out["members_told"] += 1
                except Exception as e:  # noqa: BLE001 - the member never pressed Start: logged (the owner alert still goes out)
                    logger.info("unprotected position %s notice skipped: %s", pos.id, e)
        if pos.mode == "LIVE":
            member = db.get(Member, acc.member_id)
            who = f"@{member.username}" if member is not None else f"member {acc.member_id}"
            otext = (f"Price Chat alert: LIVE position without a stop-loss\nMember: {who} (account {acc.id}), position {pos.id}\n"
                     f"{pos.coin} {pos.side} size {pos.size:g} · entry {pos.entry_px or 0:g} · stop {pos.stop:g}\n"
                     f"Close attempts: {closes} of {execution.EMERGENCY_RETRIES}" + (" (used up; only the stop is retried)" if exhausted else "")
                     + f"\nWhy: {(pos.error or '')[:300]}")
            out["owner_alerts"] += await owner_alerts.notify_owner(db, f"{UNPROTECTED_KEY}{pos.id}", otext, open_=True)
    for row in db.scalars(select(OwnerAlert).where(OwnerAlert.key.like(f"{UNPROTECTED_KEY}%"), OwnerAlert.open.is_(True))).all():
        rest = row.key[len(UNPROTECTED_KEY):]
        if not rest.isdigit() or int(rest) in open_ids:
            continue
        pos = db.get(TrPosition, int(rest))
        state = "closed" if pos is None or pos.status == "CLOSED" or pos.size <= 0 else "protected by a stop-loss again"
        coin = f"{pos.coin} {pos.side} " if pos is not None else ""
        if await owner_alerts.notify_owner(db, f"{row.key}:recovered", f"Price Chat: recovered\nLIVE position {rest} {coin}is {state}.",
                                           cooldown=False):
            out["owner_recovered"] += 1
            owner_alerts._close(db, row.key)
    return out


MAIN_CHAT_STATUSES = ("ACTIVE", "TARGET_HIT", "STOP_HIT", "CLOSED")  # opens and closes; "expired / burnt" stays in the signal group


def card_link(sig: TsSignal) -> str | None:
    """t.me link to the signal's card when the signal group is public (@name); a private group has no shareable link."""
    chat = str(sig.tg_chat or "")
    return f"https://t.me/{chat.lstrip('@')}/{sig.tg_message_id}" if chat.startswith("@") and sig.tg_message_id else None


async def announce(db: Session, client: Any, sig: TsSignal, before: str) -> bool:
    """The open / close notification, sent to the signal group (as a reply to its card) AND the main community chat
    (Settings → main chat) at the same time. Each send is independent: one failing never blocks or repeats the other.
    A failure is logged, not retried (no duplicates)."""
    text = announcement(sig, before)
    if not text or not (sig.tg_chat and sig.tg_message_id):
        return False
    main = (signals.settings(db).main_chat or "").strip()
    sends: list[tuple[str, Any]] = [("group", client.send_message(sig.tg_chat, with_referral(db, text), reply_to=sig.tg_message_id))]
    if main and main.lower() != str(sig.tg_chat).lower() and sig.status in MAIN_CHAT_STATUSES:
        link = card_link(sig)
        sends.append(("main", client.send_message(main, with_referral(db, text + (f"\n📊 Signal card: {link}" if link else "")))))
    results = await asyncio.gather(*(c for _, c in sends), return_exceptions=True)
    sent = []
    for (where, _), r in zip(sends, results):
        if isinstance(r, Exception):
            logger.info("announcement to the %s chat skipped for #%s: %s", where, sig.number, r)
        else:
            sent.append(where)
    if not sent:
        return False
    signals.audit(db, "tracker", "announced", sig, status=sig.status, chats=sent)
    db.commit()
    return True


MENU_PRIVATE = [("rules", "The group rules"), ("market", "One perp: price, funding, OI, e.g. /market BTC"), ("open", "Trade: /open BTC long 100 5x (preview first)"),
                ("balance", "Your balance"), ("orders", "Open orders"), ("settings", "Your trading limits and mode"),
                ("markets", "Market overview · /markets weekly"), ("price", "Price, e.g. /price BTC ETH SOL"),
                ("indicator", "RSI, MACD, EMAs, e.g. /indicator SUI"), ("scan", "Where a coin trades, e.g. /scan KAS"),
                ("chart", "Chart, e.g. /chart BTC 15m rsi bb oi"), ("signals", "Latest signals"), ("signal", "Analyze a coin, e.g. /signal DOGE"),
                ("committee", "AI Investment Committee, e.g. /committee BTC 4h"), ("debate", "Bull vs bear, e.g. /debate ETH"),
                ("research", "Full committee report, e.g. /research SOL"),
                ("record", "How /signal ideas played out"), ("idea", "Share an idea: /idea BTC long tp 72000"), ("ideas", "Community ideas"), ("status", "Your account"),
                ("positions", "Your bot and Hyperliquid positions"), ("pnl", "Your Hyperliquid P&L"), ("trades", "Your bot trades and results"),
                ("risk", "Your limits · /risk BTC: committee risk review"), ("pause", "STOP NEW TRADES"), ("close", "Close a bot position"), ("connect", "Connect Hyperliquid"),
                ("vpn", "Kiwi VPN: your devices and configs"), ("help", "All commands")]
MENU_GROUP = [("rules", "The group rules"), ("markets", "Market overview · /markets weekly"), ("price", "Price, e.g. /price BTC ETH SOL"),
              ("indicator", "RSI, MACD, EMAs, e.g. /indicator SUI"), ("scan", "Where a coin trades, e.g. /scan KAS"),
              ("chart", "Chart, e.g. /chart BTC 15m rsi bb oi"), ("signals", "Latest signals"), ("signal", "Analyze a coin, e.g. /signal DOGE"),
              ("committee", "AI Investment Committee, e.g. /committee BTC 4h"), ("debate", "Bull vs bear, e.g. /debate ETH"),
              ("research", "Full committee report, e.g. /research SOL"), ("risk", "Committee risk review, e.g. /risk BTC"),
              ("record", "How /signal ideas played out"), ("idea", "Share an idea: /idea BTC long tp 72000"), ("ideas", "Community ideas"), ("help", "All commands")]
_menu_set = False


async def ensure_menu(client: Any) -> None:
    """Register the "/" command menus once per process (private chats: everything; groups: the public commands)."""
    global _menu_set
    if _menu_set:
        return
    await client.set_commands(MENU_PRIVATE, "default")
    await client.set_commands(MENU_GROUP, "all_group_chats")
    _menu_set = True
