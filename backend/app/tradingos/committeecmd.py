"""Telegram commands of the Kiwi AI Investment Committee (stage 2): /committee, /debate, /research, /risk <coin> and
/committee status, plus the buttons under their answers (callback data cm:<run id>:full|bull|bear|risk|chart|refresh|sum).

Routing follows the bot's rules (bot.py): these are market-analysis commands with no account data, so like /signal they
are answered for anyone, in groups too (/risk with no coin stays the private account command). Replies in the main
community chat are tidied away after a few minutes like every other bot message there (autodelete.py); the Full Report,
which is long, goes to the presser's private chat when it's asked for in the main chat. Buttons pressed in a group never
switch the shared message: the Bull / Bear / Risk / Full view goes to the presser privately (an alert asks them to press
Start in the bot first when Telegram refuses) and the group keeps the summary; in a private chat the message is edited.
The chart button reuses /chart (and Bot Chat routing). The bot channel + referral footer goes under the finished answer when it fits.

A run takes seconds on the rules backend and up to ~3 min with Vortex, so the command answers at once with a placeholder
and a watcher (its own database session and Telegram client, never the ingestion batch's) polls the cm_runs row and edits
the placeholder into the answer when the run is done or failed. A run already done (the 15-minute cache) is answered
directly. Limits: the committee's daily limit per Telegram user / linked Kiwi Passport (orchestrator.start_run; cached runs
are free) and the "committee" rate-limit bucket per Telegram user. Research only: nothing here trades.
"""

import asyncio
import logging
import re
import time
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.committee import orchestrator
from app.committee import telegram_view as tv
from app.db.base import utcnow

logger = logging.getLogger(__name__)

COMMANDS = {"/committee", "/debate", "/research"}  # and "/risk <coin>" (bot.py routes it here)
VIEW_OF = {"/committee": "sum", "/debate": "debate", "/research": "full", "/risk": "risk"}
VIEW_LABEL = {"full": "Full Report", "bull": "Bull Case", "bear": "Bear Case", "risk": "Risk Review", "debate": "Bull vs Bear"}
TIMEFRAMES = orchestrator.TIMEFRAMES  # 15m 1h 4h 1d
USAGE = ("Usage: /committee BTC · /committee DOGE 4h · /debate ETH · /research SOL · /risk BTC · /committee status\n"
         "Timeframes: 15m 1h 4h 1d (default 1h). Research only, nothing is traded.")
POLL_SECONDS = 1.5
WATCH_FOR = 360.0  # the run may queue behind others, then take ~3 min with Vortex
RUNTIME: Any = None  # the app's AppRuntime (main.py sets it): the model provider and gate for the router backend
CLIENT_FACTORY: Callable[[], Any] | None = None  # tests inject a fake Telegram client
_WATCHERS: set[asyncio.Task] = set()
_TF = re.compile(r"^\d+[a-z]$")


# ---------------------------------------------------------------- parsing
def parse(args: list[str]) -> tuple[list[str], str, str | None]:
    """(coin words, timeframe, error). '/committee doge 4h', '/committee DOGE 4H', '/committee $TAO okx 1h'."""
    tfs = [a.lower() for a in args if _TF.match(a.lower())]
    words = [a for a in args if not _TF.match(a.lower())]
    if len(tfs) > 1:
        return words, "1h", "One timeframe please: 15m, 1h, 4h or 1d."
    tf = tfs[0] if tfs else "1h"
    if tf not in TIMEFRAMES:
        return words, tf, f"Timeframe {tf} isn't supported: use 15m, 1h, 4h or 1d."
    if not words:
        return words, tf, USAGE
    from app.tradingos import tickers

    sym, _ = tickers.symbol_args(words)
    if sym is None or len(words) > 3:
        return words, tf, f"I can't read {' '.join(words)[:40]} as a coin.\n{USAGE}"
    return words, tf, None


