"""The shared news engine and its Telegram lane: only market chaos with real evidence, once each, within limits, and delivered means Telegram gave a
message id. X and Telegram choose the same stories; each keeps its own limits and delivery. No test reaches a real service."""

import json
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.members import telegram_missions as tm
from app.members.referrals import program
from app.models.members import Mission
from app.models.newsdesk import NdEvent, NdPost, NdStory, NdTelegramPost
from app.models.telegram import TelegramMessage, TelegramSource
from app.newsdesk import chaos, checks, engine, events, sources, telegram as lane, writer
from app.telegram.errors import TelegramRateLimitError, TelegramRequestError, TelegramUnavailableError

CHANNEL_ID = -100123
HACK = "Bybaz exchange says hackers drained $40 million from its hot wallet"
ONE = "Attackers drained $40 million from the Bybaz hot wallet on Tuesday, the exchange said."
TWO = "Withdrawals are paused while Bybaz investigates with blockchain analysts."


class Tg:
    """Answers like Telegram, and remembers every send (text or card)."""

    def __init__(self, status: str = "administrator", can_post: bool = True, chat_type: str = "channel", fail: Exception | None = None, answer: dict[str, Any] | None = None,
                 photo_fail: Exception | None = None) -> None:
        self.status, self.can_post, self.chat_type, self.fail, self.answer, self.photo_fail = status, can_post, chat_type, fail, answer, photo_fail
        self.sent: list[dict[str, Any]] = []
        self.next_id = 143

    async def get_me(self) -> dict[str, Any]:
        return {"id": 9, "username": "Cryptopricechatbot"}

    async def get_chat(self, chat: Any) -> dict[str, Any]:
        return {"id": CHANNEL_ID, "type": self.chat_type, "title": "Price Updates"}

    async def get_chat_member(self, chat: Any, user: int) -> dict[str, Any]:
        return {"status": self.status, "can_post_messages": self.can_post}

    def _answer(self) -> dict[str, Any]:
        if self.fail:
            raise self.fail
        if self.answer is not None:
            return self.answer
        self.next_id += 1
        return {"message_id": self.next_id, "chat": {"id": CHANNEL_ID, "type": "channel"}}

    async def send_message(self, chat: Any, text: str, **kw: Any) -> dict[str, Any]:
        self.sent.append({"chat": chat, "text": text, "card": False, **kw})
        return self._answer()

    async def send_photo(self, chat: Any, png: bytes, caption: str, **kw: Any) -> dict[str, Any]:
        if self.photo_fail:
            raise self.photo_fail
        assert png.startswith(b"\x89PNG")
        self.sent.append({"chat": chat, "text": caption, "card": True, **kw})
        return self._answer()


def story(db: Session, title: str, source: str, *, link: str | None = None, summary: str = ONE, tier: int = 2, category: str = "crypto", hours: float = 1.0, now: Any = None) -> NdStory:
    now = now or utcnow()
    row = NdStory(link=link or f"https://{source.lower().replace(' ', '')}.test/{abs(hash(title))}", title=title, summary=summary, source=source, tier=tier, category=category,
                  published_at=now - timedelta(hours=hours), fingerprint=checks.fingerprint(title), score=2.0)
    db.add(row)
    db.flush()
    return row


def verified(db: Session, title: str = HACK, **kw: Any) -> tuple[NdStory, NdStory]:
    a = story(db, title, "The Block", summary=ONE, **kw)
    b = story(db, title + " today", "Decrypt", summary=TWO, **kw)
    return a, b


# These tests are about evidence and delivery, so the daily plan is opened up (no windows, gaps or bar); the plan has its own tests in
# test_newsdesk_events.py.
OPEN = {"regular_bar": 0, "opening_bar": 0, "regular_per_day": 50, "ceiling": 50, "window_hours": 0, "regular_gap_minutes": 0, "min_gap_minutes": 0, "topic_max": 50,
        "asset_cooldown_minutes": 360}


def desk_running(db: Session, now: Any, plan: dict[str, Any] | None = None) -> None:
    """The desk read the feeds moments ago, so the lane makes no web request of its own."""
    desk = engine.settings_row(db)
    desk.state = {"last_run": {"at": now.isoformat()}, "policy": OPEN if plan is None else plan}
    db.commit()


async def agrees(item: dict[str, Any], price: float, market: Any = None) -> tuple[bool, str]:
    return True, f"Hyperliquid {price * 1.0005:,.2f} vs Yahoo {price:,.2f}: 0.05% apart"


async def decide(db: Session, now: Any, quotes: dict[str, Any] | None = None, second: Any = agrees) -> dict[str, Any]:
    """What the desk's own run does: the shared decision, for X and Telegram alike."""
    desk = engine.settings_row(db)
    return await events.decide(db, now, quotes=quotes or {}, watchlist=desk.watchlist or [], categories=desk.categories or {}, desk_state=desk.state, liquidations=False,
                               second_opinion=second)


