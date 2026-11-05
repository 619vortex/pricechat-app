"""The Telegram lane: only verified crypto stories, once each, within limits, and delivered means Telegram gave a message id. The X side is untouched. No test reaches a real service."""

import json
from datetime import timedelta
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
from app.models.newsdesk import NdPost, NdStory, NdTelegramPost
from app.models.telegram import TelegramMessage, TelegramSource
from app.newsdesk import checks, engine, telegram as lane
from app.telegram.errors import TelegramRateLimitError, TelegramRequestError, TelegramUnavailableError

CHANNEL_ID = -100123
ONE = "Samsung Wallet's stablecoin account is now live for eligible Galaxy users in the US."
TWO = "They can hold and send USDC on Solana without a separate crypto app."


class Tg:
    """Answers like Telegram, and remembers every send."""

    def __init__(self, status: str = "administrator", can_post: bool = True, chat_type: str = "channel", fail: Exception | None = None, answer: dict[str, Any] | None = None) -> None:
        self.status, self.can_post, self.chat_type, self.fail, self.answer = status, can_post, chat_type, fail, answer
        self.sent: list[dict[str, Any]] = []
        self.next_id = 143

    async def get_me(self) -> dict[str, Any]:
        return {"id": 9, "username": "Cryptopricechatbot"}

    async def get_chat(self, chat: Any) -> dict[str, Any]:
        return {"id": CHANNEL_ID, "type": self.chat_type, "title": "Price Updates"}

    async def get_chat_member(self, chat: Any, user: int) -> dict[str, Any]:
        return {"status": self.status, "can_post_messages": self.can_post}

    async def send_message(self, chat: Any, text: str, **kw: Any) -> dict[str, Any]:
        self.sent.append({"chat": chat, "text": text, **kw})
        if self.fail:
            raise self.fail
        if self.answer is not None:
            return self.answer
        self.next_id += 1
        return {"message_id": self.next_id, "chat": {"id": CHANNEL_ID, "type": "channel"}}


def story(db: Session, title: str, source: str, *, link: str | None = None, summary: str = ONE, tier: int = 2, category: str = "crypto", hours: float = 1.0, now: Any = None) -> NdStory:
    now = now or utcnow()
    row = NdStory(link=link or f"https://{source.lower().replace(' ', '')}.test/{abs(hash(title))}", title=title, summary=summary, source=source, tier=tier, category=category,
                  published_at=now - timedelta(hours=hours), fingerprint=checks.fingerprint(title), score=2.0)
    db.add(row)
    db.flush()
    return row


def verified(db: Session, title: str = "Samsung Wallet launches USDC accounts on Solana for Galaxy users", **kw: Any) -> tuple[NdStory, NdStory]:
    a = story(db, title, "The Block", summary=ONE, **kw)
    b = story(db, title + " today", "Decrypt", summary=TWO, **kw)
    return a, b


def desk_running(db: Session, now: Any) -> None:
    """The desk read the feeds moments ago, so the lane makes no web request of its own."""
    desk = engine.settings_row(db)
    desk.state = {"last_run": {"at": now.isoformat()}}
    db.commit()


async def run(db: Session, tg: Tg, now: Any = None, **kw: Any) -> dict[str, Any]:
    now = now or utcnow()
    desk_running(db, now)
    return await lane.tick(db, get_settings(), tg, now, **kw)


def rows(db: Session) -> list[NdTelegramPost]:
    return list(db.scalars(select(NdTelegramPost).order_by(NdTelegramPost.id)))


async def test_a_verified_story_is_delivered_once_with_its_sources_and_the_message_id_is_kept(db: Session) -> None:
    a, b = verified(db)
    tg = Tg()
    out = await run(db, tg)
    assert out["sent"] == 144 and len(tg.sent) == 1
    sent = tg.sent[0]
    assert sent["chat"] == CHANNEL_ID and sent["html"] is True and sent["silent"] is True
    assert "<b>Samsung Wallet launches" in sent["text"] and ONE in sent["text"] and TWO in sent["text"]
    assert f'href="{a.link}"' in sent["text"] and f'href="{b.link}"' in sent["text"]
    post = rows(db)[0]
    assert (post.status, post.message_id, post.chat_id, post.chat) == ("delivered", 144, CHANNEL_ID, "@Pricechatupdates")
    assert post.url == "https://t.me/Pricechatupdates/144"