async def resolve(words: list[str]) -> Any:
    """The market the run will read (venues.resolve, as /signal does): raises venues.NotFound / Ambiguous before a run is
    spent or counted."""
    from app.tradingos import market, tickers, venues

    sym, _ = tickers.symbol_args(words)
    return await asyncio.to_thread(venues.resolve, sym, market.data(), venues.client())


def _ambiguous_text(e: Any, tf: str) -> str:
    from app.tradingos.readout import usd_short

    lines = [tv.esc(str(e))]
    for o in e.options[:6]:
        lines.append(f"· {tv.esc(o.pool_name or o.base)} · {tv.esc(o.label)} · liquidity {usd_short(o.liquidity)}\n"
                     f"  <code>{tv.esc(o.token_address or o.symbol)}</code>")
    if e.options:
        lines.append(f"Pick one: /committee {tv.esc(e.options[0].token_address or e.options[0].symbol)} {tf}")
    return "\n".join(lines)


# ---------------------------------------------------------------- the Telegram client of a watcher
def _client() -> Any:
    if CLIENT_FACTORY is not None:
        return CLIENT_FACTORY()
    from app.config.settings import get_settings
    from app.telegram.service import make_client

    return make_client(get_settings(), getattr(getattr(RUNTIME, "telegram", None), "transport", None))


def _raw(client: Any) -> Any:
    return getattr(client, "_c", client)  # bot._Footed → the plain client (a message that will be edited gets no footer yet)


def _with_footer(text: str, foot: str) -> str:
    return text + foot if foot and len(text + foot) <= tv.TG_MAX else text


def _main_chat(db: Session, chat_id: Any, chat_username: str | None) -> bool:
    from app.tradingos import bot

    return isinstance(chat_id, int) and chat_id < 0 and bot._is_main_chat(db, chat_id, chat_username)


# ---------------------------------------------------------------- commands
async def command(db: Session, client: Any, chat_id: int, user_id: int, cmd: str, args: list[str], reply_to: int | None = None,
                  chat_username: str | None = None, foot: str = "") -> str | None:
    """Returns a short reply for bot.handle_command to send (usage, errors, status), or None when it answered itself."""
    if cmd == "/committee" and args and args[0].lower() == "status":
        return await status(db, user_id)
    if not args:
        return USAGE
    words, tf, err = parse(args)
    if err:
        return tv.esc(err)
    from app.security.rate_limit import LIMITS
    from app.tradingos import venues

    wait = LIMITS["committee"].hit(f"tg:{user_id}")
    if wait is not None:
        return f"🥝 Easy: one committee at a time. Try again in {wait} s."
    try:
        listing = await resolve(words)
    except venues.Ambiguous as e:
        return _ambiguous_text(e, tf)
    except venues.NotFound as e:
        return f"{tv.esc(str(e))}\n{tv.esc(USAGE)}"
    shown = getattr(listing, "tag", None) or " ".join(words).upper()
    view = VIEW_OF.get(cmd, "sum")
    main = _main_chat(db, chat_id, chat_username)
    who = {"tg_user_id": int(user_id), "chat_id": int(chat_id) if isinstance(chat_id, int) else None}
    try:
        rid = orchestrator.start_run(db, " ".join(words), tf, requested_by=who, provider=getattr(RUNTIME, "provider", None),
                                     gate=getattr(RUNTIME, "gate", None), market=market_of(listing))
    except orchestrator.LimitReached as e:
        return limit_text(db, who, e.message)
    except orchestrator.CommitteeError as e:
        return f"⚠️ {tv.esc(e.message)}"
    db.expire_all()
    row = orchestrator.find(db, rid)
    if row is not None and row.status == "done" and row.result:  # the 15-minute cache: answered at once
        await deliver(_raw(client), chat_id, None, row.result, view, reply_to, foot, user_id=user_id, main=main)
        return None
    sent = await _raw(client).send_message(chat_id, tv.placeholder(shown, tf), html=True, reply_to=reply_to)
    mid = int(sent["message_id"]) if isinstance(sent, dict) and sent.get("message_id") else None
    _remember(db, rid, chat_id, mid)
    spawn(watch(rid, chat_id, mid, view, reply_to, foot, shown, tf, user_id=user_id, main=main))
    return None