async def run(db: Session, tg: Tg, now: Any = None, **kw: Any) -> dict[str, Any]:
    now = now or utcnow()
    desk_running(db, now, kw.pop("plan", None))
    kw.setdefault("quotes", {})
    await decide(db, now, kw["quotes"], kw.pop("second", agrees))
    return await lane.tick(db, get_settings(), tg, now, **kw)


def rows(db: Session) -> list[NdTelegramPost]:
    return list(db.scalars(select(NdTelegramPost).order_by(NdTelegramPost.id)))


def quote(symbol: str, start: float, end: float, now: datetime, hours: int = 49) -> sources.Quote:
    """Five-minute bars moving in a straight line from start to end over the last `hours`."""
    n = hours * 12
    t0 = now.timestamp() - hours * 3600
    bars = [(int(t0 + i * 300), start + (end - start) * i / n, 1.0) for i in range(n + 1)]
    return sources.Quote(symbol, end, now, None, None, bars)


# --- delivery -------------------------------------------------------------------------------------------------------


async def test_a_verified_story_is_delivered_once_with_its_sources_and_the_message_id_is_kept(db: Session) -> None:
    a, b = verified(db)
    tg = Tg()
    out = await run(db, tg)
    assert out["sent"] == 144 and len(tg.sent) == 1
    sent = tg.sent[0]
    assert sent["chat"] == CHANNEL_ID and sent["html"] is True and sent["silent"] is True
    assert "🥝 PRICE ALERT" in sent["text"] and "🔴 ALERT: BYBAZ EXCHANGE SAYS HACKERS DRAINED $40 MILLION" in sent["text"]
    assert TWO in sent["text"] and ONE not in sent["text"]  # the outlet's sentence that only repeats the headline is left out
    assert f'href="{a.link}"' in sent["text"] and f'href="{b.link}"' in sent["text"]
    post = rows(db)[0]
    assert (post.status, post.message_id, post.chat_id, post.chat) == ("delivered", 144, CHANNEL_ID, "@Pricechatupdates")
    assert post.url == "https://t.me/Pricechatupdates/144"


async def test_the_lane_leaves_the_x_queue_alone(db: Session) -> None:
    a, b = verified(db)
    desk_before = engine.settings_row(db).mode
    db.commit()
    await run(db, Tg())
    db.refresh(a)
    db.refresh(b)
    assert a.used is False and b.used is False
    assert list(db.scalars(select(NdPost))) == []
    assert engine.settings_row(db).mode == desk_before


async def test_x_and_telegram_carry_the_same_event_with_the_same_headline(db: Session) -> None:
    now = utcnow()
    verified(db, now=now)
    verified(db, "Zorbex founder arrested after $9 million rug pull", hours=3, now=now)
    tg = Tg()
    await run(db, tg, now)
    desk = engine.settings_row(db)
    await engine.deliver_x(db, desk, get_settings(), None, 0, now)
    ev = db.scalars(select(NdEvent).where(NdEvent.status == "released")).one()
    x = db.get(NdPost, ev.x_post_id)
    assert ev.tg_status == "delivered" and ev.x_status == "dry_run" and ev.tg_message_id == 144
    assert ev.headline in tg.sent[0]["text"] and ev.headline in x.text  # one decision, one headline, two deliveries
    assert "BYBAZ" in ev.headline  # a fresh $40m exchange hack, two outlets, outranks a three-hour-old rug pull
    assert "🥝 PRICE ALERT" in x.text and "http" not in x.text and len(x.text) <= 280 and x.facts["event_id"] == ev.id


async def test_a_second_run_never_sends_the_same_story_or_its_other_outlet(db: Session) -> None:
    verified(db)
    tg = Tg()
    now = utcnow()
    await run(db, tg, now)
    await run(db, tg, now + timedelta(hours=2))
    assert len(tg.sent) == 1


async def test_a_new_headline_on_a_story_already_carried_is_a_duplicate_but_a_development_is_an_update(db: Session) -> None:
    now = utcnow()
    verified(db, now=now)
    tg = Tg()
    await run(db, tg, now)
    story(db, "Bybaz hot wallet drained of $40 million, exchange says", "Cointelegraph", now=now)
    story(db, "Hackers drained $40 million from Bybaz hot wallet", "BBC", tier=1, now=now)
    await run(db, tg, now + timedelta(hours=1))
    assert len(tg.sent) == 1  # the same news, reworded
    story(db, "Bybaz suspends withdrawals and confirms losses rose to $90 million", "Cointelegraph", summary="Bybaz said losses rose to $90 million.", now=now + timedelta(hours=1.5), hours=0)
    story(db, "Bybaz losses rise to $90 million as withdrawals are suspended", "The Block", summary="Losses rose to $90 million, Bybaz said.", now=now + timedelta(hours=1.5), hours=0)
    await run(db, tg, now + timedelta(hours=1.6))
    assert len(tg.sent) == 2 and "🔄 UPDATE" in tg.sent[1]["text"] and tg.sent[1]["reply_to"] == 144  # a reply to the first message
    await run(db, tg, now + timedelta(hours=2.5))
    assert len(tg.sent) == 2