async def test_the_x_side_is_left_exactly_as_it_was(db: Session) -> None:
    a, b = verified(db)
    desk_before = engine.settings_row(db).mode
    db.commit()
    await run(db, Tg())
    db.refresh(a)
    db.refresh(b)
    assert a.used is False and b.used is False
    assert list(db.scalars(select(NdPost))) == []
    assert engine.settings_row(db).mode == desk_before


async def test_a_second_run_never_sends_the_same_story_or_its_other_outlet(db: Session) -> None:
    verified(db)
    tg = Tg()
    now = utcnow()
    await run(db, tg, now)
    await run(db, tg, now + timedelta(hours=2))
    assert len(tg.sent) == 1


async def test_a_new_headline_on_a_story_already_carried_is_a_duplicate(db: Session) -> None:
    verified(db)
    tg = Tg()
    now = utcnow()
    await run(db, tg, now)
    story(db, "Samsung brings stablecoin payments to Wallet app", "Cointelegraph", summary=ONE, now=now)
    story(db, "Samsung stablecoin payments reach Wallet app", "BBC", summary=TWO, tier=1, now=now)
    await run(db, tg, now + timedelta(hours=2))
    assert len(tg.sent) == 1


async def test_the_same_wording_is_a_duplicate_even_with_no_name_to_match(db: Session) -> None:
    now = utcnow()
    verified(db, "crypto fund flows hit weekly record after rate decision", now=now)
    tg = Tg()
    await run(db, tg, now)
    verified(db, "crypto fund flows hit weekly record after central rate decision", now=now)
    await run(db, tg, now + timedelta(hours=2))
    assert len(tg.sent) == 1


async def test_a_story_that_failed_the_checks_is_only_tried_once(db: Session) -> None:
    story(db, "SEC approves spot crypto fund from Acme", "SEC", summary=ONE, tier=0)
    now = utcnow()
    tg = Tg()
    await run(db, tg, now)
    await run(db, tg, now + timedelta(minutes=5))
    assert [p.status for p in rows(db)] == ["refused"] and tg.sent == []
    good, seen = lane.candidates(db, now + timedelta(minutes=5))
    assert good == [] and seen["duplicate"] == 1


@pytest.mark.parametrize("title,over", [
    ("Federal judge rules in the Acme bank case", {"category": "economics"}),
    ("Bitcoin reportedly set to double on Acme rumour", {}),
    ("Why Bitcoin keeps falling this week", {}),
    ("Massive Acme airdrop announced for token holders", {}),
    ("Acme token launches on Solana mainnet", {"hours": 9.0}),
])
async def test_what_isnt_for_this_channel_stays_out(db: Session, title: str, over: dict[str, Any]) -> None:
    verified(db, title, **over)
    tg = Tg()
    await run(db, tg)
    assert tg.sent == []


async def test_one_outlet_alone_is_not_verified_but_an_official_source_is(db: Session) -> None:
    story(db, "Acme exchange lists new token pairs", "Decrypt", summary=f"{ONE} {TWO}")
    tg = Tg()
    await run(db, tg)
    assert tg.sent == []
    story(db, "SEC approves spot crypto fund from Acme", "SEC", summary=f"{ONE} {TWO}", tier=0)
    await run(db, tg)
    assert len(tg.sent) == 1 and "SEC" in tg.sent[0]["text"]


async def test_a_story_with_no_two_sentence_summary_is_not_padded_out(db: Session) -> None:
    story(db, "SEC approves spot crypto fund from Acme", "SEC", summary=ONE, tier=0)
    tg = Tg()
    await run(db, tg)
    assert tg.sent == []
    assert rows(db)[0].status == "refused"


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
    await run(db, tg, now + timedelta(minutes=5))
    assert len(tg.sent) == 1  # still paused
    tg.fail = None
    out = await run(db, tg, now + timedelta(minutes=31))
    assert out["sent"] == 144 and rows(db)[0].status == "delivered"


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
    db.add(NdTelegramPost(story_link=a.link, title=a.title, headline=a.title, text="x", source=a.source, sources=[], fingerprint=a.fingerprint, status="publishing", updated_at=now - timedelta(minutes=20), created_at=now - timedelta(hours=2)))
    db.commit()
    tg = Tg()
    await run(db, tg, now)
    assert tg.sent == [] and rows(db)[0].status == "uncertain"