def market_of(listing: Any) -> dict[str, Any] | None:
    """The resolved market as the evidence pack names it (pack._asset): the committee's cache key."""
    venue, mid = getattr(listing, "venue", None), getattr(listing, "symbol", None)
    if not venue or not mid:
        return None
    kind = "perp" if venue == "hyperliquid" else "dex" if venue == "dex" else getattr(listing, "kind", None)
    return {"venue": venue, "market_type": kind, "market_id": str(mid)}


def _remember(db: Session, rid: str, chat_id: Any, message_id: int | None) -> None:
    """The placeholder on the run's row (when it has none yet): recovery edits it if the run never finishes."""
    row = orchestrator.find(db, rid)
    if row is not None and message_id and row.message_id is None and isinstance(chat_id, int):
        row.chat_id, row.message_id = chat_id, message_id
        db.commit()


def report_url(run_id: str) -> str:
    """The run on the website's Earn page (members_public_url, the address the site's other links use)."""
    from app.config.settings import get_settings
    from app.tradingos.bot import SITE

    base = (get_settings().members_public_url or "").rstrip("/")
    return f"{base if base.startswith('https://') else SITE}/earn/committee?run={run_id}"


def buttons(run_id: str, view: str = "sum") -> list[list[dict[str, str]]]:
    return tv.buttons(run_id, view, web=report_url(run_id) if run_id else None)


async def recover(db: Session, client: Any = None, now: Any = None) -> int:
    """committee.llm_expire (also its first run after a start): runs stuck in queued / running (older than 10 min, or left by
    a previous process) are failed as "interrupted by a restart", their usage given back, and their Telegram placeholder
    edited into a short note. A Telegram failure never stops the rest."""
    rows = orchestrator.stuck(db, now)
    if not rows:
        return 0
    own = None
    for row in rows:
        orchestrator.mark_interrupted(db, row, now)
        if row.chat_id and row.message_id:
            try:
                own = own or client or _client()
                await _raw(own).edit_message_text(row.chat_id, row.message_id,
                                                  f"⚠️ The Kiwi Committee on <b>{tv.esc(row.symbol)} {tv.esc(row.timeframe)}</b> was interrupted "
                                                  "(the bot restarted). Send the command again: this run wasn't counted.", html=True, buttons=[])
            except Exception as e:  # noqa: BLE001 - deleted, too old, Telegram down: the row is fixed either way
                logger.info("committee recovery: placeholder of %s not edited (%s)", row.public_id, str(e)[:120])
    if own is not None and client is None and CLIENT_FACTORY is None:
        close = getattr(own, "aclose", None)
        if close is not None:
            await close()
    return len(rows)


def limit_text(db: Session, who: dict[str, Any], message: str) -> str:
    from app.tradingos.bot import SITE

    limit, boosted = orchestrator.daily_limit(db, who)
    msg = f"🥝 {tv.esc(message)} It resets at 00:00 UTC; a market someone ran in the last 15 min is free."
    ranked = orchestrator.llm.settings().committee_daily_limit_ranked
    if not boosted and ranked > limit:
        msg += f" Kiwi Passport members at the boost rank get {ranked} a day: {SITE}/passport"
    return msg


async def status(db: Session, user_id: int | None = None) -> str:
    """/committee status: the /api/committee/status route's own function (backends, data providers, budget, concurrency)."""
    from types import SimpleNamespace

    from app.api.routes.committee import get_status
    from app.committee import llm

    rt = RUNTIME if RUNTIME is not None else SimpleNamespace(settings=llm.settings(), provider=None, gate=None)
    st = await get_status(db, rt)
    mine = None
    if user_id:
        who = {"tg_user_id": int(user_id)}
        mine = (orchestrator.used_today(db, who), orchestrator.daily_limit(db, who)[0])
    return tv.status_text(st, mine)