async def test_a_story_whose_headline_fails_the_checks_never_goes_out_on_either_platform(db: Session) -> None:
    story(db, "SEC charges Acme exchange over $40 million fraud, details at sec.gov", "SEC", summary="The agency charged Acme with fraud.", tier=0)
    now = utcnow()
    tg = Tg()
    await run(db, tg, now)
    await run(db, tg, now + timedelta(minutes=5))
    ev = db.scalars(select(NdEvent)).one()
    assert tg.sent == [] and ev.status == "rejected" and "link" in ev.why and ev.x_post_id is None


async def test_a_summary_that_fails_the_checks_is_dropped_and_the_headline_stands_alone(db: Session) -> None:
    story(db, "SEC charges Acme exchange over $40 million fraud", "SEC", summary="Details are at sec.gov/news today. The agency charged Acme with fraud.", tier=0)
    tg = Tg()
    await run(db, tg)
    assert len(tg.sent) == 1 and "sec.gov" not in tg.sent[0]["text"] and "ACME EXCHANGE OVER $40 MILLION FRAUD" in tg.sent[0]["text"]


async def test_a_story_with_no_summary_goes_out_as_its_headline_and_nothing_is_added(db: Session) -> None:
    story(db, "SEC charges Acme exchange with fraud", "SEC", summary="", tier=0)
    tg = Tg()
    await run(db, tg)
    text = tg.sent[0]["text"]
    assert "ACME EXCHANGE WITH FRAUD" in text and "⚖️ Allegations, not proven" in text
    assert text.count("\n\n") == 3  # brand, headline, allegation marker, source: nothing else


# --- what isn't news ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("title,over", [
    ("Federal judge rules in the Acme bank case", {"category": "economics"}),
    ("Bitcoin reportedly set to double on Acme rumour", {}),
    ("Why Bitcoin keeps falling this week", {}),
    ("Massive Acme airdrop announced for token holders", {}),
    ("Acme token launches on Solana mainnet", {}),
    ("Samsung Wallet launches USDC stablecoin accounts for Galaxy users", {}),
    ("Kraken partners with Acme to expand payments in Europe", {}),
    ("Coinbase says customer funds are safe after the Bybaz hack", {}),
    ("Coinbase says 99% of customer crypto is offline and out of hackers' reach", {}),
    ("Bybaz exchange says hackers drained $40 million from its hot wallet", {"hours": 9.0}),
    ("Local team wins the cup after a dramatic final", {"category": "viral"}),
])
async def test_what_isnt_for_this_channel_stays_out(db: Session, title: str, over: dict[str, Any]) -> None:
    verified(db, title, **over)
    tg = Tg()
    await run(db, tg)
    assert tg.sent == []


# --- evidence -------------------------------------------------------------------------------------------------------


async def test_one_reputable_outlet_is_enough_when_it_says_who_says_so(db: Session) -> None:
    story(db, HACK, "Decrypt", summary=f"{ONE} {TWO}")
    tg = Tg()
    await run(db, tg)
    assert len(tg.sent) == 1 and "Decrypt" in tg.sent[0]["text"]
    assert "evidence=attributed" not in tg.sent[0]["text"]  # the evidence is logged, not posted


async def test_one_outlet_with_no_attribution_waits_for_a_second(db: Session) -> None:
    now = utcnow()
    story(db, "Hot wallet of Bybaz drained in $40 million exploit", "Decrypt", summary="Funds moved through mixers overnight.", now=now)
    tg = Tg()
    await run(db, tg, now)
    assert tg.sent == []
    story(db, "Bybaz hot wallet drained in $40 million exploit", "The Block", summary=TWO, now=now)
    await run(db, tg, now + timedelta(minutes=5))
    assert len(tg.sent) == 1


@pytest.mark.parametrize("title", [
    "SEC sues Acme exchange, alleging it misled investors",
    "Zorbex token rug pull drains $9 million, ZachXBT says",
    "Acme founder accused of fraud by former partners",
])
async def test_scams_fraud_and_allegations_need_an_official_source_or_two_outlets(db: Session, title: str) -> None:
    now = utcnow()
    story(db, title, "Decrypt", summary=ONE, now=now)
    tg = Tg()
    await run(db, tg, now)
    assert tg.sent == []  # even though it says who says so
    story(db, title + " today", "The Block", summary=TWO, now=now)
    await run(db, tg, now + timedelta(minutes=5))
    assert len(tg.sent) == 1