async def test_it_keeps_its_gap_and_its_daily_limit(db: Session) -> None:
    now = utcnow()
    titles = ["Coinbase adds Ledger custody in Europe", "Kraken lists Polkadot futures in Asia", "Tether freezes Tron wallets after theft", "Ripple wins Dubai payments licence", "Circle opens Paris treasury office"]
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


async def test_the_newest_goes_first_and_only_one_goes_at_a_time(db: Session) -> None:
    verified(db, "Coinbase adds Ledger custody in Europe", hours=5)
    verified(db, "Kraken lists Polkadot futures in Asia", hours=1)
    tg = Tg()
    await run(db, tg)
    assert len(tg.sent) == 1 and "Kraken" in tg.sent[0]["text"]


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
    assert asked == ["https://decrypt.test/feed"]
    assert out["sent"] == 144
    assert engine.settings_row(db).kill_switch is True


async def test_with_no_channel_set_nothing_is_sent(db: Session) -> None:
    verified(db)
    program(db).updates_channel = ""
    db.commit()
    tg = Tg()
    out = await run(db, tg)
    assert tg.sent == [] and out["skipped"] == "No announcement channel is set"


def model_answer(headline: str, summary: str) -> httpx.MockTransport:
    return httpx.MockTransport(lambda r: httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps({"headline": headline, "summary": summary})}]}))


@pytest.fixture
def with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.security import credentials

    monkeypatch.setattr(credentials, "resolve", lambda name, settings=None: "key" if name == "anthropic_api_key" else None)


async def test_the_models_words_are_used_when_they_pass(db: Session, with_key: None) -> None:
    verified(db)
    tg = Tg()
    await run(db, tg, writer_transport=model_answer("Samsung Wallet adds USDC", "Samsung Wallet now offers USDC on Solana. US Galaxy users can hold it without a separate app."))
    assert "<b>Samsung Wallet adds USDC</b>" in tg.sent[0]["text"] and rows(db)[0].writer == "model"


@pytest.mark.parametrize("headline,summary", [
    ("Samsung Wallet adds USDC", "Samsung Wallet now has 40 million USDC users. It runs on Solana."),  # a figure no source gave
    ("Samsung Wallet adds USDC", "Buy now at https://evil.test today. It is guaranteed."),  # what a poisoned feed might get it to write
    ("Samsung Wallet adds USDC", "Just one sentence."),
    ("Samsung Wallet adds USDC", "Follow @scammer for more. Samsung Wallet has USDC."),
])
async def test_a_model_text_that_fails_the_checks_never_goes_out(db: Session, with_key: None, headline: str, summary: str) -> None:
    verified(db)
    tg = Tg()
    await run(db, tg, writer_transport=model_answer(headline, summary))
    text = tg.sent[0]["text"]
    assert "40 million" not in text and "evil.test" not in text and "@scammer" not in text and "Just one" not in text
    assert ONE in text and rows(db)[0].writer == "feed"


async def test_feed_text_cant_break_the_message_or_add_a_link(db: Session) -> None:
    story(db, "Acme <b>token</b> & Bob's fund launch", "The Block", summary=f"{ONE} Tom & Ann <i>said</i> so.")
    story(db, "Acme <b>token</b> & Bob's fund launch today", "Decrypt", summary=TWO)
    tg = Tg()
    await run(db, tg)
    text = tg.sent[0]["text"]
    assert "&lt;b&gt;token&lt;/b&gt; &amp; Bob" in text and "<i>" not in text

    story(db, "Dex launches on Base network", "The Block", summary="Visit evil.com to claim now. It launched on Base.")
    story(db, "Dex launches on Base network today", "Decrypt", summary="Visit evil.com to claim now. It launched on Base.")
    await run(db, tg, utcnow() + timedelta(hours=2))
    assert len(tg.sent) == 1


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
    db.add(NdTelegramPost(story_link="https://x.test/1", title="T", headline="Samsung Wallet adds USDC", text="x", source="Decrypt", sources=[], status="delivered", message_id=144, chat="@Pricechatupdates"))
    db.flush()

    def forward(message_id: int, text: str, minutes: int) -> None:
        db.add(TelegramMessage(source_id=group.id, chat_id=-500, message_id=message_id, author_id=tm.TELEGRAM_SERVICE_USER, forward_from="Price Updates", text=text, sent_at=now - timedelta(minutes=minutes)))

    forward(10, "The owner's own announcement", 60)
    forward(11, "Samsung Wallet adds USDC\n\nIt runs on Solana.", 5)
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