# ---------------------------------------------------------------- the watcher: placeholder → answer
def spawn(coro: Any) -> asyncio.Task:
    t = asyncio.get_running_loop().create_task(coro)
    _WATCHERS.add(t)
    t.add_done_callback(_WATCHERS.discard)
    return t


async def settle() -> None:
    """Tests: until every watcher has delivered."""
    while _WATCHERS:
        await asyncio.gather(*list(_WATCHERS), return_exceptions=True)


async def watch(public_id: str, chat_id: Any, message_id: int | None, view: str, reply_to: int | None, foot: str, shown: str, tf: str,
                user_id: int | None = None, main: bool = False) -> None:
    """Poll the run's row (its own short sessions) until done / failed / WATCH_FOR, then edit the placeholder."""
    from app.db import session as dbs

    deadline = time.monotonic() + WATCH_FOR
    status_, result, error = "queued", None, None
    try:
        while True:
            with dbs.SessionLocal() as db:
                row = orchestrator.find(db, public_id)
                if row is None:
                    status_, error = "failed", "the run disappeared"
                else:
                    status_, result, error = row.status, row.result, row.error
            if status_ in ("done", "failed") or time.monotonic() > deadline:
                break
            await asyncio.sleep(POLL_SECONDS)
        client = _client()
        try:
            if status_ == "done" and result:
                await deliver(client, chat_id, message_id, result, view, reply_to, foot, user_id=user_id, main=main)
            elif status_ == "failed":
                await _edit_or_send(client, chat_id, message_id, tv.failed_text(shown, tf, error, tv.esc(USAGE)), None, reply_to)
            else:
                await _edit_or_send(client, chat_id, message_id,
                                    f"⏳ The committee on <b>{tv.esc(shown)} {tv.esc(tf)}</b> is still working. Send the same command in a "
                                    "minute: a finished run is answered at once and isn't counted again.", None, reply_to)
        finally:
            close = getattr(client, "aclose", None)
            if close is not None and CLIENT_FACTORY is None:
                await close()
    except Exception:  # noqa: BLE001 - a watcher never crashes the server; the run itself is stored either way
        logger.exception("committee watcher %s failed", public_id)


async def _edit_or_send(client: Any, chat_id: Any, message_id: int | None, text: str, btns: Any, reply_to: int | None) -> Any:
    """Edit the bot's message; if it can't be edited (deleted by the main-chat tidy-up, too old), send it anew."""
    from app.telegram.errors import TelegramRequestError

    if message_id:
        try:
            return await client.edit_message_text(chat_id, message_id, text, html=True, buttons=btns)
        except TelegramRequestError as e:
            if "not modified" in str(e).lower():
                return None
            logger.info("committee: message not edited (%s), sending anew", str(e)[:120])
    return await client.send_message(chat_id, text, btns, html=True, reply_to=reply_to)


async def deliver(client: Any, chat_id: Any, message_id: int | None, run: dict[str, Any], view: str, reply_to: int | None, foot: str = "",
                  user_id: int | None = None, main: bool = False, single: bool = False) -> None:
    """Show a view of a finished run in the message (edit) or as a new reply. The full report is one or two messages; in
    the main chat it goes to the asker privately and the chat gets the compact summary."""
    rid = str(run.get("id") or "")
    if view == "full" and main:
        await _edit_or_send(client, chat_id, message_id, _with_footer(tv.compact(run), foot), buttons(rid, "sum"), reply_to)
        if user_id:
            await send_private_full(client, user_id, run)
        return
    if view == "full":
        parts = tv.full_report(run)
        if single and len(parts) > 1:  # a button press: one message, edited in place (part 2 is never sent again and again)
            parts = [parts[0] + "\n<i>… the rest is in the 🌐 Web report.</i>"]
        await _edit_or_send(client, chat_id, message_id, parts[0] if len(parts) > 1 else _with_footer(parts[0], foot), buttons(rid, "full"), reply_to)
        for p in parts[1:]:
            await client.send_message(chat_id, _with_footer(p, foot), html=True)
        return
    text = tv.view(run, view)
    await _edit_or_send(client, chat_id, message_id, _with_footer(text, foot), buttons(rid, view), reply_to)