async def test_an_allegation_is_marked_as_one(db: Session) -> None:
    verified(db, "SEC sues Acme exchange, alleging it misled investors")
    tg = Tg()
    await run(db, tg)
    assert "⚖️ Allegations, not proven" in tg.sent[0]["text"] and "💬" not in tg.sent[0]["text"]


async def test_a_market_claim_our_prices_confirm_needs_no_second_outlet(db: Session) -> None:
    now = utcnow()
    story(db, "Bitcoin crashes 9% to $60,000 as longs are liquidated", "Cointelegraph", summary="Traders were caught off guard.", hours=0.5, now=now)
    tg = Tg()
    await run(db, tg, now, quotes={"BTC-USD": quote("BTC-USD", 66000, 60000, now - timedelta(minutes=30), hours=24)})
    assert len(tg.sent) == 1 and "BITCOIN CRASHES 9% TO $60,000" in tg.sent[0]["text"]


async def test_a_market_claim_our_prices_contradict_never_goes_out(db: Session) -> None:
    now = utcnow()
    story(db, "Bitcoin crashes 9% to $60,000 as longs are liquidated", "Cointelegraph", hours=0.5, now=now)
    story(db, "Bitcoin crashes 9% to $60,000 as longs are liquidated today", "Decrypt", hours=0.5, now=now)
    tg = Tg()
    out = await run(db, tg, now, quotes={"BTC-USD": quote("BTC-USD", 60000, 66000, now - timedelta(minutes=30), hours=24)})
    assert tg.sent == [] and db.scalars(select(NdEvent)).first() is None
    v = chaos.evaluate(db.scalars(select(NdStory)).first(), [], {"BTC-USD": quote("BTC-USD", 60000, 66000, now - timedelta(minutes=30), hours=24)}, now)
    assert v.status == "rejected" and "contradict" in v.why


async def test_a_market_figure_we_cant_check_needs_a_second_outlet(db: Session) -> None:
    now = utcnow()
    story(db, "Riot shares plunge 20% as the miner reports a loss", "Cointelegraph", summary="Riot said costs rose.", now=now)
    tg = Tg()
    await run(db, tg, now)
    assert tg.sent == []
    v = chaos.evaluate(db.scalars(select(NdStory)).first(), [], {}, now)
    assert v.status == "held" and "couldn't check" in v.why


# --- price posts, cards, cooldowns ------------------------------------------------------------------------------------


def crash(now: datetime, *, minutes_ago: int = 0) -> dict[str, sources.Quote]:
    """Bitcoin falls from $70,000 to $60,000 in the hour to `minutes_ago` before now."""
    return {"BTC-USD": quote("BTC-USD", 70000, 60000, now - timedelta(minutes=minutes_ago), hours=1)}


async def test_a_live_crash_goes_out_with_the_same_headline_on_both_platforms_as_a_card(db: Session) -> None:
    now = utcnow()
    tg = Tg()
    await run(db, tg, now, quotes=crash(now))
    sent = tg.sent[0]
    assert sent["card"] is True and "🚨 BREAKING: BITCOIN CRASHES 14.3% IN AN HOUR TO $60,000" in sent["text"] and "matched against Hyperliquid" in sent["text"]
    ev = db.scalars(select(NdEvent)).one()
    assert rows(db)[0].story_link == f"event:{ev.id}" and rows(db)[0].writer == "market"
    await engine.deliver_x(db, engine.settings_row(db), get_settings(), None, 0, now)
    assert "BITCOIN CRASHES 14.3% IN AN HOUR TO $60,000" in db.get(NdPost, ev.x_post_id).text


async def test_a_crash_crypto_can_t_confirm_on_a_second_source_never_goes_out(db: Session) -> None:
    async def disagrees(item: dict[str, Any], price: float, market: Any = None) -> tuple[bool, str]:
        return False, "Hyperliquid 66,000.00 vs Yahoo 60,000.00: 10% apart"

    tg = Tg()
    now = utcnow()
    await run(db, tg, now, quotes=crash(now), second=disagrees)
    assert tg.sent == [] and db.scalars(select(NdEvent)).first() is None


async def test_a_stale_price_claims_no_move(db: Session) -> None:
    now = utcnow()
    tg = Tg()
    await run(db, tg, now, quotes=crash(now, minutes_ago=50))
    assert tg.sent == [] and db.scalars(select(NdEvent)).first() is None


async def test_one_move_per_asset_whether_it_comes_as_news_or_as_prices(db: Session) -> None:
    now = utcnow()
    tg = Tg()
    await run(db, tg, now, quotes=crash(now))
    verified(db, "Bitcoin plunges as liquidations sweep crypto markets", now=now + timedelta(minutes=50), hours=0.2)
    await run(db, tg, now + timedelta(minutes=50), quotes={"BTC-USD": quote("BTC-USD", 70000, 60000, now, hours=24)})
    assert len(tg.sent) == 1


async def test_a_price_move_after_news_of_the_same_move_doesnt_go_out_again(db: Session) -> None:
    now = utcnow()
    verified(db, "Bitcoin plunges as liquidations sweep crypto markets", now=now, hours=0.2)
    tg = Tg()
    await run(db, tg, now, quotes={"BTC-USD": quote("BTC-USD", 70000, 60000, now, hours=24)})
    assert len(tg.sent) == 1
    later = now + timedelta(minutes=50)
    await run(db, tg, later, quotes={"BTC-USD": quote("BTC-USD", 70000, 60000, later, hours=24)})
    assert len(tg.sent) == 1


async def test_routine_pr_stays_out_even_when_it_mentions_a_chaos_word(db: Session) -> None:
    title = "Acme partners with Coinbase to track whale wallets"
    verified(db, title)
    assert chaos.rejected(db.scalars(select(NdStory)).first()) == "routine (partners with)"
    tg = Tg()
    await run(db, tg)
    assert tg.sent == []


async def test_a_refused_card_still_goes_out_as_text_once(db: Session) -> None:
    now = utcnow()
    tg = Tg(photo_fail=TelegramRequestError("Telegram rejected sendPhoto: bad image"))
    await run(db, tg, now, quotes=crash(now))
    assert len(tg.sent) == 1 and tg.sent[0]["card"] is False and rows(db)[0].status == "delivered"


# --- rights, failures, limits ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("status,can_post,kind", [("member", False, "channel"), ("administrator", False, "channel"), ("left", False, "channel"), ("administrator", True, "supergroup")])
async def test_no_right_to_post_means_no_send_and_it_says_why(db: Session, status: str, can_post: bool, kind: str) -> None:
    verified(db)
    tg = Tg(status, can_post, kind)
    out = await run(db, tg)
    assert tg.sent == [] and rows(db) == []
    assert out["skipped"]
    assert lane.overview(db)["problem"]


async def test_the_channels_owner_may_post(db: Session) -> None:
    verified(db)
    tg = Tg("creator", False)
    await run(db, tg)
    assert len(tg.sent) == 1


async def test_a_send_that_may_have_gone_out_is_uncertain_and_never_resent(db: Session) -> None:
    verified(db)
    tg = Tg(fail=TelegramUnavailableError("Telegram did not respond to sendMessage in time"))
    now = utcnow()
    out = await run(db, tg, now)
    assert out["sent"] is None
    post = rows(db)[0]
    assert post.status == "uncertain" and post.message_id is None
    tg.fail = None
    await run(db, tg, now + timedelta(hours=3))
    assert len(tg.sent) == 1  # only the first, failed try


async def test_a_refused_send_isnt_delivered_and_tries_again_later(db: Session) -> None:
    verified(db)
    tg = Tg(fail=TelegramRequestError("Telegram rejected sendMessage: need administrator rights"))
    now = utcnow()
    await run(db, tg, now)
    assert rows(db) == []
    tried = len(tg.sent)  # the card, then the text alone
    await run(db, tg, now + timedelta(minutes=5))
    assert len(tg.sent) == tried  # still paused
    tg.fail = None
    out = await run(db, tg, now + timedelta(minutes=31))
    assert out["sent"] == 144 and rows(db)[0].status == "delivered"
    assert db.scalars(select(NdEvent)).one().tg_status == "delivered"


async def test_a_rate_limit_waits_as_long_as_telegram_says(db: Session) -> None:
    verified(db)
    tg = Tg(fail=TelegramRateLimitError("slow down", 600))
    now = utcnow()
    await run(db, tg, now)
    tg.fail = None
    await run(db, tg, now + timedelta(seconds=300))
    assert len(tg.sent) == 1
    await run(db, tg, now + timedelta(seconds=601))
    assert len(tg.sent) == 2 and rows(db)[0].status == "delivered"


@pytest.mark.parametrize("answer", [{}, {"chat": {"id": CHANNEL_ID}}, {"message_id": 5, "chat": {"id": -999}}])
async def test_an_answer_that_doesnt_confirm_the_message_is_not_delivered(db: Session, answer: dict[str, Any]) -> None:
    verified(db)
    tg = Tg(answer=answer)
    await run(db, tg)
    post = rows(db)[0]
    assert post.status == "uncertain" and post.message_id is None