async def send_private_full(client: Any, user_id: int, run: dict[str, Any]) -> bool:
    try:
        for p in tv.full_report(run):
            await client.send_message(int(user_id), p, html=True)
        return True
    except Exception as e:  # noqa: BLE001 - the member never pressed Start in the bot: private messages aren't possible yet
        logger.info("committee: full report not sent privately (%s)", str(e)[:120])
        return False


async def send_private_view(client: Any, user_id: int, run: dict[str, Any], view: str) -> bool:
    """A button pressed in a group: the view goes to the presser's private chat, with the buttons (pressed there, they edit
    that private message in place). False when Telegram refuses (the member never pressed Start in the bot)."""
    if view == "full":
        return await send_private_full(client, user_id, run)
    try:
        await client.send_message(int(user_id), tv.view(run, view), buttons(str(run.get("id") or ""), view), html=True)
        return True
    except Exception as e:  # noqa: BLE001 - as above
        logger.info("committee: %s view not sent privately (%s)", view, str(e)[:120])
        return False


def _group(chat: dict[str, Any]) -> bool:
    return chat.get("type") in ("group", "supergroup", "channel") or (isinstance(chat.get("id"), int) and chat["id"] < 0)


# ---------------------------------------------------------------- buttons
async def callback(db: Session, client: Any, cq: dict[str, Any]) -> None:
    from app.tradingos import bot

    data = str(cq.get("data") or "")
    uid = int((cq.get("from") or {}).get("id") or 0)
    msg = cq.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id, mid = chat.get("id"), msg.get("message_id")
    try:
        _, rid, action = data.split(":", 2)
    except ValueError:
        return
    row = orchestrator.find(db, rid)
    if row is None or chat_id is None or not mid:
        await bot._answer(client, cq, uid, "This committee run isn't available any more.", dm=False)
        return
    main = _main_chat(db, chat_id, chat.get("username"))
    if action == "refresh":
        await _refresh(db, client, cq, uid, row, chat_id, int(mid), main)
        return
    if row.status != "done" or not row.result:
        await bot._answer(client, cq, uid, "The committee is still meeting: try again in a moment.", dm=False)
        return
    run = row.result
    if action == "chart":
        await bot._answer(client, cq, uid, "Drawing the chart…", dm=False)
        await _chart(db, client, chat_id, chat.get("username"), run, int(mid))
        return
    if action not in tv.VIEWS:
        await bot._answer(client, cq, uid, "Unknown button.", dm=False)
        return
    if action != "sum" and _group(chat):  # in a group the shared message stays on the summary: the presser gets the view privately
        label = VIEW_LABEL.get(action, "This view")
        if await send_private_view(client, uid, run, action):
            await bot._answer(client, cq, uid, f"{label} sent to you privately.", dm=False)
        else:
            from app.telegram.ingestion import get_bot_state

            name = get_bot_state(db).bot_username or "cryptopricechatbot"
            await bot._answer(client, cq, uid, f"Open t.me/{name} and press Start, then tap {label} again.", alert=True, dm=False)
        return
    await bot._answer(client, cq, uid, "", dm=False)
    foot = bot.footer(db)
    await deliver(client, chat_id, int(mid), run, action, None, foot, single=True)


async def _chart(db: Session, client: Any, chat_id: Any, chat_username: str | None, run: dict[str, Any], mid: int) -> None:
    """The existing /chart rendering for the run's market and timeframe (Bot Chat routing as for /chart in the main chat)."""
    from app.tradingos import bot, market, signals, venues

    a = run.get("asset") or {}
    if a.get("venue") == "dex":
        args = [str(a.get("market_id") or a.get("symbol") or ""), str(run.get("timeframe") or "1h")]
    elif a.get("venue") == "hyperliquid":
        args = [str(a.get("symbol") or ""), str(run.get("timeframe") or "1h")]
    else:
        args = [str(a.get("symbol") or ""), str(a.get("venue") or ""), str(a.get("market_type") or ""), str(run.get("timeframe") or "1h")]
    args = [x for x in args if x]
    s = signals.settings(db)
    db.commit()
    bot_chat = (s.publish_chat or "").strip()
    target, reply = chat_id, mid
    if (_main_chat(db, chat_id, chat_username) and s.chart_routing == "bot_chat"
            and bot_chat.lower() not in ("", str(chat_id), "@" + (chat_username or "").lower())):
        target, reply = bot_chat, None
    footed = bot._Footed(_raw(client), bot.footer(db))
    try:
        out = await bot._chart(db, footed, target, market.data(), args, reply)
    except (market.MarketError, venues.NotFound, venues.Ambiguous) as e:
        out = f"Chart unavailable: {tv.esc(str(e))}"
    if out:
        await footed.send_message(chat_id, out, html=True, reply_to=mid)


async def _refresh(db: Session, client: Any, cq: dict[str, Any], uid: int, row: Any, chat_id: Any, mid: int, main: bool) -> None:
    """A new run only when the shown one is older than the cache (15 min); it counts against the presser's daily limit."""
    from app.security.rate_limit import LIMITS
    from app.tradingos import bot

    made = orchestrator._aware(row.created_at)
    if row.status in ("queued", "running"):
        await bot._answer(client, cq, uid, "The committee is still meeting on this one.", dm=False)
        return
    if utcnow() - made < orchestrator.CACHE_FOR and row.status == "done":
        await bot._answer(client, cq, uid, f"Fresh: updated {made:%H:%M} UTC. A new run is possible 15 min after that.", dm=False)
        return
    wait = LIMITS["committee"].hit(f"tg:{uid}")
    if wait is not None:
        await bot._answer(client, cq, uid, f"Easy: try again in {wait} s.", dm=False)
        return
    who = {"tg_user_id": uid, "chat_id": int(chat_id) if isinstance(chat_id, int) else None}
    try:
        rid = orchestrator.start_run(db, row.symbol, row.timeframe, requested_by=who, provider=getattr(RUNTIME, "provider", None),
                                     gate=getattr(RUNTIME, "gate", None),
                                     market={"venue": row.venue, "market_type": row.market_type, "market_id": row.market_id})
    except orchestrator.LimitReached as e:
        await bot._answer(client, cq, uid, re.sub(r"<[^>]+>", "", limit_text(db, who, e.message))[:190], alert=True, dm=False)
        return
    except orchestrator.CommitteeError as e:
        await bot._answer(client, cq, uid, e.message[:190], alert=True, dm=False)
        return
    await bot._answer(client, cq, uid, "Refreshing: the committee is meeting again.", dm=False)
    db.expire_all()
    new = orchestrator.find(db, rid)
    foot = bot.footer(db)
    shown = (new.result or {}).get("asset", {}).get("symbol") if new is not None and new.result else row.symbol
    if new is not None and new.status == "done" and new.result:
        await deliver(_raw(client), chat_id, mid, new.result, "sum", None, foot)
        return
    await _edit_or_send(_raw(client), chat_id, mid, tv.placeholder(shown or row.symbol, row.timeframe), [], None)
    _remember(db, rid, chat_id, mid)
    spawn(watch(rid, chat_id, mid, "sum", None, foot, shown or row.symbol, row.timeframe, user_id=uid, main=main))