async def test_a_run_that_died_mid_send_is_never_resent(db: Session) -> None:
    a, _ = verified(db)
    now = utcnow()
    desk_running(db, now)
    await decide(db, now)
    ev = db.scalars(select(NdEvent)).one()
    db.add(NdTelegramPost(story_link=f"event:{ev.id}", title=a.title, headline=a.title, text="x", source=a.source, sources=[], fingerprint=a.fingerprint, status="publishing",
                          updated_at=now - timedelta(minutes=20), created_at=now - timedelta(hours=2)))
    db.commit()
    tg = Tg()
    await run(db, tg, now)
    db.refresh(ev)
    assert tg.sent == [] and rows(db)[0].status == "uncertain" and ev.tg_status == "uncertain"


async def test_it_keeps_its_gap_and_its_daily_limit(db: Session) -> None:
    now = utcnow()
    titles = ["Bybaz exchange says hackers drained $40 million", "Qorvo protocol confirms $12 million exploit", "Tether freezes Tron wallets after theft, company says",
              "Lumio exchange halts withdrawals after outage, it says", "Vexa lender files for bankruptcy, filing shows"]
    for t in titles:
        verified(db, t, now=now)
    s = lane.settings_row(db)
    s.max_per_day = 2
    db.commit()
    tg = Tg()
    await run(db, tg, now)
    await run(db, tg, now + timedelta(minutes=10))
    assert len(tg.sent) == 1
    await run(db, tg, now + timedelta(minutes=50))
    assert len(tg.sent) == 2
    await run(db, tg, now + timedelta(minutes=100))
    assert len(tg.sent) == 2


async def test_at_equal_shock_the_fresher_story_goes_first_and_only_one_at_a_time(db: Session) -> None:
    verified(db, "Qorvo protocol confirms $12 million exploit", hours=5)
    verified(db, "Zelto protocol confirms $12 million exploit", hours=1)
    tg = Tg()
    await run(db, tg)
    assert len(tg.sent) == 1 and "ZELTO" in tg.sent[0]["text"]


async def test_turned_off_it_sends_nothing(db: Session) -> None:
    verified(db)
    lane.settings_row(db).enabled = False
    db.commit()
    tg = Tg()
    out = await run(db, tg)
    assert tg.sent == [] and out["skipped"] == "Turned off"


async def test_the_x_emergency_stop_doesnt_stop_telegram_and_the_lane_reads_the_feeds_itself(db: Session) -> None:
    verified(db)
    desk = engine.settings_row(db)
    desk.kill_switch, desk.feeds = True, [{"name": "Decrypt", "url": "https://decrypt.test/feed", "tier": 2, "category": "crypto"}]
    db.commit()
    asked: list[str] = []

    def web(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, text="<rss><channel></channel></rss>")

    tg = Tg()
    out = await lane.tick(db, get_settings(), tg, utcnow(), transport=httpx.MockTransport(web))
    assert asked[0] == "https://decrypt.test/feed" and all(u.startswith("https://query1.finance.yahoo.com/v8/finance/chart/") for u in asked[1:])  # the feed, then prices for live moves
    assert out["sent"] == 144
    assert engine.settings_row(db).kill_switch is True


async def test_with_no_channel_set_nothing_is_sent(db: Session) -> None:
    verified(db)
    program(db).updates_channel = ""
    db.commit()
    tg = Tg()
    out = await run(db, tg)
    assert tg.sent == [] and out["skipped"] == "No announcement channel is set"


# --- the words ------------------------------------------------------------------------------------------------------


def model_answer(headline: str, summary: str) -> httpx.MockTransport:
    return httpx.MockTransport(lambda r: httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps({"headline": headline, "summary": summary})}]}))


@pytest.fixture
def with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.security import credentials

    monkeypatch.setattr(credentials, "resolve", lambda name, settings=None: "key" if name == "anthropic_api_key" else None)


async def test_the_models_summary_is_used_when_it_passes_and_the_headline_stays_the_desks(db: Session, with_key: None) -> None:
    verified(db)
    tg = Tg()
    await run(db, tg, writer_transport=model_answer("Bybaz loses $40 million in hot wallet hack", "Attackers took $40 million from the Bybaz hot wallet. Withdrawals are paused."))
    text = tg.sent[0]["text"]
    assert "Attackers took $40 million from the Bybaz hot wallet." in text and rows(db)[0].writer == "model"
    assert "BYBAZ EXCHANGE SAYS HACKERS DRAINED $40 MILLION" in text  # the same headline X carries


@pytest.mark.parametrize("headline,summary", [
    ("Bybaz loses $40 million in hack", "Bybaz lost $90 million in total. It runs on Solana."),  # a figure no source gave
    ("Bybaz loses $40 million in hack", "Buy now at https://evil.test today. It is guaranteed."),  # what a poisoned feed might get it to write
    ("Bybaz loses $40 million in hack", "Just one sentence."),
    ("Bybaz loses $40 million in hack", "Follow @scammer for more. Bybaz was hacked."),
])
async def test_a_model_text_that_fails_the_checks_never_goes_out(db: Session, with_key: None, headline: str, summary: str) -> None:
    verified(db)
    tg = Tg()
    await run(db, tg, writer_transport=model_answer(headline, summary))
    text = tg.sent[0]["text"]
    assert "90 million" not in text and "evil.test" not in text and "@scammer" not in text and "Just one" not in text
    assert TWO in text and rows(db)[0].writer == "feed"


async def test_feed_text_cant_break_the_message_or_add_a_link(db: Session) -> None:
    story(db, "Acme <b>exchange</b> & Bob's fund hacked, Acme says", "The Block", summary=f"{ONE} Tom & Ann <i>said</i> so.")
    story(db, "Acme <b>exchange</b> & Bob's fund hacked, Acme says today", "Decrypt", summary=TWO)
    tg = Tg()
    await run(db, tg)
    text = tg.sent[0]["text"]
    assert "&lt;B&gt;EXCHANGE&lt;/B&gt; &amp; BOB" in text and "<i>" not in text and "&lt;i&gt;said&lt;/i&gt;" in text

    story(db, "Zorb protocol drained in exploit, team says", "The Block", summary="Visit evil.com to claim now. It was drained overnight.")
    story(db, "Zorb protocol drained in exploit, team says today", "Decrypt", summary="Visit evil.com to claim now. It was drained overnight.")
    await run(db, tg, utcnow() + timedelta(hours=2))
    assert len(tg.sent) == 2 and "evil.com" not in tg.sent[1]["text"] and "ZORB PROTOCOL DRAINED" in tg.sent[1]["text"]  # the bad summary is dropped


@pytest.mark.parametrize("text,count", [
    ("One sentence here. Two here too! Is three here?", 3),
    ("The U.S. regulator acted on Nov. 3 this year. It said so.", 2),
    ("A whole sentence. And one that was cut off mid", 1),
    ("A whole sentence. Read more...", 1),
    ("", 0),
])
def test_only_whole_sentences_count(text: str, count: int) -> None:
    assert len(lane.sentences(text)) == count


def test_a_news_post_never_becomes_the_discussion_mission(db: Session) -> None:
    now = utcnow()
    s = program(db)
    s.channel_linked_chat_id = -500
    group = TelegramSource(chat_id=-500, display_name="Discussion", chat_type="supergroup", enabled=True)
    db.add(group)
    db.add(NdTelegramPost(story_link="https://x.test/1", title="T", headline="BYBAZ LOSES $40 MILLION IN HACK", text="x", source="Decrypt", sources=[], status="delivered", message_id=144, chat="@Pricechatupdates"))
    db.flush()

    def forward(message_id: int, text: str, minutes: int) -> None:
        db.add(TelegramMessage(source_id=group.id, chat_id=-500, message_id=message_id, author_id=tm.TELEGRAM_SERVICE_USER, forward_from="Price Updates", text=text, sent_at=now - timedelta(minutes=minutes)))

    forward(10, "The owner's own announcement", 60)
    forward(11, "🥝 PRICE ALERT\n\n🔴 ALERT: BYBAZ LOSES $40 MILLION IN HACK\n\nWithdrawals are paused.", 5)
    db.flush()
    tm.generate(db)
    mission = db.scalar(select(Mission).where(Mission.kind == tm.DISCUSSION_KIND))
    assert mission is not None and mission.target["message_id"] == 10


def test_the_x_length_limit_is_unchanged() -> None:
    assert not checks.check_post("x" * 281, source_text="", source_names=[]).ok
    assert checks.check_post("x" * 281, source_text="", source_names=[], max_len=900).ok


def test_the_job_is_registered_and_whitelisted_by_the_desks_prefix() -> None:
    from pathlib import Path

    assert 'JobSpec("newsdesk.telegram"' in Path("app/jobs.py").read_text(encoding="utf-8")


def test_admin_can_see_and_stop_it(client: Any, db: Session) -> None:
    assert client.get("/api/x/newsdesk/telegram").json()["enabled"] is True
    off = client.put("/api/x/newsdesk/telegram", json={"enabled": False, "gap_minutes": 60})
    assert off.status_code == 200 and off.json()["enabled"] is False and off.json()["gap_minutes"] == 60
    assert client.put("/api/x/newsdesk/telegram", json={"gap_minutes": 1}).status_code == 422


# --- found replaying a real day ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("title,level", [
    ("Bitcoin Erases Election Rally, $428 Million in Crypto Longs Get Wiped Out", None),
    ("Binance seeks fork to recover $6.8M in stolen Bitcoin", None),
    ("Bitcoin miners lose $6.8M as hashprice falls", None),
    ("Bitcoin treasury firm raises $28B as shares slide", None),
    ("Bitcoin hits $80.5K as US stocks rally", 80500.0),
    ("Bitcoin rebounds past $80,000 as markets shrug off the hack", 80000.0),
])
def test_an_amount_of_money_is_never_read_as_a_price(title: str, level: float | None) -> None:
    claim = chaos.claim_of(title)
    assert (claim.level if claim else None) == level


def test_a_forecast_or_an_opinion_isnt_news(db: Session) -> None:
    s = story(db, "Binance hack may be 'temporary setback' for crypto, Grayscale's Pandl says", "Cointelegraph")
    assert chaos.rejected(s) == "a forecast or an opinion, not news"


def test_a_price_story_that_mentions_a_hack_is_about_the_price(db: Session) -> None:
    now = utcnow()
    s = story(db, "Bitcoin rebounds past $80,000 as markets shrug off the Bybaz hack", "The Block", hours=0.5, now=now)
    v = chaos.evaluate(s, [], {"BTC-USD": quote("BTC-USD", 76000, 80000, now - timedelta(minutes=30), hours=24)}, now)
    assert v.ok and v.evidence == "price data" and v.brief.label != "🔴 ALERT" and v.brief.topic == "move"


def test_the_market_as_a_whole_is_checked_against_its_index(db: Session) -> None:
    now = utcnow()
    s = story(db, "AI stocks plunge after the election, driving down markets", "Al Jazeera", tier=1, category="stocks", summary="Tech stocks lead the slide.", hours=0.5, now=now)
    up = chaos.evaluate(s, [], {"^IXIC": quote("^IXIC", 24000, 25000, now - timedelta(minutes=30), hours=24)}, now)
    down = chaos.evaluate(s, [], {"^IXIC": quote("^IXIC", 25800, 24400, now - timedelta(minutes=30), hours=24)}, now)
    assert up.status == "rejected" and down.ok and down.evidence == "price data"


def test_crypto_drama_outside_the_crypto_feeds_still_counts(db: Session) -> None:
    s = story(db, "Justin Sun seeks $264K sanctions over World Liberty's UAE disclosure", "Cointelegraph", category="politics")
    assert chaos.rejected(s) is None


def test_a_legal_fight_needs_the_stronger_evidence_and_a_flag_needs_the_country_up_front(db: Session) -> None:
    now = utcnow()
    s = story(db, "Justin Sun seeks $264K sanctions over World Liberty's UAE disclosure", "Cointelegraph", category="politics", now=now)
    v = chaos.evaluate(s, [], {}, now)
    assert v.status == "held" and v.brief.alleged and v.brief.flag == ""


def test_a_gap_in_the_price_data_falls_back_to_the_last_price_before_the_story(db: Session) -> None:
    now = utcnow()
    q = quote("^GSPC", 7700, 7400, now - timedelta(minutes=70), hours=24)  # nothing in the last 70 minutes
    s = story(db, "S&P 500 plunges as Wall Street sells off", "Decrypt", category="stocks", hours=0.1, now=now)
    v = chaos.evaluate(s, [], {"^GSPC": q}, now)
    assert v.ok and v.evidence == "price data"


def test_price_data_only_vouches_for_the_asset_the_headline_is_about(db: Session) -> None:
    now = utcnow()
    s = story(db, "Shares in Musk's SpaceX plunge as tech and AI stocks fall", "BBC", tier=1, category="stocks", summary="", hours=0.1, now=now)
    v = chaos.evaluate(s, [], {"^IXIC": quote("^IXIC", 25800, 24400, now, hours=24)}, now)
    assert v.status == "held"


async def test_x_carries_one_move_per_asset_too(db: Session) -> None:
    now = utcnow()
    btc = {"BTC-USD": quote("BTC-USD", 70000, 60000, now, hours=24)}
    verified(db, "Bitcoin plunges as liquidations sweep crypto markets", now=now, hours=0.3)
    verified(db, "Bitcoin crashes 14% to seven-week low as longs are wiped out", now=now, hours=0.2)
    desk_running(db, now)
    for minutes in (0, 30, 60):
        await decide(db, now + timedelta(minutes=minutes), btc)
        await engine.deliver_x(db, engine.settings_row(db), get_settings(), None, 0, now + timedelta(minutes=minutes))
    assert len(list(db.scalars(select(NdPost)))) == 1


